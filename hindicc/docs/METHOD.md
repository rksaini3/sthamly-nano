# Method: what hindicc measures, and how

This document says what each number means, how it is produced, and what it does
not show. Read it before quoting any number from a report.

## 1. Rules for every claim

1. A number is quoted only with its conditions: device, RAM, threads, quantization,
   token counts, llama.cpp commit, model hash, and eval set hash.
2. An estimate is never shown as a measurement. Estimates come from `memory/budget.py`
   and are labeled "ESTIMATE ONLY" in the CLI.
3. A difference is called "clear" only when it is more than 2x the combined error
   (errors added in quadrature). Smaller differences are "hint" at most.
4. Anything not measured is written as "not measured". It is never filled in.
5. The gate blocks shipping. It never approves a claim the numbers do not support.

## 2. Memory budget (estimate)

`memory/budget.py` adds weights, KV cache, activations and a fixed overhead.
Bits per weight, KV element size, live buffer count and overhead are all
ASSUMPTIONs, marked in the code. They must be calibrated against a real GGUF file
size and a measured peak RSS before the estimate is used for any claim.

## 3. Speed (measured)

`bench/runner.py` runs `llama-bench` and records:

- Prefill (pp) and decode (tg) tokens/sec, with the run-to-run stddev from llama-bench.
- Peak RSS of the benchmark process, from `wait4` rusage. Linux reports KB, macOS
  reports bytes. The code converts both to MB.
- Device description from `/proc/cpuinfo` and `/proc/meminfo`, and the model sha256.

A single record is one invocation. Repetitions come from `-r` inside llama-bench.

## 4. Thermal drift (measured)

`bench/thermal.py` repeats the decode benchmark for a fixed time and records:

- Decode tokens/sec per run, and SoC temperature before and after each run.
- Throttle flags from `vcgencmd get_throttled`, when available, stored raw.
- Drift = change between the median of the first 3 runs and the median of the last 3.

Drift is reported as a number. It is not judged. Fewer than 6 runs gives "not measured".

## 5. Quality: KLD (measured)

`quality/kld.py` runs `llama-perplexity` twice: once to write FP16 logits, once to
compare the variant against them. It reads "Mean KLD", optional RMS and same-top-p.

- Lower KLD is better. Higher same-top-p is better.
- The reference for the ship gate is plain Q4, not FP16.
- KLD is a proxy for how far the output distribution moved. It is not task accuracy.

## 6. Task accuracy (measured)

`quality/taskeval.py` scores saved model outputs against labeled Hinglish kirana orders.

- Exact match: the whole order is right, with no extra or missing items.
- Field accuracy: correct name, qty and unit, over all expected fields.
- 95% bootstrap CI (1000 resamples, fixed seed) for both.
- The FP16 baseline is the reference for the task regression check.
- Missing or unparseable output counts as wrong, and is reported as parse failures.

Task accuracy on a small set has a wide interval. The gate needs at least 200 items
before it decides anything.

## 7. The gate (decision rule)

`gate/verdict.py` combines the two checks:

- `not recommended` if KLD is clearly worse than plain Q4, or if task field accuracy
  drops more than 3 percentage points below FP16.
- `incomplete` if any measurement is missing, or the eval set has fewer than 200 items.
- `ship` otherwise.

A "better" KLD is reported but does not by itself allow shipping.

## 8. What is not measured yet

- Task accuracy on a real labeled set. The current labeled set does not exist yet.
- Speed on any device other than the first target device.
- Real-world Hindi QA quality. The KLD proxy and the kirana task are the only checks.
- Power draw and battery life.
- Behaviour under concurrent load.

## 9. Known limits

- llama-bench JSON key names are unverified until checked on a real build (see RUNBOOK step 3).
- Peak RSS is per process. It does not include memory held by other programs.
- Thermal runs measure the device as it is. Cooling and power supply change the result.
- Results apply only to the exact conditions recorded in each file.