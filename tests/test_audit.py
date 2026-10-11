"""Tests for scripts/audit.py. Run: python -m unittest discover -s tests -v

The end-to-end tests use a tiny fake `llama-perplexity` shell script, so they check the
glue (arguments, parsing, resume, report files, upload merge) but NOT real llama.cpp.
"""
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import audit  # noqa: E402

# Real numbers from results/results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json
REAL = json.loads((Path(__file__).resolve().parent.parent / "results" / "results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json").read_text())

MOCK = r"""#!/bin/bash
if [ "$1" = "--version" ]; then echo "version: 9999 (mock)"; exit 0; fi
model=""; base=""; kl=0; f=""
while [ $# -gt 0 ]; do case "$1" in -m) model="$2"; shift;; --kl-divergence-base) base="$2"; shift;; --kl-divergence) kl=1;; -f) f="$2"; shift;; esac; shift; done
if [ "$kl" = "0" ]; then echo x > "$base"; exit 0; fi
echo "$model" >> "$MOCK_LOG"
case "$model" in *plain*) k=0.0642;; *hindi*) k=0.0509;; *) k=0.0545;; esac
printf 'Mean    KLD:   %s \xc2\xb1  0.0030\nRMS \xce\x94p    :  9.9 \xc2\xb1 0.39 %%\nSame top p: 87.0 \xc2\xb1 0.60 %%\n' "$k"
"""


