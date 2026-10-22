#!/usr/bin/env python3
"""
Sthamly NanoBrain Worker (Option A: table `jobs`, schema db/schema_v3.sql + 0002 migration)

Commands:
    python scripts/worker.py --check
    python scripts/worker.py --once                    # claim the oldest queued job (claim_next_job RPC)
    python scripts/worker.py --once --job-id UUID      # claim one specific queued job
    python scripts/worker.py                           # poll forever

Job kinds:
    audit      free plain-Q4 audit (plain Q4 vs FP16 only)
    calibrate  full build: Hindi imatrix, plain Q4 and Hindi Q4, audit both

Statuses written: running -> done | failed | refused   (requeue puts it back to queued)
    refused = policy rejection (bad model, custom code, license, size). Not a bug.
    failed  = runtime error (tool crash, timeout, upload failure). Investigate.

Never invents a metric. jobs.result comes only from a complete audit report that passed validation.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import prepare_model as pm  # noqa: E402  download, policy, FP16, plain Q4, lock, atomic build
from audit import report_directory_name  # noqa: E402
from sthamly_common import (  # noqa: E402
    JOB_DONE, JOB_FAILED, JOB_KINDS, JOB_QUEUED, JOB_REFUSED, JOB_RUNNING,
    PUBLIC_VARIANT, STEP_LABELS, TABLE_JOBS, TIMEOUT_UPLOAD,
    Supabase, SupabaseError, normalize_model_id, scrub, validate_job_id,
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
STALE_SECONDS = 900          # fail_stale_jobs() threshold; must be >= 60

# Step labels, identical to STEP_LABELS in sthamly_common.py
S_PREFLIGHT = "Preflight checks"
S_DOWNLOAD = "Download and policy check"
S_FP16 = "Convert to FP16 GGUF"
S_IMATRIX = "Build Hindi imatrix"
S_PLAIN = "Quantize plain Q4_K_M"
S_HINDI = "Quantize Hindi Q4_K_M"
S_AUDIT = "Run audit"
S_SAVE = "Save results"


# ============================================================
# ERRORS AND HELPERS
# ============================================================

class JobError(Exception):
    """A step failed. refused=True means the input was rejected by policy, not a runtime bug."""

    def __init__(self, message: str, refused: bool = False) -> None:
        super().__init__(message)
        self.refused = refused


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
    if not target.is_file() or target.stat().st_size == 0:
        return False
    return all(target.stat().st_mtime >= src.stat().st_mtime for src in sources)


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e9


def parse_model_id(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        raise JobError("Job has no hf_model.", refused=True)
    model_id = normalize_model_id(text)
    if model_id is None:
        raise JobError("Invalid model id. Use owner/name or a huggingface.co model link.", refused=True)
    return model_id


def config_problems(kind: str) -> list[str]:
    """Operator-side problems (tools, files, bucket). These are failures, not refusals."""
    problems: list[str] = []
    if not pm.CONVERT.is_file():
        problems.append(f"GGUF converter not found: {pm.CONVERT}")
    if not pm.QUANTIZE.is_file():
        problems.append(f"llama-quantize not found: {pm.QUANTIZE}")
    if kind == "calibrate":
        if not IMATRIX_BIN.is_file():
            problems.append(f"llama-imatrix not found: {IMATRIX_BIN}")
        if not HINDI_CALIB.is_file() or HINDI_CALIB.stat().st_size == 0:
            problems.append(f"Hindi calibration file missing or empty: {HINDI_CALIB}")
    if not HINDI_TEST.is_file() or HINDI_TEST.stat().st_size == 0:
        problems.append(f"Hindi test file missing or empty: {HINDI_TEST}")
    if not AUDIT_SCRIPT.is_file():
        problems.append(f"audit.py not found: {AUDIT_SCRIPT}")
    if not os.getenv("SUPABASE_STORAGE_BUCKET", "").strip():
        problems.append("SUPABASE_STORAGE_BUCKET is not set")
    if kind == "calibrate" and HINDI_CALIB.is_file() and HINDI_TEST.is_file() \
            and file_sha256(HINDI_CALIB) == file_sha256(HINDI_TEST):
        problems.append("Calibration and test files are identical (leakage).")
    return problems


def preflight(kind: str) -> None:
    problems = config_problems(kind)
    free = free_gb(pm.GGUF_DIR)
    if free < MIN_FREE_GB:
        problems.append(f"free disk {free:.1f} GB; need at least {MIN_FREE_GB:g} GB")
    if problems:
        raise JobError("Preflight failed: " + "; ".join(problems))


# ============================================================
# SUBPROCESS WITH TIMEOUT
# ============================================================

def run(command: list[Any], timeout: int, label: str) -> None:
    cmd = [str(part) for part in command]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace", bufsize=1)
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
        raise JobError(f"{label}: exit code {proc.returncode}. Last output: {' | '.join(recent)[-400:]}")


# ============================================================
# STORAGE UPLOAD (streamed, never fully in RAM)
# ============================================================

def upload(db: Supabase, local: Path, obj: str, content_type: str) -> None:
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "").strip()
    if not bucket:
        raise JobError("SUPABASE_STORAGE_BUCKET is not set.")
    if not local.is_file() or local.stat().st_size == 0:
        raise JobError(f"Upload source missing or empty: {local.name}")

    bucket_q = urllib.parse.quote(bucket, safe="")
    object_q = "/".join(urllib.parse.quote(part, safe="") for part in obj.split("/"))
    req_url = f"{db.url}/storage/v1/object/{bucket_q}/{object_q}"

    with local.open("rb") as handle:
        req = urllib.request.Request(
            req_url,
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
            with urllib.request.urlopen(req, timeout=TIMEOUT_UPLOAD) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise JobError(f"Upload of {local.name} failed: HTTP {exc.code} "
                           f"{scrub(detail, (db.key,), 200)}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise JobError(f"Upload of {local.name} failed: {scrub(exc, (db.key,), 200)}") from exc


# ============================================================
# AUDIT REPORT VALIDATION AND RESULT SHAPE
# ============================================================

REQUIRED_VARIANTS = {"audit": ("q4_standard",), "calibrate": ("q4_standard", "q4_hindi")}


def validate_audit_report(report: Any, kind: str) -> dict[str, dict[str, Any]]:
    """Accept only a complete schema-2 report with finite numbers for every required variant."""
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
        if item.get("domain") != "hindi" or item.get("variant") not in REQUIRED_VARIANTS[kind]:
            continue
        for key in ("kld", "kld_error", "rms", "rms_error", "same_top_p", "same_top_p_error"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value) or value < 0 and key in ("kld", "kld_error"):
                raise JobError(f"Audit report: invalid {key} for {item.get('variant')}: {value!r}")
        picked[item["variant"]] = item

    missing = set(REQUIRED_VARIANTS[kind]) - set(picked)
    if missing:
        raise JobError(f"Audit report is missing results for: {', '.join(sorted(missing))}")
    return picked


def public_results(picked: dict[str, dict[str, Any]]) -> dict[str, dict[str, dict[str, list[float]]]]:
    """Shape of lib/types.ts Results: domain -> public variant -> {kld, rms, top}, each [mean, error]."""
    out: dict[str, dict[str, dict[str, list[float]]]] = {"hindi": {}}
    for internal, item in picked.items():
        out["hindi"][PUBLIC_VARIANT[internal]] = {
            "kld": [item["kld"], item["kld_error"]],
            "rms": [item["rms"], item["rms_error"]],
            "top": [item["same_top_p"], item["same_top_p_error"]],
        }
    return out


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


# ============================================================
# JOB ROW WRITER (every write is guarded by status = running)
# ============================================================

class Recorder:
    def __init__(self, db: Supabase, job_id: str, kind: str) -> None:
        self.db = db
        self.job_id = job_id
        self.labels = STEP_LABELS[kind]
        self.steps = [{"label": label, "state": "pending"} for label in self.labels]
        self.done_count = 0

    def _set_state(self, label: str, state: str) -> None:
        for step in self.steps:
            if step["label"] == label:
                step["state"] = state

    def sync(self, step: str | None = None) -> None:
        progress = min(99, int(100 * self.done_count / len(self.labels)))
        fields: dict[str, Any] = {"steps": self.steps, "progress": progress,
                                  "heartbeat_at": now_iso()}
        if step:
            fields["step"] = step
        try:
            rows = self.db.patch_job(self.job_id, fields, only_if_status=JOB_RUNNING)
        except SupabaseError as exc:
            print(f"WARNING: progress update failed: {scrub(exc, limit=200)}", flush=True)
            return
        if not rows:
            raise JobError("Job is no longer running (reclaimed or changed elsewhere); stopping.")

    def start(self, label: str) -> None:
        self._set_state(label, "running")
        print(f"\n[{label}]", flush=True)
        self.sync(step=label)

    def done(self, label: str) -> None:
        if label in self.labels:
            self._set_state(label, "done")
            self.done_count = sum(1 for s in self.steps if s["state"] == "done")
        self.sync()

    def requeue(self, reason: str) -> None:
        try:
            self.db.patch_job(self.job_id, {"status": JOB_QUEUED, "step": reason, "progress": 0,
                                            "heartbeat_at": now_iso()}, only_if_status=JOB_RUNNING)
        except SupabaseError as exc:
            print(f"WARNING: could not requeue: {scrub(exc, limit=200)}", flush=True)

    def fail(self, message: str, refused: bool) -> None:
        for step in self.steps:
            if step["state"] == "running":
                step["state"] = "failed"
        status = JOB_REFUSED if refused else JOB_FAILED
        text = scrub(message, (self.db.key,), 400)
        try:
            rows = self.db.patch_job(self.job_id, {
                "status": status, "step": "Refused" if refused else "Failed", "steps": self.steps,
                "error": text, "finished_at": now_iso(), "heartbeat_at": now_iso(),
            }, only_if_status=JOB_RUNNING)
            if not rows:
                print("WARNING: job was not running; final status not recorded.", flush=True)
        except SupabaseError as exc:
            print(f"CRITICAL: could not record failure: {scrub(exc, (self.db.key,), 200)}", flush=True)
        print(f"\nJOB {status.upper()}: {text}", flush=True)

    def complete(self, result: dict[str, Any]) -> None:
        for step in self.steps:
            step["state"] = "done"
        rows = self.db.patch_job(self.job_id, {
            "status": JOB_DONE, "steps": self.steps, "progress": 100, "step": "Completed",
            "result": result, "error": None, "finished_at": now_iso(), "heartbeat_at": now_iso(),
        }, only_if_status=JOB_RUNNING)
        if not rows:
            raise JobError("Final update matched no running job. Artifacts exist but the job "
                           "was not marked done.")
        print("\nSUCCESS: build, audit, uploads and final update completed.", flush=True)


class PrepareProgress:
    """prepare_model reports (status, progress, step). Only the step text is stored."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec

    def update(self, status: str | None = None, progress: int | None = None,
               step: str | None = None) -> None:
        if step:
            self.rec.sync(step=step)


