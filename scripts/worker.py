#!/usr/bin/env python3
"""
Sthamly NanoBrain Worker

Processes queued calibration jobs (quantization_jobs rows with status 'pending').

Commands:
    python scripts/worker.py --check
    python scripts/worker.py --once
    python scripts/worker.py --once --job-id JOB_UUID
    python scripts/worker.py                         # poll forever

Pipeline per job:
    0  Preflight        tools, datasets (calibration != test), storage bucket, disk
    1  Download         prepare_model.prepare(): commit-pinned download, license/size/custom-code policy
    2  Convert          FP16 GGUF (prepare_model)
    3  Hindi imatrix    llama-imatrix on the Hindi calibration file
    4  Plain Q4_K_M     from the same FP16 (prepare_model)
    5  Hindi Q4_K_M     from the same FP16 + imatrix
    6  Audit            scripts/audit.py -> reports/<slug>/report.json (status must be 'complete')
    7  Upload           Supabase Storage: quants, imatrix, report (FP16 only if UPLOAD_FP16=true)
    8  Save             job row -> completed, result built from the validated report

Environment:
    NEXT_PUBLIC_SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_STORAGE_BUCKET   (required)
    HF_TOKEN                                  (gated or private models)
    ALLOW_LICENSES, MAX_MODEL_GB              (policy, read by prepare_model)
    HINDI_CALIB_DATASET, HINDI_TEST_DATASET   (defaults: data/hindi_calib.txt, data/hindi_test.txt)
    LLAMA_CPP_DIR, LLAMA_CPP_CONVERTER, LLAMA_QUANTIZE, LLAMA_IMATRIX
    IMATRIX_CHUNKS, IMATRIX_THREADS, IMATRIX_TIMEOUT, QUANT_TIMEOUT, AUDIT_TIMEOUT
    WORKER_MIN_FREE_GB                        (default 15)
    UPLOAD_FP16                               ("true" to upload the FP16 GGUF too)

Rules:
    - Never invents a metric. Numbers in the job row come from reports/<slug>/report.json.
    - Completed only after a complete audit report, all uploads, and a successful final update.
    - Artifacts stay in models/gguf so a re-run reuses them. Only the HF snapshot is cleaned.
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
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import prepare_model as pm  # noqa: E402  (download, FP16, plain Q4, policy, locks)
from audit import report_directory_name  # noqa: E402
from sthamly_common import (  # noqa: E402
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STEP_LABELS,
    TABLE_JOBS,
    UPLOAD_TIMEOUT,
    Supabase,
    SupabaseError,
    normalize_model_id,
    scrub,
    validate_job_id,
)


# ============================================================
# CONFIGURATION
# ============================================================

HINDI_CALIB = Path(os.getenv("HINDI_CALIB_DATASET", str(ROOT / "data" / "hindi_calib.txt")))
HINDI_TEST = Path(os.getenv("HINDI_TEST_DATASET", str(ROOT / "data" / "hindi_test.txt")))
IMATRIX_BIN = Path(os.getenv("LLAMA_IMATRIX", str(pm.LLAMA_DIR / "build" / "bin" / "llama-imatrix")))
AUDIT_SCRIPT = HERE / "audit.py"

IMATRIX_CHUNKS = int(os.getenv("IMATRIX_CHUNKS", "60"))
IMATRIX_THREADS = int(os.getenv("IMATRIX_THREADS", str(max(1, min(os.cpu_count() or 2, 8)))))
IMATRIX_TIMEOUT = int(os.getenv("IMATRIX_TIMEOUT", "7200"))
QUANT_TIMEOUT = int(os.getenv("QUANT_TIMEOUT", "3600"))
AUDIT_TIMEOUT = int(os.getenv("AUDIT_TIMEOUT", "10800"))

MIN_FREE_GB = float(os.getenv("WORKER_MIN_FREE_GB", "15"))
UPLOAD_FP16 = os.getenv("UPLOAD_FP16", "false").strip().lower() == "true"
POLL_SECONDS = 15
HEARTBEAT_SECONDS = 60
STALE_MINUTES = 45

# Indexes into STEP_LABELS (sthamly_common). Keep in the same order.
STEP_PREFLIGHT = 0
STEP_DOWNLOAD = 1
STEP_CONVERT = 2
STEP_IMATRIX = 3
STEP_PLAIN_Q4 = 4
STEP_HINDI_Q4 = 5
STEP_AUDIT = 6
STEP_UPLOAD = 7
STEP_SAVE = 8

STEP_PROGRESS = {0: 5, 1: 10, 2: 25, 3: 72, 4: 70, 5: 80, 6: 88, 7: 94, 8: 98}


# ============================================================
# ERRORS AND SMALL HELPERS
# ============================================================

class JobError(Exception):
    """A pipeline step failed. The message is scrubbed and stored in error_note."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_size_gb(path: Path) -> float:
    return round(path.stat().st_size / 1e9, 4)


