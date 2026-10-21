"""Step 1 of the pipeline: download a model, convert it to FP16 GGUF, and build the
plain Q4_K_M baseline (no imatrix) from that SAME FP16 file.

Exit codes (backend in ko padh sakta hai):
    0  success
    1  runtime failure (download/convert/quantize/disk/lock)
    3  REFUSED: model me custom code (auto_map)           -> status "rejected"
    4  REFUSED: policy (bad id / license / size / no safetensors) -> status "rejected"
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

try:  # Linux (RunPod) par locking; Windows dev box par skip.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

ROOT = Path(__file__).resolve().parent.parent
LLAMA = ROOT / "llama.cpp"
HF_DIR = ROOT / "models" / "hf"
GGUF_DIR = ROOT / "models" / "gguf"
CONVERT = LLAMA / "convert_hf_to_gguf.py"
QUANTIZE = LLAMA / "build" / "bin" / "llama-quantize"

FAIL_EXIT = 1
CUSTOM_CODE_EXIT = 3
POLICY_EXIT = 4

# "org/name" -- sirf safe characters; path-traversal / shell-weird naam band.
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][\w.\-]*/[A-Za-z0-9][\w.\-]*$")
DEFAULT_LICENSES = "apache-2.0,mit"
DEFAULT_MAX_GB = 20.0          # RTX 3090 (24 GB) ke liye; env MAX_MODEL_GB se badlo
DISK_FACTOR = 2.5              # HF weights + FP16 GGUF + Q4 + margin


class PipelineError(Exception):
    """Controlled failure. `status` DB me jayega; `report=False` = DB ko mat chhedo."""

    def __init__(self, msg: str, code: int = FAIL_EXIT, status: str = "failed",
                 report: bool = True):
        super().__init__(msg)
        self.code = code
        self.status = status
        self.report = report


def run(cmd):
    """Run a command and let its output stream to the terminal."""
    cmd = [str(c) for c in cmd]
    print("$", " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0:
        raise PipelineError(f"FAILED: {' '.join(cmd[:3])} ...")


# --------------------------------------------------------------------------- DB
class JobReporter:
    """Supabase me live progress push karta hai.

    Preferred: --job-id / JOB_ID  -> sirf wahi ek row update hoti hai (id=eq.<job>).
    Legacy fallback (job id nahi): purana behaviour -- hf_model_identifier se match;
    ISSE us model ke SAARE jobs update ho jate hain, isliye warning aati hai.
    """

    def __init__(self, model_id: str, job_id: str | None):
        self.model_id = model_id
        self.job_id = (job_id or "").strip() or None
        self.url = (os.environ.get("SUPABASE_URL")
                    or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
        self.key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        self._warned_legacy = False
        self._warned_fail = False

    def _filter(self) -> str:
        if self.job_id:
            return "id=eq." + urllib.parse.quote(self.job_id, safe="")
        if not self._warned_legacy:
            print("[db-status] WARNING: --job-id nahi diya; model-name se update ho raha "
                  "hai (is model ke sab jobs prabhavit honge).", file=sys.stderr)
            self._warned_legacy = True
        return ("hf_model_identifier=eq."
                + urllib.parse.quote(self.model_id, safe=""))

    def update(self, status: str, progress: int | None = None) -> None:
        if not self.url or not self.key:
            return  # standalone terminal mode: chup-chap skip
        payload = {"status": status}
        if progress is not None:
            payload["progress"] = progress
        req = urllib.request.Request(
            f"{self.url}/rest/v1/quantization_jobs?{self._filter()}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "apikey": self.key,
                     "Authorization": f"Bearer {self.key}"},
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception as e:  # cosmetic -- compile ko kabhi mat roko, par chup bhi mat raho
            if not self._warned_fail:
                print(f"[db-status] update fail ({type(e).__name__}); "
                      "aage progress updates bhi fail ho sakte hain.", file=sys.stderr)
                self._warned_fail = True


# ---------------------------------------------------------------------- Safety
def validate_model_id(model_id: str) -> str:
    model_id = (model_id or "").strip()
    if not MODEL_ID_RE.match(model_id) or ".." in model_id:
        raise PipelineError(f"REFUSED: model id galat format ('org/name' chahiye): {model_id!r}",
                            POLICY_EXIT, "rejected")
    return model_id


def check_policy(model_id: str, revision: str | None, hf_token: str | None,
                 allowed_licenses: set[str], check_license: bool, max_gb: float):
    """Download se PEHLE: exists? safetensors? license? size? -> (commit_sha, total_bytes, license)."""
    try:
        info = HfApi(token=hf_token).model_info(model_id, revision=revision,
                                                files_metadata=True)
    except Exception as e:
        raise PipelineError(f"REFUSED: model info nahi mili ({type(e).__name__}) -- "
                            "private/gated/galat naam?", POLICY_EXIT, "rejected")

    st = [s for s in (info.siblings or []) if s.rfilename.endswith(".safetensors")]
    if not st:
        raise PipelineError("REFUSED: repo me .safetensors weights nahi hain.",
                            POLICY_EXIT, "rejected")

    total = sum((s.size or 0) for s in st)
    if total > max_gb * 1e9:
        raise PipelineError(f"REFUSED: weights {total / 1e9:.1f} GB hain; limit {max_gb:g} GB.",
                            POLICY_EXIT, "rejected")

    lic = None
    card = getattr(info, "card_data", None)
    if card is not None:
        lic = getattr(card, "license", None)
    if not lic:
        for t in info.tags or []:
            if t.startswith("license:"):
                lic = t.split(":", 1)[1]
                break
    if isinstance(lic, (list, tuple)):
        lic = lic[0] if lic else None
    lic_norm = (lic or "").strip().lower()

    if check_license and lic_norm not in allowed_licenses:
        raise PipelineError(f"REFUSED: license {lic or 'unknown'!r} allow-list me nahi "
                            f"({', '.join(sorted(allowed_licenses))}).", POLICY_EXIT, "rejected")

    return info.sha, total, lic_norm or "unknown"


def refuse_custom_code(model_dir: Path) -> None:
    cfg = model_dir / "config.json"
    if not cfg.exists():
        raise PipelineError("FAILED: config.json download nahi hua.")
    for name in ("config.json", "tokenizer_config.json"):
        f = model_dir / name
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            raise PipelineError(f"REFUSED: {name} padha nahi ja sakta.",
                                CUSTOM_CODE_EXIT, "rejected")
        if isinstance(data, dict) and "auto_map" in data:
            raise PipelineError(f"REFUSED: {name} me auto_map hai (custom code).",
                                CUSTOM_CODE_EXIT, "rejected")


def check_disk(total_weight_bytes: int) -> None:
    (ROOT / "models").mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(ROOT / "models").free
    need = int(total_weight_bytes * DISK_FACTOR)
    if free < need:
        raise PipelineError(f"FAILED: disk kam hai -- free {free / 1e9:.1f} GB, "
                            f"chahiye ~{need / 1e9:.1f} GB.")


@contextlib.contextmanager
def model_lock(name: str):
    """Ek hi model par do jobs ek saath files na bigaden."""
    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    with open(GGUF_DIR / f"{name}.lock", "w") as fh:
        if fcntl is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                # report=False: doosre chalte job ka DB status mat bigado
                raise PipelineError("Is model par doosra job already chal raha hai.",
                                    report=False)
        yield


def build_atomic(final: Path, builder) -> None:
    """`.part` me banao, phir rename -- adhoori file kabhi 'taiyar' nahi dikhegi."""
    part = final.with_name(final.name + ".part")
    part.unlink(missing_ok=True)
    try:
        builder(part)
        if not part.exists() or part.stat().st_size == 0:
            raise PipelineError(f"FAILED: {final.name} khali/missing bani.")
        os.replace(part, final)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


# -------------------------------------------------------------------- Pipeline
def prepare(model_id: str, hf_token: str | None, reporter: JobReporter, *,
            revision: str | None = None, allowed_licenses: set[str] | None = None,
            check_license: bool = True, max_gb: float = DEFAULT_MAX_GB,
            cleanup_hf: bool = False) -> None:
    for need in (CONVERT, QUANTIZE):
        if not need.exists():
            raise PipelineError(f"{need} nahi mila. Pehle `bash scripts/setup.sh` chalao.")

    model_id = validate_model_id(model_id)
    sha, total_bytes, lic = check_policy(model_id, revision, hf_token,
                                         allowed_licenses or set(), check_license, max_gb)
    print(f"[0/3] OK: {model_id} @ {sha[:8]} | license={lic} | weights={total_bytes / 1e9:.2f} GB")
    check_disk(total_bytes)

    name = model_id.replace("/", "__")
    HF_DIR.mkdir(parents=True, exist_ok=True)
    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    local = HF_DIR / name
    f16 = GGUF_DIR / f"{name}-f16.gguf"
    std = GGUF_DIR / f"{name}-Q4_K_M-standard.gguf"
    f16_rev = f16.with_name(f16.name + ".rev")   # kis commit se bani, uska nishaan

    with model_lock(name):
        print("[1/3] Download")
        reporter.update("extracting", 25)
        snapshot_download(repo_id=model_id, revision=sha, local_dir=str(local),
                          token=hf_token,
                          allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt",
                                          "*.tiktoken"])
        refuse_custom_code(local)

        print("[2/3] Convert to FP16 GGUF")
        reporter.update("extracting", 45)
        fresh = f16.exists() and f16_rev.exists() and f16_rev.read_text().strip() == sha
        if fresh:
            print("  FP16 pehle se hai (same commit), skip.")
        else:
            if f16.exists():
                print("  Purani/unverified FP16 mili -- dobara bana raha hoon.")
            # FP16 badli to usse bana Q4 bhi purana hai
            for stale in (f16, f16_rev, std):
                stale.unlink(missing_ok=True)
            build_atomic(f16, lambda out: run(
                [sys.executable, CONVERT, local, "--outfile", out, "--outtype", "f16"]))
            f16_rev.write_text(sha)

        print("[3/3] Plain Q4_K_M (no imatrix), from the same FP16")
        reporter.update("quantizing", 70)
        if std.exists():
            print("  Plain Q4 pehle se hai, skip.")
        else:
            build_atomic(std, lambda out: run([QUANTIZE, f16, out, "Q4_K_M"]))

        if cleanup_hf:
            shutil.rmtree(local, ignore_errors=True)

    # Closing state callback: benchmark nodes ko signal
    reporter.update("benchmarking", 85)

    print(f"\nTaiyaar:\n  {f16} ({f16.stat().st_size / 1e9:.2f} GB)\n  "
          f"{std} ({std.stat().st_size / 1e9:.2f} GB)")
    print(f"\nAgla step: python scripts/trl4_bench.py --model {model_id}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    p.add_argument("--job-id", default=os.getenv("JOB_ID"),
                   help="quantization_jobs.id (uuid). Dene se sirf yahi row update hoti hai.")
    p.add_argument("--revision", default=None, help="HF branch/tag/commit (default: main)")
    p.add_argument("--allow-licenses", default=os.getenv("ALLOW_LICENSES", DEFAULT_LICENSES),
                   help="comma-separated license allow-list")
    p.add_argument("--no-license-check", action="store_true",
                   help="sirf internal runs ke liye: license check band")
    p.add_argument("--max-gb", type=float,
                   default=float(os.getenv("MAX_MODEL_GB", DEFAULT_MAX_GB)),
                   help="safetensors total size limit (GB)")
    p.add_argument("--cleanup-hf", action="store_true",
                   help="FP16 GGUF banne ke baad HF snapshot delete karo (disk bachao)")
    a = p.parse_args(argv)

    model_id = (a.model or "").strip()
    reporter = JobReporter(model_id, a.job_id)
    allowed = {x.strip().lower() for x in a.allow_licenses.split(",") if x.strip()}
    try:
        prepare(model_id, a.hf_token, reporter, revision=a.revision,
                allowed_licenses=allowed, check_license=not a.no_license_check,
                max_gb=a.max_gb, cleanup_hf=a.cleanup_hf)
        return 0
    except PipelineError as e:
        print(str(e), file=sys.stderr)
        if e.report:
            reporter.update(e.status)
        return e.code
    except Exception as e:  # unexpected: DB ko 'failed' dikhao, atka hua spinner nahi
        print(f"FAILED (unexpected): {type(e).__name__}: {e}", file=sys.stderr)
        reporter.update("failed")
        return FAIL_EXIT


if __name__ == "__main__":
    sys.exit(main())
