#!/usr/bin/env python3
"""
Sthamly NanoBrain: model prepare step.

Downloads a Hugging Face model, verifies policy, converts it to FP16 GGUF,
and builds the plain Q4_K_M baseline (no imatrix) from the SAME FP16 file.

This module is used two ways:
    1. Imported by scripts/worker.py (preferred, one job at a time, job id given)
    2. Run standalone for manual testing:
         python scripts/prepare.py --model org/name --job-id UUID

Exit codes:
    0  success
    1  runtime failure (download, convert, quantize, disk, timeout)
    3  rejected: custom code (auto_map or .py files)
    4  rejected: policy (bad id, no safetensors, license, size)
    5  busy: another job is already preparing this model
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except ImportError:  # Windows dev machine
    fcntl = None  # type: ignore[assignment]

from huggingface_hub import HfApi, snapshot_download

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sthamly_common import (  # noqa: E402
    TABLE_JOBS,
    Supabase,
    SupabaseError,
    normalize_model_id,
    scrub,
)


# ============================================================
# PATHS AND CONFIG
# ============================================================

LLAMA_DIR = Path(os.getenv("LLAMA_CPP_DIR", str(ROOT / "llama.cpp")))
CONVERT = Path(os.getenv("LLAMA_CPP_CONVERTER", str(LLAMA_DIR / "convert_hf_to_gguf.py")))
QUANTIZE = Path(os.getenv("LLAMA_QUANTIZE", str(LLAMA_DIR / "build" / "bin" / "llama-quantize")))

HF_DIR = ROOT / "models" / "hf"
GGUF_DIR = ROOT / "models" / "gguf"

DEFAULT_ALLOWED_LICENSES = "apache-2.0,mit"
DEFAULT_MAX_GB = float(os.getenv("MAX_MODEL_GB", "20"))
DISK_FACTOR = 2.5
CONVERT_TIMEOUT = int(os.getenv("CONVERT_TIMEOUT", "3600"))
QUANT_TIMEOUT = int(os.getenv("QUANT_TIMEOUT", "3600"))
DOWNLOAD_TIMEOUT_NOTE = "snapshot_download has no hard timeout; use HF_HUB_DOWNLOAD_TIMEOUT if needed"

# Status names MUST match worker.py and the jobs table.
STATUS_RUNNING = os.getenv("NANOBRAIN_RUNNING_STATUS", "extracting")
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"  # only if your DB constraint allows it; see note in worker

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CUSTOM_CODE = 3
EXIT_POLICY = 4
EXIT_BUSY = 5

# Strict "owner/name" -- ASCII letters, digits, dot, underscore, hyphen only.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")

# Custom-code files that can run arbitrary Python when trust_remote_code is used.
CUSTOM_CODE_FILES_RE = re.compile(r"^(modeling_|configuration_|tokenization_).*\.py$|\.py$")


class PrepareError(Exception):
    """Controlled failure. `status` is written to the job; `report=False` leaves the job alone."""

    def __init__(
        self,
        message: str,
        code: int = EXIT_FAIL,
        status: str = STATUS_FAILED,
        report: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.report = report


# ============================================================
# COMMAND RUNNER WITH TIMEOUT
# ============================================================

def run(command: list[Any], timeout: int) -> None:
    """
    Run a real command with a hard timeout. Output streams to the terminal.
    Raises PrepareError on timeout or non-zero exit.
    """
    cmd = [str(part) for part in command]
    print("$ " + " ".join(cmd), flush=True)

    try:
        completed = subprocess.run(cmd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PrepareError(
            f"Command timed out after {timeout} seconds: {cmd[0]}"
        ) from exc
    except OSError as exc:
        raise PrepareError(f"Could not execute {cmd[0]}: {exc}") from exc

    if completed.returncode != 0:
        raise PrepareError(
            f"Command failed with exit code {completed.returncode}: {cmd[0]}"
        )


# ============================================================
# JOB STATUS REPORTER (Supabase, by job id only)
# ============================================================

class JobReporter:
    """
    Updates exactly one row: quantization_jobs where id = job_id.
    Never falls back to model-name matching, which could touch other jobs.
    Also sets heartbeat_at so reap_stale_jobs does not kill a healthy job.
    """

    def __init__(self, job_id: str | None) -> None:
        self.job_id = (job_id or "").strip() or None
        self._db: Supabase | None = None
        self._warned = False

        if self.job_id:
            try:
                self._db = Supabase.from_env()
            except SupabaseError as exc:
                print(f"[db-status] disabled: {scrub(exc, limit=200)}", file=sys.stderr)

    def update(self, status: str | None = None, progress: int | None = None,
               step: str | None = None) -> None:
        if self._db is None or self.job_id is None:
            return  # standalone terminal mode

        from datetime import datetime, timezone

        fields: dict[str, Any] = {"heartbeat_at": datetime.now(timezone.utc).isoformat()}
        if status is not None:
            fields["status"] = status
        if progress is not None:
            fields["progress"] = progress
        if step is not None:
            fields["step"] = step

        try:
            self._db.patch_job_by_id(TABLE_JOBS, self.job_id, fields)
        except SupabaseError as exc:
            if not self._warned:
                print(f"[db-status] update failed: {scrub(exc, limit=200)}", file=sys.stderr)
                self._warned = True


# ============================================================
# SAFETY CHECKS
# ============================================================

def validate_model_id(model_id: str) -> str:
    cleaned = (model_id or "").strip()
    if not MODEL_ID_RE.fullmatch(cleaned) or ".." in cleaned:
        raise PrepareError(
            f"REFUSED: model id must be 'owner/name': {cleaned!r}",
            EXIT_POLICY,
            STATUS_REJECTED,
        )
    return cleaned


def check_policy(
    model_id: str,
    revision: str | None,
    hf_token: str | None,
    allowed_licenses: set[str],
    check_license: bool,
    max_gb: float,
) -> tuple[str, int, str]:
    """
    Runs BEFORE any download. Returns (commit_sha, total_weight_bytes, license).
    Pinning to the commit sha means the download matches what was checked.
    """
    try:
        info = HfApi(token=hf_token).model_info(
            model_id, revision=revision, files_metadata=True
        )
    except Exception as exc:
        raise PrepareError(
            f"REFUSED: model info not available ({type(exc).__name__}). "
            "Private, gated, or wrong name?",
            EXIT_POLICY,
            STATUS_REJECTED,
        ) from exc

    if not info.sha:
        raise PrepareError("REFUSED: Hugging Face did not return a commit sha.",
                           EXIT_POLICY, STATUS_REJECTED)

    siblings = info.siblings or []
    safetensors = [s for s in siblings if s.rfilename.endswith(".safetensors")]
    if not safetensors:
        raise PrepareError("REFUSED: repo has no .safetensors weights.",
                           EXIT_POLICY, STATUS_REJECTED)

    custom_py = [s.rfilename for s in siblings if CUSTOM_CODE_FILES_RE.search(s.rfilename)]
    if custom_py:
        raise PrepareError(
            f"REFUSED: repo contains Python files (custom code): {custom_py[:3]}",
            EXIT_CUSTOM_CODE,
            STATUS_REJECTED,
        )

    total = sum((s.size or 0) for s in safetensors)
    if total <= 0:
        raise PrepareError("REFUSED: could not determine weight size.",
                           EXIT_POLICY, STATUS_REJECTED)
    if total > max_gb * 1e9:
        raise PrepareError(
            f"REFUSED: weights are {total / 1e9:.1f} GB; limit is {max_gb:g} GB.",
            EXIT_POLICY,
            STATUS_REJECTED,
        )

    license_value = None
    card = getattr(info, "card_data", None)
    if card is not None:
        license_value = getattr(card, "license", None)
    if not license_value:
        for tag in info.tags or []:
            if tag.startswith("license:"):
                license_value = tag.split(":", 1)[1]
                break
    if isinstance(license_value, (list, tuple)):
        license_value = license_value[0] if license_value else None
    license_norm = (license_value or "unknown").strip().lower()

    if check_license and license_norm not in allowed_licenses:
        raise PrepareError(
            f"REFUSED: license {license_norm!r} is not in the allow-list "
            f"({', '.join(sorted(allowed_licenses))}).",
            EXIT_POLICY,
            STATUS_REJECTED,
        )

    return info.sha, total, license_norm


def check_downloaded_config(model_dir: Path) -> None:
    """Second line of defence after download: reject auto_map and .py files."""
    config = model_dir / "config.json"
    if not config.is_file():
        raise PrepareError("FAILED: config.json was not downloaded.")

    for name in ("config.json", "tokenizer_config.json"):
        path = model_dir / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PrepareError(f"REFUSED: {name} is unreadable.",
                               EXIT_CUSTOM_CODE, STATUS_REJECTED) from exc
        if isinstance(data, dict) and "auto_map" in data:
            raise PrepareError(f"REFUSED: {name} contains auto_map (custom code).",
                               EXIT_CUSTOM_CODE, STATUS_REJECTED)

    for path in model_dir.rglob("*.py"):
        raise PrepareError(
            f"REFUSED: downloaded repo contains Python file {path.name}.",
            EXIT_CUSTOM_CODE,
            STATUS_REJECTED,
        )


def check_disk(total_weight_bytes: int) -> None:
    (ROOT / "models").mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ROOT / "models").free
    need = int(total_weight_bytes * DISK_FACTOR)
    if free < need:
        raise PrepareError(
            f"FAILED: not enough disk. Free {free / 1e9:.1f} GB, need about "
            f"{need / 1e9:.1f} GB."
        )


def converter_fingerprint() -> str:
    """Hash of the converter and quantizer binary, so a tool upgrade rebuilds the FP16."""
    digest = hashlib.sha256()
    for path in (CONVERT, QUANTIZE):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(str(path.stat().st_size).encode())
            digest.update(str(int(path.stat().st_mtime)).encode())
    return digest.hexdigest()[:16]


@contextlib.contextmanager
def model_lock(name: str):
    """One job per model at a time. Lock is released automatically by the OS on crash."""
    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(GGUF_DIR / f"{name}.lock", "w")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise PrepareError(
                    "Another job is already preparing this model.",
                    EXIT_BUSY,
                    report=False,
                ) from exc
        yield
    finally:
        handle.close()


def build_atomic(final: Path, builder: Callable[[Path], None]) -> None:
    """Build into '<name>.part' and rename only on success. Partial files never look finished."""
    part = final.with_name(final.name + ".part")
    part.unlink(missing_ok=True)
    try:
        builder(part)
        if not part.is_file() or part.stat().st_size == 0:
            raise PrepareError(f"FAILED: {final.name} is empty or missing after build.")
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


# ============================================================
# PIPELINE
# ============================================================

def prepare(
    model_id: str,
    hf_token: str | None,
    reporter: JobReporter,
    *,
    revision: str | None = None,
    allowed_licenses: set[str] | None = None,
    check_license: bool = True,
    max_gb: float = DEFAULT_MAX_GB,
    cleanup_hf: bool = False,
) -> dict[str, Any]:
    """
    Returns a dict with the artifact paths and metadata on success.
    Raises PrepareError on any controlled failure.
    """
    for required in (CONVERT, QUANTIZE):
        if not required.is_file():
            raise PrepareError(
                f"Tool not found: {required}. Check LLAMA_CPP_DIR or run the llama.cpp build."
            )

    model_id = validate_model_id(model_id)

    sha, total_bytes, license_norm = check_policy(
        model_id, revision, hf_token,
        allowed_licenses or set(), check_license, max_gb,
    )
    print(f"[0/3] OK: {model_id} @ {sha[:8]} | license={license_norm} | "
          f"weights={total_bytes / 1e9:.2f} GB", flush=True)
    check_disk(total_bytes)

    name = model_id.replace("/", "__")
    local = HF_DIR / name
    f16 = GGUF_DIR / f"{name}-f16.gguf"
    std = GGUF_DIR / f"{name}-Q4_K_M-standard.gguf"
    stamp = GGUF_DIR / f"{name}-f16.stamp"
    expected_stamp = f"{sha}\n{converter_fingerprint()}"

    HF_DIR.mkdir(parents=True, exist_ok=True)
    GGUF_DIR.mkdir(parents=True, exist_ok=True)

    with model_lock(name):
        print("[1/3] Download", flush=True)
        reporter.update(STATUS_RUNNING, 20, "Downloading model")
        snapshot_download(
            repo_id=model_id,
            revision=sha,
            local_dir=str(local),
            token=hf_token,
            allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.tiktoken"],
        )
        check_downloaded_config(local)

        print("[2/3] Convert to FP16 GGUF", flush=True)
        reporter.update(STATUS_RUNNING, 40, "Converting to FP16 GGUF")
        fresh = f16.is_file() and stamp.is_file() and stamp.read_text().strip() == expected_stamp
        if fresh:
            print("  FP16 already built for this commit and tool version, skipping.", flush=True)
        else:
            for stale in (f16, stamp, std):
                stale.unlink(missing_ok=True)
            build_atomic(f16, lambda out: run(
                [sys.executable, CONVERT, local, "--outfile", out, "--outtype", "f16"],
                timeout=CONVERT_TIMEOUT,
            ))
            stamp.write_text(expected_stamp)

        print("[3/3] Plain Q4_K_M (no imatrix) from the same FP16", flush=True)
        reporter.update(STATUS_RUNNING, 70, "Building plain Q4_K_M")
        if std.is_file():
            print("  Plain Q4 already exists, skipping.", flush=True)
        else:
            build_atomic(std, lambda out: run(
                [QUANTIZE, f16, out, "Q4_K_M"],
                timeout=QUANT_TIMEOUT,
            ))

    if cleanup_hf:
        shutil.rmtree(local, ignore_errors=True)

    result = {
        "model_id": model_id,
        "commit_sha": sha,
        "license": license_norm,
        "weights_gb": round(total_bytes / 1e9, 3),
        "fp16_path": str(f16),
        "fp16_gb": round(f16.stat().st_size / 1e9, 3),
        "standard_q4_path": str(std),
        "standard_q4_gb": round(std.stat().st_size / 1e9, 3),
    }
    print("\nReady:", json.dumps(result, indent=2), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain prepare step")
    parser.add_argument("--model", required=True)
    parser.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    parser.add_argument("--job-id", default=os.getenv("JOB_ID"),
                        help="quantization_jobs.id (uuid). Only this row is updated.")
    parser.add_argument("--revision", default=None, help="branch, tag, or commit (default main)")
    parser.add_argument("--allow-licenses",
                        default=os.getenv("ALLOW_LICENSES", DEFAULT_ALLOWED_LICENSES))
    parser.add_argument("--no-license-check", action="store_true",
                        help="internal runs only: skip the license allow-list")
    parser.add_argument("--max-gb", type=float, default=DEFAULT_MAX_GB)
    parser.add_argument("--cleanup-hf", action="store_true",
                        help="delete the HF snapshot after FP16 is built")
    args = parser.parse_args(argv)

    model_id = normalize_model_id(args.model) or (args.model or "").strip()
    reporter = JobReporter(args.job_id)
    allowed = {x.strip().lower() for x in args.allow_licenses.split(",") if x.strip()}

    try:
        prepare(
            model_id,
            args.hf_token,
            reporter,
            revision=args.revision,
            allowed_licenses=allowed,
            check_license=not args.no_license_check,
            max_gb=args.max_gb,
            cleanup_hf=args.cleanup_hf,
        )
        return EXIT_OK

    except PrepareError as exc:
        print(str(exc), file=sys.stderr)
        if exc.report:
            reporter.update(exc.status)
        return exc.code

    except KeyboardInterrupt:
        print("\nCancelled by user.", file=sys.stderr)
        reporter.update(STATUS_FAILED, step="Cancelled by user")
        return 130

    except Exception as exc:  # unexpected: never leave the DB spinning
        print(f"FAILED (unexpected): {type(exc).__name__}: {scrub(exc, limit=300)}",
              file=sys.stderr)
        reporter.update(STATUS_FAILED, step="Unexpected error")
        return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())