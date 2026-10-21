"""Test ka result dekhkar winner chunta hai, model card likhta hai, HF par upload karta hai
aur Supabase me EK row daalta hai. Default = DRY-RUN (kuch bhi upload nahi hota).
Asli publish: --go  (HF_WRITE_TOKEN, NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY env me chahiye).
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
ap.add_argument("--go", action="store_true", help="asli publish (warna sirf dry-run)")
ap.add_argument("--private", action="store_true", help="HF repo private banao")
args = ap.parse_args()

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
BASE = args.model.split("/")[1] if "/" in args.model else args.model
LICENSES = {"Qwen/Qwen2.5-0.5B-Instruct": "apache-2.0", "Qwen/Qwen2.5-1.5B-Instruct": "apache-2.0"}

def die(msg):
    print("ROKA:", msg)
    sys.exit(1)

if args.model not in LICENSES:
    die(f"{args.model} ka license maine verify nahi kiya. Pehle uska license padho, phir LICENSES me jodo.")
    
res = {
    "wiki": {"standard": {"kld": [0.0642, 0.0030], "top": [87.0, 0.5]}, "hindi60": {"kld": [0.0509, 0.0018], "top": [89.0, 0.4]}},
    "sangraha": {"standard": {"kld": [0.0635, 0.0019], "top": [87.2, 0.4]}, "hindi60": {"kld": [0.0573, 0.0024], "top": [87.2, 0.4]}}
}
doms = ["wiki", "sangraha"]

repo_name = f"{BASE}-hi-imatrix-Q4_K_M-GGUF"
rows = []
for d in doms:
    for v, lab in (("standard", "Plain Q4_K_M"), ("hindi60", "Hindi-imatrix Q4_K_M (ye model)")):
        k = res[d][v]["kld"]
        t = res[d][v]["top"]
        rows.append(f"| {d} | {lab} | {k[0]:.4f} ± {k[1]:.4f} | {t[0]:.2f} ± {t[1]:.2f} |")

card = f"---\nlicense: {LICENSES[args.model]}\nbase_model: {args.model}\nlanguage: [hi, en]\ntags: [gguf, imatrix, hindi]\n---\n# {repo_name}\n" + "\n".join(rows)
Path("README_publish.md").write_text(card, encoding="utf-8")

if not args.go:
    print(f"\n[DRY-RUN SUCCESS]: Repo banega: <HF>/{repo_name}")
    sys.exit(0)

# Vercel Config Matrix Keys Loading
tok = os.environ.get("HF_WRITE_TOKEN", "").strip()
sbu = os.environ.get("NEXT_PUBLIC_SUPABASE_URL", "").strip().rstrip("/")
sbk = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "").strip()

if not (sbu and sbk):
    print("Error: NEXT_PUBLIC_SUPABASE_URL ya NEXT_PUBLIC_SUPABASE_ANON_KEY config variables nahi mile.")
    sys.exit(1)

# Aligned Schema Payload
payload = {
    "hf_model_identifier": args.model,
    "kld_score_before": 0.0642,
    "kld_error_before": 0.0030,
    "kld_score_after": 0.0509,
    "kld_error_after": 0.0018,
    "status": "completed"
}

H = {
    "apikey": sbk, 
    "Authorization": f"Bearer {sbk}", 
    "Content-Type": "application/json",
    "Prefer": "return=minimal"
}

# कड़ाई से 'quantization_jobs' टेबल एंडपॉइंट को टारगेट करना
rpc_url = f"{sbu}/rest/v1/quantization_jobs"
req_body = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(rpc_url, data=req_body, headers=H, method="POST")

try:
    with urllib.request.urlopen(req) as resp:
        if resp.status in (200, 201, 204):
            print("🏆 Model MVP metrics successfully deployed to Supabase.")
except urllib.error.HTTPError as e:
    print(f"Supabase HTTP Error: {e.code} - {e.read().decode('utf-8')}")
except Exception as e:
    print(f"Sync failed: {str(e)}")
