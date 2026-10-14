"""Helpers shared by scripts/worker.py and scripts/admin.py: a small Supabase REST client (service-role
key, standard library only), model-id and activation-key handling, and secret scrubbing.

normalize_model_id() must behave exactly like normalizeModelId() in lib/jobs.ts and like
public.normalize_hf_model() in db/schema_v3.sql. All three are tested against tests/fixtures/model_ids.json.
If you change one, change all three.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------- model id
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
URL_PREFIX_RE = re.compile(r"^(https?://)?(www\.)?(huggingface\.co|hf\.co)/", re.I)
RESERVED_OWNERS = {"datasets", "spaces", "papers", "collections", "docs", "blog", "api", "organizations", "models"}


def normalize_model_id(text: str | None) -> str | None:
    """'owner/name' from an id or a Hugging Face URL, or None if it is not a valid model id."""
    v = (text or "").strip(" \t\r\n")
    v = re.sub(r"[?#][\s\S]*$", "", v)
    had_url = False
    if URL_PREFIX_RE.match(v):
        v = URL_PREFIX_RE.sub("", v, count=1)
        had_url = True
    v = re.sub(r"/+$", "", v)
    if had_url:
        m = re.match(r"^([^/]+/[^/]+)", v)
        v = m.group(1) if m else ""
    # fullmatch, not match: Python's "$" would also accept a trailing newline
    if not MODEL_ID_RE.fullmatch(v):
        return None
    if v.split("/")[0].lower() in RESERVED_OWNERS:
        return None
    return v


# ---------------------------------------------------------------- activation keys
KEY_PREFIX = "STH"


def generate_key() -> str:
    """STH-XXXX-XXXX-XXXX-XXXX: 80 random bits as 16 base32 characters."""
    body = base64.b32encode(secrets.token_bytes(10)).decode("ascii")  # 10 bytes -> exactly 16 chars, no padding
    return "-".join([KEY_PREFIX, body[0:4], body[4:8], body[8:12], body[12:16]])


def normalize_key(key: str | None) -> str:
    """Upper-case, letters and digits only (so dashes, spaces and case never matter)."""
    return re.sub(r"[^A-Za-z0-9]", "", key or "").upper()


def hash_key(key: str | None) -> str:
    """sha256 hex of the normalized key. Same as encode(sha256(convert_to(norm, 'utf8')), 'hex') in SQL."""
    return hashlib.sha256(normalize_key(key).encode("utf-8")).hexdigest()


def valid_key_shape(key: str | None) -> bool:
    n = normalize_key(key)
    return len(n) == 19 and n.startswith(KEY_PREFIX)


# ---------------------------------------------------------------- secrets
_TOKEN_PATTERNS = [
    re.compile(r"hf_[A-Za-z0-9]{16,}"),                                            # Hugging Face tokens
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),   # JWTs (Supabase keys)
    re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{10,}"),
]


def scrub(text: Any, extra_secrets: tuple[str, ...] = (), limit: int = 240) -> str:
    """Removes tokens and known secret values and shortens the text. Use it for anything stored in a job row."""
    out = str(text)
    for s in extra_secrets:
        if s and len(s) >= 8:
            out = out.replace(s, "***")
    for p in _TOKEN_PATTERNS:
        out = p.sub("***", out)
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 3] + "..."


# ---------------------------------------------------------------- Supabase REST (service role)
class SupabaseError(RuntimeError):
    pass


class Supabase:
    """PostgREST + RPC with the service-role key. Never use this in anything that runs in a browser."""

    def __init__(self, url: str, key: str, timeout: float = 60.0):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout

    @classmethod
    def from_env(cls) -> "Supabase":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not (url and key):
            raise SystemExit("ROKA: SUPABASE_URL aur SUPABASE_SERVICE_ROLE_KEY environment me chahiye (.env.example dekho).")
        return cls(url, key)

    def _call(self, method: str, path: str, body: Any = None, prefer: str = "return=minimal") -> Any:
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "Prefer": prefer}
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"{self.url}/rest/v1/{path}", data=data, headers=headers, method=method)
        where = f"{method} {path.split('?')[0]}"
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise SupabaseError(scrub(f"Supabase {where} fail: {e.code} {detail}", (self.key,))) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SupabaseError(scrub(f"Supabase {where} fail: {getattr(e, 'reason', e)}", (self.key,))) from None
        return json.loads(raw) if raw else None

    @staticmethod
    def _match(match: dict[str, Any]) -> str:
        return urllib.parse.urlencode({k: f"eq.{v}" for k, v in match.items()})

    def rpc(self, fn: str, args: dict[str, Any] | None = None) -> Any:
        return self._call("POST", f"rpc/{fn}", args or {})

    def select(self, path: str) -> list[dict[str, Any]]:
        return self._call("GET", path) or []

    def insert(self, table: str, row: dict[str, Any]) -> list[dict[str, Any]]:
        return self._call("POST", table, row, prefer="return=representation") or []

    def update(self, table: str, match: dict[str, Any], patch: dict[str, Any]) -> list[dict[str, Any]]:
        return self._call("PATCH", f"{table}?{self._match(match)}", patch, prefer="return=representation") or []

    def patch_job(self, job_id: str, fields: dict[str, Any]) -> None:
        # --- FIXED TARGET ROUTING TABLE: quantization_jobs ---
        # Maps the telemetry progress update strings safely right onto the active database schema
        self._call("PATCH", f"quantization_jobs?{self._match({'id': job_id})}", fields)
