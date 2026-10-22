#!/usr/bin/env python3
"""
Sthamly NanoBrain: compress one model end-to-end, for MANUAL testing.

Production runs should go through scripts/worker.py (job queue). This script
does the same steps for a single model, and never writes fake metrics.

Pipeline:
    prepare (download, FP16 GGUF, plain Q4)  -> Hindi imatrix -> Hindi Q4
    -> audit (real llama-perplexity KLD)     -> record result in quantization_jobs

Usage:
    python scripts/compress_and_log.py --model TinyLlama/TinyLlama-1.1B-Chat-v1.0
    python scripts/compress_and_log.py --model org/model --dry-run

Status is set to "completed" ONLY after the audit report exists and is valid.
The KLD value stored in the DB is read from the report, never typed in.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local")  # .env.local overrides .env if both exist
except ImportError:
    pass

from sthamly_common import (  # noqa: E402
    STEP_LABELS,
    TABLE_JOBS,
    Supabase,
    SupabaseError,
    normalize_model_id,
    scrub,
)


LLAMA_DIR = ROOT / "llama.cpp"
BIN = LLAMA_DIR / "build" / "bin"
IMATRIX = BIN / "llama-imatrix"
QUANTIZE = BIN / "llama-quantize"
CALIB_FILE = ROOT / "data" / "hindi_calib.txt"
TEST_FILE = ROOT / "data" / "hindi_test.txt"

IMATRIX_CHUNKS = int(os.getenv("IMATRIX_CHUNKS", "60"))
IMATRIX_TIMEOUT = 7200
AUDIT_TIMEOUT = 10800


class StepError(RuntimeError):
    pass


def run_streaming(command: list[Any], timeout: int, label: str) -> None:
    """Run a real command with live output and a hard timeout."""
    cmd = [str(part) for part in command]
    print(f"\n$ {' '.join(cmd)}", flush=True)
    try:
        completed = subprocess.run(cmd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise StepError(f"{label} timed out after {timeout} seconds.") from exc
    except OSError as exc:
        raise StepError(f"{label} could not start: {exc}") from exc
    if completed.returncode != 0:
        raise StepError(f"{label} failed with exit code {completed.returncode}.")


def require_tool(path: Path, label: str) -> None:
    if not path.is_file():
        raise StepError(f"{label} not found: {path}. Run the llama.cpp build first.")


def build_hindi_imatrix(fp16: Path, out: Path) -> None:
    require_tool(IMATRIX, "llama-imatrix")
    if not CALIB_FILE.is_file() or CALIB_FILE.stat().st_size == 0:
        raise StepError("data/hindi_calib.txt missing. Run scripts/download_hindi_data.py first.")
    if out.exists():
        print(f"  imatrix already exists, reusing: {out.name}")
        return
    run_streaming(
        [IMATRIX, "-m", fp16, "-f", CALIB_FILE, "-o", out,
         "--chunks", str(IMATRIX_CHUNKS)],
        IMATRIX_TIMEOUT,
        "llama-imatrix",
    )
    if not out.is_file() or out.stat().st_size == 0:
        raise StepError("llama-imatrix finished but produced no file.")


def build_hindi_quant(fp16: Path, imatrix: Path, out: Path) -> None:
    require_tool(QUANTIZE, "llama-quantize")
    part = out.with_name(out.name + ".part")
    part.unlink(missing_ok=True)
    try:
        run_streaming(
            [QUANTIZE, "--imatrix", imatrix, fp16, part, "Q4_K_M"],
            3600,
            "llama-quantize (Hindi)",
        )
        if not part.is_file() or part.stat().st_size == 0:
            raise StepError("Hindi Q4 was not written.")
        os.replace(part, out)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def run_real_audit(model_id: str, fp16: Path, std_q4: Path, hindi_q4: Path) -> Path:
    """Calls scripts/audit.py. The report is the only source of numbers."""
    audit = SCRIPT_DIR / "audit.py"
    if not audit.is_file():
        raise StepError("scripts/audit.py not found.")

    report_dir = ROOT / "reports" / (
        model_id.replace("/", "_").replace(" ", "_").strip("._-").lower()
    )
    report_path = report_dir / "report.json"

    run_streaming(
        [sys.executable, audit,
         "--name", model_id,
         "--fp16", fp16,
         "--dataset", f"hindi={TEST_FILE}",
         "--quant", f"q4_standard={std_q4}",
         "--quant", f"q4_hindi={hindi_q4}",
         "--calibration", CALIB_FILE,
         "--out", report_path],
        AUDIT_TIMEOUT,
        "audit.py",
    )

    if not report_path.is_file():
        raise StepError(f"Audit finished but no report at {report_path}.")
    return report_path


def read_kld_from_report(report_path: Path) -> dict[str, float]:
    """Extract real KLD per variant. Fails if the report has no valid numbers."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "complete":
        raise StepError(f"Audit report status is {report.get('status')!r}, not 'complete'.")

    values: dict[str, float] = {}
    for item in report.get("results", []):
        if item.get("domain") == "hindi" and "kld" in item:
            value = item["kld"]
            if not isinstance(value, (int, float)) or value != value or value < 0:
                raise StepError(f"Invalid KLD for {item.get('variant')}: {value!r}")
            values[item["variant"]] = float(value)

    if "q4_hindi" not in values or "q4_standard" not in values:
        raise StepError("Report is missing KLD for q4_hindi or q4_standard.")
    return values


