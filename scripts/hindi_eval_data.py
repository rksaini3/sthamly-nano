"""Hindi test sets for scripts/audit.py.

This is the same logic scripts/trl4_bench.py uses (copied so the audit works on its own).
If you change how test sets are built, change BOTH files, otherwise audit numbers stop
being comparable with the benchmark numbers.
"""
import itertools
import random
from pathlib import Path


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
    """True if the start or end of `para` also appears in the calibration text."""
    if not blob:
        return False
    return para[:40] in blob or para[-40:] in blob


def read_lines(path):
    return [l for l in Path(path).read_text(encoding="utf-8").split("\n") if l.strip()]


def make_wiki_test(data_dir, blob):
    from datasets import load_dataset
    ds = load_dataset("wikimedia/wikipedia", "20231101.hi", split="train", streaming=True)
    lines, seen = [], set()
    for row in ds:
        cand = [c for c in good_paragraphs(row.get("text"), seen) if not leaked(c, blob)]
        if cand:
            # --- FIXED INDEX OUT OF BOUNDS INTERFACE MATRIX ---
            # Corrected inline configuration choice logic to block list pointer exceptions safely
            pick = cand[1] if len(cand) > 1 else cand[0]
            seen.add(pick)
            lines.append(pick)
        if len(lines) >= 150:
            break
    (Path(data_dir) / "test_wiki.txt").write_text("\n".join(lines), encoding="utf-8")


def make_sangraha_test(data_dir, blob):
    from datasets import load_dataset
    # --- FIXED DATASET STREAMING MATRIX SLUG ---
    # Aligned perfectly with our download_hindi_data setup script config constraints
    try:
        ds = load_dataset("ai4bharat/sangraha", name="verified/hin", split="train", streaming=True)
    except Exception:
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
    (Path(data_dir) / "test_sangraha.txt").write_text("\n".join(lines), encoding="utf-8")


def ensure_eval_sets(data_dir, log=print):
    """Return {label: (path, n_lines)}; builds missing sets (needs `datasets` + internet)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    calib = data_dir / "hindi_calib.txt"
    blob = calib.read_text(encoding="utf-8") if calib.exists() else ""
    doms = {}
    
    for label, builder, test_name in [("wiki", make_wiki_test, "test_wiki.txt"),
                                      ("sangraha", make_sangraha_test, "test_sangraha.txt"),
                                      ("hinglish", None, "hinglish_test.txt")]:
        eval_path = data_dir / f"eval_{label}.txt"
        if eval_path.exists():
            doms[label] = (str(eval_path), len(read_lines(eval_path)))
            continue
            
        test_path = data_dir / test_name
        if not test_path.exists() and builder is not None:
            if not blob:
                log("NOTE: data/hindi_calib.txt nahi hai, leak-check chhod raha hu "
                    "(python scripts/download_hindi_data.py se banta hai).")
            log(f"{test_name} bana raha hu (internet + `pip install datasets` chahiye)...")
            try:
                builder(data_dir, blob)
            except Exception as e:
                log(f"WARNING: {test_name} nahi bana, ye test set chhod raha hu: {str(e)[:200]}")
                
        if test_path.exists():
            lines = read_lines(test_path)
            if len(lines) >= 30:
                random.Random(0).shuffle(lines)
                eval_path.write_text("\n".join(lines), encoding="utf-8")
                doms[label] = (str(eval_path), len(lines))
            else:
                log(f"WARNING: {test_path} me sirf {len(lines)} lines, ye test set chhod raha hu")
                
    if not doms:
        raise RuntimeError("Koi Hindi test set nahi bana. Internet + `pip install datasets` check karo, "
                           "ya data/eval_wiki.txt / data/eval_sangraha.txt khud rakho.")
    return doms
