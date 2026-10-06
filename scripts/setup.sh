#!/usr/bin/env bash
# One-time setup: Python venv + llama.cpp clone + build.
# llama.cpp changes often. If a later step breaks after an update, the bench scripts
# parse llama-perplexity's text output (scripts/trl4_bench.py: PATS), so check that first.
set -e
cd "$(dirname "$0")/.."

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

if [ ! -d llama.cpp ]; then
  git clone https://github.com/ggerganov/llama.cpp
fi
pip install -r llama.cpp/requirements/requirements-convert_hf_to_gguf.txt

cd llama.cpp
# LLAMA_CURL=OFF: we never download models through llama.cpp, and without libcurl
# headers installed the default configure fails on recent versions.
cmake -B build -DLLAMA_CURL=OFF
cmake --build build --config Release -j
echo "Setup done. Activate: source .venv/bin/activate"