#!/usr/bin/env bash
# Runs the self-checking testbench. Run from the repo root:
#     bash hardware/testbench/run_verilator.sh
# Uses Verilator 5+ if installed, otherwise Icarus Verilog (iverilog).
set -e

DESIGN=hardware/verilog/sthamly_top.v
TB=hardware/testbench/tb_top.v
BUILD=hardware/testbench/build

if [ ! -f "$DESIGN" ]; then
  echo "Missing $DESIGN. Generate it first (verilog_writer.py)."
  exit 1
fi

mkdir -p "$BUILD"

if command -v verilator >/dev/null 2>&1; then
  echo "== Verilator =="
  verilator --binary --timing -Wno-fatal --top-module tb_top \
    -Mdir "$BUILD/obj_dir" "$DESIGN" "$TB"
  "$BUILD/obj_dir/Vtb_top"
elif command -v iverilog >/dev/null 2>&1; then
  echo "== Icarus Verilog =="
  iverilog -g2012 -o "$BUILD/sim.out" "$DESIGN" "$TB"
  vvp "$BUILD/sim.out"
else
  echo "Neither verilator nor iverilog found."
  echo "Install one:  sudo apt-get install -y iverilog     (easiest)"
  echo "          or: sudo apt-get install -y verilator    (needs version 5+)"
  exit 1
fi