"""Test ka result dekhkar winner chunta hai, model card likhta hai, HF par upload karta hai
aur Supabase me EK row daalta hai. Default = DRY-RUN (kuch bhi upload nahi hota).
Asli publish: --go  (HF_WRITE_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY env me chahiye).
"""
import argparse, json, os, sys, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
ap.add_argument("--go", action="store_true", help="asli publish (warna sirf dry-run)")
ap.add_argument("--private", action="store_true", help="HF repo private banao (baad me HF settings se public kar sakte ho)")
args = ap.parse_args()

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
TAG = args.model.replace("/", "__")
BASE = args.model.split("/")[1]
LICENSES = {"Qwen/Qwen2.5-0.5B-Instruct": "apache-2.0", "Qwen/Qwen2.5-1.5B-Instruct": "apache-2.0"}


def die(msg):
    print("ROKA:", msg)
    sys.exit(1)


if args.model not in LICENSES:
    die(f"{args.model} ka license maine verify nahi kiya. Pehle uska license padho, phir LICENSES me jodo.")
RES = f"results_trl4_{TAG}.json"
if not os.path.exists(RES):
    die(f"{RES} nahi mila. Pehle TRL-4 test poora chalao (ya restore cell).")
res = json.load(open(RES))
doms = [d for d in res if all(v in res[d] for v in ("standard", "hindi60", "english60", "mixed60"))]
if not doms:
    die("Test adhoora hai: kisi domain me chaaron variants (standard, hindi60, english60, mixed60) nahi naape gaye.")


def kld(d, v): return res[d][v]["kld"]
def top(d, v): return res[d][v]["top"]
def clear_win(a, b, d, key="kld"):
    """a, b = variant naam. True agar a, b se 2x milaa-hua-error se behtar."""
    (x, ex), (y, ey) = res[d][a][key], res[d][b][key]
    gain = (y - x) if key == "kld" else (x - y)
    return gain > 2 * (ex ** 2 + ey ** 2) ** 0.5


mean = {v: sum(kld(d, v)[0] for d in doms) / len(doms) for v in ("standard", "hindi60", "english60", "mixed60")}
print("Domains:", doms)
print("Mean KLD (kam = behtar):", {k: round(x, 4) for k, x in mean.items()})
cand = min(("hindi60", "mixed60"), key=lambda v: mean[v])
if mean["english60"] < mean[cand]:
    die("English-calibrated Q4 Hindi/Mixed se behtar nikla. 'Hindi calibration' wala daava abhi nahi bechna. Product angle dobara sochni padegi.")
if not all(clear_win(cand, "standard", d) for d in doms):
    die(f"{cand} har domain me plain Q4 se SAAF behtar nahi hai. Publish mat karo; pehle aur chunks ya data se dobara naapo.")

vs_en = all(clear_win(cand, "english60", d) for d in doms)
gain_std = {d: 100 * (1 - kld(d, cand)[0] / kld(d, "standard")[0]) for d in doms}
gain_en = {d: 100 * (1 - kld(d, cand)[0] / kld(d, "english60")[0]) for d in doms}
print(f"Winner: {cand}  (plain Q4 se har domain me saaf behtar)")
print("Hindi-vs-English fark saabit:", "HAAN" if vs_en else "NAHI (English calibration bhi lagbhag utna hi achha)")

NAMES = {"hindi60": "Hindi", "mixed60": "Hindi+English mixed"}
repo_name = f"{BASE}-{'hindi' if cand == 'hindi60' else 'hi-en'}-imatrix-Q4_K_M-GGUF"
src = f"models/gguf/{TAG}-Q4_K_M-{cand}.gguf"
f16 = f"models/gguf/{TAG}-f16.gguf"
if not os.path.exists(src):
    die(f"{src} nahi mili.")
_std = f"models/gguf/{TAG}-Q4_K_M-standard.gguf"
if os.path.exists(_std) and abs(os.path.getsize(_std) - os.path.getsize(src)) / os.path.getsize(src) > 0.02:
    die("Plain Q4 ka size imatrix wale se alag hai (alag FP16 se bani lagti hai), to 'plain Q4 se kitna behtar' ka number saaf nahi. Pehle safai wala cell chalao.")
size_gb = round(os.path.getsize(src) / 1e9, 2)
fp16_gb = round(os.path.getsize(f16) / 1e9, 2) if os.path.exists(f16) else None

rows = []
for d in doms:
    for v, lab in (("standard", "Plain Q4_K_M"), ("english60", "English-imatrix Q4_K_M"), (cand, f"{NAMES[cand]}-imatrix Q4_K_M (ye model)")):
        k, t = kld(d, v), top(d, v)
        rows.append(f"| {d} | {lab} | {k[0]:.4f} ± {k[1]:.4f} | {t[0]:.2f} ± {t[1]:.2f} |")
en_clear = {d: clear_win("english60", "standard", d) for d in doms}
if vs_en:
    claim = "Is test me Hindi-calibrated quantization English-calibrated se bhi saaf behtar nikli."
elif all(en_clear.values()):
    claim = ("Is test me English-calibrated quantization bhi plain Q4 se saaf behtar nikli aur Hindi se lagbhag barabar; "
             "yani fayda 'Hindi text se calibrate karne' se zyada 'imatrix lagane' ka lagta hai. "
             "Hindi vs English ka fark kisi test set me saaf nahi nikla.")
