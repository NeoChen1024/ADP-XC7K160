#!/usr/bin/env sh
# Runs the workbench's commands in simulation, with Verilator 5: sh run.sh [work directory]. It prints PASS.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
work="${1:-adp-workbench-sim}"
mkdir -p "$work"
python3 "$here/../board.py" "$here/../../pins.csv" "$work" >/dev/null
verilator --binary --timing -Wno-fatal -Wno-WIDTH -Wno-TIMESCALEMOD -Wno-PINMISSING --top-module tb -j 0 -Mdir "$work/obj" \
    "$work/workbench_pkg.sv" "$here/stubs.sv" "$here/../../example-ddr3-test/rtl/uart.sv" \
    "$here/../rtl/adp_xc7k160_workbench.sv" "$here/tb.sv" >"$work/verilate.log" 2>&1 || { tail -20 "$work/verilate.log"; exit 1; }
"$work/obj/Vtb" | grep -v -E '^capture|^- '