def is_fresh(target: Path, *sources: Path) -> bool:
    """True if target exists, is non-empty, and is not older than any of its inputs."""
    if not target.is_file() or target.stat().st_size == 0:
        return False
    return all(target.stat().st_mtime >= src.stat().st_mtime for src in sources)


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def parse_model_id(job: dict[str, Any]) -> str:
    raw = str(job.get("hf_model_identifier") or "").strip()
    if not raw:
        raise JobError("Job has no hf_model_identifier.")
    # URL-level check first: normalize_model_id would turn huggingface.co/datasets/a/b into datasets/a.
    if re.search(r"huggingface\.co/(datasets|spaces)/", raw, re.IGNORECASE):
        raise JobError("Only model repos are accepted; dataset and space URLs are refused.")
    model_id = normalize_model_id(raw)
    if model_id is None:
        raise JobError("Invalid model ID. Use owner/name or a huggingface.co model URL.")
    return model_id


def config_problems() -> list[str]:
    """Every missing tool or input, as plain text. An empty list means ready."""
    problems: list[str] = []

    if not pm.CONVERT.is_file():
        problems.append(f"GGUF converter not found: {pm.CONVERT}")
    if not pm.QUANTIZE.is_file():
        problems.append(f"llama-quantize not found: {pm.QUANTIZE}")
    if not IMATRIX_BIN.is_file():
        problems.append(f"llama-imatrix not found: {IMATRIX_BIN}")
    if not AUDIT_SCRIPT.is_file():
        problems.append(f"audit.py not found: {AUDIT_SCRIPT}")

    for label, path in (("calibration", HINDI_CALIB), ("test", HINDI_TEST)):
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"Hindi {label} file missing or empty: {path}")

    if not os.getenv("SUPABASE_STORAGE_BUCKET", "").strip():
        problems.append("SUPABASE_STORAGE_BUCKET is not set")

    # Same text in calibration and test would make the Hindi comparison meaningless.
    if not problems and file_sha256(HINDI_CALIB) == file_sha256(HINDI_TEST):
        problems.append("Calibration and test files are identical (leakage).")

    return problems


def preflight() -> None:
    problems = config_problems()
    free = free_gb(pm.GGUF_DIR)
    if free < MIN_FREE_GB:
        problems.append(f"free disk {free:.1f} GB; need at least {MIN_FREE_GB:g} GB")
    if problems:
        raise JobError("Preflight failed: " + "; ".join(problems))


# ============================================================
# SUBPROCESS WITH TIMEOUT
# ============================================================

def run(command: list[Any], timeout: int, label: str) -> None:
    """Run a real command with live output and a hard timeout. Raises JobError on any failure."""
    cmd = [str(part) for part in command]
    print(f"\n$ {' '.join(cmd)}", flush=True)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        raise JobError(f"{label}: could not start ({exc}).") from exc

    recent: deque[str] = deque(maxlen=60)
    timed_out = threading.Event()

    def kill_process() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill_process)
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line, flush=True)
            recent.append(line)
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    if timed_out.is_set():
        raise JobError(f"{label}: timed out after {timeout} seconds.")
    if proc.returncode != 0:
        last = " | ".join(recent)[-400:]
        raise JobError(f"{label}: exit code {proc.returncode}. Last output: {last}")


