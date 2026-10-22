#!/usr/bin/env python3
"""
Sthamly NanoBrain - Developer PDF report

report.json (audit.py ka output) -> report.pdf
Isme: measured numbers, error-bar chart, pros / cons, aur saaf limitations.

Usage:
    python scripts/make_pdf.py --report reports/<name>/report.json
    python scripts/make_pdf.py --report reports/<name>/report.json --out my.pdf

Code se:
    from make_pdf import make_pdf
    pdf_path = make_pdf(Path("reports/<name>/report.json"))

Install (ek baar):
    pip install weasyprint
    # Linux/RunPod par agar error aaye:
    apt-get update && apt-get install -y libpango-1.0-0 libpangoft2-1.0-0

Rules:
    - Har number report.json se aata hai. Kuch invent nahi hota.
    - Pros/cons un numbers par threshold-rules se bante hain (neeche KLD_TIERS).
      Ye rules-of-thumb hain, guarantee nahi -- PDF me bhi yahi likha jata hai.
    - Model ka naam/labels user input ho sakte hain, isliye sab kuch HTML-escape hota hai
      aur PDF banate waqt koi bahari URL/file load nahi hoti.

Exit codes: 0 = PDF bani | 1 = nahi bani
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ---------------------------------------------------------------- thresholds
# Rules-of-thumb (sudhaar sakte ho). PDF me inhe publish kiya jata hai.
KLD_TIERS = [
    (0.005, "Very close to FP16", "good"),
    (0.02, "Close to FP16", "good"),
    (0.06, "Noticeable drift", "warn"),
    (math.inf, "Large drift", "bad"),
]
GOOD_TIERS = ("Very close to FP16", "Close to FP16")
MIN_CHUNKS = 50          # isse kam chunks = rough estimate
LOW_CONF_REL = 0.25      # error bar value ke 25% se bada = low confidence
GAP_RATIO = 1.5          # ek dataset par KLD doosre se 1.5x se zyada
SIG = 2.0                # difference > SIG x combined error  => "clear"
TOP_P_LOW = 90.0         # Same-top-p (%) isse neeche = ginti ke laayak mismatch
TOP_P_HIGH = 95.0

STATIC_WARNING_PREFIXES = (
    "KLD measures",
    "RMS and Same top-p",
    "Dataset results depend",
    "No quality improvement",
)


class ReportError(RuntimeError):
    """PDF nahi ban sakti (report kharab/khaali, library missing, ...)."""


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


# ------------------------------------------------------------------- loading
def _f(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _metric(value: Any) -> tuple[float | None, float | None]:
    """v2 style {'value','error'} ya [mean, error]."""
    if isinstance(value, dict):
        return _f(value.get("value")), _f(value.get("error"))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return _f(value[0]), _f(value[1])
    return None, None


def _file_gb(path_like: Any) -> float | None:
    try:
        p = Path(str(path_like))
        if p.is_file():
            return round(p.stat().st_size / 1e9, 4)
    except (OSError, ValueError):
        pass
    return None


def load_report(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReportError(f"report.json padha nahi ja saka: {exc}")

    if not isinstance(data, dict):
        raise ReportError("report.json ka format galat hai (object chahiye).")

    return data


def normalise(data: dict[str, Any]) -> dict[str, Any]:
    """Dono schemas (v1 list-wala, v2 dict-wala) ko ek shape me laata hai."""
    results = data.get("results")
    records: list[dict[str, Any]] = []
    errors: list[str] = [str(x) for x in (data.get("errors") or [])]
    sizes: dict[str, float | None] = {}

    def blank(quant: str, dataset: str) -> dict[str, Any]:
        return {
            "quant": quant, "dataset": dataset, "error": None,
            "kld": None, "kld_err": None, "rms": None, "rms_err": None,
            "top": None, "top_err": None, "ppl": None, "ppl_base": None,
        }

    if isinstance(results, list):                         # schema v1
        for item in results:
            if not isinstance(item, dict):
                continue
            r = blank(str(item.get("variant", "?")), str(item.get("domain", "?")))
            r["kld"], r["kld_err"] = _f(item.get("kld")), _f(item.get("kld_error"))
            r["rms"], r["rms_err"] = _f(item.get("rms")), _f(item.get("rms_error"))
            r["top"], r["top_err"] = _f(item.get("same_top_p")), _f(item.get("same_top_p_error"))
            r["ppl"], r["ppl_base"] = _f(item.get("ppl")), _f(item.get("ppl_base"))
            records.append(r)
            if r["quant"] not in sizes:
                sizes[r["quant"]] = _file_gb(item.get("model_path"))
        for name, gb in (data.get("quantized_sizes_gb") or {}).items():
            if _f(gb) is not None:
                sizes[str(name)] = _f(gb)
        for name, path in (data.get("quantized_models") or {}).items():
            if sizes.get(str(name)) is None:
                sizes[str(name)] = _file_gb(path)
        fp16_gb = _f(data.get("fp16_size_gb")) or _file_gb(data.get("fp16_model"))
    elif isinstance(results, dict):                        # schema v2
        for quant, per_ds in results.items():
            if not isinstance(per_ds, dict):
                continue
            for dataset, res in per_ds.items():
                r = blank(str(quant), str(dataset))
                if isinstance(res, dict):
                    if res.get("error"):
                        r["error"] = str(res["error"])
                    r["kld"], r["kld_err"] = _metric(res.get("kld"))
                    r["rms"], r["rms_err"] = _metric(res.get("rms"))
                    r["top"], r["top_err"] = _metric(res.get("top"))
                    r["ppl"], _ = _metric(res.get("ppl"))
                    r["ppl_base"], _ = _metric(res.get("ppl_base"))
                records.append(r)
        for name, meta in (data.get("variants_meta") or {}).items():
            if isinstance(meta, dict):
                sizes[str(name)] = _f(meta.get("size_gb"))
        ref = data.get("reference") if isinstance(data.get("reference"), dict) else {}
        fp16_gb = _f(ref.get("size_gb"))
    else:
        raise ReportError("report.json me 'results' nahi mila.")

    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    notes = [
        str(w) for w in (data.get("warnings") or data.get("audit_warnings") or [])
        if not str(w).startswith(STATIC_WARNING_PREFIXES)
    ]
    if isinstance(data.get("audit_warnings"), list) and data.get("warnings"):
        notes += [str(w) for w in data["audit_warnings"]]

    chunks = settings.get("chunks")
    ctx = settings.get("ctx_size")
    return {
        "model": str(data.get("target_model") or data.get("name") or "unknown model"),
        "created": str(data.get("timestamp") or data.get("created_at") or ""),
        "fp16_gb": fp16_gb,
        "sizes": sizes,
        "chunks": int(chunks) if isinstance(chunks, (int, float)) else None,
        "ctx": int(ctx) if isinstance(ctx, (int, float)) else None,
        "records": records,
        "errors": errors,
        "notes": notes,
        "datasets": data.get("datasets") if isinstance(data.get("datasets"), dict) else {},
    }


def load_meta(model: str, explicit: Path | None) -> dict[str, Any]:
    """prepare_model.py ki <name>.meta.json (license / commit). Na mile to khaali."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(ROOT / "models" / "gguf" / f"{model.replace('/', '__')}.meta.json")
    for path in candidates:
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError):
            continue
    return {}


