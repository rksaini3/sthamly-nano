#!/usr/bin/env python3
"""
Sthamly NanoBrain - Hindi Quantization Audit

Compare quantized GGUF models against an FP16 reference using
llama.cpp's llama-perplexity tool (KL-divergence mode).

llama.cpp ka KLD workflow DO step ka hai:
    1. FP16 model ko dataset par chalao aur uske logits ek "base" file me save karo
       (--kl-divergence-base <base.kld>).   <-- base file GGUF nahi, logits file hai
    2. Har quantized model ko usi dataset par chalao aur us base file se compare karo
       (--kl-divergence-base <base.kld> --kl-divergence).
Yeh script dono steps khud karti hai (dataset ke hisaab se base ek baar banta hai).

Outputs:
    reports/<name>/report.json | report.md | report.html
    reports/<name>/logs/*.log      (har benchmark ka raw output)

Example:
    python scripts/audit.py \
        --name Qwen2.5-1.5B-Instruct \
        --fp16 models/gguf/model-f16.gguf \
        --dataset hindi=data/hindi-test.txt \
        --quant q4=models/gguf/model-Q4_K_M.gguf \
        --ngl 99 --chunks 100

Exit codes:  0 = sab theek | 2 = report bani par kuch benchmarks fail | 1 = kuch bhi nahi chala

Important:
    Metrics real benchmark output se parse hote hain. Script koi number invent nahi karti.
    NOTE: base file ka size ~ (vocab size x tokens x 2 bytes) hota hai -- bade vocab
    (jaise 150k) par yeh GB me jata hai. --chunks se dataset chhota rakho.
"""

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading

from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")

MIN_DATASET_BYTES = 20_000       # isse chhota dataset statistically kamzor hota hai
OVERLAP_WARN = 0.05              # calibration se >5% lines same -> warning


class AuditError(Exception):
    """Ek benchmark/step fail hua -- baaki audit chalta rahega."""


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


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)

    return digest.hexdigest()


def parse_label_paths(items, option_name):
    """Parse repeated arguments in label=path format."""
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


# --------------------------------------------------------------------- binary
def find_perplexity_binary(user_value):
    """
    Explicit --perplexity-bin sabse upar (galat ho to seedha error -- chup-chaap
    doosra binary nahi chunte). Phir PATH, phir common llama.cpp build locations.
    """
    def usable(candidate):
        candidate_path = Path(candidate).expanduser()

        if not candidate_path.is_file():
            return None

        if os.name != "nt" and not os.access(candidate_path, os.X_OK):
            return None

        return str(candidate_path.resolve())

    if user_value:
        found = usable(user_value)

        if found is None:
            die(f"--perplexity-bin chalne layak file nahi hai: {user_value}")

        return found

    candidates = []
    path_binary = shutil.which("llama-perplexity")

    if path_binary:
        candidates.append(path_binary)

    for base in (ROOT / "llama.cpp" / "build" / "bin", ROOT / "build" / "bin"):
        for name in ("llama-perplexity", "perplexity"):
            candidates.append(str(base / name))

            if os.name == "nt":
                candidates.append(str(base / f"{name}.exe"))
                candidates.append(str(base / "Release" / f"{name}.exe"))

    for candidate in candidates:
        found = usable(candidate)

        if found:
            return found

    die(
        "llama-perplexity executable nahi mili. "
        "--perplexity-bin se executable ka sahi path dein."
    )


# --------------------------------------------------------------------- parsing
NUM = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
# "±" ya ASCII "+/-"; Windows/encoding gadbad ho to U+FFFD ya mojibake bhi chalega.
PM = r"\s*(?:±|\+/-|\+\s*/\s*-|\ufffd|Â±)\s*"
PAIR = NUM + PM + NUM

METRIC_PATTERNS = {
    "kld": r"Mean\s+KLD\s*[:=]\s*" + PAIR,
    # "RMS Δp" ka Δ encoding me bigad sakta hai -> label ke beech 12 char tak tolerate
    "rms": r"RMS\s+[^:=\n]{0,12}[:=]\s*" + PAIR + r"\s*%?",
    "top": r"Same\s+top\s*p\s*[:=]\s*" + PAIR + r"\s*%?",
}

PPL_Q_PATTERN = r"Mean\s+PPL\(Q\)\s*[:=]\s*" + PAIR
PPL_BASE_PATTERN = r"Mean\s+PPL\(base\)\s*[:=]\s*" + PAIR
PPL_PLAIN_PATTERN = (
    r"Final estimate:\s*PPL\s*=\s*" + NUM + r"(?:" + PM + NUM + r")?"
)


