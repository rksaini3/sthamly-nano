"""Tests for memory/budget.py.

Run from the hindicc/ root:
    python -m unittest discover -s tests -t . -p "test_*.py"
"""

import unittest

from memory.budget import (
    DEFAULT_OVERHEAD_BYTES,
    MB,
    BudgetReport,
    ModelShape,
    activation_bytes,
    kv_cache_bytes,
    max_context_tokens,
    plan,
    weight_bytes,
)

# Qwen2.5-1.5B-Instruct architecture (approximate parameter count).
QWEN_1_5B = ModelShape(
    n_params=1_543_000_000,
    n_layers=28,
    n_kv_heads=2,
    head_dim=128,
    hidden_size=1536,
)


class WeightBytesTest(unittest.TestCase):
    def test_fp16_is_two_bytes_per_param(self):
        self.assertEqual(weight_bytes(8, "fp16"), 16)

    def test_partial_byte_rounds_up(self):
        # 3 params * 4.85 bits = 14.55 -> 14 bits -> ceil to 2 bytes
        self.assertEqual(weight_bytes(3, "q4_k_m"), 2)

    def test_unknown_quant_raises(self):
        with self.assertRaises(ValueError):
            weight_bytes(100, "q3_k_s")

    def test_non_positive_params_raises(self):
        with self.assertRaises(ValueError):
            weight_bytes(0, "fp16")


class KvCacheTest(unittest.TestCase):
    def test_per_token_matches_formula(self):
        per_token = 2 * 28 * 2 * 128 * 2  # K and V, layers, kv heads, head_dim, fp16
        self.assertEqual(kv_cache_bytes(QWEN_1_5B, 1), per_token)
        self.assertEqual(kv_cache_bytes(QWEN_1_5B, 4096), per_token * 4096)

    def test_non_positive_ctx_raises(self):
        with self.assertRaises(ValueError):
            kv_cache_bytes(QWEN_1_5B, 0)


class ActivationTest(unittest.TestCase):
    def test_matches_formula(self):
        # 8 live hidden buffers * batch 1 * hidden 1536 * fp32 (4 bytes)
        self.assertEqual(activation_bytes(QWEN_1_5B), 8 * 1 * 1536 * 4)


class ModelShapeTest(unittest.TestCase):
    def test_zero_layers_rejected(self):
        with self.assertRaises(ValueError):
            ModelShape(n_params=1, n_layers=0, n_kv_heads=1, head_dim=1, hidden_size=1)

    def test_negative_hidden_rejected(self):
        with self.assertRaises(ValueError):
            ModelShape(n_params=1, n_layers=1, n_kv_heads=1, head_dim=1, hidden_size=-4)


class PlanTest(unittest.TestCase):
    def test_512mb_budget_does_not_fit_1_5b_q4(self):
        # ~933 MB of weights alone exceeds 512 MB. The estimator must say so.
        report = plan(QWEN_1_5B, "q4_k_m", ctx_len=512, budget_bytes=512 * MB)
        self.assertFalse(report.fits)
        self.assertLess(report.headroom, 0)

    def test_total_is_sum_of_parts(self):
        report = plan(QWEN_1_5B, "q4_k_m", ctx_len=1024, budget_bytes=2048 * MB)
        self.assertEqual(
            report.total,
            report.weights + report.kv_cache + report.activations + report.overhead,
        )
        self.assertEqual(report.headroom, report.budget - report.total)

    def test_overhead_is_included(self):
        report = plan(QWEN_1_5B, "q4_k_m", ctx_len=1, budget_bytes=2048 * MB)
        self.assertEqual(report.overhead, DEFAULT_OVERHEAD_BYTES)

    def test_non_positive_budget_raises(self):
        with self.assertRaises(ValueError):
            plan(QWEN_1_5B, "q4_k_m", ctx_len=1, budget_bytes=0)


class MaxContextTest(unittest.TestCase):
    def test_zero_when_fixed_costs_exceed_budget(self):
        self.assertEqual(max_context_tokens(QWEN_1_5B, "q4_k_m", 512 * MB), 0)

    def test_2gb_budget_gives_positive_context(self):
        ctx = max_context_tokens(QWEN_1_5B, "q4_k_m", 2048 * MB)
        self.assertGreater(ctx, 0)

    def test_result_is_the_exact_boundary(self):
        budget = 2048 * MB
        ctx = max_context_tokens(QWEN_1_5B, "q4_k_m", budget)
        # The largest fitting context fits, and one more token does not.
        self.assertTrue(plan(QWEN_1_5B, "q4_k_m", ctx, budget).fits)
        self.assertFalse(plan(QWEN_1_5B, "q4_k_m", ctx + 1, budget).fits)


class BudgetReportTest(unittest.TestCase):
    def test_fits_when_total_equals_budget(self):
        report = BudgetReport(weights=10, kv_cache=10, activations=10, overhead=10, budget=40)
        self.assertTrue(report.fits)
        self.assertEqual(report.headroom, 0)


if __name__ == "__main__":
    unittest.main()
