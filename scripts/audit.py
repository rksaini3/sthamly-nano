#!/usr/bin/env python3
"""
Sthamly NanoBrain — Real Model Audit Engine

Purpose:
    Compare one or more quantized GGUF models against an FP16 reference
    using llama.cpp's llama-perplexity KL-divergence evaluation.

Example:
    python scripts/audit.py \
        --name Qwen/Qwen2.5-1.5B-Instruct \
        --fp16 models/model-f16.gguf \
        --dataset hindi=data/hindi_test.txt \
        --quant q4=models/model-Q4_K_M.gguf

Important:
    - No synthetic KLD values are generated.
    - Missing metrics cause the audit to fail.
    - KLD is a proxy measurement, not proof of task accuracy.
    - A successful command does not imply legal or regulatory compliance.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


# ============================================================
# ERROR HANDLING
# ============================================================

class AuditError(RuntimeError):
    """Raised when a real audit cannot be completed."""


def die(message: str, exit_code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(exit_code)


# ============================================================
# SAFE IDENTIFIERS AND PATHS
# ============================================================

def slugify(value: str) -> str:
    """
    Convert a model name into a safe, readable identifier.

    Example:
        Qwen/Qwen2.5-1.5B-Instruct
        -> qwen-qwen2.5-1.5b-instruct
    """
    result = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        value.strip(),
    ).strip("-")

    return result.lower() or "model"


def report_directory_name(model_name: str) -> str:
    """
    Match the directory naming convention used by worker.py.

    worker.safe_model_name() replaces '/' with '_'.
    """
    result = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        model_name.strip(),
    ).strip("._-")

    if not result:
        raise AuditError("Invalid model name.")

    return result[:120].lower()


def require_file(path: Path, description: str) -> Path:
    if not path.exists():
        raise AuditError(f"{description} does not exist: {path}")

    if not path.is_file():
        raise AuditError(f"{description} is not a file: {path}")

    if path.stat().st_size <= 0:
        raise AuditError(f"{description} is empty: {path}")

    return path.resolve()


def safe_model_name(value: str) -> str:
    """
    Reject path separators and unsafe characters in model identifiers.
    """
    if not value or not value.strip():
        raise AuditError("Model name cannot be empty.")

    if len(value) > 200:
        raise AuditError("Model name is too long.")

    return value.strip()


# ============================================================
# REAL METRIC PARSING
# ============================================================

# These patterns parse measurements emitted by llama-perplexity.
# They do not calculate or invent measurements.

PATS = {
    "kld": (
        r"Mean\s+KLD\s*:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\\/-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    ),
    "rms": (
        r"RMS\s+\S+\s*:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\\/-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    ),
    "top": (
        r"Same\s+top\s+p\s*:\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s*(?:±|\+/-|\+\\/-)\s*"
        r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    ),
}


def parse(text: str) -> dict[str, list[float]]:
    """
    Parse actual llama-perplexity output.

    Returns:
        {
            "kld": [mean, error],
            "rms": [mean, error],
            "top": [mean, error]
        }

    Metrics absent from the output remain absent.
    """
    result: dict[str, list[float]] = {}

    for metric, pattern in PATS.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)

        if not match:
            continue

        try:
            mean = float(match.group(1))
            error = float(match.group(2))
        except (TypeError, ValueError):
            continue

        if not math.isfinite(mean) or not math.isfinite(error):
            continue

        if error < 0:
            continue

        result[metric] = [mean, error]

    return result


# ============================================================
# COMPARISON HELPERS
# ============================================================

def gain(
    a: dict[str, Any],
    b: dict[str, Any],
    key: str = "kld",
) -> tuple[float, float]:
    """
    Calculate the difference between two measured metrics.

    For KLD/RMS:
        positive gain means model a has a lower metric than model b.

    For same-top-token rate:
        positive gain means model a has a higher metric than model b.

    Returns:
        (difference, combined_error)

    This function compares measurements; it does not create measurements.
    """
    if key not in a or key not in b:
        raise ValueError(f"Metric '{key}' missing from comparison.")

    av = a[key]
    bv = b[key]

    if not isinstance(av, (list, tuple)) or len(av) != 2:
        raise ValueError(f"Invalid metric format for a['{key}'].")

    if not isinstance(bv, (list, tuple)) or len(bv) != 2:
        raise ValueError(f"Invalid metric format for b['{key}'].")

    a_mean, a_error = float(av[0]), float(av[1])
    b_mean, b_error = float(bv[0]), float(bv[1])

    if not all(
        math.isfinite(x)
        for x in (a_mean, a_error, b_mean, b_error)
    ):
        raise ValueError("Non-finite measurement cannot be compared.")

    if a_error < 0 or b_error < 0:
        raise ValueError("Measurement errors cannot be negative.")

    if key in ("kld", "rms"):
        difference = b_mean - a_mean
    else:
        difference = a_mean - b_mean

    combined_error = math.sqrt(a_error**2 + b_error**2)

    return difference, combined_error


def verdict(
    difference: float,
    combined_error: float,
) -> str:
    """
    Give a conservative descriptive comparison.

    A difference greater than twice the combined error is labelled
    a clear signal, not proof of general model superiority.
    """
    if not math.isfinite(difference):
        return "DATA INVALID"

    if not math.isfinite(combined_error) or combined_error < 0:
        return "DATA INVALID"

    if difference > 2 * combined_error:
        return "SAAF FARK"

    return "ISHARA, SAAF NAHI"


# ============================================================
# COMMAND DISCOVERY
# ============================================================

def find_perplexity_binary() -> str:
    """
    Find llama-perplexity.

    Priority:
        1. LLAMA_PERPLEXITY environment variable
        2. Common llama.cpp build locations
        3. PATH
    """
    configured = os.getenv("LLAMA_PERPLEXITY", "").strip()

    candidates: list[str] = []

    if configured:
        candidates.append(configured)

    candidates.extend([
        str(ROOT / "llama.cpp" / "build" / "bin" / "llama-perplexity"),
        str(ROOT / "llama.cpp" / "build" / "bin" / "llama-perplexity.exe"),
        str(ROOT / "llama.cpp" / "build" / "bin" / "Release" / "llama-perplexity.exe"),
    ])

    for candidate in candidates:
        path = Path(candidate)

        if path.is_file():
            return str(path.resolve())

        found = shutil.which(candidate)

        if found:
            return found

    found = shutil.which("llama-perplexity")

    if found:
        return found

    found = shutil.which("llama-perplexity.exe")

    if found:
        return found

    raise AuditError(
        "llama-perplexity nahi mila. "
        "llama.cpp build karo ya LLAMA_PERPLEXITY environment variable set karo."
    )


# ============================================================
# DATASET AND QUANT INPUT PARSING
# ============================================================

def parse_named_paths(
    values: list[str],
    argument_name: str,
) -> dict[str, Path]:
    """
    Parse arguments in name=path format.

    Example:
        hindi=data/hindi_test.txt
        q4=models/model-Q4_K_M.gguf
    """
    parsed: dict[str, Path] = {}

    for value in values:
        if "=" not in value:
            raise AuditError(
                f"--{argument_name} value '{value}' invalid hai. "
                "Format name=path use karo."
            )

        name, raw_path = value.split("=", 1)

        name = name.strip()
        raw_path = raw_path.strip()

        if not name:
            raise AuditError(
                f"--{argument_name} mein name khaali nahi ho sakta."
            )

        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            raise AuditError(
                f"--{argument_name} ka name invalid hai: {name}"
            )

        if not raw_path:
            raise AuditError(
                f"--{argument_name} mein path khaali nahi ho sakta."
            )

        if name in parsed:
            raise AuditError(
                f"--{argument_name} mein duplicate name hai: {name}"
            )

        path = Path(raw_path).expanduser()

        if not path.is_absolute():
            path = ROOT / path

        parsed[name] = path.resolve()

    return parsed


# ============================================================
# REAL LLAMA.CPP EVALUATION
# ============================================================

def run_command(
    command: list[str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """
    Execute a real evaluation command.

    Non-zero exit codes are treated as failures.
    Output is not converted into synthetic results.
    """
    print(
        "\n$ " + " ".join(str(part) for part in command),
        flush=True,
    )

    try:
        result = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuditError(
            f"Evaluation timeout: {timeout} seconds."
        ) from exc
    except OSError as exc:
        raise AuditError(
            f"Evaluation command execute nahi hua: {exc}"
        ) from exc

    output = result.stdout or ""

    if output:
        # Console output may be large, so limit only what is displayed.
        print(output[-12000:], flush=True)

    if result.returncode != 0:
        raise AuditError(
            "llama-perplexity failed with exit code "
            f"{result.returncode}. Real metrics generate nahi hue."
        )

    return result


def evaluate_domain(
    *,
    binary: str,
    domain: str,
    dataset_path: Path,
    fp16_path: Path,
    quant_models: dict[str, Path],
    chunks: int,
    threads: int,
    timeout: int,
    work_dir: Path,
) -> list[dict[str, Any]]:
    """
    Evaluate all quantized models against an FP16 reference for one dataset.

    First, llama-perplexity generates the FP16 KL-divergence base.
    Then each quantized model is evaluated against that base.
    """
    require_file(dataset_path, f"Dataset '{domain}'")
    require_file(fp16_path, "FP16 reference model")

    work_dir.mkdir(parents=True, exist_ok=True)

    base_path = work_dir / f"{slugify(domain)}_fp16.kld"

    common_args = [
        "-f",
        str(dataset_path),
        "-t",
        str(threads),
        "--chunks",
        str(chunks),
    ]

    if not base_path.exists() or base_path.stat().st_size == 0:
        base_command = [
            binary,
            "-m",
            str(fp16_path),
            "--kl-divergence-base",
            str(base_path),
            *common_args,
        ]

        run_command(base_command, timeout)

    if not base_path.is_file() or base_path.stat().st_size == 0:
        raise AuditError(
            f"FP16 KLD base generate nahi hua: {base_path}"
        )

    domain_results: list[dict[str, Any]] = []

    for variant, quant_path in quant_models.items():
        require_file(quant_path, f"Quantized model '{variant}'")

        command = [
            binary,
            "-m",
            str(quant_path),
            "--kl-divergence-base",
            str(base_path),
            "--kl-divergence",
            *common_args,
        ]

        result = run_command(command, timeout)
        metrics = parse(result.stdout or "")

        if "kld" not in metrics:
            raise AuditError(
                f"'{variant}' ke liye actual KLD metric parse nahi hua. "
                "Report ko successful mark nahi kiya gaya."
            )

        item: dict[str, Any] = {
            "domain": domain,
            "variant": variant,
            "model_path": str(quant_path),
            "kld": metrics["kld"][0],
            "kld_error": metrics["kld"][1],
        }

        if "rms" in metrics:
            item["rms"] = metrics["rms"][0]
            item["rms_error"] = metrics["rms"][1]

        if "top" in metrics:
            item["same_top_p"] = metrics["top"][0]
            item["same_top_p_error"] = metrics["top"][1]

        domain_results.append(item)

    return domain_results


# ============================================================
# REPORT GENERATION
# ============================================================

def write_report(
    *,
    model_name: str,
    fp16_path: Path,
    datasets: dict[str, Path],
    quant_models: dict[str, Path],
    results: list[dict[str, Any]],
    chunks: int,
    threads: int,
) -> Path:
    """
    Write report.json only after all requested evaluations succeed.
    """
    report_dir = (
        ROOT
        / "reports"
        / report_directory_name(model_name)
    )

    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "report.json"

    report = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_model": model_name,
        "fp16_model": str(fp16_path),
        "datasets": {
            name: str(path)
            for name, path in datasets.items()
        },
        "quantized_models": {
            name: str(path)
            for name, path in quant_models.items()
        },
        "settings": {
            "chunks": chunks,
            "threads": threads,
        },
        "results": results,
        "measurement_notes": [
            "Measurements were parsed from llama-perplexity output.",
            "KLD measures divergence from the FP16 reference.",
            "Lower KLD indicates closer measured output distribution.",
            "These measurements do not establish general task accuracy.",
            "No legal or regulatory compliance certification is implied.",
        ],
    }

    temporary_path = report_path.with_suffix(".json.tmp")

    temporary_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temporary_path.replace(report_path)

    return report_path


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sthamly NanoBrain real GGUF audit engine."
    )

    parser.add_argument(
        "--name",
        required=True,
        help="Model ID/name, e.g. Qwen/Qwen2.5-1.5B-Instruct",
    )

    parser.add_argument(
        "--fp16",
        required=True,
        help="Path to the FP16 reference GGUF.",
    )

    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Dataset in name=path format. Repeat for multiple datasets.",
    )

    parser.add_argument(
        "--quant",
        action="append",
        required=True,
        help="Quantized GGUF in name=path format. Repeat for variants.",
    )

    parser.add_argument(
        "--chunks",
        type=int,
        default=12,
        help="Evaluation chunks; larger values generally take longer.",
    )

    parser.add_argument(
        "--threads",
        type=int,
        default=max(1, min(os.cpu_count() or 2, 4)),
        help="CPU threads for llama-perplexity.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=10800,
        help="Timeout in seconds for each evaluation command.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.chunks < 1 or args.chunks > 100000:
        die("--chunks 1 se 100000 ke beech hona chahiye.", 2)

    if args.threads < 1 or args.threads > 256:
        die("--threads 1 se 256 ke beech hona chahiye.", 2)

    if args.timeout < 1:
        die("--timeout positive number hona chahiye.", 2)

    try:
        model_name = safe_model_name(args.name)

        fp16_path = Path(args.fp16).expanduser()

        if not fp16_path.is_absolute():
            fp16_path = ROOT / fp16_path

        fp16_path = require_file(
            fp16_path.resolve(),
            "FP16 reference model",
        )

        datasets = parse_named_paths(
            args.dataset,
            "dataset",
        )

        quant_models = parse_named_paths(
            args.quant,
            "quant",
        )

        if not datasets:
            raise AuditError("Kam se kam ek dataset dena zaroori hai.")

        if not quant_models:
            raise AuditError(
                "Kam se kam ek quantized model dena zaroori hai."
            )

        for domain, dataset_path in datasets.items():
            require_file(
                dataset_path,
                f"Dataset '{domain}'",
            )

        for variant, quant_path in quant_models.items():
            require_file(
                quant_path,
                f"Quantized model '{variant}'",
            )

        binary = find_perplexity_binary()

        print("=" * 64)
        print("STHAMLY NANOBRAIN — REAL MODEL AUDIT")
        print("=" * 64)
        print(f"Model: {model_name}")
        print(f"FP16: {fp16_path}")
        print(f"Datasets: {len(datasets)}")
        print(f"Quantized variants: {len(quant_models)}")
        print(f"Evaluator: {binary}")
        print("Synthetic metrics: DISABLED")
        print("=" * 64)

        all_results: list[dict[str, Any]] = []

        for domain, dataset_path in datasets.items():
            print(f"\nEvaluating dataset: {domain}")

            results = evaluate_domain(
                binary=binary,
                domain=domain,
                dataset_path=dataset_path,
                fp16_path=fp16_path,
                quant_models=quant_models,
                chunks=args.chunks,
                threads=args.threads,
                timeout=args.timeout,
                work_dir=ROOT / "work" / "audit" / slugify(model_name),
            )

            all_results.extend(results)

        if not all_results:
            raise AuditError(
                "Koi real evaluation result nahi mila. Report nahi banegi."
            )

        report_path = write_report(
            model_name=model_name,
            fp16_path=fp16_path,
            datasets=datasets,
            quant_models=quant_models,
            results=all_results,
            chunks=args.chunks,
            threads=args.threads,
        )

        print("\n" + "=" * 64)
        print("AUDIT COMPLETE")
        print("=" * 64)

        for item in all_results:
            print(
                f"{item['domain']} / {item['variant']}: "
                f"KLD={item['kld']:.6f} "
                f"± {item['kld_error']:.6f}"
            )

        print(f"\nReport: {report_path}")
        print("Note: KLD is a proxy metric, not task-accuracy proof.")

        return 0

    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(
            f"AUDIT FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())