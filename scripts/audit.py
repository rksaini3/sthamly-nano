#!/usr/bin/env python3
"""
Sthamly NanoBrain - Hindi Quantization Audit

Compare quantized GGUF models against an FP16 reference using
llama.cpp's llama-perplexity tool.

Outputs:
    reports/<name>/report.json
    reports/<name>/report.md
    reports/<name>/report.html

Example:
    python scripts/audit.py \
        --name Qwen2.5-1.5B-Instruct \
        --fp16 models/gguf/model-f16.gguf \
        --dataset hindi=data/hindi-test.txt \
        --quant q4=models/gguf/model-Q4_K_M.gguf

Requirements:
    - A compatible llama.cpp llama-perplexity executable.
    - An FP16 GGUF reference model.
    - One or more quantized GGUF models.
    - One or more plain-text test datasets.

Important:
    Metrics are measured from real benchmark output.
    This script does not invent benchmark results.
"""

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys

from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def die(message, exit_code=1):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


def slugify(value):
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-").lower()
    return result or "model"


def resolve_path(value):
    path = Path(value).expanduser()

    if not path.is_absolute():
        path = Path.cwd() / path

    return path.resolve()


def require_file(value, description):
    path = resolve_path(value)

    if not path.is_file():
        die(f"{description} file nahi mili: {path}")

    if path.stat().st_size == 0:
        die(f"{description} file khaali hai: {path}")

    return path


def parse_label_paths(items, option_name):
    """
    Parse repeated arguments in label=path format.
    """
    result = []
    seen = set()

    for item in items:
        if "=" not in item:
            die(
                f"{option_name} ke liye label=path format use karein: "
                f"{item!r}"
            )

        label, raw_path = item.split("=", 1)

        label = label.strip()
        raw_path = raw_path.strip()

        if not LABEL_RE.fullmatch(label):
            die(
                f"Invalid label {label!r}. "
                "Letters, digits, dot, underscore aur hyphen use karein."
            )

        if label in seen:
            die(f"Duplicate label: {label}")

        if not raw_path:
            die(f"{option_name} me path khaali hai: {item!r}")

        path = require_file(raw_path, f"{option_name} ({label})")

        seen.add(label)
        result.append((label, path))

    return result


def find_perplexity_binary(user_value):
    """
    Find llama-perplexity.

    Explicit --perplexity-bin takes priority, followed by PATH and
    common llama.cpp build locations.
    """
    candidates = []

    if user_value:
        candidates.append(user_value)

    path_binary = shutil.which("llama-perplexity")

    if path_binary:
        candidates.append(path_binary)

    candidates.extend(
        [
            str(ROOT / "llama.cpp" / "build" / "bin" / "llama-perplexity"),
            str(ROOT / "llama.cpp" / "build" / "bin" / "perplexity"),
            str(ROOT / "build" / "bin" / "llama-perplexity"),
            str(ROOT / "build" / "bin" / "perplexity"),
        ]
    )

    for candidate in candidates:
        candidate_path = Path(candidate).expanduser()

        if candidate_path.is_file():
            if os.name == "nt":
                return str(candidate_path.resolve())

            if os.access(candidate_path, os.X_OK):
                return str(candidate_path.resolve())

    die(
        "llama-perplexity executable nahi mili. "
        "--perplexity-bin se executable ka sahi path dein."
    )


def parse_metric(output, patterns, metric_name):
    """
    Extract metric value and uncertainty from actual tool output.

    Accepted examples:
        Mean KLD: 0.000551 ± 0.000002
        RMS Δp: 0.787 ± 0.004 %
        Same top p: 99.739 ± 0.013 %
    """
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)

        if match:
            value = float(match.group(1))
            error = float(match.group(2))

            if not math.isfinite(value) or not math.isfinite(error):
                raise ValueError(
                    f"{metric_name} me non-finite number mila."
                )

            if error < 0:
                raise ValueError(
                    f"{metric_name} uncertainty negative hai."
                )

            return {
                "value": value,
                "error": error,
            }

    return None


