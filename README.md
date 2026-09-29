# Sthamly NanoBrain

Hindi-calibrated AI model compression. FP16 -> GGUF INT4, tuned so Hindi/Hinglish
quality survives compression, with a public benchmark dashboard.

## Setup

### 1. Node dashboard
    npm install
    cp .env.example .env.local   # fill in real values

### 2. Supabase
Create a project at supabase.com, then run `db/schema.sql` in its SQL editor.

### 3. Python pipeline (WSL2 / Linux / Mac)
    bash scripts/setup.sh
    source .venv/bin/activate
    python scripts/download_hindi_data.py
    python scripts/compress_and_log.py --model TinyLlama/TinyLlama-1.1B-Chat-v1.0

### 4. Run dashboard
    npm run dev   # http://localhost:3000

## Structure
- `app/` — Next.js pages (home, benchmark) + API route
- `components/` — ModelCard, ComparisonTable, UI primitives
- `lib/supabase.js` — public read-only client
- `scripts/` — Python compression pipeline (download, imatrix, quantize, log)
- `db/schema.sql` — Supabase table + RLS policy

## Still to do
- Verify `DATASET_ID` in `scripts/download_hindi_data.py` on Hugging Face.
- Write `scripts/run_benchmark.py` — scores the Hindi test set across FP16,
  standard Q4, and Hindi-calibrated Q4, and writes results back to Supabase.
  Nothing on the dashboard shows a number until this exists and runs.