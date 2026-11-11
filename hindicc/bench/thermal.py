"""Thermal run for hindicc. Repeats llama-bench for a fixed time and records
how decode speed changes as the device heats up.

A single benchmark can look fast because the device has not heated up yet.
This run shows the drift: the median of the first few runs versus the median
of the last few runs. Nothing here decides pass or fail; it only records numbers.

For each iteration it records:
  - decode tokens/sec (from runner.run_llama_bench)
  - SoC temperature before the run (Linux thermal zone, if present)
  - firmware throttle flags from `vcgencmd get_throttled` (Raspberry Pi, if present)

Usage (from the hindicc/ root):

    PYTHONPATH=. python bench/thermal.py \
        --binary /path/to/llama-bench --model /path/to/model.gguf \
        --threads 4 --gen 128 --minutes 10
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from bench.runner import device_info, run_llama_bench, sha256_of

RESULTS_DIR = Path("results/thermal")
SCHEMA_VERSION = 1
DEFAULT_TEMP_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
EDGE_RUNS = 3  # how many runs at each end to compare


def read_temp_c(path: Path) -> float | None:
    """Linux thermal zones report millidegrees Celsius. Returns None if unavailable."""
    try:
        return round(int(path.read_text().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_throttle_flags() -> str | None:
    """Raw output of `vcgencmd get_throttled`, or None if the tool is not installed."""
    if shutil.which("vcgencmd") is None:
        return None
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def drift_summary(decode_rates: list[float]) -> dict:
    """Compare the start and end of the run. Needs at least 2 * EDGE_RUNS samples."""
    if len(decode_rates) < 2 * EDGE_RUNS:
        return {"enough_samples": False, "samples": len(decode_rates)}
    start = statistics.median(decode_rates[:EDGE_RUNS])
    end = statistics.median(decode_rates[-EDGE_RUNS:])
    change_pct = (end - start) / start * 100 if start else None
    return {
        "enough_samples": True,
        "samples": len(decode_rates),
        "start_median_tps": round(start, 3),
        "end_median_tps": round(end, 3),
        "change_percent": round(change_pct, 2) if change_pct is not None else None,
    }


def run_thermal(args: argparse.Namespace) -> dict:
    model_sha = sha256_of(args.model)
    deadline = time.monotonic() + args.minutes * 60
    samples: list[dict] = []
    decode_rates: list[float] = []
    stop_reason = "time limit reached"

    while time.monotonic() < deadline:
        temp_before = read_temp_c(args.temp_path)
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = run_llama_bench(
            args.binary, args.model, args.threads, args.prompt, args.gen, 1
        )
        temp_after = read_temp_c(args.temp_path)

        sample = {
            "started_utc": started,
            "temp_before_c": temp_before,
            "temp_after_c": temp_after,
            "returncode": result["returncode"],
            "parse_error": result["parse_error"],
            "decode_tps": None,
        }
        if result["returncode"] != 0 or result["parse_error"]:
            samples.append(sample)
            stop_reason = "llama-bench failed; see samples"
            break

        decode_row = next(
            (r for r in result["rows"] if r.get("n_gen", 0) > 0 and r.get("n_prompt", 0) == 0),
            None,
        )
        if decode_row is None or decode_row.get("avg_ts") is None:
            sample["parse_error"] = "no decode row in llama-bench output"
            samples.append(sample)
            stop_reason = "no decode row; see samples"
            break

        sample["decode_tps"] = decode_row["avg_ts"]
        sample["throttle_flags"] = read_throttle_flags()
        samples.append(sample)
        decode_rates.append(decode_row["avg_ts"])

    return {
        "schema": SCHEMA_VERSION,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": device_info(),
        "binary": str(args.binary),
        "model": {"path": str(args.model), "sha256": model_sha},
        "params": {
            "threads": args.threads,
            "prompt_tokens": args.prompt,
            "gen_tokens": args.gen,
            "minutes": args.minutes,
            "temp_path": str(args.temp_path),
        },
        "stop_reason": stop_reason,
        "samples": samples,
        "drift": drift_summary(decode_rates),
    }


def write_record(record: dict, model: Path, threads: int) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / f"{stamp}_{model.stem}_t{threads}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thermal", description="thermal drift run for hindicc")
    p.add_argument("--binary", type=Path, required=True, help="path to llama-bench")
    p.add_argument("--model", type=Path, required=True, help="path to a GGUF file")
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--prompt", type=int, default=512, help="prefill tokens per run")
    p.add_argument("--gen", type=int, default=128, help="decode tokens per run")
    p.add_argument("--minutes", type=float, default=10.0, help="how long to keep running")
    p.add_argument("--temp-path", type=Path, default=DEFAULT_TEMP_PATH)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.binary.exists():
        print(f"error: binary not found: {args.binary}", file=sys.stderr)
        return 2
    if not args.model.exists():
        print(f"error: model not found: {args.model}", file=sys.stderr)
        return 2
    for name in ("threads", "prompt", "gen"):
        if getattr(args, name) <= 0:
            print(f"error: --{name} must be positive", file=sys.stderr)
            return 2
    if args.minutes <= 0:
        print("error: --minutes must be positive", file=sys.stderr)
        return 2

    record = run_thermal(args)
    path = write_record(record, args.model, args.threads)

    drift = record["drift"]
    print(f"Record written: {path}")
    print(f"samples={len(record['samples'])} stop_reason={record['stop_reason']}")
    if drift["enough_samples"]:
        print(
            f"decode start median {drift['start_median_tps']} tok/s, "
            f"end median {drift['end_median_tps']} tok/s, change {drift['change_percent']}%"
        )
    else:
        print(f"not enough samples for drift ({drift['samples']} collected, need {2 * EDGE_RUNS})")
    return 0 if record["stop_reason"] == "time limit reached" else 1


if __name__ == "__main__":
    sys.exit(main())