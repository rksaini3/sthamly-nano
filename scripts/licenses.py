#!/usr/bin/env python3
"""License tool (sirf aapke computer par). Key banana, revoke karna, list karna.

Service-role key chahiye (SUPABASE_SERVICE_ROLE_KEY). Ye key desk software me kabhi nahi jaati.

    export SUPABASE_URL=https://YOUR_PROJECT.supabase.co
    export SUPABASE_SERVICE_ROLE_KEY=...
    python scripts/licenses.py create --note "Adv. Sharma, Gonda" --max-devices 2
    python scripts/licenses.py revoke --key STH-XXXX-XXXX-XXXX-XXXX
    python scripts/licenses.py list

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.request

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # server rule: STH + 16 characters from A-Z and 2-7


def make_key() -> str:
    body = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return f"STH-{body[0:4]}-{body[4:8]}-{body[8:12]}-{body[12:16]}"


def normalize(key: str) -> str:
    return "".join(ch for ch in key.upper() if ch.isalnum())


def key_hash(key: str) -> str:
    return hashlib.sha256(normalize(key).encode("utf-8")).hexdigest()


def rest(method: str, path: str, body=None, prefer: str | None = None):
    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base or not service:
        sys.exit("SUPABASE_URL aur SUPABASE_SERVICE_ROLE_KEY environment me set karo.")
    headers = {"apikey": service, "Authorization": f"Bearer {service}", "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{base}/rest/v1/{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        sys.exit(f"Supabase error {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def cmd_create(a: argparse.Namespace) -> int:
    key = make_key()
    rest("POST", "licenses",
         {"key_hash": key_hash(key), "profile": "lawyer", "max_devices": a.max_devices, "note": a.note},
         prefer="return=minimal")
    print("Nayi license key (ye ek baar dikhegi, abhi save kar lo; database me sirf hash hai):")
    print(key)
    return 0


def cmd_revoke(a: argparse.Namespace) -> int:
    rest("PATCH", f"licenses?key_hash=eq.{key_hash(a.key)}", {"revoked": True}, prefer="return=minimal")
    print("Revoke request bhej di gayi. (Agar key maujood thi to ab band hai.)")
    return 0


def cmd_list(_a: argparse.Namespace) -> int:
    rows = rest("GET", "licenses?select=created_at,max_devices,revoked,note&order=created_at.desc") or []
    for r in rows:
        status = "BAND" if r["revoked"] else "chalu"
        print(f'{r["created_at"][:10]}  {status:5}  devices<={r["max_devices"]}  {r.get("note") or ""}')
    print(f"Kul: {len(rows)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sthamly Legal Desk license tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="Nayi key banao")
    c.add_argument("--note", default="", help="kiske liye (sirf aapke reference ke liye)")
    c.add_argument("--max-devices", type=int, default=2)
    c.set_defaults(func=cmd_create)
    r = sub.add_parser("revoke", help="Key band karo")
    r.add_argument("--key", required=True)
    r.set_defaults(func=cmd_revoke)
    sub.add_parser("list", help="Saari keys ki list").set_defaults(func=cmd_list)
    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
