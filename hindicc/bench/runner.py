"""Benchmark runner for hindicc. Wraps llama.cpp's `llama-bench`.

For one GGUF model on one device it records:
  - prefill tokens/sec (pp) and decode tokens/sec (tg), with stddev from llama-bench
  - peak RSS of the benchmark process (from wait4 rusage)
  - the conditions: device, binary, model hash, threads, token counts

Every number comes from a real run. Nothing is estimated here. Each run writes one
JSON file under results/bench/ so a claim can always be traced back to its source.

Usage (from the hindicc/ root):

    PYTHONPATH=. python bench/runner.py \
        --binary /path/to/llama-bench --model /path/to/model.gguf \
        --threads 4 --prompt 512 --gen 128 --reps 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

RESULTS_DIR = Path("results/bench")
SCHEMA_VERSION = 1
MB = 1024 * 1024


def device_info() -> dict:
    """Best-effort description of the machine. Missing fields are left out."""
    info: dict = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            # Raspberry Pi prints "Model", many ARM SoCs print "model name".
            if line.startswith(("model name", "Model")):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                info["ram_total_mb"] = int(line.split()[1]) // 1024
                break
    except OSError:
        pass
    return info


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def peak_rss_mb(ru_maxrss: int) -> float:
    """ru_maxrss is kilobytes on Linux and bytes on macOS."""
    if sys.platform == "darwin":
        return ru_maxrss / MB
    return ru_maxrss / 1024


def run_llama_bench(
    binary: Path,
    model: Path,
    threads: int,
    prompt_tokens: int,
    gen_tokens: int,
    repetitions: int,
) -> dict:
    """Run llama-bench once and return its exit code, parsed rows, peak RSS and stderr tail.

    wait4 is used instead of communicate() so we get this child's own rusage, not
    the maximum over every child this process has ever run.
    """
    cmd = [
        str(binary),
        "-m", str(model),
        "-t", str(threads),
        "-p", str(prompt_tokens),
        "-n", str(gen_tokens),
        "-r", str(repetitions),
        "-o", "json",
    ]
    with tempfile.TemporaryFile("w+") as out, tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen(cmd, stdout=out, stderr=err)
        _, status, rusage = os.wait4(proc.pid, 0)
        out.seek(0)
        err.seek(0)
        stdout_text = out.read()
        stderr_text = err.read()

    returncode = os.waitstatus_to_exitcode(status)
    rows: list = []
    parse_error = None
    if returncode == 0:
        try:
            rows = json.loads(stdout_text)
            if not isinstance(rows, list):
                parse_error = "llama-bench JSON was not a list"
                rows = []
        except json.JSONDecodeError as exc:
            parse_error = f"could not parse llama-bench JSON: {exc}"

    return {
        "returncode": returncode,
        "rows": rows,
        "parse_error": parse_error,
        "peak_rss_mb": round(peak_rss_mb(rusage.ru_maxrss), 1),
        "stderr_tail": stderr_text.splitlines()[-20:],
    }


def summarize(rows: list) -> dict:
    """Pick the prefill (pp) and decode (tg) rows out of llama-bench output."""
    prefill = next((r for r in rows if r.get("n_prompt", 0) > 0 and r.get("n_gen", 0) == 0), None)
    decode = next((r for r in rows if r.get("n_gen", 0) > 0 and r.get("n_prompt", 0) == 0), None)

    def pick(row):
        if row is None:
            return None
        return {
            "tokens_per_sec": row.get("avg_ts"),
            "stddev_tokens_per_sec": row.get("stddev_ts"),
            "tokens": row.get("n_prompt") or row.get("n_gen"),
        }

    return {"prefill": pick(prefill), "decode": pick(decode)}


def build_record(args: argparse.Namespace, result: dict) -> dict:
    model_size = args.model.stat().st_size
    summary = summarize(result["rows"]) if result["returncode"] == 0 else {"prefill": None, "decode": None}
    return {
        "schema": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": device_info(),
        "binary": str(args.binary),
        "model": {
            "path": str(args.model),
            "size_bytes": model_size,
            "sha256": sha256_of(args.model),
        },
        "params": {
            "threads": args.threads,
            "prompt_tokens": args.prompt,
            "gen_tokens": args.gen,
            "repetitions": args.reps,
        },
        "returncode": result["returncode"],
        "parse_error": result["parse_error"],
        "peak_rss_mb": result["peak_rss_mb"],
        "prefill": summary["prefill"],
        "decode": summary["decode"],
        "rows": result["rows"],
        "stderr_tail": result["stderr_tail"],
    }


def write_record(record: dict, model: Path, threads: int) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}_{model.stem}_t{threads}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description="llama-bench wrapper for hindicc")
    p.add_argument("--binary", type=Path, required=True, help="path to llama-bench")
    p.add_argument("--model", type=Path, required=True, help="path to a GGUF file")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--prompt", type=int, default=512, help="prefill token count")
    p.add_argument("--gen", type=int, default=128, help="decode token count")
    p.add_argument("--reps", type=int, default=5, help="repetitions for error bars")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.binary.exists():
        print(f"error: binary not found: {args.binary}", file=sys.stderr)
        return 2
    if not args.model.exists():
        print(f"error: model not found: {args.model}", file=sys.stderr)
        return 2
    for name in ("threads", "prompt", "gen", "reps"):
        if getattr(args, name) <= 0:
            print(f"error: --{name} must be positive", file=sys.stderr)
            return 2

    result = run_llama_bench(
        args.binary, args.model, args.threads, args.prompt, args.gen, args.reps
    )
    record = build_record(args, result)
    path = write_record(record, args.model, args.threads)

    print(f"Record written: {path}")
    print(f"returncode={record['returncode']} peak_rss={record['peak_rss_mb']} MB")
    if record["prefill"]:
        p = record["prefill"]
        print(f"prefill: {p['tokens_per_sec']} tok/s (stddev {p['stddev_tokens_per_sec']})")
    if record["decode"]:
        d = record["decode"]
        print(f"decode:  {d['tokens_per_sec']} tok/s (stddev {d['stddev_tokens_per_sec']})")
    if record["parse_error"]:
        print(f"warning: {record['parse_error']}", file=sys.stderr)
    return 0 if record["returncode"] == 0 and not record["parse_error"] else 1


if __name__ == "__main__":
    sys.exit(main())