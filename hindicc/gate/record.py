"""Save quality measurements and the gate decision as a JSON record.

One record per artifact comparison. The record holds the raw KLD numbers, the
task accuracy numbers, the gate decision and its reasons, and the conditions
needed to trace them: model hashes, the eval set hash, item counts, and time.
Anything that was not measured is stored as null, never as a guessed value.

report/build.py reads these records. This module does not run any model.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from gate.verdict import GateResult
from quality.kld import Measurement
from quality.taskeval import EvalSummary

RESULTS_DIR = Path("results/quality")
SCHEMA_VERSION = 1


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _measurement(m: Measurement | None) -> dict | None:
    if m is None:
        return None
    return {"mean": m.mean, "err": m.err}


def _summary(s: EvalSummary | None) -> dict | None:
    if s is None:
        return None
    return {
        "n": s.n,
        "parse_failures": s.parse_failures,
        "exact_rate": s.exact_rate,
        "exact_ci": list(s.exact_ci),
        "field_accuracy": s.field_accuracy,
        "field_ci": list(s.field_ci),
    }


def build_quality_record(
    *,
    variant_name: str,
    variant_model: Path,
    reference_name: str,
    reference_model: Path,
    eval_items: Path,
    kld_variant: Measurement | None,
    kld_reference: Measurement | None,
    task_baseline: EvalSummary | None,
    task_variant: EvalSummary | None,
    gate: GateResult,
    kld_settings: dict,
) -> dict:
    """Assemble one record. Pure data, nothing is written here."""
    return {
        "schema": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "variant": {
            "name": variant_name,
            "path": str(variant_model),
            "sha256": sha256_of(variant_model) if variant_model.exists() else None,
        },
        "reference": {
            "name": reference_name,
            "path": str(reference_model),
            "sha256": sha256_of(reference_model) if reference_model.exists() else None,
        },
        "eval_set": {
            "path": str(eval_items),
            "sha256": sha256_of(eval_items) if eval_items.exists() else None,
        },
        "kld_settings": kld_settings,
        "kld": {
            "variant": _measurement(kld_variant),
            "reference": _measurement(kld_reference),
        },
        "task": {
            "fp16_baseline": _summary(task_baseline),
            "variant": _summary(task_variant),
        },
        "gate": {
            "decision": gate.decision,
            "kld_status": gate.kld_status,
            "task_drop_pp": gate.task_drop_pp,
            "reasons": list(gate.reasons),
        },
    }


def write_quality_record(record: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = record["variant"]["name"].replace("/", "_").replace(" ", "_")
    path = RESULTS_DIR / f"{stamp}_{name}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path