# ============================================================
# SUPABASE: STORAGE UPLOAD
# ============================================================

def upload(db: Supabase, local: Path, obj: str, content_type: str) -> None:
    """Streams one file to Supabase Storage. The file is never fully loaded into RAM."""
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
    if not bucket:
        raise JobError("SUPABASE_STORAGE_BUCKET is not set.")
    if not local.is_file() or local.stat().st_size == 0:
        raise JobError(f"Upload source missing or empty: {local.name}")

    bucket_q = urllib.parse.quote(bucket, safe="")
    object_q = "/".join(urllib.parse.quote(part, safe="") for part in obj.split("/"))
    url = f"{db.url}/storage/v1/object/{bucket_q}/{object_q}"

    with local.open("rb") as handle:
        req = urllib.request.Request(
            url,
            data=handle,
            headers={
                "apikey": db.key,
                "Authorization": f"Bearer {db.key}",
                "Content-Type": content_type,
                "Content-Length": str(local.stat().st_size),
                "x-upsert": "true",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=UPLOAD_TIMEOUT) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise JobError(
                f"Upload of {local.name} failed: HTTP {exc.code} "
                f"{scrub(detail, extra_secrets=(db.key,), limit=200)}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JobError(
                f"Upload of {local.name} failed: {scrub(exc, extra_secrets=(db.key,), limit=200)}"
            ) from exc


# ============================================================
# AUDIT REPORT VALIDATION
# ============================================================

def validate_audit_report(report: Any) -> dict[str, dict[str, Any]]:
    """
    Accepts only a complete, schema-2 audit report with finite KLD numbers for both
    variants on the 'hindi' test set. Returns {variant: result_item}.
    """
    if not isinstance(report, dict):
        raise JobError("Audit report is not a JSON object.")
    if report.get("schema_version") != 2:
        raise JobError(f"Audit report schema_version {report.get('schema_version')!r} is not 2.")
    if report.get("status") != "complete":
        raise JobError(f"Audit report status is {report.get('status')!r}; only 'complete' is accepted.")

    results = report.get("results")
    if not isinstance(results, list) or not results:
        raise JobError("Audit report has no results.")

    picked: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            raise JobError("Audit report has a malformed result row.")
        if item.get("domain") != "hindi":
            continue
        variant = item.get("variant")
        if variant not in ("q4_standard", "q4_hindi"):
            continue
        for key in ("kld", "kld_error"):
            value = item.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise JobError(f"Audit report: invalid {key} for {variant}: {value!r}")
        picked[variant] = item

    missing = {"q4_standard", "q4_hindi"} - set(picked)
    if missing:
        raise JobError(f"Audit report is missing results for: {', '.join(sorted(missing))}")
    return picked


def variant_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "domain": item.get("domain"),
        "kld": item["kld"],
        "kld_error": item["kld_error"],
    }
    for key in ("rms", "rms_error", "same_top_p", "same_top_p_error"):
        if key in item:
            summary[key] = item[key]
    return summary


# ============================================================
# JOB ROW: PROGRESS, FAILURE, COMPLETION
# ============================================================

