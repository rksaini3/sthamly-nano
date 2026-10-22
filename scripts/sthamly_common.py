#!/usr/bin/env python3
"""
Shared contract for scripts/worker.py, admin.py, prepare_model.py and publish.py.

Canonical schema: db/schema_v3.sql (table `jobs`) + db/migrations/0002_align_jobs_v3.sql.
Every name here must match that SQL. Server-side only: the service-role key must never
reach browser code or any NEXT_PUBLIC_ variable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable


# ============================================================
# SCHEMA CONTRACT (keep in sync with db/schema_v3.sql)
# ============================================================

TABLE_JOBS = "jobs"
TABLE_LICENSES = "licenses"

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"        # runtime error (tool crashed, timeout, upload failed)
JOB_REFUSED = "refused"      # policy rejection (bad model, custom code, license, size)
JOB_STATUSES = (JOB_QUEUED, JOB_RUNNING, JOB_DONE, JOB_FAILED, JOB_REFUSED)
JOB_ACTIVE = (JOB_QUEUED, JOB_RUNNING)
JOB_TERMINAL = (JOB_DONE, JOB_FAILED, JOB_REFUSED)

JOB_KINDS = ("audit", "calibrate")   # audit = free plain-Q4 audit, calibrate = full Hindi build

# Step labels per kind. Worker writes these into jobs.steps; web shows them.
STEP_LABELS = {
    "calibrate": [
        "Preflight checks",
        "Download and policy check",
        "Convert to FP16 GGUF",
        "Build Hindi imatrix",
        "Quantize plain Q4_K_M",
        "Quantize Hindi Q4_K_M",
        "Run audit",
        "Save results",
    ],
    "audit": [
        "Preflight checks",
        "Download and policy check",
        "Convert to FP16 GGUF",
        "Quantize plain Q4_K_M",
        "Run audit",
        "Save results",
    ],
}

# Public variant names used in jobs.result.results (matches lib/verdict.ts).
# Internal audit names (audit.py --quant) stay q4_standard / q4_hindi.
PUBLIC_VARIANT = {"q4_standard": "standard", "q4_hindi": "hindi60"}

TIMEOUT_REST = int(os.getenv("SUPABASE_TIMEOUT", "30"))
TIMEOUT_UPLOAD = int(os.getenv("SUPABASE_UPLOAD_TIMEOUT", "3600"))
UPLOAD_TIMEOUT = TIMEOUT_UPLOAD  # name used by older callers


# ============================================================
# IDS AND VALIDATION
# ============================================================

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def valid_job_id(value: str | None) -> bool:
    return bool(value) and UUID_RE.fullmatch(value.strip()) is not None


def validate_job_id(value: str) -> str:
    cleaned = (value or "").strip()
    if not valid_job_id(cleaned):
        raise ValueError("Job ID must be a UUID.")
    return cleaned.lower()


# ============================================================
# LICENSE KEYS (must match public.normalize_license_key / valid_license_key_shape / hash_license_key)
# ============================================================

# Subset of the SQL shape [A-Z2-7]. Excludes 0, 1, 8, 9, I and O to avoid misreading.
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ234567"
KEY_SHAPE_RE = re.compile(r"^STH[A-Z2-7]{16}$")  # checked on the normalized form


def generate_key() -> str:
    groups = ["".join(secrets.choice(KEY_ALPHABET) for _ in range(4)) for _ in range(4)]
    return "STH-" + "-".join(groups)


def normalize_key(value: str | None) -> str:
    """Same as SQL: uppercase, then keep only A-Z and 0-9 (dashes and spaces removed)."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def valid_key_shape(value: str | None) -> bool:
    """Same rule as public.valid_license_key_shape(): check the normalized form, so dashes are optional."""
    return KEY_SHAPE_RE.fullmatch(normalize_key(value)) is not None


def hash_key(value: str) -> str:
    """SHA-256 hex of the normalized key. Matches public.hash_license_key()."""
    if not valid_key_shape(value):
        raise ValueError("Invalid license key format.")
    return hashlib.sha256(normalize_key(value).encode("utf-8")).hexdigest()


# ============================================================
# MODEL IDS (must match lib/jobs.ts normalizeModelId and public.normalize_hf_model)
# ============================================================

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
URL_PREFIX_RE = re.compile(r"^(https?://)?(www\.)?(huggingface\.co|hf\.co)/", re.IGNORECASE)
RESERVED_OWNERS = {
    "datasets", "spaces", "papers", "collections", "docs", "blog",
    "api", "organizations", "models",
}


