#!/usr/bin/env python3
"""Scans a JTAG chain through a Xilinx Virtual Cable server and reads what a Kintex-7 says of itself:
its IDCODE, the configuration status, and the XADC's die temperature and supply voltages with their
extremes since configuration.

    xvc_scan.py [host[:port]]           everything
    xvc_scan.py --xadc [host[:port]]    the temperature and the supplies only

Everything here is a read: IDCODE, the IR capture bits, eFUSE registers, configuration registers through
CFG_IN/CFG_OUT, and the XADC's status registers. Nothing reconfigures the device, and the design in it keeps
running. The server is one that is running already, 127.0.0.1:2542 by default: for a Raspberry Pi Pico
with xvc-pico, its xvcd-pico. The speed grade is not readable this way; it is printed on the package.
"""
import socket
import struct
import sys

IRLEN = 6
IDCODE, USERCODE, CFG_OUT, CFG_IN = 0x09, 0x08, 0x04, 0x05
FUSE_KEY, FUSE_DNA, FUSE_USER, FUSE_CNTL, XADC_DRP = 0x31, 0x32, 0x33, 0x34, 0x37

# IDCODE[27:0] of the Kintex-7 parts (UG470 table 1-1).
PARTS = {
    0x3647093: "XC7K70T", 0x364C093: "XC7K160T", 0x3651093: "XC7K325T", 0x3747093: "XC7K355T",
    0x3656093: "XC7K410T", 0x3752093: "XC7K420T", 0x3751093: "XC7K480T",
}


