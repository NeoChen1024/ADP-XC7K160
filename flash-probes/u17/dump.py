#!/usr/bin/env python3
"""Reads the user SPI flash, U17, through the flashjtag probe design, which must be the design in the
FPGA; ../run.sh sees to that. Only read commands are sent: 9F (ID), 05 (status), 5A (SFDP), 03 and 0B
(read).

    dump.py             the ID, the status register and the start of the SFDP table
    dump.py out.bin     the whole flash, 2 MiB, read twice in two ways; written if the two agree

The cable is an XVC server at 127.0.0.1:2542.
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


def spi(x, command, nbytes, lag=1):
    """One chip select: the command's bytes, then nbytes of answer."""
    send = len(command) * 8
    n = send + nbytes * 8 + lag
    word = int.from_bytes(command, "big")
    tdi = 0
    for i in range(send):                       # the first bit shifted is the command's most significant
        if (word >> (send - 1 - i)) & 1:
            tdi |= 1 << (3 + i)
    tms = 1 | (1 << (3 + n - 1)) | (1 << (3 + n))            # 1 0 0, n shifts with the last leaving, 1 0
    tdo = shift_int(x, tms, tdi, n + 5) >> (3 + send + lag)
    out = bytearray(nbytes)
    for i in range(nbytes):
        b = (tdo >> (8 * i)) & 0xFF
        out[i] = int(f"{b:08b}"[::-1], 2)       # most significant bit came first
    return bytes(out)


def main():
    x = Xvc()
    tap = Tap(x)
    tap.reset()
    tap.ir(USER1)
    for lag in (0, 1, 2):
        print("lag", lag, "id", spi(x, b"\x9f", 6, lag).hex(), file=sys.stderr)
    ident = spi(x, b"\x9f", 3)
    assert ident == bytes.fromhex("c22535"), ident.hex()       # the MX25U1635E of this board
    status = spi(x, b"\x05", 2)
    sfdp = spi(x, b"\x5a\x00\x00\x00\x00", 64)
    print("status", status.hex(), "sfdp", sfdp[:16].hex(), sfdp[:4], file=sys.stderr)
    assert sfdp[:4] == b"SFDP"
    if len(sys.argv) < 2:
        return
    size = 1 << 21
    images = []
    for chunk, command in ((4096, 0x03), (1000, 0x0B)):     # another split and another read command
        start = time.monotonic()
        image = bytearray()
        at = 0
        while at < size:
            k = min(chunk, size - at)
            head = bytes([command]) + at.to_bytes(3, "big") + (b"\x00" if command == 0x0B else b"")
            image += spi(x, head, k)
            at += k
        images.append(bytes(image))
        print("pass", hex(command), chunk, "%.1f s" % (time.monotonic() - start),
              hashlib.sha256(image).hexdigest(), file=sys.stderr)
    assert images[0] == images[1], "two reads differ"
    assert spi(x, b"\x9f", 3) == ident
    tap.reset()
    open(sys.argv[1], "wb").write(images[0])
    blank = sum(1 for at in range(0, size, 4096) if images[0][at:at + 4096] == b"\xff" * 4096)
    print("written", sys.argv[1], "blank 4 KiB sectors:", blank, "of", size // 4096, file=sys.stderr)


main()
