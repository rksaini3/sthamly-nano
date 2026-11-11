"""Tests for report/build.py.

Run from the hindicc/ root:
    PYTHONPATH=. python -m unittest discover -s tests -p "test_*.py" -v
"""

import json
import tempfile
import unittest
from pathlib import Path

from report import build


def make_dir() -> Path:
    return Path(tempfile.mkdtemp())


def write(directory: Path, name: str, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data)
    (directory / name).write_text(text, encoding="utf-8")


def quality_record(**overrides) -> dict:
    record = {
        "timestamp_utc": "2026-11-11T10:00:00+00:00",
        "variant": {"name": "hindi60-q4", "sha256": "abcdef1234567890"},
        "reference": {"name": "plain-q4", "sha256": "1111"},
        "eval_set": {"path": "items.jsonl", "sha256": "feedface00112233"},
        "kld": {
            "variant": {"mean": 0.0509, "err": 0.0018},
            "reference": {"mean": 0.0642, "err": 0.0030},
        },
        "task": {
            "fp16_baseline": {"n": 300, "field_accuracy": 0.90, "field_ci": [0.88, 0.92]},
            "variant": {"n": 300, "field_accuracy": 0.80, "field_ci": [0.78, 0.82]},
        },
        "gate": {
            "decision": "not recommended",
            "kld_status": "better",
            "task_drop_pp": 10.0,
            "reasons": ["task accuracy dropped 10.00 points vs FP16, above the allowed limit"],
        },
    }
    record.update(overrides)
    return record


class LoadRecordsTest(unittest.TestCase):
    def test_missing_directory_returns_warning(self):
        records, warnings = build.load_records(Path("/no/such/dir/hindicc"))
        self.assertEqual(records, [])
        self.assertEqual(len(warnings), 1)

    def test_broken_json_is_skipped_with_warning(self):
        d = make_dir()
        write(d, "good.json", {"timestamp_utc": "2026-01-01"})
        write(d, "broken.json", "{not json")
        records, warnings = build.load_records(d)
        self.assertEqual([r["_file"] for r in records], ["good.json"])
        self.assertTrue(any("broken.json" in w for w in warnings))

    def test_non_object_json_is_skipped(self):
        d = make_dir()
        write(d, "list.json", [1, 2, 3])
        records, warnings = build.load_records(d)
        self.assertEqual(records, [])
        self.assertTrue(any("not a JSON object" in w for w in warnings))

    def test_records_are_sorted_by_timestamp(self):
        d = make_dir()
        write(d, "b.json", {"timestamp_utc": "2026-02-01"})
        write(d, "a.json", {"timestamp_utc": "2026-01-01"})
        records, _ = build.load_records(d)
        self.assertEqual([r["_file"] for r in records], ["a.json", "b.json"])


class NotMeasuredTest(unittest.TestCase):
    def test_fmt_none_is_not_measured(self):
        self.assertEqual(build.fmt(None), build.NOT_MEASURED)

    def test_tps_cell_missing_block_is_not_measured(self):
        self.assertEqual(build.tps_cell(None), build.NOT_MEASURED)
        self.assertEqual(build.tps_cell({"tokens_per_sec": None}), build.NOT_MEASURED)

    def test_empty_quality_section_says_no_records(self):
        self.assertIn("no quality records found", build.quality_rows([]))

    def test_quality_record_with_missing_parts_does_not_crash(self):
        record = {"_file": "sparse.json", "gate": {"decision": "incomplete"}}
        html_text = build.quality_rows([record])
        self.assertIn(build.NOT_MEASURED, html_text)
        self.assertIn("incomplete", html_text)

    def test_thermal_without_enough_samples_shows_not_measured_change(self):
        record = {
            "_file": "t.json",
            "drift": {"enough_samples": False, "samples": 2},
            "params": {"minutes": 1},
        }
        html_text = build.thermal_rows([record])
        self.assertIn(build.NOT_MEASURED, html_text)
        self.assertIn("only 2 samples", html_text)


class EscapingTest(unittest.TestCase):
    def test_variant_name_is_escaped(self):
        record = quality_record(variant={"name": "<script>alert(1)</script>", "sha256": "x"})
        record["_file"] = "x.json"
        html_text = build.quality_rows([record])
        self.assertNotIn("<script>alert(1)</script>", html_text)
        self.assertIn("&lt;script&gt;", html_text)

    def test_reason_text_is_escaped(self):
        record = quality_record(gate={"decision": "not recommended", "reasons": ["<b>x</b>"]})
        record["_file"] = "x.json"
        html_text = build.quality_rows([record])
        self.assertNotIn("<b>x</b>", html_text)


class QualitySectionTest(unittest.TestCase):
    def test_gate_decision_is_shown_exactly_as_recorded(self):
        record = quality_record()
        record["_file"] = "q.json"
        html_text = build.quality_rows([record])
        self.assertIn("not recommended", html_text)
        self.assertIn("task accuracy dropped 10.00 points", html_text)

    def test_numbers_and_ci_are_shown(self):
        record = quality_record()
        record["_file"] = "q.json"
        html_text = build.quality_rows([record])
        self.assertIn("0.0509 ± 0.0018", html_text)
        self.assertIn("90.0%", html_text)
        self.assertIn("n=300", html_text)

    def test_eval_set_hash_prefix_is_shown(self):
        record = quality_record()
        record["_file"] = "q.json"
        self.assertIn("feedface0011", build.quality_rows([record]))


class BuildHtmlTest(unittest.TestCase):
    def test_all_three_sections_are_present(self):
        page = build.build_html([], [], [])
        self.assertIn("<h2>Speed (llama-bench)</h2>", page)
        self.assertIn("<h2>Thermal drift</h2>", page)
        self.assertIn("<h2>Quality and gate</h2>", page)

    def test_empty_inputs_produce_not_measured_notes(self):
        page = build.build_html([], [], [])
        self.assertIn("no bench records found", page)
        self.assertIn("no thermal records found", page)
        self.assertIn("no quality records found", page)


class MainTest(unittest.TestCase):
    def test_main_writes_report_file(self):
        bench_dir = make_dir()
        thermal_dir = make_dir()
        quality_dir = make_dir()
        write(quality_dir, "q.json", quality_record())
        out = make_dir() / "report.html"

        code = build.main([
            "--bench", str(bench_dir),
            "--thermal", str(thermal_dir),
            "--quality", str(quality_dir),
            "--out", str(out),
        ])
        self.assertEqual(code, 0)
        text = out.read_text(encoding="utf-8")
        self.assertIn("hindi60-q4", text)
        self.assertIn("not recommended", text)


if __name__ == "__main__":
    unittest.main()