"""Worker: queue se job uthakar chalata hai (free audit ya paid calibrate). Aapke PC / Colab / RunPod par haath se chalta hai.

    python scripts/worker.py --once        # ek job (agar hai) chalao aur ruko. Pehli baar yahi se try karo.
    python scripts/worker.py               # chalta rahe, har 20 second me queue dekhe

Env (shell me export karo, Vercel me NAHI): SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY. Pehle `bash scripts/setup.sh`.

Har job ke liye:
  1. claim_next_job (ek hi worker ko milta hai)
  2. Hugging Face API se check: public? gated? safetensors? size <= ~4 GB? (calibrate: license allowlist)
  3. work/<job-id>/ me alag folder: scripts ki copy + llama.cpp / data ke symlink. Customer ka model sirf yahin
     utarta hai, isliye job ke baad rmtree se saaf, aur aapke apne models/ ko haath nahi lagta.
  4. prepare_model.py (download, FP16 GGUF, plain Q4 usi FP16 se) -> audit.py ya trl4_bench.py
  5. result + progress steps Supabase me. Exception par job "failed" (chhota scrubbed message).

Safety:
  * Subprocess ko Supabase / HF tokens nahi milte, aur DRIVE_DIR khaali hota hai (warna trl4_bench.py customer ka
    model aapke Drive me copy kar deta).
  * trust_remote_code kabhi nahi. prepare_model.py custom code (auto_map) par exit code 3 deta hai -> polite refusal.
  * Final result ki likhai 3 baar retry hoti hai. Progress likhai fail ho to job nahi marta.
  * Paid calibrate "improved" ho to best GGUF outputs/<job-id>/ me rakhi jaati hai (haath se bhejne ke liye) aur
    2 ghante baad khud delete hoti hai.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import audit  # noqa: E402  (gain / slugify: wahi "saaf fark" rule jo baaki sab jagah hai)
from sthamly_common import Supabase, SupabaseError, normalize_model_id, scrub  # noqa: E402

# ---------------------------------------------------------------- constants
# Ye labels db/schema_v3.sql (audit) aur scripts/admin.py (calibrate) ke saath mel khate hain; tests check karte hain.
AUDIT_STEPS = [
    "Model ki jaankari check ho rahi hai",
    "Model download aur FP16 GGUF ban raha hai",
    "Hindi test sets par FP16 se naap",
    "Result likha ja raha hai",
]
CALIBRATE_STEPS = [
    "Model ki jaankari check ho rahi hai",
    "Model download aur FP16 GGUF ban raha hai",
    "Hindi calibration data taiyaar ho raha hai",
    "Hindi / English / Mixed imatrix ban rahe hain aur naape ja rahe hain",
    "Best variant chuna ja raha hai",
]

MAX_WEIGHTS_BYTES = 4_000_000_000  # ~4 GB safetensors
PERMISSIVE_LICENSES = {"apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause", "cc-by-4.0", "cc0-1.0", "unlicense", "isc"}
CUSTOM_CODE_EXIT = 3  # scripts/prepare_model.py CUSTOM_CODE_EXIT
PREPARE_TIMEOUT = 3 * 3600
AUDIT_TIMEOUT = 4 * 3600
CALIBRATE_TIMEOUT = 10 * 3600
DATA_TIMEOUT = 2 * 3600
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
AUDIT_NOTE = "Ye proxy naap hai (FP16 se distribution ka fark), asli Hindi task (sawal-jawab, summary) nahi."

# ---------------------------------------------------------------- errors, secrets
class Refused(Exception):
    """Job nahi liya ja sakta. Message visitor ko dikhta hai, isliye chhota aur saaf likho (koi secret ya path nahi)."""


def safe_error(exc: BaseException | str, extra_secrets: Iterable[str] = ()) -> str:
    """Technical failure ka chhota scrubbed text. Sirf admin.py jobs me dikhta hai, visitor ko nahi."""
    return "Job fail hua: " + scrub(exc, tuple(extra_secrets), limit=220)


def secrets_in_env() -> tuple[str, ...]:
    return tuple(v for k in ("SUPABASE_SERVICE_ROLE_KEY", "HF_TOKEN", "HF_WRITE_TOKEN") if (v := os.environ.get(k, "")))


_SECRET_MARKERS = ("SUPABASE", "TOKEN", "SECRET", "SERVICE_ROLE", "API_KEY", "PASSWORD")


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Subprocess ka environment: koi token / key nahi, aur DRIVE_DIR khaali (customer model Drive me copy na ho)."""
    src = os.environ if base is None else base
    env = {k: v for k, v in src.items() if not any(m in k.upper() for m in _SECRET_MARKERS)}
    env["DRIVE_DIR"] = ""
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