class JobRecorder:
    """Writes this job's progress to its own row. Every write is guarded by status = running."""

    def __init__(self, db: Supabase, job_id: str) -> None:
        self.db = db
        self.job_id = job_id
        self.steps = [{"label": label, "state": "pending"} for label in STEP_LABELS]
        self.progress = 1

    def _patch(self, fields: dict[str, Any]) -> list[dict[str, Any]]:
        fields = {**fields, "heartbeat_at": now_iso()}
        return self.db.patch_job_by_id(self.job_id, fields, only_if_status=STATUS_RUNNING)

    def sync(self, step: str | None = None) -> None:
        fields: dict[str, Any] = {"steps": self.steps, "progress": self.progress}
        if step:
            fields["step"] = step
        try:
            rows = self._patch(fields)
        except SupabaseError as exc:
            # A telemetry failure must not stop the build. Completion is checked separately.
            print(f"WARNING: progress update failed: {scrub(exc, limit=200)}", flush=True)
            return
        if not rows:
            raise JobError("Job is no longer running (reclaimed or changed elsewhere); stopping.")

    def start(self, index: int) -> None:
        self.steps[index]["state"] = "running"
        self.progress = max(self.progress, STEP_PROGRESS[index])
        label = STEP_LABELS[index]
        print(f"\n[{index + 1}/{len(STEP_LABELS)}] {label}", flush=True)
        self.sync(label)

    def done(self, *indices: int) -> None:
        for index in indices:
            self.steps[index]["state"] = "done"
        self.sync()

    def requeue(self, reason: str) -> None:
        """Put the job back in the queue (for example, when the model is locked by another job)."""
        try:
            self.db.patch_job_by_id(
                self.job_id,
                {"status": STATUS_PENDING, "step": reason, "progress": 0, "heartbeat_at": now_iso()},
                only_if_status=STATUS_RUNNING,
            )
        except SupabaseError as exc:
            print(f"WARNING: could not requeue job: {scrub(exc, limit=200)}", flush=True)

    def fail(self, message: str) -> None:
        for step in self.steps:
            if step["state"] == "running":
                step["state"] = "failed"
        text = scrub(message, extra_secrets=(self.db.key,), limit=400)
        try:
            rows = self.db.patch_job_by_id(
                self.job_id,
                {
                    "status": STATUS_FAILED,
                    "step": "Failed",
                    "steps": self.steps,
                    "error_note": text,
                    "heartbeat_at": now_iso(),
                },
                only_if_status=STATUS_RUNNING,
            )
            if not rows:
                print("WARNING: job was not running; failure not recorded.", flush=True)
        except SupabaseError as exc:
            print(f"CRITICAL: could not record failure: {scrub(exc, limit=200)}", flush=True)
        print(f"\nJOB FAILED: {text}", flush=True)

    def complete(self, result: dict[str, Any]) -> None:
        for step in self.steps:
            step["state"] = "done"
        rows = self.db.patch_job_by_id(
            self.job_id,
            {
                "status": STATUS_COMPLETED,
                "steps": self.steps,
                "progress": 100,
                "step": "Completed",
                "result": result,
                "error_note": None,
                "heartbeat_at": now_iso(),
            },
            only_if_status=STATUS_RUNNING,
        )
        if not rows:
            raise JobError(
                "Final update matched no running job. Artifacts were uploaded, "
                "but the job was not marked completed."
            )
        print("\nSUCCESS: build, audit, uploads and final update completed.", flush=True)


class PrepareProgress:
    """prepare_model reports (status, progress, step). Store those on the job row."""

    def __init__(self, rec: JobRecorder) -> None:
        self.rec = rec

    def update(self, status: str | None = None, progress: int | None = None,
               step: str | None = None) -> None:
        if progress is not None:
            # prepare_model covers steps 1-4, so keep its progress below the imatrix step.
            self.rec.progress = max(self.rec.progress, min(int(progress), 70))
        self.rec.sync(step)


class Heartbeat(threading.Thread):
    """Keeps heartbeat_at fresh during long steps so the reaper does not kill a healthy job."""

    def __init__(self, db: Supabase, job_id: str) -> None:
        super().__init__(daemon=True)
        self.db = db
        self.job_id = job_id
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(HEARTBEAT_SECONDS):
            try:
                self.db.patch_job_by_id(
                    self.job_id,
                    {"heartbeat_at": now_iso()},
                    only_if_status=STATUS_RUNNING,
                )
            except SupabaseError as exc:
                print(f"WARNING: heartbeat failed: {scrub(exc, limit=200)}", flush=True)


# ============================================================
# BUILD PIPELINE
# ============================================================

