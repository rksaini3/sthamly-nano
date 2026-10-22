"""TRL-4 check: is the Hindi imatrix edge real, or is it just "any imatrix"?

What it does (resumable; finished steps are skipped on re-run):
  1. Builds Hindi test domains (Hindi Wikipedia + held-out sangraha rows,
     plus data/hinglish_test.txt if present). Lines overlapping calibration
     text are dropped (leak guard on 40-char prefix/suffix).
  2. Builds Q4 variants with the SAME calibration token budget:
       standard  plain Q4_K_M, no imatrix
       hindi<N>  imatrix from Hindi calibration
       english<N> imatrix from English calibration
       mixed<N>  imatrix from Hindi+English interleaved, same token budget
  3. Measures each variant against FP16 (KL-divergence mode of llama-perplexity)
     on each domain, and reports the paired per-chunk difference when available.
  4. Writes results + a report. Multiple-comparison correction is applied.

Usage (repo root):
    python scripts/trl4_bench.py --model Qwen/Qwen2.5-0.5B-Instruct
    python scripts/trl4_bench.py --calib-tokens 40000 --eval-chunks 50
"""
from __future__ import annotations

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

# ============================================================
# ARGUMENTS
# ============================================================

ap = argparse.ArgumentParser(description="TRL-4 Hindi imatrix edge check")
ap.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
ap.add_argument("--eval-chunks", type=int, default=50,
                help="eval chunks per domain; more = smaller error bars, slower (default 50)")
ap.add_argument("--eval-ctx", type=int, default=512,
                help="context length used for eval chunks (-c); reported in output")
ap.add_argument("--calib-chunks", type=int, default=60,
                help="imatrix chunks for the English/Hindi/mixed variants")
ap.add_argument("--calib-ctx", type=int, default=512,
                help="context length used for calibration chunks")
ap.add_argument("--variants", default="",
                help="comma list; default standard,hindi<N>,english<N>,mixed<N>")
ap.add_argument("--threads", type=int, default=2)
ap.add_argument("--cmd-timeout", type=int, default=7200,
                help="seconds before a single llama.cpp command is killed")
ap.add_argument("--fresh", action="store_true",
                help="ignore saved results and re-measure everything")
args = ap.parse_args()

N = args.calib_chunks
BIN = Path("llama.cpp/build/bin")
MODEL_TAG = args.model.replace("/", "__")
G = Path(f"models/gguf/{MODEL_TAG}")
RES = Path(f"results_trl4_{MODEL_TAG}.json")
REPORT = Path(f"report_trl4_{MODEL_TAG}.txt")
DRIVE = os.environ.get("DRIVE_DIR", "")
T0 = time.time()

DEFAULT_VARIANTS = ["standard", f"hindi{N}", f"english{N}", f"mixed{N}"]
VARIANTS = [v.strip() for v in args.variants.split(",") if v.strip()] or DEFAULT_VARIANTS

LABELS = {"standard": "Plain Q4", f"hindi{N}": f"Hindi {N}",
          f"english{N}": f"English {N}", f"mixed{N}": f"Mixed {N}"}

# Per-chunk paired comparison requires --kl-output-file style logs; llama-perplexity
# prints only aggregates, so pairing is done on aggregates with the conservative
# independence assumption and labelled as such in the report.
PAIRED_AVAILABLE = False


def log(msg: str) -> None:
    s = int(time.time() - T0)
    print(f"[{s // 60:02d}:{s % 60:02d}] {msg}", flush=True)


def run_cmd(cmd: list[Any], label: str) -> tuple[int, str]:
    """Run a llama.cpp command with a timeout. Never hangs silently."""
    cmd = [str(c) for c in cmd]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.cmd_timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "") if isinstance(exc.stdout, str) else ""
        return -1, f"TIMEOUT after {args.cmd_timeout}s ({label})\n{out}"


def stop(msg: str) -> None:
    save()
    sys.exit(msg)


# ============================================================
# DRIVE SAVE / RESTORE
# ============================================================

DRIVE_SIZE_LIMIT_BYTES = 2.2e9  # Drive upload practical limit used here; larger GGUFs stay local


