#!/usr/bin/env python3
"""
Sthamly Engine: standalone orchestrator for packaging and reporting.

Works ONLY from real artifacts. Nothing here invents metrics.

    python scripts/engine.py audit  --model Qwen/Qwen2.5-1.5B-Instruct
        Runs scripts/audit.py on existing files, then prints the verdict.

    python scripts/engine.py build  --model Qwen/Qwen2.5-1.5B-Instruct
        Reads the audit report, picks the variant, copies the GGUF into dist/,
        writes manifest.json and report.pdf.

    python scripts/engine.py report --report reports/<slug>/report.json --out dist/report.pdf
        Builds only the PDF from an existing report.json.

Decision rule (same as the audit comparisons):
    A candidate imatrix variant is chosen only if, on EVERY domain, its KLD is
    lower than the plain Q4 baseline AND the gain is greater than 2x the combined
    error. Otherwise plain Q4_K_M is packaged, and the report says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from sthamly_common import normalize_model_id  # noqa: E402

GGUF_DIR = ROOT / "models" / "gguf"
REPORTS_DIR = ROOT / "reports"
DIST_DIR = ROOT / "dist"

PLAIN_VARIANT = "q4_standard"
IMATRIX_PREFIX = "q4_"  # audit variant names from worker.py / compress script

NOT_CLAIMED = [
    "Ye proxy naap hai (FP16 se doori). Task accuracy ya summary quality nahi naapi gayi.",
    "Test sets chhote hain. Error bar (±) dekhe bina kisi chhote fark par bharosa mat karo.",
    "Fark 'saaf' tabhi kaha gaya jab KLD gain, combined error ke 2 guna se bada tha.",
]

DOMAIN_LABELS = {
    "hindi": "Hindi test set",
    "wiki": "Hindi Wikipedia",
    "sangraha": "Sangraha (Hindi web text)",
}


class EngineError(RuntimeError):
    pass


# ============================================================
# REPORT READING (real data only)
# ============================================================

def load_report(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EngineError(f"Report nahi mili: {path}. Pehle audit.py chalao.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(f"Report padh nahi paya: {exc}") from exc

    if report.get("schema_version") != 2:
        raise EngineError(f"Report schema_version {report.get('schema_version')!r} supported nahi hai (2 chahiye).")
    if report.get("status") != "complete":
        raise EngineError(f"Report status {report.get('status')!r} hai; 'complete' hone par hi package banega.")
    if not isinstance(report.get("results"), list) or not report["results"]:
        raise EngineError("Report me results nahi hain.")
    return report


def index_results(report: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """{domain: {variant: result_item}} from the list-shaped audit results."""
    table: dict[str, dict[str, dict[str, Any]]] = {}
    for item in report["results"]:
        table.setdefault(item["domain"], {})[item["variant"]] = item
    return table


def decide(report: dict[str, Any]) -> dict[str, Any]:
    """
    Pick the imatrix variant only if it beats plain Q4 on EVERY domain with a
    gain above 2x the combined error. Mirrors audit.py's comparisons.
    """
    table = index_results(report)
    baseline = PLAIN_VARIANT if any(PLAIN_VARIANT in v for v in table.values()) else report.get("quantized_models", {}) and next(iter(report["quantized_models"]))

    candidates = sorted({
        variant
        for by_variant in table.values()
        for variant in by_variant
        if variant != baseline
    })

    best: str | None = None
    best_reduction: dict[str, float] = {}
    best_clear: list[str] = []

    for candidate in candidates:
        reductions: dict[str, float] = {}
        clear: list[str] = []
        ok_everywhere = True

        for domain, by_variant in table.items():
            base = by_variant.get(baseline)
            cand = by_variant.get(candidate)
            if base is None or cand is None:
                ok_everywhere = False
                break

            base_kld, base_err = base["kld"], base["kld_error"]
            cand_kld, cand_err = cand["kld"], cand["kld_error"]
            gain = base_kld - cand_kld
            combined = (base_err ** 2 + cand_err ** 2) ** 0.5

            if gain <= 0:
                ok_everywhere = False
                break

            reductions[domain] = round(100.0 * gain / base_kld, 2) if base_kld else 0.0
            if gain > 2 * combined:
                clear.append(domain)

        if ok_everywhere and clear:
            # Clear win on at least one domain and no domain worse.
            # Stricter option: require clear on all domains. Use the strict rule:
            if len(clear) == len(table):
                if best is None or sum(reductions.values()) > sum(best_reduction.values()):
                    best, best_reduction, best_clear = candidate, reductions, clear

    if best is None:
        return {
            "baseline": baseline,
            "best": None,
            "decision": "kept_plain",
            "reduction_pct": {},
            "clear_domains": [],
            "reason": "Koi imatrix variant har domain par saaf behtar nahi nikla.",
        }

    return {
        "baseline": baseline,
        "best": best,
        "decision": "improved",
        "reduction_pct": best_reduction,
        "clear_domains": best_clear,
        "reason": "Har domain par plain Q4 se KLD kam aur gain 2x combined error se bada.",
    }


# ============================================================
# PDF
# ============================================================

def variant_label(variant: str) -> str:
    if variant.endswith("standard"):
        return "Plain Q4_K_M (bina imatrix)"
    if "hindi" in variant:
        return "Hindi imatrix"
    if "english" in variant:
        return "English imatrix"
    if "mixed" in variant:
        return "Mixed imatrix"
    return variant


def verdict_sentences(table: dict[str, dict[str, dict[str, Any]]], decision: dict[str, Any]) -> list[str]:
    if decision["decision"] != "improved":
        return [
            "Koi imatrix variant har domain par plain Q4 se saaf behtar nahi nikla.",
            "Isliye is package mein plain Q4_K_M diya gaya hai.",
        ]
    lines = [f"Chuna gaya variant: {variant_label(decision['best'])}."]
    for domain, pct in decision["reduction_pct"].items():
        lines.append(f"{DOMAIN_LABELS.get(domain, domain)}: KLD mein {pct:.1f}% kami.")
    return lines


def make_pdf(path: Path, *, report: dict[str, Any], decision: dict[str, Any]) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=20,
                           textColor=colors.HexColor("#14213d"), spaceAfter=4)
    heading = ParagraphStyle("heading", parent=styles["Heading2"], fontSize=13,
                             textColor=colors.HexColor("#14213d"), spaceBefore=14)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=colors.HexColor("#555555"))

    table = index_results(report)
    model = report.get("target_model", "unknown")
    story: list[Any] = [
        Paragraph("Sthamly Quantization Report", title),
        Paragraph(escape(model), body),
        Spacer(1, 6),
    ]

    info = [
        ["Tareekh", date.today().isoformat()],
        ["Kaam", "Audit + imatrix optimize + quantize" if decision["decision"] == "improved"
                 else "Audit (plain Q4_K_M packaged)"],
        ["FP16 size", f"{report.get('fp16_size_gb', '?')} GB"],
        ["Calibration", escape(str(report.get("calibration_file") or "nahi diya gaya"))],
    ]
    info_table = Table(info, colWidths=[35 * mm, 120 * mm])
    info_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]))
    story += [info_table, Paragraph("Nateeja", heading)]
    story += [Paragraph(escape(line), body) for line in verdict_sentences(table, decision)]

    story.append(Paragraph("Poore number (FP16 se doori, kam = behtar)", heading))
    rows = [["Test set", "Variant", "KLD", "RMS dp (%)", "Top token same (%)"]]
    for domain, by_variant in table.items():
        for index, (variant, item) in enumerate(sorted(by_variant.items())):
            kld = f"{item['kld']:.4f} ± {item['kld_error']:.4f}"
            rms = f"{item['rms']:.2f}" if "rms" in item else "-"
            top = f"{item['same_top_p']:.2f}" if "same_top_p" in item else "-"
            rows.append([
                DOMAIN_LABELS.get(domain, domain) if index == 0 else "",
                variant_label(variant), kld, rms, top,
            ])

    data_table = Table(rows, colWidths=[38 * mm, 48 * mm, 30 * mm, 28 * mm, 32 * mm], repeatRows=1)
    data_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14213d")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cfd6e4")),
    ]))
    story += [data_table, Paragraph("Hum ye claim nahi karte", heading)]
    story += [Paragraph("• " + escape(text), small) for text in NOT_CLAIMED]

    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Sthamly report: {model}", author="Sthamly",
    ).build(story)
    return path


# ============================================================
# PACKAGING
# ============================================================

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def gguf_for_variant(model: str, variant: str) -> Path:
    """
    Expected quant file naming (from compress/prepare/worker):
        <owner>__<name>-Q4_K_M-standard.gguf
        <owner>__<name>-Q4_K_M-hindi.gguf
    """
    tag = model.replace("/", "__")
    if variant.endswith("standard"):
        suffix = "standard"
    else:
        suffix = variant.replace("q4_", "")
    return GGUF_DIR / f"{tag}-Q4_K_M-{suffix}.gguf"


def package(model: str, report_path: Path, report: dict[str, Any], decision: dict[str, Any],
            out_root: Path) -> Path:
    chosen = decision["best"] if decision["decision"] == "improved" else decision["baseline"]
    src = gguf_for_variant(model, chosen)
    if not src.is_file() or src.stat().st_size == 0:
        raise EngineError(f"Quant file nahi mili: {src}")

    owner, name = model.split("/", 1)
    out = out_root / f"{owner}__{name}"
    out.mkdir(parents=True, exist_ok=True)

    dst = out / src.name
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dst)

    manifest = {
        "model": model,
        "license": report.get("license", "unknown"),
        "file": dst.name,
        "variant": chosen,
        "size_gb": round(dst.stat().st_size / 1e9, 3),
        "sha256": sha256_file(dst),
        "decision": decision["decision"],
        "decision_reason": decision["reason"],
        "reduction_pct": decision["reduction_pct"],
        "clear_domains": decision["clear_domains"],
        "source_report": str(report_path.relative_to(ROOT)) if report_path.is_relative_to(ROOT) else report_path.name,
        "report_timestamp": report.get("timestamp"),
        "built": date.today().isoformat(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ============================================================
# COMMANDS
# ============================================================

def report_path_for(model: str) -> Path:
    slug = model.replace("/", "_").strip("._-").lower()
    return REPORTS_DIR / slug / "report.json"


def cmd_audit(model: str, args: argparse.Namespace) -> int:
    """Runs scripts/audit.py on existing FP16 and quant files, then shows the decision."""
    tag = model.replace("/", "__")
    fp16 = GGUF_DIR / f"{tag}-f16.gguf"
    plain = gguf_for_variant(model, PLAIN_VARIANT)
    hindi = gguf_for_variant(model, "q4_hindi")
    dataset = Path(args.dataset)
    calib = Path(args.calibration)

    for path, label in [(fp16, "FP16"), (plain, "plain Q4"), (hindi, "Hindi Q4"),
                        (dataset, "test dataset"), (calib, "calibration")]:
        if not path.is_file():
            print(f"MISSING {label}: {path}", file=sys.stderr)
            return 1

    report_path = report_path_for(model)
    command = [
        sys.executable, str(HERE / "audit.py"),
        "--name", model,
        "--fp16", str(fp16),
        "--dataset", f"hindi={dataset}",
        "--quant", f"q4_standard={plain}",
        "--quant", f"q4_hindi={hindi}",
        "--calibration", str(calib),
        "--out", str(report_path),
    ]
    completed = subprocess.run(command, cwd=str(ROOT), check=False)
    if completed.returncode not in (0, 2):
        print(f"audit.py failed with exit code {completed.returncode}", file=sys.stderr)
        return 1

    report = load_report(report_path)
    decision = decide(report)
    print(f"Decision: {decision['decision']} ({decision['reason']})")
    return 0


def cmd_build(model: str, args: argparse.Namespace) -> int:
    report_path = report_path_for(model)
    report = load_report(report_path)
    decision = decide(report)

    out = package(model, report_path, report, decision, DIST_DIR)
    make_pdf(out / "report.pdf", report=report, decision=decision)

    print(f"Package: {out}")
    print(f"Decision: {decision['decision']} -> {decision.get('best') or decision['baseline']}")
    return 0


def cmd_report(model: str, args: argparse.Namespace) -> int:
    report_path = Path(args.report) if args.report else report_path_for(model)
    report = load_report(report_path)
    decision = decide(report)
    out = Path(args.out) if args.out else DIST_DIR / "report.pdf"
    make_pdf(out, report=report, decision=decision)
    print(f"PDF: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sthamly Engine (real artifacts only).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("audit", "build", "report"):
        p = sub.add_parser(name)
        p.add_argument("--model", required=True)
        if name == "audit":
            p.add_argument("--dataset", required=True, help="Hindi held-out test file")
            p.add_argument("--calibration", required=True, help="Hindi imatrix calibration file")
        if name == "report":
            p.add_argument("--report", default=None, help="Path to report.json (default: reports/<slug>/)")
            p.add_argument("--out", default=None, help="Output PDF path")

    args = parser.parse_args(argv)

    model = normalize_model_id(args.model)
    if model is None:
        print("Model ID sahi nahi hai. Example: owner/name", file=sys.stderr)
        return 1

    try:
        if args.cmd == "audit":
            return cmd_audit(model, args)
        if args.cmd == "build":
            return cmd_build(model, args)
        return cmd_report(model, args)
    except (EngineError, OSError, ValueError, KeyError) as exc:
        print(f"ENGINE FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())