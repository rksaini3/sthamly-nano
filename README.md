# Sthamly Legal Desk

Vakeel ke liye offline AI sahayak. PDF aur chat aapke computer par rehte hain. Notice aur bail application ka draft,
aur case ke ratios nikaalna, sab aapki PDF se.

Ye kanooni salah nahi hai. Output ko kisi qualified vakeel se verify karwayein.
Privacy aur limits: [docs/PRIVACY.md](docs/PRIVACY.md).

## Structure

- `desk/`: software (Python). `sthamly_desk.py` entry point hai.
- `scripts/licenses.py`: license banane, revoke karne aur list karne ka tool (sirf owner ke computer par).
- `db/schema.sql`: Supabase license tables aur verify function.
- `tests/`: desk ke tests.
- `app/`, `components/`: download aur info wali website (Next.js).

## Software chalana (developer)

```
pip install -r requirements-desk.txt
# llama-server aur ek GGUF model chahiye (llama.cpp ke release se)
python desk/sthamly_desk.py --check
python desk/sthamly_desk.py --model path/to/model.gguf
```

Developer test (license ke bina, sirf source code se): `STHAMLY_DEV=1 python desk/sthamly_desk.py --model X.gguf --no-license`
Ye exe me kaam nahi karta.

## Tests

```
python -m unittest discover -s tests -v
```

## Server setup (ek baar)

1. Supabase me project banao (dev project se shuru karo).
2. SQL Editor me `db/schema.sql` chalao.
3. `desk/desk_config.json` me `supabase_url` aur `supabase_anon_key` (anon key) bharo.
4. License banao: `SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/licenses.py create --note "naam"`

Service-role key sirf `scripts/licenses.py` ke paas hoti hai. Desk software aur website me ye key kabhi nahi jaati.

## Windows installer (release)

```
git tag v0.1.0
git push --tags
```

Tag push hote hi GitHub Actions `SthamlyDesk.exe` banata hai aur draft release banata hai.

## Website

```
npm install
npm run typecheck
npm run build
```

Download link ke liye `NEXT_PUBLIC_DESK_WIN_URL` set karo (`.env.example` dekho).

## Abhi nahi hai

- Server-signed license token (abhi local cache hai).
- Scanned PDF (OCR).
- Mac installer.
- Finance aur student desk.

## License

Code: [LICENSE](LICENSE) dekho. Product ke liye license ka faisla abhi baaki hai.
