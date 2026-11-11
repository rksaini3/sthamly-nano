"""Tests for quality/taskeval.py.

Run from the hindicc/ root:
    PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py" -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from quality.taskeval import (
    REGRESSION_PP,
    EvalFormatError,
    EvalSummary,
    Item,
    bootstrap_ci,
    build_prompt,
    evaluate,
    load_items,
    parse_prediction,
    score_item,
    task_regression,
)

ATTA_TEL = Item(
    id="a",
    text="2 kg atta, 1 litre tel aur 5 pav",
    items=(
        {"name": "atta", "qty": 2, "unit": "kg"},
        {"name": "tel", "qty": 1, "unit": "litre"},
        {"name": "pav", "qty": 5, "unit": "piece"},
    ),
)


def as_json(items: list[dict]) -> str:
    return json.dumps({"items": items}, ensure_ascii=False)


def write_jsonl(rows: list[dict]) -> Path:
    path = Path(tempfile.mkdtemp()) / "items.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


class LoadItemsTest(unittest.TestCase):
    def test_valid_file_loads(self):
        path = write_jsonl(
            [{"id": "x", "text": "1 kg dal", "items": [{"name": "dal", "qty": 1, "unit": "kg"}]}]
        )
        items = load_items(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "x")

    def test_missing_key_raises(self):
        path = write_jsonl([{"id": "x", "items": []}])
        with self.assertRaises(EvalFormatError):
            load_items(path)

    def test_duplicate_id_raises(self):
        row = {"id": "x", "text": "t", "items": [{"name": "dal", "qty": 1, "unit": "kg"}]}
        with self.assertRaises(EvalFormatError):
            load_items(write_jsonl([row, row]))

    def test_item_missing_field_raises(self):
        path = write_jsonl([{"id": "x", "text": "t", "items": [{"name": "dal", "qty": 1}]}])
        with self.assertRaises(EvalFormatError):
            load_items(path)

    def test_empty_file_raises(self):
        path = Path(tempfile.mkdtemp()) / "empty.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        with self.assertRaises(EvalFormatError):
            load_items(path)


class PromptTest(unittest.TestCase):
    def test_prompt_contains_order_text(self):
        self.assertIn("2 kg atta", build_prompt(ATTA_TEL))


class ParsePredictionTest(unittest.TestCase):
    def test_plain_json(self):
        out = parse_prediction(as_json([{"name": "atta", "qty": 2, "unit": "kg"}]))
        self.assertEqual(out, [{"name": "atta", "qty": 2.0, "unit": "kg"}])

    def test_json_with_preamble_and_fence(self):
        raw = "Sure! ```json\n" + as_json([{"name": "dal", "qty": 1, "unit": "kg"}]) + "\n```"
        self.assertIsNotNone(parse_prediction(raw))

    def test_no_json_returns_none(self):
        self.assertIsNone(parse_prediction("I cannot do that"))

    def test_broken_json_returns_none(self):
        self.assertIsNone(parse_prediction('{"items": [{"name": "dal"'))

    def test_qty_as_string_is_rejected(self):
        self.assertIsNone(parse_prediction(as_json([{"name": "dal", "qty": "2", "unit": "kg"}])))

    def test_qty_as_bool_is_rejected(self):
        self.assertIsNone(parse_prediction(as_json([{"name": "dal", "qty": True, "unit": "kg"}])))

    def test_missing_items_key_returns_none(self):
        self.assertIsNone(parse_prediction('{"orders": []}'))


class ScoreItemTest(unittest.TestCase):
    def test_perfect_match_is_exact(self):
        pred = [
            {"name": "atta", "qty": 2.0, "unit": "kg"},
            {"name": "tel", "qty": 1.0, "unit": "litre"},
            {"name": "pav", "qty": 5.0, "unit": "piece"},
        ]
        s = score_item(ATTA_TEL, pred)
        self.assertTrue(s.exact)
        self.assertEqual((s.fields_correct, s.fields_total), (9, 9))

    def test_case_and_spacing_are_ignored(self):
        pred = [
            {"name": "  ATTA ", "qty": 2.0, "unit": "KG"},
            {"name": "tel", "qty": 1.0, "unit": "Litre"},
            {"name": "pav", "qty": 5.0, "unit": "piece"},
        ]
        self.assertTrue(score_item(ATTA_TEL, pred).exact)

    def test_wrong_qty_is_not_exact_but_partial_credit(self):
        pred = [
            {"name": "atta", "qty": 3.0, "unit": "kg"},  # wrong qty
            {"name": "tel", "qty": 1.0, "unit": "litre"},
            {"name": "pav", "qty": 5.0, "unit": "piece"},
        ]
        s = score_item(ATTA_TEL, pred)
        self.assertFalse(s.exact)
        self.assertEqual(s.fields_correct, 8)  # name still matched, qty wrong

    def test_extra_item_is_not_exact(self):
        pred = [
            {"name": "atta", "qty": 2.0, "unit": "kg"},
            {"name": "tel", "qty": 1.0, "unit": "litre"},
            {"name": "pav", "qty": 5.0, "unit": "piece"},
            {"name": "namak", "qty": 1.0, "unit": "kg"},
        ]
        self.assertFalse(score_item(ATTA_TEL, pred).exact)

    def test_missing_item_loses_its_fields(self):
        pred = [
            {"name": "atta", "qty": 2.0, "unit": "kg"},
            {"name": "tel", "qty": 1.0, "unit": "litre"},
        ]
        s = score_item(ATTA_TEL, pred)
        self.assertFalse(s.exact)
        self.assertEqual(s.fields_correct, 6)  # pav's 3 fields are lost

    def test_unparsed_output_scores_zero(self):
        s = score_item(ATTA_TEL, None)
        self.assertFalse(s.parsed)
        self.assertEqual(s.fields_correct, 0)
        self.assertEqual(s.fields_total, 9)


class EvaluateTest(unittest.TestCase):
    def test_missing_output_counts_as_parse_failure(self):
        summary = evaluate([ATTA_TEL], {})
        self.assertEqual(summary.parse_failures, 1)
        self.assertEqual(summary.exact_rate, 0.0)
        self.assertEqual(summary.field_accuracy, 0.0)

    def test_all_correct_gives_full_marks(self):
        good = as_json(
            [
                {"name": "atta", "qty": 2, "unit": "kg"},
                {"name": "tel", "qty": 1, "unit": "litre"},
                {"name": "pav", "qty": 5, "unit": "piece"},
            ]
        )
        summary = evaluate([ATTA_TEL], {"a": good})
        self.assertEqual(summary.exact_rate, 1.0)
        self.assertEqual(summary.field_accuracy, 1.0)

    def test_ci_stays_inside_zero_and_one(self):
        summary = evaluate([ATTA_TEL], {})
        lo, hi = summary.field_ci
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)


class BootstrapTest(unittest.TestCase):
    def test_same_seed_gives_same_interval(self):
        pairs = [(1.0, 1.0), (0.0, 1.0), (1.0, 1.0), (0.0, 1.0)]
        self.assertEqual(bootstrap_ci(pairs, seed=7), bootstrap_ci(pairs, seed=7))

    def test_empty_input_returns_zeros(self):
        self.assertEqual(bootstrap_ci([]), (0.0, 0.0))

    def test_interval_contains_point_estimate(self):
        pairs = [(1.0, 1.0)] * 30 + [(0.0, 1.0)] * 10
        lo, hi = bootstrap_ci(pairs)
        self.assertLessEqual(lo, 0.75)
        self.assertGreaterEqual(hi, 0.75)


def _summary(field_accuracy: float) -> EvalSummary:
    return EvalSummary(
        n=1,
        parse_failures=0,
        exact_rate=0.0,
        exact_ci=(0.0, 0.0),
        field_accuracy=field_accuracy,
        field_ci=(0.0, 0.0),
    )


class RegressionTest(unittest.TestCase):
    def test_drop_above_threshold_is_flagged(self):
        drop, flagged = task_regression(_summary(0.90), _summary(0.90 - 0.05))
        self.assertAlmostEqual(drop, 5.0, places=6)
        self.assertTrue(flagged)

    def test_drop_exactly_at_threshold_is_not_flagged(self):
        drop, flagged = task_regression(_summary(0.90), _summary(0.90 - REGRESSION_PP / 100))
        self.assertAlmostEqual(drop, REGRESSION_PP, places=6)
        self.assertFalse(flagged)

    def test_improvement_is_negative_drop_and_not_flagged(self):
        drop, flagged = task_regression(_summary(0.80), _summary(0.90))
        self.assertLess(drop, 0)
        self.assertFalse(flagged)


if __name__ == "__main__":
    unittest.main()