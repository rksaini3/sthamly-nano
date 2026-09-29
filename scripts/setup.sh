#!/usr/bin/env bash
# One-time setup: Python venv + llama.cpp clone + build
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
cmake -B build
cmake --build build --config Release -j
echo "Setup done. Activate: source .venv/bin/activate"