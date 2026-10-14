"""Step 1 of the pipeline: download a model, convert it to FP16 GGUF, and build the
plain Q4_K_M baseline (no imatrix) from that SAME FP16 file.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent
LLAMA = ROOT / "llama.cpp"
HF_DIR = ROOT / "models" / "hf"
GGUF_DIR = ROOT / "models" / "gguf"
CONVERT = LLAMA / "convert_hf_to_gguf.py"
QUANTIZE = LLAMA / "build" / "bin" / "llama-quantize"

CUSTOM_CODE_EXIT = 3

def run(cmd):
    """Run a command and let its output stream to the terminal."""
    cmd = [str(c) for c in cmd]
    print("\$", " ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd[:3])} ...")

def update_db_status(model_id: str, status: str, progress: int):
    """Pings the Supabase infrastructure layer to synchronize live progress states."""
    url = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        return # Skip quietly if running in standalone offline terminal mode

    # Perform index lookup on the hf_model_identifier context array
    pattern = model_id.strip()
    rpc_url = f"{url.rstrip('/')}/rest/v1/quantization_jobs?hf_model_identifier=eq.{urllib.parse.quote(pattern)}"
    
    payload = {
        "status": status,
        "progress": progress
    }
    
    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url,
        data=req_body,
        headers={"Content-Type": "application/json", "apikey": key, "Authorization": f"Bearer {key}"},
        method="PATCH"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            pass
    except Exception:
        pass # Cosmetic logging failure never breaks compilation execution layers

def refuse_custom_code(model_dir: Path) -> None:
    for name in ("config.json", "tokenizer_config.json"):
        f = model_dir / name
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            print(f"REFUSED: {name} padha nahi ja sakta.")
            sys.exit(CUSTOM_CODE_EXIT)
        if isinstance(data, dict) and "auto_map" in data:
            print(f"REFUSED: {name} me auto_map hai (custom code).")
            sys.exit(CUSTOM_CODE_EXIT)

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
    # Live progress push: notify frontend that weight extraction matrix has initiated
    update_db_status(model_id, "extracting", 25)
    
    snapshot_download(repo_id=model_id, local_dir=str(local), token=hf_token,
                      allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.tiktoken"])

    refuse_custom_code(local)

    print("[2/3] Convert to FP16 GGUF")
    update_db_status(model_id, "extracting", 45)
    if f16.exists():
        print("  FP16 pehle se hai, skip.")
    else:
        run([sys.executable, CONVERT, local, "--outfile", f16, "--outtype", "f16"])

    print("[3/3] Plain Q4_K_M (no imatrix), from the same FP16")
    update_db_status(model_id, "quantizing", 70)
    if std.exists():
        print("  Plain Q4 pehle se hai, skip.")
    else:
        run([QUANTIZE, f16, std, "Q4_K_M"])

    # Closing state callback push to signal pipeline graduation down to benchmark nodes
    update_db_status(model_id, "benchmarking", 85)

    print(f"\nTaiyaar:\n  {f16} ({f16.stat().st_size / 1e9:.2f} GB)\n  {std} ({std.stat().st_size / 1e9:.2f} GB)")
    print(f"\nAgla step: python scripts/trl4_bench.py --model {model_id}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--hf-token", default=os.getenv("HF_TOKEN"))
    a = p.parse_args()
    prepare(a.model, a.hf_token)
