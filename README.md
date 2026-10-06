# Sthamly NanoBrain

Hindi ke liye GGUF quantization, **FP16 se naapa hua, error bars ke saath**, plus ek public
benchmark dashboard.

Pehla model: [Qwen2.5-1.5B-Instruct-hindi-imatrix-Q4_K_M-GGUF](https://huggingface.co/rajasaini2208/Qwen2.5-1.5B-Instruct-hindi-imatrix-Q4_K_M-GGUF)
(1.12 GB, FP16 3.56 GB).

## Kya saabit hua, kya nahi

KLD vs FP16 (kam = behtar), 150 held-out lines per test set, 12 chunks. Poora data:
[`results/`](results/PROVENANCE.md).

| | wiki | sangraha |
|---|---|---|
| Plain Q4_K_M | 0.0642 ± 0.0030 | 0.0635 ± 0.0019 |
| English imatrix | 0.0545 ± 0.0019 | 0.0627 ± 0.0023 |
| **Hindi imatrix** | **0.0509 ± 0.0018** | **0.0573 ± 0.0024** |
| Mixed imatrix | 0.0498 ± 0.0018 | 0.0586 ± 0.0019 |

- Hindi imatrix plain Q4 se **saaf behtar** hai (KLD −21% wiki, −10% sangraha).
- English imatrix wiki par saaf behtar hai, sangraha par **saaf nahi** (0.0008 ± 0.0030).
- **Hindi vs English imatrix: kisi test set me saaf fark nahi.** Isliye hum ye nahi kehte ki
  "Hindi text se calibrate karna" alag se behtar hai. Saaf fayda "imatrix lagane" ka hai.
- Ye sirf proxy hai (FP16 se distribution ka fark). **Asli Hindi task (QA, summary) abhi naapa nahi gaya.**

"Saaf" ka matlab: fark > 2 × combined error (`scripts/trl4_bench.py` aur `lib/verdict.ts` same rule use karte hain).

## Pipeline (Python; Linux / WSL2 / Mac / Colab)

    bash scripts/setup.sh                      # venv + llama.cpp build
    source .venv/bin/activate
    python scripts/prepare_model.py --model Qwen/Qwen2.5-1.5B-Instruct   # FP16 GGUF + plain Q4 (same FP16)
    python scripts/download_hindi_data.py      # Hindi calibration text (sangraha verified/hin, pehli 2000 rows)
    python scripts/trl4_bench.py --model Qwen/Qwen2.5-1.5B-Instruct      # imatrix variants + KLD vs FP16
    python scripts/publish_mvp.py --model Qwen/Qwen2.5-1.5B-Instruct     # DRY-RUN: card dikhata hai, upload nahi

`trl4_bench.py` resumable hai (har step skip hota hai agar ho chuka). Free Colab (2 CPU) par 1.5B model ke liye ~7 min
per variant per test set lagte hain (Colab log se); zyada CPU ho to `--threads` badhao.

Asli publish (HF + Supabase), env pehle export karo (`.env.example` dekho):

    export HF_WRITE_TOKEN=... SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...
    python scripts/publish_mvp.py --model Qwen/Qwen2.5-1.5B-Instruct --go          # public
    python scripts/publish_mvp.py --model Qwen/Qwen2.5-1.5B-Instruct --go --private

`publish_mvp.py` khud mana kar deta hai agar Hindi imatrix plain Q4 se *har* test set me saaf behtar nahi,
ya plain Q4 alag FP16 se bani lagti hai (size mismatch). Model card me Hindi-vs-English ka claim
sirf tab aata hai jab data use support kare.

Agar purani plain Q4 alag FP16 se bani thi: `scripts/rebuild_plain_q4.py`.

## Dashboard (Next.js)

    npm install
    cp .env.example .env.local        # NEXT_PUBLIC_* values bharo
    npm run dev                        # http://localhost:3000
    npm run typecheck && npm test

1. Supabase project banao, `db/schema.sql` SQL editor me chalao (dobara chalana safe hai).
2. Website sirf **anon key** se padhti hai. RLS me sirf SELECT policy hai, likhne ka koi rasta nahi.
   Service-role key sirf `publish_mvp.py` ke paas hoti hai, website ke paas kabhi nahi.
3. Site bina Supabase env ke bhi build hoti hai (error message dikhati hai, crash nahi).
4. `NEXT_PUBLIC_CONTACT_URL` set karo to home page par "Free Hindi Quality Audit" button dikhta hai.

## Structure

- `app/`, `components/`, `lib/` — dashboard. `lib/verdict.ts` me "saaf fark" ka rule (tests: `lib/verdict.test.ts`)
- `scripts/` — `prepare_model.py`, `download_hindi_data.py`, `trl4_bench.py`, `publish_mvp.py`, `rebuild_plain_q4.py`, `setup.sh`
- `db/schema.sql` — Supabase table + RLS
- `results/` — asli benchmark numbers + `PROVENANCE.md` (kahan se aaye, kya nahi dikhate)
- `data/hindi_test.txt` — chhota hand-written sample (benchmark me use nahi hota)

## Seemaayein / baaki kaam

- Asli Hindi task par quality (QA, summarisation) naapi nahi gayi.
- Test chhote hain (150 lines, 12 chunks) aur sirf Hindi Wikipedia + sangraha se. Hinglish test set nahi hai
  (`data/hinglish_test.txt` rakho to `trl4_bench.py` use utha leta hai).
- `results/` me JSON hath se Colab report se utaara gaya hai, raw file nahi (`PROVENANCE.md`).
- Sirf Qwen2.5 0.5B/1.5B ka license `publish_mvp.py` me verify hai; naya model add karne se pehle uska license padho.

## License

Code: MIT (`LICENSE`). Published models apne base model ka license follow karte hain (Qwen2.5: Apache-2.0).