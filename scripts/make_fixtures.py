# Banata hai: tests/fixtures/model_ids.json aur tests/fixtures/license_key.json
import hashlib
import json
import os

os.makedirs("tests/fixtures", exist_ok=True)
A96, A97 = "a" * 96, "a" * 97
Q = "Qwen/Qwen2.5-1.5B-Instruct"

valid = [
    (Q, Q), ("  " + Q + " \n", Q),
    ("https://huggingface.co/" + Q, Q), ("huggingface.co/" + Q + "/", Q),
    ("https://huggingface.co/" + Q + "/tree/main", Q), ("https://hf.co/" + Q + "?x=1#y", Q),
    ("http://www.huggingface.co/a/b", "a/b"), ("HTTPS://HuggingFace.co/A/B", "A/B"),
    ("meta-llama/Llama-3.2-1B", "meta-llama/Llama-3.2-1B"),
    (A96 + "/b", A96 + "/b"), (A96 + "/" + A96, A96 + "/" + A96),
]

invalid = [
    "Qwen", "", "   ", "a/b/c", "../etc/passwd", "a/..", "-a/b", "a/-b", "a b/c", "a/b\nc",
    "https://huggingface.co/datasets/a/b", "https://huggingface.co/spaces/a/b", "datasets/a",
    "https://huggingface.co/onlyone", "https://example.com/a/b", "http://evil.com/a/b",
    A97 + "/b", "a/" + "b" * 97, 
    "a/b;drop table quantization_jobs", # --- FIXED: Real database table security check ---
    "a/b'--"
]

cases = [{"input": i, "expected": e} for i, e in valid] + [{"input": i, "expected": None} for i in invalid]

with open("tests/fixtures/model_ids.json", "w", encoding="utf-8") as f:
    json.dump(cases, f, indent=1, ensure_ascii=False)
    f.write("\n")

key = "STH-ABCD-EFGH-JKLM-NPQR"
norm = "".join(ch for ch in key.upper() if ch.isalnum())

with open("tests/fixtures/license_key.json", "w", encoding="utf-8") as f:
    json.dump({"key": key, "normalized": norm, "hash": hashlib.sha256(norm.encode()).hexdigest()}, f, indent=1)
    f.write("\n")

print("fixtures ban gaye:", len(cases), "model cases")
