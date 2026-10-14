"""Tests for scripts/sthamly_common.py and scripts/admin.py. Run: python -m unittest discover -s tests -v

The model-id and key fixtures in tests/fixtures are ALSO used by lib/jobs.test.ts and db/schema_v3.test.ts, so
Python, TypeScript and SQL are proven to agree.
"""
import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import admin  # noqa: E402
import sthamly_common as c  # noqa: E402
import worker  # noqa: E402
from mock_supabase import MockSupabase  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
KEY_FX = json.loads((FIXTURES / "license_key.json").read_text(encoding="utf-8"))


class ModelIdTests(unittest.TestCase):
    def test_matches_shared_fixtures(self):
        cases = json.loads((FIXTURES / "model_ids.json").read_text(encoding="utf-8"))
        self.assertGreater(len(cases), 25)
        for case in cases:
            with self.subTest(case["input"]):
                self.assertEqual(c.normalize_model_id(case["input"]), case["expected"])

    def test_none_is_not_a_model(self):
        self.assertIsNone(c.normalize_model_id(None))

    def test_trailing_newline_is_not_smuggled_in(self):
        self.assertIsNone(c.normalize_model_id("a/b\nc"))
        self.assertEqual(c.normalize_model_id("a/b\n"), "a/b")  # outer whitespace is trimmed, like the other two


class KeyTests(unittest.TestCase):
    def test_hash_matches_the_fixture_that_the_sql_test_also_uses(self):
        self.assertEqual(c.normalize_key(KEY_FX["key"]), KEY_FX["normalized"])
        self.assertEqual(c.hash_key(KEY_FX["key"]), KEY_FX["hash"])
        self.assertEqual(c.hash_key(" sth abcd-efgh ijkl mnop "), KEY_FX["hash"])

    def test_generated_keys_have_the_right_shape_and_are_unique(self):
        keys = {c.generate_key() for _ in range(300)}
        self.assertEqual(len(keys), 300)
        for k in keys:
            self.assertRegex(k, r"^STH(-[A-Z2-7]{4}){4}$")
            self.assertTrue(c.valid_key_shape(k))
            self.assertRegex(c.hash_key(k), r"^[0-9a-f]{64}$")

    def test_bad_shapes(self):
        for k in ("", None, "STH-ABCD", "XXX-ABCD-EFGH-IJKL-MNOP", "STH-ABCD-EFGH-IJKL-MNOPQ"):
            self.assertFalse(c.valid_key_shape(k), k)


class ScrubTests(unittest.TestCase):
    def test_tokens_and_known_secrets_are_hidden(self):
        msg = "boom hf_abcdefghijklmnopqrstuvwx and key SECRETVALUE1234 and eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZSJ9.abcde12345"
        out = c.scrub(msg, ("SECRETVALUE1234", ""))
        for leaked in ("hf_abcdef", "SECRETVALUE1234", "eyJhbGci"):
            self.assertNotIn(leaked, out)
        self.assertIn("***", out)

    def test_long_messages_are_shortened_and_newlines_removed(self):
        out = c.scrub("x\n" * 500, limit=100)
        self.assertLessEqual(len(out), 100)
        self.assertNotIn("\n", out)

    def test_worker_errors_use_it(self):
        err = worker.safe_error(RuntimeError("fail hf_abcdefghijklmnopqrstuvwx"), ("abc",))
        self.assertNotIn("hf_abcdef", err)
        self.assertTrue(err.startswith("Job fail hua:"))