# ---------------------------------------------------------------- Hugging Face check
def http_get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "sthamly-worker/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def license_of(info: dict[str, Any]) -> str | None:
    card = info.get("cardData")
    lic = card.get("license") if isinstance(card, dict) else None
    if isinstance(lic, list):
        lic = lic[0] if lic else None
    if not lic:
        for t in info.get("tags") or []:
            if isinstance(t, str) and t.startswith("license:"):
                lic = t.split(":", 1)[1]
                break
    return str(lic).strip().lower() if lic else None


def check_model(model_id: str, kind: str, fetch_json: Callable[[str], Any] = http_get_json) -> dict[str, Any]:
    """HF API se model dekho. Sirf public, non-gated, safetensors, <= ~4 GB. Wapas: {"license", "weights_gb"}.

    Koi HF token nahi bheja jaata: private / gated model waise bhi nahi chahiye.
    """
    url = f"https://huggingface.co/api/models/{urllib.parse.quote(model_id, safe='/')}?blobs=true"
    try:
        info = fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise Refused("Ye model public nahi mila (ya private hai). Sirf public Hugging Face models chalte hain.") from None
        raise
    if not isinstance(info, dict):
        raise RuntimeError("Hugging Face API se samajh aane wala jawab nahi aaya")
    if info.get("private"):
        raise Refused("Ye model private hai. Sirf public Hugging Face models chalte hain.")
    if info.get("gated"):
        raise Refused("Ye model gated hai (Hugging Face par permission chahiye), isliye abhi nahi liya ja sakta.")

    files = [s for s in (info.get("siblings") or []) if isinstance(s, dict) and str(s.get("rfilename", "")).endswith(".safetensors")]
    if not files:
        raise Refused("Is model me safetensors weights nahi hain. Abhi sirf safetensors wale models chalte hain.")
    sizes = [s.get("size") for s in files]
    if any(not isinstance(x, (int, float)) or x <= 0 for x in sizes):
        raise Refused("Is model ka size pata nahi chala, isliye abhi nahi liya ja sakta.")
    total = float(sum(sizes))
    if total > MAX_WEIGHTS_BYTES:
        raise Refused(f"Ye model bahut bada hai ({total / 1e9:.1f} GB). Abhi lagbhag 4 GB tak ke weights chalte hain.")

    lic = license_of(info)
    if kind == "calibrate" and lic not in PERMISSIVE_LICENSES:
        raise Refused(f"Is model ka license ({lic or 'pata nahi'}) calibrated copy banane ke liye allowed list me nahi hai.")
    return {"license": lic, "weights_gb": round(total / 1e9, 2)}


# ---------------------------------------------------------------- running commands
# runner(cmd, env, timeout_seconds, on_line, cwd) -> (return_code, last_output_text). Tests inject a fake one.
Runner = Callable[[list, dict, float, "Callable[[str], None] | None", Path], "tuple[int, str]"]


def run_streaming(cmd: list, env: dict[str, str], timeout: float, on_line: Callable[[str], None] | None, cwd: Path) -> tuple[int, str]:
    """Command chalao, output line-by-line on_line ko do, aakhri ~60 lines wapas karo. Timeout par process kill."""
    tail: collections.deque[str] = collections.deque(maxlen=60)
    timed_out = threading.Event()
    # `with` pipe band karta hai aur process ka intezaar karta hai (warna ResourceWarning)
    with subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", env=env, cwd=str(cwd)) as proc:

        def kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                tail.append(line)
                if on_line:
                    try:
                        on_line(line)
                    except Exception:  # progress kabhi kaam nahi rokta
                        pass
            rc = proc.wait()
        finally:
            timer.cancel()
    if timed_out.is_set():
        tail.append(f"TIMEOUT: {int(timeout)} second se zyada lag gaye")
        rc = rc if rc not in (0, None) else -9
    return rc, "\n".join(tail)


def run_step(runner: Runner, cmd: list, env: dict[str, str], timeout: float, on_line, cwd: Path, what: str) -> None:
    rc, out = runner(cmd, env, timeout, on_line, cwd)
    if what == "prepare_model" and rc == CUSTOM_CODE_EXIT:
        raise Refused("Is model me custom code (auto_map) hai. Hum kisi aur ka code nahi chalate, isliye ye model nahi liya ja sakta.")
    if rc != 0:
        raise RuntimeError(f"{what} fail hua (code {rc}): {out[-300:]}")


