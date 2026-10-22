#!/usr/bin/env python3
"""
Shared helpers for scripts/worker.py and scripts/admin.py.
Keep STEP_LABELS and TABLE names here so both files stay in sync.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable


# ============================================================
# SHARED CONSTANTS
# ============================================================

TABLE_JOBS = os.getenv("NANOBRAIN_JOBS_TABLE", "quantization_jobs")
TABLE_LICENSES = os.getenv("NANOBRAIN_LICENSES_TABLE", "licenses")

# Worker and admin both use these exact labels for the step list.
STEP_LABELS = [
    "Preflight checks",
    "Download Hugging Face model",
    "Convert model to FP16 GGUF",
    "Build Hindi imatrix",
    "Quantize standard Q4_K_M",
    "Quantize Hindi-imatrix Q4_K_M",
    "Run audit and validate report",
    "Upload artifacts",
    "Save results",
]

# Ambiguous characters (0, O, 1, I) removed so keys are easy to read aloud.
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_RE = re.compile(r"^STH-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
MODEL_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"
)
HF_URL_RE = re.compile(r"^https?://(?:www\.)?huggingface\.co/(.+)$", re.IGNORECASE)

_SECRET_PATTERNS = (
    re.compile(r"STH-[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


class SupabaseError(Exception):
    """Database or network request failed."""


# ============================================================
# LICENSE KEYS
# ============================================================

def generate_key() -> str:
    """Create a key like STH-ABCD-EFGH-JKLM-NPQR using a cryptographic RNG."""
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "STH-" + "-".join(groups)


def normalize_key(value: str) -> str:
    """Remove all whitespace and uppercase, so pasted keys match stored hashes."""
    return re.sub(r"\s+", "", value or "").upper()


def valid_key_shape(value: str) -> bool:
    return KEY_RE.fullmatch(normalize_key(value)) is not None


def hash_key(value: str) -> str:
    """SHA-256 hex of the normalized key. Only this hash is stored."""
    key = normalize_key(value)
    if not KEY_RE.fullmatch(key):
        raise ValueError("Invalid license key format.")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ============================================================
# MODEL IDS
# ============================================================

def normalize_model_id(value: str | None) -> str | None:
    """
    Accepts "owner/model" or a huggingface.co URL.
    Returns "owner/model" or None if invalid.
    """
    text = (value or "").strip()

    match = HF_URL_RE.match(text)
    if match:
        text = match.group(1)

    text = text.split("?")[0].split("#")[0].strip("/")

    # Drop /tree/main, /resolve/main, etc.
    parts = text.split("/")
    if len(parts) >= 2:
        text = "/".join(parts[:2])

    if MODEL_RE.fullmatch(text) is None:
        return None
    return text


# ============================================================
# OUTPUT SCRUBBING
# ============================================================

def scrub(
    value: Any,
    extra_secrets: Iterable[str] = (),
    limit: int = 300,
) -> str:
    """Single-line, redacted, length-limited text safe to print or store."""
    text = " ".join(str(value or "").split())

    for secret in extra_secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)

    return text[:limit]


# ============================================================
# SUPABASE REST CLIENT
# ============================================================

class Supabase:
    """
    Minimal PostgREST client using the service-role key.
    Server-side only.
    """

    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self.key = key

    @classmethod
    def from_env(cls) -> "Supabase":
        url = (
            os.getenv("NEXT_PUBLIC_SUPABASE_URL")
            or os.getenv("SUPABASE_URL")
            or ""
        ).strip()
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

        if not url.startswith("https://"):
            raise SupabaseError("Supabase URL missing or not HTTPS (NEXT_PUBLIC_SUPABASE_URL).")
        if not key:
            raise SupabaseError("SUPABASE_SERVICE_ROLE_KEY is missing.")

        return cls(url, key)

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        prefer: str | None = None,
    ) -> Any:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.url + path, data=body, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SupabaseError(
                f"HTTP {exc.code}: {scrub(detail, extra_secrets=(self.key,))}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SupabaseError(f"Connection failed: {scrub(exc, extra_secrets=(self.key,))}") from exc

        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise SupabaseError("Invalid JSON from Supabase.") from exc

    def select(self, query: str) -> list[dict[str, Any]]:
        """query example: 'quantization_jobs?select=id&status=eq.pending&limit=10'"""
        result = self._request("GET", f"/rest/v1/{query}")
        if not isinstance(result, list):
            raise SupabaseError("Expected a JSON array from Supabase.")
        return result

    def insert(self, table: str, record: dict[str, Any]) -> list[dict[str, Any]]:
        result = self._request(
            "POST", f"/rest/v1/{table}", payload=record, prefer="return=representation"
        )
        return result if isinstance(result, list) else []

    def update(
        self,
        table: str,
        match: dict[str, Any],
        values: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Equality match on every key in `match`. Returns updated rows."""
        if not match:
            raise ValueError("update() requires at least one match condition.")

        qs = urllib.parse.urlencode({k: f"eq.{v}" for k, v in match.items()})
        result = self._request(
            "PATCH",
            f"/rest/v1/{table}?{qs}",
            payload=values,
            prefer="return=representation",
        )
        return result if isinstance(result, list) else []