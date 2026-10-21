#!/usr/bin/env python3
"""
Sthamly NanoBrain — Admin CLI

Commands:
    python scripts/admin.py enqueue --model Qwen/Qwen2.5-1.5B-Instruct
    python scripts/admin.py jobs
    python scripts/admin.py jobs --status pending
    python scripts/admin.py license --profile lawyer --job <UUID>
    python scripts/admin.py revoke --key STH-XXXX-XXXX-XXXX-XXXX

Requirements:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Security:
    - Never print Supabase credentials.
    - Never store a plaintext activation key.
    - Validate model IDs, UUIDs, statuses, and limits.
    - Use quantization_jobs for worker jobs.
    - Store a SHA-256 hash of activation keys.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse

from pathlib import Path
from typing import Any


# ------------------------------------------------------------
# Import shared project helpers
# ------------------------------------------------------------

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
)


# ------------------------------------------------------------
# Configuration
# ------------------------------------------------------------

PROFILES = (
    "general",
    "lawyer",
    "finance",
    "student",
)

STATUSES = (
    "pending",
    "extracting",
    "benchmarking",
    "quantizing",
    "completed",
    "failed",
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{4}-"
    r"[0-9a-f]{12}$",
    re.IGNORECASE,
)

DEFAULT_JOB_LIMIT = 20
MAX_JOB_LIMIT = 100


# ------------------------------------------------------------
# Common validation
# ------------------------------------------------------------

def valid_uuid(value: str | None) -> bool:
    if not value:
        return False

    return UUID_RE.fullmatch(value.strip()) is not None


def validate_limit(value: int) -> int:
    if value < 1 or value > MAX_JOB_LIMIT:
        raise ValueError(
            f"Limit 1 se {MAX_JOB_LIMIT} ke beech hona chahiye."
        )

    return value


def validate_profile(value: str) -> str:
    if value not in PROFILES:
        raise ValueError(
            f"Invalid profile. Allowed profiles: {', '.join(PROFILES)}"
        )

    return value


def validate_device_limit(value: int) -> int:
    if value < 1 or value > 10:
        raise ValueError(
            "Max devices 1 se 10 ke beech hona chahiye."
        )

    return value


def print_error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def safe_value(value: Any, max_length: int = 180) -> str:
    """
    Restrict potentially untrusted values before printing.
    """
    text = str(value or "")
    text = " ".join(text.split())

    if len(text) > max_length:
        text = text[: max_length - 3] + "..."

    return text


# ------------------------------------------------------------
# Model queue
# ------------------------------------------------------------

def cmd_enqueue(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Add a model calibration job to the worker queue.

    The worker currently uses the quantization_jobs table and
    claims jobs with status='pending'.
    """
    model = normalize_model_id(args.model)

    if not model:
        print_error(
            "Model ID invalid hai. Example: owner/model-name"
        )
        return 2

    # URL-encode the model identifier before including it in
    # a PostgREST filter.
    encoded_model = urllib.parse.quote(
        model,
        safe="",
    )

    query = (
        "quantization_jobs"
        "?select=id,status,hf_model_identifier"
        f"&hf_model_identifier=eq.{encoded_model}"
        "&status=in.(pending,extracting,benchmarking,quantizing)"
        "&limit=1"
    )

    existing = db.select(query)

    if existing:
        row = existing[0]

        print(
            "Is model ka active job pehle se maujood hai:"
        )
        print(f"Job ID: {safe_value(row.get('id'))}")
        print(f"Status: {safe_value(row.get('status'))}")

        return 1

    rows = db.insert(
        "quantization_jobs",
        {
            "hf_model_identifier": model,
            "status": "pending",
        },
    )

    if not rows:
        print_error(
            "Database ne naya job record return nahi kiya. "
            "Schema aur INSERT permissions check karo."
        )
        return 1

    row = rows[0]
    job_id = row.get("id")

    if not job_id:
        print_error(
            "Job create hua, lekin response mein ID nahi mili. "
            "Supabase response aur schema check karo."
        )
        return 1

    print("Calibration job create ho gaya.")
    print(f"Model: {model}")
    print(f"Job ID: {safe_value(job_id)}")
    print(f"Job page: /job/{safe_value(job_id)}")
    print("Worker command: python scripts/worker.py")

    return 0


# ------------------------------------------------------------
# Activation license creation
# ------------------------------------------------------------

