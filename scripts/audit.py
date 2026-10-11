"""Hindi Quant Audit: ek command me kisi bhi GGUF quant(s) ko Hindi text par FP16 ke muqable naapo.

Usage (repo root, llama.cpp built with `bash scripts/setup.sh`):

    python scripts/audit.py --name "Qwen2.5-1.5B-Instruct" \\
        --fp16  models/gguf/Qwen__Qwen2.5-1.5B-Instruct-f16.gguf \\
        --quant plain=models/gguf/Qwen__Qwen2.5-1.5B-Instruct-Q4_K_M-standard.gguf \\
        --quant community=/path/to/someones-Q4_K_M.gguf

Output: reports/<name>/report.json, report.md, report.html (open the html in a browser and
"Print -> Save as PDF" for the customer PDF).

Fair-audit rules (the report repeats them):
  * The FP16 file and every --quant file must be the SAME base model (same vocabulary).
  * Numbers are KLD / same-top-p against that FP16 (a proxy), not Hindi task accuracy.
  * A difference only counts as "clear" when it is more than 2x its combined error.

Resumable: re-running skips quants already measured (same file size, same settings).
Optional: --upload (with --hf-repo owner/repo) adds/merges the result into the Supabase
`models` table so it appears on the website leaderboard. Needs SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY in the environment. Nothing is uploaded without --upload.
"""
import argparse
import hashlib
import html
import json
import math
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))  # lets `import hindi_eval_data` work however this file is started

# llama-perplexity (KL-divergence mode) prints e.g. "Mean    KLD:   0.050000 ±  0.002000".
# Same patterns as scripts/trl4_bench.py, which produced the published numbers.
PATS = {
    "kld": r"Mean\s+KLD:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "rms": r"RMS\s+\S+\s*:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "top": r"Same top p:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
}
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")


def die(msg):
    print("ROKA:", msg)
    sys.exit(1)


# ---------------------------------------------------------------- pure helpers
def parse(out):
    d = {}
    for k, p in PATS.items():
        m = re.search(p, out)
        if m:
            d[k] = [float(m.group(1)), float(m.group(2))]
    return d


def gain(a, b, key="kld"):
    """How much better a is than b (positive = a better) and the combined error."""
    low_is_better = key in ("kld", "rms")
    g = (b[key][0] - a[key][0]) if low_is_better else (a[key][0] - b[key][0])
    return g, math.sqrt(a[key][1] ** 2 + b[key][1] ** 2)


def verdict(g, err):
    if g > 2 * err:
        return "SAAF FARK"
    if g > 0:
        return "ishara, saaf nahi"
    return "fark nahi / ulta"


