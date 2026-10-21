"""
Sthamly NanoBrain Worker
========================

Commands:
    python scripts/worker.py --check
    python scripts/worker.py --once
    python scripts/worker.py
    python scripts/worker.py --once --job-id JOB_UUID

Required environment variables:
    NEXT_PUBLIC_SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
    HINDI_TEST_DATASET
    SUPABASE_STORAGE_BUCKET

Required tools:
    Python
    Hugging Face CLI (`hf`)
    llama.cpp converter: convert_hf_to_gguf.py
    llama.cpp: llama-quantize
    scripts/audit.py

The worker never invents metrics.
A job is marked completed only after real artifacts,
a successful audit report, and configured storage uploads.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))


# ============================================================
# CONFIGURATION
# ============================================================

@dataclass
class Config:
    root: Path = ROOT

    poll_seconds: int = 15
    request_timeout: int = 30

    max_weights_gb: float = 4.2
    threads: int = max(1, min(os.cpu_count() or 2, 8))

    prepare_timeout: int = 3600
    audit_timeout: int = 10800

    keep_files: bool = (
        os.getenv("NANOBRAIN_KEEP_FILES", "false").lower()
        == "true"
    )

    @property
    def work_dir(self) -> Path:
        return self.root / "work"

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def dataset(self) -> Path:
        return Path(
            os.getenv(
                "HINDI_TEST_DATASET",
                str(self.root / "data" / "hindi-test.txt"),
            )
        )

    @property
    def supabase_url(self) -> str:
        return os.getenv(
            "NEXT_PUBLIC_SUPABASE_URL", ""
        ).strip().rstrip("/")

    @property
    def supabase_key(self) -> str:
        return os.getenv(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip()

    @property
    def storage_bucket(self) -> str:
        return os.getenv(
            "SUPABASE_STORAGE_BUCKET", ""
        ).strip()

    @property
    def hf_command(self) -> str:
        return os.getenv("HF_COMMAND", "hf")

    @property
    def converter(self) -> str:
        return os.getenv(
            "LLAMA_CPP_CONVERTER",
            str(
                self.root
                / "llama.cpp"
                / "convert_hf_to_gguf.py"
            ),
        )

    @property
    def quantize_command(self) -> str:
        return os.getenv(
            "LLAMA_QUANTIZE",
            "llama-quantize",
        )

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
    """Job rejected because its input is invalid or unsupported."""


# ============================================================
# HELPERS
# ============================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_text(value: Any, limit: int = 500) -> str:
    """Remove newlines and avoid dumping huge error messages."""
    text = str(value or "")
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text[:limit]


def safe_model_name(model_id: str) -> str:
    """Turn a Hugging Face model ID into a safe directory name."""
    cleaned = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        model_id.strip(),
    ).strip("._-")

    if not cleaned:
        raise Refused("Invalid model identifier.")

    return cleaned[:120]


def parse_positive_int(
    value: Any,
    default: int,
    minimum: int = 1,
    maximum: int = 100000,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default

    return max(minimum, min(maximum, result))


def run_command(
    command: list[str],
    timeout: int,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Execute a real command.
    Never pretend that a failed command succeeded.
    """
    print(
        "\n$ " + " ".join(str(part) for part in command),
        flush=True,
    )

    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkerError(
            f"Command timed out after {timeout} seconds."
        ) from exc
    except OSError as exc:
        raise WorkerError(
            f"Could not execute command: {exc}"
        ) from exc

    output = result.stdout or ""

    if output:
        print(
            safe_text(output, limit=12000),
            flush=True,
        )

    if result.returncode != 0:
        raise WorkerError(
            "Command failed with exit code "
            f"{result.returncode}: "
            f"{safe_text(output, 300)}"
        )

    return result


def require_file(path: Path, description: str) -> Path:
    if not path.exists() or not path.is_file():
        raise WorkerError(
            f"{description} not found: {path}"
        )

    if path.stat().st_size <= 0:
        raise WorkerError(
            f"{description} is empty: {path}"
        )

    return path


def file_size_gb(path: Path) -> float:
    return round(
        path.stat().st_size / (1024 ** 3),
        4,
    )