def cmd_license(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Generate an activation key.

    IMPORTANT:
    This function expects activation_keys.license_key_hash to
    exist and to be used by the server-side activation verifier.

    The plaintext key is printed only after a successful insert.
    """
    try:
        profile = validate_profile(args.profile)
        max_devices = validate_device_limit(args.max_devices)
    except ValueError as exc:
        print_error(str(exc))
        return 2

    if args.job and not valid_uuid(args.job):
        print_error("--job mein valid UUID dena zaroori hai.")
        return 2

    key = generate_key()
    key_hash = hash_key(key)

    record: dict[str, Any] = {
        "license_key_hash": key_hash,
        "assigned_profile": profile,
        "max_activations": max_devices,
        "is_active": True,
    }

    if args.job:
        record["associated_job_id"] = args.job

    try:
        rows = db.insert(
            "activation_keys",
            record,
        )
    except SupabaseError:
        # Do not print the generated key when the insert fails.
        # The database state is uncertain until the operation
        # has been verified.
        raise

    if not rows:
        print_error(
            "License create hone ki confirmation nahi mili. "
            "Key ko valid mat samjho; database verify karo."
        )
        return 1

    print("\nActivation license successfully create ho gayi.")
    print(f"Profile: {profile}")
    print(f"Maximum devices: {max_devices}")

    if args.job:
        print(f"Associated job: {args.job}")

    if args.note:
        print(f"Operator note: {safe_value(args.note)}")

    print("\nIMPORTANT: Is key ko abhi securely copy kar lo:")
    print(key)
    print(
        "\nPlaintext key dobara database se recover nahi ki ja sakegi."
    )

    return 0


# ------------------------------------------------------------
# Activation license revocation
# ------------------------------------------------------------

def cmd_revoke(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Revoke a key using its normalized SHA-256 hash.

    Never query the database using the plaintext activation key.
    """
    supplied_key = args.key

    if not valid_key_shape(supplied_key):
        print_error(
            "License key format invalid hai. "
            "Expected format: STH-XXXX-XXXX-XXXX-XXXX"
        )
        return 2

    normalized = normalize_key(supplied_key)

    if not normalized:
        print_error("License key normalize nahi ho saki.")
        return 2

    lookup_hash = hash_key(supplied_key)

    rows = db.update(
        "activation_keys",
        {
            "license_key_hash": lookup_hash,
        },
        {
            "is_active": False,
        },
    )

    if not rows:
        print(
            "License nahi mili, ya database ne update ko confirm nahi kiya."
        )
        print(
            "Agar schema mein hash column ka naam alag hai, "
            "to database schema verify karo."
        )
        return 1

    print("License successfully revoke ho gayi.")
    print(f"Updated records: {len(rows)}")

    return 0


# ------------------------------------------------------------
# Job listing
# ------------------------------------------------------------

def cmd_jobs(
    db: Supabase,
    args: argparse.Namespace,
) -> int:
    """
    Display recent jobs without exposing credentials or full
    result payloads.
    """
    try:
        limit = validate_limit(args.limit)
    except ValueError as exc:
        print_error(str(exc))
        return 2

    query = (
        "quantization_jobs"
        "?select=id,hf_model_identifier,status,created_at"
        "&order=created_at.desc"
        f"&limit={limit}"
    )

    if args.status:
        if args.status not in STATUSES:
            print_error("Invalid job status.")
            return 2

        query += (
            "&status=eq."
            + urllib.parse.quote(args.status, safe="")
        )

    rows = db.select(query)

    if not rows:
        print("Koi matching job nahi mila.")
        return 0

    print(
        f"{'JOB ID':36}  {'STATUS':14}  {'MODEL':40}  CREATED"
    )
    print("-" * 112)

    for row in rows:
        job_id = safe_value(row.get("id"), 36)
        status = safe_value(row.get("status"), 14)
        model = safe_value(
            row.get("hf_model_identifier"),
            40,
        )
        created_at = safe_value(
            row.get("created_at"),
            30,
        )

        print(
            f"{job_id:36}  "
            f"{status:14}  "
            f"{model:40}  "
            f"{created_at}"
        )

    return 0


# ------------------------------------------------------------
# CLI parser
# ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sthamly NanoBrain admin CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="cmd",
        required=True,
    )

    # enqueue
    command = subparsers.add_parser(
        "enqueue",
        help="Model calibration job queue mein add karo.",
    )

    command.add_argument(
        "--model",
        required=True,
        help="Hugging Face model ID or URL.",
    )

    command.set_defaults(fn=cmd_enqueue)

    # license
    command = subparsers.add_parser(
        "license",
        help="Activation license create karo.",
    )

    command.add_argument(
        "--profile",
        choices=PROFILES,
        required=True,
    )

    command.add_argument(
        "--job",
        help="Associated job UUID.",
    )

    command.add_argument(
        "--max-devices",
        type=int,
        default=2,
        choices=range(1, 11),
        metavar="1-10",
    )

    command.add_argument(
        "--note",
        help="Optional operator-only note.",
    )

    command.set_defaults(fn=cmd_license)

    # revoke
    command = subparsers.add_parser(
        "revoke",
        help="Activation license revoke karo.",
    )

    command.add_argument(
        "--key",
        required=True,
        help="Plaintext activation key to revoke.",
    )

    command.set_defaults(fn=cmd_revoke)

    # jobs
    command = subparsers.add_parser(
        "jobs",
        help="Recent jobs list karo.",
    )

    command.add_argument(
        "--status",
        choices=STATUSES,
        help="Optional status filter.",
    )

    command.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_JOB_LIMIT,
        help=f"Number of jobs to list (1-{MAX_JOB_LIMIT}).",
    )

    command.set_defaults(fn=cmd_jobs)

    return parser


# ------------------------------------------------------------
# Entry point
# ------------------------------------------------------------

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
        print_error(str(exc))
        return 1

    except KeyboardInterrupt:
        print_error("Operation user ne interrupt kar di.")
        return 130

    except SystemExit:
        raise

    except Exception as exc:
        # Avoid dumping environment variables or credentials.
        print_error(
            f"Unexpected error: {type(exc).__name__}: {exc}"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())