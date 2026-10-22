#!/usr/bin/env bash
# One-time setup: Python venv + llama.cpp clone + build.
#
# llama.cpp output format matters: scripts/audit.py and scripts/trl4_bench.py parse
# llama-perplexity's text output (PATTERNS). After changing LLAMA_CPP_REF, re-run a
# known small model and check that the KLD line still parses.
set -euo pipefail
cd "$(dirname "$0")/.."

# Pin to a tested llama.cpp commit. Update only after verifying audit still parses.
LLAMA_CPP_REF="${LLAMA_CPP_REF:-REPLACE_WITH_TESTED_COMMIT}"
# GPU build on RunPod: set USE_CUDA=1 before running.
USE_CUDA="${USE_CUDA:-0}"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -r requirements.txt
pip install -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt 2>/dev/null || true

if [ ! -d llama.cpp ]; then
  git clone https://github.com/ggerganov/llama.cpp
fi
(cd llama.cpp && git fetch --quiet && git checkout --quiet "$LLAMA_CPP_REF")

# Requirements for the converter come from the pinned checkout, so install after checkout.
pip install -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt

CMAKE_FLAGS=(-DLLAMA_CURL=OFF)
if [ "$USE_CUDA" = "1" ]; then
  CMAKE_FLAGS+=(-DGGML_CUDA=ON)
fi

cd llama.cpp
rm -rf build
cmake -B build "${CMAKE_FLAGS[@]}"
cmake --build build --config Release -j"$(nproc)"
cd ..

# Verify the binaries the pipeline needs. Fail loudly instead of later.
for bin in llama-quantize llama-imatrix llama-perplexity; do
  if [ ! -x "llama.cpp/build/bin/$bin" ]; then
    echo "ERROR: llama.cpp/build/bin/$bin missing after build." >&2
    exit 1
  fi
done

echo "Setup done."
echo "Converter: llama.cpp/convert_hf_to_gguf.py"
echo "Activate Python env in each new shell: source .venv/bin/activate"