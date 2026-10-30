from datetime import datetime, timezone
from pathlib import Path
from typing import Dict
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)


def _n(v, d=2):
    return "-" if v is None else f"{v:.{d}f}"


def make_pdf(report: Dict, out_path: str) -> str:
    """Write report dict (from metrics.build_report) to a PDF. Invents nothing."""
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=8, leading=10)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white)

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(p), pagesize=landscape(A4),
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title="Sthamly Engine report")

    story = []
    story.append(Paragraph("Sthamly Engine: Before / After report", styles["Title"]))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph(
        f"Model: {escape(str(report['model']))} &nbsp;|&nbsp; "
        f"MACs per inference: {report['total_macs']:,} &nbsp;|&nbsp; "
        f"Weights: {report['total_params']:,} &nbsp;|&nbsp; "
        f"Grid units: {report['grid_units']} &nbsp;|&nbsp; "
        f"Baseline: {escape(report['baseline'])} &nbsp;|&nbsp; {now}", small))
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph(
        "Accuracy is <b>measured</b> (MNIST test set). Energy, time and area are "
        "<b>simulated estimates</b>. The graphene rows are a <b>hypothetical what-if</b>.",
        styles["BodyText"]))
    story.append(Spacer(1, 4 * mm))

    cols = ["Precision", "Tech", "Accuracy", "Energy (uJ)", "Time (us)",
            "Area (mm2)", "Weights (B)", "Energy save %", "Speed x", "Size x"]
    rows = [[Paragraph(c, head) for c in cols]]
    for r in report["results"]:
        tech = "graphene (what-if)" if r["tech"] == "graphene_what_if" else "silicon FPGA"
        rows.append([Paragraph(escape(str(x)), cell) for x in (
            r["precision"],
            tech,
            f"{r['accuracy'] * 100:.2f}% +/- {r['accuracy_se'] * 100:.2f} ({r['accuracy_verdict']})",
            _n(r["energy_uj"]),
            _n(r["time_us"], 1),
            _n(r["area_mm2"], 4),
            f"{r['weight_bytes']:,}",
            _n(r.get("energy_saving_pct"), 1),
            _n(r.get("speedup_x")),
            _n(r.get("size_reduction_x"), 1),
        )])

    t = Table(rows, repeatRows=1,
              colWidths=[22 * mm, 32 * mm, 52 * mm, 24 * mm, 22 * mm,
                         22 * mm, 24 * mm, 24 * mm, 16 * mm, 16 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
    ]))
    story.append(t)

    # what each precision name means (only when the report carries notes)
    notes = []
    seen_p = set()
    for r in report["results"]:
        if r["precision"] in seen_p or not r.get("precision_note"):
            continue
        seen_p.add(r["precision"])
        notes.append((r["precision"], r["precision_note"]))
    if notes:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Precision key", styles["Heading3"]))
        for name, note in notes:
            story.append(Paragraph(f"<b>{escape(name)}</b>: {escape(note)}", small))

    # simulation of the generated Verilog (only when present)
    sim = report.get("hardware_simulation")
    if sim:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Hardware simulation (generated Verilog)", styles["Heading3"]))
        exact = "YES" if sim.get("bit_exact") else "NO (see mismatches)"
        line = [
            f"Images checked: {sim.get('images_checked', '-')}",
            f"Bit-exact vs Python integer model: <b>{exact}</b>",
            f"Logit mismatches: {sim.get('logit_mismatches', '-')}",
            f"Prediction mismatches: {sim.get('pred_mismatches', '-')}",
        ]
        if sim.get("hw_correct") is not None:
            line.append(f"Correct on those images: {sim['hw_correct']}/{sim['hw_total']}")
        if sim.get("cycles_per_image"):
            line.append(
                f"Cycles per image: {sim['cycles_per_image']:,} "
                f"(about {sim['latency_us_at_assumed_clock']:.1f} us at an assumed "
                f"{sim['clock_mhz_assumed']:.0f} MHz)")
        if sim.get("lanes"):
            line.append(f"MAC lanes per cycle: {sim['lanes']}")
        for s in line:
            story.append(Paragraph("&bull; " + s, small))
        story.append(Paragraph(escape(sim.get("label", "")), small))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("What this report does NOT claim", styles["Heading3"]))
    for line_text in report["not_claimed"]:
        story.append(Paragraph("&bull; " + escape(line_text), small))

    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Assumptions per technology", styles["Heading3"]))
    seen = set()
    for r in report["results"]:
        if r["tech"] in seen:
            continue
        seen.add(r["tech"])
        story.append(Paragraph(f"<b>{escape(r['tech'])}</b>: {escape(r['assumptions'])}", small))

    doc.build(story)
    return str(p)