def normalize_model_id(value: str | None) -> str | None:
    """
    Accepts "owner/name" or a huggingface.co / hf.co model link.
    Returns "owner/name", or None if the input is not a model id.
    """
    if value is None:
        return None
    text = value.strip(" \t\r\n")
    text = re.sub(r"[?#][\s\S]*$", "", text)

    had_url = False
    match = URL_PREFIX_RE.match(text)
    if match:
        text = text[match.end():]
        had_url = True

    text = text.rstrip("/")
    if had_url:
        parts = text.split("/")
        if len(parts) < 2:
            return None
        text = "/".join(parts[:2])  # drop /tree/main, /resolve/main, ...

    if MODEL_RE.fullmatch(text) is None:
        return None
    if text.split("/", 1)[0].lower() in RESERVED_OWNERS:
        return None
    return text


# ============================================================
# OUTPUT SCRUBBING
# ============================================================

_SECRET_PATTERNS = (
    re.compile(r"STH-[A-Z2-7]{4}(?:-[A-Z2-7]{4}){3}"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def scrub(value: Any, extra_secrets: Iterable[str] = (), limit: int = 300) -> str:
    """Single line, secrets masked, length limited. Safe to print or store in the DB."""
    raw = "" if value is None else str(value)
    text = " ".join(raw.split())
    for secret in extra_secrets:
        if secret:
            text = text.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text[:limit]


# ============================================================
# SUPABASE REST CLIENT (service role, server side only)
# ============================================================

class SupabaseError(Exception):
    """Database, RPC, storage or network request failed."""


def build_query(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params, safe=".,()*")


class Supabase:
    def __init__(self, url: str, key: str) -> None:
        self.url = url.rstrip("/")
        self.key = key

    @classmethod
    def from_env(cls) -> "Supabase":
        url = (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL") or "").strip()
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url.startswith("https://"):
            sys.exit("SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) must be set and start with https://")
        if not key:
            sys.exit("SUPABASE_SERVICE_ROLE_KEY is not set.")
        return cls(url, key)

    def _request(self, method: str, path: str, payload: Any = None,
                 prefer: str | None = None, timeout: int | None = None) -> Any:
        headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or TIMEOUT_REST) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SupabaseError(f"HTTP {exc.code}: {scrub(detail, (self.key,))}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SupabaseError(f"Connection failed: {scrub(exc, (self.key,))}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SupabaseError("Invalid JSON from Supabase.") from exc

    def _rows(self, result: Any) -> list[dict[str, Any]]:
        if result is None:
            return []
        if not isinstance(result, list):
            raise SupabaseError("Expected a JSON array from Supabase.")
        return result

    # --- reads ---
    def select(self, query: str) -> list[dict[str, Any]]:
        """Raw PostgREST query, e.g. 'jobs?select=id&status=eq.queued'. Prefer get_rows()."""
        return self._rows(self._request("GET", f"/rest/v1/{query}"))

    def get_rows(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        return self._rows(self._request("GET", f"/rest/v1/{table}?{build_query(params)}"))

    # --- writes ---
    def insert(self, table: str, record: dict[str, Any]) -> list[dict[str, Any]]:
        return self._rows(self._request("POST", f"/rest/v1/{table}", payload=record,
                                        prefer="return=representation"))

    def update(self, table: str, match: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        if not match:
            raise ValueError("update() needs at least one match condition.")
        params = {k: f"eq.{v}" for k, v in match.items()}
        return self._rows(self._request("PATCH", f"/rest/v1/{table}?{build_query(params)}",
                                        payload=values, prefer="return=representation"))

    def patch_job(self, job_id: str, fields: dict[str, Any],
                  only_if_status: str | None = None) -> list[dict[str, Any]]:
        """
        PATCH exactly one job row. only_if_status makes the write race-safe:
        an empty list means the row was not in that status and nothing changed.
        """
        params = {"id": f"eq.{validate_job_id(job_id)}", "select": "id,status"}
        if only_if_status:
            params["status"] = f"eq.{only_if_status}"
        return self._rows(self._request("PATCH", f"/rest/v1/{TABLE_JOBS}?{build_query(params)}",
                                        payload=fields, prefer="return=representation"))

    # --- RPC (SECURITY DEFINER functions in schema_v3) ---
    def rpc(self, name: str, payload: dict[str, Any] | None = None) -> Any:
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise ValueError("Invalid RPC name.")
        return self._request("POST", f"/rest/v1/rpc/{name}", payload=payload or {})