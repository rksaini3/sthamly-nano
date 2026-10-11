"""Operator tool for the Phase 1 backend. Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.

    python scripts/admin.py enqueue --model Qwen/Qwen2.5-1.5B-Instruct       # paid calibrate job
    python scripts/admin.py license --profile lawyer --job <job-id>          # makes a key, shows it ONCE
    python scripts/admin.py revoke --key STH-XXXX-XXXX-XXXX-XXXX
    python scripts/admin.py jobs --status queued

Only the sha256 hash of an activation key is stored. If you lose the raw key, make a new one.
First-customer flow (Phase 1, payment by hand over UPI): take the money, run `enqueue`, run
`python scripts/worker.py --once`, check the result page, send the GGUF yourself within 2 hours, make a key.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sthamly_common import (  # noqa: E402
    Supabase, SupabaseError, generate_key, hash_key, normalize_model_id, valid_key_shape,
)

PROFILES = ("general", "lawyer", "finance", "student")
STATUSES = ("queued", "running", "done", "failed", "refused")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
# Same first labels as scripts/worker.py CALIBRATE_STEPS: the job page shows them while the job is still queued.
QUEUED_STEPS = [
    "Model ki jaankari check ho rahi hai",
    "Model download aur FP16 GGUF ban raha hai",
    "Hindi calibration data taiyaar ho raha hai",
    "Hindi / English / Mixed imatrix ban rahe hain aur naape ja rahe hain",
    "Best variant chuna ja raha hai",
]


def cmd_enqueue(db: Supabase, args: argparse.Namespace) -> int:
    model = normalize_model_id(args.model)
    if not model:
        print("Model ID sahi nahi hai. Aise likho: owner/model-name")
        return 2
    # ilike = case-insensitive match; "_" is a wildcard in LIKE, so escape it (model ids may contain it)
    pattern = urllib.parse.quote(model.replace("_", "\\_"), safe="")
    live = db.select(f"jobs?select=id,status&kind=eq.calibrate&hf_model=ilike.{pattern}&status=in.(queued,running)")
    if live:
        print(f"Ye model pehle se {live[0]['status']} hai: job {live[0]['id']}")
        return 1
    row = db.insert("jobs", {
        "kind": "calibrate", "hf_model": model, "requester_hash": "admin", "step": "Queue me hai",
        "steps": [{"label": label, "state": "pending"} for label in QUEUED_STEPS],
    })[0]
    print(f"Calibrate job bana: {row['id']}")
    print(f"Result page: /job/{row['id']}")
    print("Worker chalao: python scripts/worker.py --once")
    return 0


def cmd_license(db: Supabase, args: argparse.Namespace) -> int:
    if args.job and not UUID_RE.match(args.job):
        print("--job ek UUID hona chahiye.")
        return 2
    key = generate_key()
    row: dict[str, Any] = {"key_hash": hash_key(key), "profile": args.profile, "max_devices": args.max_devices}
    if args.job:
        row["job_id"] = args.job
    if args.note:
        row["note"] = args.note
    db.insert("licenses", row)
    print(f"\n  Activation key:  {key}\n")
    print("Ye key sirf abhi dikh rahi hai (database me sirf hash hai). Abhi copy kar lo.")
    return 0


def cmd_revoke(db: Supabase, args: argparse.Namespace) -> int:
    if args.key:
        if not valid_key_shape(args.key):
            print("Key ka format sahi nahi hai (STH-XXXX-XXXX-XXXX-XXXX).")
            return 2
        key_hash = hash_key(args.key)
    else:
        key_hash = args.hash.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", key_hash):
            print("--hash 64 hex characters ka hona chahiye.")
            return 2
    changed = db.update("licenses", {"key_hash": key_hash}, {"revoked": True})
    print("Key revoke ho gayi." if changed else "Is key ka koi license nahi mila.")
    return 0 if changed else 1


def cmd_jobs(db: Supabase, args: argparse.Namespace) -> int:
    query = f"jobs?select=id,kind,hf_model,status,progress,error,created_at&order=created_at.desc&limit={args.limit}"
    if args.status:
        query += f"&status=eq.{args.status}"
    rows = db.select(query)
    if not rows:
        print("Koi job nahi.")
        return 0
    for r in rows:
        extra = f"  [{r['error']}]" if r.get("error") else ""
        print(f"{r['id']}  {r['status']:<8} {r['progress']:>3}%  {r['kind']:<9} {r['hf_model']}{extra}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enqueue", help="calibrate job queue me daalo")
    p.add_argument("--model", required=True)
    p.set_defaults(fn=cmd_enqueue)

    p = sub.add_parser("license", help="nayi activation key banao")
    p.add_argument("--profile", choices=PROFILES, required=True)
    p.add_argument("--job", help="us job ki id jiske liye key hai")
    p.add_argument("--max-devices", type=int, default=2, choices=range(1, 11), metavar="1-10")
    p.add_argument("--note", help="kiske liye / kab (sirf aapke liye)")
    p.set_defaults(fn=cmd_license)

    p = sub.add_parser("revoke", help="key band karo")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--key")
    g.add_argument("--hash")
    p.set_defaults(fn=cmd_revoke)

    p = sub.add_parser("jobs", help="jobs ki list")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_jobs)
    return ap


def main(argv: list[str] | None = None, db: Supabase | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(db or Supabase.from_env(), args)
    except SupabaseError as e:
        print(f"Supabase error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