# ------------------------------------------------------------------ analysis
def tier_of(kld: float) -> tuple[str, str]:
    for limit, label, css in KLD_TIERS:
        if kld < limit:
            return label, css
    return KLD_TIERS[-1][1], KLD_TIERS[-1][2]


def significance(a: float, ea: float | None, b: float, eb: float | None) -> str:
    """b - a ka matlab: a behtar (chhota) hai?  'clear' / 'not clear' / 'unknown'."""
    if ea is None or eb is None:
        return "unknown"
    return "clear" if (b - a) > SIG * math.sqrt(ea ** 2 + eb ** 2) else "not clear"


def norm_variant(label: str) -> tuple[str, bool]:
    """('q4_k_m_imatrix') -> ('q4_k_m', True)."""
    low = label.lower()
    is_imat = bool(re.search(r"imat", low))
    key = re.sub(r"[_.-]?(imatrix|imat|standard|std|plain)", "", low)
    return key.strip("_.-"), is_imat


def summarise(norm: dict[str, Any]) -> list[dict[str, Any]]:
    by_quant: dict[str, list[dict[str, Any]]] = {}
    for r in norm["records"]:
        by_quant.setdefault(r["quant"], []).append(r)

    out = []
    for quant, recs in by_quant.items():
        ok = [r for r in recs if r["kld"] is not None]
        size = norm["sizes"].get(quant)
        fp16 = norm["fp16_gb"]
        ratio = (fp16 / size) if (fp16 and size and size > 0) else None

        s: dict[str, Any] = {
            "quant": quant, "records": recs, "ok": ok, "size": size, "ratio": ratio,
            "worst": None, "tier": None, "css": "na", "low_conf": [],
            "pros": [], "cons": [],
        }

        if ok:
            worst = max(ok, key=lambda r: r["kld"])
            s["worst"] = worst
            s["tier"], s["css"] = tier_of(worst["kld"])

            for r in ok:
                if r["kld"] > 0 and r["kld_err"] is not None and r["kld_err"] / r["kld"] > LOW_CONF_REL:
                    s["low_conf"].append(r["dataset"])

            # ---- pros
            if size and ratio:
                s["pros"].append(f"{size:.2f} GB on disk - {ratio:.1f}x smaller than FP16 ({norm['fp16_gb']:.2f} GB).")
            if s["tier"] in GOOD_TIERS:
                s["pros"].append(
                    f"Output distribution stays {s['tier'].replace('Very close', 'very close').replace('Close', 'close')}: "
                    f"worst-case Mean KLD {worst['kld']:.4f} (on '{worst['dataset']}')."
                )
            for r in ok:
                if r["top"] is not None and r["top"] >= TOP_P_HIGH:
                    s["pros"].append(f"Picks the same top token as FP16 {r['top']:.1f}% of the time on '{r['dataset']}'.")
                    break

            # ---- cons
            if s["tier"] not in GOOD_TIERS:
                s["cons"].append(
                    f"{s['tier']}: worst-case Mean KLD {worst['kld']:.4f} on '{worst['dataset']}' "
                    f"(rule-of-thumb limit for 'Close' is {KLD_TIERS[1][0]})."
                )
            for r in ok:
                if r["top"] is not None and r["top"] < TOP_P_LOW:
                    s["cons"].append(
                        f"Top token differs from FP16 about {100 - r['top']:.1f}% of the time on '{r['dataset']}'."
                    )
            if len(ok) >= 2:
                hi = max(ok, key=lambda r: r["kld"])
                lo = min(ok, key=lambda r: r["kld"])
                if lo["kld"] > 0 and hi["kld"] / lo["kld"] >= GAP_RATIO and \
                        significance(lo["kld"], lo["kld_err"], hi["kld"], hi["kld_err"]) == "clear":
                    s["cons"].append(
                        f"Quantization hurts '{hi['dataset']}' more: KLD {hi['kld']:.4f} vs "
                        f"{lo['kld']:.4f} on '{lo['dataset']}' ({hi['kld'] / lo['kld']:.1f}x higher)."
                    )
            if s["low_conf"]:
                s["cons"].append(
                    "Low confidence (error bar > 25% of the value) on: " + ", ".join(f"'{d}'" for d in s["low_conf"]) + "."
                )
        for r in recs:
            if r["error"]:
                s["cons"].append(f"Benchmark failed on '{r['dataset']}': {r['error']}")
        if not size:
            s["cons"].append("File size not recorded in the report.")
        out.append(s)
    return out


