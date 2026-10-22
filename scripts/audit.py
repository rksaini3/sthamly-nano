#!/usr/bin/env python3
"""
Sthamly NanoBrain - Real Model Audit Engine (v2)

Compares quantized GGUF models against an FP16 reference using llama.cpp's
llama-perplexity KL-divergence mode, and writes reports/<slug>/report.json.

Example (worker.py calls this exact form):
    python scripts/audit.py \
        --name Qwen/Qwen2.5-1.5B-Instruct \
        --fp16 models/gguf/Qwen__Qwen2.5-1.5B-Instruct-f16.gguf \
        --dataset hindi=data/hindi_test.txt \
        --quant q4_standard=models/gguf/...-Q4_K_M-standard.gguf \
        --quant q4_hindi=models/gguf/...-Q4_K_M-hindi.gguf \
        --calibration data/hindi_calib.txt \
        --out reports/qwen__qwen2.5-1.5b-instruct/report.json

Important:
    - No synthetic metrics. Every number is parsed from llama-perplexity output.
    - KLD is a proxy for distribution drift, not proof of task accuracy.
    - Verdicts are computed and written into the report, so the output is
      self-explanatory.

Exit codes:
    0  complete report, all requested evaluations succeeded
    1  audit failed (no report written)
    2  partial report written (--allow-partial and at least one evaluation failed)
"""

from __future__ import annotations

import argparse
import hashlib
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
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

MIN_DATASET_BYTES = 20_000
OVERLAP_WARN = 0.05
MIN_CHUNKS_WARN = 50
MAX_CALIBRATION_LINES_FOR_SET = 2_000_000  # memory guard for overlap check

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_PARTIAL = 2


class AuditError(RuntimeError):
    """A real audit step could not be completed."""


# ============================================================
# PATHS, NAMES, FILES
# ============================================================