def run_build(db: Supabase, rec: JobRecorder, model_id: str, name: str) -> None:
    hf_token = os.getenv("HF_TOKEN", "").strip() or None
    allowed = {
        item.strip().lower()
        for item in os.getenv("ALLOW_LICENSES", pm.DEFAULT_ALLOWED_LICENSES).split(",")
        if item.strip()
    }

    # Steps 1, 2 and 4 happen inside prepare(): commit-pinned download with policy checks,
    # FP16 GGUF, and the plain Q4_K_M built from that same FP16.
    rec.start(STEP_DOWNLOAD)
    prep = pm.prepare(
        model_id,
        hf_token,
        PrepareProgress(rec),
        allowed_licenses=allowed,
        check_license=True,
        max_gb=pm.DEFAULT_MAX_GB,
        cleanup_hf=True,
    )
    rec.done(STEP_DOWNLOAD, STEP_CONVERT, STEP_PLAIN_Q4)

    fp16 = Path(prep["fp16_path"])
    plain_q4 = Path(prep["standard_q4_path"])
    imatrix = pm.GGUF_DIR / f"{name}.hindi.imatrix"
    hindi_q4 = pm.GGUF_DIR / f"{name}-Q4_K_M-hindi.gguf"

    # Step 3: Hindi imatrix. Rebuilt whenever the FP16 is newer than it.
    rec.start(STEP_IMATRIX)
    if not is_fresh(imatrix, fp16):
        hindi_q4.unlink(missing_ok=True)  # a Q4 built from an old imatrix is stale too
        pm.build_atomic(
            imatrix,
            lambda part: run(
                [IMATRIX_BIN, "-m", fp16, "-f", HINDI_CALIB, "-o", part,
                 "--chunks", IMATRIX_CHUNKS, "-t", IMATRIX_THREADS],
                IMATRIX_TIMEOUT,
                "llama-imatrix",
            ),
        )
    else:
        print("  Hindi imatrix is up to date, reusing it.", flush=True)
    rec.done(STEP_IMATRIX)

    # Step 5: Hindi-imatrix Q4_K_M from the same FP16.
    rec.start(STEP_HINDI_Q4)
    if not is_fresh(hindi_q4, fp16, imatrix):
        pm.build_atomic(
            hindi_q4,
            lambda part: run(
                [pm.QUANTIZE, "--imatrix", imatrix, fp16, part, "Q4_K_M"],
                QUANT_TIMEOUT,
                "llama-quantize (Hindi)",
            ),
        )
    else:
        print("  Hindi Q4_K_M is up to date, reusing it.", flush=True)
    rec.done(STEP_HINDI_Q4)

    # Step 6: real audit. The old report is deleted first, so a stale one can never pass.
    rec.start(STEP_AUDIT)
    report_dir = ROOT / "reports" / report_directory_name(model_id)
    report_path = report_dir / "report.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)

    run(
        [
            sys.executable, AUDIT_SCRIPT,
            "--name", model_id,
            "--fp16", fp16,
            "--dataset", f"hindi={HINDI_TEST}",
            "--quant", f"q4_standard={plain_q4}",
            "--quant", f"q4_hindi={hindi_q4}",
            "--calibration", HINDI_CALIB,
            "--out", report_path,
        ],
        AUDIT_TIMEOUT,
        "audit.py",
    )

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobError(f"Audit report could not be read: {exc}") from exc
    picked = validate_audit_report(report)
    rec.done(STEP_AUDIT)

    # Step 7: uploads. On failure the local files stay, so a retry does not rebuild everything.
    rec.start(STEP_UPLOAD)
    prefix = f"jobs/{rec.job_id}/{name}"
    items: list[tuple[str, Path, str]] = [
        ("quantized_standard", plain_q4, "application/octet-stream"),
        ("quantized_hindi", hindi_q4, "application/octet-stream"),
        ("imatrix", imatrix, "application/octet-stream"),
        ("report", report_path, "application/json"),
    ]
    if UPLOAD_FP16:
        items.insert(0, ("fp16", fp16, "application/octet-stream"))

    objects: dict[str, str] = {}
    for key, local, content_type in items:
        obj = f"{prefix}/{local.name}"
        upload(db, local, obj, content_type)
        objects[key] = obj
    rec.done(STEP_UPLOAD)

    # Step 8: save. Every number comes from the validated report.
    rec.start(STEP_SAVE)
    result = {
        "model_id": model_id,
        "job_id": rec.job_id,
        "commit_sha": prep["commit_sha"],
        "license": prep["license"],
        "weights_gb": prep["weights_gb"],
        "audit": {
            "schema_version": report.get("schema_version"),
            "timestamp": report.get("timestamp"),
            "settings": report.get("settings"),
            "warnings": report.get("audit_warnings", []),
            "comparisons": report.get("comparisons", []),
        },
        "variants": {variant: variant_summary(item) for variant, item in picked.items()},
        "sizes_gb": {
            "fp16": file_size_gb(fp16),
            "quantized_standard": file_size_gb(plain_q4),
            "quantized_hindi": file_size_gb(hindi_q4),
        },
        "artifacts": {"bucket": os.getenv("SUPABASE_STORAGE_BUCKET", "").strip(), **objects},
        "generated_at": now_iso(),
    }
    rec.complete(result)


