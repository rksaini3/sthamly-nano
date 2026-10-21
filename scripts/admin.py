#!/usr/bin/env python3
"""
Sthamly NanoBrain Admin CLI

Commands:
    python scripts/admin.py enqueue --model owner/model-name
    python scripts/admin.py jobs
    python scripts/admin.py jobs --status failed
    python scripts/admin.py license --profile lawyer
    python scripts/admin.py license --profile lawyer --job UUID
    python scripts/admin.py revoke --key STH-XXXX-XXXX-XXXX-XXXX
    python scripts/admin.py revoke --hash <64-character-sha256>

Required environment:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Database tables expected by this CLI:
    jobs
    licenses

Never expose the Supabase service-role key to browser code.
Never store plaintext activation keys in the database.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse

from pathlib import Path
from typing import Any


# ============================================================
# IMPORTS
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sthamly_common import (  # noqa: E402
    Supabase,
    SupabaseError,
    generate_key,
    hash_key,
    normalize_key,
    normalize_model_id,
    valid_key_shape,
    scrub,
)


# ============================================================
# CONSTANTS
# ============================================================

PROFILES = (
    "general",
    "lawyer",
    "finance",
    "student",
)

STATUSES = (
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
)

ACTIVE_STATUSES = (
    "queued",
    "running",
)

DEFAULT_JOB_LIMIT = 20
MAX_JOB_LIMIT = 100

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{12}$",
    re.IGNORECASE,
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# Keep these labels consistent with the worker.
QUEUED_STEPS = [
    "Model ki jaankari check ho rahi hai",
    "Model download aur FP16 GGUF ban raha hai",
    "Hindi calibration data taiyaar ho raha hai",
    "Hindi / English / Mixed imatrix ban rahe hain aur naape ja rahe hain",
    "Best variant chuna ja raha hai",
]


# ============================================================
# VALIDATION
# ============================================================

def valid_uuid(value: str | None) -> bool:
    if not value:
        return False

    return UUID_RE.fullmatch(value.strip()) is not None


def validate_limit(value: int) -> int:
    if not 1 <= value <= MAX_JOB_LIMIT:
        raise ValueError(
            f"--limit 1 se {MAX_JOB_LIMIT} ke beech hona chahiye."
        )

    return value


def validate_profile(value: str) -> str:
    if value not in PROFILES:
        raise ValueError(
            "Invalid profile. Allowed: "
            + ", ".join(PROFILES)
        )

    return value


def validate_max_devices(value: int) -> int:
    if not 1 <= value <= 10:
        raise ValueError(
            "--max-devices 1 se 10 ke beech hona chahiye."
        )

    return value


def validate_note(value: str | None) -> str | None:
    if value is None:
        return None

    value = " ".join(value.split())

    if len(value) > 300:
        raise ValueError("--note maximum 300 characters ka ho sakta hai.")

    return value or None


def safe_print_value(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").split())

    if len(text) > limit:
        return text[:limit - 3] + "..."

    return text


# ============================================================
# JOB ENQUEUE
# ============================================================

def cmd_enqueue(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Add a calibration job to the jobs queue.

    Schema:
        jobs.kind
        jobs.hf_model
        jobs.requester_hash
        jobs.status
        jobs.steps

    The requester_hash is a fixed operator label, not a password.
    """

    model = normalize_model_id(args.model)

    if not model:
        print(
            "Model ID sahi nahi hai. Example: owner/model-name"
        )
        return 2

    # Use an exact model comparison after normalization.
    # URL encoding prevents reserved URL characters from breaking
    # the PostgREST query.
    encoded_model = urllib.parse.quote(model, safe="")

    active_statuses = ",".join(ACTIVE_STATUSES)

    query = (
        "jobs"
        "?select=id,kind,hf_model,status"
        f"&hf_model=ilike.{encoded_model}"
        f"&status=in.({active_statuses})"
        "&limit=100"
    )

    existing_rows = db.select(query)

    # Case-insensitive duplicate detection.
    for row in existing_rows:
        existing_model = normalize_model_id(
            str(row.get("hf_model", ""))
        )

        if (
            existing_model is not None
            and existing_model.casefold() == model.casefold()
            and row.get("kind") == "calibrate"
            and row.get("status") in ACTIVE_STATUSES
        ):
            print(
                "Ye model pehle se active queue mein hai: "
                f"job {safe_print_value(row.get('id'))}"
            )
            return 1

    record = {
        "kind": "calibrate",
        "hf_model": model,
        "requester_hash": "admin",
        "status": "queued",
        "progress": 0,
        "steps": QUEUED_STEPS,
        "result": None,
        "error": None,
    }

    rows = db.insert("jobs", record)

    if not rows:
        print(
            "Job create hone ki confirmation nahi mili. "
            "Database response aur schema check karo."
        )
        return 1

    job_id = rows[0].get("id")

    if not job_id:
        print(
            "Job response mein ID nahi mili. "
            "Database verify kiye bina dobara enqueue mat karo."
        )
        return 1

    print("Calibration job successfully queue ho gaya.")
    print(f"Model: {model}")
    print(f"Job ID: {job_id}")
    print(f"Result page: /job/{job_id}")
    print("Worker: python scripts/worker.py")

    return 0


# ============================================================
# LICENSE CREATION
# ============================================================

