"""Worker: takes jobs from the Supabase queue and runs them on THIS computer (llama.cpp must be built here).

    python scripts/worker.py --check     # pehle ye: kya-kya taiyaar hai, kya kam hai
    python scripts/worker.py --once      # ek job chalao aur ruko (testing ke liye)
    python scripts/worker.py             # hamesha chalta raho, har 15 second me queue dekho

Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment (see .env.example). Never in .env.local / Vercel.

Free audit (kind "audit"): check model on Hugging Face -> download -> FP16 GGUF + plain Q4_K_M (scripts/prepare_model.py)
-> KLD vs FP16 on the Hindi test sets (scripts/audit.py) -> result written into the job row. The model files are
deleted afterwards. Paid calibration (kind "calibrate", queued by `scripts/admin.py enqueue`) reuses
scripts/trl4_bench.py and keeps the Q4 files so you can send the best one by hand.

Safety rules this file keeps:
  * only public, non-gated, safetensors models up to ~4 GB are accepted; everything else is a polite "refused";
  * scripts started for a visitor's model get an environment WITHOUT HF_TOKEN / HF_WRITE_TOKEN / Supabase keys;
  * anything stored in a job row goes through scrub(): no tokens, short, one line.
"""
from __future__ import annotations

import argparse
import json
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
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from sthamly_common import Supabase, SupabaseError, normalize_model_id, scrub  # noqa: E402

# Labels shown on the job page. AUDIT_STEPS must match create_audit_job() in db/schema_v3.sql;
# CALIBRATE_STEPS must match QUEUED_STEPS in scripts/admin.py. Unit tests check both.
AUDIT_STEPS = [
    "Model Hugging Face par check",
    "Weights download",
    "FP16 aur Q4 banana",
    "Hindi test sets par FP16 se naap",
    "Result likhna",
]
CALIBRATE_STEPS = [
    "Model ki jaankari check ho rahi hai",
    "Model download aur FP16 GGUF ban raha hai",
    "Hindi calibration data taiyaar ho raha hai",
    "Hindi / English / Mixed imatrix ban rahe hain aur naape ja rahe hain",
    "Best variant chuna ja raha hai",
]

CUSTOM_CODE_EXIT = 3  # same number as in scripts/prepare_model.py
SECRET_ENV = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_WRITE_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
STALE_MINUTES = 120
AUDIT_NOTE = ("Plain Q4_K_M, usi FP16 file se bana, FP16 ke muqable KLD. Proxy naap hai, Hindi task accuracy nahi.")


class Refused(Exception):
    """A job we decline on purpose. The message is shown to the visitor, so keep it short, plain and polite."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc: BaseException, extra_secrets: tuple[str, ...] = ()) -> str:
    """The text stored for a failed job: no tokens, one line, short. The website never shows it to visitors."""
    return "Job fail hua: " + scrub(f"{exc}", extra_secrets, limit=400)


@dataclass
class Config:
    root: Path = ROOT
    threads: int = 2
    chunks: int = 12
    max_weights_gb: float = 4.2
    keep_files: bool = False
    prepare_timeout: int = 3600
    audit_timeout: int = 3 * 3600
    python: str = sys.executable

    @property
    def llama_bin(self) -> Path:
        return self.root / "llama.cpp" / "build" / "bin"


def clean_env() -> dict[str, str]:
    """Environment for scripts that handle a visitor's model: no tokens and no database keys."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


def secret_values() -> tuple[str, ...]:
    return tuple(v for k in SECRET_ENV if (v := os.environ.get(k, "").strip()))