def report_directory_name(model_name: str) -> str:
    """
    Single naming rule shared with worker.py:
    '/' and unsafe chars -> '_', then lowercase. Must match worker.py exactly.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", model_name.strip()).strip("._-")
    if not cleaned:
        raise AuditError("Invalid model name.")
    return cleaned[:120].lower()


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return cleaned.lower() or "item"


def require_file(path: Path, description: str) -> Path:
    if not path.exists():
        raise AuditError(f"{description} does not exist: {path}")
    if not path.is_file():
        raise AuditError(f"{description} is not a file: {path}")
    if path.stat().st_size <= 0:
        raise AuditError(f"{description} is empty: {path}")
    return path.resolve()


def relative_to_root(path: Path) -> str:
    """Store relative paths in the report so it works on another machine."""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return path.name


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ============================================================
# METRIC PARSING (real llama-perplexity output only)
# ============================================================

NUM = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
PM = r"\s*(?:±|\+/-|\+\s*/\s*-|\ufffd|Â±)\s*"
PAIR = NUM + PM + NUM

PATTERNS = {
    "kld": r"Mean\s+KLD\s*[:=]\s*" + PAIR,
    "rms": r"RMS\s+[^:=\n]{0,12}[:=]\s*" + PAIR,
    "top": r"Same\s+top\s*p\s*[:=]\s*" + PAIR,
    "ppl": r"Mean\s+PPL\(Q\)\s*[:=]\s*" + PAIR,
    "ppl_base": r"Mean\s+PPL\(base\)\s*[:=]\s*" + PAIR,
}


def parse_metrics(text: str) -> dict[str, list[float]]:
    """
    Returns {"kld": [mean, error], ...}. Metrics not in the output are absent.
    The LAST match is used because llama-perplexity prints the summary at the end.
    """
    result: dict[str, list[float]] = {}
    for metric, pattern in PATTERNS.items():
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if not matches:
            continue
        try:
            mean = float(matches[-1].group(1))
            error = float(matches[-1].group(2))
        except (TypeError, ValueError):
            continue
        if math.isfinite(mean) and math.isfinite(error) and error >= 0:
            result[metric] = [mean, error]
    return result


# ============================================================
# COMPARISON AND VERDICT
# ============================================================

def compare(a: list[float], b: list[float], key: str) -> tuple[float, float]:
    """
    Returns (gain, combined_error). Positive gain means model A is BETTER.
    KLD and RMS: lower is better. Same-top-p: higher is better.
    Errors are combined as if independent (conservative for correlated chunks).
    """
    a_mean, a_err = a
    b_mean, b_err = b
    if not all(math.isfinite(x) for x in (a_mean, a_err, b_mean, b_err)):
        raise AuditError("Non-finite measurement cannot be compared.")
    gain = (b_mean - a_mean) if key in ("kld", "rms") else (a_mean - b_mean)
    return gain, math.sqrt(a_err ** 2 + b_err ** 2)


def verdict(gain: float, combined_error: float) -> str:
    if gain > 2 * combined_error:
        return "BETTER (gain > 2x combined error)"
    if gain > 0:
        return "BETTER BUT WITHIN ERROR"
    return "NO ADVANTAGE"


# ============================================================
# COMMAND DISCOVERY
# ============================================================

def find_perplexity_binary() -> str:
    def usable(candidate: str) -> str | None:
        path = Path(candidate).expanduser()
        if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK)):
            return str(path.resolve())
        return None

    configured = os.getenv("LLAMA_PERPLEXITY", "").strip()
    if configured:
        found = usable(configured) or shutil.which(configured)
        if found:
            return found
        raise AuditError(f"LLAMA_PERPLEXITY is not runnable: {configured}")

    for rel in (
        ("llama.cpp", "build", "bin", "llama-perplexity"),
        ("llama.cpp", "build", "bin", "llama-perplexity.exe"),
        ("llama.cpp", "build", "bin", "Release", "llama-perplexity.exe"),
    ):
        found = usable(str(ROOT.joinpath(*rel)))
        if found:
            return found

    for name in ("llama-perplexity", "llama-perplexity.exe"):
        found = shutil.which(name)
        if found:
            return found

    raise AuditError(
        "llama-perplexity not found. Build llama.cpp or set LLAMA_PERPLEXITY."
    )


# ============================================================
# NAME=PATH ARGUMENTS
# ============================================================

def parse_named_paths(values: list[str], argument_name: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise AuditError(f"--{argument_name} value '{value}' must be name=path.")
        name, raw_path = (part.strip() for part in value.split("=", 1))
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            raise AuditError(f"--{argument_name} name is invalid: {name!r}")
        if not raw_path:
            raise AuditError(f"--{argument_name} path is empty for {name!r}.")
        if name in parsed:
            raise AuditError(f"--{argument_name} duplicate name: {name}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        parsed[name] = path.resolve()
    return parsed


# ============================================================
# REAL EVALUATION RUNNER
# ============================================================

def run_command(command: list[str], timeout: int, log_path: Path, label: str) -> str:
    """
    Run llama-perplexity with a real timeout. Output streams live and is saved
    to log_path. The process is terminated (then killed) on timeout.
    """
    print("\n$ " + " ".join(str(part) for part in command), flush=True)

    try:
        process = subprocess.Popen(
            [str(part) for part in command],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise AuditError(f"Could not start evaluator: {exc}") from exc

    timed_out = threading.Event()

    def stop_process() -> None:
        timed_out.set()
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()

    timer = threading.Timer(timeout, stop_process)
    timer.start()
    lines: list[str] = []

    try:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            print(line, end="", flush=True)
        process.wait()
    except BaseException:
        process.kill()
        raise
    finally:
        timer.cancel()

    output = "".join(lines)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"# {label}\n# CMD: {' '.join(str(x) for x in command)}\n\n{output}",
        encoding="utf-8",
    )

    if timed_out.is_set():
        raise AuditError(f"Evaluation timed out after {timeout} seconds ({label}).")
    if process.returncode != 0:
        raise AuditError(
            f"llama-perplexity exit code {process.returncode} ({label}). No metric recorded."
        )
    return output


def ensure_fp16_base(
    *,
    binary: str,
    domain: str,
    dataset_path: Path,
    fp16_path: Path,
    common_args: list[str],
    timeout: int,
    base_dir: Path,
    log_dir: Path,
    fingerprint: dict[str, Any],
    min_free_gb: float,
) -> Path:
    """
    Build (or reuse) the FP16 reference logits file for one dataset.
    Reuse only when the stored fingerprint matches exactly.
    Written to .part first, renamed on success.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    stem = slugify(domain)
    base_path = base_dir / f"{stem}_fp16.kld"
    meta_path = base_dir / f"{stem}_fp16.kld.json"

    if base_path.is_file() and base_path.stat().st_size > 0 and meta_path.is_file():
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")) == fingerprint:
                print(f"[INFO] Reusing FP16 base for {domain}: {base_path.name}")
                return base_path
        except ValueError:
            pass

    free_gb = shutil.disk_usage(base_dir).free / 1e9
    if free_gb < min_free_gb:
        raise AuditError(
            f"Not enough disk for FP16 base: {free_gb:.1f} GB free, need {min_free_gb:g} GB."
        )

    base_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)
    part = base_path.with_name(base_path.name + ".part")
    part.unlink(missing_ok=True)

    command = [binary, "-m", str(fp16_path), "--kl-divergence-base", str(part), *common_args]
    try:
        run_command(command, timeout, log_dir / f"base__{stem}.log",
                    f"FP16 base logits on {dataset_path.name}")
        if not part.is_file() or part.stat().st_size == 0:
            raise AuditError(
                "FP16 base logits were not written. Does this llama.cpp build support "
                "--kl-divergence-base?"
            )
        os.replace(part, base_path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise

    meta_path.write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
    return base_path


def evaluate_domain(
    *,
    binary: str,
    domain: str,
    dataset_path: Path,
    fp16_path: Path,
    quant_models: dict[str, Path],
    chunks: int,
    threads: int,
    ngl: int | None,
    timeout: int,
    base_dir: Path,
    log_dir: Path,
    min_free_gb: float,
    failures: list[str] | None,
) -> list[dict[str, Any]]:
    """
    failures=None  -> first failure raises (strict mode).
    failures=list  -> record failure and continue with other variants (partial mode).
    """
    common_args = ["-f", str(dataset_path), "-t", str(threads), "--chunks", str(chunks)]
    if ngl is not None:
        common_args += ["-ngl", str(ngl)]

    fp16_stat = fp16_path.stat()
    # Size + mtime, not a full hash: hashing a multi-GB FP16 on every run is too slow.
    # Drive copy or re-download changes mtime, which only causes a rebuild, never a wrong reuse.
    fingerprint = {
        "fp16_size": fp16_stat.st_size,
        "fp16_mtime_ns": fp16_stat.st_mtime_ns,
        "dataset_sha256": sha256_file(dataset_path),
        "chunks": chunks,
        "threads": threads,
        "ngl": ngl,
    }

    base_path = ensure_fp16_base(
        binary=binary, domain=domain, dataset_path=dataset_path, fp16_path=fp16_path,
        common_args=common_args, timeout=timeout, base_dir=base_dir, log_dir=log_dir,
        fingerprint=fingerprint, min_free_gb=min_free_gb,
    )

    results: list[dict[str, Any]] = []

    for variant, quant_path in quant_models.items():
        label = f"{variant} vs FP16 on {dataset_path.name}"
        try:
            require_file(quant_path, f"Quantized model '{variant}'")
            output = run_command(
                [binary, "-m", str(quant_path), "--kl-divergence-base", str(base_path),
                 "--kl-divergence", *common_args],
                timeout,
                log_dir / f"{slugify(variant)}__{slugify(domain)}.log",
                label,
            )
            metrics = parse_metrics(output)
            if "kld" not in metrics:
                raise AuditError(f"Mean KLD not found in output for '{variant}' on '{domain}'.")
        except AuditError as exc:
            if failures is None:
                raise
            message = f"[{variant} x {domain}] {exc}"
            print(f"ERROR: {message}", file=sys.stderr)
            failures.append(message)
            continue

        item: dict[str, Any] = {
            "domain": domain,
            "variant": variant,
            "model_path": relative_to_root(quant_path),
            "kld": metrics["kld"][0],
            "kld_error": metrics["kld"][1],
            "metrics_missing": sorted(set(PATTERNS) - set(metrics)),
        }
        field_map = {
            "rms": ("rms", "rms_error"),
            "top": ("same_top_p", "same_top_p_error"),
            "ppl": ("ppl", "ppl_error"),
            "ppl_base": ("ppl_base", "ppl_base_error"),
        }
        for key, (field, field_err) in field_map.items():
            if key in metrics:
                item[field] = metrics[key][0]
                item[field_err] = metrics[key][1]
        results.append(item)

    return results


# ============================================================
# DATA VALIDITY CHECKS
# ============================================================

def line_set(path: Path) -> set[str] | None:
    """
    Set of non-empty lines for overlap checks. Returns None if the file is too large
    to hold in memory; the caller then reports overlap as unchecked.
    """
    lines: set[str] = set()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                lines.add(line)
                if len(lines) > MAX_CALIBRATION_LINES_FOR_SET:
                    return None
    return lines


def dataset_checks(
    datasets: dict[str, Path],
    calibration: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    info: dict[str, Any] = {}
    warnings: list[str] = []

    calib_sha = sha256_file(calibration) if calibration else None
    calib_lines = line_set(calibration) if calibration else None
    if calibration and calib_lines is None:
        warnings.append("Calibration file too large for overlap check; overlap is UNCHECKED.")

    for name, path in datasets.items():
        size = path.stat().st_size
        entry: dict[str, Any] = {
            "file": relative_to_root(path),
            "bytes": size,
            "sha256": sha256_file(path),
        }

        if size < MIN_DATASET_BYTES:
            warnings.append(
                f"Dataset '{name}' is small ({size} bytes < {MIN_DATASET_BYTES}); estimates are weak."
            )

        if calibration is not None:
            if entry["sha256"] == calib_sha:
                entry["calibration_overlap"] = 1.0
                warnings.append(
                    f"Dataset '{name}' is identical to the calibration file; "
                    "before/after comparison is not a fair test."
                )
            elif calib_lines is not None:
                lines = line_set(path) or set()
                overlap = len(lines & calib_lines) / len(lines) if lines else 0.0
                entry["calibration_overlap"] = round(overlap, 4)
                if overlap > OVERLAP_WARN:
                    warnings.append(
                        f"Dataset '{name}': {overlap:.0%} of lines also appear in calibration; "
                        "use a held-out dataset."
                    )
            else:
                entry["calibration_overlap"] = None

        info[name] = entry

    return info, warnings


# ============================================================
# REPORT
# ============================================================

def build_comparisons(results: list[dict[str, Any]], variants: list[str]) -> list[dict[str, Any]]:
    """
    Pairwise comparison of every variant against the first variant named on the
    command line (the baseline). Computed here, so the report contains verdicts.
    """
    comparisons: list[dict[str, Any]] = []
    if len(variants) < 2:
        return comparisons

    by_key = {(r["domain"], r["variant"]): r for r in results}
    domains = sorted({r["domain"] for r in results})
    baseline = variants[0]

    for domain in domains:
        base = by_key.get((domain, baseline))
        if base is None:
            continue
        for other in variants[1:]:
            cand = by_key.get((domain, other))
            if cand is None:
                continue
            for key in ("kld", "rms", "same_top_p"):
                metric_key = {"kld": "kld", "rms": "rms", "same_top_p": "same_top_p"}[key]
                if metric_key not in base or metric_key not in cand:
                    continue
                mkey = {"kld": "kld", "rms": "rms", "same_top_p": "top"}[key]
                gain, err = compare(
                    [base[metric_key], base[f"{metric_key}_error"] if metric_key != "same_top_p"
                     else base["same_top_p_error"]],
                    [cand[metric_key], cand[f"{metric_key}_error"] if metric_key != "same_top_p"
                     else cand["same_top_p_error"]],
                    mkey,
                )
                comparisons.append({
                    "domain": domain,
                    "baseline": baseline,
                    "candidate": other,
                    "metric": key,
                    "gain_for_candidate": round(-gain if mkey in ("kld", "rms") else gain, 6),
                    "combined_error": round(err, 6),
                    "verdict": verdict(gain, err),
                })
    return comparisons


def write_report(
    *,
    model_name: str,
    fp16_path: Path,
    datasets: dict[str, Path],
    quant_models: dict[str, Path],
    results: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    chunks: int,
    threads: int,
    ngl: int | None,
    dataset_info: dict[str, Any],
    audit_warnings: list[str],
    failures: list[str],
    calibration: Path | None,
    out_path: Path | None,
) -> Path:
    report_dir = ROOT / "reports" / report_directory_name(model_name)
    report_path = out_path or (report_dir / "report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    missing_domains = sorted(set(datasets) - {r["domain"] for r in results})

    report: dict[str, Any] = {
        "schema_version": 2,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_model": model_name,
        "status": "partial" if failures else "complete",
        "fp16_model": relative_to_root(fp16_path),
        "fp16_size_gb": round(fp16_path.stat().st_size / 1e9, 4),
        "datasets": dataset_info,
        "calibration_file": relative_to_root(calibration) if calibration else None,
        "quantized_models": {n: relative_to_root(p) for n, p in quant_models.items()},
        "quantized_sizes_gb": {
            n: round(p.stat().st_size / 1e9, 4) for n, p in quant_models.items() if p.is_file()
        },
        "settings": {"chunks": chunks, "threads": threads, "ngl": ngl},
        "results": results,
        "comparisons": comparisons,
        "missing_domains": missing_domains,
        "audit_warnings": audit_warnings,
        "failures": failures,
        "measurement_notes": [
            "Every number was parsed from llama-perplexity output in this run.",
            "KLD measures divergence from the FP16 reference on the given dataset.",
            "Lower KLD means closer output distribution to FP16.",
            "Comparison errors assume independence, which is conservative for correlated chunks.",
            "These measurements do not establish task accuracy or regulatory compliance.",
        ],
    }

    tmp_path = report_path.with_name(report_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp_path, report_path)
    return report_path


def build_pdf(report_path: Path) -> Path | None:
    """Run make_pdf.py as a separate process so its import side effects cannot touch audit."""
    script = HERE / "make_pdf.py"
    if not script.is_file():
        print("[WARN] make_pdf.py not found; skipping PDF.", file=sys.stderr)
        return None
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--report", str(report_path)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[WARN] PDF step failed: {exc}. report.json is safe.", file=sys.stderr)
        return None
    if completed.returncode != 0:
        print(f"[WARN] PDF step exit {completed.returncode}. report.json is safe.\n"
              f"{completed.stderr[-400:]}", file=sys.stderr)
        return None
    pdf = report_path.with_suffix(".pdf")
    return pdf if pdf.is_file() else None


# ============================================================
# CLI
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain real GGUF audit engine.")
    parser.add_argument("--name", required=True, help="Model ID, e.g. Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--fp16", required=True, help="FP16 reference GGUF.")
    parser.add_argument("--dataset", action="append", required=True,
                        help="name=path. Repeat for multiple datasets.")
    parser.add_argument("--quant", action="append", required=True,
                        help="name=path. The FIRST --quant is the baseline for comparisons.")
    parser.add_argument("--calibration", default=None,
                        help="imatrix calibration text; overlap with datasets is checked.")
    parser.add_argument("--out", default=None,
                        help="Exact report.json path (worker.py uses this). Default: reports/<slug>/report.json")
    parser.add_argument("--chunks", type=int, default=100)
    parser.add_argument("--threads", type=int, default=max(1, min(os.cpu_count() or 2, 4)))
    parser.add_argument("--ngl", type=int, default=99,
                        help="GPU layers. 99 on a GPU box, 0 for CPU only.")
    parser.add_argument("--timeout", type=int, default=10800,
                        help="Seconds allowed for each llama-perplexity run.")
    parser.add_argument("--min-free-gb", type=float, default=5.0)
    parser.add_argument("--allow-partial", action="store_true",
                        help="Write a partial report if some evaluations fail (exit code 2).")
    parser.add_argument("--pdf", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not 1 <= args.chunks <= 100_000:
        print("ERROR: --chunks must be between 1 and 100000.", file=sys.stderr)
        return EXIT_FAIL
    if not 1 <= args.threads <= 256:
        print("ERROR: --threads must be between 1 and 256.", file=sys.stderr)
        return EXIT_FAIL
    if args.ngl < 0 or args.timeout < 1:
        print("ERROR: --ngl must be >= 0 and --timeout >= 1.", file=sys.stderr)
        return EXIT_FAIL

    try:
        model_name = args.name.strip()
        if not model_name or len(model_name) > 200:
            raise AuditError("Model name is empty or too long.")

        fp16_path = Path(args.fp16).expanduser()
        if not fp16_path.is_absolute():
            fp16_path = ROOT / fp16_path
        fp16_path = require_file(fp16_path.resolve(), "FP16 reference model")

        datasets = parse_named_paths(args.dataset, "dataset")
        quant_models = parse_named_paths(args.quant, "quant")

        for name, path in datasets.items():
            require_file(path, f"Dataset '{name}'")
        for name, path in quant_models.items():
            require_file(path, f"Quantized model '{name}'")
            if path == fp16_path:
                raise AuditError(f"--quant '{name}' is the same file as --fp16.")

        calibration = None
        if args.calibration:
            cpath = Path(args.calibration).expanduser()
            calibration = require_file(cpath if cpath.is_absolute() else ROOT / cpath,
                                       "Calibration file")

        binary = find_perplexity_binary()
        dataset_info, audit_warnings = dataset_checks(datasets, calibration)

        fp16_size = fp16_path.stat().st_size
        for name, path in quant_models.items():
            if path.stat().st_size >= fp16_size:
                audit_warnings.append(f"Quant '{name}' is not smaller than FP16; check file pairing.")

        if args.chunks < MIN_CHUNKS_WARN:
            audit_warnings.append(
                f"Only {args.chunks} chunks (< {MIN_CHUNKS_WARN}); numbers are a rough estimate."
            )

        for text in audit_warnings:
            print(f"[WARN] {text}")

        print("=" * 64)
        print("STHAMLY NANOBRAIN - REAL MODEL AUDIT")
        print("=" * 64)
        print(f"Model:    {model_name}")
        print(f"FP16:     {fp16_path}")
        print(f"Datasets: {', '.join(datasets)}")
        print(f"Variants: {', '.join(quant_models)} (baseline: {next(iter(quant_models))})")
        print(f"Evaluator: {binary}")
        print("=" * 64)

        work_root = ROOT / "work" / "audit" / slugify(model_name)
        log_dir = work_root / "logs"
        base_dir = work_root / "base"

        all_results: list[dict[str, Any]] = []
        failures: list[str] = []
        sink = failures if args.allow_partial else None

        for domain, dataset_path in datasets.items():
            print(f"\nEvaluating dataset: {domain}")
            all_results.extend(evaluate_domain(
                binary=binary,
                domain=domain,
                dataset_path=dataset_path,
                fp16_path=fp16_path,
                quant_models=quant_models,
                chunks=args.chunks,
                threads=args.threads,
                ngl=args.ngl,
                timeout=args.timeout,
                base_dir=base_dir,
                log_dir=log_dir,
                min_free_gb=args.min_free_gb,
                failures=sink,
            ))

        if not all_results:
            raise AuditError("No real evaluation result was produced. No report written.")

        comparisons = build_comparisons(all_results, list(quant_models))

        out_path = None
        if args.out:
            out_path = Path(args.out).expanduser()
            if not out_path.is_absolute():
                out_path = ROOT / out_path

        report_path = write_report(
            model_name=model_name,
            fp16_path=fp16_path,
            datasets=datasets,
            quant_models=quant_models,
            results=all_results,
            comparisons=comparisons,
            chunks=args.chunks,
            threads=args.threads,
            ngl=args.ngl,
            dataset_info=dataset_info,
            audit_warnings=audit_warnings,
            failures=failures,
            calibration=calibration,
            out_path=out_path.resolve() if out_path else None,
        )

        print("\n" + "=" * 64)
        print("AUDIT PARTIAL" if failures else "AUDIT COMPLETE")
        print("=" * 64)
        for item in all_results:
            print(f"{item['domain']} / {item['variant']}: "
                  f"KLD={item['kld']:.6f} ± {item['kld_error']:.6f}")
        for comp in comparisons:
            print(f"{comp['domain']}: {comp['candidate']} vs {comp['baseline']} "
                  f"[{comp['metric']}] gain={comp['gain_for_candidate']:+.6f} "
                  f"err={comp['combined_error']:.6f} -> {comp['verdict']}")

        print(f"\nReport: {report_path}")
        print("Note: KLD is a proxy metric, not proof of task accuracy.")

        if args.pdf:
            pdf_path = build_pdf(report_path)
            if pdf_path:
                print(f"PDF: {pdf_path}")

        return EXIT_PARTIAL if failures else EXIT_OK

    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except (OSError, ValueError, TypeError) as exc:
        print(f"AUDIT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())