class Heartbeat(threading.Thread):
    def __init__(self, db: Supabase, job_id: str) -> None:
        super().__init__(daemon=True)
        self.db = db
        self.job_id = job_id
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(HEARTBEAT_SECONDS):
            try:
                self.db.patch_job(self.job_id, {"heartbeat_at": now_iso()}, only_if_status=JOB_RUNNING)
            except SupabaseError as exc:
                print(f"WARNING: heartbeat failed: {scrub(exc, limit=200)}", flush=True)


# ============================================================
# BUILD PIPELINE
# ============================================================

def run_build(db: Supabase, rec: Recorder, kind: str, model_id: str, name: str) -> dict[str, Any]:
    hf_token = os.getenv("HF_TOKEN", "").strip() or None
    allowed = {x.strip().lower() for x in
               os.getenv("ALLOW_LICENSES", pm.DEFAULT_ALLOWED_LICENSES).split(",") if x.strip()}

    # Download, policy, FP16 and plain Q4 all happen inside prepare(). Its own lock is <name>.lock.
    rec.start(S_DOWNLOAD)
    prep = pm.prepare(model_id, hf_token, PrepareProgress(rec), allowed_licenses=allowed,
                      check_license=True, max_gb=pm.DEFAULT_MAX_GB, cleanup_hf=True)
    rec.done(S_DOWNLOAD)
    rec.done(S_FP16)
    rec.done(S_PLAIN)

    fp16 = Path(prep["fp16_path"])
    plain_q4 = Path(prep["standard_q4_path"])
    imatrix = pm.GGUF_DIR / f"{name}.hindi.imatrix"
    hindi_q4 = pm.GGUF_DIR / f"{name}-Q4_K_M-hindi.gguf"

    quants = [f"q4_standard={plain_q4}"]
    if kind == "calibrate":
        rec.start(S_IMATRIX)
        if not is_fresh(imatrix, fp16):
            hindi_q4.unlink(missing_ok=True)
            pm.build_atomic(imatrix, lambda part: run(
                [IMATRIX_BIN, "-m", fp16, "-f", HINDI_CALIB, "-o", part,
                 "--chunks", IMATRIX_CHUNKS, "-t", IMATRIX_THREADS],
                IMATRIX_TIMEOUT, "llama-imatrix"))
        else:
            print("  Hindi imatrix is up to date, reusing it.", flush=True)
        rec.done(S_IMATRIX)

        rec.start(S_HINDI)
        if not is_fresh(hindi_q4, fp16, imatrix):
            pm.build_atomic(hindi_q4, lambda part: run(
                [pm.QUANTIZE, "--imatrix", imatrix, fp16, part, "Q4_K_M"],
                QUANT_TIMEOUT, "llama-quantize (Hindi)"))
        else:
            print("  Hindi Q4_K_M is up to date, reusing it.", flush=True)
        rec.done(S_HINDI)
        quants.append(f"q4_hindi={hindi_q4}")

    rec.start(S_AUDIT)
    report_dir = ROOT / "reports" / report_directory_name(model_id)
    report_path = report_dir / "report.json"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)  # a stale report must never pass

    audit_cmd: list[Any] = [sys.executable, AUDIT_SCRIPT, "--name", model_id, "--fp16", fp16,
                            "--dataset", f"hindi={HINDI_TEST}", "--out", report_path]
    for quant in quants:
        audit_cmd += ["--quant", quant]
    if kind == "calibrate":
        audit_cmd += ["--calibration", HINDI_CALIB]
    run(audit_cmd, AUDIT_TIMEOUT, "audit.py")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise JobError(f"Audit report could not be read: {exc}") from exc
    picked = validate_audit_report(report, kind)
    rec.done(S_AUDIT)

    rec.start(S_SAVE)
    prefix = f"jobs/{rec.job_id}/{name}"
    files: dict[str, tuple[Path, str]] = {"quantized_standard": (plain_q4, "application/octet-stream"),
                                           "report": (report_path, "application/json")}
    if kind == "calibrate":
        files["quantized_hindi"] = (hindi_q4, "application/octet-stream")
        files["imatrix"] = (imatrix, "application/octet-stream")
    if UPLOAD_FP16:
        files["fp16"] = (fp16, "application/octet-stream")

    artifacts: dict[str, str] = {}
    for key, (local, content_type) in files.items():
        obj = f"{prefix}/{local.name}"
        upload(db, local, obj, content_type)
        artifacts[key] = obj

    sizes = {"fp16": file_size_gb(fp16), "quantized_standard": file_size_gb(plain_q4)}
    if kind == "calibrate":
        sizes["quantized_hindi"] = file_size_gb(hindi_q4)

    common = {
        "model": model_id,
        "license": prep["license"],
        "weights_gb": prep["weights_gb"],
        "commit_sha": prep["commit_sha"],
        "chunks": (report.get("settings") or {}).get("chunks"),
        "domains": {"hindi": count_lines(HINDI_TEST)},
        "fp16_gb": sizes["fp16"],
        "quant_gb": sizes.get("quantized_hindi", sizes["quantized_standard"]),
        "results": public_results(picked),
        "sizes_gb": sizes,
        "artifacts": {"bucket": os.getenv("SUPABASE_STORAGE_BUCKET", "").strip(), **artifacts},
        "audit_warnings": report.get("audit_warnings", []),
        "note": "KLD vs FP16 on held-out text. A proxy, not task accuracy.",
        "generated_at": now_iso(),
    }
    rec.done(S_SAVE)
    return {"kind": kind, **common}