class PureFunctions(unittest.TestCase):
    def test_parse_handles_the_plus_minus_sign(self):
        out = "x\nMean    KLD:   0.050000 ±  0.002000\nRMS Δp    :  10.0 ± 0.400 %\nSame top p: 87.00 ± 0.90 %\ny"
        self.assertEqual(audit.parse(out), {"kld": [0.05, 0.002], "rms": [10.0, 0.4], "top": [87.0, 0.9]})

    def test_parse_missing_numbers(self):
        self.assertEqual(audit.parse("nothing here"), {})

    def test_gain_and_verdict(self):
        a, b = {"kld": [0.0509, 0.0018]}, {"kld": [0.0642, 0.0030]}
        g, e = audit.gain(a, b)
        self.assertAlmostEqual(g, 0.0133)
        self.assertEqual(audit.verdict(g, e), "SAAF FARK")
        g, e = audit.gain({"top": [88.95, 0.57]}, {"top": [87.48, 0.60]}, "top")
        self.assertEqual(audit.verdict(g, e), "ishara, saaf nahi")  # 1.47 < 2 * 0.83
        self.assertEqual(audit.verdict(*audit.gain(b, a)), "fark nahi / ulta")

    def test_parse_quants_validation(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a.gguf"
            p.write_bytes(b"x")
            self.assertEqual(audit.parse_quants([f"plain={p}"]), [("plain", p)])
            for bad in ["nolabel", f"bad label={p}", f"=x{p}", f"a={p}", f"ok={Path(t) / 'missing.gguf'}"]:
                items = [bad] if not bad.startswith("a=") else [bad, bad]  # duplicate label
                with self.assertRaises(SystemExit, msg=bad):
                    audit.parse_quants(items)

    def test_rank_matches_the_website_logic(self):
        rows = {r["label"]: r for r in audit.rank(REAL)}
        order = [r["label"] for r in audit.rank(REAL)]
        self.assertEqual(order, ["hindi60", "mixed60", "english60", "standard"])
        self.assertTrue(rows["hindi60"]["is_best"])
        self.assertEqual(rows["standard"]["behind"], 2)  # clearly behind on both test sets
        self.assertEqual(rows["english60"]["behind"], 0)  # Hindi vs English: not clear
        self.assertEqual(rows["mixed60"]["behind"], 0)
        self.assertAlmostEqual(rows["hindi60"]["kld"], 0.0541)

    def test_rank_skips_quants_missing_on_a_test_set(self):
        partial = {"wiki": dict(REAL["wiki"]), "sangraha": {"standard": REAL["sangraha"]["standard"]}}
        self.assertEqual([r["label"] for r in audit.rank(partial)], ["standard"])
        self.assertEqual(audit.rank({}), [])

    def test_report_text_is_honest(self):
        rep = {"name": "Qwen2.5-1.5B-Instruct", "date": "2026-10-07", "base_model": "Qwen/Qwen2.5-1.5B-Instruct",
               "fp16": {"file": "f16.gguf", "size_gb": 3.56}, "settings": {"chunks": 12}, "llama_cpp": "",
               "domains": {"wiki": 150, "sangraha": 150}, "results": REAL,
               "variants_meta": {k: {"size_gb": 1.12} for k in REAL["wiki"]}}
        md, page = audit.build_markdown(rep), audit.build_html(rep)
        for text in (md, page):
            self.assertIn("0.0509", text)
            self.assertIn("Seemaayein", text)
            self.assertIn("proxy", text.lower())
            self.assertNotIn("40%", text)
        self.assertIn("version pata nahi", md)
        self.assertTrue(page.startswith("<!doctype html>"))
        rep["name"] = "<script>x</script>"
        self.assertNotIn("<script>x</script>", audit.build_html(rep))  # escaped

    def test_slugify(self):
        self.assertEqual(audit.slugify("Qwen2.5 1.5B / Instruct!"), "qwen2.5-1.5b-instruct")
        self.assertEqual(audit.slugify("!!!"), "model")


@unittest.skipIf(os.name == "nt", "mock llama-perplexity is a bash script")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "bin").mkdir()
        mock = t / "bin" / "llama-perplexity"
        mock.write_text(MOCK)
        mock.chmod(mock.stat().st_mode | stat.S_IEXEC)
        (t / "data").mkdir()
        for d in ("wiki", "sangraha"):  # existing eval files are reused, so no internet is needed
            (t / "data" / f"eval_{d}.txt").write_text("\n".join(f"line {i}" for i in range(40)), encoding="utf-8")
        self.fp16 = t / "f16.gguf"
        self.fp16.write_bytes(b"0" * 4000)
        self.plain, self.hindi = t / "plain-Q4.gguf", t / "hindi-Q4.gguf"
        self.plain.write_bytes(b"0" * 1000)
        self.hindi.write_bytes(b"0" * 1000)
        self.log = t / "calls.log"
        os.environ["MOCK_LOG"] = str(self.log)
        self.argv = ["--name", "Test Model", "--fp16", str(self.fp16),
                     "--quant", f"plain={self.plain}", "--quant", f"hindi={self.hindi}",
                     "--llama", str(t / "bin"), "--data-dir", str(t / "data"), "--out", str(t / "reports")]
        self.out = t / "reports" / "test-model"

    def tearDown(self):
        self.tmp.cleanup()

    def calls(self):
        return self.log.read_text().count("\n") if self.log.exists() else 0

    def test_full_run_then_resume(self):
        self.assertEqual(audit.main(self.argv), 0)
        rep = json.loads((self.out / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(set(rep["results"]), {"wiki", "sangraha"})
        self.assertEqual(rep["results"]["wiki"]["plain"]["kld"], [0.0642, 0.003])
        self.assertEqual(rep["results"]["wiki"]["hindi"]["kld"], [0.0509, 0.003])
        self.assertEqual(rep["llama_cpp"], "version: 9999 (mock)")
        self.assertEqual(rep["variants_meta"]["plain"]["source"], "plain-Q4.gguf")
        self.assertEqual(self.calls(), 4)  # 2 quants x 2 test sets, FP16 base calls are not logged
        self.assertIn("Hindi Quant Audit: Test Model", (self.out / "report.md").read_text(encoding="utf-8"))
        self.assertTrue((self.out / "report.html").exists())

        audit.main(self.argv)  # nothing changed: must not measure again
        self.assertEqual(self.calls(), 4)

        self.hindi.write_bytes(b"0" * 1200)  # one file changed: only that quant is measured again
        audit.main(self.argv)
        self.assertEqual(self.calls(), 6)

    def test_rejects_quant_not_smaller_than_fp16(self):
        self.plain.write_bytes(b"0" * 4000)
        with self.assertRaises(SystemExit):
            audit.main(self.argv)

    def test_missing_llama_binary(self):
        (Path(self.tmp.name) / "bin" / "llama-perplexity").unlink()
        with self.assertRaises(SystemExit):
            audit.main(self.argv)

    def test_unparseable_output_stops_with_a_clear_message(self):
        import contextlib
        import io
        mock = Path(self.tmp.name) / "bin" / "llama-perplexity"
        # creates the FP16 base file but prints no KLD numbers (e.g. wrong quant for this FP16)
        mock.write_text('#!/bin/bash\nif [ "$1" = "--version" ]; then exit 0; fi\n'
                        'while [ $# -gt 0 ]; do case "$1" in --kl-divergence-base) base="$2"; shift;; esac; shift; done\n'
                        'echo x > "$base"; echo garbage\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            audit.main(self.argv)
        self.assertIn("number padh nahi paya", buf.getvalue())

    def test_missing_base_file_stops_with_a_clear_message(self):
        import contextlib
        import io
        mock = Path(self.tmp.name) / "bin" / "llama-perplexity"
        mock.write_text('#!/bin/bash\nif [ "$1" = "--version" ]; then exit 0; fi\necho garbage\n')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            audit.main(self.argv)
        self.assertIn("FP16 base nahi bana", buf.getvalue())


class _Supabase(BaseHTTPRequestHandler):
    existing = []
    seen = []

    def _json(self, body):
        b = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        _Supabase.seen.append(("GET", self.path, None))
        self._json(_Supabase.existing)

    def _write(self, method):
        n = int(self.headers.get("Content-Length", 0))
        _Supabase.seen.append((method, self.path, json.loads(self.rfile.read(n))))
        self.send_response(201 if method == "POST" else 204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self._write("POST")

    def do_PATCH(self):
        self._write("PATCH")

    def log_message(self, *a):
        pass


class Upload(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _Supabase)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        os.environ["SUPABASE_URL"] = f"http://127.0.0.1:{self.srv.server_port}"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "test-key"
        _Supabase.seen = []
        self.rep = {"name": "Test Model", "base_model": "", "fp16": {"size_gb": 3.56}, "settings": {"chunks": 12},
                    "results": {"wiki": {"new": REAL["wiki"]["standard"]}},
                    "variants_meta": {"new": {"size_gb": 1.0, "source": "n.gguf"}}}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_insert_when_repo_is_new(self):
        _Supabase.existing = []
        audit.upload(self.rep, "owner/repo", "Q4_K_M", "")
        method, path, body = _Supabase.seen[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(body["hf_repo"], "owner/repo")
        self.assertEqual(body["model_name"], "Test Model")
        self.assertNotIn("base_model", body)  # empty base model is not sent

    def test_merge_keeps_existing_numbers_name_and_notes(self):
        _Supabase.existing = [{"id": "1", "results": {"wiki": {"hindi60": REAL["wiki"]["hindi60"]}},
                               "variants_meta": {"hindi60": {"size_gb": 1.12}}}]
        audit.upload(self.rep, "owner/repo", "Q4_K_M", "Qwen/Qwen2.5-1.5B-Instruct")
        method, path, body = _Supabase.seen[-1]
        self.assertEqual(method, "PATCH")
        self.assertEqual(set(body["results"]["wiki"]), {"hindi60", "new"})
        self.assertEqual(set(body["variants_meta"]), {"hindi60", "new"})
        for untouched in ("notes", "model_name", "download_url", "hf_repo"):
            self.assertNotIn(untouched, body)
        self.assertEqual(body["base_model"], "Qwen/Qwen2.5-1.5B-Instruct")

    def test_upload_needs_credentials(self):
        del os.environ["SUPABASE_URL"]
        with self.assertRaises(SystemExit):
            audit.upload(self.rep, "owner/repo", "Q4_K_M", "")


if __name__ == "__main__":
    unittest.main()
