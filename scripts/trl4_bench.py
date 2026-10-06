"""TRL-4 check: is the Hindi edge real, and is it Hindi or just "any imatrix"?

What it does (resumable; every finished step is skipped on a re-run):
  1. Builds a bigger, multi-domain Hindi test set (Hindi Wikipedia + held-out
     sangraha rows; plus data/hinglish_test.txt if you put one there).
     Lines that also appear in the calibration text are dropped (leak guard).
  2. Builds equal-budget Q4 variants: hindi<N>, english<N>, mixed<N>
     (mixed = Hindi and English text interleaved), same number of chunks each.
  3. Measures every variant against FP16 (KL-divergence mode of
     llama-perplexity) on every domain, with +/- error, and prints verdicts.
  4. Saves results and new files to Google Drive after every step if the
     DRIVE_DIR environment variable is set.

Usage (repo root):
    python scripts/trl4_bench.py --model Qwen/Qwen2.5-0.5B-Instruct
    python scripts/trl4_bench.py --variants standard,hindi60,english60,mixed60,hindi,english
"""
import argparse
import itertools
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
ap.add_argument("--chunks", type=int, default=12, help="eval chunks per domain (more = smaller +/-, slower)")
ap.add_argument("--calib-chunks", type=int, default=60, help="imatrix chunks for the new variants")
ap.add_argument("--variants", default="", help="comma list; default = standard,hindi<N>,english<N>,mixed<N>")
ap.add_argument("--threads", type=int, default=2, help="llama.cpp threads (-t); 2 was a free Colab CPU")
args = ap.parse_args()

N = args.calib_chunks
BIN = "llama.cpp/build/bin"
MODEL_TAG = args.model.replace("/", "__")
G = f"models/gguf/{MODEL_TAG}"
RES = f"results_trl4_{MODEL_TAG}.json"
REPORT = f"report_trl4_{MODEL_TAG}.txt"
DRIVE = os.environ.get("DRIVE_DIR", "")
T0 = time.time()

NAMES = {
    "standard": "Plain Q4",
    "hindi": "Hindi 40",
    "english": "English 40",
    f"hindi{N}": f"Hindi {N}",
    f"english{N}": f"English {N}",
    f"mixed{N}": f"Mixed {N}",
}
VARIANTS = [v.strip() for v in args.variants.split(",") if v.strip()] or ["standard", f"hindi{N}", f"english{N}", f"mixed{N}"]


def log(msg):
    s = int(time.time() - T0)
    print(f"[{s // 60:02d}:{s % 60:02d}] {msg}", flush=True)


def sh(cmd):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def stop(msg):
    save()
    sys.exit(msg)


# ---------------------------------------------------------------- Drive save / restore
def _copy_if_new(src, dst):
    src, dst = Path(src), Path(dst)
    if src.stat().st_size > 2.2e9:
        return
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def save():
    """Copy results, data and non-FP16 model files to Drive (no-op without DRIVE_DIR)."""
    if not DRIVE or not os.path.isdir(DRIVE):
        return
    try:
        for f in Path(".").glob("results_trl4_*.json"):
            _copy_if_new(f, Path(DRIVE) / "results" / f.name)
        for f in Path(".").glob("report_trl4_*.txt"):
            shutil.copy2(f, Path(DRIVE) / "results" / f.name)
        for f in Path("data").glob("*.txt"):
            _copy_if_new(f, Path(DRIVE) / "data" / f.name)
        for f in Path("models/gguf").glob("*"):
            if f.name.endswith("-f16.gguf") or f.suffix == ".kld":
                continue
            _copy_if_new(f, Path(DRIVE) / "models" / "gguf" / f.name)
        log("Drive me save ho gaya")
    except Exception as e:  # saving must never kill the run
        print("save warning:", str(e)[:200])


