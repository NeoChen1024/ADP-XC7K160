#!/usr/bin/env sh
# Reads one of the two flash devices of the ADP-XC7K160 through JTAG: builds the probe design for it
# if there is none yet, loads it into the FPGA, and runs the probe's dump.py with the arguments given.
#
#   [VIVADO=/path/to/vivado] [XVCD=/path/to/xvcd-pico] [WORK=dir] sh run.sh u17 [out.bin]
#   [VIVADO=/path/to/vivado] [XVCD=/path/to/xvcd-pico] [WORK=dir] sh run.sh u4 probe
#   [VIVADO=/path/to/vivado] [XVCD=/path/to/xvcd-pico] [WORK=dir] sh run.sh u4 out.bin [words in a scan]
#
# u17 is the user SPI flash (2 MiB, about 20 seconds for each of two reads). Without a file, only its
# ID, status and SFDP header are read. u4 is the configuration flash (128 MiB, about 11 minutes);
# "probe" reads a few words at five addresses and writes no file.
#
# Neither probe writes a flash. The U4 design cannot: the flash's write enable is tied high and its
# data pins are inputs. The U17 design passes on whatever dump.py shifts in, and dump.py shifts in
# read commands only. Loading a probe replaces the design in the FPGA until the next load; no flash
# is involved in that either.
#
# The build and the logs go under WORK, ./adp-flash-probes in the current directory by default.
# An image of U4 holds Andes's software: keep it as a backup, and do not publish it.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
case "${1:-}" in
    u17) top=flashjtag ;;
    u4)  top=bpijtag ;;
    *)   echo "usage: sh run.sh u17 [out.bin] | u4 probe | u4 out.bin [words in a scan]" >&2; exit 2 ;;
esac
which="$1"
shift
vivado="${VIVADO:-vivado}"
mkdir -p "${WORK:-adp-flash-probes}/$which"
out="$(cd "${WORK:-adp-flash-probes}/$which" && pwd)"

if [ ! -f "$out/$top.bit" ]; then
    (
        cd "$out"
        nice -n 19 "$vivado" -mode batch -nojournal -log build.log -source "$here/build.tcl" \
            -tclargs xc7k160tfbg676-1 "$top" "$here/$which" >build.out 2>&1 </dev/null || true
    )
    if [ ! -f "$out/$top.bit" ]; then
        echo "Vivado failed; see $out/build.log" >&2
        exit 1
    fi
    # Timing that is not met still makes a bitstream.
    if grep -q 'Timing constraints are not met' "$out/timing.rpt"; then
        rm -f "$out/$top.bit"
        echo "timing is not met; see $out/timing.rpt" >&2
        exit 3
    fi
fi

# The XVC server stays up after the load, for dump.py, and is stopped at the end: it listens on
# every interface.
"${XVCD:-xvcd-pico}" >"$out/xvcd.log" 2>&1 &
daemon=$!
sleep 1
kill -0 "$daemon" 2>/dev/null || { echo "the XVC server did not start; see $out/xvcd.log" >&2; exit 1; }
status=0
VIVADO="$vivado" XVC=localhost:2542 sh "$here/../jtag/load.sh" "$out/$top.bit" || status=$?
if [ "$status" -eq 0 ]; then
    sleep 1
    python3 "$here/$which/dump.py" "$@" || status=$?
fi
kill "$daemon" 2>/dev/null || true
wait "$daemon" 2>/dev/null || true
exit "$status"
