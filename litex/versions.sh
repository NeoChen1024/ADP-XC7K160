#!/usr/bin/env sh
# Lists the URL and the commit of each Git repository of a LiteX installation, one line each.
#
#   sh versions.sh /path/to/litex-hub > versions.txt
#
# litex_setup.py clones all the repositories side by side into one directory. A result that names
# versions.txt can be built again from the same commits.
set -eu
[ $# -eq 1 ] && [ -d "$1" ] || { echo "usage: sh versions.sh /path/to/litex-hub" >&2; exit 2; }
for d in "$1"/*/; do
    [ -d "$d/.git" ] || continue
    printf '%-34s %s %s\n' "$(basename "$d")" "$(git -C "$d" remote get-url origin)" "$(git -C "$d" rev-parse HEAD)"
done