# ---------------------------------------------------------------- progress
class Reporter:
    """Steps aur progress Supabase me likhta hai. Likhai fail ho to sirf warning: job isse nahi marta."""

    def __init__(self, db: Supabase, job_id: str, labels: list[str], log: Callable[[str], None] = print):
        self.db, self.job_id, self.labels, self.log = db, job_id, labels, log
        self.idx, self.progress, self._warned = 0, 0, False

    def steps(self, idx: int, state: str = "running") -> list[dict[str, str]]:
        return [{"label": l, "state": "done" if i < idx else (state if i == idx else "pending")} for i, l in enumerate(self.labels)]

    def all_done(self) -> list[dict[str, str]]:
        return [{"label": l, "state": "done"} for l in self.labels]

    def set(self, idx: int, progress: int, state: str = "running") -> None:
        self.idx = idx
        self.progress = max(self.progress, min(int(progress), 99))  # progress kabhi peeche nahi jaata; 100 sirf done par
        self._write({"step": self.labels[idx], "progress": self.progress, "steps": self.steps(idx, state)})

    def bump(self, progress: int) -> None:
        p = max(self.progress, min(int(progress), 99))
        if p != self.progress:
            self.progress = p
            self._write({"progress": p})

    def _write(self, fields: dict[str, Any]) -> None:
        try:
            self.db.patch_job(self.job_id, fields)
        except SupabaseError as e:
            if not self._warned:  # ek baar bolo, baar-baar nahi
                self.log(f"warning: progress likhna fail hua (job chalta rahega): {e}")
                self._warned = True