def comparisons(norm: dict[str, Any], summaries: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    datasets = sorted({r["dataset"] for r in norm["records"] if r["kld"] is not None})

    for ds in datasets:
        rs = sorted((r for r in norm["records"] if r["dataset"] == ds and r["kld"] is not None),
                    key=lambda r: r["kld"])
        if len(rs) >= 2:
            a, b = rs[0], rs[1]
            sig = significance(a["kld"], a["kld_err"], b["kld"], b["kld_err"])
            verdict = {"clear": "a clear difference (> 2x combined error)",
                       "not clear": "NOT a clear difference (within 2x combined error)",
                       "unknown": "significance unknown (no error bars)"}[sig]
            lines.append(
                f"On '{ds}': '{a['quant']}' has the lowest KLD ({a['kld']:.4f}) vs '{b['quant']}' ({b['kld']:.4f}) - {verdict}."
            )

        # imatrix vs plain, same quant type
        for ra in rs:
            ka, ia = norm_variant(ra["quant"])
            if not ia:
                continue
            for rb in rs:
                kb, ib = norm_variant(rb["quant"])
                if ib or kb != ka:
                    continue
                delta = rb["kld"] - ra["kld"]          # +ve => imatrix ka KLD kam hai
                comb = math.sqrt((ra["kld_err"] or 0) ** 2 + (rb["kld_err"] or 0) ** 2)
                sig = significance(ra["kld"], ra["kld_err"], rb["kld"], rb["kld_err"])
                direction = "lower" if delta >= 0 else "higher"
                strength = {"clear": "a clear difference", "not clear": "NOT a clear difference",
                            "unknown": "significance unknown"}[sig]
                if delta < 0 and sig == "clear":
                    strength = "imatrix looks worse here"
                lines.append(
                    f"imatrix vs plain ({ra['quant']} vs {rb['quant']}) on '{ds}': imatrix KLD is {direction} by "
                    f"{abs(delta):.4f} (+/- {comb:.4f}) - {strength}. "
                    "Valid only if the test text was NOT used for calibration."
                )
    return lines


def recommendation(summaries: list[dict[str, Any]]) -> str:
    usable = [s for s in summaries if s["ok"] and s["tier"] in GOOD_TIERS and not s["low_conf"]]
    if not usable:
        return ("None of the tested variants reached 'Close to FP16' with confidence. "
                "Consider a higher-bit quant (e.g. Q5_K_M, Q6_K, Q8_0) - not tested here.")
    usable.sort(key=lambda s: (s["size"] is None, s["size"] or 0))
    best = usable[0]
    return (f"Smallest variant that stays within 'Close to FP16' on every tested dataset: "
            f"'{best['quant']}'" + (f" ({best['size']:.2f} GB)." if best["size"] else "."))


# --------------------------------------------------------------------- chart
CHART_COLORS = {"good": "#15803d", "warn": "#b45309", "bad": "#b42318", "na": "#6b7280"}


def svg_chart(norm: dict[str, Any]) -> str:
    rows = [r for r in norm["records"] if r["kld"] is not None]
    if not rows:
        return ""

    label_w, bar_w, row_h, pad_top = 210, 380, 26, 22
    width = label_w + bar_w + 90
    height = pad_top + row_h * len(rows) + 22
    top = max(r["kld"] + (r["kld_err"] or 0) for r in rows) * 1.12 or 1.0

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="Mean KLD per variant with error bars">',
        f'<text x="{label_w}" y="12" font-size="9" fill="#6b7280">Mean KLD vs FP16 (lower = closer). Whiskers = measured error.</text>',
        f'<line x1="{label_w}" y1="{pad_top - 4}" x2="{label_w}" y2="{height - 18}" stroke="#9ca3af"/>',
    ]
    for i, r in enumerate(rows):
        y = pad_top + i * row_h
        _, css = tier_of(r["kld"])
        color = CHART_COLORS[css]
        w = max(1.0, r["kld"] / top * bar_w)
        label = f"{r['quant']} / {r['dataset']}"
        if len(label) > 34:
            label = label[:33] + "..."
        parts.append(f'<text x="{label_w - 8}" y="{y + 14}" font-size="10" text-anchor="end" fill="#1f2937">{e(label)}</text>')
        parts.append(f'<rect x="{label_w}" y="{y + 3}" width="{w:.1f}" height="14" rx="2" fill="{color}"/>')
        if r["kld_err"]:
            x1 = label_w + max(0.0, (r["kld"] - r["kld_err"]) / top * bar_w)
            x2 = label_w + (r["kld"] + r["kld_err"]) / top * bar_w
            parts.append(f'<line x1="{x1:.1f}" y1="{y + 10}" x2="{x2:.1f}" y2="{y + 10}" stroke="#111827" stroke-width="1.5"/>')
            parts.append(f'<line x1="{x1:.1f}" y1="{y + 6}" x2="{x1:.1f}" y2="{y + 14}" stroke="#111827"/>')
            parts.append(f'<line x1="{x2:.1f}" y1="{y + 6}" x2="{x2:.1f}" y2="{y + 14}" stroke="#111827"/>')
            text_x = x2 + 6
        else:
            text_x = label_w + w + 6
        parts.append(f'<text x="{text_x:.1f}" y="{y + 14}" font-size="10" fill="#1f2937">{r["kld"]:.4f}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------------- html
CSS = """
@page { size: A4; margin: 16mm 14mm 18mm 14mm;
  @bottom-center { content: "Sthamly NanoBrain - audit report  |  page " counter(page) " of " counter(pages);
                   font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif; font-size: 8pt; color: #6b7280; } }
* { box-sizing: border-box; }
body { font-family: "DejaVu Sans", "Liberation Sans", Arial, sans-serif; font-size: 9.2pt; line-height: 1.5; color: #1f2937; }
h1 { font-size: 20pt; color: #0f2742; margin: 0 0 2pt; }
h2 { font-size: 12.5pt; color: #0f766e; margin: 16pt 0 5pt; border-bottom: 1.5pt solid #0f766e; padding-bottom: 2pt; page-break-after: avoid; }
h3 { font-size: 10.5pt; color: #0f2742; margin: 10pt 0 3pt; page-break-after: avoid; }
p { margin: 0 0 5pt; } ul { margin: 0 0 5pt; padding-left: 14pt; } li { margin-bottom: 2pt; }
.sub { color: #6b7280; font-size: 9pt; margin-bottom: 8pt; word-break: break-all; }
.meta { width: 100%; border-collapse: collapse; margin-bottom: 6pt; }
.meta td { padding: 2pt 6pt 2pt 0; vertical-align: top; border: 0; }
.meta td:first-child { color: #6b7280; width: 26%; }
table.t { width: 100%; border-collapse: collapse; font-size: 8.2pt; margin: 4pt 0 8pt; }
table.t th { background: #0f2742; color: #fff; text-align: left; padding: 4pt 4pt; }
table.t td { border-bottom: 0.6pt solid #d6dde6; padding: 3.5pt 4pt; vertical-align: top; word-break: break-word; }
table.t tr { page-break-inside: avoid; }
.badge { display: inline-block; padding: 1pt 5pt; border-radius: 3pt; color: #fff; font-size: 7.6pt; font-weight: bold; }
.good { background: #15803d; } .warn { background: #b45309; } .bad { background: #b42318; } .na { background: #6b7280; }
.card { border: 1pt solid #d6dde6; border-radius: 4pt; padding: 7pt 9pt; margin: 6pt 0; page-break-inside: avoid; }
.card h3 { margin-top: 0; }
.cols { width: 100%; border-collapse: separate; border-spacing: 6pt 0; margin: 0 -6pt; }
.cols td { width: 50%; vertical-align: top; border: 0; padding: 0; }
.pro h4 { color: #15803d; margin: 0 0 2pt; font-size: 9pt; } .con h4 { color: #b42318; margin: 0 0 2pt; font-size: 9pt; }
.note { background: #f3f6f9; border-left: 3pt solid #0f766e; padding: 6pt 9pt; margin: 6pt 0; page-break-inside: avoid; }
.warnbox { background: #fdf3f2; border-left: 3pt solid #b42318; padding: 6pt 9pt; margin: 6pt 0; page-break-inside: avoid; }
.small { font-size: 8pt; color: #6b7280; }
"""


def fmt_pm(value: float | None, err: float | None, digits: int = 6, unit: str = "") -> str:
    if value is None:
        return "n/a"
    text = f"{value:.{digits}f}"
    if err is not None:
        text += f" &plusmn; {err:.{digits}f}"
    return text + unit


def build_html(norm: dict[str, Any], meta: dict[str, Any], summaries: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model = norm["model"]

    meta_rows = [("Model", e(model))]
    if meta.get("revision"):
        meta_rows.append(("Source commit", e(str(meta["revision"])[:12])))
    meta_rows.append(("License (Hugging Face)", e(meta.get("license") or "not recorded") +
                      ' <span class="small">- check its terms before redistributing the GGUF</span>'))
    if norm["fp16_gb"]:
        meta_rows.append(("FP16 reference size", f"{norm['fp16_gb']:.2f} GB"))
    settings = []
    if norm["chunks"] is not None:
        settings.append(f"{norm['chunks']} chunks")
    if norm["ctx"] is not None:
        settings.append(f"context {norm['ctx']}")
    if settings:
        meta_rows.append(("Test settings", e(", ".join(settings))))
    ds_text = []
    for name, info in norm["datasets"].items():
        if isinstance(info, dict) and info.get("bytes"):
            ds_text.append(f"{e(name)} ({int(info['bytes']) / 1000:.0f} KB)")
        else:
            ds_text.append(e(name))
    if ds_text:
        meta_rows.append(("Datasets", ", ".join(ds_text)))
    meta_rows.append(("Generated", e(norm["created"] or now)))

    meta_html = '<table class="meta">' + "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in meta_rows) + "</table>"

    summary_rows = []
    for s in summaries:
        worst = s["worst"]
        badge = '<span class="badge ' + s["css"] + '">' + e(s["tier"] or "No result") + "</span>"
        conf = "low" if s["low_conf"] else ("ok" if s["ok"] else "-")
        size_txt = "%.2f GB" % s["size"] if s["size"] else "n/a"
        ratio_txt = "%.1fx" % s["ratio"] if s["ratio"] else "n/a"
        kld_txt = "%.4f" % worst["kld"] if worst else "n/a"
        summary_rows.append(
            "<tr><td><b>" + e(s["quant"]) + "</b></td><td>" + size_txt + "</td><td>" + ratio_txt +
            "</td><td>" + kld_txt + "</td><td>" + badge + "</td><td>" + conf + "</td></tr>"
        )

    detail_rows = []
    for r in norm["records"]:
        if r["error"]:
            detail_rows.append(f"<tr><td>{e(r['quant'])}</td><td>{e(r['dataset'])}</td>"
                               f"<td colspan='5'>FAILED: {e(r['error'])}</td></tr>")
            continue
        ppl = "n/a"
        if r["ppl"] is not None:
            ppl = f"{r['ppl']:.3f}" + (f" (FP16 {r['ppl_base']:.3f})" if r["ppl_base"] is not None else "")
        detail_rows.append(
            f"<tr><td>{e(r['quant'])}</td><td>{e(r['dataset'])}</td>"
            f"<td>{fmt_pm(r['kld'], r['kld_err'])}</td>"
            f"<td>{fmt_pm(r['rms'], r['rms_err'], 3, ' %')}</td>"
            f"<td>{fmt_pm(r['top'], r['top_err'], 2, ' %')}</td>"
            f"<td>{e(ppl)}</td></tr>"
        )

    cards = []
    for s in summaries:
        pros = "".join(f"<li>{e(x)}</li>" for x in s["pros"]) or "<li>No measured advantage to report.</li>"
        cons = "".join(f"<li>{e(x)}</li>" for x in s["cons"]) or "<li>No issue found in the measured data.</li>"
        cards.append(
            f'<div class="card"><h3>{e(s["quant"])} <span class="badge {s["css"]}">{e(s["tier"] or "No result")}</span></h3>'
            '<table class="cols"><tr>'
            f'<td class="pro"><h4>Pros</h4><ul>{pros}</ul></td>'
            f'<td class="con"><h4>Cons</h4><ul>{cons}</ul></td>'
            "</tr></table></div>"
        )

    cmp_lines = comparisons(norm, summaries)
    cmp_html = "<ul>" + "".join(f"<li>{e(x)}</li>" for x in cmp_lines) + "</ul>" if cmp_lines else "<p>Only one variant/dataset - nothing to compare.</p>"

    warn_items = list(norm["notes"])
    if norm["chunks"] is not None and norm["chunks"] < MIN_CHUNKS:
        warn_items.append(f"Only {norm['chunks']} chunks were evaluated (< {MIN_CHUNKS}); treat the numbers as a rough estimate.")
    warn_items += norm["errors"]
    warn_html = ""
    if warn_items:
        warn_html = '<div class="warnbox"><b>Data-quality warnings from this run</b><ul>' + \
                    "".join(f"<li>{e(x)}</li>" for x in warn_items) + "</ul></div>"

    tier_items = []
    for limit, label, css in KLD_TIERS:
        rule = ("KLD &lt; " + str(limit)) if limit != math.inf else ("KLD &ge; " + str(KLD_TIERS[-2][0]))
        tier_items.append("<li><span class='badge " + css + "'>" + e(label) + "</span> " + rule + "</li>")
    tiers_html = "".join(tier_items)

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Quantization audit - {e(model)}</title><style>{CSS}</style></head>
<body>
<h1>Quantization audit</h1>
<div class="sub">{e(model)}</div>
{meta_html}

<div class="note"><b>Suggested starting point (from these numbers only):</b> {e(recommendation(summaries))}</div>
{warn_html}

<h2>1. Summary</h2>
<table class="t"><tr><th>Variant</th><th>Size</th><th>vs FP16</th><th>Worst-case KLD</th><th>Fidelity</th><th>Confidence</th></tr>
{''.join(summary_rows)}</table>
{svg_chart(norm)}

<h2>2. Measured results</h2>
<table class="t"><tr><th>Variant</th><th>Dataset</th><th>Mean KLD &plusmn; error</th><th>RMS &Delta;p</th><th>Same top-p</th><th>Perplexity</th></tr>
{''.join(detail_rows)}</table>

<h2>3. Pros and cons per variant</h2>
{''.join(cards)}

<h2>4. Comparisons</h2>
{cmp_html}

<h2>5. How to read this report</h2>
<ul>
<li><b>Mean KLD</b> - how far the quantized model's next-token probabilities are from the FP16 model on the test text. Lower = closer. It is measured by llama.cpp's <i>llama-perplexity</i>.</li>
<li><b>Same top-p</b> - how often both models pick the same most-likely token. <b>RMS &Delta;p</b> - average change in token probability.</li>
<li><b>&plusmn; error</b> - statistical error of the measurement. A difference smaller than about 2x the combined error is not a reliable difference.</li>
<li><b>Fidelity labels</b> are simple rules-of-thumb on the worst dataset (not a standard):<ul>{tiers_html}</ul></li>
<li><b>Size</b> is the GGUF file size. Runtime memory is higher (KV cache and overhead) and was not measured here.</li>
</ul>

<h2>6. Limitations - please read</h2>
<div class="warnbox"><ul>
<li>KLD measures similarity to the FP16 model, <b>not task accuracy</b>. No Hindi (or any) task benchmark was run in this report.</li>
<li>Results hold only for the datasets listed above, this llama.cpp build, and this tokenization. Another domain can behave differently.</li>
<li>A quantized model is never better than its FP16 source in this measurement; calibration (imatrix) can only reduce the loss.</li>
<li>Pros/cons are generated from measured numbers with fixed rules. They are a starting point for your own testing, not a guarantee.</li>
</ul></div>
</body></html>"""


# ------------------------------------------------------------------ public API
def _no_fetch(url: str, *args: Any, **kwargs: Any) -> Any:
    raise ValueError(f"External resource blocked: {url[:60]}")


def make_pdf(report_path: Path, out_path: Path | None = None, meta_path: Path | None = None) -> Path:
    report_path = Path(report_path)
    data = load_report(report_path)
    norm = normalise(data)

    if not any(r["kld"] is not None for r in norm["records"]):
        raise ReportError("Report me koi measured KLD nahi hai -- khaali PDF nahi banaunga.")

    meta = load_meta(norm["model"], meta_path)
    summaries = summarise(norm)
    document = build_html(norm, meta, summaries)

    try:
        from weasyprint import HTML
    except Exception as exc:  # ImportError ya pango library missing (OSError)
        raise ReportError(
            f"WeasyPrint load nahi hua ({type(exc).__name__}: {exc}). "
            "`pip install weasyprint` chalao; Linux par: "
            "apt-get install -y libpango-1.0-0 libpangoft2-1.0-0"
        )

    out_path = Path(out_path) if out_path else report_path.with_suffix(".pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part = out_path.with_name(out_path.name + ".part")
    part.unlink(missing_ok=True)

    try:
        HTML(string=document, url_fetcher=_no_fetch).write_pdf(str(part))
        if not part.is_file() or part.stat().st_size == 0:
            raise ReportError("PDF khaali bani.")
        os.replace(part, out_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sthamly: audit report.json -> developer PDF")
    parser.add_argument("--report", required=True, help="audit.py ka report.json")
    parser.add_argument("--out", default=None, help="PDF path (default: report.json ke saath report.pdf)")
    parser.add_argument("--meta", default=None, help="prepare_model.py ki <name>.meta.json (optional)")
    args = parser.parse_args(argv)

    try:
        pdf = make_pdf(Path(args.report), Path(args.out) if args.out else None,
                       Path(args.meta) if args.meta else None)
    except ReportError as exc:
        print(f"PDF FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"PDF FAILED (unexpected): {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"PDF: {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())