def restore():
    """Bring back files from Drive that are missing locally (after a runtime reset)."""
    if not DRIVE or not os.path.isdir(DRIVE):
        return
    n = 0
    for sub in ["data", "models/gguf"]:
        src = Path(DRIVE) / sub
        if not src.is_dir():
            continue
        for f in src.glob("*"):
            dst = Path(sub) / f.name
            if f.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                n += 1
    for f in (Path(DRIVE) / "results").glob("results_trl4_*.json"):
        if not Path(f.name).exists():
            shutil.copy2(f, f.name)
            n += 1
    if n:
        log(f"Drive se {n} file wapas laayi")


# ---------------------------------------------------------------- text helpers
def deva_frac(s):
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum("\u0900" <= c <= "\u097f" for c in letters) / len(letters)


def good_paragraphs(text, seen):
    out = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if 100 <= len(para) <= 250 and deva_frac(para) > 0.8 and para not in seen and para not in out:
            out.append(para)
    return out


def leaked(para, blob):
    return para[:40] in blob or para[-40:] in blob


def read_lines(path):
    return [l for l in Path(path).read_text(encoding="utf-8").split("\n") if l.strip()]


# ---------------------------------------------------------------- data
def make_english_calib():
    from datasets import load_dataset
    ds = load_dataset("wikimedia/wikipedia", "20231101.en", split="train", streaming=True)
    chunks, total = [], 0
    for row in ds:
        t = (row.get("text") or "").strip()
        if len(t) < 200:
            continue
        chunks.append(t[:2000])
        total += len(chunks[-1])
        if total >= 200_000:
            break
    Path("data/english_calib.txt").write_text("\n".join(chunks), encoding="utf-8")
    print("english calib chunks:", len(chunks))


def make_wiki_test(blob):
    from datasets import load_dataset
    ds = load_dataset("wikimedia/wikipedia", "20231101.hi", split="train", streaming=True)
    lines, seen = [], set()
    for row in ds:
        cand = [c for c in good_paragraphs(row.get("text"), seen) if not leaked(c, blob)]
        if cand:
            pick = cand[1] if len(cand) > 1 else cand[0]
            seen.add(pick)
            lines.append(pick)
        if len(lines) >= 150:
            break
    Path("data/test_wiki.txt").write_text("\n".join(lines), encoding="utf-8")
    print("wiki test lines:", len(lines))


def make_sangraha_test(blob):
    from datasets import load_dataset
    ds = load_dataset("ai4bharat/sangraha", data_dir="verified/hin", split="train", streaming=True)
    lines, seen = [], set()
    # rows before 2000 may have fed the calibration text, so start after them
    for row in itertools.islice(ds, 2000, 2000 + 4000):
        cand = [c for c in good_paragraphs(row.get("text"), seen) if not leaked(c, blob)]
        if cand:
            pick = cand[-1]
            seen.add(pick)
            lines.append(pick)
        if len(lines) >= 150:
            break
    Path("data/test_sangraha.txt").write_text("\n".join(lines), encoding="utf-8")
    print("sangraha held-out test lines:", len(lines))


def build_domains():
    blob = Path("data/hindi_calib.txt").read_text(encoding="utf-8")
    if not Path("data/english_calib.txt").exists():
        try:
            make_english_calib()
        except Exception as e:
            stop(f"English calibration data nahi bana: {str(e)[:200]}")
    for name, fn in [("test_wiki", make_wiki_test), ("test_sangraha", make_sangraha_test)]:
        if not Path(f"data/{name}.txt").exists():
            log(f"{name} bana raha hu...")
            try:
                fn(blob)
            except Exception as e:
                print(f"WARNING: {name} nahi bana, ye domain chhod raha hu: {str(e)[:200]}")
    doms = {}
    for label, path in [("wiki", "data/test_wiki.txt"), ("sangraha", "data/test_sangraha.txt"),
                        ("hinglish", "data/hinglish_test.txt")]:
        if Path(path).exists():
            lines = read_lines(path)
            if len(lines) >= 30:
                random.Random(0).shuffle(lines)
                out = f"data/eval_{label}.txt"
                Path(out).write_text("\n".join(lines), encoding="utf-8")
                doms[label] = (out, len(lines))
            else:
                print(f"WARNING: {path} me sirf {len(lines)} lines, domain chhod raha hu")
    if not doms:
        stop("Koi test domain nahi bana. Upar ke WARNING padho ya internet check karo.")
    return doms