class Xvc:
    def __init__(self, host="127.0.0.1", port=2542):
        self.s = socket.create_connection((host, port), timeout=10)
        self.s.sendall(b"getinfo:")
        info = b""
        while not info.endswith(b"\n"):
            info += self.s.recv(1)
        self.info = info.decode().strip()
        self.max_bits = min(int(self.info.split(":")[1]) // 2, 1024) * 8

    def _recv(self, n):
        out = b""
        while len(out) < n:
            part = self.s.recv(n - len(out))
            if not part:
                raise IOError("XVC server closed the connection")
            out += part
        return out

    def shift(self, tms, tdi):
        """Clock len(tms) bits; returns TDO sampled before each rising edge."""
        assert len(tms) == len(tdi)
        tdo = []
        for at in range(0, len(tms), self.max_bits):
            m, d = tms[at:at + self.max_bits], tdi[at:at + self.max_bits]
            n = len(m)
            nbytes = (n + 7) // 8
            pack = lambda bits: sum(b << i for i, b in enumerate(bits)).to_bytes(nbytes, "little")
            self.s.sendall(b"shift:" + struct.pack("<I", n) + pack(m) + pack(d))
            got = int.from_bytes(self._recv(nbytes), "little")
            tdo += [(got >> i) & 1 for i in range(n)]
        return tdo


class Tap:
    """Starts and ends every operation in Run-Test/Idle."""

    def __init__(self, xvc):
        self.x = xvc

    def reset(self):
        self.x.shift([1] * 6 + [0], [0] * 7)

    def idle(self, n):
        self.x.shift([0] * n, [0] * n)

    def _scan(self, lead, bits):
        n = len(bits)
        tms = lead + [0] * (n - 1) + [1, 1, 0]
        tdo = self.x.shift(tms, [0] * len(lead) + bits + [0, 0])
        return tdo[len(lead):len(lead) + n]

    def ir_bits(self, bits):
        return self._scan([1, 1, 0, 0], bits)

    def dr_bits(self, bits):
        return self._scan([1, 0, 0], bits)

    def ir(self, value, n=IRLEN):
        return unbits(self.ir_bits(tobits(value, n)))

    def dr(self, value, n):
        return unbits(self.dr_bits(tobits(value, n)))


def tobits(value, n):
    return [(value >> i) & 1 for i in range(n)]


def unbits(bits):
    return sum(b << i for i, b in enumerate(bits))


def first_one(bits):
    return bits.index(1) if 1 in bits else None


def scan_chain(tap):
    tap.reset()
    raw = tap.dr_bits([1] * 256)       # after reset every DR is IDCODE, or BYPASS (a single 0)
    ids, at = [], 0
    while at < len(raw):
        if raw[at] == 0:
            ids.append(None)
            at += 1
            continue
        word = unbits(raw[at:at + 32])
        if word == 0xFFFFFFFF:
            break
        ids.append(word)
        at += 32
    tap.reset()
    out = tap.ir_bits([0] * 64 + [1] * 64)
    irlen = first_one(out[64:])
    capture = out[:irlen] if irlen else out[:16]
    out = tap.dr_bits([0] * 32 + [1] * 32)   # every IR is all ones now: BYPASS
    ndev = first_one(out[32:])
    tap.reset()
    return ids, irlen, capture, ndev


def cfg_words(words):
    """Configuration words go in most significant bit first."""
    bits = []
    for w in words:
        bits += [(w >> i) & 1 for i in range(31, -1, -1)]
    return bits


def read_cfg(tap, addr):
    """UG470 table 6-5: read one configuration register over JTAG, then desynchronize."""
    tap.reset()
    tap.ir(CFG_IN)
    tap.dr_bits(cfg_words([0xAA995566, 0x20000000, 0x28000001 | (addr << 13), 0x20000000, 0x20000000]))
    tap.ir(CFG_OUT)
    out = tap.dr_bits([0] * 32)
    tap.ir(CFG_IN)
    tap.dr_bits(cfg_words([0x30008001, 0x0000000D, 0x20000000, 0x20000000]))
    tap.reset()
    return sum(b << (31 - i) for i, b in enumerate(out))   # first bit out is bit 31


def xadc_read(tap, addr):
    """UG480: DR is {cmd[29:26], addr[25:16], data[15:0]}; the result is captured by the next DR scan."""
    tap.dr((1 << 26) | (addr << 16), 32)
    tap.idle(32)
    value = tap.dr(0, 32) & 0xFFFF
    tap.idle(32)
    return value


def temperature(code):
    return (code >> 4) * 503.975 / 4096 - 273.15


def supply(code):
    return (code >> 4) * 3.0 / 4096


def show_xadc(tap):
    tap.ir(XADC_DRP)
    tap.idle(32)
    x = {a: xadc_read(tap, a) for a in (0x00, 0x01, 0x02, 0x06, 0x20, 0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27)}
    tap.reset()
    print(f"temperature {temperature(x[0x00]):5.1f} C  (min {temperature(x[0x24]):.1f}, max {temperature(x[0x20]):.1f})")
    for name, now, lo, hi in (("VCCINT ", 0x01, 0x25, 0x21), ("VCCAUX ", 0x02, 0x26, 0x22), ("VCCBRAM", 0x06, 0x27, 0x23)):
        print(f"{name}     {supply(x[now]):.3f} V  (min {supply(x[lo]):.3f}, max {supply(x[hi]):.3f})")


def main():
    args = [arg for arg in sys.argv[1:] if arg != "--xadc"]
    host, _, port = (args[0] if args else "127.0.0.1").partition(":")
    xvc = Xvc(host, int(port or 2542))
    tap = Tap(xvc)
    if "--xadc" in sys.argv[1:]:
        tap.reset()
        show_xadc(tap)
        return 0
    print("server:", xvc.info)

    ids, irlen, capture, ndev = scan_chain(tap)
    print(f"chain: {ndev} device(s), total IR length {irlen}, IR capture (LSB first) {capture}")
    for i, word in enumerate(ids):
        if word is None:
            print(f"  device {i}: no IDCODE")
            continue
        part = PARTS.get(word & 0x0FFFFFFF, "unknown")
        print(f"  device {i}: IDCODE {word:#010x}  manufacturer {(word >> 1) & 0x7FF:#05x}  "
              f"part {(word >> 12) & 0xFFFF:#06x}  revision {word >> 28}  -> {part}")
    if ndev != 1 or len(ids) != 1 or ids[0] is None or (ids[0] & 0x0FFFFFFF) not in PARTS:
        print("not a lone Kintex-7; stopping here")
        return 1

    # Repeat the IDCODE read: a marginal cable shows up as a changing answer.
    tap.ir(IDCODE)
    reads = {tap.dr(0, 32) for _ in range(200)}
    print("IDCODE x200:", " ".join(f"{r:#010x}" for r in sorted(reads)), "(stable)" if len(reads) == 1 else "(UNSTABLE)")

    cap = tap.ir(IDCODE)
    print(f"IR capture {cap:06b}: DONE={cap >> 5 & 1} INIT_COMPLETE={cap >> 4 & 1} "
          f"ISC_ENABLED={cap >> 3 & 1} ISC_DONE={cap >> 2 & 1}")

    tap.ir(USERCODE)
    print(f"USERCODE  {tap.dr(0, 32):#010x}")
    tap.ir(FUSE_DNA)
    dna = tap.dr(0, 64)
    print(f"FUSE_DNA  {dna:#018x} (64 bits as shifted out, first bit = bit 0)")
    tap.ir(FUSE_USER)
    print(f"FUSE_USER {tap.dr(0, 32):#010x}")
    tap.ir(FUSE_CNTL)
    print(f"FUSE_CNTL {tap.dr(0, 32):#010x}")
    tap.reset()

    regs = {"STAT": 0x07, "IDCODE": 0x0C, "BOOTSTS": 0x16, "CTL0": 0x05, "COR0": 0x09, "COR1": 0x0E,
            "AXSS": 0x0D, "WBSTAR": 0x10, "TIMER": 0x11}
    values = {name: read_cfg(tap, addr) for name, addr in regs.items()}
    for name, v in values.items():
        print(f"cfg {name:8s}{v:#010x}")
    s = values["STAT"]
    fields = [("CRC_ERROR", 0, 1), ("PART_SECURED", 1, 1), ("MMCM_LOCK", 2, 1), ("DCI_MATCH", 3, 1),
              ("EOS", 4, 1), ("GTS_CFG_B", 5, 1), ("GWE", 6, 1), ("GHIGH_B", 7, 1), ("MODE", 8, 3),
              ("INIT_COMPLETE", 11, 1), ("INIT_B", 12, 1), ("RELEASE_DONE", 13, 1), ("DONE", 14, 1),
              ("ID_ERROR", 15, 1), ("DEC_ERROR", 16, 1), ("XADC_OVER_TEMP", 17, 1),
              ("STARTUP_STATE", 18, 3), ("BUS_WIDTH", 25, 2)]
    print("STAT:", " ".join(f"{n}={(s >> lo) & ((1 << w) - 1):0{w}b}" for n, lo, w in fields))

    show_xadc(tap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