# ============================================================
# JOB LIFECYCLE
# ============================================================

def process_job(db: Supabase, row: dict[str, Any]) -> str:
    """Runs one claimed job. Returns the final status: done | failed | refused | queued."""
    job_id = validate_job_id(str(row.get("id", "")))
    kind = str(row.get("kind") or "")
    rec = Recorder(db, job_id, kind if kind in JOB_KINDS else "audit")
    hb = Heartbeat(db, job_id)
    hb.start()
    try:
        if kind not in JOB_KINDS:
            raise JobError(f"Unknown job kind {kind!r}.", refused=True)

        model_id = parse_model_id(row.get("hf_model"))
        name = model_id.replace("/", "__")

        rec.start(S_PREFLIGHT)
        preflight(kind)
        rec.done(S_PREFLIGHT)

        with pm.model_lock(f"{name}.job"):
            result = run_build(db, rec, kind, model_id, name)
        rec.complete(result)
        return JOB_DONE

    except pm.PrepareError as exc:
        if exc.code == pm.EXIT_BUSY:
            rec.requeue("Waiting: another job is building this model")
            print("Model is busy; job put back in the queue.", flush=True)
            return JOB_QUEUED
        refused = exc.code in (pm.EXIT_CUSTOM_CODE, pm.EXIT_POLICY)
        rec.fail(str(exc), refused=refused)
        return JOB_REFUSED if refused else JOB_FAILED

    except JobError as exc:
        rec.fail(str(exc), refused=exc.refused)
        return JOB_REFUSED if exc.refused else JOB_FAILED

    except KeyboardInterrupt:
        rec.fail("Stopped by operator.", refused=False)
        raise

    except Exception as exc:
        rec.fail(f"{type(exc).__name__}: {exc}", refused=False)
        return JOB_FAILED

    finally:
        hb.stop_event.set()