def slugify(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower() or "model"


def parse_quants(items):
    """['label=path', ...] -> [(label, Path)], with validation."""
    out, seen = [], set()
    for it in items:
        if "=" not in it:
            die(f"--quant ko label=path format me do (mila: {it!r})")
        label, path = it.split("=", 1)
        label = label.strip()
        if not LABEL_RE.match(label):
            die(f"label {label!r} theek nahi: sirf letters, digits, . _ - (max 40, shuru letter/digit se)")
        if label in seen:
            die(f"label {label!r} do baar diya")
        p = Path(path.strip()).expanduser()
        if not p.is_file():
            die(f"{p} nahi mili")
        seen.add(label)
        out.append((label, p))
    return out


def rank(results):
    """Rank quants measured on EVERY test set by mean KLD (lower = better)."""
    doms = list(results)
    if not doms:
        return []
    labels = [l for l in results[doms[0]] if all(l in results[d] for d in doms)]
    n = len(doms)
    rows = []
    for l in labels:
        rows.append({
            "label": l,
            "kld": sum(results[d][l]["kld"][0] for d in doms) / n,
            "kld_err": math.sqrt(sum(results[d][l]["kld"][1] ** 2 for d in doms)) / n,
            "top": sum(results[d][l]["top"][0] for d in doms) / n,
        })
    rows.sort(key=lambda r: r["kld"])
    for i, r in enumerate(rows):
        r["is_best"] = i == 0
        if i == 0:
            r["behind"] = 0
        else:
            best = rows[0]["label"]
            r["behind"] = sum(
                1 for d in doms
                if (lambda g, e: g > 2 * e)(*gain(results[d][best], results[d][r["label"]]))
            )
    return rows


def standing(row, n_domains):
    if row["is_best"]:
        return "Best (is model me)"
    if row["behind"] == n_domains:
        return f"Best se saaf peeche ({row['behind']}/{n_domains} test sets)"
    if row["behind"] > 0:
        return f"Kuch me peeche ({row['behind']}/{n_domains} test sets)"
    return "Best se alag nahi (error ke andar)"


# ---------------------------------------------------------------- report building
def _f(m, d):
    return f"{m[0]:.{d}f} ± {m[1]:.{d}f}"


def report_parts(rep):
    """Single source for both the .md and .html report: (paragraph lists, tables)."""
    res, meta = rep["results"], rep["variants_meta"]
    doms = list(res)
    n_dom = len(doms)
    rows = rank(res)

    intro = [
        f"Model: {rep['name']}" + (f" (base: {rep['base_model']})" if rep.get("base_model") else ""),
        f"Date: {rep['date']}",
        f"FP16 reference: {rep['fp16']['file']} ({rep['fp16']['size_gb']} GB)",
        "Test sets: " + ", ".join(f"{d} ({rep['domains'][d]} lines)" for d in doms) + f"; {rep['settings']['chunks']} chunks each",
        f"llama.cpp: {rep['llama_cpp'] or 'version pata nahi'}",
    ]
    tables = []
    if rows:
        head = ["#", "Quant", "Size", "Mean KLD (kam = behtar)"] + [f"KLD {d}" for d in doms] + ["Same top-p %", "Best ke muqable"]
        body = []
        for i, r in enumerate(rows, 1):
            sz = meta.get(r["label"], {}).get("size_gb")
            body.append([str(i), r["label"], f"{sz} GB" if sz else "-", f"{r['kld']:.4f} ± {r['kld_err']:.4f}"]
                        + [_f(res[d][r["label"]]["kld"], 4) for d in doms] + [f"{r['top']:.2f}", standing(r, n_dom)])
        tables.append(("Ranking (mean KLD, kam = behtar)", head, body))
    for d in doms:
        head = ["Quant", "KLD ±", "RMS dp % ±", "Same top-p % ±"]
        body = [[l, _f(v["kld"], 4), _f(v["rms"], 2), _f(v["top"], 2)] for l, v in res[d].items()]
        tables.append((f"Test set: {d}", head, body))

    how = [
        "KLD: quantized model ka output distribution FP16 se kitna door hai. Kam = FP16 ke kareeb.",
        "Same top-p: kitni baar quantized model ka sabse sambhavit token FP16 jaisa hi hai. Zyada = behtar.",
        "\"Saaf\" fark: do quants ke KLD ka fark unke combined error ke 2 guna se bada ho. Isse chhota fark sirf ishara hai, saboot nahi.",
    ]
    total_lines = sum(rep["domains"].values())
    limits = [
        "Ye proxy naap hai (FP16 se distribution ka fark). Hindi sawal-jawab, summary ya translation par accuracy nahi naapi gayi.",
        f"Test chhote hain ({total_lines} lines, {n_dom} test sets) aur Hindi Wikipedia / sangraha jaisa text hai. "
        "Hinglish ya aapke domain ke text par natija alag ho sakta hai.",
        "Naap sirf in files par hai: " + ", ".join(rep["variants_meta"]) + ", ek hi FP16 reference ke saath. Community files badal sakti hain.",
        "Ye report quality ki guarantee nahi hai.",
    ]
    return intro, tables, how, limits


def build_markdown(rep):
    intro, tables, how, limits = report_parts(rep)
    L = [f"# Hindi Quant Audit: {rep['name']}", ""]
    L += [f"- {x}" for x in intro] + [""]
    for title, head, body in tables:
        L += [f"## {title}", "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
        L += ["| " + " | ".join(r) + " |" for r in body] + [""]
    L += ["## Kaise padhein", ""] + [f"- {x}" for x in how] + [""]
    L += ["## Seemaayein (zaroor padhein)", ""] + [f"- {x}" for x in limits] + [""]
    return "\n".join(L)


def build_html(rep):
    intro, tables, how, limits = report_parts(rep)
    e = html.escape

    def ul(items):
        return "<ul>" + "".join(f"<li>{e(x)}</li>" for x in items) + "</ul>"

    parts = [f"<h1>Hindi Quant Audit: {e(rep['name'])}</h1>", ul(intro)]
    for title, head, body in tables:
        parts.append(f"<h2>{e(title)}</h2><table><thead><tr>" + "".join(f"<th>{e(h)}</th>" for h in head) + "</tr></thead><tbody>")
        parts += ["<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in body]
        parts.append("</tbody></table>")
    parts += ["<h2>Kaise padhein</h2>", ul(how), "<h2>Seemaayein (zaroor padhein)</h2>", ul(limits)]
    css = ("body{font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#111;background:#fff}"
           "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem;font-size:13px}"
           "th,td{border:1px solid #ccc;padding:6px 8px;text-align:left}th{background:#f3f3f3}"
           "@media(prefers-color-scheme:dark){body{color:#eee;background:#111}th{background:#222}th,td{border-color:#444}}"
           "@media print{body{color:#000;background:#fff}}")
    return (f"<!doctype html><html lang=\"hi\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Hindi Quant Audit: {e(rep['name'])}</title><style>{css}</style></head><body>"
            + "".join(parts) + "</body></html>")


# ---------------------------------------------------------------- measuring
def run(cmd, timeout=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, r.stdout + r.stderr


def llama_version(binary):
    try:
        rc, out = run([binary, "--version"], timeout=30)
    except Exception:
        return ""
    for line in out.splitlines():
        if "version" in line.lower():
            return line.strip()[:120]
    return ""


def kld_base_path(data_dir, fp16, dom, eval_path, chunks):
    h = hashlib.sha1()
    h.update(f"{fp16.resolve()}|{fp16.stat().st_size}|{chunks}|".encode())
    h.update(Path(eval_path).read_bytes())
    return Path(data_dir) / f"audit_{h.hexdigest()[:10]}_{dom}.kld"


def measure_all(rep, bin_dir, data_dir, fp16, quants, doms, threads):
    perplexity = Path(bin_dir) / "llama-perplexity"
    chunks = rep["settings"]["chunks"]
    for dom, (eval_path, _n) in doms.items():
        K = kld_base_path(data_dir, fp16, dom, eval_path, chunks)
        common = ["-f", eval_path, "-t", threads, "--chunks", chunks]
        todo = [(l, p) for l, p in quants if l not in rep["results"].get(dom, {})]
        if not todo:
            continue
        if not K.exists():
            print(f"[{dom}] FP16 base bana raha hu...", flush=True)
            rc, out = run([perplexity, "-m", fp16, "--kl-divergence-base", K] + common)
            if not K.exists():
                print(out[-1500:])
                die(f"[{dom}] FP16 base nahi bana (FP16 file / llama.cpp check karo)")
        for label, path in todo:
            print(f"[{dom}] {label} naap raha hu...", flush=True)
            rc, out = run([perplexity, "-m", path, "--kl-divergence-base", K, "--kl-divergence"] + common)
            d = parse(out)
            if len(d) < 3:
                print(out[-1800:])
                die(f"[{dom}] {label}: number padh nahi paya. Kya ye quant isi FP16 ke base model ka hai?")
            rep["results"].setdefault(dom, {})[label] = d
            yield  # caller saves report.json after every measurement


# ---------------------------------------------------------------- upload (optional)
def upload(rep, hf_repo, quant_type, base_model):
    sbu = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    sbk = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not (sbu and sbk):
        die("--upload ke liye SUPABASE_URL aur SUPABASE_SERVICE_ROLE_KEY environment me chahiye.")
    H = {"apikey": sbk, "Authorization": f"Bearer {sbk}", "Content-Type": "application/json", "Prefer": "return=minimal"}
    key = urllib.parse.quote(hf_repo, safe="")
    q = urllib.request.Request(f"{sbu}/rest/v1/models?hf_repo=eq.{key}&select=id,results,variants_meta", headers=H)
    existing = json.load(urllib.request.urlopen(q))

    results = {d: dict(v) for d, v in rep["results"].items()}
    vmeta = dict(rep["variants_meta"])
    if existing:  # merge into the existing row: never wipe numbers that are already there
        old = existing[0]
        for d, byv in (old.get("results") or {}).items():
            merged = dict(byv)
            merged.update(results.get(d, {}))
            results[d] = merged
        merged_meta = dict(old.get("variants_meta") or {})
        merged_meta.update(vmeta)
        vmeta = merged_meta

    base = base_model or rep.get("base_model") or None
    row = {"results": results, "variants_meta": vmeta, "fp16_size_gb": rep["fp16"]["size_gb"]}
    if base:
        row["base_model"] = base
    if existing:
        # PATCH only touches numbers + extras; the row's name, notes, download link stay as they were.
        req = urllib.request.Request(f"{sbu}/rest/v1/models?hf_repo=eq.{key}", data=json.dumps(row).encode(), headers=H, method="PATCH")
    else:
        first = next(iter(rep["variants_meta"].values()))
        row.update({"model_name": rep["name"], "hf_repo": hf_repo, "quant_type": quant_type,
                    "size_gb": first.get("size_gb"), "download_url": f"https://huggingface.co/{hf_repo}",
                    "notes": f"Audit: {len(rep['variants_meta'])} quant(s) vs FP16, {rep['settings']['chunks']} chunks. Proxy naap (KLD)."})
        req = urllib.request.Request(f"{sbu}/rest/v1/models", data=json.dumps(row).encode(), headers=H, method="POST")
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        die(f"Supabase {'update' if existing else 'insert'} fail: {e.code} {e.read()[:300]}")
    print("Supabase me", "row update ho gayi (merge)." if existing else "nayi row judi.")


# ---------------------------------------------------------------- main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Hindi Quant Audit (KLD vs FP16)")
    ap.add_argument("--name", required=True, help="model ka naam jo report me dikhega")
    ap.add_argument("--fp16", required=True, help="FP16 GGUF (reference)")
    ap.add_argument("--quant", action="append", required=True, metavar="LABEL=PATH", help="quantized GGUF; kai baar de sakte ho")
    ap.add_argument("--base-model", default="", help="jaise Qwen/Qwen2.5-1.5B-Instruct (report/website ke liye)")
    ap.add_argument("--chunks", type=int, default=12, help="eval chunks per test set (zyada = chhota +/-, slow)")
    ap.add_argument("--threads", type=int, default=2)
    ap.add_argument("--llama", default=str(ROOT / "llama.cpp" / "build" / "bin"), help="llama.cpp bin folder")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--upload", action="store_true", help="result website ke Supabase me bhi daalo")
    ap.add_argument("--hf-repo", default="", help="--upload ke liye: wo HF repo (owner/name) jisme ye files rehti hain")
    ap.add_argument("--quant-type", default="Q4_K_M", help="--upload ke liye label, jaise Q4_K_M")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.upload and not re.fullmatch(r"[\w.-]+/[\w.-]+", args.hf_repo):
        die("--upload ke saath --hf-repo owner/name dena zaroori hai")
    fp16 = Path(args.fp16).expanduser()
    if not fp16.is_file():
        die(f"{fp16} nahi mili")
    quants = parse_quants(args.quant)
    for label, p in quants:
        if p.stat().st_size >= 0.9 * fp16.stat().st_size:
            die(f"{label}: {p.name} FP16 se chhoti nahi lagti. Galat file ya galat FP16?")
    perplexity = Path(args.llama) / "llama-perplexity"
    if not perplexity.exists():
        die(f"{perplexity} nahi mila. Pehle `bash scripts/setup.sh` chalao (ya --llama do).")

    from hindi_eval_data import ensure_eval_sets
    try:
        doms = ensure_eval_sets(args.data_dir)
    except RuntimeError as e:
        die(str(e))

    out_dir = Path(args.out) / slugify(args.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    inputs = {"fp16_bytes": fp16.stat().st_size, "chunks": args.chunks,
              "quants": {l: p.stat().st_size for l, p in quants},
              "domains": {d: v[1] for d, v in doms.items()}}
    rep = {"results": {}}
    if json_path.exists():  # resume only if nothing relevant changed
        try:
            old = json.loads(json_path.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        oi = old.get("inputs", {})
        if (oi.get("fp16_bytes"), oi.get("chunks"), oi.get("domains")) == (inputs["fp16_bytes"], inputs["chunks"], inputs["domains"]):
            # keep a measurement only if that quant file is unchanged (same size) and still requested
            keep = {l for l, b in inputs["quants"].items() if oi.get("quants", {}).get(l) == b}
            rep["results"] = {d: {l: v for l, v in byv.items() if l in keep} for d, byv in old.get("results", {}).items()}
    rep.update({
        "name": args.name, "date": date.today().isoformat(), "base_model": args.base_model,
        "fp16": {"file": fp16.name, "size_gb": round(fp16.stat().st_size / 1e9, 2)},
        "settings": {"chunks": args.chunks, "threads": args.threads},
        "llama_cpp": llama_version(perplexity),
        "domains": {d: v[1] for d, v in doms.items()},
        "variants_meta": {l: {"size_gb": round(p.stat().st_size / 1e9, 2), "source": p.name} for l, p in quants},
        "inputs": inputs,
    })

    for _ in measure_all(rep, args.llama, args.data_dir, fp16, quants, doms, args.threads):
        json_path.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    json_path.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")

    (out_dir / "report.md").write_text(build_markdown(rep), encoding="utf-8")
    (out_dir / "report.html").write_text(build_html(rep), encoding="utf-8")
    print(f"\nReport taiyaar: {out_dir}/report.md, report.html, report.json")
    for r in rank(rep["results"]):
        print(f"  {r['label']:20s} mean KLD {r['kld']:.4f} ± {r['kld_err']:.4f}   {standing(r, len(rep['results']))}")

    if args.upload:
        upload(rep, args.hf_repo, args.quant_type, args.base_model)
    else:
        print("Website ke liye: wahi command --upload --hf-repo owner/name ke saath dobara chalao (kuch dobara naapa nahi jayega).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