def _copy_if_new(src: Path, dst: Path) -> None:
    if src.stat().st_size > DRIVE_SIZE_LIMIT_BYTES:
        return
    if dst.exists() and dst.stat().st_size == src.stat().st_size:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def save() -> None:
    if not DRIVE or not os.path.isdir(DRIVE):
        return
    try:
        drive = Path(DRIVE)
        for f in Path(".").glob("results_trl4_*.json"):
            _copy_if_new(f, drive / "results" / f.name)
        for f in Path(".").glob("report_trl4_*.txt"):
            shutil.copy2(f, drive / "results" / f.name)
        for f in Path("data").glob("*.txt"):
            _copy_if_new(f, drive / "data" / f.name)
        for f in Path("models/gguf").glob("*"):
            if f.is_file() and not f.name.endswith("-f16.gguf") and f.suffix != ".kld":
                _copy_if_new(f, drive / "models" / "gguf" / f.name)
        log("Drive me save ho gaya")
    except Exception as exc:
        print("save warning:", str(exc)[:200])


def restore() -> None:
    if not DRIVE or not os.path.isdir(DRIVE):
        return
    drive = Path(DRIVE)
    n = 0
    for sub in ("data", "models/gguf"):
        src = drive / sub
        if not src.is_dir():
            continue
        for f in src.glob("*"):
            dst = Path(sub) / f.name
            if f.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                n += 1
    # Only restore results that belong to this model.
    res_file = drive / "results" / RES.name
    if res_file.is_file() and not RES.exists():
        shutil.copy2(res_file, RES)
        n += 1
    if n:
        log(f"Drive se {n} file wapas laayi")


# ============================================================
# TEXT HELPERS
# ============================================================