def line_progress(rep: Reporter, start: int, end: int, total: int, pattern: str) -> Callable[[str], None]:
    """Har matching output line par progress start..end ke beech badhao."""
    rx = re.compile(pattern)
    seen = {"n": 0}

    def on_line(line: str) -> None:
        if rx.search(line):
            seen["n"] += 1
            rep.bump(start + (end - start) * min(seen["n"], total) // total)

    return on_line


def write_final(db: Supabase, job_id: str, fields: dict[str, Any], retries: int = 3, sleep: Callable[[float], None] = time.sleep,
                log: Callable[[str], None] = print) -> bool:
    """Aakhri result 3 baar tak koshish se likho. Fail ho to job 'running' rehta hai aur fail_stale_jobs use pakad leta hai."""
    for attempt in range(retries):
        try:
            db.patch_job(job_id, fields)
            return True
        except SupabaseError as e:
            log(f"warning: final result likhna fail hua ({attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                sleep(min(2 ** attempt, 5))
    return False


# ---------------------------------------------------------------- work folder
def tag_of(model: str) -> str:
    return model.replace("/", "__")


def make_workdir(root: Path, job_id: str) -> Path:
    """work/<job-id>: scripts ki copy (taaki prepare_model.py / trl4_bench.py yahin models/ banayein, aapke apne
    models/ me nahi) + llama.cpp aur data ke symlink (bade aur shared hain)."""
    wd = root / "work" / job_id
    shutil.rmtree(wd, ignore_errors=True)
    (wd / "scripts").mkdir(parents=True)
    for f in (root / "scripts").glob("*.py"):
        shutil.copy2(f, wd / "scripts" / f.name)
    (root / "data").mkdir(exist_ok=True)
    (wd / "reports").mkdir()
    try:
        os.symlink(root / "data", wd / "data", target_is_directory=True)
        if (root / "llama.cpp").exists():
            os.symlink(root / "llama.cpp", wd / "llama.cpp", target_is_directory=True)
    except OSError as e:
        raise RuntimeError(f"symlink nahi ban paya ({e}). Linux / WSL2 / Mac par chalao.") from None
    return wd


def kld_files(data_dir: Path) -> set[str]:
    return {p.name for p in data_dir.glob("*.kld")} if data_dir.is_dir() else set()


def cleanup_job(root: Path, job_id: str, kld_before: set[str] | None = None) -> None:
    """Customer ka model, FP16, Q4 files, imatrix: sab work/<job-id> ke saath jaate hain. Shared data/ me is job ki
    nayi .kld files (bade hote hain) bhi hata do. data/ ki baaki files (Hindi test sets) cache hain, rehne do."""
    shutil.rmtree(root / "work" / job_id, ignore_errors=True)
    if kld_before is not None:
        for p in (root / "data").glob("*.kld"):
            if p.name not in kld_before:
                p.unlink(missing_ok=True)


def keep_output(root: Path, workdir: Path, model: str, best: str, job_id: str) -> Path | None:
    """Paid job 'improved': best GGUF outputs/<job-id>/ me le jao (haath se bhejne ke liye)."""
    src = workdir / "models" / "gguf" / f"{tag_of(model)}-Q4_K_M-{best}.gguf"
    if not src.is_file():
        return None
    dst_dir = root / "outputs" / job_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.move(str(src), str(dst))
    return dst


def purge_old_outputs(root: Path, max_age_hours: float = 2.0, now: float | None = None) -> int:
    """outputs/<job-id>/ 2 ghante baad delete (result page par yahi likha hai)."""
    out = root / "outputs"
    if not out.is_dir():
        return 0
    now = time.time() if now is None else now
    n = 0
    for d in out.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > max_age_hours * 3600:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def check_disk(root: Path, weights_gb: float, kind: str) -> None:
    """Weights, FP16 GGUF, Q4 files aur imatrix variants ke liye jagah. Kam ho to job fail (aapki taraf ki dikkat)."""
    need = weights_gb * 1e9 * (6 if kind == "calibrate" else 3) + 2e9
    free = shutil.disk_usage(root).free
    if free < need:
        raise RuntimeError(f"Disk me jagah kam hai: {free / 1e9:.1f} GB free, {need / 1e9:.1f} GB chahiye")


# ---------------------------------------------------------------- results
def _valid_metrics(results: Any, must_have: Iterable[str]) -> bool:
    if not isinstance(results, dict) or not results:
        return False
    for byv in results.values():
        if not isinstance(byv, dict):
            return False
        for v in must_have:
            m = byv.get(v)
            if not isinstance(m, dict) or any(not (isinstance(m.get(k), list) and len(m[k]) == 2) for k in ("kld", "rms", "top")):
                return False
    return True


def build_audit_result(model: str, info: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    results = report.get("results")
    if not _valid_metrics(results, ["plain"]):
        raise RuntimeError("audit report me plain Q4 ke numbers poore nahi hain")
    meta = report.get("variants_meta") or {}
    return {
        "kind": "audit",
        "model": model,
        "license": info.get("license"),
        "weights_gb": info.get("weights_gb"),
        "chunks": (report.get("settings") or {}).get("chunks"),
        "domains": report.get("domains") or {},
        "fp16_gb": (report.get("fp16") or {}).get("size_gb"),
        "quant_gb": (meta.get("plain") or {}).get("size_gb"),
        "results": results,
        "note": AUDIT_NOTE,
    }


def decide(results: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    """Calibrate ka faisla. 'improved' tabhi jab best imatrix variant plain Q4 se kam se kam ek test set me SAAF
    behtar ho (fark > 2 x error) aur kisi test set me SAAF kharab na ho. Warna 'no_clear_gain'."""
    doms = list(results)
    none = {"best": None, "decision": "no_clear_gain", "clear_domains": [], "reduction_pct": {}}
    if not doms:
        return none
    cands = [v for v in results[doms[0]]
             if v != "standard" and all(v in results[d] and "standard" in results[d] for d in doms)]
    if not cands:
        return none
    best = min(cands, key=lambda v: sum(results[d][v]["kld"][0] for d in doms) / len(doms))
    clear, worse, reduction = [], [], {}
    for d in doms:
        b, s = results[d][best], results[d]["standard"]
        g, err = audit.gain(b, s)
        if g > 2 * err:
            clear.append(d)
        g2, err2 = audit.gain(s, b)
        if g2 > 2 * err2:
            worse.append(d)
        if s["kld"][0] > 0:
            reduction[d] = round(100 * (1 - b["kld"][0] / s["kld"][0]), 1)
    return {"best": best, "decision": "improved" if clear and not worse else "no_clear_gain",
            "clear_domains": clear, "reduction_pct": reduction}


def build_calibrate_result(model: str, info: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    if not _valid_metrics(results, ["standard"]):
        raise RuntimeError("calibrate results me plain Q4 ke numbers poore nahi hain")
    return {"kind": "calibrate", "model": model, "license": info.get("license"), "weights_gb": info.get("weights_gb"),
            "results": results, **decide(results)}


# ---------------------------------------------------------------- the two pipelines
def run_audit(rep: Reporter, wd: Path, model: str, info: dict[str, Any], runner: Runner, env: dict[str, str], threads: int) -> dict[str, Any]:
    tag = tag_of(model)
    f16 = wd / "models" / "gguf" / f"{tag}-f16.gguf"
    std = wd / "models" / "gguf" / f"{tag}-Q4_K_M-standard.gguf"

    rep.set(1, 8)
    run_step(runner, [sys.executable, wd / "scripts" / "prepare_model.py", "--model", model], env, PREPARE_TIMEOUT, None, wd, "prepare_model")
    for f in (f16, std):
        if not f.is_file():
            raise RuntimeError(f"prepare_model ke baad {f.name} nahi mili")

    rep.set(2, 35)
    name = model.split("/")[-1]
    cmd = [sys.executable, wd / "scripts" / "audit.py", "--name", name, "--base-model", model, "--fp16", f16,
           "--quant", f"plain={std}", "--out", wd / "reports", "--threads", threads]
    # 2 test sets x (FP16 base + plain Q4) = 4 naap
    on_line = line_progress(rep, 35, 90, 4, r"naap raha hu|base bana raha hu")
    run_step(runner, cmd, env, AUDIT_TIMEOUT, on_line, wd, "audit")

    rep.set(3, 92)
    report_path = wd / "reports" / audit.slugify(name) / "report.json"
    if not report_path.is_file():
        raise RuntimeError("audit ke baad report.json nahi mili")
    return build_audit_result(model, info, json.loads(report_path.read_text(encoding="utf-8")))


def run_calibrate(rep: Reporter, wd: Path, root: Path, job_id: str, model: str, info: dict[str, Any], runner: Runner,
                  env: dict[str, str], threads: int, log: Callable[[str], None]) -> dict[str, Any]:
    tag = tag_of(model)

    rep.set(1, 6)
    run_step(runner, [sys.executable, wd / "scripts" / "prepare_model.py", "--model", model], env, PREPARE_TIMEOUT, None, wd, "prepare_model")

    rep.set(2, 22)
    if not (root / "data" / "hindi_calib.txt").is_file():
        run_step(runner, [sys.executable, wd / "scripts" / "download_hindi_data.py"], env, DATA_TIMEOUT, None, wd, "download_hindi_data")

    rep.set(3, 30)
    # 3 imatrix (imatrix + quantize) + 2 test sets x (FP16 base + 4 variants) = 16 events
    on_line = line_progress(rep, 30, 90, 16, r"imatrix ban raha|quantize ho raha|naap raha hu|base bana raha hu")
    cmd = [sys.executable, wd / "scripts" / "trl4_bench.py", "--model", model, "--threads", threads]
    run_step(runner, cmd, env, CALIBRATE_TIMEOUT, on_line, wd, "trl4_bench")

    rep.set(4, 94)
    res_path = wd / f"results_trl4_{tag}.json"
    if not res_path.is_file():
        raise RuntimeError("trl4_bench ke baad results file nahi mili")
    result = build_calibrate_result(model, info, json.loads(res_path.read_text(encoding="utf-8")))
    if result["decision"] == "improved" and result["best"]:
        kept = keep_output(root, wd, model, result["best"], job_id)
        if kept:
            result["output_gb"] = round(kept.stat().st_size / 1e9, 2)
            log(f"GGUF yahan rakhi hai (2 ghante baad delete): {kept}")
    return result


# ---------------------------------------------------------------- one job
def process_job(db: Supabase, job: dict[str, Any], *, root: Path = ROOT, runner: Runner = run_streaming,
                fetch_json: Callable[[str], Any] = http_get_json, env: dict[str, str] | None = None, threads: int = 2,
                sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = print) -> str:
    """Ek job poora chalao. Wapas: 'done' | 'refused' | 'failed'. Kabhi exception nahi uthata (KeyboardInterrupt ko chhodkar)."""
    job_id = str(job.get("id", ""))
    if not UUID_RE.match(job_id):
        log(f"ROKA: job id UUID nahi hai: {job_id!r}")
        return "failed"
    kind = job.get("kind") or "audit"
    labels = CALIBRATE_STEPS if kind == "calibrate" else AUDIT_STEPS
    rep = Reporter(db, job_id, labels, log)
    env = clean_env() if env is None else env
    secret_values = secrets_in_env()
    model = normalize_model_id(job.get("hf_model"))
    kld_before = kld_files(root / "data")
    log(f"Job {job_id}: {kind} {model or job.get('hf_model')!r}")

    try:
        if not model:
            raise Refused("Model ID sahi nahi hai.")
        rep.set(0, 2)
        info = check_model(model, kind, fetch_json)
        check_disk(root, info["weights_gb"], kind)
        wd = make_workdir(root, job_id)
        if kind == "calibrate":
            result = run_calibrate(rep, wd, root, job_id, model, info, runner, env, threads, log)
        else:
            result = run_audit(rep, wd, model, info, runner, env, threads)
        write_final(db, job_id, {"status": "done", "progress": 100, "step": "Poora hua", "steps": rep.all_done(),
                                 "result": result, "error": None}, sleep=sleep, log=log)
        log(f"Job {job_id}: done")
        return "done"
    except Refused as e:
        write_final(db, job_id, {"status": "refused", "step": rep.labels[rep.idx], "steps": rep.steps(rep.idx, "failed"),
                                 "error": str(e)[:300]}, sleep=sleep, log=log)
        log(f"Job {job_id}: refused ({e})")
        return "refused"
    except Exception as e:  # noqa: BLE001 - kuch bhi ho, job ko failed likhna hai
        write_final(db, job_id, {"status": "failed", "step": rep.labels[rep.idx], "steps": rep.steps(rep.idx, "failed"),
                                 "error": safe_error(e, secret_values)}, sleep=sleep, log=log)
        log(f"Job {job_id}: failed ({safe_error(e, secret_values)})")
        return "failed"
    except KeyboardInterrupt:
        write_final(db, job_id, {"status": "failed", "steps": rep.steps(rep.idx, "failed"),
                                 "error": "Job fail hua: worker beech me band kiya gaya."}, retries=1, sleep=sleep, log=log)
        raise
    finally:
        cleanup_job(root, job_id, kld_before)


# ---------------------------------------------------------------- loop
def preflight(root: Path) -> None:
    """Pehli galti jaldi pakdo (job claim karne se pehle)."""
    for need in (root / "llama.cpp" / "build" / "bin" / "llama-perplexity",
                 root / "llama.cpp" / "build" / "bin" / "llama-quantize",
                 root / "llama.cpp" / "build" / "bin" / "llama-imatrix"):
        if not need.exists():
            raise SystemExit(f"ROKA: {need} nahi mila. Pehle `bash scripts/setup.sh` chalao.")


def housekeeping(db: Supabase, root: Path, stale_minutes: int, output_hours: float, log: Callable[[str], None] = print) -> None:
    for fn, args in (("fail_stale_jobs", {"p_minutes": stale_minutes}), ("purge_old_jobs", {})):
        try:
            n = db.rpc(fn, args)
            if n:
                log(f"{fn}: {n}")
        except SupabaseError as e:
            log(f"warning: {fn} fail hua: {e}")
    purge_old_outputs(root, output_hours)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="ek job (agar hai) chalao aur ruko")
    ap.add_argument("--poll-seconds", type=float, default=20.0)
    ap.add_argument("--stale-minutes", type=int, default=180, help="itne minute koi update na aaye to running job failed")
    ap.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 2), help="llama.cpp threads")
    ap.add_argument("--keep-outputs-hours", type=float, default=2.0)
    ap.add_argument("--skip-preflight", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv: list[str] | None = None, *, db: Supabase | None = None, runner: Runner = run_streaming,
         fetch_json: Callable[[str], Any] = http_get_json, sleep: Callable[[float], None] = time.sleep,
         root: Path = ROOT, log: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    if not args.skip_preflight and runner is run_streaming:
        preflight(root)
    db = db or Supabase.from_env()
    housekeeping(db, root, args.stale_minutes, args.keep_outputs_hours, log)
    last_house = time.monotonic()
    log("Worker chalu. Queue dekh raha hoon..." if not args.once else "Worker (--once): ek job dekhta hoon...")

    while True:
        try:
            job = db.rpc("claim_next_job")
        except SupabaseError as e:
            log(f"Queue padhna fail hua: {e}")
            if args.once:
                return 1
            sleep(args.poll_seconds)
            continue
        if not job:
            if args.once:
                log("Queue khaali hai.")
                return 0
            sleep(args.poll_seconds)
        else:
            process_job(db, job, root=root, runner=runner, fetch_json=fetch_json, threads=args.threads, sleep=sleep, log=log)
            if args.once:
                return 0
        if time.monotonic() - last_house > 600:
            housekeeping(db, root, args.stale_minutes, args.keep_outputs_hours, log)
            last_house = time.monotonic()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nWorker band kiya gaya.")
        sys.exit(130)
