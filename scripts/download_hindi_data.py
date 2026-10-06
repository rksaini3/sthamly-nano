"""Builds the Hindi calibration text (data/hindi_calib.txt) that the Hindi imatrix is made from.

Uses ai4bharat/sangraha, config `verified/hin`, and ONLY its first ROW_LIMIT rows.
scripts/trl4_bench.py builds its held-out sangraha test set from rows AFTER
ROW_LIMIT, so calibration and test text never overlap. Do not raise ROW_LIMIT above
2000 without changing the `islice(ds, 2000, ...)` start in trl4_bench.py.
"""
import itertools
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "hindi_calib.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

DATASET_ID = "ai4bharat/sangraha"
DATA_DIR = "verified/hin"  # same config trl4_bench.py uses for its test set
ROW_LIMIT = 2000


def build_calibration_file(max_chars: int = 2_000_000):
    from datasets import load_dataset

    ds = load_dataset(DATASET_ID, data_dir=DATA_DIR, split="train", streaming=True)
    chunks, total = [], 0
    for row in itertools.islice(ds, ROW_LIMIT):
        txt = (row.get("text") or "").strip()
        if not txt:
            continue
        chunks.append(txt)
        total += len(txt)
        if total >= max_chars:
            break

    if not chunks:
        raise SystemExit("Dataset se koi text nahi aaya. Internet / dataset access check karo.")
    OUT.write_text("\n".join(chunks), encoding="utf-8")
    print(f"Saved {len(chunks)} rows ({total:,} chars) -> {OUT}")


if __name__ == "__main__":
    build_calibration_file()