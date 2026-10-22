#!/usr/bin/env python3
"""
Sthamly NanoBrain Admin CLI (v2)

Commands:
    python scripts/admin.py enqueue --model owner/model-name
    python scripts/admin.py enqueue --model https://huggingface.co/owner/model-name
    python scripts/admin.py jobs
    python scripts/admin.py jobs --status failed --limit 50
    python scripts/admin.py license --profile lawyer
    python scripts/admin.py license --profile lawyer --job UUID --max-devices 2 --note "pilot-1"
    python scripts/admin.py revoke --prompt-key
    python scripts/admin.py revoke --hash <64-char-sha256>

Required environment (same as worker.py):
    NEXT_PUBLIC_SUPABASE_URL   (SUPABASE_URL is also accepted)
    SUPABASE_SERVICE_ROLE_KEY

Database expectations (must match worker.py):
    quantization_jobs: id, hf_model_identifier, status, progress, step,
                       steps, error_note, requester_hash, result,
                       heartbeat_at, created_at
    licenses:          key_hash (unique), profile, max_devices, revoked,
                       note, job_id, created_at

Recommended DB guard (blocks duplicate active jobs even under race):
    create unique index quantization_jobs_one_active_model
      on quantization_jobs (lower(hf_model_identifier))
      where status in ('pending', 'extracting');

Never expose the service-role key to browser code.
Never store plaintext activation keys in the database.
"""

from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sthamly_common import (  # noqa: E402
    STEP_LABELS,
    TABLE_JOBS,
    TABLE_LICENSES,
    Supabase,
    SupabaseError,
    generate_key,
    hash_key,
    normalize_key,
    normalize_model_id,
    scrub,
    valid_key_shape,
)


# ============================================================
# CONSTANTS
# ============================================================

PROFILES = ("general", "lawyer", "finance", "student")

# Must match the status names used by worker.py.
JOB_STATUSES = ("pending", "extracting", "completed", "failed", "cancelled")
ACTIVE_STATUSES = ("pending", "extracting")

DEFAULT_JOB_LIMIT = 20
MAX_JOB_LIMIT = 100
MAX_DEVICES_LIMIT = 10
MAX_NOTE_LENGTH = 300

ADMIN_REQUESTER = "admin"

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ============================================================
# VALIDATION
# ============================================================

def valid_uuid(value: str | None) -> bool:
    return bool(value) and UUID_RE.fullmatch(value.strip()) is not None


def validate_limit(value: int) -> int:
    if not 1 <= value <= MAX_JOB_LIMIT:
        raise ValueError(f"--limit 1 se {MAX_JOB_LIMIT} ke beech hona chahiye.")
    return value


def validate_profile(value: str) -> str:
    if value not in PROFILES:
        raise ValueError("Invalid profile. Allowed: " + ", ".join(PROFILES))
    return value


def validate_max_devices(value: int) -> int:
    if not 1 <= value <= MAX_DEVICES_LIMIT:
        raise ValueError(f"--max-devices 1 se {MAX_DEVICES_LIMIT} ke beech hona chahiye.")
    return value


