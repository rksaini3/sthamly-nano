"""Sthamly Engine: ek Hugging Face model lo -> audit -> optimize (imatrix) -> quantize -> package + PDF report.

    python scripts/engine.py audit  --model Qwen/Qwen2.5-1.5B-Instruct   # sirf naap: plain Q4 vs FP16
    python scripts/engine.py build  --model Qwen/Qwen2.5-1.5B-Instruct   # optimize + quantize + package
    python scripts/engine.py report --results results/results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json \\
                                    --model Qwen/Qwen2.5-1.5B-Instruct --out report.pdf

`build` ka output: dist/<model>/  ->  <model>-Q4_K_M-<variant>.gguf, manifest.json, report.pdf
Ye GGUF file Sthamly Desk (desk/sthamly_desk.py) me seedha chalti hai.

Ye scripts/worker.py ke wahi tukde use karta hai jo website ka free audit chalate hain (model check, safe environment,
disk check, result ka faisla), isliye dono ek hi tarah sochte hain. Yahan Supabase nahi chahiye.
Sirf public, non-gated, safetensors models (~4 GB tak; --max-gb se badha sakte ho). Custom code wale models refuse hote hain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import worker  # noqa: E402
from sthamly_common import normalize_model_id  # noqa: E402

DOMAIN_NAMES = {"wiki": "Hindi Wikipedia", "sangraha": "Sangraha (Hindi web text)"}
NOT_CLAIMED = [
    "Ye proxy naap hai (FP16 se doori). Sawal-jawab ya summary jaisi task accuracy nahi naapi gayi.",
    "Test sets chhote hain; error bar (±) dekhe bina kisi chhote fark par bharosa mat karo.",
    "Fark tabhi 'saaf' kaha gaya jab wo error se 2 guna bada tha.",
]


def variant_label(v: str) -> str:
    if v in ("standard", "plain"):
        return "Plain Q4_K_M (bina imatrix)"
    m = re.match(r"^(hindi|english|mixed)(\d*)$", v)
    if m:
        n = f" ({m.group(2)} chunks)" if m.group(2) else ""
        return f"{m.group(1).capitalize()} imatrix{n}"
    return v


def ordered_variants(variants: list[str]) -> list[str]:
    base = [v for v in variants if v in ("standard", "plain")]
    return base + sorted(v for v in variants if v not in base)


# ---------------------------------------------------------------- PDF report
def verdict_sentences(results: dict[str, dict[str, Any]], mode: str, decision: dict[str, Any] | None) -> list[str]:
    """Plain sentences, said only as far as the numbers go."""
    if mode == "audit":
        out = []
        for d, byv in results.items():
            p = byv.get("plain") or byv.get("standard")
            if p:
                out.append(f"{DOMAIN_NAMES.get(d, d)}: plain Q4_K_M ka KLD {p['kld'][0]:.4f} (± {p['kld'][1]:.4f}). "
                           "Kam KLD = FP16 ke jawab ke kareeb.")
        return out or ["Koi number nahi mila."]
    if not decision or not decision.get("best"):
        return ["Koi imatrix variant naapa nahi gaya."]
    if decision["decision"] != "improved":
        return ["Koi imatrix variant plain Q4 se saaf behtar nahi nikla (ya kisi test set me saaf kharab nikla). "
                "Isliye is package me plain Q4_K_M diya gaya hai."]
    out = [f"Sabse achha variant: {variant_label(decision['best'])}."]
    for d, g in decision["reduction_pct"].items():
        tag = "saaf behtar" if d in decision["clear_domains"] else "saaf fark nahi (sirf ishara)"
        out.append(f"{DOMAIN_NAMES.get(d, d)}: KLD me {g:.1f}% kam, {tag}.")
    return out


def make_pdf(path: Path, *, model: str, results: dict[str, dict[str, Any]], meta: dict[str, Any], mode: str,
             decision: dict[str, Any] | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=20, textColor=colors.HexColor("#14213d"), spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=13, textColor=colors.HexColor("#14213d"), spaceBefore=14)
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=10, leading=14)
    small = ParagraphStyle("small", parent=body, fontSize=8.5, textColor=colors.HexColor("#555555"))

    story: list[Any] = [Paragraph("Sthamly Hindi Quantization Report", h1),
                        Paragraph(escape(model), body), Spacer(1, 6)]
    info = [["Tareekh", date.today().isoformat()],
            ["Kaam", "Audit (plain Q4 vs FP16)" if mode == "audit" else "Audit + imatrix optimize + quantize"],
            ["License", str(meta.get("license") or "pata nahi")],
            ["Weights", f"{meta.get('weights_gb', '?')} GB (FP16 safetensors)"]]
    t = Table(info, colWidths=[35 * mm, 120 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd"))]))
    story += [t, Paragraph("Nateeja", h2)]
    story += [Paragraph(escape(s), body) for s in verdict_sentences(results, mode, decision)]

    story.append(Paragraph("Poore number (FP16 se doori, kam = behtar)", h2))
    rows = [["Test set", "Variant", "KLD", "RMS dp (%)", "Top token same (%)"]]
    for d, byv in results.items():
        for i, v in enumerate(ordered_variants(list(byv))):
            m = byv[v]
            fmt = lambda k, nd: f"{m[k][0]:.{nd}f} ± {m[k][1]:.{nd}f}" if k in m else "-"  # noqa: E731
            rows.append([DOMAIN_NAMES.get(d, d) if i == 0 else "", variant_label(v), fmt("kld", 4), fmt("rms", 2), fmt("top", 2)])
    tbl = Table(rows, colWidths=[38 * mm, 48 * mm, 30 * mm, 28 * mm, 32 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14213d")),
                             ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5f9")]),
                             ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cfd6e4"))]))
    story += [tbl, Paragraph("Hum ye claim nahi karte", h2)]
    story += [Paragraph("• " + escape(s), small) for s in NOT_CLAIMED]

    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title=f"Sthamly report: {model}", author="Sthamly").build(story)
    return path


# ---------------------------------------------------------------- packaging
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def package(cfg: worker.Config, model: str, meta: dict[str, Any], results: dict[str, Any],
            decision: dict[str, Any], out_root: Path) -> Path:
    """Copies the chosen GGUF (best imatrix variant if it is clearly better, else plain Q4) with manifest + PDF."""
    name = model.split("/", 1)[1]
    tag = model.replace("/", "__")
    chosen = decision["best"] if decision["decision"] == "improved" and decision["best"] else "standard"
    src = cfg.root / "models" / "gguf" / f"{tag}-Q4_K_M-{chosen}.gguf"
    if not src.is_file():
        raise RuntimeError(f"{src.name} nahi mili (quantize adhoora raha?)")
    out = out_root / name
    out.mkdir(parents=True, exist_ok=True)
    dst = out / f"{name}-Q4_K_M-{chosen}.gguf"
    shutil.copy2(src, dst)
    manifest = {"model": model, "license": meta.get("license"), "file": dst.name, "variant": chosen,
                "size_gb": round(dst.stat().st_size / 1e9, 3), "sha256": sha256_file(dst),
                "decision": decision["decision"], "reduction_pct": decision["reduction_pct"],
                "clear_domains": decision["clear_domains"], "results": results, "built": date.today().isoformat()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    make_pdf(out / "report.pdf", model=model, results=results, meta=meta, mode="build", decision=decision)
    return out


# ---------------------------------------------------------------- pipeline
def _say(line: str) -> None:
    print("   |", line, flush=True)


def _run(cfg: worker.Config, script: str, args: list[Any], env: dict[str, str], timeout: float, what: str) -> None:
    code, tail = worker.run_stream([cfg.python, HERE / script, *args], cwd=cfg.root, env=env, on_line=_say, timeout=timeout)
    if code == worker.CUSTOM_CODE_EXIT and script == "prepare_model.py":
        raise worker.Refused("Is model me custom code (auto_map) hai. Hum kisi doosre ka code nahi chalate.")
    if code != 0:
        raise RuntimeError(f"{what} fail hua (exit {code}): {tail[-300:]}")


def run_pipeline(model: str, mode: str, cfg: worker.Config, out_root: Path) -> Path:
    """mode 'audit': plain Q4 vs FP16 -> audit.json + report.pdf.  mode 'build': + imatrix variants -> GGUF package."""
    env = worker.clean_env()
    p = worker.model_paths(cfg, model)
    name = model.split("/", 1)[1]

    print("[1/5] Model Hugging Face par check", flush=True)
    meta = worker.check_model(worker.hf_model_info(model), cfg.max_weights_gb)
    print(f"      license: {meta['license']}, weights: {meta['weights_gb']} GB", flush=True)
    worker.check_disk(cfg, meta["weights_gb"] * (2 if mode == "build" else 1))
    worker.purge_model_files(cfg, model)
    try:
        print("[2/5] Download + FP16 GGUF + plain Q4 (ek hi FP16 se)", flush=True)
        _run(cfg, "prepare_model.py", ["--model", model], env, cfg.prepare_timeout, "prepare_model.py")
        for need in (p["f16"], p["std"]):
            if not Path(need).is_file():
                raise RuntimeError(f"prepare_model.py ke baad {Path(need).name} nahi mili")

        print("[3/5] Hindi test sets taiyaar", flush=True)
        worker.load_eval_sets(cfg)

        if mode == "audit":
            print("[4/5] FP16 se naap (plain Q4)", flush=True)
            rep_dir = cfg.root / "work" / "reports" / f"engine-{name}"
            _run(cfg, "audit.py", ["--name", name, "--fp16", p["f16"], "--quant", f"plain={p['std']}", "--base-model", model,
                                   "--chunks", cfg.chunks, "--threads", cfg.threads, "--llama", cfg.llama_bin,
                                   "--data-dir", cfg.root / "data", "--out", rep_dir], env, cfg.audit_timeout, "audit.py")
            reports = sorted(rep_dir.glob("*/report.json"))
            if not reports:
                raise RuntimeError("audit.py ne report.json nahi banaya")
            result = worker.build_audit_result(model, meta, json.loads(reports[0].read_text(encoding="utf-8")))
            print("[5/5] Report likhna", flush=True)
            out = out_root / name
            out.mkdir(parents=True, exist_ok=True)
            (out / "audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            make_pdf(out / "report.pdf", model=model, results=result["results"], meta=meta, mode="audit")
            return out

        calib = cfg.root / "data" / "hindi_calib.txt"
        if not calib.exists():
            print("      Hindi calibration text download ho raha hai (ek baar)", flush=True)
            _run(cfg, "download_hindi_data.py", [], dict(__import__("os").environ), 1800, "download_hindi_data.py")
        print("[4/5] Hindi / English / Mixed imatrix bana kar quantize + naap (sabse lamba step)", flush=True)
        _run(cfg, "trl4_bench.py", ["--model", model, "--threads", cfg.threads, "--chunks", cfg.chunks], env,
             cfg.audit_timeout * 2, "trl4_bench.py")
        rf = cfg.root / f"results_trl4_{model.replace('/', '__')}.json"
        if not rf.exists():
            raise RuntimeError("trl4_bench.py ne results file nahi banayi")
        results = json.loads(rf.read_text(encoding="utf-8"))
        decision = worker.calibrate_decision(results)
        print("[5/5] Best variant chun kar package", flush=True)
        return package(cfg, model, meta, results, decision, out_root)
    finally:
        if not cfg.keep_files:
            worker.purge_model_files(cfg, model)


# ---------------------------------------------------------------- CLI
def cmd_report(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.results).read_text(encoding="utf-8"))
    results = data["results"] if isinstance(data, dict) and "results" in data and "domains" in data else data
    mode = "audit" if all("standard" not in v for v in results.values()) else "build"
    decision = worker.calibrate_decision(results) if mode == "build" else None
    meta = {"license": data.get("license") if isinstance(data, dict) else None, "weights_gb": data.get("weights_gb", "?")
            if isinstance(data, dict) else "?"}
    out = make_pdf(Path(args.out), model=args.model, results=results, meta=meta, mode=mode, decision=decision)
    print("PDF taiyaar:", out)
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    model = normalize_model_id(args.model)
    if model is None:
        print("Model ID sahi nahi hai. Aise likho: owner/model-name")
        return 2
    cfg = worker.Config(root=ROOT, threads=args.threads, chunks=args.chunks, max_weights_gb=args.max_gb, keep_files=args.keep)
    try:
        out = run_pipeline(model, args.cmd, cfg, Path(args.out))
    except worker.Refused as e:
        print(f"\nNahi liya gaya: {e}")
        return 2
    except (RuntimeError, TimeoutError) as e:
        print(f"\nFAIL: {e}")
        return 1
    print(f"\nTaiyaar: {out}")
    for f in sorted(out.iterdir()):
        print(f"   {f.name}  ({f.stat().st_size / 1e6:.1f} MB)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sthamly Engine", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("audit", "sirf naap: plain Q4 vs FP16, PDF report"),
                        ("build", "optimize (imatrix) + quantize + package (.gguf + manifest + PDF)")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--model", required=True, help="owner/model-name ya Hugging Face URL")
        s.add_argument("--out", default=str(ROOT / "dist"))
        s.add_argument("--threads", type=int, default=2)
        s.add_argument("--chunks", type=int, default=12)
        s.add_argument("--max-gb", type=float, default=4.2, help="safetensors weights ki sima (GB)")
        s.add_argument("--keep", action="store_true", help="model/GGUF temporary files delete mat karo")
        s.set_defaults(fn=cmd_pipeline)
    r = sub.add_parser("report", help="kisi results JSON se PDF banao")
    r.add_argument("--results", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--out", default="report.pdf")
    r.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