METRIC_PATTERNS = {
    "kld": [
        r"Mean\s+KLD\s*[:=]\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\s*/\s*-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    ],
    "rms": [
        r"RMS\s*(?:Δp|dp|delta\s*p)?\s*[:=]\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\s*/\s*-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*%?"
    ],
    "top": [
        r"Same\s+top\s*p\s*[:=]\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\s*/\s*-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*%?"
    ],
}


def parse_metrics(output):
    result = {}

    for name, patterns in METRIC_PATTERNS.items():
        metric = parse_metric(output, patterns, name)

        if metric is not None:
            result[name] = metric

    return result


def parse_ppl(output):
    patterns = [
        r"Final estimate:\s*PPL\s*=\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"(?:\s*(?:±|\+/-|\+\s*/\s*-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?))?"
    ]

    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)

        if match:
            value = float(match.group(1))
            error = (
                float(match.group(2))
                if match.group(2) is not None
                else None
            )

            return {
                "value": value,
                "error": error,
            }

    return None


def run_command(command, timeout, label):
    """
    Execute a benchmark and return its combined output.

    The command is passed as an argument list, not through a shell.
    """
    print(f"\n[RUN] {label}")
    print("[CMD]", " ".join(str(x) for x in command))

    try:
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        die(
            f"{label} timeout ke baad band hua. "
            f"Timeout: {timeout} seconds."
        )
    except OSError as exc:
        die(f"{label} execute nahi hua: {exc}")

    output = process.stdout or ""

    if output:
        print(output[-12000:])

    if process.returncode != 0:
        die(
            f"{label} fail hua. Exit code: {process.returncode}. "
            "Model compatibility, RAM, dataset aur llama.cpp version check karein."
        )

    return output


def check_binary(binary):
    output = run_command(
        [binary, "--version"],
        timeout=30,
        label="llama-perplexity version check",
    )

    return output.strip() or "Version output unavailable"


def model_size_gb(path):
    return round(path.stat().st_size / (1024 ** 3), 4)


def run_audit(binary, fp16, quant_path, dataset, timeout, threads):
    """
    Run one actual quantized-model versus FP16 comparison.

    Uses the modern llama.cpp CLI mode in which --kl-divergence-base
    accepts the reference GGUF model path.

    If the installed build does not support this mode, the benchmark
    fails and the script reports the failure rather than fabricating
    results.
    """
    command = [
        binary,
        "-m",
        str(quant_path),
        "--kl-divergence-base",
        str(fp16),
        "--kl-divergence",
        "-f",
        str(dataset),
    ]

    if threads is not None:
        command.extend(["-t", str(threads)])

    output = run_command(
        command,
        timeout=timeout,
        label=f"Compare {quant_path.name} against FP16 on {dataset.name}",
    )

    metrics = parse_metrics(output)
    ppl = parse_ppl(output)

    if "kld" not in metrics:
        die(
            "Benchmark command chala, lekin output se Mean KLD parse nahi hua. "
            "Apne llama.cpp build ke output format ko check karein. "
            "Raw benchmark output ko audit log me save karna recommended hai."
        )

    return {
        "metrics": metrics,
        "ppl": ppl,
        "raw_output": output,
    }


def mean_metric(records, metric_name):
    values = []

    for record in records:
        metric = record["metrics"].get(metric_name)

        if metric is not None:
            values.append(metric)

    if not values:
        return None

    count = len(values)

    mean_value = sum(x["value"] for x in values) / count

    # Independent uncertainties combined in quadrature.
    combined_error = math.sqrt(
        sum(x["error"] ** 2 for x in values)
    ) / count

    return {
        "value": mean_value,
        "error": combined_error,
        "datasets": count,
    }


