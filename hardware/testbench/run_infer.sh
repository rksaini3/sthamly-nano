#!/usr/bin/env bash
# Runs the inference testbench (sthamly_infer.v vs the Python integer model).
# Run from the repo root, after `python engine/run_infer.py`:
#     bash hardware/testbench/run_infer.sh
# Uses Verilator 5+ if installed (fast), otherwise Icarus Verilog (iverilog).
set -e

DESIGN=hardware/verilog/sthamly_infer.v
TB=hardware/testbench/tb_infer.v
DATA=hardware/verilog/data
BUILD=hardware/testbench/build_infer
LOG="$BUILD/sim.log"

for f in "$DESIGN" "$TB" "$DATA/manifest.json"; do
  if [ ! -f "$f" ]; then
    echo "Missing $f"
    echo "Generate it first:  python engine/run_infer.py"
    exit 1
  fi
done

mkdir -p "$BUILD"

use_verilator=0
if command -v verilator >/dev/null 2>&1; then
  major=$(verilator --version | grep -oE '[0-9]+' | head -1)
  if [ "${major:-0}" -ge 5 ]; then
    use_verilator=1
  fi
fi

if [ "$use_verilator" = "1" ]; then
  echo "== Verilator =="
  if ! verilator --binary --timing -Wno-fatal --top-module tb_infer \
        -Mdir "$BUILD/obj_dir" "$DESIGN" "$TB" > "$BUILD/compile.log" 2>&1; then
    cat "$BUILD/compile.log"
    echo "Verilator compile failed (log above)."
    exit 1
  fi
  "$BUILD/obj_dir/Vtb_infer" | tee "$LOG"
elif command -v iverilog >/dev/null 2>&1; then
  echo "== Icarus Verilog =="
  if ! iverilog -g2012 -o "$BUILD/sim.out" "$DESIGN" "$TB" 2> "$BUILD/compile.log"; then
    cat "$BUILD/compile.log"
    echo "iverilog compile failed (log above)."
    exit 1
  fi
  vvp "$BUILD/sim.out" | tee "$LOG"
else
  echo "Neither Verilator 5+ nor iverilog found."
  echo "Install the easy one:  sudo apt-get install -y iverilog"
  exit 1
fi

echo
if grep -q '^PASS' "$LOG"; then
  echo "RESULT: PASS (Verilog matches the Python integer model)"
else
  echo "RESULT: FAIL (see messages above)"
  exit 1
fi