def log_to_supabase(db: Supabase, model_id: str, report_path: Path, kld: dict[str, float]) -> str:
    """Insert ONE row for this run. The row reflects the real report."""
    record = {
        "hf_model_identifier": model_id,
        "requester_hash": "manual-compress",
        "status": "completed",
        "step": "Completed",
        "progress": 100,
        "steps": [{"label": label, "state": "done"} for label in STEP_LABELS],
        "kld_score_before": kld["q4_standard"],
        "kld_score_after": kld["q4_hindi"],
        "result": {
            "report_path": str(report_path.relative_to(ROOT)),
            "kld_standard": kld["q4_standard"],
            "kld_hindi": kld["q4_hindi"],
            "source": "audit.py report",
        },
        "error_note": None,
    }
    rows = db.insert(TABLE_JOBS, record)
    if not rows or not rows[0].get("id"):
        raise StepError("Supabase did not confirm the insert. Check the table and columns.")
    return str(rows[0]["id"])


def compress(model_id: str, hf_token: str | None, dry_run: bool) -> int:
    from prepare_model import PrepareError, JobReporter, prepare  # reuse, don't duplicate

    model_id = normalize_model_id(model_id)
    if model_id is None:
        print("Invalid model ID. Use owner/name or a huggingface.co URL.", file=sys.stderr)
        return 2

    if dry_run:
        print(f"DRY RUN: would process {model_id}")
        print("  1. prepare (download, FP16, plain Q4)")
        print("  2. Hindi imatrix from data/hindi_calib.txt")
        print("  3. Hindi Q4_K_M")
        print("  4. audit.py (real llama-perplexity)")
        print("  5. insert one quantization_jobs row from the report")
        return 0

    reporter = JobReporter(job_id=None)  # manual run: no job row to update
    try:
        result = prepare(model_id, hf_token, reporter)
    except PrepareError as exc:
        print(f"Prepare failed: {exc}", file=sys.stderr)
        return exc.code

    name = model_id.replace("/", "__")
    fp16 = Path(result["fp16_path"])
    std_q4 = Path(result["standard_q4_path"])
    hindi_q4 = ROOT / "models" / "gguf" / f"{name}-Q4_K_M-hindi.gguf"
    imatrix = ROOT / "models" / "gguf" / f"{name}.hindi.imatrix"

    try:
        print("\n[3/5] Hindi imatrix")
        build_hindi_imatrix(fp16, imatrix)

        print("\n[4/5] Hindi Q4_K_M")
        build_hindi_quant(fp16, imatrix, hindi_q4)

        print("\n[5/5] Real audit")
        report_path = run_real_audit(model_id, fp16, std_q4, hindi_q4)
        kld = read_kld_from_report(report_path)
        print(f"Measured KLD (from report): standard={kld['q4_standard']:.6f} "
              f"hindi={kld['q4_hindi']:.6f}")

        db = Supabase.from_env()
        job_id = log_to_supabase(db, model_id, report_path, kld)
        print(f"Logged job {job_id}. Report: {report_path}")
        return 0

    except (StepError, SupabaseError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAILED: {scrub(exc, limit=400)}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual end-to-end compression (no fake metrics).")
    parser.add_argument("--model", required=True)
    parser.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    parser.add_argument("--dry-run", action="store_true", help="Print the plan, change nothing.")
    args = parser.parse_args()
    return compress(args.model, args.hf_token, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())