def build_mixed_calib():
    out = Path("data/mixed_calib.txt")
    if out.exists():
        return
    h = read_lines("data/hindi_calib.txt")
    e = read_lines("data/english_calib.txt")
    mixed = []
    for i in range(max(len(h), len(e))):
        if i < len(h):
            mixed.append(h[i])
        if i < len(e):
            mixed.append(e[i])
    out.write_text("\n".join(mixed), encoding="utf-8")


# ---------------------------------------------------------------- variants
CALIB_FOR = {"hindi": "data/hindi_calib.txt", "english": "data/english_calib.txt", "mixed": "data/mixed_calib.txt"}


def variant_file(v):
    return f"{G}-Q4_K_M-{v}.gguf"


def ensure_variant(v):
    if v == "standard":
        return
    m = re.fullmatch(r"(hindi|english|mixed)(\d*)", v)
    if not m:
        stop(f"Variant samajh nahi aaya: {v}")
    kind, num = m.group(1), m.group(2)
    if Path(variant_file(v)).exists():
        return
    imx = f"{G}.{v}.imatrix" if num else (f"{G}.imatrix" if kind == "hindi" else f"{G}.english.imatrix")
    if not Path(imx).exists():
        if kind == "mixed":
            build_mixed_calib()
        chunks = int(num) if num else 40
        log(f"{v}: imatrix ban raha hai ({chunks} chunks)...")
        rc, out = sh([f"{BIN}/llama-imatrix", "-m", f"{G}-f16.gguf", "-f", CALIB_FOR[kind],
                      "-o", imx, "--chunks", str(chunks), "-t", str(args.threads)])
        if not Path(imx).exists():
            print(out[-1500:])
            stop(f"{v}: imatrix nahi bana")
        save()
    log(f"{v}: quantize ho raha hai...")
    rc, out = sh([f"{BIN}/llama-quantize", "--imatrix", imx, f"{G}-f16.gguf", variant_file(v), "Q4_K_M"])
    if not Path(variant_file(v)).exists():
        print(out[-1500:])
        stop(f"{v}: Q4 nahi bana")
    save()


# ---------------------------------------------------------------- measuring
PATS = {
    "kld": r"Mean\s+KLD:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "rms": r"RMS\s+\S+\s*:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "top": r"Same top p:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
}


def parse(out):
    d = {}
    for k, p in PATS.items():
        m = re.search(p, out)
        if m:
            d[k] = [float(m.group(1)), float(m.group(2))]
    return d


def load_res():
    return json.load(open(RES)) if os.path.exists(RES) else {}


def put_res(res):
    json.dump(res, open(RES, "w"), indent=1)


def measure(res, dom, path, variants):
    K = f"data/f16_{dom}_{MODEL_TAG}.kld"
    common = ["-f", path, "-t", str(args.threads), "--chunks", str(args.chunks)]
    todo = [v for v in variants if v not in res.get(dom, {})]
    if not todo:
        return
    if not Path(K).exists():
        log(f"[{dom}] FP16 base bana raha hu...")
        rc, out = sh([f"{BIN}/llama-perplexity", "-m", f"{G}-f16.gguf", "--kl-divergence-base", K] + common)
        if not Path(K).exists():
            print(out[-1500:])
            stop(f"[{dom}] FP16 base nahi bana")
    for v in todo:
        log(f"[{dom}] {v} naap raha hu...")
        rc, out = sh([f"{BIN}/llama-perplexity", "-m", variant_file(v),
                      "--kl-divergence-base", K, "--kl-divergence"] + common)
        d = parse(out)
        if len(d) < 3:
            print(out[-1800:])
            stop(f"[{dom}] {v}: number padh nahi paya, upar ka output bhejo")
        res.setdefault(dom, {})[v] = d
        put_res(res)
        save()


# ---------------------------------------------------------------- report
def gain(a, b, key):
    """how much better a is than b (positive = a better) and the combined error"""
    low_is_better = key in ("kld", "rms")
    g = (b[key][0] - a[key][0]) if low_is_better else (a[key][0] - b[key][0])
    return g, math.sqrt(a[key][1] ** 2 + b[key][1] ** 2)