class SupabaseClientTests(unittest.TestCase):
    def test_insert_update_select_and_wrong_key(self):
        with MockSupabase() as mock:
            db = c.Supabase(mock.url, mock.api_key)
            row = db.insert("jobs", {"hf_model": "a/b", "kind": "audit"})[0]
            self.assertEqual(db.select("jobs?select=*&hf_model=eq.a%2Fb")[0]["id"], row["id"])
            self.assertEqual(len(db.update("jobs", {"id": row["id"]}, {"status": "done"})), 1)
            self.assertEqual(mock.rows["jobs"][0]["status"], "done")
            bad = c.Supabase(mock.url, "wrong-key-value")
            with self.assertRaises(c.SupabaseError) as cm:
                bad.select("jobs")
            self.assertNotIn("wrong-key-value", str(cm.exception))

    def test_from_env_stops_with_a_message(self):
        import os
        old = {k: os.environ.pop(k, None) for k in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")}
        try:
            with self.assertRaises(SystemExit) as cm:
                c.Supabase.from_env()
            self.assertIn("SUPABASE_URL", str(cm.exception))
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.mock = MockSupabase()
        self.mock.__enter__()
        self.addCleanup(self.mock.__exit__)
        self.db = c.Supabase(self.mock.url, self.mock.api_key)

    def run_cmd(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = admin.main(list(argv), db=self.db)
        return code, out.getvalue()

    def test_queued_steps_match_the_worker(self):
        self.assertEqual(admin.QUEUED_STEPS, worker.CALIBRATE_STEPS)

    def test_enqueue_creates_a_calibrate_job_from_an_id_or_url(self):
        code, out = self.run_cmd("enqueue", "--model", "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/")
        self.assertEqual(code, 0)
        job = self.mock.rows["jobs"][0]
        self.assertEqual((job["kind"], job["hf_model"], job["requester_hash"]), ("calibrate", "Qwen/Qwen2.5-1.5B-Instruct", "admin"))
        self.assertEqual(len(job["steps"]), 5)
        self.assertIn(job["id"], out)

    def test_enqueue_refuses_bad_ids_and_duplicates(self):
        self.assertEqual(self.run_cmd("enqueue", "--model", "not a model")[0], 2)
        self.assertEqual(self.run_cmd("enqueue", "--model", "my_org/my_model")[0], 0)
        code, out = self.run_cmd("enqueue", "--model", "MY_ORG/my_model")  # same model, other case
        self.assertEqual(code, 1)
        self.assertIn("pehle se", out)
        self.assertEqual(len(self.mock.rows["jobs"]), 1)

    def test_license_stores_only_the_hash_and_prints_a_key_that_matches_it(self):
        code, out = self.run_cmd("license", "--profile", "lawyer", "--note", "Adv. Sharma", "--max-devices", "3")
        self.assertEqual(code, 0)
        key = re.search(r"STH(?:-[A-Z2-7]{4}){4}", out).group(0)
        row = self.mock.rows["licenses"][0]
        self.assertEqual(row["key_hash"], c.hash_key(key))
        self.assertEqual((row["profile"], row["max_devices"], row["note"]), ("lawyer", 3, "Adv. Sharma"))
        self.assertNotIn(key, json.dumps(row))
        self.assertNotIn("job_id", row)

    def test_license_rejects_unknown_profile_and_bad_job_id(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            admin.main(["license", "--profile", "wizard"], db=self.db)
        self.assertEqual(self.run_cmd("license", "--profile", "lawyer", "--job", "nope")[0], 2)
        self.assertEqual(self.mock.rows["licenses"], [])

    def test_profiles_match_the_sql_seed(self):
        sql = (ROOT / "db" / "schema_v3.sql").read_text(encoding="utf-8")
        for profile in admin.PROFILES:
            self.assertIn(f"('{profile}',", sql)

    def test_revoke_by_key_or_hash(self):
        self.mock.rows["licenses"].append({"id": "1", "key_hash": KEY_FX["hash"], "profile": "lawyer", "revoked": False})
        self.assertEqual(self.run_cmd("revoke", "--key", KEY_FX["key"].lower())[0], 0)
        self.assertTrue(self.mock.rows["licenses"][0]["revoked"])
        self.assertEqual(self.run_cmd("revoke", "--hash", "0" * 64)[0], 1)  # unknown license
        self.assertEqual(self.run_cmd("revoke", "--key", "bad")[0], 2)
        self.assertEqual(self.run_cmd("revoke", "--hash", "xyz")[0], 2)

    def test_jobs_lists_and_filters(self):
        self.mock.add_job("a/one", status="queued")
        failed = self.mock.add_job("a/two", status="failed")
        failed["error"] = "boom"
        code, out = self.run_cmd("jobs")
        self.assertEqual(code, 0)
        self.assertIn("a/one", out)
        self.assertIn("a/two", out)
        code, out = self.run_cmd("jobs", "--status", "failed")
        self.assertIn("[boom]", out)
        self.assertNotIn("a/one", out)

    def test_supabase_errors_are_reported_not_raised(self):
        self.db.key = "wrong-key-wrong"
        code, out = self.run_cmd("jobs")
        self.assertEqual(code, 1)
        self.assertIn("Supabase error", out)
        self.assertNotIn("wrong-key-wrong", out)


if __name__ == "__main__":
    unittest.main()
