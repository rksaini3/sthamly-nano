#!/usr/bin/env python3
"""
Sthamly NanoBrain Worker (v2)
=============================

Commands:
    python scripts/worker.py --check
    python scripts/worker.py --once
    python scripts/worker.py
    python scripts/worker.py --once --job-id JOB_UUID

Required environment variables:
    NEXT_PUBLIC_SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    SUPABASE_STORAGE_BUCKET
    HINDI_TEST_DATASET          (held-out evaluation lines, never used for calibration)
    HINDI_CALIB_DATASET         (calibration text for the Hindi imatrix)

Optional environment variables:
    HF_COMMAND, LLAMA_CPP_CONVERTER, LLAMA_QUANTIZE, LLAMA_IMATRIX
    NANOBRAIN_RUNNING_STATUS    (status name used while a job runs; default "extracting")
    NANOBRAIN_KEEP_FILES        ("true" to keep local work files)

Required tools:
    Python, Hugging Face CLI (`hf`), llama.cpp: convert_hf_to_gguf.py,
    llama-quantize, llama-imatrix, scripts/audit.py

Pipeline per job:
    preflight -> download -> FP16 GGUF -> Hindi imatrix -> standard Q4_K_M
    -> Hindi-imatrix Q4_K_M -> audit -> upload -> save result

The worker never invents metrics. A job is marked completed only after
real artifacts, a valid audit report, and successful storage uploads.
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))

RUNNING_STATUS = os.getenv("NANOBRAIN_RUNNING_STATUS", "extracting")

PROGRESS = [5, 10, 25, 35, 50, 65, 80, 90, 97]

AUDIT_STEPS = [
    "Preflight checks",
    "Download Hugging Face model",
    "Convert model to FP16 GGUF",
    "Build Hindi imatrix",
    "Quantize standard Q4_K_M",
    "Quantize Hindi-imatrix Q4_K_M",
    "Run audit and validate report",
    "Upload artifacts",
    "Save results",
]


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class Config:
    root: Path = ROOT

    poll_seconds: int = 15
    request_timeout: int = 30
    upload_timeout: int = 3600
    heartbeat_seconds: int = 60
    stale_minutes: int = 45

    min_free_gb: float = 15.0
    max_weights_gb: float = 4.2
    threads: int = max(1, min(os.cpu_count() or 2, 8))

    prepare_timeout: int = 3600
    audit_timeout: int = 10800

    keep_files: bool = (
        os.getenv("NANOBRAIN_KEEP_FILES", "false").lower() == "true"
    )

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def test_dataset(self) -> Path:
        return Path(
            os.getenv("HINDI_TEST_DATASET", str(self.root / "data" / "hindi-test.txt"))
        )

    @property
    def calib_dataset(self) -> Path:
        return Path(
            os.getenv("HINDI_CALIB_DATASET", str(self.root / "data" / "hindi-calib.txt"))
        )

    @property
    def supabase_url(self) -> str:
        return os.getenv("NEXT_PUBLIC_SUPABASE_URL", "").strip().rstrip("/")

    @property
    def supabase_key(self) -> str:
        return os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    @property
    def storage_bucket(self) -> str:
        return os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()

    @property
    def hf_command(self) -> str:
        return os.getenv("HF_COMMAND", "hf")

    @property
    def converter(self) -> Path:
        return Path(
            os.getenv(
                "LLAMA_CPP_CONVERTER",
                str(self.root / "llama.cpp" / "convert_hf_to_gguf.py"),
            )
        )

    @property
    def quantize_command(self) -> str:
        return os.getenv("LLAMA_QUANTIZE", "llama-quantize")

    @property
    def imatrix_command(self) -> str:
        return os.getenv("LLAMA_IMATRIX", "llama-imatrix")

    @property
    def audit_script(self) -> Path:
        return self.root / "scripts" / "audit.py"


CFG = Config()


# ============================================================
# ERRORS
# ============================================================

class WorkerError(Exception):
    """Expected processing or configuration failure."""


class SupabaseError(WorkerError):
    """Database or storage request failed."""


class Refused(WorkerError):
    """Job rejected because its input or environment is unsupported."""


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: Any, limit: int = 500) -> str:
    """Single-line, length-limited text for DB fields and short messages."""
    text = re.sub(r"[\r\n\t]+", " ", str(value or ""))
    return text[:limit]


def tail(value: Any, limit: int = 600) -> str:
    """Last N characters. Errors usually appear at the END of tool output."""
    return str(value or "")[-limit:]


def safe_model_name(model_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", model_id.strip()).strip("._-")
    if not cleaned:
        raise Refused("Invalid model identifier.")
    return cleaned[:120]


def find_tool(name: str) -> str | None:
    path = Path(name)
    if path.is_file():
        return str(path.resolve())
    return shutil.which(name)


def require_file(path: Path, description: str) -> Path:
    if not path.is_file():
        raise WorkerError(f"{description} not found: {path}")
    if path.stat().st_size <= 0:
        raise WorkerError(f"{description} is empty: {path}")
    return path


def file_size_gb(path: Path) -> float:
    return round(path.stat().st_size / (1024 ** 3), 4)


def free_gb(path: Path) -> float:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return round(shutil.disk_usage(p).free / (1024 ** 3), 2)


def run_command(command: list[Any], timeout: int, cwd: Path | None = None) -> str:
    """
    Run a real command, stream its output live, and return the output.
    Raises WorkerError on timeout or non-zero exit. Never hides a failure.
    """
    cmd = [str(part) for part in command]
    print("\n$ " + " ".join(cmd), flush=True)

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise WorkerError(f"Could not execute command: {exc}") from exc

    killed = threading.Event()

    def _kill() -> None:
        killed.set()
        proc.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()

    recent: deque[str] = deque(maxlen=200)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line, flush=True)
            recent.append(line)
        proc.wait()
    except BaseException:
        # Ctrl+C or unexpected error: do not leave an orphaned llama.cpp process.
        proc.kill()
        raise
    finally:
        timer.cancel()

    output = "\n".join(recent)

    if killed.is_set():
        raise WorkerError(
            f"Command timed out after {timeout} seconds. Last output: {tail(output)}"
        )

    if proc.returncode != 0:
        raise WorkerError(
            f"Command failed with exit code {proc.returncode}: {tail(output)}"
        )

    return output


# ============================================================
# SUPABASE REST + STORAGE CLIENT
# ============================================================

class Supabase:
    """
    Minimal PostgREST + Storage client.
    Uses the service-role key on the worker machine only.
    Never expose this key to browser/client-side code.
    """

    def __init__(self) -> None:
        self.url = CFG.supabase_url
        self.key = CFG.supabase_key

    def check_config(self) -> None:
        if not self.url:
            raise SupabaseError("NEXT_PUBLIC_SUPABASE_URL is missing.")
        if not self.url.startswith("https://"):
            raise SupabaseError("Supabase URL must use HTTPS.")
        if not self.key:
            raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY is missing.")

    def _json_request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        prefer: str | None = None,
    ) -> bytes:
        self.check_config()

        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        body = json.dumps(payload).encode("utf-8") if payload is not None else None

        req = urllib.request.Request(
            self.url + path, data=body, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=CFG.request_timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SupabaseError(
                f"Supabase HTTP {exc.code}: {safe_text(detail, 500)}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SupabaseError(
                f"Supabase connection failed: {safe_text(exc, 300)}"
            ) from exc

    def _json_list(self, raw: bytes) -> list[dict[str, Any]]:
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SupabaseError("Supabase returned invalid JSON.") from exc
        if not isinstance(result, list):
            raise SupabaseError("Expected a JSON array from Supabase.")
        return result

    def get_rows(self, table: str, query: dict[str, str]) -> list[dict[str, Any]]:
        qs = urllib.parse.urlencode(query)
        raw = self._json_request("GET", f"/rest/v1/{table}?{qs}")
        return self._json_list(raw)

    def patch_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        only_if_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Conditional PATCH. Returned rows show whether the update matched.
        Empty list means the job was not in the expected status.
        """
        query: dict[str, str] = {"id": f"eq.{job_id}", "select": "id,status"}
        if only_if_status:
            query["status"] = f"eq.{only_if_status}"

        qs = urllib.parse.urlencode(query)
        raw = self._json_request(
            "PATCH",
            f"/rest/v1/quantization_jobs?{qs}",
            payload=fields,
            prefer="return=representation",
        )
        return self._json_list(raw)

    def fetch_pending_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.get_rows(
            "quantization_jobs",
            {
                "select": "*",
                "status": "eq.pending",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )

    def claim_job(self, job: dict[str, Any]) -> dict[str, Any] | None:
        """
        Claim only if still pending. Two workers cannot both win this PATCH.
        For very high volume, a PostgreSQL RPC with FOR UPDATE SKIP LOCKED
        is the stronger option.
        """
        job_id = str(job.get("id", "")).strip()
        if not job_id:
            raise WorkerError("Pending job has no ID.")

        rows = self.patch_job(
            job_id,
            {
                "status": RUNNING_STATUS,
                "step": "Claiming job",
                "heartbeat_at": now_iso(),
                "progress": 1,
            },
            only_if_status="pending",
        )
        if not rows:
            return None

        claimed = dict(job)
        claimed["status"] = RUNNING_STATUS
        return claimed

    def upload_file(self, local_path: Path, object_path: str, content_type: str) -> str:
        """
        Streaming upload. The file is read in chunks, never fully loaded into RAM.

        Note: Supabase has a default upload size limit (lower on free plans).
        Check your project's limit before uploading multi-GB files. For larger
        files, switch to the TUS resumable upload endpoint.
        """
        if not CFG.storage_bucket:
            raise SupabaseError("SUPABASE_STORAGE_BUCKET is missing.")

        self.check_config()
        require_file(local_path, "Upload artifact")

        bucket = urllib.parse.quote(CFG.storage_bucket, safe="")
        obj = "/".join(
            urllib.parse.quote(seg, safe="")
            for seg in object_path.lstrip("/").split("/")
        )
        url = f"{self.url}/storage/v1/object/{bucket}/{obj}"

        with local_path.open("rb") as fh:
            req = urllib.request.Request(
                url,
                data=fh,
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": content_type,
                    "Content-Length": str(local_path.stat().st_size),
                    "x-upsert": "true",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=CFG.upload_timeout) as response:
                    response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise SupabaseError(
                    f"Storage upload failed (HTTP {exc.code}): {safe_text(detail, 300)}"
                ) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise SupabaseError(
                    f"Storage upload failed: {safe_text(exc, 300)}"
                ) from exc

        return object_path


# ============================================================
# HEARTBEAT + STALE JOB RECOVERY
# ============================================================

class Heartbeat(threading.Thread):
    """
    Updates heartbeat_at in the background while a long step (download,
    conversion, audit) runs. Without this, a healthy job looks stale.
    """

    def __init__(self, db: Supabase, job_id: str, every: int) -> None:
        super().__init__(daemon=True)
        self.db = db
        self.job_id = job_id
        self.every = every
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.every):
            try:
                self.db.patch_job(self.job_id, {"heartbeat_at": now_iso()})
            except SupabaseError as exc:
                print(f"WARNING: heartbeat failed: {safe_text(exc, 200)}", flush=True)