def deva_frac(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return 0.0
    return sum("\u0900" <= c <= "\u097f" for c in letters) / len(letters)


def good_paragraphs(text: str | None, seen: set[str]) -> list[str]:
    out: list[str] = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if 100 <= len(para) <= 250 and deva_frac(para) > 0.8 and para not in seen and para not in out:
            out.append(para)
    return out


def leaked(para: str, blob: str) -> bool:
    return para[:40] in blob or para[-40:] in blob


def read_lines(path: str | Path) -> list[str]:
    return [l for l in Path(path).read_text(encoding="utf-8").split("\n") if l.strip()]


def approx_tokens(path: str | Path) -> int:
    """Rough token count: chars / 3 for mixed Hindi/English text. Used for budget matching."""
    return int(Path(path).stat().st_size / 3)


# ============================================================
# DATA
# ============================================================

def make_english_calib(target_chars: int) -> None:
    from datasets import load_dataset
    ds = load_dataset("wikimedia/wikipedia", "20231101.en", split="train", streaming=True)
    chunks: list[str] = []
    total = 0
    for row in ds:
        t = (row.get("text") or "").strip()
        if len(t) < 200:
            continue
        piece = t[:2000]
        chunks.append(piece)
        total += len(piece)
        if total >= target_chars:
            break
    Path("data/english_calib.txt").write_text("\n".join(chunks), encoding="utf-8")
    print("english calib chunks:", len(chunks), "chars:", total)


def make_wiki_test(blob: str) -> None:
    from datasets import load_dataset
    ds = load_dataset("wikimedia/wikipedia", "20231101.hi", split="train", streaming=True)
    lines: list[str] = []
    seen: set[str] = set()
    for row in ds:
        cand = [c for c in good_paragraphs(row.get("text"), seen) if not leaked(c, blob)]
        if cand:
            pick = cand[1] if len(cand) > 1 else cand[0]
            seen.add(pick)
            lines.append(pick)
        if len(lines) >= 200:
            break
    Path("data/test_wiki.txt").write_text("\n".join(lines), encoding="utf-8")
    print("wiki test lines:", len(lines))


def make_sangraha_test(blob: str) -> None:
    """
    Held-out sangraha rows. The calibration builder must record which rows it used
    (data/hindi_calib_rows.json) so the test rows can be chosen disjoint from them.
    If that file is missing, the prefix/suffix leak check is the only guard.
    """
    from datasets import load_dataset
    used_rows: set[int] = set()
    rows_file = Path("data/hindi_calib_rows.json")
    if rows_file.exists():
        used_rows = set(json.loads(rows_file.read_text()))
    else:
        print("WARNING: data/hindi_calib_rows.json nahi mila; sangraha ka row-level leak check nahi ho sakta.")

    ds = load_dataset("ai4bharat/sangraha", data_dir="verified/hin", split="train", streaming=True)
    lines: list[str] = []
    seen: set[str] = set()
    for idx, row in enumerate(ds):
        if idx >= 20000:
            break
        if idx in used_rows:
            continue
        cand = [c for c in good_paragraphs(row.get("text"), seen) if not leaked(c, blob)]
        if cand:
            pick = cand[-1]
            seen.add(pick)
            lines.append(pick)
        if len(lines) >= 200:
            break
    Path("data/test_sangraha.txt").write_text("\n".join(lines), encoding="utf-8")
    print("sangraha held-out test lines:", len(lines))


def build_domains() -> dict[str, tuple[str, int]]:
    blob = Path("data/hindi_calib.txt").read_text(encoding="utf-8")
    if not Path("data/english_calib.txt").exists():
        # Match the English calibration budget to the Hindi calibration budget.
        target = Path("data/hindi_calib.txt").stat().st_size
        try:
            make_english_calib(target)
        except Exception as exc:
            stop(f"English calibration data nahi bana: {str(exc)[:200]}")

    for name, fn in [("test_wiki", make_wiki_test), ("test_sangraha", make_sangraha_test)]:
        if not Path(f"data/{name}.txt").exists():
            log(f"{name} bana raha hoon...")
            try:
                fn(blob)
            except Exception as exc:
                print(f"WARNING: {name} nahi bana, ye domain chhod raha hoon: {str(exc)[:200]}")

    doms: dict[str, tuple[str, int]] = {}
    sources = [("wiki", "data/test_wiki.txt"), ("sangraha", "data/test_sangraha.txt"),
               ("hinglish", "data/hinglish_test.txt")]
    for label, path in sources:
        if not Path(path).exists():
            continue
        lines = read_lines(path)
        if len(lines) < 30:
            print(f"WARNING: {path} me sirf {len(lines)} lines, domain chhod raha hoon")
            continue
        random.Random(0).shuffle(lines)
        out = Path(f"data/eval_{label}.txt")
        out.write_text("\n".join(lines), encoding="utf-8")
        doms[label] = (str(out), len(lines))

    if not doms:
        stop("Koi test domain nahi bana. Upar ke WARNING padho ya internet check karo.")
    return doms


def build_mixed_calib() -> None:
    """Hindi and English lines interleaved. Each line keeps its own language."""
    out = Path("data/mixed_calib.txt")
    if out.exists():
        return
    h = read_lines("data/hindi_calib.txt")
    e = read_lines("data/english_calib.txt")
    mixed: list[str] = []
    for hl, el in itertools.zip_longest(h, e):
        if hl is not None:
            mixed.append(hl)
        if el is not None:
            mixed.append(el)
    out.write_text("\n".join(mixed), encoding="utf-8")


# ============================================================
# VARIANTS (equal calibration token budget)
# ============================================================

CALIB_FOR = {
    "hindi": "data/hindi_calib.txt",
    "english": "data/english_calib.txt",
    "mixed": "data/mixed_calib.txt",
}

VARIANT_RE = re.compile(r"^(standard|(hindi|english|mixed)(\d+))$")


def variant_file(v: str) -> Path:
    return Path(f"{G}-Q4_K_M-{v}.gguf")


def imatrix_file(v: str) -> Path:
    return Path(f"{G}.{v}.imatrix")


def ensure_variant(v: str) -> None:
    if v == "standard":
        if not variant_file(v).exists():
            stop(f"Plain Q4 nahi mila: {variant_file(v)}")
        return

    m = VARIANT_RE.fullmatch(v)
    if not m:
        stop(f"Variant samajh nahi aaya: {v!r}")
    kind, chunks = m.group(2), int(m.group(3))

    if variant_file(v).exists():
        return

    if kind == "mixed":
        build_mixed_calib()

    imx = imatrix_file(v)
    if not imx.exists():
        log(f"{v}: imatrix ban raha hai ({chunks} chunks, ctx {args.calib_ctx})...")
        rc, out = run_cmd(
            [BIN / "llama-imatrix", "-m", f"{G}-f16.gguf", "-f", CALIB_FOR[kind],
             "-o", imx, "--chunks", chunks, "-c", args.calib_ctx, "-t", args.threads],
            label=f"imatrix {v}",
        )
        if rc != 0 or not imx.exists():
            print(out[-1500:])
            stop(f"{v}: imatrix nahi bana")
        save()

    log(f"{v}: quantize ho raha hai...")
    rc, out = run_cmd(
        [BIN / "llama-quantize", "--imatrix", imx, f"{G}-f16.gguf", variant_file(v), "Q4_K_M"],
        label=f"quantize {v}",
    )
    if rc != 0 or not variant_file(v).exists():
        print(out[-1500:])
        stop(f"{v}: Q4 nahi bana")
    save()


# ============================================================
# MEASUREMENT
# ============================================================

PATS = {
    "kld": r"Mean\s+KLD:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "rms": r"RMS\s+\S+\s*:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "top": r"Same top p:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
}


def parse(out: str) -> dict[str, list[float]]:
    d: dict[str, list[float]] = {}
    for key, pattern in PATS.items():
        m = re.search(pattern, out)
        if m:
            d[key] = [float(m.group(1)), float(m.group(2))]
    return d


def load_res() -> dict[str, Any]:
    if args.fresh or not RES.exists():
        return {}
    return json.loads(RES.read_text(encoding="utf-8"))


def put_res(res: dict[str, Any]) -> None:
    RES.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")


def measure(res: dict[str, Any], dom: str, path: str, variants: list[str]) -> None:
    # Base file is keyed by domain AND eval settings, so a changed setting never reuses a stale base.
    base = Path(f"data/f16_{dom}_{MODEL_TAG}_c{args.eval_chunks}_x{args.eval_ctx}.kld")
    common = ["-f", path, "-t", args.threads, "--chunks", args.eval_chunks, "-c", args.eval_ctx]

    todo = [v for v in variants if v not in res.get(dom, {})]
    if not todo:
        return

    if not base.exists():
        log(f"[{dom}] FP16 base bana raha hoon...")
        rc, out = run_cmd(
            [BIN / "llama-perplexity", "-m", f"{G}-f16.gguf", "--kl-divergence-base", base] + common,
            label=f"base {dom}",
        )
        if rc != 0 or not base.exists():
            print(out[-1500:])
            stop(f"[{dom}] FP16 base nahi bana")

    for v in todo:
        log(f"[{dom}] {v} naap raha hoon...")
        rc, out = run_cmd(
            [BIN / "llama-perplexity", "-m", variant_file(v),
             "--kl-divergence-base", base, "--kl-divergence"] + common,
            label=f"measure {dom} {v}",
        )
        d = parse(out)
        if len(d) < 3:
            print(out[-1800:])
            stop(f"[{dom}] {v}: number padh nahi paya; upar ka output bhejo")
        res.setdefault(dom, {})[v] = d
        put_res(res)
        save()


# ============================================================
# REPORT
# ============================================================

@dataclass
class Comparison:
    dom: str
    a: str
    b: str
    metric: str
    gain: float
    err: float
    z: float
    p_value: float
    verdict: str


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def compare(res_dom: dict[str, Any], a: str, b: str, key: str) -> tuple[float, float]:
    """Positive gain = a is better than b. Lower KLD/RMS is better; higher top-p is better."""
    low_is_better = key in ("kld", "rms")
    ga = res_dom[a][key][0]
    gb = res_dom[b][key][0]
    gain = (gb - ga) if low_is_better else (ga - gb)
    # Conservative: treat the two reported +/- values as independent.
    err = math.sqrt(res_dom[a][key][1] ** 2 + res_dom[b][key][1] ** 2)
    return gain, err


def report(res: dict[str, Any], doms: dict[str, tuple[str, int]]) -> None:
    lines: list[str] = []

    def P(s: str = "") -> None:
        print(s)
        lines.append(s)

    P(f"Model: {args.model} | eval chunks: {args.eval_chunks} | eval ctx: {args.eval_ctx}")
    P(f"Calibration chunks: {N} | calib ctx: {args.calib_ctx}")
    P("Pairing: aggregate only (per-chunk paired test unavailable); errors treated as independent (conservative).")

    pairs = [
        (f"hindi{N}", "standard"), (f"english{N}", "standard"),
        (f"hindi{N}", f"english{N}"), (f"mixed{N}", f"english{N}"), (f"mixed{N}", f"hindi{N}"),
    ]
    pairs = [(a, b) for a, b in pairs if a in VARIANTS and b in VARIANTS]

    comparisons: list[Comparison] = []

    for dom, (_, nlines) in doms.items():
        r = res.get(dom, {})
        P(f"\n=== Domain: {dom} ({nlines} lines) ===")
        P("variant        KLD (kam=achha)        RMS dp % (kam)      same-top-p % (zyada)")
        for v in VARIANTS:
            if v in r:
                d = r[v]
                P(f"{LABELS.get(v, v):12s}  {d['kld'][0]:.4f} +/- {d['kld'][1]:.4f}   "
                  f"{d['rms'][0]:.2f} +/- {d['rms'][1]:.2f}   {d['top'][0]:.2f} +/- {d['top'][1]:.2f}")

        for a, b in pairs:
            if a not in r or b not in r:
                continue
            for key, label in [("kld", "KLD"), ("top", "same-top-p")]:
                gain, err = compare(r, a, b, key)
                z = gain / err if err > 0 else 0.0
                p = normal_two_sided_p(z)
                comparisons.append(Comparison(dom, a, b, label, gain, err, z, p, ""))

    # Multiple-comparison correction (Holm-Bonferroni style, conservative).
    m = len(comparisons)
    ordered = sorted(range(m), key=lambda i: comparisons[i].p_value)
    adjusted = [1.0] * m
    running_max = 0.0
    for rank, idx in enumerate(ordered):
        adj = min(1.0, (m - rank) * comparisons[idx].p_value)
        running_max = max(running_max, adj)
        adjusted[idx] = running_max

    P("\n=== Paired comparisons (Holm-corrected p) ===")
    for i, c in enumerate(comparisons):
        p_adj = adjusted[i]
        if c.gain > 0 and p_adj < 0.05:
            v = "SAAF FARK (corrected p < 0.05)"
        elif c.gain > 0:
            v = "ishara, corrected p >= 0.05"
        else:
            v = "fark nahi / ulta"
        P(f"[{c.dom}] {LABELS.get(c.a, c.a)} vs {LABELS.get(c.b, c.b)} [{c.metric}]: "
          f"fark {c.gain:+.4f}, err {c.err:.4f}, p_adj {p_adj:.3f} -> {v}")

    P("\n=== Faisla: Hindi imatrix vs English imatrix (KLD) ===")
    hk = [c for c in comparisons if c.a == f"hindi{N}" and c.b == f"english{N}" and c.metric == "KLD"]
    if not hk:
        P("Hindi vs English comparison available nahi hai (variants check karo).")
    else:
        sig = [i for i, c in enumerate(comparisons) if c in hk and c.gain > 0 and adjusted[i] < 0.05]
        if len(sig) == len(hk):
            P(f"Hindi edge: sabhi {len(hk)} domains me corrected p < 0.05 -> edge asli lagta hai (proxy metric par).")
        elif sig:
            P(f"Hindi edge: {len(sig)}/{len(hk)} domains me saabit -> adhoora saboot.")
        else:
            P("Hindi edge: kisi domain me corrected p < 0.05 nahi -> edge saabit nahi.")

    P("\nLimitations:")
    P("- Ek hi model (0.5B/1.5B) par; dusre models par generalize nahi hota.")
    P("- KLD/top-p proxy hain, asli Hindi task accuracy nahi.")
    P("- Errors independent maane gaye hain; paired per-chunk test se ye better hoga.")
    P("- Calibration ka token budget chars/3 estimate par based hai; exact tokens ke liye tokenizer se count karo.")

    REPORT.write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# MAIN
# ============================================================

def need(path: Path, hint: str) -> None:
    if not path.exists():
        stop(f"MISSING: {path} -> {hint}")


def main() -> None:
    restore()
    need(BIN / "llama-perplexity", "llama.cpp build nahi hai: bash scripts/setup.sh")
    need(BIN / "llama-imatrix", "llama.cpp build nahi hai: bash scripts/setup.sh")
    need(Path(f"{G}-f16.gguf"), f"FP16 file nahi hai: python scripts/prepare_model.py --model {args.model}")
    need(variant_file("standard"), f"plain Q4 nahi hai: python scripts/prepare_model.py --model {args.model}")
    need(Path("data/hindi_calib.txt"), "Hindi calibration data nahi hai: python scripts/download_hindi_data.py")
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


if __name__ == "__main__":
    main()