# ============================================================
# SUPABASE REST CLIENT
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
            raise SupabaseError(
                "NEXT_PUBLIC_SUPABASE_URL is missing."
            )

        if not self.url.startswith("https://"):
            raise SupabaseError(
                "Supabase URL must use HTTPS."
            )

        if not self.key:
            raise SupabaseError(
                "SUPABASE_SERVICE_ROLE_KEY is missing."
            )

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        prefer: str | None = None,
        timeout: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        self.check_config()

        url = self.url + path

        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }

        if payload is not None:
            headers["Content-Type"] = "application/json"

        if prefer:
            headers["Prefer"] = prefer

        if extra_headers:
            headers.update(extra_headers)

        body = None

        if payload is not None:
            body = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=timeout or CFG.request_timeout,
            ) as response:
                return (
                    response.status,
                    response.read(),
                )

        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode(
                "utf-8",
                errors="replace",
            )

            raise SupabaseError(
                f"Supabase HTTP {exc.code}: "
                f"{safe_text(error_body, 500)}"
            ) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
        ) as exc:
            raise SupabaseError(
                f"Supabase connection failed: "
                f"{safe_text(exc, 300)}"
            ) from exc

    def get_rows(
        self,
        table: str,
        query: dict[str, str],
    ) -> list[dict[str, Any]]:
        query_string = urllib.parse.urlencode(query)

        _, raw = self.request(
            "GET",
            f"/rest/v1/{table}?{query_string}",
        )

        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SupabaseError(
                "Supabase returned invalid JSON."
            ) from exc

        if not isinstance(result, list):
            raise SupabaseError(
                "Expected a JSON array from Supabase."
            )

        return result

    def patch_job(
        self,
        job_id: str,
        fields: dict[str, Any],
        only_if_status: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        PATCH can condition on the current status.

        The returned rows tell us whether the conditional update
        actually matched a job.
        """
        query = {
            "id": f"eq.{job_id}",
            "select": "id,status",
        }

        if only_if_status:
            query["status"] = f"eq.{only_if_status}"

        query_string = urllib.parse.urlencode(query)

        _, raw = self.request(
            "PATCH",
            f"/rest/v1/quantization_jobs?{query_string}",
            payload=fields,
            prefer="return=representation",
        )

        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SupabaseError(
                "Could not parse job update response."
            ) from exc

        if not isinstance(result, list):
            raise SupabaseError(
                "Unexpected job update response."
            )

        return result

    def fetch_pending_jobs(
        self,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return self.get_rows(
            "quantization_jobs",
            {
                "select": "*",
                "status": "eq.pending",
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )

    def claim_job(
        self,
        job: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Claim only if status is still pending.

        This conditional PATCH helps prevent two workers from
        processing the same pending job.

        For high-volume production, prefer a dedicated atomic
        PostgreSQL RPC using SELECT ... FOR UPDATE SKIP LOCKED.
        """
        job_id = str(job.get("id", "")).strip()

        if not job_id:
            raise WorkerError(
                "Pending job has no ID."
            )

        rows = self.patch_job(
            job_id,
            {
                "status": "extracting",
                "step": "Claiming job",
                "heartbeat_at": now_iso(),
                "progress": 1,
            },
            only_if_status="pending",
        )

        if not rows:
            return None

        claimed = dict(job)
        claimed["status"] = "extracting"

        return claimed

    def upload_file(
        self,
        local_path: Path,
        object_path: str,
        content_type: str,
    ) -> str:
        """
        Upload an artifact to a Supabase Storage bucket.

        The bucket must already exist and storage permissions
        must permit the worker's service-role credentials.
        """
        if not CFG.storage_bucket:
            raise SupabaseError(
                "SUPABASE_STORAGE_BUCKET is missing."
            )

        require_file(local_path, "Upload artifact")

        bucket = urllib.parse.quote(
            CFG.storage_bucket,
            safe="",
        )

        object_path = object_path.lstrip("/")

        encoded_object = "/".join(
            urllib.parse.quote(segment, safe="")
            for segment in object_path.split("/")
        )

        path = (
            f"/storage/v1/object/"
            f"{bucket}/{encoded_object}"
        )

        data = local_path.read_bytes()

        self.request(
            "POST",
            path,
            payload=None,
            extra_headers={
                "Content-Type": content_type,
                "x-upsert": "true",
                "Content-Length": str(len(data)),
            },
        ) if False else None

        # Binary upload must not use the JSON request helper.
        req = urllib.request.Request(
            self.url + path,
            data=data,
            headers={
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=max(CFG.request_timeout, 120),
            ) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            message = exc.read().decode(
                "utf-8",
                errors="replace",
            )
            raise SupabaseError(
                f"Storage upload failed (HTTP {exc.code}): "
                f"{safe_text(message, 300)}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SupabaseError(
                f"Storage upload failed: {safe_text(exc, 300)}"
            ) from exc

        return object_path


# ============================================================
# JOB STATE / TELEMETRY
# ============================================================

AUDIT_STEPS = [
    "Validate model and configuration",
    "Download Hugging Face model",
    "Convert model to FP16 GGUF",
    "Quantize model to Q4_K_M",
    "Run real audit and validate report",
    "Upload artifacts and save results",
]


class JobState:
    def __init__(
        self,
        db: Supabase,
        job_id: str,
    ) -> None:
        self.db = db
        self.job_id = job_id
        self.steps = [
            {
                "label": label,
                "state": "pending",
            }
            for label in AUDIT_STEPS
        ]
        self.progress = 1

    def push(self, step: str | None = None) -> None:
        fields: dict[str, Any] = {
            "steps": self.steps,
            "progress": self.progress,
            "heartbeat_at": now_iso(),
        }

        if step is not None:
            fields["step"] = step

        try:
            self.db.patch_job(
                self.job_id,
                fields,
            )
        except SupabaseError as exc:
            # Telemetry failure must not silently fake success.
            # A final DB update is still required at completion.
            print(
                f"WARNING: telemetry update failed: {exc}",
                flush=True,
            )

    def start(
        self,
        index: int,
        progress: int,
    ) -> None:
        for i in range(index):
            self.steps[i]["state"] = "done"

        self.steps[index]["state"] = "running"
        self.progress = max(
            self.progress,
            min(99, progress),
        )

        label = self.steps[index]["label"]

        print(
            f"\n[{index + 1}/{len(self.steps)}] {label}",
            flush=True,
        )

        self.push(label)

    def done(self, index: int) -> None:
        self.steps[index]["state"] = "done"
        self.push()

    def complete(
        self,
        report: dict[str, Any],
        artifact_paths: dict[str, str],
    ) -> None:
        for item in self.steps:
            item["state"] = "done"

        fields: dict[str, Any] = {
            "status": "completed",
            "steps": self.steps,
            "progress": 100,
            "step": "Completed",
            "heartbeat_at": now_iso(),
            "result": report,
        }

        # Only use fields whose existence is verified by your schema.
        # Artifact URLs are stored inside result to avoid assuming
        # a dedicated download_url column exists.
        report["artifacts"] = artifact_paths
        fields["result"] = report

        rows = self.db.patch_job(
            self.job_id,
            fields,
            only_if_status="extracting",
        )

        if not rows:
            raise SupabaseError(
                "Final status update matched no extracting job. "
                "Check whether another process changed its status "
                "or your database schema uses different statuses."
            )

        self.progress = 100

        print(
            "\nSUCCESS: Real processing and final DB update completed.",
            flush=True,
        )

    def fail(self, error: BaseException) -> None:
        for item in self.steps:
            if item["state"] == "running":
                item["state"] = "failed"

        message = safe_text(error, 400)

        try:
            self.db.patch_job(
                self.job_id,
                {
                    "status": "failed",
                    "steps": self.steps,
                    "step": "Failed",
                    "error_note": message,
                    "heartbeat_at": now_iso(),
                },
            )
        except Exception as db_error:
            print(
                f"CRITICAL: Could not record job failure: "
                f"{safe_text(db_error, 300)}",
                flush=True,
            )

        print(
            f"\nJOB FAILED: {message}",
            flush=True,
        )


# ============================================================
# DEPENDENCY CHECKS
# ============================================================

def find_hf_command() -> str | None:
    return shutil.which(CFG.hf_command)


def find_quantize_command() -> str | None:
    candidate = CFG.quantize_command

    if Path(candidate).is_file():
        return str(Path(candidate).resolve())

    return shutil.which(candidate)


def check_environment(db: Supabase) -> list[str]:
    problems: list[str] = []

    print("\n=== STHAMLY NANOBRAIN WORKER CHECK ===\n")

    try:
        db.check_config()
        print("OK: Supabase URL and service-role key configured.")
    except SupabaseError as exc:
        problems.append(str(exc))
        print(f"FAIL: {exc}")

    if find_hf_command():
        print(f"OK: Hugging Face CLI: {find_hf_command()}")
    else:
        problems.append(
            f"Hugging Face CLI not found: {CFG.hf_command}"
        )
        print("FAIL: Hugging Face CLI not found.")

    converter = Path(CFG.converter)

    if converter.exists() and converter.is_file():
        print(f"OK: GGUF converter: {converter}")
    else:
        problems.append(
            f"GGUF converter not found: {converter}"
        )
        print(f"FAIL: GGUF converter not found: {converter}")

    quantize = find_quantize_command()

    if quantize:
        print(f"OK: llama-quantize: {quantize}")
    else:
        problems.append(
            f"llama-quantize not found: {CFG.quantize_command}"
        )
        print("FAIL: llama-quantize not found.")

    if CFG.audit_script.is_file():
        print(f"OK: Audit script: {CFG.audit_script}")
    else:
        problems.append(
            f"Audit script not found: {CFG.audit_script}"
        )
        print(f"FAIL: Audit script not found: {CFG.audit_script}")

    if CFG.dataset.is_file() and CFG.dataset.stat().st_size > 0:
        print(f"OK: Hindi test dataset: {CFG.dataset}")
    else:
        problems.append(
            f"Hindi test dataset missing or empty: {CFG.dataset}"
        )
        print(
            "FAIL: Hindi test dataset missing or empty."
        )

    if CFG.storage_bucket:
        print(
            f"OK: Storage bucket configured: "
            f"{CFG.storage_bucket}"
        )
    else:
        problems.append(
            "SUPABASE_STORAGE_BUCKET is missing."
        )
        print("FAIL: SUPABASE_STORAGE_BUCKET is missing.")

    print(f"\nPython: {sys.version.split()[0]}")
    print(f"Project root: {CFG.root}")
    print(f"Threads: {CFG.threads}")

    if problems:
        print(
            f"\nCHECK FAILED: {len(problems)} issue(s) found."
        )
        return problems

    print("\nCHECK PASSED: Required local files/configuration exist.")
    print(
        "Note: This check does not prove that model conversion, "
        "GPU/CPU capacity, database schema, or a full job works."
    )

    return []


# ============================================================
# MODEL VALIDATION AND PROCESSING
# ============================================================

def validate_model_id(job: dict[str, Any]) -> str:
    model_id = str(
        job.get("hf_model_identifier")
        or job.get("hf_model")
        or ""
    ).strip()

    if not model_id:
        raise Refused(
            "Job is missing hf_model_identifier."
        )

    # Expected Hugging Face namespace/model form.
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/"
        r"[A-Za-z0-9][A-Za-z0-9._-]*",
        model_id,
    ):
        raise Refused(
            "Model ID must use namespace/model format."
        )

    return model_id


def download_model(
    model_id: str,
    destination: Path,
) -> Path:
    hf = find_hf_command()

    if not hf:
        raise WorkerError(
            "Hugging Face CLI is not installed."
        )

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    command = [
        hf,
        "download",
        model_id,
        "--local-dir",
        str(destination),
    ]

    # Hugging Face access token, if configured, is read from
    # the environment by the Hugging Face CLI.
    run_command(
        command,
        timeout=CFG.prepare_timeout,
    )

    if not any(destination.iterdir()):
        raise WorkerError(
            "Hugging Face download directory is empty."
        )

    return destination


def convert_to_fp16(
    model_dir: Path,
    fp16_path: Path,
) -> Path:
    converter = Path(CFG.converter)

    if not converter.is_file():
        raise WorkerError(
            f"Converter script not found: {converter}"
        )

    fp16_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_command(
        [
            sys.executable,
            str(converter),
            str(model_dir),
            "--outfile",
            str(fp16_path),
            "--outtype",
            "f16",
        ],
        timeout=CFG.prepare_timeout,
    )

    require_file(
        fp16_path,
        "FP16 GGUF artifact",
    )

    size_gb = file_size_gb(fp16_path)

    if size_gb > CFG.max_weights_gb:
        raise Refused(
            f"FP16 GGUF is {size_gb} GB; configured maximum "
            f"is {CFG.max_weights_gb} GB. Increase the limit "
            "only after checking available disk space."
        )

    return fp16_path


def quantize_model(
    fp16_path: Path,
    quant_path: Path,
) -> Path:
    quantizer = find_quantize_command()

    if not quantizer:
        raise WorkerError(
            "llama-quantize binary was not found."
        )

    quant_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_command(
        [
            quantizer,
            str(fp16_path),
            str(quant_path),
            "Q4_K_M",
        ],
        timeout=CFG.prepare_timeout,
    )

    require_file(
        quant_path,
        "Q4_K_M GGUF artifact",
    )

    return quant_path


def run_real_audit(
    model_id: str,
    fp16_path: Path,
    quant_path: Path,
    job_dir: Path,
) -> Path:
    """
    Invoke scripts/audit.py using the CLI discussed earlier.

    The audit script must produce report.json.
    No metrics are generated by this worker.
    """
    audit_script = CFG.audit_script

    require_file(
        audit_script,
        "Audit script",
    )

    command = [
        sys.executable,
        str(audit_script),
        "--name",
        model_id,
        "--fp16",
        str(fp16_path),
        "--dataset",
        f"hindi={CFG.dataset}",
        "--quant",
        f"q4={quant_path}",
    ]

    run_command(
        command,
        timeout=CFG.audit_timeout,
        cwd=CFG.root,
    )

    slug = safe_model_name(model_id).lower()

    report_path = (
        CFG.reports_dir
        / slug
        / "report.json"
    )

    if not report_path.is_file():
        raise WorkerError(
            "Audit command returned success, but report.json "
            f"was not created at {report_path}."
        )

    try:
        report = json.loads(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError(
            "Audit report is unreadable or invalid JSON."
        ) from exc

    if not isinstance(report, dict):
        raise WorkerError(
            "Audit report must be a JSON object."
        )

    # Require evidence that the report contains real run results.
    results = report.get("results")

    if not isinstance(results, list) or not results:
        raise WorkerError(
            "Audit report has no non-empty results array."
        )

    if not any(
        isinstance(item, dict)
        and item.get("kld") is not None
        for item in results
    ):
        raise WorkerError(
            "Audit report contains no real KLD result. "
            "Job will not be marked completed."
        )

    return report_path


# ============================================================
# FULL JOB PIPELINE
# ============================================================

def process_job(
    job: dict[str, Any],
    db: Supabase,
) -> None:
    job_id = str(job.get("id", "")).strip()

    if not job_id:
        raise WorkerError(
            "Cannot process a job without an ID."
        )

    state = JobState(db, job_id)

    try:
        # ----------------------------------------------------
        # Step 1: Validate input and dependencies
        # ----------------------------------------------------
        state.start(0, 5)

        model_id = validate_model_id(job)

        if not CFG.dataset.is_file():
            raise Refused(
                f"Hindi dataset does not exist: {CFG.dataset}"
            )

        if CFG.dataset.stat().st_size == 0:
            raise Refused(
                "Hindi dataset is empty."
            )

        model_slug = safe_model_name(model_id)

        job_dir = (
            CFG.work_dir
            / job_id
        )

        model_dir = (
            job_dir
            / "hf-model"
        )

        artifact_dir = (
            CFG.models_dir
            / model_slug
        )

        artifact_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        state.done(0)

        # ----------------------------------------------------
        # Step 2: Download actual Hugging Face weights
        # ----------------------------------------------------
        state.start(1, 10)

        download_model(
            model_id,
            model_dir,
        )

        state.done(1)

        # ----------------------------------------------------
        # Step 3: Convert to FP16 GGUF
        # ----------------------------------------------------
        state.start(2, 30)

        fp16_path = (
            artifact_dir
            / f"{model_slug}-f16.gguf"
        )

        convert_to_fp16(
            model_dir,
            fp16_path,
        )

        state.done(2)

        # ----------------------------------------------------
        # Step 4: Quantize to Q4_K_M
        # ----------------------------------------------------
        state.start(3, 55)

        quant_path = (
            artifact_dir
            / f"{model_slug}-Q4_K_M.gguf"
        )

        quantize_model(
            fp16_path,
            quant_path,
        )

        state.done(3)

        # ----------------------------------------------------
        # Step 5: Run real audit
        # ----------------------------------------------------
        state.start(4, 75)

        report_path = run_real_audit(
            model_id,
            fp16_path,
            quant_path,
            job_dir,
        )

        report = json.loads(
            report_path.read_text(encoding="utf-8")
        )

        state.done(4)

        # ----------------------------------------------------
        # Step 6: Upload artifacts
        # ----------------------------------------------------
        state.start(5, 90)

        # Use job-specific paths so concurrent jobs cannot
        # overwrite each other's output in Storage.
        storage_prefix = (
            f"jobs/{job_id}/{model_slug}"
        )

        fp16_object = (
            f"{storage_prefix}/{fp16_path.name}"
        )

        quant_object = (
            f"{storage_prefix}/{quant_path.name}"
        )

        report_object = (
            f"{storage_prefix}/report.json"
        )

        db.upload_file(
            fp16_path,
            fp16_object,
            "application/octet-stream",
        )

        db.upload_file(
            quant_path,
            quant_object,
            "application/octet-stream",
        )

        db.upload_file(
            report_path,
            report_object,
            "application/json",
        )

        artifact_paths = {
            "fp16_object": fp16_object,
            "quantized_object": quant_object,
            "report_object": report_object,
            "storage_bucket": CFG.storage_bucket,
        }

        report["model_id"] = model_id
        report["job_id"] = job_id
        report["generated_at"] = now_iso()

        report["artifacts_metadata"] = {
            "fp16_size_gb": file_size_gb(fp16_path),
            "quantized_size_gb": file_size_gb(quant_path),
            "quantization": "Q4_K_M",
        }

        # ----------------------------------------------------
        # Final state update
        # ----------------------------------------------------
        state.complete(
            report,
            artifact_paths,
        )

        # Keep artifacts by default. Model files can be huge.
        # Automatic deletion should be implemented only after
        # confirming Storage upload, download policy, and
        # retention requirements.
        if not CFG.keep_files:
            # Remove temporary HF source weights only.
            # Do not remove the generated artifacts/reports.
            shutil.rmtree(
                model_dir,
                ignore_errors=True,
            )

    except Exception as exc:
        state.fail(exc)
        raise


# ============================================================
# WORKER LOOP
# ============================================================

def run_once(
    db: Supabase,
    requested_job_id: str | None = None,
) -> bool:
    """
    Process at most one job.
    Returns True if a job was claimed and attempted.
    """
    if requested_job_id:
        rows = db.get_rows(
            "quantization_jobs",
            {
                "select": "*",
                "id": f"eq.{requested_job_id}",
                "status": "eq.pending",
                "limit": "1",
            },
        )
    else:
        rows = db.fetch_pending_jobs(limit=10)

    if not rows:
        print(
            "No pending job found.",
            flush=True,
        )
        return False

    for candidate in rows:
        claimed = db.claim_job(candidate)

        if claimed is None:
            print(
                f"Job {candidate.get('id')} was claimed "
                "by another worker. Trying the next candidate.",
                flush=True,
            )
            continue

        try:
            process_job(
                claimed,
                db,
            )
        except Exception as exc:
            print(
                f"Job attempt failed: {safe_text(exc, 400)}",
                flush=True,
            )

        return True

    print(
        "No job could be claimed.",
        flush=True,
    )

    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sthamly NanoBrain Worker"
    )

    parser.add_argument(
        "--check",
        action="store_true",
        help="Check required configuration and local tools.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Attempt at most one pending job and exit.",
    )

    parser.add_argument(
        "--job-id",
        default=None,
        help="Process a specific pending job UUID.",
    )

    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=CFG.poll_seconds,
        help="Queue polling interval in seconds.",
    )

    args = parser.parse_args()

    db = Supabase()

    if args.check:
        problems = check_environment(db)
        return 1 if problems else 0

    try:
        db.check_config()
    except SupabaseError as exc:
        print(
            f"Configuration error: {exc}",
            file=sys.stderr,
        )
        return 2

    if args.job_id and not args.once:
        parser.error(
            "--job-id requires --once."
        )

    if args.job_id:
        try:
            run_once(
                db,
                requested_job_id=args.job_id,
            )
        except Exception as exc:
            print(
                f"Worker error: {safe_text(exc, 400)}",
                file=sys.stderr,
            )
            return 1

        return 0

    if args.once:
        try:
            run_once(db)
        except Exception as exc:
            print(
                f"Worker error: {safe_text(exc, 400)}",
                file=sys.stderr,
            )
            return 1

        return 0

    poll_seconds = max(
        3,
        min(300, args.poll_seconds),
    )

    print(
        "STHAMLY NANOBRAIN WORKER STARTED\n"
        f"Project: {CFG.root}\n"
        f"Queue polling: every {poll_seconds} seconds\n"
        "Press Ctrl+C to stop.",
        flush=True,
    )

    while True:
        try:
            run_once(db)
        except KeyboardInterrupt:
            print(
                "\nWorker stopped by user.",
                flush=True,
            )
            return 0
        except Exception as exc:
            print(
                f"Queue polling error: {safe_text(exc, 400)}",
                file=sys.stderr,
                flush=True,
            )

        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print(
                "\nWorker stopped by user.",
                flush=True,
            )
            return 0


if __name__ == "__main__":
    raise SystemExit(main())