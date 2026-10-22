#!/usr/bin/env python3
"""
Tests for scripts/audit.py pure functions (no llama.cpp, no GPU, no network).
Run: python -m unittest discover -s tests -v
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit  # noqa: E402

PERPLEXITY_OUTPUT = (
    "x\n"
    "Mean    KLD:   0.050000 ±  0.002000\n"
    "RMS Δp    :  10.0 ± 0.400 %\n"
    "Same top p: 87.00 ± 0.90 %\n"
    "y"
)


class ParseTests(unittest.TestCase):
    def test_parses_the_plus_minus_sign(self):
        res = audit.parse_metrics(PERPLEXITY_OUTPUT)
        self.assertEqual(res["kld"], [0.05, 0.002])
        self.assertEqual(res["rms"], [10.0, 0.4])
        self.assertEqual(res["top"], [87.0, 0.9])

    def test_missing_metrics_are_absent_not_invented(self):
        self.assertEqual(audit.parse_metrics("nothing here"), {})

    def test_uses_the_last_summary_when_printed_twice(self):
        text = "Mean    KLD:   0.900000 ±  0.100000\n" + PERPLEXITY_OUTPUT
        self.assertEqual(audit.parse_metrics(text)["kld"], [0.05, 0.002])

    def test_negative_error_is_rejected(self):
        res = audit.parse_metrics("Mean    KLD:   0.05 ±  -0.002")
        self.assertNotIn("kld", res)


class CompareTests(unittest.TestCase):
    def test_lower_kld_is_a_positive_gain_for_the_better_model(self):
        gain, err = audit.compare([0.05, 0.002], [0.06, 0.002], "kld")
        self.assertGreater(gain, 0)
        self.assertAlmostEqual(err, math.sqrt(0.002 ** 2 + 0.002 ** 2))

    def test_higher_top_p_is_a_positive_gain_for_the_better_model(self):
        gain, _ = audit.compare([88.0, 0.5], [87.0, 0.5], "top")
        self.assertGreater(gain, 0)

    def test_non_finite_values_are_refused(self):
        with self.assertRaises(audit.AuditError):
            audit.compare([float("nan"), 0.1], [0.1, 0.1], "kld")


class VerdictTests(unittest.TestCase):
    def test_gain_bigger_than_twice_the_error_is_better(self):
        self.assertTrue(audit.verdict(0.01, 0.002).startswith("BETTER (gain > 2x"))

    def test_small_gain_inside_error_is_not_claimed(self):
        self.assertEqual(audit.verdict(0.003, 0.002), "BETTER BUT WITHIN ERROR")

    def test_no_gain_is_no_advantage(self):
        self.assertEqual(audit.verdict(0.0, 0.002), "NO ADVANTAGE")
        self.assertEqual(audit.verdict(-0.01, 0.002), "NO ADVANTAGE")


class NamingTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(audit.slugify("Qwen2.5 1.5B / Instruct!"), "qwen2.5-1.5b-instruct")

    def test_report_directory_name_is_safe_and_lowercase(self):
        self.assertEqual(audit.report_directory_name("Qwen/Qwen2.5-1.5B-Instruct"), "qwen_qwen2.5-1.5b-instruct")

    def test_report_directory_name_refuses_empty(self):
        with self.assertRaises(audit.AuditError):
            audit.report_directory_name("///")


if __name__ == "__main__":
    unittest.main()