def claim_next(db: Supabase) -> dict[str, Any] | None:
    """Atomic claim via the claim_next_job() RPC (FOR UPDATE SKIP LOCKED in SQL)."""
    row = db.rpc("claim_next_job", {})
    return row if isinstance(row, dict) and row.get("id") else None


def claim_specific(db: Supabase, job_id: str) -> dict[str, Any] | None:
    rows = db.patch_job(job_id, {"status": JOB_RUNNING, "step": "Claimed", "progress": 1,
                                 "started_at": now_iso(), "heartbeat_at": now_iso()},
                        only_if_status=JOB_QUEUED)
    if not rows:
        return None
    full = db.get_rows(TABLE_JOBS, {"select": "*", "id": f"eq.{job_id}", "limit": "1"})
    return full[0] if full else None


def reap_stale_jobs(db: Supabase) -> None:
    changed = db.rpc("fail_stale_jobs", {"stale_after_seconds": STALE_SECONDS})
    if isinstance(changed, int) and changed > 0:
        print(f"Failed {changed} stale running job(s) (heartbeat older than {STALE_SECONDS}s).", flush=True)


def run_once(db: Supabase, job_id: str | None = None) -> str:
    row = claim_specific(db, validate_job_id(job_id)) if job_id else claim_next(db)
    if row is None:
        print("No queued job available." if not job_id else f"Job {job_id} is not queued.", flush=True)
        return "idle"
    return process_job(db, row)