# ============================================================
# JOB LIFECYCLE
# ============================================================

def process_job(db: Supabase, job: dict[str, Any]) -> str:
    """Runs one claimed job. Returns 'completed', 'failed', or 'requeued'."""
    job_id = validate_job_id(str(job.get("id", "")))
    rec = JobRecorder(db, job_id)
    heartbeat = Heartbeat(db, job_id)
    heartbeat.start()

    try:
        model_id = parse_model_id(job)
        name = model_id.replace("/", "__")

        rec.start(STEP_PREFLIGHT)
        preflight()
        rec.done(STEP_PREFLIGHT)

        # One lock for the whole build of this model. prepare_model uses <name>.lock
        # internally, so this lock uses <name>.job.lock to avoid a self-deadlock.
        with pm.model_lock(f"{name}.job"):
            run_build(db, rec, model_id, name)
        return "completed"

    except pm.PrepareError as exc:
        if exc.code == pm.EXIT_BUSY:
            rec.requeue("Waiting: another job is building this model")
            print("Model is busy; job put back in the queue.", flush=True)
            return "requeued"
        rec.fail(str(exc))
        return "failed"

    except JobError as exc:
        rec.fail(str(exc))
        return "failed"

    except Exception as exc:
        rec.fail(f"{type(exc).__name__}: {exc}")
        return "failed"

    except KeyboardInterrupt:
        rec.fail("Stopped by operator (KeyboardInterrupt).")
        raise

    finally:
        heartbeat.stop_event.set()


def claim(db: Supabase, job: dict[str, Any]) -> str | None:
    """Atomically move one pending job to running. Returns its id if this worker won."""
    job_id = validate_job_id(str(job.get("id", "")))
    rows = db.patch_job_by_id(
        job_id,
        {"status": STATUS_RUNNING, "step": "Claimed", "progress": 1, "heartbeat_at": now_iso()},
        only_if_status=STATUS_PENDING,
    )
    return job_id if rows else None


