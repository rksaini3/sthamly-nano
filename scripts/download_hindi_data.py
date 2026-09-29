"""Hindi/Hinglish calibration text banata hai — isi text se imatrix banega.

IMPORTANT: DATASET_ID neeche ek udaharan hai. Hugging Face par jaake sahi
dataset/config/split khud confirm karo, warna load fail hoga.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "hindi_calib.txt"
OUT.parent.mkdir(parents=True, exist_ok=True)

DATASET_ID = "ai4bharat/sangraha"  # verify on huggingface.co before running


def build_calibration_file(max_chars: int = 2_000_000):
    text_chunks = []
    try:
        from datasets import load_dataset
        ds = load_dataset(DATASET_ID, split="train", streaming=True)
        total = 0
        for row in ds:
            txt = row.get("text", "")
            if not txt:
                continue
            text_chunks.append(txt)
            total += len(txt)
            if total >= max_chars:
                break
    except Exception as e:
        print(f"Dataset load failed ({e}).")
        print(f"Fallback: manually Hindi/Hinglish text {OUT} me paste kar do.")
        OUT.touch()
        return

    OUT.write_text("\n".join(text_chunks), encoding="utf-8")
    print(f"Saved {len(text_chunks)} chunks -> {OUT}")


if __name__ == "__main__":
    build_calibration_file()