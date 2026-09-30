"""Run the Hindi benchmark: FP16 vs standard Q4 vs Hindi-calibrated Q4.

Computes perplexity (PPL) on a held-out Hindi/Hinglish test set using
llama.cpp's llama-perplexity binary, then logs the scores to Supabase.
Lower PPL = better = the model predicts Hindi text more confidently.

Usage:
    python scripts/run_benchmark.py --model TinyLlama/TinyLlama-1.1B-Chat-v1.0
"""
import argparse
import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(".env.local")

ROOT = Path(__file__).resolve().parent.parent
LLAMA = ROOT / "llama.cpp"
GGUF_DIR = ROOT / "models" / "gguf"
TEST_FILE = ROOT / "data" / "hindi_test.txt"

PERPLEXITY_BIN = LLAMA / "build" / "bin" / "llama-perplexity"

QUANT = "Q4_K_M"


def run_perplexity(gguf_path: Path) -> float:
    """Runs llama-perplexity on the Hindi test file, returns the final PPL score."""
    if not gguf_path.exists():
        raise FileNotFoundError(f"Missing: {gguf_path}")
    if not PERPLEXITY_BIN.exists():
        raise FileNotFoundError("llama-perplexity not built. Run scripts/setup.sh first.")
    if not TEST_FILE.exists():
        raise FileNotFoundError(f"Missing test set: {TEST_FILE}")

    cmd = [str(PERPLEXITY_BIN), "-m", str(gguf_path), "-f", str(TEST_FILE)]
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    output = r.stdout + r.stderr

    # llama-perplexity prints: "Final estimate: PPL = 12.3456 +/- 0.0789"
    match = re.search(r"Final estimate:\s*PPL\s*=\s*([\d.]+)", output)
    if not match:
        print(output[-2000:])
        raise RuntimeError("Could not parse PPL from llama-perplexity output.")
    return float(match.group(1))


def benchmark(model_id: str):
    sb = create_client(os.environ["NEXT_PUBLIC_SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
    name = model_id.replace("/", "__")

    f16 = GGUF_DIR / f"{name}-f16.gguf"
    standard = GGUF_DIR / f"{name}-{QUANT}-standard.gguf"
    hindi = GGUF_DIR / f"{name}-{QUANT}-hindi.gguf"

    print("[1/3] FP16 baseline")
    ppl_fp16 = run_perplexity(f16)
    print(f"  PPL = {ppl_fp16}")

    print("[2/3] Standard Q4 (no Hindi calibration)")
    ppl_standard = run_perplexity(standard)
    print(f"  PPL = {ppl_standard}")

    print("[3/3] Hindi-calibrated Q4")
    ppl_hindi = run_perplexity(hindi)
    print(f"  PPL = {ppl_hindi}")

    print("\nResult (lower = better):")
    print(f"  FP16:              {ppl_fp16:.4f}")
    print(f"  Standard Q4:       {ppl_standard:.4f}")
    print(f"  Hindi-calibrated:  {ppl_hindi:.4f}")

    if ppl_hindi < ppl_standard:
        gain = ppl_standard - ppl_hindi
        print(f"\nHindi calibration WON by {gain:.4f} PPL points.")
    else:
        print("\nHindi calibration did NOT beat standard Q4 — gate failed, reconsider the niche.")

    sb.table("models").update({
        "hindi_score_fp16": round(ppl_fp16, 4),
        "hindi_score_standard_q4": round(ppl_standard, 4),
        "hindi_score_hindi_q4": round(ppl_hindi, 4),
        "notes": "Benchmark complete",
    }).eq("model_name", model_id).execute()
    print("\nLogged to Supabase.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    a = p.parse_args()
    benchmark(a.model)