else:
    claim = ("English-imatrix plain Q4 se " + ", ".join(f"{d}: {'saaf behtar' if en_clear[d] else 'saaf behtar nahi'}" for d in doms)
             + ". Hindi-imatrix plain Q4 se har test set me saaf behtar hai, par Hindi vs English ka fark kisi test set me saaf nahi nikla; "
             "isliye ye nahi kaha ja sakta ki Hindi text se calibrate karne ka alag fayda hai.")
card = f"""---
license: {LICENSES[args.model]}
base_model: {args.model}
language: [hi, en]
tags: [gguf, quantized, imatrix, hindi, llama.cpp]
---
# {repo_name}

`{args.model}` ka Q4_K_M GGUF, jo {NAMES[cand]} text se bane imatrix se quantize kiya gaya hai (llama.cpp).
Size: {size_gb} GB (FP16 {fp16_gb} GB).

## Naap (FP16 se kitna door; KLD kam = behtar, top-p zyada = behtar)
| Test set | Variant | Mean KLD | Same top-p % |
|---|---|---|---|
""" + "\n".join(rows) + f"""

Plain Q4 ke muqable KLD kam hua: """ + ", ".join(f"{d}: {g:.0f}%" for d, g in gain_std.items()) + f""".
English-imatrix ke muqable: """ + ", ".join(f"{d}: {g:.0f}%" for d, g in gain_en.items()) + f""".

{claim}

## Seemaayein (zaroor padhein)
- Naap sirf proxy hai (FP16 se distribution ka fark). Asli Hindi kaam (sawal-jawab, summary) par quality abhi nahi naapi gayi.
- Test chhota hai ({', '.join(f'{d}: 150 lines' for d in doms)}), aur Hindi Wikipedia / sangraha se hai.
- Plain, English aur Hindi variants ek hi FP16 file se bane aur usi se naape gaye (FP16 = Qwen ki official GGUF fp16 file).
- License base model ka hi hai ({LICENSES[args.model]}).
"""
note = (f"{NAMES[cand]} imatrix Q4_K_M. Mean KLD vs plain Q4: " + ", ".join(f"{d} -{g:.0f}%" for d, g in gain_std.items())
        + ". Proxy naap (KLD); asli Hindi task nahi naapa. "
        + ("Hindi>English saabit." if vs_en else "Hindi>English saabit nahi (English imatrix bhi lagbhag barabar)."))
Path("README_publish.md").write_text(card, encoding="utf-8")
print("\n--- model card (README_publish.md me bhi hai) ---\n" + card)

if not args.go:
    print(f"\nDRY-RUN: kuch upload nahi hua. Repo banega: <HF-user>/{repo_name}\nNote (Supabase):", note)
    print("Asli publish ke liye cell me GO = True karo.")
    sys.exit(0)

# ---------------------------------------------------------------- asli publish
tok = os.environ.get("HF_WRITE_TOKEN", "").strip()
sbu, sbk = os.environ.get("SUPABASE_URL", "").strip().rstrip("/"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
if not (tok and sbu and sbk):
    die("HF_WRITE_TOKEN, SUPABASE_URL ya SUPABASE_SERVICE_ROLE_KEY secret nahi mila (Colab Secrets me teeno chahiye, notebook access ON).")
from huggingface_hub import HfApi
api = HfApi(token=tok)
user = api.whoami()["name"]
repo = f"{user}/{repo_name}"
print("HF par upload:", repo)
api.create_repo(repo, repo_type="model", private=args.private, exist_ok=True)
api.upload_file(path_or_fileobj="README_publish.md", path_in_repo="README.md", repo_id=repo)
api.upload_file(path_or_fileobj=src, path_in_repo=f"{repo_name.replace('-GGUF', '').lower()}.gguf", repo_id=repo)
dl = f"https://huggingface.co/{repo}"
print("HF ho gaya:", dl)

H = {"apikey": sbk, "Authorization": f"Bearer {sbk}", "Content-Type": "application/json", "Prefer": "return=minimal"}
q = urllib.request.Request(f"{sbu}/rest/v1/models?hf_repo=eq.{urllib.parse.quote(repo, safe='')}&select=id", headers=H)
if json.load(urllib.request.urlopen(q)):
    # Row already exists (e.g. made by an older version without `results`): update it, never duplicate.
    upd = {"base_model": args.model, "results": res, "size_gb": size_gb, "fp16_size_gb": fp16_gb,
           "download_url": dl, "notes": note}
    r = urllib.request.Request(f"{sbu}/rest/v1/models?hf_repo=eq.{urllib.parse.quote(repo, safe='')}",
                               data=json.dumps(upd).encode(), headers=H, method="PATCH")
    try:
        urllib.request.urlopen(r)
        print("Supabase me ye repo pehle se tha; row update kar di (duplicate nahi).")
    except urllib.error.HTTPError as e:
        die(f"Supabase update fail: {e.code} {e.read()[:300]}")
else:
    row = {"model_name": f"{BASE} ({NAMES[cand]} imatrix)", "hf_repo": repo, "base_model": args.model,
           "quant_type": "Q4_K_M", "size_gb": size_gb, "fp16_size_gb": fp16_gb, "download_url": dl,
           "results": res, "notes": note}
    r = urllib.request.Request(f"{sbu}/rest/v1/models", data=json.dumps(row).encode(), headers=H, method="POST")
    try:
        urllib.request.urlopen(r)
        print("Supabase row jud gayi.")
    except urllib.error.HTTPError as e:
        die(f"Supabase insert fail: {e.code} {e.read()[:300]}")
print("\nPUBLISH POORA. Link:", dl)