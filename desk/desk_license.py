"""Activation key: Supabase ke activate_license() se jaanch, aur local cache (offline bhi kuch din chalta hai).

Server (db/schema_v3.sql) key ka sirf hash rakhta hai aur device limit (default 2) lagata hai.
Yahan sirf PUBLIC anon key chalti hai. service_role key is software me kabhi nahi hoti.
Key aapke apne computer par ~/.sthamly/license.json me rakhi jaati hai taaki 7 din baad dobara jaanch ho sake.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from desk_errors import DeskError

RECHECK_DAYS = 7   # itne din baad server se dobara poochte hain
GRACE_DAYS = 14    # internet na ho to itne din tak purani jaanch maani jaati hai

MESSAGES = {
    "invalid_key": "Key galat hai. Dobara dekho (jaise STH-ABCD-EFGH-IJKL-MNOP).",
    "revoked": "Ye key band kar di gayi hai. Jisne di hai unse baat karo.",
    "device_limit": "Is key par device ki limit poori ho chuki hai. Jisne di hai unse baat karo.",
    "rate_limited": "Bahut zyada galat koshishein hui hain. Thodi der baad dobara try karo.",
    "invalid_device": "Is computer ki pehchaan nahi ban saki.",
}
DEV_PROFILE = {"profile": "general", "label": "Sthamly Desk (testing)", "actions": [],
               "disclaimer": "Testing mode: license jaanch band hai. Output ko hamesha khud verify karein."}


def device_id() -> str:
    """Is computer ki sthir pehchaan (hash). Naam/MAC seedha kahin nahi bheja jaata."""
    raw = f"{platform.system()}|{platform.node()}|{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def rpc(cfg: dict[str, Any], fn: str, payload: dict[str, Any], timeout: float = 15.0) -> Any:
    url = str(cfg.get("supabase_url") or "").rstrip("/")
    key = str(cfg.get("supabase_anon_key") or "")
    if not url or not key:
        raise DeskError("not_configured", "Is software me server ka pata (Supabase) set nahi hai. desk_config.json dekho.", 500)
    req = urllib.request.Request(f"{url}/rest/v1/rpc/{fn}", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        raise DeskError("server", f"Server ne error diya ({code}). Thodi der baad dobara try karo.", 502) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DeskError("offline", "Internet nahi chal raha ya server tak nahi pahunch paya.", 503) from None


def public(data: dict[str, Any]) -> dict[str, Any]:
    return {k: data.get(k) for k in ("profile", "label", "actions", "disclaimer")}


class LicenseStore:
    def __init__(self, home: Path, cfg: dict[str, Any], dev: bool = False):
        self.path = home / "license.json"
        self.cfg, self.dev = cfg, dev

    def _load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("key") else None
        except (OSError, ValueError):
            return None

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def activate(self, key: str) -> dict[str, Any]:
        key = (key or "").strip()
        if not key:
            raise DeskError("empty", "Pehle key likho.")
        r = rpc(self.cfg, "activate_license", {"p_key": key, "p_device": device_id()})
        if not isinstance(r, dict) or not r.get("ok"):
            code = r.get("error", "invalid_key") if isinstance(r, dict) else "invalid_key"
            raise DeskError(code, MESSAGES.get(code, "Key chal nahi saki."), 403)
        data = {**public(r), "key": key, "checked_at": time.time()}
        self._save(data)
        return public(data)

    def current(self) -> dict[str, Any] | None:
        """Chalu license ya None. 7 din baad server se dobara poochta hai; internet na ho to 14 din ki chhoot."""
        if self.dev:
            return DEV_PROFILE
        data = self._load()
        if not data:
            return None
        age = (time.time() - float(data.get("checked_at", 0))) / 86400
        if age > RECHECK_DAYS:
            try:
                r = rpc(self.cfg, "activate_license", {"p_key": data["key"], "p_device": device_id()})
            except DeskError:
                return public(data) if age <= GRACE_DAYS else None
            if isinstance(r, dict) and r.get("ok"):
                data = {**data, **public(r), "checked_at": time.time()}
                self._save(data)
            elif isinstance(r, dict) and r.get("error") in ("revoked", "invalid_key"):
                self.clear()
                return None
            elif age > GRACE_DAYS:
                return None
        return public(data)