def reap_stale_jobs(db: Supabase) -> None:
    """Fail jobs whose worker died. Only rows still in the running status are touched."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)).isoformat()
    stale = db.get_rows(
        TABLE_JOBS,
        {
            "select": "id",
            "status": f"eq.{STATUS_RUNNING}",
            "heartbeat_at": f"lt.{cutoff}",
            "limit": "50",
        },
    )
    for row in stale:
        try:
            job_id = validate_job_id(str(row.get("id", "")))
        except ValueError:
            continue
        rows = db.patch_job_by_id(
            job_id,
            {
                "status": STATUS_FAILED,
                "step": "Failed",
                "error_note": "Worker heartbeat timed out; job reclaimed as failed.",
                "heartbeat_at": now_iso(),
            },
            only_if_status=STATUS_RUNNING,
        )
        if rows:
            print(f"Reaped stale job {job_id}", flush=True)


def run_once(db: Supabase, requested_job_id: str | None = None) -> str:
    """Process at most one job. Returns 'completed', 'failed', 'requeued', or 'idle'."""
    if requested_job_id:
        job_id = validate_job_id(requested_job_id)
        rows = db.get_rows(
            TABLE_JOBS,
            {"select": "*", "id": f"eq.{job_id}", "status": f"eq.{STATUS_PENDING}", "limit": "1"},
        )
        if not rows:
            print(f"Job {job_id} is not pending (missing, running, or finished).", flush=True)
            return "idle"
    else:
        rows = db.get_rows(
            TABLE_JOBS,
            {
                "select": "*",
                "status": f"eq.{STATUS_PENDING}",
                "order": "created_at.asc",
                "limit": "10",
            },
        )
        if not rows:
            print("No pending job.", flush=True)
            return "idle"

    for row in rows:
        if claim(db, row) is None:
            print(f"Job {row.get('id')} was claimed by another worker; trying the next one.", flush=True)
            continue
        return process_job(db, row)

    return "idle"


# ============================================================
# CHECK COMMAND
# ============================================================

def cmd_check(db: Supabase) -> int:
    print("\n=== STHAMLY NANOBRAIN WORKER CHECK ===\n")
    problems: list[str] = []

    try:
        db.get_rows(TABLE_JOBS, {"select": "id", "limit": "1"})
    except SupabaseError as exc:
        problems.append(f"Supabase: {scrub(exc, extra_secrets=(db.key,), limit=200)}")

    problems += config_problems()

    free = free_gb(pm.GGUF_DIR)
    if free < MIN_FREE_GB:
        problems.append(f"free disk {free:.1f} GB; need at least {MIN_FREE_GB:g} GB")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"\nCHECK FAILED: {len(problems)} issue(s).")
        return 1

    print("CHECK PASSED: config, tools, datasets, Supabase and disk look ready.")
    print("This does not prove that a full model build will succeed on this machine.")
    return 0


# ============================================================
# ENTRY POINT
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain worker")
    parser.add_argument("--check", action="store_true", help="Check config, tools and Supabase, then exit.")
    parser.add_argument("--once", action="store_true", help="Process at most one job, then exit.")
    parser.add_argument("--job-id", default=None, help="Process this pending job UUID (use with --once).")
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS,
                        help="Polling interval in seconds (3-300).")
    args = parser.parse_args()

    if args.job_id and not args.once:
        parser.error("--job-id requires --once.")

    try:
        db = Supabase.from_env()
    except SupabaseError as exc:
        print(f"Configuration error: {scrub(exc, limit=300)}", file=sys.stderr)
        return 1 if args.check else 2

    if args.check:
        return cmd_check(db)

    if args.once or args.job_id:
        try:
            reap_stale_jobs(db)
            outcome = run_once(db, args.job_id)
        except (SupabaseError, ValueError) as exc:
            print(f"Worker error: {scrub(exc, extra_secrets=(db.key,), limit=300)}", file=sys.stderr)
            return 1
        print(f"Outcome: {outcome}", flush=True)
        return 1 if outcome == "failed" else 0

    poll = max(3, min(300, args.poll_seconds))
    print(
        "STHAMLY NANOBRAIN WORKER STARTED\n"
        f"Project: {ROOT}\n"
        f"Polling every {poll} seconds. Press Ctrl+C to stop.",
        flush=True,
    )

    while True:
        try:
            reap_stale_jobs(db)
            run_once(db)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0
        except (SupabaseError, ValueError) as exc:
            print(f"Polling error: {scrub(exc, extra_secrets=(db.key,), limit=300)}",
                  file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"Unexpected worker error: {type(exc).__name__}: "
                  f"{scrub(exc, extra_secrets=(db.key,), limit=300)}",
                  file=sys.stderr, flush=True)

        try:
            time.sleep(poll)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())