def validate_note(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if len(value) > MAX_NOTE_LENGTH:
        raise ValueError(f"--note maximum {MAX_NOTE_LENGTH} characters ka ho sakta hai.")
    return value or None


def short(value: Any, limit: int = 200) -> str:
    """Single-line text for terminal output."""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ============================================================
# ENQUEUE
# ============================================================

def cmd_enqueue(db: Supabase, args: argparse.Namespace) -> int:
    """
    Add a calibration job for a Hugging Face model.
    Accepts owner/model or a huggingface.co URL.
    """
    model = normalize_model_id(args.model)
    if model is None:
        print("Model ID sahi nahi hai. Example: owner/model-name")
        return 2

    # Duplicate check in Python (case-insensitive, no LIKE wildcard issues).
    active_rows = db.select(
        f"{TABLE_JOBS}"
        "?select=id,hf_model_identifier,status"
        f"&status=in.({','.join(ACTIVE_STATUSES)})"
        "&limit=200"
    )
    for row in active_rows:
        existing = normalize_model_id(str(row.get("hf_model_identifier", "")))
        if existing and existing.casefold() == model.casefold():
            print(f"Ye model pehle se active queue mein hai: job {short(row.get('id'))}")
            return 1

    record = {
        "hf_model_identifier": model,
        "requester_hash": ADMIN_REQUESTER,
        "status": "pending",
        "step": "Queued",
        "progress": 0,
        "steps": [{"label": label, "state": "pending"} for label in STEP_LABELS],
        "result": None,
        "error_note": None,
    }

    try:
        rows = db.insert(TABLE_JOBS, record)
    except SupabaseError as exc:
        # The unique index may reject a race-condition duplicate.
        print(f"Job create nahi hua: {scrub(exc, limit=300)}")
        return 1

    if not rows or not rows[0].get("id"):
        print("Job create hone ki confirmation nahi mili. Database verify karo, dobara enqueue mat karo.")
        return 1

    job_id = rows[0]["id"]
    print("Calibration job queue mein add ho gaya.")
    print(f"Model:  {model}")
    print(f"Job ID: {job_id}")
    print("Worker chalane ke liye: python scripts/worker.py")
    return 0


# ============================================================
# JOB LISTING
# ============================================================

def cmd_jobs(db: Supabase, args: argparse.Namespace) -> int:
    try:
        limit = validate_limit(args.limit)
    except ValueError as exc:
        print(str(exc))
        return 2

    query = (
        f"{TABLE_JOBS}"
        "?select=id,hf_model_identifier,status,progress,error_note,created_at"
        "&order=created_at.desc"
        f"&limit={limit}"
    )
    if args.status:
        query += f"&status=eq.{args.status}"

    rows = db.select(query)
    if not rows:
        print("Koi matching job nahi mila.")
        return 0

    for row in rows:
        try:
            progress = max(0, min(100, int(row.get("progress") or 0)))
        except (TypeError, ValueError):
            progress = 0

        job_id = short(row.get("id"), 40)
        status = short(row.get("status"), 20)
        model = short(row.get("hf_model_identifier"), 120)

        print(f"{job_id}  {status:<11} {progress:3d}%  {model}")

        if row.get("error_note"):
            note = scrub(row["error_note"], extra_secrets=(db.key,), limit=200)
            print(f"    [{note}]")

    return 0


# ============================================================
# LICENSE CREATION
# ============================================================

def cmd_license(db: Supabase, args: argparse.Namespace) -> int:
    """
    Create an activation key. Only the SHA-256 hash goes to the database.
    The plaintext key is printed once, after the insert is confirmed.
    """
    profile = validate_profile(args.profile)
    max_devices = validate_max_devices(args.max_devices)
    note = validate_note(args.note)

    if args.job and not valid_uuid(args.job):
        print("--job ek valid UUID hona chahiye.")
        return 2

    key = generate_key()

    record: dict[str, Any] = {
        "key_hash": hash_key(key),
        "profile": profile,
        "max_devices": max_devices,
        "revoked": False,
        "note": note,
    }
    if args.job:
        record["job_id"] = args.job.strip().lower()

    rows = db.insert(TABLE_LICENSES, record)
    if not rows:
        print("License creation confirm nahi hui. Is generated key ko valid mat samjho.")
        return 1

    print("\nLicense successfully create ho gayi.")
    print(f"Profile:        {profile}")
    print(f"Maximum devices: {max_devices}")
    if args.job:
        print(f"Associated job: {args.job}")

    print("\nActivation key (abhi securely copy kar lo, dobara nahi dikhegi):")
    print(key)
    print("\nDatabase mein sirf SHA-256 hash store hua hai.")
    return 0


# ============================================================
# LICENSE REVOCATION
# ============================================================

def resolve_revoke_hash(args: argparse.Namespace) -> str | None:
    """Return the lookup hash, or None if input is invalid (message already printed)."""
    if args.hash:
        candidate = args.hash.strip().lower()
        if not SHA256_RE.fullmatch(candidate):
            print("--hash mein 64 hexadecimal characters hone chahiye.")
            return None
        return candidate

    if args.key:
        plaintext = args.key
    else:
        plaintext = getpass.getpass("Activation key paste karo (input hidden hai): ")

    if not valid_key_shape(plaintext):
        print("License key ka format invalid hai. Expected: STH-XXXX-XXXX-XXXX-XXXX")
        return None

    # Normalize first so case and spacing differences never break revoke.
    return hash_key(normalize_key(plaintext))


def cmd_revoke(db: Supabase, args: argparse.Namespace) -> int:
    lookup_hash = resolve_revoke_hash(args)
    if lookup_hash is None:
        return 2

    changed = db.update(TABLE_LICENSES, {"key_hash": lookup_hash}, {"revoked": True})
    if not changed:
        print("Is key/hash se koi license record nahi mila.")
        return 1

    print("License revoke ho gayi.")
    print(f"Updated records: {len(changed)}")
    return 0


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sthamly NanoBrain Admin CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    enqueue = subparsers.add_parser("enqueue", help="Calibration job queue mein add karo.")
    enqueue.add_argument("--model", required=True, help="Hugging Face model ID ya URL.")
    enqueue.set_defaults(fn=cmd_enqueue)

    lic = subparsers.add_parser("license", help="Activation license create karo.")
    lic.add_argument("--profile", required=True, choices=PROFILES)
    lic.add_argument("--job", help="Optional associated job UUID.")
    lic.add_argument("--max-devices", type=int, default=2, metavar="1-10")
    lic.add_argument("--note", help="Optional internal operator note (max 300 chars).")
    lic.set_defaults(fn=cmd_license)

    rev = subparsers.add_parser("revoke", help="Activation license revoke karo.")
    group = rev.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", help="Plaintext key (shell history mein save ho sakti hai).")
    group.add_argument("--prompt-key", action="store_true", dest="prompt_key",
                       help="Key hidden prompt mein paste karo (recommended).")
    group.add_argument("--hash", help="64-character SHA-256 hash.")
    rev.set_defaults(fn=cmd_revoke)

    jobs = subparsers.add_parser("jobs", help="Recent jobs list karo.")
    jobs.add_argument("--status", choices=JOB_STATUSES)
    jobs.add_argument("--limit", type=int, default=DEFAULT_JOB_LIMIT)
    jobs.set_defaults(fn=cmd_jobs)

    return parser


def main(argv: list[str] | None = None, db: Supabase | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --prompt-key sets args.key to None so resolve_revoke_hash prompts.
    if getattr(args, "prompt_key", False):
        args.key = None

    try:
        database = db or Supabase.from_env()
        return int(args.fn(database, args))
    except SupabaseError as exc:
        print("Supabase error: " + scrub(exc, limit=300), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nOperation cancel kar di gayi.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())