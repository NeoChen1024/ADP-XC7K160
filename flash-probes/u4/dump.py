#!/usr/bin/env python3
"""Reads the configuration flash, U4, through the bpijtag probe design, which must be the design in the
FPGA; ../run.sh sees to that. The probe cannot write the flash.

    dump.py probe               16 words at each of five addresses, in both halves of the flash
    dump.py out.bin [words]     the whole flash, 128 MiB, that many words in a scan (4096 if not given)

The file has word address 0 first and the low byte (FDQ7..0) of a word first. Read it twice with two
scan lengths and compare: one read has no check of its own. The cable is an XVC server at
127.0.0.1:2542.
"""
import hashlib
import os
import struct
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jtag"))
from xvc_scan import Tap, Xvc  # noqa: E402

USER1 = 0x02
HEAD = 26
FIRST = HEAD + 16            # the shift at which the first word's bit 0 is read
WORDS = 1 << 26


def shift_int(x, tms, tdi, n):
    out, at = 0, 0
    while at < n:
        k = min(x.max_bits, n - at)
        nbytes = (k + 7) // 8
        mask = (1 << k) - 1
        x.s.sendall(b"shift:" + struct.pack("<I", k) + ((tms >> at) & mask).to_bytes(nbytes, "little")
                    + ((tdi >> at) & mask).to_bytes(nbytes, "little"))
        out |= (int.from_bytes(x._recv(nbytes), "little") & mask) << at
        at += k
    return out


def read(x, address, words, first=FIRST):
    """Words from a word address on, as bytes, the low byte of a word first."""
    n = first + 16 * words
    tms = 1 | (1 << (3 + n - 1)) | (1 << (3 + n))
    tdo = shift_int(x, tms, address << 3, n + 5) >> (3 + first)
    return (tdo & ((1 << (16 * words)) - 1)).to_bytes(2 * words, "little")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    x = Xvc()
    tap = Tap(x)
    tap.reset()
    tap.ir(USER1)
    if sys.argv[1] == "probe":
        for first in (FIRST - 1, FIRST, FIRST + 1):
            print(first, read(x, 0x10, 16, first).hex(), file=sys.stderr)
        for address in (0, 0x18, 1 << 25, (1 << 25) + 0x18, (1 << 26) - 16):
            print(hex(address), read(x, address, 16).hex(), file=sys.stderr)
        tap.reset()
        return
    out, chunk = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 4096
    start = time.monotonic()
    digest = hashlib.sha256()
    with open(out, "wb") as f:
        at = 0
        while at < WORDS:
            k = min(chunk, WORDS - at)
            part = read(x, at, k)
            f.write(part)
            digest.update(part)
            at += k
            if at % (1 << 22) < chunk:
                print("%5.1f%% %.0f s" % (100 * at / WORDS, time.monotonic() - start), file=sys.stderr, flush=True)
    tap.reset()
    print("read", out, "chunk", chunk, "%.0f s" % (time.monotonic() - start), digest.hexdigest(), file=sys.stderr)


main()
