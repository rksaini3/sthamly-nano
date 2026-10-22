#!/usr/bin/env python3
"""
Sthamly NanoBrain admin CLI (tables `jobs` and `licenses`, schema db/schema_v3.sql + migration 0002).

    python scripts/admin.py enqueue --model owner/name            # calibrate job (full build)
    python scripts/admin.py enqueue --model owner/name --kind audit
    python scripts/admin.py jobs [--status failed] [--limit 20]
    python scripts/admin.py license --profile lawyer [--job UUID] [--max-devices 2] [--note "pilot"]
    python scripts/admin.py revoke --prompt-key                   # hidden prompt (recommended)
    python scripts/admin.py revoke --key STH-XXXX-XXXX-XXXX-XXXX  # ends up in shell history
    python scripts/admin.py revoke --hash <64-hex>
    python scripts/admin.py purge --days 90                       # remove old finished jobs

Only the SHA-256 of a license key is stored. The plaintext key is printed once.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from sthamly_common import (  # noqa: E402
    JOB_ACTIVE, JOB_KINDS, JOB_STATUSES, STEP_LABELS, TABLE_JOBS, TABLE_LICENSES,
    Supabase, SupabaseError, generate_key, hash_key, normalize_model_id,
    scrub, valid_job_id, valid_key_shape,
)

PROFILES = ("general", "lawyer", "finance", "student")
ADMIN_REQUESTER = "admin"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def find_active_duplicate(db: Supabase, model: str) -> dict[str, Any] | None:
    """
    A queued or running job for the same model, compared case-insensitively.
    HF repo ids are case-insensitive, and the DB index is on lower(hf_model), so this must match it.
    """
    rows = db.get_rows(TABLE_JOBS, {"select": "id,status,hf_model",
                                    "status": f"in.({','.join(JOB_ACTIVE)})"})
    wanted = model.lower()
    return next((r for r in rows if str(r.get("hf_model") or "").lower() == wanted), None)


def cmd_enqueue(db: Supabase, args: argparse.Namespace) -> int:
    model = normalize_model_id(args.model)
    if model is None:
        print("Model id invalid. Use owner/name or a huggingface.co model link.")
        return 2

    duplicate = find_active_duplicate(db, model)
    if duplicate:
        print(f"Ye model pehle se active hai: {duplicate['id']} ({duplicate['status']}).")
        return 1

    steps = [{"label": label, "state": "pending"} for label in STEP_LABELS[args.kind]]
    try:
        rows = db.insert(TABLE_JOBS, {"kind": args.kind, "hf_model": model,
                                      "requester_hash": ADMIN_REQUESTER, "status": "queued",
                                      "step": "Queued", "progress": 0, "steps": steps})
    except SupabaseError as exc:
        text = scrub(exc, (db.key,), 300)
        if "23505" in text:
            print("Ye model abhi kisi aur ne queue kiya hai (same time par). Jobs list dekhiye.")
        else:
            print(f"Job not created: {text}")
        return 1

    if not rows or not rows[0].get("id"):
        print("Insert not confirmed. Check the database before retrying.")
        return 1
    print(f"Queued {args.kind} job for {model}\nJob ID: {rows[0]['id']}")
    return 0


def cmd_jobs(db: Supabase, args: argparse.Namespace) -> int:
    limit = max(1, min(100, args.limit))
    params = {"select": "id,kind,hf_model,status,progress,error,created_at",
              "order": "created_at.desc", "limit": str(limit)}
    if args.status:
        params["status"] = f"eq.{args.status}"
    rows = db.get_rows(TABLE_JOBS, params)
    if not rows:
        print("No matching job.")
        return 0
    for row in rows:
        progress = int(row.get("progress") or 0)
        print(f"{row['id']}  {row['status']:<9} {row['kind']:<9} {progress:3d}%  {row['hf_model']}")
        if row.get("error"):
            print(f"    [{scrub(row['error'], (db.key,), 200)}]")
    return 0


def cmd_license(db: Supabase, args: argparse.Namespace) -> int:
    if args.profile not in PROFILES:
        print("Invalid profile.")
        return 2
    if not 1 <= args.max_devices <= 10:
        print("--max-devices must be 1 to 10.")
        return 2
    if args.job and not valid_job_id(args.job):
        print("--job must be a UUID.")
        return 2

    key = generate_key()
    note = (args.note or "").strip()[:300] or None
    record: dict[str, Any] = {"key_hash": hash_key(key), "profile": args.profile,
                              "max_devices": args.max_devices, "note": note}
    if args.job:
        record["job_id"] = args.job.lower()
    try:
        rows = db.insert(TABLE_LICENSES, record)
    except SupabaseError as exc:
        print(f"License not created: {scrub(exc, (db.key,), 300)}")
        return 1
    if not rows:
        print("License creation not confirmed. Do NOT give out this key.")
        return 1
    print(f"License created. Profile: {args.profile}, max devices: {args.max_devices}")
    print(f"Activation key (shown once, copy it now):\n{key}")
    return 0


def resolve_revoke_hash(args: argparse.Namespace) -> str | None:
    if args.hash:
        candidate = args.hash.strip().lower()
        if not SHA256_RE.fullmatch(candidate):
            print("--hash must be 64 hex characters.")
            return None
        return candidate
    plaintext = args.key if args.key else getpass.getpass("Paste activation key (hidden): ")
    if not valid_key_shape(plaintext):
        print("Key format invalid. Expected STH-XXXX-XXXX-XXXX-XXXX.")
        return None
    return hash_key(plaintext)


def cmd_revoke(db: Supabase, args: argparse.Namespace) -> int:
    lookup = resolve_revoke_hash(args)
    if lookup is None:
        return 2
    changed = db.update(TABLE_LICENSES, {"key_hash": lookup},
                        {"revoked": True, "revoked_at": datetime.now(timezone.utc).isoformat()})
    if not changed:
        print("No license matches that key or hash.")
        return 1
    print(f"License revoked. Records updated: {len(changed)}")
    return 0


def cmd_purge(db: Supabase, args: argparse.Namespace) -> int:
    result = db.rpc("purge_old_jobs", {"retention_days": max(1, args.days)})
    if isinstance(result, list):
        result = result[0] if result else None
    if isinstance(result, int):
        print(f"Purged {result} finished job(s) older than {args.days} days.")
    else:
        print(f"Purge finished (older than {args.days} days). The function returned no count.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sthamly NanoBrain admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enqueue", help="Queue a job for a Hugging Face model.")
    p.add_argument("--model", required=True)
    p.add_argument("--kind", choices=JOB_KINDS, default="calibrate")
    p.set_defaults(fn=cmd_enqueue)

    p = sub.add_parser("jobs", help="List recent jobs.")
    p.add_argument("--status", choices=JOB_STATUSES)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_jobs)

    p = sub.add_parser("license", help="Create an activation license.")
    p.add_argument("--profile", required=True, choices=PROFILES)
    p.add_argument("--job")
    p.add_argument("--max-devices", type=int, default=2)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_license)

    p = sub.add_parser("revoke", help="Revoke a license by key or hash.")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", help="plaintext key (ends up in shell history)")
    group.add_argument("--prompt-key", action="store_true", dest="prompt_key")
    group.add_argument("--hash")
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("purge", help="Delete finished jobs older than N days.")
    p.add_argument("--days", type=int, default=90)
    p.set_defaults(fn=cmd_purge)
    return parser


def main(argv: list[str] | None = None, db: Supabase | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "prompt_key", False):
        args.key = None
    database = db or Supabase.from_env()
    try:
        return int(args.fn(database, args))
    except SupabaseError as exc:
        print(f"Supabase error: {scrub(exc, (database.key,), 300)}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())