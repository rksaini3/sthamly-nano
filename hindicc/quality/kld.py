"""KLD quality measurement for hindicc.

KLD (mean KL divergence) and same-top-p measure how far a quantized model's
next-token distribution is from its FP16 original. Lower KLD and higher
same-top-p are better. This is a proxy: it does not measure task accuracy.

The regexes and the "clear difference" rule are the same as NanoBrain's
scripts/trl4_bench.py and lib/verdict.ts, so numbers stay comparable across
the two repos.

Verdict rule: a gain is "clear" only when it is more than 2x the combined
error (errors added in quadrature). Smaller positive gains are only a "hint".
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Same patterns as NanoBrain's scripts/trl4_bench.py (the "±" sign is matched by
# the non-digit character class, so both ASCII and Unicode output work).
PATTERNS = {
    "kld": r"Mean\s+KLD:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "rms": r"RMS\s+\S+\s*:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
    "top": r"Same top p:\s*([-\d.]+)\s*[^\d\s.-]{1,3}\s*([\d.]+)",
}

LOW_IS_BETTER = {"kld", "rms"}
CLEAR_FACTOR = 2.0


@dataclass(frozen=True)
class Measurement:
    """A value with its reported error (the +/- number)."""

    mean: float
    err: float


@dataclass(frozen=True)
class Comparison:
    """How much variant `a` beats variant `b` on one metric."""

    metric: str
    gain: float  # positive means a is better
    err: float
    verdict: str  # "clear", "hint", or "none"


class KldParseError(ValueError):
    """Raised when llama-perplexity output does not contain the expected numbers."""


def parse_kld_output(text: str) -> dict[str, Measurement]:
    """Extract KLD, RMS and same-top-p from llama-perplexity output.

    KLD is required. RMS and same-top-p are optional, because some builds omit them.
    """
    found: dict[str, Measurement] = {}
    for key, pattern in PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            found[key] = Measurement(float(match.group(1)), float(match.group(2)))
    if "kld" not in found:
        raise KldParseError("no 'Mean KLD' line found in llama-perplexity output")
    return found


def gain_and_error(a: Measurement, b: Measurement, metric: str) -> tuple[float, float]:
    """Positive gain means a is better than b.

    The two reported errors are treated as independent (conservative).
    """
    gain = (b.mean - a.mean) if metric in LOW_IS_BETTER else (a.mean - b.mean)
    err = math.sqrt(a.err**2 + b.err**2)
    return gain, err


def verdict(gain: float, err: float) -> str:
    if gain > CLEAR_FACTOR * err:
        return "clear"
    if gain > 0:
        return "hint"
    return "none"


def compare(a: dict[str, Measurement], b: dict[str, Measurement], metric: str = "kld") -> Comparison:
    if metric not in a or metric not in b:
        raise KldParseError(f"metric {metric!r} missing from one of the measurements")
    gain, err = gain_and_error(a[metric], b[metric], metric)
    return Comparison(metric=metric, gain=gain, err=err, verdict=verdict(gain, err))


def run_kld(
    perplexity_bin: Path,
    fp16_model: Path,
    variant_model: Path,
    base_file: Path,
    text_file: Path,
    *,
    chunks: int,
    ctx: int,
    threads: int,
) -> dict[str, Measurement]:
    """Measure one quantized variant against its FP16 original.

    Two llama-perplexity runs, same as NanoBrain:
      1. FP16 model writes its logits to base_file (--kl-divergence-base).
      2. Variant reads base_file and reports KLD against it (--kl-divergence).
    Raises RuntimeError if a run fails, KldParseError if the output has no KLD.
    """
    common = [
        "-f", str(text_file),
        "--chunks", str(chunks),
        "-c", str(ctx),
        "-t", str(threads),
    ]

    base_file.parent.mkdir(parents=True, exist_ok=True)
    if not base_file.exists():
        base_cmd = [
            str(perplexity_bin), "-m", str(fp16_model),
            "--kl-divergence-base", str(base_file), *common,
        ]
        base = subprocess.run(base_cmd, capture_output=True, text=True)
        if base.returncode != 0 or not base_file.exists():
            raise RuntimeError(f"FP16 base run failed (exit {base.returncode}): {base.stderr[-500:]}")

    variant_cmd = [
        str(perplexity_bin), "-m", str(variant_model),
        "--kl-divergence-base", str(base_file), "--kl-divergence", *common,
    ]
    run = subprocess.run(variant_cmd, capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError(f"variant run failed (exit {run.returncode}): {run.stderr[-500:]}")
    return parse_kld_output(run.stdout + "\n" + run.stderr)