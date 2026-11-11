"""Final quality gate for hindicc.

Decides whether a quantized artifact is shipped, based on two measurements:

  1. KLD against FP16, compared with the reference quantization (plain Q4).
     Ship is blocked if the variant is clearly worse, that is, worse by more
     than 2x the combined error (the same rule as quality/kld.py).
  2. Task accuracy (field accuracy from quality/taskeval.py), compared with the
     FP16 baseline. Ship is blocked if the drop is more than REGRESSION_PP points.

Decisions:
  - "ship":            both checks pass.
  - "not recommended": a check fails. Reasons are listed.
  - "incomplete":      a measurement is missing, or the eval set is too small
                       to decide. Nothing is shipped on incomplete data.

A "better" KLD result is reported but does not block or unlock shipping on its own.
The gate only blocks. It never approves a claim that the numbers do not support.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quality.kld import Measurement, gain_and_error
from quality.taskeval import EvalSummary, task_regression

MIN_ITEMS = 200  # plan: a labeled set of at least 200 orders before deciding

SHIP = "ship"
NOT_RECOMMENDED = "not recommended"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class GateResult:
    decision: str
    kld_status: str  # "better", "same", "worse", or "missing"
    task_drop_pp: float | None
    reasons: tuple = field(default_factory=tuple)


def kld_status(variant: Measurement | None, reference: Measurement | None) -> str:
    """Compare the variant's KLD with the reference quantization (lower is better)."""
    if variant is None or reference is None:
        return "missing"
    gain, err = gain_and_error(variant, reference, "kld")
    if gain > 2.0 * err:
        return "better"
    if gain < -2.0 * err:
        return "worse"
    return "same"


def decide(
    kld_variant: Measurement | None,
    kld_reference: Measurement | None,
    task_baseline: EvalSummary | None,
    task_variant: EvalSummary | None,
    *,
    min_items: int = MIN_ITEMS,
) -> GateResult:
    """Combine the checks into one decision. Every reason is recorded.

    kld_reference is the plain Q4 KLD. task_baseline is the FP16 task run.
    """
    status = kld_status(kld_variant, kld_reference)

    if task_baseline is None or task_variant is None:
        return GateResult(
            INCOMPLETE, status, None,
            ("task accuracy measurement is missing for the baseline or the variant",),
        )
    if kld_variant is None or kld_reference is None:
        return GateResult(
            INCOMPLETE, status, None,
            ("KLD measurement is missing for the variant or the reference",),
        )

    smaller = min(task_baseline.n, task_variant.n)
    if smaller < min_items:
        return GateResult(
            INCOMPLETE, status, None,
            (f"eval set has {smaller} items, need at least {min_items} to decide",),
        )

    drop_pp, task_flagged = task_regression(task_baseline, task_variant)
    reasons: list[str] = []

    if status == "worse":
        reasons.append("KLD is clearly worse than the plain Q4 reference")
    if task_flagged:
        reasons.append(
            f"task accuracy dropped {drop_pp:.2f} points vs FP16, above the allowed limit"
        )
    if task_variant.parse_failures:
        reasons.append(
            f"note: {task_variant.parse_failures} outputs could not be parsed as JSON"
        )

    blocking = status == "worse" or task_flagged
    decision = NOT_RECOMMENDED if blocking else SHIP
    return GateResult(decision, status, drop_pp, tuple(reasons))


def render(result: GateResult) -> str:
    """Plain-text summary for the report. Numbers are shown as they are, with no extra claims."""
    lines = [f"Decision: {result.decision}", f"KLD vs plain Q4: {result.kld_status}"]
    if result.task_drop_pp is not None:
        lines.append(f"Task accuracy drop vs FP16: {result.task_drop_pp:.2f} points")
    for reason in result.reasons:
        lines.append(f"- {reason}")
    return "\n".join(lines)