def reap_stale_jobs(db: Supabase) -> None:
    """
    Mark jobs as failed if their worker died (heartbeat too old).
    Only touches jobs still in the running status.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=CFG.stale_minutes)
    ).isoformat()

    stale = db.get_rows(
        "quantization_jobs",
        {
            "select": "id",
            "status": f"eq.{RUNNING_STATUS}",
            "heartbeat_at": f"lt.{cutoff}",
        },
    )

    for row in stale:
        job_id = str(row.get("id", ""))
        if not job_id:
            continue
        db.patch_job(
            job_id,
            {
                "status": "failed",
                "step": "Failed",
                "error_note": "Worker heartbeat timed out; job reclaimed as failed.",
                "heartbeat_at": now_iso(),
            },
            only_if_status=RUNNING_STATUS,
        )
        print(f"Reaped stale job {job_id}", flush=True)


# ============================================================
# JOB STATE / TELEMETRY
# ============================================================

class JobState:
    def __init__(self, db: Supabase, job_id: str) -> None:
        self.db = db
        self.job_id = job_id
        self.steps = [{"label": label, "state": "pending"} for label in AUDIT_STEPS]
        self.progress = 1

    def _push(self, extra: dict[str, Any] | None = None) -> None:
        fields: dict[str, Any] = {
            "steps": self.steps,
            "progress": self.progress,
            "heartbeat_at": now_iso(),
        }
        if extra:
            fields.update(extra)

        try:
            self.db.patch_job(self.job_id, fields, only_if_status=RUNNING_STATUS)
        except SupabaseError as exc:
            # Telemetry failure must not fake success. The final update is checked.
            print(f"WARNING: telemetry update failed: {safe_text(exc, 200)}", flush=True)

    def start(self, index: int) -> None:
        for i in range(index):
            self.steps[i]["state"] = "done"
        self.steps[index]["state"] = "running"
        self.progress = PROGRESS[index]

        label = self.steps[index]["label"]
        print(f"\n[{index + 1}/{len(self.steps)}] {label}", flush=True)
        self._push({"step": label})

    def done(self, index: int) -> None:
        self.steps[index]["state"] = "done"
        self._push()

    def complete(self, report: dict[str, Any], artifacts: dict[str, str]) -> None:
        for item in self.steps:
            item["state"] = "done"

        report["artifacts"] = artifacts

        rows = self.db.patch_job(
            self.job_id,
            {
                "status": "completed",
                "steps": self.steps,
                "progress": 100,
                "step": "Completed",
                "heartbeat_at": now_iso(),
                "result": report,
            },
            only_if_status=RUNNING_STATUS,
        )
        if not rows:
            raise SupabaseError(
                "Final status update matched no running job. Check whether "
                "another process changed the status, or whether the schema "
                "uses different status names (NANOBRAIN_RUNNING_STATUS)."
            )

        self.progress = 100
        print("\nSUCCESS: Real processing and final DB update completed.", flush=True)

    def fail(self, error: BaseException) -> None:
        for item in self.steps:
            if item["state"] == "running":
                item["state"] = "failed"

        message = safe_text(f"{type(error).__name__}: {error}", 400)

        try:
            rows = self.db.patch_job(
                self.job_id,
                {
                    "status": "failed",
                    "steps": self.steps,
                    "step": "Failed",
                    "error_note": message,
                    "heartbeat_at": now_iso(),
                },
                only_if_status=RUNNING_STATUS,
            )
            if not rows:
                print("WARNING: job was not running; failure not recorded.", flush=True)
        except Exception as db_error:
            print(
                f"CRITICAL: could not record job failure: {safe_text(db_error, 300)}",
                flush=True,
            )

        print(f"\nJOB FAILED: {message}", flush=True)


# ============================================================
# CHECKS (used by --check and by per-job preflight)
# ============================================================

def _check(ok: bool, label: str) -> tuple[bool, str]:
    return ok, label


def collect_checks(db: Supabase) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    try:
        db.check_config()
        results.append((True, "Supabase URL and service-role key configured"))
        db.get_rows("quantization_jobs", {"select": "id", "limit": "1"})
        results.append((True, "Supabase reachable and quantization_jobs readable"))
    except SupabaseError as exc:
        results.append((False, str(exc)))

    results.append(_check(bool(find_tool(CFG.hf_command)), f"Hugging Face CLI: {CFG.hf_command}"))
    results.append(_check(CFG.converter.is_file(), f"GGUF converter: {CFG.converter}"))
    results.append(_check(bool(find_tool(CFG.quantize_command)), f"llama-quantize: {CFG.quantize_command}"))
    results.append(_check(bool(find_tool(CFG.imatrix_command)), f"llama-imatrix: {CFG.imatrix_command}"))
    results.append(_check(CFG.audit_script.is_file(), f"Audit script: {CFG.audit_script}"))
    results.append(_check(
        CFG.test_dataset.is_file() and CFG.test_dataset.stat().st_size > 0,
        f"Hindi test dataset: {CFG.test_dataset}",
    ))
    results.append(_check(
        CFG.calib_dataset.is_file() and CFG.calib_dataset.stat().st_size > 0,
        f"Hindi calibration dataset: {CFG.calib_dataset}",
    ))
    results.append(_check(
        CFG.calib_dataset.resolve() != CFG.test_dataset.resolve(),
        "Calibration and test datasets are different files (no leakage)",
    ))
    results.append(_check(bool(CFG.storage_bucket), f"Storage bucket: {CFG.storage_bucket or 'MISSING'}"))

    free = free_gb(CFG.work_dir)
    results.append(_check(
        free >= CFG.min_free_gb,
        f"Free disk {free} GB (required >= {CFG.min_free_gb} GB)",
    ))

    return results


def check_environment(db: Supabase) -> int:
    print("\n=== STHAMLY NANOBRAIN WORKER CHECK ===\n")
    results = collect_checks(db)
    failures = 0
    for ok, label in results:
        print(("OK:   " if ok else "FAIL: ") + label)
        if not ok:
            failures += 1

    print(f"\nPython: {sys.version.split()[0]}")
    print(f"Project root: {CFG.root}")
    print(f"Threads: {CFG.threads}")

    if failures:
        print(f"\nCHECK FAILED: {failures} issue(s) found.")
        return 1

    print("\nCHECK PASSED.")
    print(
        "Note: this does not prove that a full conversion, audit, or upload "
        "will succeed on this machine's hardware."
    )
    return 0


def preflight(db: Supabase) -> None:
    """Run before any download. Fail fast instead of after 3 GB of work."""
    failed = [label for ok, label in collect_checks(db) if not ok]
    if failed:
        raise Refused("Preflight failed: " + "; ".join(safe_text(f, 200) for f in failed))


# ============================================================
# MODEL VALIDATION AND PROCESSING
# ============================================================

def validate_model_id(job: dict[str, Any]) -> str:
    model_id = str(
        job.get("hf_model_identifier") or job.get("hf_model") or ""
    ).strip()

    if not model_id:
        raise Refused("Job is missing hf_model_identifier.")

    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*",
        model_id,
    ):
        raise Refused("Model ID must use namespace/model format.")

    return model_id


def validate_job_id(job_id: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", job_id):
        raise Refused("Job ID must be a UUID.")
    return job_id


def download_model(model_id: str, destination: Path) -> Path:
    hf = find_tool(CFG.hf_command)
    if not hf:
        raise WorkerError("Hugging Face CLI is not installed.")

    destination.mkdir(parents=True, exist_ok=True)

    # List-form command: no shell, so model_id cannot inject commands.
    run_command(
        [hf, "download", model_id, "--local-dir", destination],
        timeout=CFG.prepare_timeout,
    )

    if not any(destination.iterdir()):
        raise WorkerError("Hugging Face download directory is empty.")

    return destination


def convert_to_fp16(model_dir: Path, fp16_path: Path) -> Path:
    if not CFG.converter.is_file():
        raise WorkerError(f"Converter script not found: {CFG.converter}")

    fp16_path.parent.mkdir(parents=True, exist_ok=True)

    run_command(
        [
            sys.executable, CFG.converter, model_dir,
            "--outfile", fp16_path,
            "--outtype", "f16",
        ],
        timeout=CFG.prepare_timeout,
    )

    require_file(fp16_path, "FP16 GGUF artifact")

    size_gb = file_size_gb(fp16_path)
    if size_gb > CFG.max_weights_gb:
        raise Refused(
            f"FP16 GGUF is {size_gb} GB; configured maximum is "
            f"{CFG.max_weights_gb} GB. Raise the limit only after checking disk."
        )

    return fp16_path


def build_hindi_imatrix(fp16_path: Path, calib_path: Path, out_path: Path) -> Path:
    """
    The core of the NanoBrain bet: an importance matrix computed from Hindi
    calibration text. Without this step, the "Hindi-calibrated" quant
    does not exist.
    """
    imatrix = find_tool(CFG.imatrix_command)
    if not imatrix:
        raise WorkerError("llama-imatrix binary was not found.")

    require_file(calib_path, "Hindi calibration dataset")

    run_command(
        [
            imatrix,
            "-m", fp16_path,
            "-f", calib_path,
            "-o", out_path,
            "-t", CFG.threads,
        ],
        timeout=CFG.prepare_timeout,
    )

    require_file(out_path, "Hindi imatrix")
    return out_path


def quantize_model(
    fp16_path: Path,
    quant_path: Path,
    imatrix_path: Path | None = None,
) -> Path:
    quantizer = find_tool(CFG.quantize_command)
    if not quantizer:
        raise WorkerError("llama-quantize binary was not found.")

    quant_path.parent.mkdir(parents=True, exist_ok=True)

    command: list[Any] = [quantizer]
    if imatrix_path is not None:
        command += ["--imatrix", imatrix_path]
    command += [fp16_path, quant_path, "Q4_K_M"]

    run_command(command, timeout=CFG.prepare_timeout)

    require_file(quant_path, "Q4_K_M GGUF artifact")
    return quant_path


def validate_audit_report(report: dict[str, Any]) -> None:
    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise WorkerError("Audit report: results missing or empty.")

    for item in results:
        if not isinstance(item, dict):
            raise WorkerError("Audit report: malformed result row.")

        kld = item.get("kld")
        if (
            isinstance(kld, bool)
            or not isinstance(kld, (int, float))
            or not math.isfinite(kld)
            or kld < 0
        ):
            raise WorkerError(f"Audit report: invalid KLD value {kld!r}.")


def run_audit(
    model_id: str,
    fp16_path: Path,
    std_quant: Path,
    hindi_quant: Path,
    report_dir: Path,
) -> Path:
    """
    Calls scripts/audit.py. The audit must accept the labels below and write
    report.json to --out. No metrics are generated by this worker.
    """
    require_file(CFG.audit_script, "Audit script")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.json"

    run_command(
        [
            sys.executable, CFG.audit_script,
            "--name", model_id,
            "--fp16", fp16_path,
            "--dataset", f"hindi={CFG.test_dataset}",
            "--quant", f"q4_standard={std_quant}",
            "--quant", f"q4_hindi={hindi_quant}",
            "--out", report_path,
        ],
        timeout=CFG.audit_timeout,
        cwd=CFG.root,
    )

    if not report_path.is_file():
        raise WorkerError(f"Audit finished but report.json is missing: {report_path}")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError("Audit report is unreadable or invalid JSON.") from exc

    if not isinstance(report, dict):
        raise WorkerError("Audit report must be a JSON object.")

    validate_audit_report(report)
    return report_path


# ============================================================
# FULL JOB PIPELINE
# ============================================================

def process_job(job: dict[str, Any], db: Supabase) -> None:
    job_id = validate_job_id(str(job.get("id", "")).strip())

    state = JobState(db, job_id)
    job_dir = CFG.work_dir / job_id
    hb = Heartbeat(db, job_id, CFG.heartbeat_seconds)
    hb.start()

    try:
        # ---- 0. Preflight -------------------------------------------------
        state.start(0)
        model_id = validate_model_id(job)
        preflight(db)
        slug = safe_model_name(model_id)

        model_dir = job_dir / "hf-model"
        art_dir = job_dir / "artifacts"
        report_dir = job_dir / "report"
        art_dir.mkdir(parents=True, exist_ok=True)
        state.done(0)

        # ---- 1. Download -------------------------------------------------
        state.start(1)
        download_model(model_id, model_dir)
        state.done(1)

        # ---- 2. FP16 GGUF -------------------------------------------------
        state.start(2)
        fp16_path = art_dir / f"{slug}-f16.gguf"
        convert_to_fp16(model_dir, fp16_path)
        # HF source weights are no longer needed; free the disk now.
        shutil.rmtree(model_dir, ignore_errors=True)
        state.done(2)

        # ---- 3. Hindi imatrix ----------------------------------------------
        state.start(3)
        imatrix_path = build_hindi_imatrix(
            fp16_path,
            CFG.calib_dataset,
            art_dir / f"{slug}-hindi.imatrix",
        )
        state.done(3)

        # ---- 4. Standard Q4_K_M (baseline, no imatrix) ---------------------
        state.start(4)
        std_path = quantize_model(
            fp16_path,
            art_dir / f"{slug}-Q4_K_M-standard.gguf",
        )
        state.done(4)

        # ---- 5. Hindi-imatrix Q4_K_M ---------------------------------------
        state.start(5)
        hindi_path = quantize_model(
            fp16_path,
            art_dir / f"{slug}-Q4_K_M-hindi.gguf",
            imatrix_path=imatrix_path,
        )
        state.done(5)

        # ---- 6. Audit ------------------------------------------------------
        state.start(6)
        report_path = run_audit(model_id, fp16_path, std_path, hindi_path, report_dir)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        state.done(6)

        # ---- 7. Upload -----------------------------------------------------
        state.start(7)
        prefix = f"jobs/{job_id}/{slug}"
        objects = {
            "fp16_object": (fp16_path, f"{prefix}/{fp16_path.name}", "application/octet-stream"),
            "quantized_standard_object": (std_path, f"{prefix}/{std_path.name}", "application/octet-stream"),
            "quantized_hindi_object": (hindi_path, f"{prefix}/{hindi_path.name}", "application/octet-stream"),
            "imatrix_object": (imatrix_path, f"{prefix}/{imatrix_path.name}", "application/octet-stream"),
            "report_object": (report_path, f"{prefix}/report.json", "application/json"),
        }

        artifact_paths: dict[str, str] = {"storage_bucket": CFG.storage_bucket}
        for key, (local, obj, ctype) in objects.items():
            db.upload_file(local, obj, ctype)
            artifact_paths[key] = obj
        state.done(7)

        # ---- 8. Save results ----------------------------------------------
        state.start(8)
        report["model_id"] = model_id
        report["job_id"] = job_id
        report["generated_at"] = now_iso()
        report["quantization"] = {
            "standard": "Q4_K_M without imatrix",
            "hindi": "Q4_K_M with Hindi imatrix",
            "calibration_file": CFG.calib_dataset.name,
            "test_file": CFG.test_dataset.name,
        }
        report["artifacts_metadata"] = {
            "fp16_size_gb": file_size_gb(fp16_path),
            "quantized_standard_size_gb": file_size_gb(std_path),
            "quantized_hindi_size_gb": file_size_gb(hindi_path),
        }

        state.complete(report, artifact_paths)

    except BaseException as exc:
        state.fail(exc)
        raise

    finally:
        hb.stop_event.set()
        if not CFG.keep_files:
            shutil.rmtree(job_dir, ignore_errors=True)


# ============================================================
# WORKER LOOP
# ============================================================

def run_once(db: Supabase, requested_job_id: str | None = None) -> str:
    """
    Process at most one job.
    Returns "completed", "failed", or "idle".
    """
    if requested_job_id:
        rows = db.get_rows(
            "quantization_jobs",
            {
                "select": "*",
                "id": f"eq.{validate_job_id(requested_job_id)}",
                "status": "eq.pending",
                "limit": "1",
            },
        )
    else:
        rows = db.fetch_pending_jobs(limit=10)

    if not rows:
        print("No pending job found.", flush=True)
        return "idle"

    for candidate in rows:
        claimed = db.claim_job(candidate)
        if claimed is None:
            print(
                f"Job {candidate.get('id')} was claimed by another worker; trying next.",
                flush=True,
            )
            continue

        try:
            process_job(claimed, db)
            return "completed"
        except Exception:
            # process_job already recorded the failure and printed it.
            return "failed"

    print("No job could be claimed.", flush=True)
    return "idle"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain Worker")
    parser.add_argument("--check", action="store_true", help="Check configuration and tools.")
    parser.add_argument("--once", action="store_true", help="Attempt at most one job and exit.")
    parser.add_argument("--job-id", default=None, help="Process a specific pending job UUID (requires --once).")
    parser.add_argument(
        "--poll-seconds", type=int, default=CFG.poll_seconds,
        help="Queue polling interval in seconds (3-300).",
    )
    args = parser.parse_args()

    db = Supabase()

    if args.check:
        return check_environment(db)

    try:
        db.check_config()
    except SupabaseError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.job_id and not args.once:
        parser.error("--job-id requires --once.")

    if args.once or args.job_id:
        try:
            reap_stale_jobs(db)
            outcome = run_once(db, requested_job_id=args.job_id)
        except Exception as exc:
            print(f"Worker error: {safe_text(exc, 400)}", file=sys.stderr)
            return 1
        return 1 if outcome == "failed" else 0

    poll_seconds = max(3, min(300, args.poll_seconds))

    print(
        "STHAMLY NANOBRAIN WORKER STARTED\n"
        f"Project: {CFG.root}\n"
        f"Queue polling: every {poll_seconds} seconds\n"
        "Press Ctrl+C to stop.",
        flush=True,
    )

    while True:
        try:
            reap_stale_jobs(db)
            run_once(db)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0
        except Exception as exc:
            print(f"Queue polling error: {safe_text(exc, 400)}", file=sys.stderr, flush=True)

        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())