"""Full pipeline: download -> convert -> Hindi imatrix -> quantize -> Supabase me log.

Usage:
    python scripts/compress_and_log.py --model TinyLlama/TinyLlama-1.1B-Chat-v1.0

Note: the FP16 GGUF is kept (not deleted) after quantizing, because
scripts/run_benchmark.py needs it as the baseline for comparison.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import snapshot_download
from supabase import create_client

load_dotenv(".env.local")

ROOT = Path(__file__).resolve().parent.parent
LLAMA = ROOT / "llama.cpp"
HF_DIR = ROOT / "models" / "hf"
GGUF_DIR = ROOT / "models" / "gguf"
CALIB_FILE = ROOT / "data" / "hindi_calib.txt"

CONVERT = LLAMA / "convert_hf_to_gguf.py"
BIN = LLAMA / "build" / "bin"
QUANTIZE = BIN / "llama-quantize"
IMATRIX = BIN / "llama-imatrix"

QUANT = "Q4_K_M"


def run(cmd):
    print("$", " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])
    return r.stdout


def compress(model_id: str, hf_token: str | None = None):
    sb = create_client(os.environ["NEXT_PUBLIC_SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    name = model_id.replace("/", "__")
    HF_DIR.mkdir(parents=True, exist_ok=True)
    GGUF_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/5] Download")
    local = HF_DIR / name
    snapshot_download(repo_id=model_id, local_dir=str(local), token=hf_token,
                       allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt"])

    print("[2/5] Convert to FP16 GGUF")
    f16 = GGUF_DIR / f"{name}-f16.gguf"
    run([sys.executable, CONVERT, local, "--outfile", f16, "--outtype", "f16"])

    print("[3/5] Build Hindi imatrix (the calibration step)")
    if not CALIB_FILE.exists() or CALIB_FILE.stat().st_size == 0:
        raise FileNotFoundError("Pehle scripts/download_hindi_data.py chalao — data/hindi_calib.txt chahiye.")
    imatrix_file = GGUF_DIR / f"{name}.imatrix"
    run([IMATRIX, "-m", f16, "-f", CALIB_FILE, "-o", imatrix_file])

    print("[4/5] Quantize (Hindi-calibrated + standard baseline)")
    out = GGUF_DIR / f"{name}-{QUANT}-hindi.gguf"
    run([QUANTIZE, "--imatrix", imatrix_file, f16, out, QUANT])

    out_std = GGUF_DIR / f"{name}-{QUANT}-standard.gguf"
    run([QUANTIZE, f16, out_std, QUANT])

    size_gb = round(out.stat().st_size / 1e9, 2)
    fp16_gb = round(f16.stat().st_size / 1e9, 2)
    # NOTE: f16 is intentionally kept on disk here (not deleted) —
    # scripts/run_benchmark.py needs it as the FP16 baseline for comparison.

    print("[5/5] Log to Supabase (scores fill in after scripts/run_benchmark.py)")
    row = {
        "model_name": model_id,
        "hf_repo": model_id,
        "quant_type": f"{QUANT} (Hindi-calibrated)",
        "size_gb": size_gb,
        "fp16_size_gb": fp16_gb,
        "notes": "Benchmark pending",
    }
    sb.table("models").insert(row).execute()
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    a = p.parse_args()
    compress(a.model, a.hf_token)