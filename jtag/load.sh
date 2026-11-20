#!/usr/bin/env sh
# Loads a bitstream into the ADP-XC7K160 over JTAG. This configures the FPGA only: it is gone at
# power-off, and no flash is touched.
#
#   [VIVADO=/path/to/vivado] [XVC=host:port | XVCD=/path/to/xvcd-pico] sh load.sh design.bit
#
# The cable is a Xilinx Virtual Cable server. With XVC it is one that is running already; otherwise
# XVCD, xvcd-pico for a Raspberry Pi Pico running xvc-pico, is started for the load and stopped after,
# since it listens on every interface. The logs (load.log, load.out, xvcd.log) go beside the bitstream.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: sh load.sh design.bit" >&2; exit 2; }
bit="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
vivado="${VIVADO:-vivado}"
cd "$(dirname "$bit")"

daemon=""
if [ -z "${XVC:-}" ]; then
    "${XVCD:-xvcd-pico}" >xvcd.log 2>&1 &
    daemon=$!
    sleep 1
    kill -0 "$daemon" 2>/dev/null || { echo "the XVC server did not start; see $(pwd)/xvcd.log" >&2; exit 1; }
fi
status=0
"$vivado" -mode batch -nojournal -log load.log -source "$here/load.tcl" -tclargs "$bit" "${XVC:-localhost:2542}" >load.out 2>&1 </dev/null || status=$?
if [ -n "$daemon" ]; then
    kill "$daemon" 2>/dev/null || true
    wait "$daemon" 2>/dev/null || true
fi
# Vivado leaves its servers running.
pkill -u "$(id -u)" -x hw_server 2>/dev/null || true
pkill -u "$(id -u)" -x cs_server 2>/dev/null || true
if [ "$status" -ne 0 ] || ! grep -q '^DONE=1' load.out; then
    echo "load failed; see $(pwd)/load.log" >&2
    exit 1
fi
