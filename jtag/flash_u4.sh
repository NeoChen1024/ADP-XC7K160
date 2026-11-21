#!/usr/bin/env sh
# Writes a bitstream into the configuration flash U4, so that the FPGA loads it at power-on.
#
#   [VIVADO=/path/to/vivado] [XVC=host:port | XVCD=/path/to/xvcd-pico] sh flash_u4.sh design.bit
#
# Run it only when the owner explicitly asks for this write (AGENTS.md). README section 5 says what
# U4 holds, and how a backup is read and restored.
#
# The script makes an MCS file of the bitstream at address 0 (BPI x16, 128 MiB part), and refuses a
# file that goes above 0x4000000: the upper half of U4 is out of reach (FA25, README section 5).
# Vivado then erases only the blocks of the file, writes them, verifies them, and reconfigures the
# FPGA from U4. The MCS file and the logs (flash_u4.log, flash_u4.out, xvcd.log) go beside the
# bitstream. The cable is the same as for load.sh.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
if [ $# -ne 1 ] || [ ! -f "$1" ]; then
    echo "usage: sh flash_u4.sh design.bit" >&2
    exit 2
fi
bit="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
vivado="${VIVADO:-vivado}"
cd "$(dirname "$bit")"
name="$(basename "$bit" .bit)"

printf 'write_cfgmem -force -format mcs -size 128 -interface BPIx16 -loadbit {up 0x00000000 %s} -file %s\n' \
    "$bit" "$name.mcs" >flash_u4_mcs.tcl
"$vivado" -mode batch -nojournal -nolog -source flash_u4_mcs.tcl >flash_u4_mcs.out 2>&1 </dev/null
# The .prm file gives the range as "0x00000000    0x<end>    <date>    <file>".
end="$(awk '/^0x00000000 +0x/ { print $2; exit }' "$name.prm")"
[ -n "$end" ] || { echo "no address range in $name.prm" >&2; exit 1; }
if [ $((end)) -ge $((0x4000000)) ]; then
    echo "the file ends at $end, above the lower half of U4" >&2
    exit 1
fi
echo "writing $name.mcs into U4: 0x00000000 to $end"

daemon=""
if [ -z "${XVC:-}" ]; then
    "${XVCD:-xvcd-pico}" >xvcd.log 2>&1 &
    daemon=$!
    sleep 1
    kill -0 "$daemon" 2>/dev/null || { echo "the XVC server did not start; see $(pwd)/xvcd.log" >&2; exit 1; }
fi
status=0
"$vivado" -mode batch -nojournal -log flash_u4.log -source "$here/flash_u4.tcl" \
    -tclargs "$name.mcs" "$name.prm" "${XVC:-localhost:2542}" >flash_u4.out 2>&1 </dev/null || status=$?
if [ -n "$daemon" ]; then
    kill "$daemon" 2>/dev/null || true
    wait "$daemon" 2>/dev/null || true
fi
# Vivado leaves its servers running.
pkill -u "$(id -u)" -x hw_server 2>/dev/null || true
pkill -u "$(id -u)" -x cs_server 2>/dev/null || true
if [ "$status" -ne 0 ] || ! grep -q '^U4_PROGRAMMED_AND_VERIFIED' flash_u4.out; then
    echo "the write of U4 failed; see $(pwd)/flash_u4.log. JTAG can still load a design (load.sh)." >&2
    exit 1
fi
grep '^DONE=' flash_u4.out