def cmd_license(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Create a license using a SHA-256 key hash.

    Plaintext activation key is printed only after the database
    confirms that the insert succeeded.
    """

    profile = validate_profile(args.profile)
    max_devices = validate_max_devices(args.max_devices)
    note = validate_note(args.note)

    if args.job and not valid_uuid(args.job):
        print("--job ek valid UUID hona chahiye.")
        return 2

    key = generate_key()
    key_hash = hash_key(key)

    record: dict[str, Any] = {
        "key_hash": key_hash,
        "profile": profile,
        "max_devices": max_devices,
        "revoked": False,
        "note": note,
    }

    if args.job:
        record["job_id"] = args.job

    rows = db.insert("licenses", record)

    if not rows:
        print(
            "License creation confirm nahi hui. "
            "Generated key ko valid mat samjho."
        )
        return 1

    print("\nLicense successfully create ho gayi.")
    print(f"Profile: {profile}")
    print(f"Maximum devices: {max_devices}")

    if args.job:
        print(f"Associated job: {args.job}")

    print("\nActivation key — ise abhi securely copy kar lo:")
    print(key)

    print(
        "\nSecurity: database mein plaintext key nahi, "
        "sirf SHA-256 hash store hua hai."
    )

    return 0


# ============================================================
# LICENSE REVOCATION
# ============================================================

def cmd_revoke(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Revoke a license by plaintext activation key or SHA-256 hash.

    The plaintext key is normalized and hashed locally.
    The database is queried by key_hash, never by plaintext key.
    """

    if args.key:
        if not valid_key_shape(args.key):
            print(
                "License key ka format invalid hai. "
                "Expected: STH-XXXX-XXXX-XXXX-XXXX"
            )
            return 2

        lookup_hash = hash_key(args.key)

    elif args.hash:
        if not SHA256_RE.fullmatch(args.hash):
            print("--hash mein 64 hexadecimal characters hone chahiye.")
            return 2

        lookup_hash = args.hash.lower()

    else:
        print("--key ya --hash dena zaroori hai.")
        return 2

    changed = db.update(
        "licenses",
        {"key_hash": lookup_hash},
        {"revoked": True},
    )

    if not changed:
        print("Is hash se koi active license record nahi mila.")
        return 1

    print("License revoke ho gayi.")
    print(f"Updated records: {len(changed)}")

    return 0


# ============================================================
# JOB LISTING
# ============================================================

def cmd_jobs(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    List recent jobs without exposing credentials or full payloads.
    """

    try:
        limit = validate_limit(args.limit)
    except ValueError as exc:
        print(str(exc))
        return 2

    query = (
        "jobs"
        "?select=id,kind,hf_model,status,progress,error,created_at"
        "&order=created_at.desc"
        f"&limit={limit}"
    )

    if args.status:
        if args.status not in STATUSES:
            print("Invalid job status.")
            return 2

        query += (
            "&status=eq."
            + urllib.parse.quote(args.status, safe="")
        )

    rows = db.select(query)

    if not rows:
        print("Koi matching job nahi mila.")
        return 0

    for row in rows:
        job_id = safe_print_value(row.get("id"))
        kind = safe_print_value(row.get("kind"), 30)
        status = safe_print_value(row.get("status"), 20)
        model = safe_print_value(row.get("hf_model"), 120)
        progress = row.get("progress", 0)

        try:
            progress = max(0, min(100, int(progress)))
        except (ValueError, TypeError):
            progress = 0

        print(
            f"{job_id}  {status:<10} "
            f"{kind:<12} {progress:3d}%  {model}"
        )

        if row.get("error"):
            # Avoid accidentally printing known token patterns.
            error = scrub(
                row["error"],
                extra_secrets=(db.key,),
                limit=200,
            )
            print(f"    [{error}]")

    return 0


# ============================================================
# CLI ARGUMENTS
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="cmd",
        required=True,
    )

    # --------------------------------------------------------
    # enqueue
    # --------------------------------------------------------

    command = subparsers.add_parser(
        "enqueue",
        help="Calibration job queue mein add karo.",
    )

    command.add_argument(
        "--model",
        required=True,
        help="Hugging Face model ID ya URL.",
    )

    command.set_defaults(fn=cmd_enqueue)

    # --------------------------------------------------------
    # license
    # --------------------------------------------------------

    command = subparsers.add_parser(
        "license",
        help="Activation license create karo.",
    )

    command.add_argument(
        "--profile",
        required=True,
        choices=PROFILES,
    )

    command.add_argument(
        "--job",
        help="Optional associated job UUID.",
    )

    command.add_argument(
        "--max-devices",
        type=int,
        default=2,
        metavar="1-10",
    )

    command.add_argument(
        "--note",
        help="Optional internal operator note.",
    )

    command.set_defaults(fn=cmd_license)

    # --------------------------------------------------------
    # revoke
    # --------------------------------------------------------

    command = subparsers.add_parser(
        "revoke",
        help="Activation license revoke karo.",
    )

    group = command.add_mutually_exclusive_group(
        required=True,
    )

    group.add_argument(
        "--key",
        help="Plaintext activation key.",
    )

    group.add_argument(
        "--hash",
        help="64-character SHA-256 hash.",
    )

    command.set_defaults(fn=cmd_revoke)

    # --------------------------------------------------------
    # jobs
    # --------------------------------------------------------

    command = subparsers.add_parser(
        "jobs",
        help="Recent jobs list karo.",
    )

    command.add_argument(
        "--status",
        choices=STATUSES,
    )

    command.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_JOB_LIMIT,
    )

    command.set_defaults(fn=cmd_jobs)

    return parser


# ============================================================
# ENTRY POINT
# ============================================================

def main(
    argv: list[str] | None = None,
    db: Supabase | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        database = db or Supabase.from_env()
        return int(args.fn(database, args))

    except SupabaseError as exc:
        print(
            "Supabase error: "
            + scrub(str(exc), limit=300)
        )
        return 1

    except ValueError as exc:
        print(f"Invalid input: {exc}")
        return 2

    except KeyboardInterrupt:
        print("Operation cancel kar di gayi.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())