# ---------------------------------------------------------------- Hugging Face check
def hf_model_info(model: str, timeout: float = 20.0) -> dict[str, Any]:
    """Public metadata of a model repo (no token is sent). 401/403/404 all mean 'not public' -> refused."""
    url = "https://huggingface.co/api/models/" + urllib.parse.quote(model, safe="/") + "?blobs=true"
    req = urllib.request.Request(url, headers={"User-Agent": "sthamly-worker"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        code = e.code
        e.close()
        if code in (401, 403, 404):
            raise Refused("Ye model Hugging Face par public nahi mila (ID galat hai, ya model private ya gated hai).") from None
        raise RuntimeError(f"Hugging Face ne {code} diya") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Hugging Face tak nahi pahunch paya: {getattr(e, 'reason', e)}") from None


def check_model(info: dict[str, Any], max_gb: float) -> dict[str, Any]:
    """Raises Refused for models we do not take. Returns {license, weights_gb} for the ones we do."""
    if info.get("private") or info.get("gated"):
        raise Refused("Ye model gated ya private hai. Abhi sirf public models liye jaate hain.")
    files = [s for s in (info.get("siblings") or []) if str(s.get("rfilename", "")).endswith(".safetensors")]
    if not files:
        raise Refused("Is repo me safetensors weights nahi hain. Abhi sirf safetensors wale models liye jaate hain.")
    sizes = [s.get("size") for s in files]
    if not all(isinstance(x, (int, float)) for x in sizes):
        raise Refused("Weights ka size pata nahi chal paya, isliye ye model abhi nahi liya ja sakta.")
    gb = sum(sizes) / 1e9
    if gb > max_gb:
        raise Refused(f"Weights {gb:.1f} GB ke hain. Abhi lagbhag {max_gb:g} GB tak ke models hi liye jaate hain.")
    lic = (info.get("cardData") or {}).get("license")
    if isinstance(lic, list):
        lic = ", ".join(str(x) for x in lic)
    if not lic:
        lic = next((t.split(":", 1)[1] for t in info.get("tags") or [] if str(t).startswith("license:")), None)
    return {"license": scrub(lic, limit=60) if lic else None, "weights_gb": round(gb, 2)}


# ---------------------------------------------------------------- job row updates
class JobState:
    """Keeps the step list and progress of one job and writes them to Supabase (cosmetic writes never kill a job)."""

    def __init__(self, db: Supabase, job_id: str, labels: list[str]):
        self.db, self.id = db, job_id
        self.labels = labels
        self.steps = [{"label": label, "state": "pending"} for label in labels]
        self.progress = 0

    def _push(self, step: str | None = None) -> None:
        fields: dict[str, Any] = {"steps": self.steps, "progress": self.progress, "heartbeat_at": now_iso()}
        if step is not None:
            fields["step"] = step
        try:
            self.db.patch_job(self.id, fields)
        except SupabaseError as e:
            print("  (progress update fail, kaam chal raha hai:", e, ")", flush=True)

    def start(self, i: int, progress: int | None = None) -> None:
        for k, s in enumerate(self.steps):
            if k < i:
                s["state"] = "done"
        self.steps[i]["state"] = "running"
        if progress is not None:
            self.progress = max(self.progress, min(99, int(progress)))
        print(f"  [{i + 1}/{len(self.steps)}] {self.steps[i]['label']}", flush=True)
        self._push(self.steps[i]["label"])

    def advance(self, progress: float) -> None:
        p = max(0, min(99, int(progress)))
        if p > self.progress:
            self.progress = p
            self._push()

    def stop_running(self, to: str) -> None:
        for s in self.steps:
            if s["state"] == "running":
                s["state"] = to
        self._push()

    def reset(self) -> dict[str, Any]:
        self.steps = [{"label": label, "state": "pending"} for label in self.labels]
        self.progress = 0
        return {"status": "queued", "progress": 0, "step": "Queue me hai", "steps": self.steps,
                "started_at": None, "heartbeat_at": None}

    def finish_ok(self, result: dict[str, Any]) -> None:
        for s in self.steps:
            s["state"] = "done"
        fields = {"status": "done", "progress": 100, "step": None, "steps": self.steps, "result": result,
                  "error": None, "finished_at": now_iso()}
        for attempt in range(3):  # the final write matters: try a few times
            try:
                self.db.patch_job(self.id, fields)
                return
            except SupabaseError:
                if attempt == 2:
                    raise
                time.sleep(2)


class Heartbeat:
    """While a long command runs, tell the database every minute that this worker is still alive."""

    def __init__(self, db: Supabase, job_id: str, every: float = 60.0):
        self.db, self.id, self.every = db, job_id, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.wait(self.every):
            try:
                self.db.patch_job(self.id, {"heartbeat_at": now_iso()})
            except Exception:  # noqa: BLE001 - a missed heartbeat must never stop the job
                pass

    def __enter__(self) -> "Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


# ---------------------------------------------------------------- running commands
def run_stream(cmd: list[Any], *, cwd: Path, env: dict[str, str], on_line: Callable[[str], None] | None = None,
               timeout: float | None = None, tail: int = 40) -> tuple[int, str]:
    """Runs a command, hands every output line to on_line, returns (exit code, last lines). Kills it after `timeout`."""
    proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, kill) if timeout else None
    if timer:
        timer.start()
    last: deque[str] = deque(maxlen=tail)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            last.append(line)
            if on_line:
                on_line(line)
        code = proc.wait()
    finally:
        if timer:
            timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout:
            proc.stdout.close()
    if timed_out.is_set():
        name = Path(str(cmd[1])).name if len(cmd) > 1 else str(cmd[0])
        raise TimeoutError(f"{name} {int(timeout or 0)} second me poora nahi hua")
    return code, "\n".join(last)


def model_paths(cfg: Config, model: str) -> dict[str, Path]:
    tag = model.replace("/", "__")
    gguf = cfg.root / "models" / "gguf"
    return {"tag": Path(tag), "hf": cfg.root / "models" / "hf" / tag, "f16": gguf / f"{tag}-f16.gguf",
            "std": gguf / f"{tag}-Q4_K_M-standard.gguf", "gguf_dir": gguf}


def purge_model_files(cfg: Config, model: str, keep_variants: bool = False) -> None:
    """Removes downloaded weights and big temporary files of one model. Never raises."""
    p = model_paths(cfg, model)
    tag = str(p["tag"])
    shutil.rmtree(p["hf"], ignore_errors=True)
    for f in [p["f16"], *p["gguf_dir"].glob(f"{tag}.*.imatrix"), *p["gguf_dir"].glob(f"{tag}.imatrix")]:
        Path(f).unlink(missing_ok=True)
    if not keep_variants:
        for f in p["gguf_dir"].glob(f"{tag}-Q4_K_M-*.gguf"):
            f.unlink(missing_ok=True)
    data = cfg.root / "data"
    for f in [*data.glob("audit_*.kld"), *data.glob(f"f16_*_{tag}.kld")]:  # FP16 reference logits: can be GBs
        f.unlink(missing_ok=True)


def check_disk(cfg: Config, weights_gb: float) -> None:
    free_gb = shutil.disk_usage(cfg.root).free / 1e9
    need = weights_gb * 3 + 3
    if free_gb < need:
        raise RuntimeError(f"Disk me jagah kam hai ({free_gb:.1f} GB free, {need:.1f} GB chahiye)")


def load_eval_sets(cfg: Config) -> dict[str, tuple[str, int]]:
    """{test set: (file, lines)}. Builds missing sets once (needs internet + `pip install datasets`)."""
    from hindi_eval_data import ensure_eval_sets
    try:
        return ensure_eval_sets(cfg.root / "data", log=lambda m: print("  " + str(m), flush=True))
    except RuntimeError as e:
        raise RuntimeError(f"Hindi test set nahi mile: {e}") from None


def run_prepare(model: str, cfg: Config, state: JobState, split_steps: bool, progress: tuple[int, int]) -> None:
    """scripts/prepare_model.py: download, FP16 GGUF, plain Q4.

    progress = (when the FP16 conversion starts, when plain Q4 starts). With split_steps the job page gets a new
    step at the conversion (audit: "Weights download" then "FP16 aur Q4 banana"); without it only the bar moves.
    """
    p = model_paths(cfg, model)

    def on_line(line: str) -> None:
        if line.startswith("[2/3]"):
            if split_steps:
                state.start(2, progress[0])
            else:
                state.advance(progress[0])
        elif line.startswith("[3/3]"):
            state.advance(progress[1])

    code, tail = run_stream([cfg.python, HERE / "prepare_model.py", "--model", model], cwd=cfg.root, env=clean_env(),
                            on_line=on_line, timeout=cfg.prepare_timeout)
    if code == CUSTOM_CODE_EXIT:
        raise Refused("Is model me custom code (auto_map) hai. Hum kisi doosre ka code nahi chalate.")
    if code != 0:
        raise RuntimeError(f"prepare_model.py fail hua (exit {code}): {tail[-300:]}")
    for need in (p["f16"], p["std"]):
        if not Path(need).is_file():
            raise RuntimeError(f"prepare_model.py ke baad {Path(need).name} nahi mili")


# ---------------------------------------------------------------- free audit
def build_audit_result(model: str, meta: dict[str, Any], rep: dict[str, Any]) -> dict[str, Any]:
    """The `result` stored on an audit job (shape: AuditResult in lib/jobs.ts) from audit.py's report.json."""
    results = rep.get("results") or {}
    if not results or not all("plain" in byv and len(byv["plain"]) == 3 for byv in results.values()):
        raise RuntimeError("audit.py ne koi number nahi diya")
    plain_meta = (rep.get("variants_meta") or {}).get("plain", {})
    return {
        "kind": "audit",
        "model": model,
        "license": meta.get("license"),
        "weights_gb": meta["weights_gb"],
        "chunks": rep["settings"]["chunks"],
        "domains": rep["domains"],
        "fp16_gb": rep["fp16"]["size_gb"],
        "quant_gb": plain_meta.get("size_gb"),
        "results": results,
        "note": AUDIT_NOTE,
    }


def run_audit(db: Supabase, job_id: str, model: str, cfg: Config, state: JobState) -> None:
    p = model_paths(cfg, model)
    out_dir = cfg.root / "work" / "reports" / job_id

    state.start(0, 3)
    meta = check_model(hf_model_info(model), cfg.max_weights_gb)
    check_disk(cfg, meta["weights_gb"])
    purge_model_files(cfg, model)  # start clean: a half-written file from a crashed run must never be reused
    try:
        with Heartbeat(db, job_id):
            state.start(1, 8)
            run_prepare(model, cfg, state, split_steps=True, progress=(20, 38))

            state.start(3, 40)
            doms = load_eval_sets(cfg)
            total = max(1, 2 * len(doms))  # per test set: one FP16 base + one plain Q4 measurement
            seen = 0

            def on_audit(line: str) -> None:
                nonlocal seen
                if re.match(r"^\[[^\]]+\] .*(bana raha hu|naap raha hu)", line):
                    state.advance(40 + 55 * seen / total)
                    seen += 1

            cmd = [cfg.python, HERE / "audit.py", "--name", model.split("/", 1)[1], "--fp16", p["f16"],
                   "--quant", f"plain={p['std']}", "--base-model", model, "--chunks", cfg.chunks,
                   "--threads", cfg.threads, "--llama", cfg.llama_bin, "--data-dir", cfg.root / "data",
                   "--out", out_dir]
            code, tail = run_stream(cmd, cwd=cfg.root, env=clean_env(), on_line=on_audit, timeout=cfg.audit_timeout)
            if code != 0:
                raise RuntimeError(f"audit.py fail hua (exit {code}): {tail[-300:]}")

            state.start(4, 96)
            reports = sorted(out_dir.glob("*/report.json"))
            if not reports:
                raise RuntimeError("audit.py ne report.json nahi banaya")
            rep = json.loads(reports[0].read_text(encoding="utf-8"))
            result = build_audit_result(model, meta, rep)
        state.finish_ok(result)
    finally:
        if not cfg.keep_files:
            purge_model_files(cfg, model)


# ---------------------------------------------------------------- paid calibration
def _gain(a: dict[str, Any], b: dict[str, Any]) -> tuple[float, float]:
    """How much better a is than b in KLD (positive = a better) and the combined error. Same rule as lib/verdict.ts."""
    return b["kld"][0] - a["kld"][0], (a["kld"][1] ** 2 + b["kld"][1] ** 2) ** 0.5


def calibrate_decision(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Best imatrix variant against plain Q4, said only as far as the numbers go (a gain counts when > 2x its error)."""
    doms = list(results)
    nothing = {"decision": "no_clear_gain", "best": None, "clear_domains": [], "reduction_pct": {}}
    if not doms or not all("standard" in results[d] for d in doms):
        return nothing
    variants = [v for v in results[doms[0]] if v != "standard" and all(v in results[d] for d in doms)]
    if not variants:
        return nothing
    best = min(variants, key=lambda v: sum(results[d][v]["kld"][0] for d in doms) / len(doms))
    clear, worse, reduction = [], False, {}
    for d in doms:
        g, err = _gain(results[d][best], results[d]["standard"])
        if g > 2 * err:
            clear.append(d)
        elif -g > 2 * err:
            worse = True
        std = results[d]["standard"]["kld"][0]
        if std > 0:
            reduction[d] = round(100 * (1 - results[d][best]["kld"][0] / std), 1)
    improved = bool(clear) and not worse
    return {"decision": "improved" if improved else "no_clear_gain", "best": best, "clear_domains": clear,
            "reduction_pct": reduction}


def run_calibrate(db: Supabase, job_id: str, model: str, cfg: Config, state: JobState) -> None:
    p = model_paths(cfg, model)
    tag = str(p["tag"])
    results_file = cfg.root / f"results_trl4_{tag}.json"

    state.start(0, 3)
    meta = check_model(hf_model_info(model), cfg.max_weights_gb)
    check_disk(cfg, meta["weights_gb"] * 2)
    try:
        with Heartbeat(db, job_id):
            state.start(1, 8)
            run_prepare(model, cfg, state, split_steps=False, progress=(14, 20))

            state.start(2, 22)
            calib = cfg.root / "data" / "hindi_calib.txt"
            if not calib.exists():  # public dataset: runs with the normal environment on purpose
                code, tail = run_stream([cfg.python, HERE / "download_hindi_data.py"], cwd=cfg.root, env=dict(os.environ),
                                        timeout=1800)
                if code != 0 or not calib.exists():
                    raise RuntimeError(f"download_hindi_data.py fail hua: {tail[-300:]}")
            load_eval_sets(cfg)

            state.start(3, 28)
            total, seen = 16, 0  # rough count of imatrix / quantize / measure steps, only for the progress bar

            def on_bench(line: str) -> None:
                nonlocal seen
                if re.search(r"(naap raha hu|imatrix ban raha hai|quantize ho raha hai|base bana raha hu)", line):
                    seen += 1
                    state.advance(28 + 62 * min(seen, total) / total)

            code, tail = run_stream([cfg.python, HERE / "trl4_bench.py", "--model", model, "--threads", cfg.threads],
                                    cwd=cfg.root, env=clean_env(), on_line=on_bench, timeout=cfg.audit_timeout * 2)
            if code != 0:
                raise RuntimeError(f"trl4_bench.py fail hua (exit {code}): {tail[-300:]}")

            state.start(4, 95)
            if not results_file.exists():
                raise RuntimeError("trl4_bench.py ne results file nahi banayi")
            results = json.loads(results_file.read_text(encoding="utf-8"))
            decision = calibrate_decision(results)
            result: dict[str, Any] = {"kind": "calibrate", "model": model, "license": meta["license"],
                                      "weights_gb": meta["weights_gb"], "results": results, **decision}
            if decision["best"]:
                best_file = p["gguf_dir"] / f"{tag}-Q4_K_M-{decision['best']}.gguf"
                if best_file.is_file():
                    result["output_gb"] = round(best_file.stat().st_size / 1e9, 2)
        state.finish_ok(result)
    finally:
        if not cfg.keep_files:
            purge_model_files(cfg, model, keep_variants=True)  # the Q4 files stay: you send the best one by hand


# ---------------------------------------------------------------- one job, and the loop
RUNNERS = {"audit": (run_audit, AUDIT_STEPS), "calibrate": (run_calibrate, CALIBRATE_STEPS)}


def process_one(db: Supabase, cfg: Config) -> bool:
    """Claims and runs one job. Returns False when the queue is empty."""
    claimed = db.rpc("claim_next_job")
    if not claimed:
        return False
    job_id, kind, model = claimed["id"], claimed["kind"], claimed["hf_model"]
    secrets = secret_values()
    print(f"\nJob {job_id}: {kind} {model}", flush=True)
    runner, labels = RUNNERS.get(kind, (None, []))
    state = JobState(db, job_id, labels or ["Kaam"])
    try:
        if runner is None:
            raise Refused("Ye kism ka kaam abhi nahi liya jaata.")
        clean_model = normalize_model_id(model)  # the database already checked, but the worker runs commands: check again
        if clean_model is None:
            raise Refused("Model ID sahi nahi hai. Aise likho: owner/model-name")
        runner(db, job_id, clean_model, cfg, state)
        print("  poora hua.", flush=True)
    except Refused as e:
        print(f"  refused: {e}", flush=True)
        state.stop_running("pending")
        db.patch_job(job_id, {"status": "refused", "error": scrub(e, secrets, limit=240), "finished_at": now_iso()})
    except KeyboardInterrupt:
        print("\n  roka gaya: job wapas line me daal diya.", flush=True)
        try:
            db.patch_job(job_id, state.reset())
        except SupabaseError:
            pass
        raise
    except Exception as e:  # noqa: BLE001 - any failure becomes a failed job, never a dead worker
        print(f"  fail: {safe_error(e, secrets)}", flush=True)
        state.stop_running("failed")
        db.patch_job(job_id, {"status": "failed", "error": safe_error(e, secrets), "finished_at": now_iso()})
    return True


# ---------------------------------------------------------------- --check
def preflight(cfg: Config, db: Supabase | None = None) -> int:
    """Prints what is ready and what is missing. Returns 0 only if a job could run."""
    bad = 0

    def line(ok: bool, what: str, fix: str = "", required: bool = True) -> None:
        nonlocal bad
        mark = "OK  " if ok else ("KAM " if required else "note")
        print(f"[{mark}] {what}" + ("" if ok or not fix else f"  ->  {fix}"))
        if not ok and required:
            bad += 1

    print(f"Python {sys.version.split()[0]}, folder: {cfg.root}")
    for mod in ("huggingface_hub", "datasets"):
        try:
            __import__(mod)
            line(True, f"python package {mod}")
        except ImportError:
            line(False, f"python package {mod}", "source .venv/bin/activate && pip install -r requirements.txt")
    for name, path in [("llama.cpp convert script", cfg.root / "llama.cpp" / "convert_hf_to_gguf.py"),
                       ("llama-quantize", cfg.llama_bin / "llama-quantize"),
                       ("llama-perplexity", cfg.llama_bin / "llama-perplexity"),
                       ("llama-imatrix (sirf paid calibrate ke liye)", cfg.llama_bin / "llama-imatrix")]:
        line(path.exists(), name, "bash scripts/setup.sh", required="imatrix" not in name)
    data = cfg.root / "data"
    sets = [f for f in ("eval_wiki.txt", "eval_sangraha.txt") if (data / f).exists()]
    line(bool(sets), "Hindi test sets (data/eval_*.txt)" + (f": {', '.join(sets)}" if sets else ""),
         "pehli baar audit chalane par apne-aap ban jaate hain (internet + datasets chahiye)", required=False)
    line((data / "hindi_calib.txt").exists(), "Hindi calibration text (sirf paid calibrate ke liye)",
         "python scripts/download_hindi_data.py", required=False)
    line(shutil.disk_usage(cfg.root).free > 15e9, f"disk free {shutil.disk_usage(cfg.root).free / 1e9:.0f} GB (15 GB+ achha hai)",
         "jagah banao", required=False)
    try:
        info = hf_model_info("Qwen/Qwen2.5-0.5B-Instruct", timeout=10)
        line(True, "Hugging Face tak network, safetensors size padh sakte hain" if info.get("siblings") else "Hugging Face API")
    except (Refused, RuntimeError) as e:
        line(False, "Hugging Face tak network", str(e))

    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        line(bool(os.environ.get(var, "").strip()), f"environment {var}", "export karo (.env.example dekho)")
    try:
        client = db or Supabase.from_env()
        client.select("jobs?select=id&limit=1")
        line(True, "Supabase: jobs table padh sakte hain")
        client.rpc("fail_stale_jobs", {"p_minutes": STALE_MINUTES})
        line(True, "Supabase: worker functions chal rahe hain")
    except SystemExit:
        pass  # the missing variables were already reported above
    except SupabaseError as e:
        line(False, "Supabase se baat", f"{e}  (db/schema.sql aur db/schema_v3.sql SQL editor me chalaye?)")

    print("\nSab taiyaar hai." if bad == 0 else f"\n{bad} cheez(en) kam hain. Upar KAM wali lines dekho.")
    return 0 if bad == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sthamly job worker", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="sirf jaanch: kya taiyaar hai aur kya kam hai")
    ap.add_argument("--once", action="store_true", help="ek job chalao aur ruk jao")
    ap.add_argument("--poll", type=int, default=15, help="queue kitne second baad dobara dekhe (default 15)")
    ap.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 2), help="llama.cpp threads")
    ap.add_argument("--chunks", type=int, default=12, help="eval chunks per test set")
    ap.add_argument("--keep-files", action="store_true", help="model files delete mat karo (debugging)")
    args = ap.parse_args(argv)
    cfg = Config(threads=args.threads, chunks=args.chunks, keep_files=args.keep_files)

    if args.check:
        return preflight(cfg)
    db = Supabase.from_env()
    last_purge = 0.0
    print("Worker chalu. Band karne ke liye Ctrl+C.", flush=True)
    while True:
        worked = False
        try:
            db.rpc("fail_stale_jobs", {"p_minutes": STALE_MINUTES})
            if time.time() - last_purge > 3600:
                db.rpc("purge_old_jobs", {"p_days": 30})
                last_purge = time.time()
            worked = process_one(db, cfg)
            if args.once and not worked:
                print("Queue me koi job nahi hai.")
        except SupabaseError as e:
            print("Supabase error:", e, flush=True)
        if args.once:
            return 0
        if not worked:
            time.sleep(args.poll)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
