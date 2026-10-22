#!/usr/bin/env python3
"""
Sthamly NanoBrain - Real Model Audit Engine

Purpose:
    Compare one or more quantized GGUF models against an FP16 reference
    using llama.cpp's llama-perplexity KL-divergence evaluation.

Example:
    python scripts/audit.py \
        --name Qwen/Qwen2.5-1.5B-Instruct \
        --fp16 models/gguf/Qwen__Qwen2.5-1.5B-Instruct-f16.gguf \
        --dataset hindi=data/hindi_test.txt \
        --quant q4=models/gguf/Qwen__Qwen2.5-1.5B-Instruct-Q4_K_M-standard.gguf \
        --chunks 100 --pdf

Important:
    - No synthetic KLD values are generated.
    - Missing metrics cause the audit to fail (ya --allow-partial se baaki results ke saath report).
    - KLD is a proxy measurement, not proof of task accuracy.

Exit codes: 0 = sab theek | 1 = fail | 2 = --allow-partial me kuch benchmarks fail hue
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
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

MIN_DATASET_BYTES = 20_000      # isse chhota dataset = kamzor estimate
OVERLAP_WARN = 0.05             # calibration se >5% lines same -> warning
MIN_CHUNKS_WARN = 50


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
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-")
    return result.lower() or "model"


def report_directory_name(model_name: str) -> str:
    """Same convention as worker.py: '/' -> '_' (aur lowercase)."""
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", model_name.strip()).strip("._-")

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
    if not value or not value.strip():
        raise AuditError("Model name cannot be empty.")

    if len(value) > 200:
        raise AuditError("Model name is too long.")

    return value.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)

    return digest.hexdigest()


# ============================================================
# REAL METRIC PARSING
# ============================================================

NUM = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
# "±" ya ASCII "+/-"; encoding bigad jaye to U+FFFD / mojibake bhi chalega.
PM = r"\s*(?:±|\+/-|\+\s*/\s*-|\ufffd|Â±)\s*"
PAIR = NUM + PM + NUM

PATS = {
    "kld": r"Mean\s+KLD\s*[:=]\s*" + PAIR,
    # "RMS Δp" ka Δ bigad sakta hai -> label ke beech kuch bhi (max 12 char) chalega
    "rms": r"RMS\s+[^:=\n]{0,12}[:=]\s*" + PAIR,
    "top": r"Same\s+top\s*p\s*[:=]\s*" + PAIR,
    "ppl": r"Mean\s+PPL\(Q\)\s*[:=]\s*" + PAIR,
    "ppl_base": r"Mean\s+PPL\(base\)\s*[:=]\s*" + PAIR,
}


def parse(text: str) -> dict[str, list[float]]:
    """
    Parse actual llama-perplexity output.

    Returns {"kld": [mean, error], "rms": [...], "top": [...], "ppl": [...], "ppl_base": [...]}.
    Metrics absent from the output remain absent.
    Output me AAKHRI match uthaya jata hai (summary), pehla nahi.
    """
    result: dict[str, list[float]] = {}

    for metric, pattern in PATS.items():
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))

        if not matches:
            continue

        try:
            mean = float(matches[-1].group(1))
            error = float(matches[-1].group(2))
        except (TypeError, ValueError):
            continue

        if not math.isfinite(mean) or not math.isfinite(error) or error < 0:
            continue

        result[metric] = [mean, error]

    return result


# ============================================================
# COMPARISON HELPERS
# ============================================================

def gain(a: dict[str, Any], b: dict[str, Any], key: str = "kld") -> tuple[float, float]:
    """
    Difference between two measured metrics.
    KLD/RMS: positive gain = model a has a LOWER metric than b.
    same-top-p: positive gain = model a has a HIGHER metric than b.
    Returns (difference, combined_error).
    """
    if key not in a or key not in b:
        raise ValueError(f"Metric '{key}' missing from comparison.")

    av, bv = a[key], b[key]

    if not isinstance(av, (list, tuple)) or len(av) != 2:
        raise ValueError(f"Invalid metric format for a['{key}'].")

    if not isinstance(bv, (list, tuple)) or len(bv) != 2:
        raise ValueError(f"Invalid metric format for b['{key}'].")

    a_mean, a_error = float(av[0]), float(av[1])
    b_mean, b_error = float(bv[0]), float(bv[1])

    if not all(math.isfinite(x) for x in (a_mean, a_error, b_mean, b_error)):
        raise ValueError("Non-finite measurement cannot be compared.")

    if a_error < 0 or b_error < 0:
        raise ValueError("Measurement errors cannot be negative.")

    difference = (b_mean - a_mean) if key in ("kld", "rms") else (a_mean - b_mean)

    return difference, math.sqrt(a_error ** 2 + b_error ** 2)


def verdict(difference: float, combined_error: float) -> str:
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
    Priority: LLAMA_PERPLEXITY env (galat ho to seedha error) -> build locations -> PATH.
    """
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

        raise AuditError(f"LLAMA_PERPLEXITY chalne layak nahi hai: {configured}")

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
        "llama-perplexity nahi mila. "
        "llama.cpp build karo ya LLAMA_PERPLEXITY environment variable set karo."
    )


