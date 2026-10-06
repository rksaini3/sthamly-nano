"""Repair tool (was `safai.py` in Colab). Normal runs of prepare_model.py already build plain Q4
from the same FP16, so you only need this if an old plain Q4 came from a different FP16
(size differs by >2% from the Hindi imatrix Q4).

Plain Q4 ko usi FP16 se (bina imatrix) dobara banata hai jis se Hindi/English/Mixed bane the,
sirf 'standard' ko dobara naapta hai, phir publish ka dry-run (ya --go se asli publish) chalata hai.
Resumable: beech me kat jaye to wahi cell dobara chalao.
"""
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
ap.add_argument("--chunks", type=int, default=12)
ap.add_argument("--go", action="store_true")
args = ap.parse_args()

os.chdir(Path(__file__).resolve().parent.parent)
TAG = args.model.replace("/", "__")
G = f"models/gguf/{TAG}"
BIN = "llama.cpp/build/bin"
RES = f"results_trl4_{TAG}.json"
DRIVE = os.environ.get("DRIVE_DIR", "")
F16, STD, REF = f"{G}-f16.gguf", f"{G}-Q4_K_M-standard.gguf", f"{G}-Q4_K_M-hindi60.gguf"


def die(m):
    print("ROKA:", m)
    sys.exit(1)


for p in (F16, REF, f"{BIN}/llama-quantize", RES):
    if not os.path.exists(p):
        die(f"{p} nahi mila. Pehle scripts/prepare_model.py aur scripts/trl4_bench.py chalao.")
res = json.load(open(RES))
doms = list(res)


def done():
    return all(res[d].get("standard", {}).get("same_f16") for d in doms)


def sz(p): return os.path.getsize(p)


if done():
    print("Plain Q4 ki safai pehle ho chuki hai.")
else:
    ref = sz(REF)
    if (not os.path.exists(STD)) or abs(sz(STD) - ref) / ref > 0.02:
        if os.path.exists(STD):
            old = STD.replace("standard", "standard-old")
            shutil.move(STD, old)
            print("purani plain Q4 ko", old, "naam de diya")
        print("Plain Q4 (bina imatrix) usi FP16 se ban rahi hai (1-3 minute)...")
        r = subprocess.run([f"{BIN}/llama-quantize", F16, STD, "Q4_K_M"], capture_output=True, text=True)
        if not os.path.exists(STD):
            print((r.stdout + r.stderr)[-1500:])
            die("plain Q4 nahi bani")
        if abs(sz(STD) - ref) / ref > 0.02:
            die(f"nayi plain Q4 ka size ({sz(STD)}) Hindi60 ({ref}) se alag hai; kuch gadbad hai. Bhejo.")
    # purana 'standard' naap hata do (har domain me), phir sirf wahi dobara naapo
    Path(RES.replace(".json", ".oldstandard.json")).write_text(json.dumps(res), encoding="utf-8")
    for d in doms:
        res[d].pop("standard", None)
    json.dump(res, open(RES, "w"), indent=1)
    print("plain Q4 dobara naapi ja rahi hai (lagbhag 15-20 minute)...")
    rc = subprocess.run([sys.executable, "scripts/trl4_bench.py", "--model", args.model, "--chunks", str(args.chunks)]).returncode
    res = json.load(open(RES))
    if rc != 0 or not all("standard" in res[d] for d in doms):
        die("naap poora nahi hua; wahi cell dobara chalao.")
    for d in doms:
        res[d]["standard"]["same_f16"] = True
    json.dump(res, open(RES, "w"), indent=1)
    if DRIVE and os.path.isdir(DRIVE):
        (Path(DRIVE) / "results").mkdir(parents=True, exist_ok=True)
        (Path(DRIVE) / "models" / "gguf").mkdir(parents=True, exist_ok=True)
        shutil.copy2(RES, Path(DRIVE) / "results" / RES)
        shutil.copy2(STD, Path(DRIVE) / "models" / "gguf" / Path(STD).name)
        print("Drive me save ho gaya")
    print("\nSAFAI POORI. Nayi plain Q4 vs imatrix numbers:")
    for d in doms:
        print(f"  {d}: " + ", ".join(f"{v} {res[d][v]['kld'][0]:.4f}" for v in ("standard", "hindi60", "english60", "mixed60") if v in res[d]))

print("\n===== publish =====")
cmd = [sys.executable, "scripts/publish_mvp.py", "--model", args.model] + (["--go"] if args.go else [])
sys.exit(subprocess.run(cmd).returncode)