# ============================================================
# CHECK, MAIN
# ============================================================

def cmd_check(db: Supabase) -> int:
    print("\n=== STHAMLY NANOBRAIN WORKER CHECK ===\n")
    problems: list[str] = []
    try:
        db.get_rows(TABLE_JOBS, {"select": "id", "limit": "1"})
    except SupabaseError as exc:
        problems.append(f"Supabase: {scrub(exc, (db.key,), 200)}")
    problems += config_problems("calibrate")
    free = free_gb(pm.GGUF_DIR)
    if free < MIN_FREE_GB:
        problems.append(f"free disk {free:.1f} GB; need at least {MIN_FREE_GB:g} GB")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"\nCHECK FAILED: {len(problems)} issue(s).")
        return 1
    print("CHECK PASSED. This does not prove a full model build will succeed on this machine.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain worker")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--job-id", default=None, help="claim this queued job (use with --once)")
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS)
    args = parser.parse_args()
    if args.job_id and not args.once:
        parser.error("--job-id requires --once.")

    db = Supabase.from_env()   # exits with a clear message if the env is missing
    if args.check:
        return cmd_check(db)

    if args.once or args.job_id:
        try:
            reap_stale_jobs(db)
            outcome = run_once(db, args.job_id)
        except (SupabaseError, ValueError) as exc:
            print(f"Worker error: {scrub(exc, (db.key,), 300)}", file=sys.stderr)
            return 1
        print(f"Outcome: {outcome}", flush=True)
        return 1 if outcome == JOB_FAILED else 0

    poll = max(3, min(300, args.poll_seconds))
    print(f"WORKER STARTED. Project: {ROOT}. Polling every {poll}s. Ctrl+C to stop.", flush=True)
    while True:
        try:
            reap_stale_jobs(db)
            run_once(db)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0
        except (SupabaseError, ValueError) as exc:
            print(f"Polling error: {scrub(exc, (db.key,), 300)}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(f"Unexpected worker error: {type(exc).__name__}: {scrub(exc, (db.key,), 300)}",
                  file=sys.stderr, flush=True)
        try:
            time.sleep(poll)
        except KeyboardInterrupt:
            print("\nWorker stopped by user.", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())