def build_report(args, binary, binary_version, fp16, quants, datasets):
    report = {
        "schema_version": 1,
        "name": args.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).date().isoformat(),
        "method": "llama.cpp llama-perplexity KL-divergence",
        "reference": {
            "file": str(fp16),
            "size_gb": model_size_gb(fp16),
        },
        "llama_cpp": {
            "binary": binary,
            "version_output": binary_version,
        },
        "settings": {
            "timeout_seconds": args.timeout,
            "threads": args.threads,
        },
        "datasets": {},
        "variants_meta": {},
        "results": {},
        "warnings": [
            "KLD measures output-distribution difference, not Hindi task accuracy.",
            "RMS and Same top-p are included only when emitted by the installed llama.cpp build.",
            "Dataset results depend on the exact text, tokenization, model architecture and llama.cpp version.",
            "No quality improvement is guaranteed by quantization.",
        ],
    }

    for dataset_label, dataset_path in datasets:
        text = dataset_path.read_text(encoding="utf-8", errors="replace")

        line_count = sum(
            1 for line in text.splitlines() if line.strip()
        )

        if not text.strip():
            die(f"Dataset khaali hai: {dataset_path}")

        report["datasets"][dataset_label] = {
            "file": str(dataset_path),
            "nonempty_lines": line_count,
            "bytes": dataset_path.stat().st_size,
        }

    for quant_label, quant_path in quants:
        report["variants_meta"][quant_label] = {
            "file": str(quant_path),
            "size_gb": model_size_gb(quant_path),
        }

        report["results"][quant_label] = {}

        for dataset_label, dataset_path in datasets:
            outcome = run_audit(
                binary=binary,
                fp16=fp16,
                quant_path=quant_path,
                dataset=dataset_path,
                timeout=args.timeout,
                threads=args.threads,
            )

            report["results"][quant_label][dataset_label] = {
                "kld": outcome["metrics"].get("kld"),
                "rms": outcome["metrics"].get("rms"),
                "top": outcome["metrics"].get("top"),
                "ppl": outcome["ppl"],
            }

            if "rms" not in outcome["metrics"]:
                print(
                    "[WARN] RMS metric output me nahi mili. "
                    "Use unavailable rakha jayega."
                )

            if "top" not in outcome["metrics"]:
                print(
                    "[WARN] Same top-p metric output me nahi mili. "
                    "Use unavailable rakha jayega."
                )

    return report


def fmt_metric(metric, digits=6):
    if metric is None:
        return "Data unavailable"

    return (
        f"{metric['value']:.{digits}f} "
        f"± {metric['error']:.{digits}f}"
    )


