"""Hinglish kirana order-parsing eval for hindicc.

Each item is a short Hinglish order with a labeled structured result. The model
must return JSON, which is scored against the label. This module does not run a
model. It takes the raw outputs, so generation (llama.cpp or any runtime) stays
separate and the scoring can be re-run on saved outputs.

Input: JSONL, one labeled item per line, for example:
  {"id": "ord-001", "text": "2 kg atta, 1 litre tel aur 5 pav",
   "items": [{"name": "atta", "qty": 2, "unit": "kg"},
             {"name": "tel", "qty": 1, "unit": "litre"},
             {"name": "pav", "qty": 5, "unit": "piece"}]}

Metrics:
  - exact match: the whole order is right (same items, qty and unit, no extras).
  - field accuracy: correct fields (name, qty, unit) divided by all expected fields.
  - 95% bootstrap CI for both, so small differences are not over-read.

A variant is flagged as a task regression if its field accuracy is more than
REGRESSION_PP percentage points below the FP16 baseline.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

REGRESSION_PP = 3.0
BOOTSTRAP_SAMPLES = 1000
BOOTSTRAP_SEED = 0

PROMPT_TEMPLATE = (
    "Parse the kirana shop order below into JSON. Return only JSON, no other text.\n"
    'Schema: {{"items": [{{"name": string, "qty": number, "unit": string}}]}}\n'
    "Order: {text}\n"
    "JSON:"
)


class EvalFormatError(ValueError):
    """Raised when a labeled item file is malformed."""


@dataclass(frozen=True)
class Item:
    id: str
    text: str
    items: tuple  # tuple of dicts with name, qty, unit


@dataclass
class ItemScore:
    id: str
    parsed: bool
    exact: bool
    fields_correct: int
    fields_total: int


@dataclass
class EvalSummary:
    n: int
    parse_failures: int
    exact_rate: float
    exact_ci: tuple[float, float]
    field_accuracy: float
    field_ci: tuple[float, float]
    scores: list[ItemScore] = field(default_factory=list)


def load_items(path: Path) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalFormatError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
        for key in ("id", "text", "items"):
            if key not in row:
                raise EvalFormatError(f"{path}:{lineno}: missing '{key}'")
        if row["id"] in seen:
            raise EvalFormatError(f"{path}:{lineno}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        for it in row["items"]:
            if not all(k in it for k in ("name", "qty", "unit")):
                raise EvalFormatError(f"{path}:{lineno}: item needs name, qty, unit: {it}")
        items.append(Item(id=row["id"], text=row["text"], items=tuple(row["items"])))
    if not items:
        raise EvalFormatError(f"{path}: no items found")
    return items


def build_prompt(item: Item) -> str:
    return PROMPT_TEMPLATE.format(text=item.text)


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name).strip().lower())


def normalize_unit(unit: str) -> str:
    return str(unit).strip().lower()


def parse_prediction(raw: str) -> list[dict] | None:
    """Pull the JSON object out of the model output. Returns None if it is not usable."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    cleaned: list[dict] = []
    for it in data["items"]:
        if not isinstance(it, dict):
            return None
        if not isinstance(it.get("name"), str):
            return None
        qty = it.get("qty")
        if isinstance(qty, bool) or not isinstance(qty, (int, float)):
            return None
        if not isinstance(it.get("unit"), str):
            return None
        cleaned.append({"name": it["name"], "qty": float(qty), "unit": it["unit"]})
    return cleaned


def score_item(item: Item, predicted: list[dict] | None) -> ItemScore:
    """Match each expected item to an unused predicted item with the same name."""
    total = 3 * len(item.items)
    if predicted is None:
        return ItemScore(item.id, parsed=False, exact=False, fields_correct=0, fields_total=total)

    unused = list(predicted)
    correct = 0
    all_matched = True
    for exp in item.items:
        match = next(
            (p for p in unused if normalize_name(p["name"]) == normalize_name(exp["name"])),
            None,
        )
        if match is None:
            all_matched = False
            continue
        unused.remove(match)
        correct += 1  # name matched
        if float(match["qty"]) == float(exp["qty"]):
            correct += 1
        else:
            all_matched = False
        if normalize_unit(match["unit"]) == normalize_unit(exp["unit"]):
            correct += 1
        else:
            all_matched = False

    exact = all_matched and not unused and len(predicted) == len(item.items)
    return ItemScore(item.id, parsed=True, exact=exact, fields_correct=correct, fields_total=total)


def bootstrap_ci(pairs: list[tuple[float, float]], seed: int = BOOTSTRAP_SEED) -> tuple[float, float]:
    """95% CI for sum(num) / sum(den), resampling items with replacement."""
    if not pairs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(pairs)
    stats = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        num = sum(p[0] for p in sample)
        den = sum(p[1] for p in sample)
        if den:
            stats.append(num / den)
    stats.sort()
    lo = stats[int(0.025 * len(stats))]
    hi = stats[min(len(stats) - 1, int(0.975 * len(stats)))]
    return (lo, hi)


def evaluate(items: list[Item], raw_outputs: dict[str, str]) -> EvalSummary:
    """Score raw model outputs against labels. A missing output counts as a parse failure."""
    scores: list[ItemScore] = []
    for item in items:
        raw = raw_outputs.get(item.id, "")
        scores.append(score_item(item, parse_prediction(raw)))

    n = len(scores)
    exact_pairs = [(1.0 if s.exact else 0.0, 1.0) for s in scores]
    field_pairs = [(float(s.fields_correct), float(s.fields_total)) for s in scores]
    exact_rate = sum(p[0] for p in exact_pairs) / n
    field_total = sum(p[1] for p in field_pairs)
    field_acc = sum(p[0] for p in field_pairs) / field_total if field_total else 0.0

    return EvalSummary(
        n=n,
        parse_failures=sum(1 for s in scores if not s.parsed),
        exact_rate=exact_rate,
        exact_ci=bootstrap_ci(exact_pairs),
        field_accuracy=field_acc,
        field_ci=bootstrap_ci(field_pairs),
        scores=scores,
    )


def task_regression(baseline: EvalSummary, variant: EvalSummary) -> tuple[float, bool]:
    """Drop in field accuracy (percentage points) and whether it exceeds REGRESSION_PP."""
    drop_pp = (baseline.field_accuracy - variant.field_accuracy) * 100
    return drop_pp, drop_pp > REGRESSION_PP
