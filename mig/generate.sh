#!/usr/bin/env sh
# Generates MIG for the ADP-XC7K160's DDR3 at one rate, and prints where its sources are.
#
#   [VIVADO=/path/to/vivado] sh generate.sh [rate] [directory]
#
# The rate is one that mig_prj.py knows, 1600 if none is given; the directory, mig-<rate> here by
# default, must be one that can be written, which this one may not be. It takes a few minutes, and is
# skipped when the directory already holds MIG. What it prints is MIG's user_design: read rtl/*/*.v,
# rtl/mig.v and rtl/mig_mig.v from it, and constraints/mig.xdc, as ../example-ddr3-test/build.tcl does.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
rate="${1:-1600}"
out="${2:-mig-$rate}"
vivado="${VIVADO:-vivado}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
user_design="$out/proj/mig.gen/sources_1/ip/mig/mig/user_design"
if [ ! -f "$user_design/rtl/mig_mig.v" ]; then
    rm -rf "$out/proj"
    python3 "$here/mig_prj.py" "$here/../pins.csv" "$rate" >"$out/mig.prj"
    # xc7k160t-ffg676/-2 in the project file is the part xc7k160tffg676-2.
    part="$(sed -n 's#.*<TargetFPGA>\(.*\)-\(.*\)/\(.*\)</TargetFPGA>.*#\1\2\3#p' "$out/mig.prj")"
    (cd "$out" && "$vivado" -mode batch -nojournal -log mig.log -source "$here/mig.tcl" -tclargs "$part" >mig.out 2>&1 </dev/null) || true
    [ -f "$user_design/rtl/mig_mig.v" ] || { echo "MIG was not generated; see $out/mig.log" >&2; exit 1; }
fi
echo "$user_design"