def parse_pair(output, pattern, name):
    """Output me aakhri match (summary) uthao; non-finite/negative error par ValueError."""
    matches = list(re.finditer(pattern, output, flags=re.IGNORECASE))

    if not matches:
        return None

    match = matches[-1]
    value = float(match.group(1))
    error = float(match.group(2)) if match.group(2) is not None else None

    if not math.isfinite(value) or (error is not None and not math.isfinite(error)):
        raise ValueError(f"{name} me non-finite number mila.")

    if error is not None and error < 0:
        raise ValueError(f"{name} uncertainty negative hai.")

    return {"value": value, "error": error}


def parse_metrics(output):
    result = {}

    for name, pattern in METRIC_PATTERNS.items():
        metric = parse_pair(output, pattern, name)

        if metric is not None:
            result[name] = metric

    return result


def parse_ppl(output):
    """KL mode me 'Mean PPL(Q)' aata hai; plain mode me 'Final estimate'."""
    quant = parse_pair(output, PPL_Q_PATTERN, "PPL(Q)")

    if quant is None:
        quant = parse_pair(output, PPL_PLAIN_PATTERN, "PPL")

    base = parse_pair(output, PPL_BASE_PATTERN, "PPL(base)")

    return quant, base


# ------------------------------------------------------------------ run + logs
def run_command(command, timeout, label, log_path=None):
    """
    Benchmark chalao: output LIVE dikhao, poora output log file me save karo,
    timeout par process kill karo. Shell use nahi hota.
    """
    print(f"\n[RUN] {label}")
    print("[CMD]", " ".join(str(x) for x in command), flush=True)

    try:
        process = subprocess.Popen(
            [str(x) for x in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise AuditError(f"{label} execute nahi hua: {exc}")

    timed_out = threading.Event()

    def kill():
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    lines = []

    try:
        for line in process.stdout:
            lines.append(line)
            print(line, end="", flush=True)
    finally:
        timer.cancel()
        process.stdout.close()
        process.wait()

    output = "".join(lines)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# {label}\n# CMD: {' '.join(str(x) for x in command)}\n\n{output}",
            encoding="utf-8",
        )

    if timed_out.is_set():
        raise AuditError(f"{label} timeout ({timeout}s) ke baad band kiya gaya.")

    if process.returncode != 0:
        raise AuditError(
            f"{label} fail hua. Exit code: {process.returncode}. "
            "Model compatibility, RAM/VRAM, dataset aur llama.cpp version check karein."
        )

    return output


def check_binary(binary):
    """Version sirf record ke liye hai -- fail hone par audit nahi rukta."""
    try:
        output = run_command(
            [binary, "--version"],
            timeout=30,
            label="llama-perplexity version check",
        )
    except AuditError as exc:
        print(f"[WARN] Version check fail ({exc}); audit aage chalega.")
        return "Version output unavailable"

    return output.strip() or "Version output unavailable"


def model_size_gb(path):
    return round(path.stat().st_size / (1024 ** 3), 4)


def free_gb(path):
    return shutil.disk_usage(path).free / 1e9


# -------------------------------------------------------------------- baseline
def perplexity_command(binary, model, dataset, opts):
    command = [binary, "-m", str(model), "-f", str(dataset), "-c", str(opts.ctx_size)]

    if opts.chunks is not None:
        command += ["--chunks", str(opts.chunks)]

    if opts.threads is not None:
        command += ["-t", str(opts.threads)]

    if opts.ngl is not None:
        command += ["-ngl", str(opts.ngl)]

    return command


def ensure_baseline(binary, fp16, label, dataset, opts, base_dir, log_dir):
    """
    FP16 ke logits ki base file banao (ya pichli same-settings wali reuse karo).
    Base file ko hamesha .part me likhte hain taaki adhoori file reuse na ho.
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    base_path = base_dir / f"{label}.kld"
    meta_path = base_dir / f"{label}.kld.json"

    stat = fp16.stat()
    meta = {
        "fp16_size": stat.st_size,
        "fp16_mtime_ns": stat.st_mtime_ns,
        "dataset_sha256": sha256_file(dataset),
        "ctx_size": opts.ctx_size,
        "chunks": opts.chunks,
    }

    if opts.keep_base and base_path.is_file() and meta_path.is_file():
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")) == meta:
                print(f"[INFO] Purani FP16 base file reuse ho rahi hai: {base_path.name}")
                return base_path
        except ValueError:
            pass

    free = free_gb(base_dir)

    if free < opts.min_free_gb:
        raise AuditError(
            f"Disk kam hai: {free:.1f} GB free, kam se kam {opts.min_free_gb:g} GB chahiye "
            "(base logits file badi hoti hai). --chunks kam karein ya disk badhayein."
        )

    base_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)

    part = base_path.with_name(base_path.name + ".part")
    part.unlink(missing_ok=True)

    command = perplexity_command(binary, fp16, dataset, opts) + [
        "--kl-divergence-base",
        str(part),
    ]

    try:
        run_command(
            command,
            timeout=opts.timeout,
            label=f"FP16 base logits on {dataset.name}",
            log_path=log_dir / f"base__{label}.log",
        )

        if not part.is_file() or part.stat().st_size == 0:
            raise AuditError(
                "FP16 run chala par base logits file nahi bani. "
                "Apna llama.cpp build --kl-divergence-base support karta hai?"
            )

        os.replace(part, base_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[INFO] Base file size: {base_path.stat().st_size / 1e9:.2f} GB")

    return base_path


def run_audit(binary, quant_label, quant_path, dataset_label, dataset, base_path, opts, log_dir):
    """Ek quantized model vs FP16 base file -- real KLD run."""
    command = perplexity_command(binary, quant_path, dataset, opts) + [
        "--kl-divergence-base",
        str(base_path),
        "--kl-divergence",
    ]

    log_path = log_dir / f"{quant_label}__{dataset_label}.log"

    output = run_command(
        command,
        timeout=opts.timeout,
        label=f"Compare {quant_path.name} against FP16 on {dataset.name}",
        log_path=log_path,
    )

    try:
        metrics = parse_metrics(output)
        ppl, ppl_base = parse_ppl(output)
    except ValueError as exc:
        raise AuditError(f"Output parse fail: {exc} (log: {log_path.name})")

    if "kld" not in metrics:
        raise AuditError(
            "Benchmark chala, lekin output se Mean KLD parse nahi hua. "
            f"Raw output log me hai: {log_path.name}"
        )

    for key, title in (("rms", "RMS"), ("top", "Same top-p")):
        if key not in metrics:
            print(f"[WARN] {title} metric output me nahi mili. Use unavailable rakha jayega.")

    return {
        "kld": metrics.get("kld"),
        "rms": metrics.get("rms"),
        "top": metrics.get("top"),
        "ppl": ppl,
        "ppl_base": ppl_base,
        "log": f"logs/{log_path.name}",
    }


# ---------------------------------------------------------------- data validity
def nonempty_lines(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    return {line.strip() for line in text.splitlines() if line.strip()}


def describe_dataset(label, path, calibration):
    """Dataset ki jaankari + validity warnings (khaali, chhota, calibration se overlap)."""
    text = path.read_text(encoding="utf-8", errors="replace")

    if not text.strip():
        die(f"Dataset khaali hai: {path}")

    info = {
        "file": str(path),
        "nonempty_lines": sum(1 for line in text.splitlines() if line.strip()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    warnings = []

    if info["bytes"] < MIN_DATASET_BYTES:
        warnings.append(
            f"Dataset '{label}' chhota hai ({info['bytes']} bytes < {MIN_DATASET_BYTES}); "
            "KLD/PPL estimates kamzor ho sakte hain."
        )

    if calibration is not None:
        if info["sha256"] == sha256_file(calibration):
            warnings.append(
                f"Dataset '{label}' calibration (imatrix) file ke BILKUL same hai -- "
                "result 'before vs after' ke liye valid nahi."
            )
            info["calibration_overlap"] = 1.0
        else:
            lines = nonempty_lines(path)
            overlap = len(lines & nonempty_lines(calibration)) / len(lines) if lines else 0.0
            info["calibration_overlap"] = round(overlap, 4)

            if overlap > OVERLAP_WARN:
                warnings.append(
                    f"Dataset '{label}' ki {overlap:.0%} lines calibration file se milti hain; "
                    "imatrix quant ko unfair fayda mil sakta hai. Held-out dataset use karein."
                )

    return info, warnings


# ---------------------------------------------------------------------- report
def build_report(args, binary, binary_version, fp16, quants, datasets, calibration, output_dir):
    now = datetime.now(timezone.utc)
    log_dir = output_dir / "logs"
    base_dir = output_dir / "base"

    report = {
        "schema_version": 2,
        "name": args.name,
        "created_at": now.isoformat(),
        "date": now.date().isoformat(),
        "method": "llama.cpp llama-perplexity KL-divergence (FP16 base logits vs quant)",
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
            "ctx_size": args.ctx_size,
            "chunks": args.chunks,
            "ngl": args.ngl,
            "calibration_checked": calibration is not None,
        },
        "datasets": {},
        "variants_meta": {},
        "results": {},
        "errors": [],
        "warnings": [
            "KLD measures output-distribution difference, not Hindi task accuracy.",
            "RMS and Same top-p are included only when emitted by the installed llama.cpp build.",
            "Dataset results depend on the exact text, tokenization, model architecture and llama.cpp version.",
            "No quality improvement is guaranteed by quantization.",
        ],
    }

    for dataset_label, dataset_path in datasets:
        info, warns = describe_dataset(dataset_label, dataset_path, calibration)
        report["datasets"][dataset_label] = info
        report["warnings"].extend(warns)

        for text in warns:
            print(f"[WARN] {text}")

    for quant_label, quant_path in quants:
        size = model_size_gb(quant_path)
        report["variants_meta"][quant_label] = {"file": str(quant_path), "size_gb": size}
        report["results"][quant_label] = {}

        if quant_path.resolve() == fp16.resolve():
            die(f"--quant ({quant_label}) aur --fp16 ek hi file hain.")

        if size >= report["reference"]["size_gb"]:
            text = (
                f"Quant '{quant_label}' ({size} GB) FP16 ({report['reference']['size_gb']} GB) "
                "se chhota nahi hai -- files ka order/jodi check karein."
            )
            report["warnings"].append(text)
            print(f"[WARN] {text}")

    # Dataset outer loop: har dataset ka FP16 base ek baar banta hai, sab quants usse compare hote hain.
    for dataset_label, dataset_path in datasets:
        base_path = None

        try:
            base_path = ensure_baseline(
                binary, fp16, dataset_label, dataset_path, args, base_dir, log_dir
            )
        except AuditError as exc:
            message = f"[{dataset_label}] FP16 base: {exc}"
            print(f"ERROR: {message}", file=sys.stderr)
            report["errors"].append(message)

        for quant_label, quant_path in quants:
            if base_path is None:
                report["results"][quant_label][dataset_label] = {"error": "FP16 base nahi bani"}
                continue

            try:
                report["results"][quant_label][dataset_label] = run_audit(
                    binary, quant_label, quant_path, dataset_label,
                    dataset_path, base_path, args, log_dir,
                )
            except AuditError as exc:
                message = f"[{quant_label} x {dataset_label}] {exc}"
                print(f"ERROR: {message}", file=sys.stderr)
                report["errors"].append(message)
                report["results"][quant_label][dataset_label] = {"error": str(exc)}

        if base_path is not None and not args.keep_base:
            base_path.unlink(missing_ok=True)
            base_path.with_name(base_path.name + ".json").unlink(missing_ok=True)

    return report


def fmt_metric(metric, digits=6, unit=""):
    if metric is None:
        return "Data unavailable"

    text = f"{metric['value']:.{digits}f}"

    if metric.get("error") is not None:
        text += f" ± {metric['error']:.{digits}f}"

    return text + unit


def fmt_ppl(result):
    ppl = result.get("ppl")

    if ppl is None:
        return "Data unavailable"

    text = f"{ppl['value']:.4f}"
    base = result.get("ppl_base")

    if base is not None:
        text += f" (FP16 {base['value']:.4f})"

    return text


def table_rows(report):
    """Dono formats ke liye ek jaisi rows: (cells, is_error)."""
    rows = []

    for quant_label, datasets in report["results"].items():
        size = str(report["variants_meta"][quant_label]["size_gb"])

        for dataset_label, result in datasets.items():
            if "error" in result:
                rows.append(
                    ([quant_label, dataset_label, size, f"FAILED: {result['error']}", "-", "-", "-"], True)
                )
                continue

            rows.append(
                (
                    [
                        quant_label,
                        dataset_label,
                        size,
                        fmt_metric(result.get("kld")),
                        fmt_metric(result.get("rms"), 4, " %"),
                        fmt_metric(result.get("top"), 4, " %"),
                        fmt_ppl(result),
                    ],
                    False,
                )
            )

    return rows


HEADERS = [
    "Quant",
    "Dataset",
    "Size (GB)",
    "Mean KLD ± error",
    "RMS Δp (%) ± error",
    "Same top-p (%) ± error",
    "PPL quant (FP16)",
]


def md_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


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
        "| " + " | ".join(HEADERS) + " |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]

    for cells, _ in table_rows(report):
        lines.append("| " + " | ".join(md_cell(c) for c in cells) + " |")

    if report["errors"]:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {md_cell(e)}" for e in report["errors"])

    lines.extend(
        [
            "",
            "## How to read the results",
            "",
            "- Mean KLD: smaller values indicate greater similarity to the FP16 reference distribution.",
            "- RMS Δp: root-mean-square change in token probabilities (percent), when supported by the tool.",
            "- Same top-p: how often the top token matches FP16 (percent), when supported by the tool.",
            "- PPL: perplexity of the quantized model; FP16 value in brackets when available.",
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

    for cells, is_error in table_rows(report):
        css = ' class="err"' if is_error else ""
        rows.append(
            f"<tr{css}>" + "".join(f"<td>{escape(str(v))}</td>" for v in cells) + "</tr>"
        )

    header_html = "".join(f"<th>{escape(h)}</th>" for h in HEADERS)

    warning_html = "".join(f"<li>{escape(w)}</li>" for w in report["warnings"])

    error_html = ""

    if report["errors"]:
        error_html = (
            "<h2>Errors</h2><ul>"
            + "".join(f"<li>{escape(e)}</li>" for e in report["errors"])
            + "</ul>"
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
tr.err td {{ color: #fca5a5; }}
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
<tr>{header_html}</tr>
</thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
{error_html}
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
    markdown_path.write_text(build_markdown(report), encoding="utf-8")
    html_path.write_text(build_html(report), encoding="utf-8")

    print(f"\nJSON:     {json_path}")
    print(f"Markdown: {markdown_path}")
    print(f"HTML:     {html_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sthamly Hindi Quant Audit - FP16 vs quantized GGUF"
    )

    parser.add_argument("--name", required=True, help="Model/report name")
    parser.add_argument("--fp16", required=True, help="Path to FP16 reference GGUF")
    parser.add_argument(
        "--dataset", action="append", default=[],
        help="Dataset as label=path. Can be repeated.",
    )
    parser.add_argument(
        "--quant", action="append", default=[],
        help="Quantized model as label=path. Can be repeated.",
    )
    parser.add_argument(
        "--calibration", default=None,
        help="imatrix calibration text file; dataset se overlap ho to warning aati hai",
    )
    parser.add_argument("--perplexity-bin", default=None, help="Path to llama-perplexity executable")
    parser.add_argument("--timeout", type=int, default=7200, help="Timeout per benchmark, in seconds")
    parser.add_argument("--threads", type=int, default=None, help="CPU threads (-t)")
    parser.add_argument("--ngl", type=int, default=None, help="GPU layers (-ngl); GPU par 99 do")
    parser.add_argument("--ctx-size", type=int, default=512, help="Context size (-c), default 512")
    parser.add_argument("--chunks", type=int, default=None, help="Sirf pehle N chunks (--chunks); base file chhoti rahegi")
    parser.add_argument("--min-free-gb", type=float, default=10.0, help="Base file banane se pehle itna free disk chahiye")
    parser.add_argument(
        "--keep-base", action="store_true",
        help="FP16 base logits file rakho/reuse karo (default: audit ke baad delete)",
    )
    parser.add_argument("--output-dir", default=None, help="Optional report output directory")

    args = parser.parse_args(argv)

    for option, value in (("--timeout", args.timeout), ("--ctx-size", args.ctx_size)):
        if value <= 0:
            die(f"{option} zero se bada hona chahiye.")

    if args.threads is not None and args.threads <= 0:
        die("--threads positive integer hona chahiye.")

    if args.chunks is not None and args.chunks <= 0:
        die("--chunks positive integer hona chahiye.")

    if args.ngl is not None and args.ngl < 0:
        die("--ngl negative nahi ho sakta.")

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
    calibration = require_file(args.calibration, "--calibration") if args.calibration else None

    binary = find_perplexity_binary(args.perplexity_bin)
    binary_version = check_binary(binary)

    output_dir = (
        resolve_path(args.output_dir)
        if args.output_dir
        else ROOT / "reports" / slugify(args.name)
    )

    print("\n[INFO] Starting real model audit.")
    print(f"[INFO] FP16: {fp16}")
    print(f"[INFO] Quantized models: {len(quants)}")
    print(f"[INFO] Datasets: {len(datasets)}")

    report = build_report(
        args, binary, binary_version, fp16, quants, datasets, calibration, output_dir
    )

    write_reports(report, output_dir)

    total = len(quants) * len(datasets)
    failed = sum(
        1
        for per_dataset in report["results"].values()
        for result in per_dataset.values()
        if "error" in result
    )

    if failed == 0:
        print("\nSUCCESS: Real benchmark report files created.")
        return 0

    if failed < total:
        print(f"\nPARTIAL: {failed}/{total} benchmarks fail hue; report me unka error likha hai.")
        return 2

    print("\nFAILED: koi bhi benchmark nahi chala. Logs dekhein.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
