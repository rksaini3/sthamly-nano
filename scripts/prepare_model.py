"""Step 1 of the pipeline: download a model, convert it to FP16 GGUF, and build the
plain Q4_K_M baseline (no imatrix) from that SAME FP16 file.

Why the baseline is built here: every variant that scripts/trl4_bench.py compares
(plain, Hindi, English, Mixed imatrix) must come from one FP16 file. An earlier run
compared a plain Q4 from a different FP16 and showed inflated gains.

Usage (repo root, after scripts/setup.sh):
    python scripts/prepare_model.py --model Qwen/Qwen2.5-1.5B-Instruct

Next:
    python scripts/download_hindi_data.py
    python scripts/trl4_bench.py --model Qwen/Qwen2.5-1.5B-Instruct
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent
LLAMA = ROOT / "llama.cpp"
HF_DIR = ROOT / "models" / "hf"
GGUF_DIR = ROOT / "models" / "gguf"
CONVERT = LLAMA / "convert_hf_to_gguf.py"
QUANTIZE = LLAMA / "build" / "bin" / "llama-quantize"


def run(cmd):
    """Run a command and let its output stream to the terminal (imatrix/convert can take long)."""
    cmd = [str(c) for c in cmd]
    print("$", " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd[:3])} ...")


def prepare(model_id: str, hf_token: str | None):
    for need in (CONVERT, QUANTIZE):
        if not need.exists():
            sys.exit(f"{need} nahi mila. Pehle `bash scripts/setup.sh` chalao.")

    name = model_id.replace("/", "__")
    HF_DIR.mkdir(parents=True, exist_ok=True)
    GGUF_DIR.mkdir(parents=True, exist_ok=True)
    local = HF_DIR / name
    f16 = GGUF_DIR / f"{name}-f16.gguf"
    std = GGUF_DIR / f"{name}-Q4_K_M-standard.gguf"

    print("[1/3] Download")
    snapshot_download(repo_id=model_id, local_dir=str(local), token=hf_token,
                      allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.tiktoken"])

    print("[2/3] Convert to FP16 GGUF")
    if f16.exists():
        print("  FP16 pehle se hai, skip.")
    else:
        run([sys.executable, CONVERT, local, "--outfile", f16, "--outtype", "f16"])

    print("[3/3] Plain Q4_K_M (no imatrix), from the same FP16")
    if std.exists():
        print("  Plain Q4 pehle se hai, skip.")
    else:
        run([QUANTIZE, f16, std, "Q4_K_M"])

    # The FP16 file is kept on purpose: it is the reference for every KLD measurement.
    print(f"\nTaiyaar:\n  {f16} ({f16.stat().st_size / 1e9:.2f} GB)\n  {std} ({std.stat().st_size / 1e9:.2f} GB)")
    print(f"\nAgla step: python scripts/trl4_bench.py --model {model_id}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    a = p.parse_args()
    prepare(a.model, a.hf_token)