# ============================================================
# DATASET AND QUANT INPUT PARSING
# ============================================================

def parse_named_paths(values: list[str], argument_name: str) -> dict[str, Path]:
    """Parse name=path arguments (relative path ROOT se resolve hota hai)."""
    parsed: dict[str, Path] = {}

    for value in values:
        if "=" not in value:
            raise AuditError(
                f"--{argument_name} value '{value}' invalid hai. Format name=path use karo."
            )

        name, raw_path = value.split("=", 1)
        name, raw_path = name.strip(), raw_path.strip()

        if not name:
            raise AuditError(f"--{argument_name} mein name khaali nahi ho sakta.")

        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name):
            raise AuditError(f"--{argument_name} ka name invalid hai: {name}")

        if not raw_path:
            raise AuditError(f"--{argument_name} mein path khaali nahi ho sakta.")

        if name in parsed:
            raise AuditError(f"--{argument_name} mein duplicate name hai: {name}")

        path = Path(raw_path).expanduser()

        if not path.is_absolute():
            path = ROOT / path

        parsed[name] = path.resolve()

    return parsed


# ============================================================
# REAL LLAMA.CPP EVALUATION
# ============================================================

def run_command(command: list[str], timeout: int, log_path: Path | None = None,
                label: str = "") -> str:
    """
    Real evaluation chalao: output LIVE dikhta hai, poora output log file me save hota hai,
    timeout par process kill hota hai. Non-zero exit = AuditError. Shell use nahi hota.
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
        raise AuditError(f"Evaluation command execute nahi hua: {exc}") from exc

    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        process.kill()

    timer = threading.Timer(timeout, kill)
    timer.start()
    lines: list[str] = []

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
        raise AuditError(f"Evaluation timeout: {timeout} seconds.")

    if process.returncode != 0:
        raise AuditError(
            f"llama-perplexity failed with exit code {process.returncode}. "
            "Real metrics generate nahi hue."
        )

    return output


def ensure_base(*, binary: str, domain: str, dataset_path: Path, fp16_path: Path,
                common_args: list[str], timeout: int, work_dir: Path, log_dir: Path,
                fingerprint: dict[str, Any], min_free_gb: float) -> Path:
    """
    FP16 ki base logits file banao ya pichli SAME settings wali reuse karo.
    Fingerprint (fp16 file, dataset content, chunks, ngl...) badla to purani base nahi chalegi.
    Base .part me banti hai, isliye adhoori file reuse nahi hoti.
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    base_path = work_dir / f"{slugify(domain)}_fp16.kld"
    meta_path = work_dir / f"{slugify(domain)}_fp16.kld.json"

    if base_path.is_file() and base_path.stat().st_size > 0 and meta_path.is_file():
        try:
            if json.loads(meta_path.read_text(encoding="utf-8")) == fingerprint:
                print(f"[INFO] FP16 base reuse ho raha hai: {base_path.name}")
                return base_path
        except ValueError:
            pass

    free = shutil.disk_usage(work_dir).free / 1e9

    if free < min_free_gb:
        raise AuditError(
            f"Disk kam hai: {free:.1f} GB free, kam se kam {min_free_gb:g} GB chahiye "
            "(base logits file badi hoti hai). --chunks kam karo ya disk badhao."
        )

    base_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)

    part = base_path.with_name(base_path.name + ".part")
    part.unlink(missing_ok=True)

    command = [binary, "-m", str(fp16_path), "--kl-divergence-base", str(part), *common_args]

    try:
        run_command(command, timeout, log_dir / f"base__{slugify(domain)}.log",
                    f"FP16 base logits on {dataset_path.name}")

        if not part.is_file() or part.stat().st_size == 0:
            raise AuditError(
                f"FP16 KLD base generate nahi hua: {base_path}. "
                "Aapka llama.cpp build --kl-divergence-base support karta hai?"
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
    timeout: int,
    work_dir: Path,
    ngl: int | None = None,
    log_dir: Path | None = None,
    min_free_gb: float = 5.0,
    errors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Evaluate all quantized models against FP16 for one dataset.

    errors=None  -> pehli fail par AuditError raise (strict).
    errors=[...] -> fail ko list me daalo aur baaki quants chalate raho (--allow-partial).
    """
    require_file(dataset_path, f"Dataset '{domain}'")
    require_file(fp16_path, "FP16 reference model")

    log_dir = log_dir or (work_dir / "logs")

    common_args = ["-f", str(dataset_path), "-t", str(threads), "--chunks", str(chunks)]

    if ngl is not None:
        common_args += ["-ngl", str(ngl)]

    stat = fp16_path.stat()
    fingerprint = {
        "fp16_size": stat.st_size,
        "fp16_mtime_ns": stat.st_mtime_ns,
        "dataset_sha256": sha256_file(dataset_path),
        "chunks": chunks,
    }

    base_path = ensure_base(
        binary=binary, domain=domain, dataset_path=dataset_path, fp16_path=fp16_path,
        common_args=common_args, timeout=timeout, work_dir=work_dir, log_dir=log_dir,
        fingerprint=fingerprint, min_free_gb=min_free_gb,
    )

    domain_results: list[dict[str, Any]] = []

    for variant, quant_path in quant_models.items():
        try:
            require_file(quant_path, f"Quantized model '{variant}'")

            command = [
                binary, "-m", str(quant_path),
                "--kl-divergence-base", str(base_path),
                "--kl-divergence", *common_args,
            ]

            output = run_command(
                command, timeout, log_dir / f"{slugify(variant)}__{slugify(domain)}.log",
                f"{quant_path.name} vs FP16 on {dataset_path.name}",
            )
            metrics = parse(output)

            if "kld" not in metrics:
                raise AuditError(
                    f"'{variant}' ke liye actual KLD metric parse nahi hua. "
                    "Report ko successful mark nahi kiya gaya."
                )
        except AuditError as exc:
            if errors is None:
                raise

            message = f"[{variant} x {domain}] {exc}"
            print(f"ERROR: {message}", file=sys.stderr)
            errors.append(message)
            continue

        item: dict[str, Any] = {
            "domain": domain,
            "variant": variant,
            "model_path": str(quant_path),
            "kld": metrics["kld"][0],
            "kld_error": metrics["kld"][1],
        }

        for key, (field, field_err) in {
            "rms": ("rms", "rms_error"),
            "top": ("same_top_p", "same_top_p_error"),
            "ppl": ("ppl", "ppl_error"),
            "ppl_base": ("ppl_base", "ppl_base_error"),
        }.items():
            if key in metrics:
                item[field] = metrics[key][0]
                item[field_err] = metrics[key][1]

        domain_results.append(item)

    return domain_results


# ============================================================
# DATA-VALIDITY CHECKS
# ============================================================

def nonempty_lines(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {line.strip() for line in text.splitlines() if line.strip()}


def dataset_checks(datasets: dict[str, Path], calibration: Path | None) -> tuple[dict[str, Any], list[str]]:
    info: dict[str, Any] = {}
    warnings: list[str] = []
    calib_sha = sha256_file(calibration) if calibration else None
    calib_lines = nonempty_lines(calibration) if calibration else set()

    for name, path in datasets.items():
        size = path.stat().st_size
        entry: dict[str, Any] = {"file": str(path), "bytes": size, "sha256": sha256_file(path)}

        if size < MIN_DATASET_BYTES:
            warnings.append(
                f"Dataset '{name}' chhota hai ({size} bytes < {MIN_DATASET_BYTES}); KLD estimates kamzor ho sakte hain."
            )

        if calibration is not None:
            if entry["sha256"] == calib_sha:
                entry["calibration_overlap"] = 1.0
                warnings.append(
                    f"Dataset '{name}' calibration (imatrix) file ke BILKUL same hai -- 'before vs after' valid nahi."
                )
            else:
                lines = nonempty_lines(path)
                overlap = len(lines & calib_lines) / len(lines) if lines else 0.0
                entry["calibration_overlap"] = round(overlap, 4)

                if overlap > OVERLAP_WARN:
                    warnings.append(
                        f"Dataset '{name}' ki {overlap:.0%} lines calibration file se milti hain; "
                        "imatrix quant ko unfair fayda mil sakta hai. Held-out dataset use karo."
                    )

        info[name] = entry

    return info, warnings


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
    ngl: int | None = None,
    dataset_info: dict[str, Any] | None = None,
    audit_warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> Path:
    """report.json atomically likho (pehle .tmp, phir rename)."""
    report_dir = ROOT / "reports" / report_directory_name(model_name)
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "report.json"

    report = {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "target_model": model_name,
        "fp16_model": str(fp16_path),
        "fp16_size_gb": round(fp16_path.stat().st_size / 1e9, 4),
        "datasets": dataset_info if dataset_info else {n: str(p) for n, p in datasets.items()},
        "quantized_models": {n: str(p) for n, p in quant_models.items()},
        "quantized_sizes_gb": {
            n: round(p.stat().st_size / 1e9, 4) for n, p in quant_models.items() if p.is_file()
        },
        "settings": {"chunks": chunks, "threads": threads, "ngl": ngl},
        "results": results,
        "audit_warnings": audit_warnings or [],
        "errors": errors or [],
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
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    temporary_path.replace(report_path)

    return report_path


def build_pdf(report_path: Path) -> Path | None:
    """make_pdf.py chalao. PDF fail ho to audit fail nahi hota (report.json already safe hai)."""
    script = HERE / "make_pdf.py"

    try:
        spec = importlib.util.spec_from_file_location("sthamly_make_pdf", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.make_pdf(report_path)
    except Exception as exc:
        print(f"[WARN] PDF nahi bani ({type(exc).__name__}: {exc}). report.json safe hai; "
              f"baad me: python scripts/make_pdf.py --report {report_path}", file=sys.stderr)
        return None


# ============================================================
# CLI
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain real GGUF audit engine.")

    parser.add_argument("--name", required=True, help="Model ID/name, e.g. Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--fp16", required=True, help="Path to the FP16 reference GGUF.")
    parser.add_argument("--dataset", action="append", required=True,
                        help="Dataset in name=path format. Repeat for multiple datasets.")
    parser.add_argument("--quant", action="append", required=True,
                        help="Quantized GGUF in name=path format. Repeat for variants.")
    parser.add_argument("--chunks", type=int, default=100,
                        help=f"Evaluation chunks (default 100; <{MIN_CHUNKS_WARN} = rough estimate).")
    parser.add_argument("--threads", type=int, default=max(1, min(os.cpu_count() or 2, 4)),
                        help="CPU threads for llama-perplexity.")
    parser.add_argument("--ngl", type=int, default=99,
                        help="GPU layers (-ngl). GPU (RunPod) par 99; CPU-only par 0.")
    parser.add_argument("--timeout", type=int, default=10800,
                        help="Timeout in seconds for each evaluation command.")
    parser.add_argument("--calibration", default=None,
                        help="imatrix calibration text file; test dataset se overlap ho to warning.")
    parser.add_argument("--min-free-gb", type=float, default=5.0,
                        help="FP16 base file banane se pehle itna free disk chahiye.")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Kuch benchmarks fail hon to bhi baaki ke saath report likho (exit code 2).")
    parser.add_argument("--pdf", action="store_true",
                        help="Report ke baad developer PDF bhi banao (scripts/make_pdf.py).")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.chunks < 1 or args.chunks > 100000:
        die("--chunks 1 se 100000 ke beech hona chahiye.", 2)

    if args.threads < 1 or args.threads > 256:
        die("--threads 1 se 256 ke beech hona chahiye.", 2)

    if args.ngl < 0:
        die("--ngl negative nahi ho sakta.", 2)

    if args.timeout < 1:
        die("--timeout positive number hona chahiye.", 2)

    try:
        model_name = safe_model_name(args.name)

        fp16_path = Path(args.fp16).expanduser()

        if not fp16_path.is_absolute():
            fp16_path = ROOT / fp16_path

        fp16_path = require_file(fp16_path.resolve(), "FP16 reference model")

        datasets = parse_named_paths(args.dataset, "dataset")
        quant_models = parse_named_paths(args.quant, "quant")

        for domain, dataset_path in datasets.items():
            require_file(dataset_path, f"Dataset '{domain}'")

        for variant, quant_path in quant_models.items():
            require_file(quant_path, f"Quantized model '{variant}'")

            if quant_path == fp16_path:
                raise AuditError(f"--quant '{variant}' aur --fp16 ek hi file hain.")

        calibration = None

        if args.calibration:
            cpath = Path(args.calibration).expanduser()
            calibration = require_file(cpath if cpath.is_absolute() else ROOT / cpath, "Calibration file")

        binary = find_perplexity_binary()
        dataset_info, audit_warnings = dataset_checks(datasets, calibration)

        fp16_size = fp16_path.stat().st_size

        for variant, quant_path in quant_models.items():
            if quant_path.stat().st_size >= fp16_size:
                audit_warnings.append(
                    f"Quant '{variant}' FP16 se chhota nahi hai -- files ka order/jodi check karo."
                )

        if args.chunks < MIN_CHUNKS_WARN:
            audit_warnings.append(
                f"Sirf {args.chunks} chunks evaluate hue (< {MIN_CHUNKS_WARN}); numbers rough estimate hain."
            )

        for text in audit_warnings:
            print(f"[WARN] {text}")

        print("=" * 64)
        print("STHAMLY NANOBRAIN - REAL MODEL AUDIT")
        print("=" * 64)
        print(f"Model: {model_name}")
        print(f"FP16: {fp16_path}")
        print(f"Datasets: {len(datasets)}")
        print(f"Quantized variants: {len(quant_models)}")
        print(f"Evaluator: {binary}")
        print("Synthetic metrics: DISABLED")
        print("=" * 64)

        all_results: list[dict[str, Any]] = []
        errors: list[str] = []
        sink = errors if args.allow_partial else None

        for domain, dataset_path in datasets.items():
            print(f"\nEvaluating dataset: {domain}")

            all_results.extend(
                evaluate_domain(
                    binary=binary,
                    domain=domain,
                    dataset_path=dataset_path,
                    fp16_path=fp16_path,
                    quant_models=quant_models,
                    chunks=args.chunks,
                    threads=args.threads,
                    timeout=args.timeout,
                    work_dir=ROOT / "work" / "audit" / slugify(model_name),
                    ngl=args.ngl,
                    log_dir=ROOT / "reports" / report_directory_name(model_name) / "logs",
                    min_free_gb=args.min_free_gb,
                    errors=sink,
                )
            )

        if not all_results:
            raise AuditError("Koi real evaluation result nahi mila. Report nahi banegi.")

        report_path = write_report(
            model_name=model_name,
            fp16_path=fp16_path,
            datasets=datasets,
            quant_models=quant_models,
            results=all_results,
            chunks=args.chunks,
            threads=args.threads,
            ngl=args.ngl,
            dataset_info=dataset_info,
            audit_warnings=audit_warnings,
            errors=errors,
        )

        print("\n" + "=" * 64)
        print("AUDIT COMPLETE" if not errors else "AUDIT PARTIAL")
        print("=" * 64)

        for item in all_results:
            print(f"{item['domain']} / {item['variant']}: KLD={item['kld']:.6f} ± {item['kld_error']:.6f}")

        print(f"\nReport: {report_path}")
        print("Note: KLD is a proxy metric, not task-accuracy proof.")

        if args.pdf:
            pdf_path = build_pdf(report_path)

            if pdf_path:
                print(f"PDF: {pdf_path}")

        return 2 if errors else 0

    except AuditError as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"AUDIT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())