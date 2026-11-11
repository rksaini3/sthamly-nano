"""Tests for the pure functions in bench/runner.py and bench/thermal.py.

These do not run llama-bench. They check how its output is read and how
drift is computed.

Run from the hindicc/ root:
    PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py" -v
"""

import sys
import tempfile
import unittest
from pathlib import Path

from bench import runner, thermal

PREFILL_ROW = {"n_prompt": 512, "n_gen": 0, "avg_ts": 48.2, "stddev_ts": 0.9}
DECODE_ROW = {"n_prompt": 0, "n_gen": 128, "avg_ts": 9.7, "stddev_ts": 0.2}


class SummarizeTest(unittest.TestCase):
    def test_picks_prefill_and_decode_rows(self):
        out = runner.summarize([PREFILL_ROW, DECODE_ROW])
        self.assertEqual(out["prefill"]["tokens_per_sec"], 48.2)
        self.assertEqual(out["prefill"]["stddev_tokens_per_sec"], 0.9)
        self.assertEqual(out["prefill"]["tokens"], 512)
        self.assertEqual(out["decode"]["tokens_per_sec"], 9.7)
        self.assertEqual(out["decode"]["tokens"], 128)

    def test_missing_decode_row_gives_none(self):
        out = runner.summarize([PREFILL_ROW])
        self.assertIsNone(out["decode"])
        self.assertIsNotNone(out["prefill"])

    def test_empty_rows_give_none_for_both(self):
        out = runner.summarize([])
        self.assertIsNone(out["prefill"])
        self.assertIsNone(out["decode"])

    def test_row_order_does_not_matter(self):
        out = runner.summarize([DECODE_ROW, PREFILL_ROW])
        self.assertEqual(out["decode"]["tokens_per_sec"], 9.7)
        self.assertEqual(out["prefill"]["tokens_per_sec"], 48.2)


class PeakRssTest(unittest.TestCase):
    def test_linux_kilobytes_to_mb(self):
        if sys.platform == "darwin":
            self.skipTest("macOS reports bytes; this test is for Linux")
        self.assertEqual(runner.peak_rss_mb(2048), 2.0)

    def test_result_is_float(self):
        self.assertIsInstance(runner.peak_rss_mb(1024), float)


class DeviceInfoTest(unittest.TestCase):
    def test_returns_platform_fields(self):
        info = runner.device_info()
        self.assertIn("platform", info)
        self.assertIn("cpu_count", info)


class Sha256Test(unittest.TestCase):
    def test_matches_known_digest(self):
        import hashlib

        path = Path(tempfile.mkdtemp()) / "f.bin"
        path.write_bytes(b"hindicc")
        self.assertEqual(runner.sha256_of(path), hashlib.sha256(b"hindicc").hexdigest())


class ThermalTempTest(unittest.TestCase):
    def test_millidegrees_to_celsius(self):
        path = Path(tempfile.mkdtemp()) / "temp"
        path.write_text("52300\n", encoding="utf-8")
        self.assertEqual(thermal.read_temp_c(path), 52.3)

    def test_missing_file_is_none(self):
        self.assertIsNone(thermal.read_temp_c(Path("/no/such/thermal/zone")))

    def test_garbage_content_is_none(self):
        path = Path(tempfile.mkdtemp()) / "temp"
        path.write_text("not a number", encoding="utf-8")
        self.assertIsNone(thermal.read_temp_c(path))


class DriftTest(unittest.TestCase):
    def test_too_few_samples_is_not_enough(self):
        out = thermal.drift_summary([10.0] * 5)
        self.assertFalse(out["enough_samples"])
        self.assertEqual(out["samples"], 5)

    def test_flat_run_has_zero_change(self):
        out = thermal.drift_summary([10.0] * 8)
        self.assertTrue(out["enough_samples"])
        self.assertEqual(out["change_percent"], 0.0)

    def test_slowdown_is_negative_change(self):
        rates = [10.0] * 3 + [9.0] * 2 + [8.0] * 3
        out = thermal.drift_summary(rates)
        self.assertEqual(out["start_median_tps"], 10.0)
        self.assertEqual(out["end_median_tps"], 8.0)
        self.assertAlmostEqual(out["change_percent"], -20.0, places=2)

    def test_uses_medians_not_means(self):
        # One outlier in the first three runs must not move the start median.
        rates = [100.0, 10.0, 10.0] + [10.0] * 3
        out = thermal.drift_summary(rates)
        self.assertEqual(out["start_median_tps"], 10.0)

    def test_zero_start_gives_none_change(self):
        out = thermal.drift_summary([0.0] * 6)
        self.assertIsNone(out["change_percent"])


if __name__ == "__main__":
    unittest.main()