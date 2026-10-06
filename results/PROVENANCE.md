# Results provenance

`results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json` holds the numbers behind the published
model `rajasaini2208/Qwen2.5-1.5B-Instruct-hindi-imatrix-Q4_K_M-GGUF`.

- Format is exactly what `scripts/trl4_bench.py` writes: `domain -> variant -> {kld, rms, top}`,
  each `[mean, +/- error]`.
- **Transcribed by hand** from the final Colab report of the matched-FP16 run
  (after `scripts/rebuild_plain_q4.py` rebuilt plain Q4 from the same FP16 file as the
  imatrix variants). This is not the raw JSON that Colab produced. If you still have that
  file in Google Drive (`results/results_trl4_Qwen__Qwen2.5-1.5B-Instruct.json`), replace
  this one with it.
- Setup: 150 held-out lines per domain (Hindi Wikipedia, sangraha `verified/hin`),
  12 eval chunks, imatrix variants built from 60 calibration chunks each.
- An earlier run compared plain Q4 built from a *different* FP16 file (size mismatch,
  986 MB vs 1.1 GB) and showed inflated gains (~40%). That run is superseded. Do not cite it.

What the numbers do and do not show (see the report block of `trl4_bench.py`):

| Claim | wiki | sangraha |
|---|---|---|
| Hindi imatrix beats plain Q4 | clear | clear |
| English imatrix beats plain Q4 | clear | **not clear** (0.0008 ± 0.0030) |
| Hindi imatrix beats English imatrix | not clear | not clear |
| Mixed imatrix beats English imatrix | not clear | not clear |

KLD and same-top-p against FP16 are a proxy. No real Hindi task (QA, summarisation) has been scored yet.

Rounding note: the file keeps 4 decimals, so differences recomputed from it can differ from the
original report by 0.0001 (e.g. English vs plain on wiki: 0.0097 here, 0.0098 in the Colab report).
Verdicts do not change.

Republishing: `publish_mvp.py` computes card percentages from this file, so with the rounded numbers the
"vs English-imatrix" wiki figure reads 7% while the live card says 6%. To keep the card identical, copy the
raw `results_trl4_*.json` from Google Drive into the repo root before republishing.