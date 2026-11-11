"""HTML report builder for hindicc.

Reads the JSON records written by bench/runner.py and bench/thermal.py and
writes one static HTML file. Every number is shown next to its conditions
(device, threads, token counts, model hash). Anything that was not measured
is shown as "not measured". Nothing is estimated or rounded to look better.

This module does not decide pass or fail. The quality gate result is shown
only if it is provided as a record.

Usage (from the hindicc/ root):

    PYTHONPATH=. python report/build.py
    PYTHONPATH=. python report/build.py --bench results/bench --thermal results/thermal \
        --out results/report.html
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_BENCH = Path("results/bench")
DEFAULT_THERMAL = Path("results/thermal")
DEFAULT_OUT = Path("results/report.html")
NOT_MEASURED = "not measured"


def load_records(directory: Path) -> tuple[list[dict], list[str]]:
    """Load every JSON record in a directory. Returns (records, warnings)."""
    records: list[dict] = []
    warnings: list[str] = []
    if not directory.exists():
        return records, [f"{directory} does not exist; nothing loaded from it"]
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"skipped {path.name}: {exc}")
            continue
        if not isinstance(data, dict):
            warnings.append(f"skipped {path.name}: not a JSON object")
            continue
        data["_file"] = path.name
        records.append(data)
    records.sort(key=lambda r: str(r.get("timestamp_utc", "")))
    return records, warnings


def fmt(value, suffix: str = "", digits: int | None = None) -> str:
    """Format a value, or return NOT_MEASURED if it is missing."""
    if value is None:
        return NOT_MEASURED
    if digits is not None and isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def tps_cell(block: dict | None) -> str:
    if not block or block.get("tokens_per_sec") is None:
        return NOT_MEASURED
    tps = block["tokens_per_sec"]
    std = block.get("stddev_tokens_per_sec")
    if std is None:
        return f"{tps:.2f} tok/s (spread not reported)"
    return f"{tps:.2f} ± {std:.2f} tok/s"


def bench_rows(records: list[dict]) -> str:
    if not records:
        return f"<p>{NOT_MEASURED}: no bench records found.</p>"
    rows = []
    for r in records:
        model = r.get("model", {})
        params = r.get("params", {})
        sha = model.get("sha256") or ""
        status = "ok" if r.get("returncode") == 0 and not r.get("parse_error") else "failed"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('timestamp_utc', NOT_MEASURED)))}</td>"
            f"<td>{html.escape(Path(model.get('path', '')).name or NOT_MEASURED)}"
            f"<br><small>sha256 {html.escape(sha[:12])}…</small></td>"
            f"<td>{fmt(params.get('threads'))}</td>"
            f"<td>{fmt(params.get('prompt_tokens'))} / {fmt(params.get('gen_tokens'))}</td>"
            f"<td>{html.escape(tps_cell(r.get('prefill')))}</td>"
            f"<td>{html.escape(tps_cell(r.get('decode')))}</td>"
            f"<td>{fmt(r.get('peak_rss_mb'), ' MB')}</td>"
            f"<td>{html.escape(status)}</td>"
            f"<td><small>{html.escape(r['_file'])}</small></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>UTC time</th><th>Model</th><th>Threads</th><th>Prefill / decode tokens</th>"
        "<th>Prefill</th><th>Decode</th><th>Peak RSS</th><th>Status</th><th>Record</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def thermal_rows(records: list[dict]) -> str:
    if not records:
        return f"<p>{NOT_MEASURED}: no thermal records found.</p>"
    rows = []
    for r in records:
        drift = r.get("drift", {})
        if drift.get("enough_samples"):
            change = fmt(drift.get("change_percent"), " %", 2)
            detail = (
                f"start median {fmt(drift.get('start_median_tps'), ' tok/s', 2)}, "
                f"end median {fmt(drift.get('end_median_tps'), ' tok/s', 2)}"
            )
        else:
            change = NOT_MEASURED
            detail = f"only {drift.get('samples', 0)} samples (need more to compare ends)"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(r.get('timestamp_utc', NOT_MEASURED)))}</td>"
            f"<td>{html.escape(Path(r.get('model', {}).get('path', '')).name or NOT_MEASURED)}</td>"
            f"<td>{fmt(r.get('params', {}).get('minutes'), ' min')}</td>"
            f"<td>{html.escape(detail)}</td>"
            f"<td>{html.escape(change)}</td>"
            f"<td>{html.escape(str(r.get('stop_reason', NOT_MEASURED)))}</td>"
            f"<td><small>{html.escape(r['_file'])}</small></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>UTC time</th><th>Model</th><th>Run length</th><th>Start vs end</th>"
        "<th>Change</th><th>Stop reason</th><th>Record</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def device_section(records: list[dict]) -> str:
    if not records:
        return f"<p>{NOT_MEASURED}: no device information.</p>"
    device = records[-1].get("device", {})
    items = [
        ("CPU", device.get("cpu_model")),
        ("CPU count", device.get("cpu_count")),
        ("RAM (MB)", device.get("ram_total_mb")),
        ("Platform", device.get("platform")),
        ("Machine", device.get("machine")),
    ]
    lis = "".join(
        f"<li>{html.escape(label)}: {html.escape(fmt(value))}</li>" for label, value in items
    )
    return (
        "<p>From the most recent bench record. Each row above is tied to its own record file.</p>"
        f"<ul>{lis}</ul>"
    )


PAGE = """<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<title>hindicc report</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1a1a1a; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 0.9rem; margin-bottom: 2rem; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; vertical-align: top; text-align: left; }}
  th {{ background: #f2f2f2; }}
  small {{ color: #555; }}
  .note {{ background: #fff8e1; border-left: 4px solid #f0b400; padding: 0.6rem 0.9rem; }}
</style>
</head>
<body>
<h1>hindicc report</h1>
<p class="note">Estimates are not measurements. Every number below comes from a recorded run,
with its conditions shown. Values that were not measured say "{nm}".</p>
<p>Generated: {generated}</p>

<h2>Device</h2>
{device}

<h2>Speed (llama-bench)</h2>
{bench}

<h2>Thermal drift</h2>
{thermal}

</body>
</html>
"""


def build_html(bench: list[dict], thermal: list[dict]) -> str:
    return PAGE.format(
        nm=NOT_MEASURED,
        generated=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        device=device_section(bench),
        bench=bench_rows(bench),
        thermal=thermal_rows(thermal),
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="report", description="build the hindicc HTML report")
    p.add_argument("--bench", type=Path, default=DEFAULT_BENCH)
    p.add_argument("--thermal", type=Path, default=DEFAULT_THERMAL)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bench, bench_warnings = load_records(args.bench)
    thermal, thermal_warnings = load_records(args.thermal)
    for warning in bench_warnings + thermal_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_html(bench, thermal), encoding="utf-8")
    print(f"Report written: {args.out}")
    print(f"bench records: {len(bench)}, thermal records: {len(thermal)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())