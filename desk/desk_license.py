"""Activation key: Supabase verify_client_activation() se jaanch, aur local cache (offline bhi chalta hai).

Blueprint B7 ke hisaab se:
- har RECHECK_DAYS (30) din mein server se dobara jaanch.
- server tak na pahunch paayein to GRACE_DAYS (14) din ki chhoot. Uske baad sirf padhne/niryaat ka mode
  (read_only=True). Desk_app ko read_only ka matlab AI action band karna hai.
- key OS keychain (keyring) mein rakhi jaati hai, license.json mein nahi. keyring na ho to file mein
  (kam surakshit, cache mein key_storage="file" likha jaata hai).
- license.json par HMAC lagta hai. checked_at, profile ya kisi aur field ko haath se badlo to cache rad.
- ghadi peeche karne par (rollback) cache rad.

Honest limit: local cache puri tarah bharosemand nahi ho sakta. Poori suraksha ke liye server Ed25519-signed
license token dega (blueprint B7-2). Ye file us token ke liye taiyaar hai, par token abhi server par nahi hai.

Server (db/schema_v3.sql) key ka sirf hash rakhta hai aur device limit lagata hai.
Yahan sirf PUBLIC anon key chalti hai. service_role key is software me kabhi nahi hoti.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from desk_errors import DeskError

RECHECK_DAYS = 30        # itne din baad server se dobara poochte hain (blueprint B7)
GRACE_DAYS = 14          # server na mile to itne din tak poora access; uske baad read_only
CLOCK_TOLERANCE = 3600   # seconds. Ghadi isse zyada peeche gayi to cache rad
DAY = 86400.0
CACHE_VERSION = 2
KEYRING_SERVICE = "sthamly-desk"

MESSAGES = {
    "invalid_key": "Key galat hai. Dobara dekho (jaise STH-ABCD-EFGH-IJKL).",
    "revoked": "Ye key band kar di gayi hai. Jisne di hai unse baat karo.",
    "device_limit": "Is key par device ki limit poori ho chuki hai. Jisne di hai unse baat karo.",
    "rate_limited": "Bahut zyada galat koshishein hui hain. Thodi der baad dobara try karo.",
    "invalid_device": "Is computer ki pehchaan nahi ban saki.",
    "clock": "Computer ki ghadi peeche lagti hai. Ghadi theek karke dobara kholo.",
    "relicense": "Is computer par license dobara daalni hogi.",
}
DEV_PROFILE = {"profile": "general", "label": "Sthamly Desk (testing)", "actions": [],
               "disclaimer": "Testing mode: license jaanch band hai. Output ko hamesha khud verify karein."}


def device_id() -> str:
    """Is computer ki sthir pehchaan (hash). Naam/MAC seedha kahin nahi bheja jaata."""
    raw = f"{platform.system()}|{platform.node()}|{uuid.getnode()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def get_os_platform() -> str:
    """Detects running operating system mapping for hardware tracking logs."""
    sys_name = platform.system().lower()
    if "windows" in sys_name:
        return "windows"
    if "darwin" in sys_name:
        return "macos"
    return "linux"


def normalize_key(key: str) -> str:
    """Server ki tarah: sirf A-Z aur 0-9, capital letters."""
    return "".join(ch for ch in (key or "").upper() if ch.isalnum())


def key_id(key: str) -> str:
    """Key ki pehchaan (hash ka chhota hissa). Keychain ka username yahi hai, asli key nahi."""
    return hashlib.sha256(normalize_key(key).encode("utf-8")).hexdigest()[:16]


def _keyring():
    """OS keychain module agar install hai aur kaam karta hai, warna None."""
    try:
        import keyring  # type: ignore[import-not-found]
        keyring.get_password(KEYRING_SERVICE, "probe")  # raises if no usable backend
        return keyring
    except Exception:
        return None


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
    """UI ko jaane wali license jaankari. system_prompt do jagah se padha jaata hai (cache aur server reply)."""
    payload_box = data.get("payload") or {}
    return {
        "profile": data.get("profile"),
        "label": data.get("label") or f"Sthamly {str(data.get('profile')).capitalize()} Desk",
        "actions": data.get("actions") or [],
        "disclaimer": data.get("disclaimer") or "100% Private Offline Workspace footprint.",
        "system_prompt": data.get("system_prompt") or payload_box.get("system_prompt"),
    }


class LicenseStore:
    def __init__(self, home: Path, cfg: dict[str, Any], dev: bool = False):
        self.home = home
        self.path = home / "license.json"
        self.cfg, self.dev = cfg, dev
        self._kr = _keyring()

    # ---------------------------------------------------------------- integrity

    def _mac_key(self) -> bytes:
        """Cache par HMAC ki chaabi. Keychain mein random secret; keychain na ho to device se nikli (kam mazboot)."""
        if self._kr is not None:
            try:
                secret = self._kr.get_password(KEYRING_SERVICE, "cache-secret")
                if not secret:
                    secret = secrets.token_hex(32)
                    self._kr.set_password(KEYRING_SERVICE, "cache-secret", secret)
                return secret.encode("utf-8")
            except Exception:
                pass
        return hashlib.sha256(f"sthamly-cache|{device_id()}".encode("utf-8")).digest()

    def _mac(self, data: dict[str, Any]) -> str:
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hmac.new(self._mac_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    def _load(self) -> dict[str, Any] | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("v") != CACHE_VERSION:
            return None
        mac = raw.pop("mac", None)
        if not isinstance(mac, str) or not hmac.compare_digest(mac, self._mac(raw)):
            return None  # file haath se badli gayi hai
        return raw

    def _save(self, data: dict[str, Any]) -> None:
        body = {k: v for k, v in data.items() if k not in ("mac", "v")}
        body["v"] = CACHE_VERSION
        body["mac"] = self._mac(body)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(body), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)

    # ---------------------------------------------------------------- key storage

    def _store_key(self, kid: str, key: str) -> str:
        """Returns where the key went: 'keyring' (preferred) or 'file' (fallback)."""
        if self._kr is not None:
            try:
                self._kr.set_password(KEYRING_SERVICE, kid, key)
                return "keyring"
            except Exception:
                pass
        return "file"

    def _read_key(self, data: dict[str, Any]) -> str | None:
        if data.get("key_storage") == "file":
            return data.get("key_file") or None
        if self._kr is None:
            return None
        try:
            return self._kr.get_password(KEYRING_SERVICE, str(data.get("key_id", ""))) or None
        except Exception:
            return None

    def clear(self) -> None:
        data = self._load()
        if data and data.get("key_storage") == "keyring" and self._kr is not None:
            try:
                self._kr.delete_password(KEYRING_SERVICE, str(data.get("key_id", "")))
            except Exception:
                pass
        self.path.unlink(missing_ok=True)

    # ---------------------------------------------------------------- server

    @staticmethod
    def _payload(key: str) -> dict[str, Any]:
        return {
            "p_license_key": key,
            "p_hardware_fingerprint": device_id(),
            "p_client_os": get_os_platform(),
        }

    @staticmethod
    def _reject_message(r: Any) -> str:
        msg = str(r.get("message", "")).lower() if isinstance(r, dict) else ""
        if "deactivated" in msg:
            return MESSAGES["revoked"]
        if "limit" in msg:
            return MESSAGES["device_limit"]
        if "invalid_device" in msg:
            return MESSAGES["invalid_device"]
        return MESSAGES["invalid_key"]

    @staticmethod
    def _active(data: dict[str, Any], read_only: bool) -> dict[str, Any]:
        return {**public(data), "read_only": read_only}

    # ---------------------------------------------------------------- public API

    def activate(self, key: str) -> dict[str, Any]:
        key = (key or "").strip()
        if not key:
            raise DeskError("empty", "Pehle key likho.")

        r = rpc(self.cfg, "verify_client_activation", self._payload(key))
        if not isinstance(r, dict) or not r.get("success"):
            raise DeskError("invalid_key", self._reject_message(r), 403)

        kid = key_id(key)
        now = time.time()
        data: dict[str, Any] = {
            **public(r),
            "key_id": kid,
            "key_storage": self._store_key(kid, key),
            "checked_at": now,
            "last_seen": now,
        }
        if data["key_storage"] == "file":
            data["key_file"] = key
        self._save(data)
        return self._active(data, read_only=False)

    def current(self) -> dict[str, Any] | None:
        """
        Chalu license ya None. Keys: profile, label, actions, disclaimer, system_prompt, read_only.
        read_only=True: sirf padhne aur niryaat ka mode (blueprint B7-3). AI actions band karo.
        """
        if self.dev:
            return {**DEV_PROFILE, "read_only": False}

        data = self._load()
        if not data:
            return None

        now = time.time()
        last_seen = float(data.get("last_seen", 0))
        if now + CLOCK_TOLERANCE < last_seen:
            self.clear()  # ghadi peeche ki gayi hai
            return None
        if now - last_seen > 3600:
            data["last_seen"] = now
            self._save(data)

        age_days = (now - float(data.get("checked_at", 0))) / DAY
        if age_days <= RECHECK_DAYS:
            return self._active(data, read_only=False)

        key = self._read_key(data)
        if not key:
            self.clear()
            return None  # key keychain se gayi, dobara activate karna hoga

        try:
            r = rpc(self.cfg, "verify_client_activation", self._payload(key))
        except DeskError:
            # Server nahi mila. Grace ke andar poora access; uske baad read_only.
            return self._active(data, read_only=age_days > RECHECK_DAYS + GRACE_DAYS)

        if not isinstance(r, dict) or not r.get("success"):
            self.clear()  # revoke ho gayi ya device limit; cache hata do
            return None

        data.update(public(r))
        data["checked_at"] = now
        data["last_seen"] = now
        self._save(data)
        return self._active(data, read_only=False)