def verdict(g, err):
    if g > 2 * err:
        return "SAAF FARK"
    if g > 0:
        return "ishara sahi, saaf nahi"
    return "fark nahi / ulta"


def report(res, doms):
    L = []
    P = lambda s="": (print(s), L.append(s))
    cmp_pairs = [("hindi", "standard"), ("english", "standard"), ("hindi", "english"), ("mixed", "english"), ("mixed", "hindi")]
    base = lambda kind: next((v for v in VARIANTS if re.fullmatch(kind + r"\d+", v)), kind if kind in VARIANTS else None)
    wins = {"hindi_vs_english": [], "mixed_vs_english": []}
    for dom, (path, nlines) in doms.items():
        r = res.get(dom, {})
        P(f"\n=== Domain: {dom} ({nlines} lines, {args.chunks} chunks) ===")
        P("variant        KLD (kam=achha)     RMS dp % (kam)    same-top-p % (zyada)")
        for v in VARIANTS:
            if v in r:
                d = r[v]
                P(f"{NAMES.get(v, v):12s}  {d['kld'][0]:.4f} +/- {d['kld'][1]:.4f}   "
                  f"{d['rms'][0]:.2f} +/- {d['rms'][1]:.2f}   {d['top'][0]:.2f} +/- {d['top'][1]:.2f}")
        for a_kind, b_kind in cmp_pairs:
            a, b = base(a_kind), base(b_kind)
            if not a or not b or a not in r or b not in r:
                continue
            for key, label in [("kld", "KLD"), ("top", "same-top-p")]:
                g, err = gain(r[a], r[b], key)
                v = verdict(g, err)
                P(f"{NAMES.get(a, a)} vs {NAMES.get(b, b)} [{label}]: fark {g:.4f}, error {err:.4f} -> {v}")
                if label == "KLD" and (a_kind, b_kind) == ("hindi", "english"):
                    wins["hindi_vs_english"].append(v)
                if label == "KLD" and (a_kind, b_kind) == ("mixed", "english"):
                    wins["mixed_vs_english"].append(v)
    P("\n=== Faisla (KLD, English ke muqable, barabar chunk budget) ===")
    for k, name in [("hindi_vs_english", "Hindi"), ("mixed_vs_english", "Mixed")]:
        vs = wins[k]
        if not vs:
            continue
        if all(x == "SAAF FARK" for x in vs):
            P(f"{name}: har domain me English se SAAF behtar -> edge asli lagta hai")
        elif any(x == "SAAF FARK" for x in vs):
            P(f"{name}: kuch domain me saaf, kuch me nahi -> adhoora saboot")
        else:
            P(f"{name}: kisi domain me English se saaf behtar nahi -> edge saabit nahi")
    P("\nNote: ye check sakht hai (dono naap alag maane gaye), aur sirf KLD/top-p proxy hai, asli Hindi task nahi.")
    Path(REPORT).write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------- main
def need(path, hint):
    if not Path(path).exists():
        stop(f"MISSING: {path} -> {hint}")


restore()
need(f"{BIN}/llama-perplexity", "llama.cpp build nahi hai: bash scripts/setup.sh")
need(f"{BIN}/llama-imatrix", "llama.cpp build nahi hai: bash scripts/setup.sh")
need(f"{G}-f16.gguf", f"FP16 file nahi hai: python scripts/prepare_model.py --model {args.model}")
need(variant_file("standard"), f"plain Q4 nahi hai: python scripts/prepare_model.py --model {args.model}")
need("data/hindi_calib.txt", "Hindi calibration data nahi hai: python scripts/download_hindi_data.py")
Path("data").mkdir(exist_ok=True)

doms = build_domains()
print("domains:", {k: v[1] for k, v in doms.items()})
for v in VARIANTS:
    ensure_variant(v)
log("saari Q4 files taiyaar")

res = load_res()
for dom, (path, _) in doms.items():
    measure(res, dom, path, VARIANTS)
report(res, doms)
save()
log("khatam")