def build_markdown(report):
    lines = [
        f"# Hindi Quant Audit: {report['name']}",
        "",
        f"- Date: {report['date']}",
        f"- FP16 reference: `{report['reference']['file']}`",
        f"- FP16 size: {report['reference']['size_gb']} GB",
        f"- llama.cpp binary: `{report['llama_cpp']['binary']}`",
        "",
        "## Results",
        "",
        "| Quant | Dataset | Size (GB) | Mean KLD ± error | RMS Δp ± error | Same top-p ± error | PPL |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    for quant_label, datasets in report["results"].items():
        size = report["variants_meta"][quant_label]["size_gb"]

        for dataset_label, result in datasets.items():
            ppl = result.get("ppl")

            ppl_text = (
                f"{ppl['value']:.6f}"
                if ppl is not None
                else "Data unavailable"
            )

            lines.append(
                "| "
                + " | ".join(
                    [
                        quant_label,
                        dataset_label,
                        str(size),
                        fmt_metric(result.get("kld")),
                        fmt_metric(result.get("rms"), 4),
                        fmt_metric(result.get("top"), 4),
                        ppl_text,
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## How to read the results",
            "",
            "- Mean KLD: smaller values indicate greater similarity to the FP16 reference distribution.",
            "- RMS Δp: root-mean-square change in token probabilities, when supported by the tool.",
            "- Same top-p: token-probability overlap metric emitted by compatible llama.cpp builds.",
            "- PPL: perplexity estimate, when the output provides it.",
            "",
            "## Limitations",
            "",
        ]
    )

    lines.extend(f"- {warning}" for warning in report["warnings"])

    lines.append("")

    return "\n".join(lines)


def build_html(report):
    escape = html.escape

    rows = []

    for quant_label, datasets in report["results"].items():
        size = report["variants_meta"][quant_label]["size_gb"]

        for dataset_label, result in datasets.items():
            ppl = result.get("ppl")

            ppl_text = (
                f"{ppl['value']:.6f}"
                if ppl is not None
                else "Data unavailable"
            )

            values = [
                quant_label,
                dataset_label,
                str(size),
                fmt_metric(result.get("kld)),
                fmt_metric(result.get("rms"), 4),
                fmt_metric(result.get("top"), 4),
                ppl_text,
            ]

            rows.append(
                "<tr>"
                + "".join(f"<td>{escape(v)}</td>" for v in values)
                + "</tr>"
            )

    warning_html = "".join(
        f"<li>{escape(warning)}</li>"
        for warning in report["warnings"]
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hindi Quant Audit - {escape(report['name'])}</title>
<style>
body {{
    font-family: system-ui, sans-serif;
    margin: 24px;
    background: #0b0f17;
    color: #e5e7eb;
}}
h1, h2 {{ color: #34d399; }}
table {{
    border-collapse: collapse;
    width: 100%;
    background: #121824;
}}
th, td {{
    padding: 10px;
    border: 1px solid #374151;
    text-align: left;
    overflow-wrap: anywhere;
}}
th {{ background: #1f2937; color: #34d399; }}
.notice {{
    background: #1f2937;
    padding: 14px;
    border-radius: 8px;
}}
</style>
</head>
<body>
<h1>Hindi Quant Audit: {escape(report['name'])}</h1>
<div class="notice">
<p>Date: {escape(report['date'])}</p>
<p>FP16 reference: {escape(report['reference']['file'])}</p>
<p>FP16 size: {report['reference']['size_gb']} GB</p>
<p>llama.cpp: {escape(report['llama_cpp']['binary'])}</p>
</div>
<h2>Results</h2>
<table>
<thead>
<tr>
<th>Quant</th>
<th>Dataset</th>
<th>Size GB</th>
<th>Mean KLD ± error</th>
<th>RMS Δp ± error</th>
<th>Same top-p ± error</th>
<th>PPL</th>
</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
<h2>Limitations</h2>
<ul>{warning_html}</ul>
</body>
</html>
"""


def write_reports(report, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    html_path = output_dir / "report.html"

    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    markdown_path.write_text(
        build_markdown(report),
        encoding="utf-8",
    )

    html_path.write_text(
        build_html(report),
        encoding="utf-8",
    )

    print("\nSUCCESS: Real benchmark report files created.")
    print(f"JSON:     {json_path}")
    print(f"Markdown: {markdown_path}")
    print(f"HTML:     {html_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Sthamly Hindi Quant Audit - FP16 vs quantized GGUF"
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Model/report name",
    )

    parser.add_argument(
        "--fp16",
        required=True,
        help="Path to FP16 reference GGUF",
    )

    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Dataset as label=path. Can be repeated.",
    )

    parser.add_argument(
        "--quant",
        action="append",
        default=[],
        help="Quantized model as label=path. Can be repeated.",
    )

    parser.add_argument(
        "--perplexity-bin",
        default=None,
        help="Path to llama-perplexity executable",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Timeout for each benchmark, in seconds",
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=None,
        help="CPU threads passed to llama-perplexity",
    )

    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional report output directory",
    )

    args = parser.parse_args()

    if args.timeout <= 0:
        die("--timeout zero se bada hona chahiye.")

    if args.threads is not None and args.threads <= 0:
        die("--threads positive integer hona chahiye.")

    if not args.quant:
        die("Kam se kam ek --quant label=path dena zaroori hai.")

    if not args.dataset:
        die(
            "Kam se kam ek --dataset label=path dena zaroori hai. "
            "Hard-coded test sets use nahi hote."
        )

    fp16 = require_file(args.fp16, "FP16 reference")
    quants = parse_label_paths(args.quant, "--quant")
    datasets = parse_label_paths(args.dataset, "--dataset")

    binary = find_perplexity_binary(args.perplexity_bin)
    binary_version = check_binary(binary)

    print("\n[INFO] Starting real model audit.")
    print(f"[INFO] FP16: {fp16}")
    print(f"[INFO] Quantized models: {len(quants)}")
    print(f"[INFO] Datasets: {len(datasets)}")

    report = build_report(
        args=args,
        binary=binary,
        binary_version=binary_version,
        fp16=fp16,
        quants=quants,
        datasets=datasets,
    )

    output_dir = (
        resolve_path(args.output_dir)
        if args.output_dir
        else ROOT / "reports" / slugify(args.name)
    )

    write_reports(report, output_dir)


if __name__ == "__main__":
    main()