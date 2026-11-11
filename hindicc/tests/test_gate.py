"""Tests for gate/verdict.py.

Run from the hindicc/ root:
    PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py" -v
"""

import unittest

from gate.verdict import (
    INCOMPLETE,
    MIN_ITEMS,
    NOT_RECOMMENDED,
    SHIP,
    decide,
    kld_status,
    render,
)
from quality.kld import Measurement
from quality.taskeval import EvalSummary

# NanoBrain's published wiki numbers (README.md).
PLAIN_Q4 = Measurement(0.0642, 0.0030)
HINDI_IMATRIX = Measurement(0.0509, 0.0018)


def summary(accuracy: float, n: int = MIN_ITEMS, parse_failures: int = 0) -> EvalSummary:
    return EvalSummary(
        n=n,
        parse_failures=parse_failures,
        exact_rate=0.0,
        exact_ci=(0.0, 0.0),
        field_accuracy=accuracy,
        field_ci=(0.0, 0.0),
    )


class KldStatusTest(unittest.TestCase):
    def test_clearly_lower_kld_is_better(self):
        self.assertEqual(kld_status(HINDI_IMATRIX, PLAIN_Q4), "better")

    def test_clearly_higher_kld_is_worse(self):
        self.assertEqual(kld_status(PLAIN_Q4, HINDI_IMATRIX), "worse")

    def test_small_difference_is_same(self):
        close = Measurement(0.0640, 0.0030)
        self.assertEqual(kld_status(close, PLAIN_Q4), "same")

    def test_missing_side_is_missing(self):
        self.assertEqual(kld_status(None, PLAIN_Q4), "missing")
        self.assertEqual(kld_status(HINDI_IMATRIX, None), "missing")


class ShipTest(unittest.TestCase):
    def test_better_kld_and_small_task_drop_ships(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.89))
        self.assertEqual(result.decision, SHIP)
        self.assertEqual(result.kld_status, "better")
        self.assertEqual(result.reasons, ())

    def test_same_kld_with_no_task_drop_ships(self):
        same = Measurement(0.0640, 0.0030)
        result = decide(same, PLAIN_Q4, summary(0.90), summary(0.90))
        self.assertEqual(result.decision, SHIP)

    def test_better_kld_is_not_enough_if_task_regresses(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.80))
        self.assertEqual(result.decision, NOT_RECOMMENDED)


class BlockTest(unittest.TestCase):
    def test_task_drop_above_limit_blocks(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.85))
        self.assertEqual(result.decision, NOT_RECOMMENDED)
        self.assertTrue(any("task accuracy dropped" in r for r in result.reasons))

    def test_clearly_worse_kld_blocks_even_with_no_task_drop(self):
        result = decide(PLAIN_Q4, HINDI_IMATRIX, summary(0.90), summary(0.90))
        self.assertEqual(result.decision, NOT_RECOMMENDED)
        self.assertTrue(any("KLD is clearly worse" in r for r in result.reasons))

    def test_both_failures_are_both_reported(self):
        result = decide(PLAIN_Q4, HINDI_IMATRIX, summary(0.90), summary(0.80))
        self.assertEqual(result.decision, NOT_RECOMMENDED)
        self.assertEqual(len(result.reasons), 2)

    def test_parse_failures_are_noted_but_do_not_block(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.90, parse_failures=3))
        self.assertEqual(result.decision, SHIP)
        self.assertTrue(any("could not be parsed" in r for r in result.reasons))


class IncompleteTest(unittest.TestCase):
    def test_missing_task_baseline_is_incomplete(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, None, summary(0.90))
        self.assertEqual(result.decision, INCOMPLETE)

    def test_missing_task_variant_is_incomplete(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), None)
        self.assertEqual(result.decision, INCOMPLETE)

    def test_missing_kld_is_incomplete(self):
        result = decide(None, PLAIN_Q4, summary(0.90), summary(0.90))
        self.assertEqual(result.decision, INCOMPLETE)
        self.assertEqual(result.kld_status, "missing")

    def test_too_few_items_is_incomplete(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90, n=MIN_ITEMS - 1), summary(0.90))
        self.assertEqual(result.decision, INCOMPLETE)

    def test_exactly_min_items_is_decided(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.90))
        self.assertIn(result.decision, (SHIP, NOT_RECOMMENDED))

    def test_too_few_items_is_checked_on_the_smaller_set(self):
        result = decide(
            HINDI_IMATRIX, PLAIN_Q4,
            summary(0.90, n=MIN_ITEMS), summary(0.90, n=MIN_ITEMS - 1),
        )
        self.assertEqual(result.decision, INCOMPLETE)

    def test_custom_min_items_is_respected(self):
        result = decide(
            HINDI_IMATRIX, PLAIN_Q4,
            summary(0.90, n=10), summary(0.90, n=10),
            min_items=5,
        )
        self.assertIn(result.decision, (SHIP, NOT_RECOMMENDED))


class RenderTest(unittest.TestCase):
    def test_render_contains_decision_and_reasons(self):
        result = decide(HINDI_IMATRIX, PLAIN_Q4, summary(0.90), summary(0.80))
        text = render(result)
        self.assertIn("Decision: not recommended", text)
        self.assertIn("task accuracy dropped", text)

    def test_render_omits_drop_line_when_incomplete(self):
        result = decide(None, PLAIN_Q4, summary(0.90), summary(0.90))
        self.assertNotIn("Task accuracy drop", render(result))


if __name__ == "__main__":
    unittest.main()