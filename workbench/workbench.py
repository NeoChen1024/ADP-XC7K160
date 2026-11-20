#!/usr/bin/env python3
"""The host's side of the ADP-XC7K160 workbench (rtl/adp_xc7k160_workbench.sv): what the board's pins do,
and what the parts on them answer. Build it and load one of its two bitstreams first: build.sh, then
../jtag/load.sh. The port is $BOARD_PORT or -p.

Passive, with either bitstream; nothing is driven:

    workbench.py snapshot FILE      every channel's level and its rises a second, into FILE (JSON)
    workbench.py pulls UP DOWN      two snapshots, one from each bitstream: which pins follow the FPGA's
                                    weak pull, and so have nothing driving them, and which do not
    workbench.py clocks             the channels that are clocks, in Hz
    workbench.py watch [SECONDS]    every change as it happens, for 60 s if not told: press a button,
                                    pull a card
    workbench.py buttons            asks for each button in turn, and says which channel answered
    workbench.py boot               what every channel did since the FPGA was loaded: its first level, and
                                    when it first rose and first fell. Run it right after a load

Active, with either bitstream unless it says so; each drives only lines the board gives the FPGA to drive, and lets
go of them after:

    workbench.py set NAME 0|1|z     drive a channel low or high, or let it go (z), and leave it so: to look at
                                    an LED or to put a meter on a pin. Only the channels the design may drive
    workbench.py i2c                the I2C bus: who answers, and the EEPROM's first bytes (nothing is written)
    workbench.py mdio               both Ethernet PHYs' management buses: who answers, and their registers
    workbench.py eth                the 10/100 PHY: link, a frame from the wire, a frame out and back
                                    through the PHY's own loopback (the link drops for a few seconds).
                                    With the pull-down bitstream: the PHY takes its mode from the pulls
    workbench.py sd                 the SD card over SPI: its identity and size (nothing is written)
    workbench.py audio              the audio codec: its bit clock in and out of reset, its registers, and
                                    two tones through its converters and back (its outputs stay muted)
    workbench.py tone [SECONDS]     the same tones to the codec's outputs, to listen to: 1 kHz on the left,
                                    500 Hz on the right
    workbench.py sw12               whether RS0 and RS1 are tied to the flash's FA23 and FA24
    workbench.py rts                the board's RTS line, at the host's CTS
    workbench.py digits             lights each segment in turn, and asks what is seen
    workbench.py auto [FILE]        the active tests that ask nothing, one after another; results into FILE

These two do more than look, and are not part of auto:

    workbench.py net [BOARD_IP [HOST_IP [PORT]]]
                                    the board as a station on the LAN, talking to this host and to nobody
                                    else: ARP both ways, ping, UDP there and back, and frames by the thousand
                                    each way. The board takes an address nobody answers for unless it is
                                    given one; PORT (5201) must be a UDP port the host's firewall lets in
    workbench.py sdrw [BLOCKS]      WRITES TO THE CARD: blocks read, written, read back and put back as they
                                    were, over SPI with the card checking every CRC; then the same on the
                                    card's own four-bit bus, which is what uses SD_DAT1 and SD_DAT2
"""
import argparse
import json
import os
import random
import socket
import subprocess
import sys
import time
import zlib

import serial

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import board  # noqa: E402

NAMES, INDEX = board.NAMES, board.INDEX
NCH = len(NAMES)
ROUND = 4 * ((NCH + 3) // 4)


class Bench:
    def __init__(self, path):
        self.port = serial.Serial(path, 115200, timeout=1.0)
        # Opening the port sends the board a few bytes of nothing, and the board drops what it cannot read.
        time.sleep(0.05)
        self.port.reset_input_buffer()
        for _ in range(3):
            answer = self.answer("i", 10)
            if answer:
                break
        else:
            sys.exit("the workbench does not answer: is it loaded?")
        magic, channels = int(answer[:8], 16), int(answer[8:], 16)
        if magic != board.MAGIC or channels != NCH:
            sys.exit(f"the board runs design {magic:08x} with {channels} channels; this script is for "
                     f"{board.MAGIC:08x} with {NCH}: build and load again")

    def tell(self, text):
        self.port.write(text.encode())
        if len(text) > 64:
            # An answer is waited for a second: what is asked after a long run of commands would be
            # asked before the run has left the port.
            self.port.flush()

    def answer(self, text, digits):
        """A command's answer of that many hex digits, or nothing if what came is not that."""
        self.port.write(text.encode())
        line = self.port.readline().decode("ascii", "replace").strip()
        return line if len(line) == digits and all(c in "0123456789abcdef" for c in line) else ""

    def ask(self, text, digits):
        line = self.answer(text, digits)
        if not line:
            # A glitch on the board's supply, as a card pushed into the SD socket gives, garbles a byte:
            # the question is asked again once, after what is left of the bad answer has come.
            time.sleep(0.3)
            self.port.reset_input_buffer()
            line = self.answer(text, digits)
        if not line:
            sys.exit(f"bad answer to {text!r}")
        return line

    # ---- channels ----
    def drive(self, name, value):
        self.tell(f"c{INDEX[name]:02x}{2 | value:x}")

    def release(self, *names):
        for name in names:
            self.tell(f"c{INDEX[name]:02x}0")

    def level(self, name):
        return int(self.ask(f"g{INDEX[name]:02x}", 1))

    def look(self):
        """Levels, and what was seen since the last look: three dicts by name."""
        line = self.ask("s", 3 * ROUND // 4)
        sets = [int(line[at * ROUND // 4:(at + 1) * ROUND // 4], 16) for at in range(3)]
        return [{name: value >> at & 1 for at, name in enumerate(NAMES)} for value in sets]

    def counts(self):
        """The second's number and every channel's rises in the last whole second."""
        line = self.ask("f", 2 + 7 * NCH)
        return int(line[:2], 16), {name: int(line[2 + 7 * at:9 + 7 * at], 16) for at, name in enumerate(NAMES)}

    def counts_from_now(self):
        """Rises in a second that began after this call."""
        first, _ = self.counts()
        while True:
            time.sleep(0.2)
            second, counts = self.counts()
            if (second - first) % 256 >= 2:
                return counts

    # ---- bits on a clock ----
    def clocked(self, clock, out, data_in):
        self.tell(f"k{INDEX[clock]:02x}{INDEX[out]:02x}{INDEX[data_in]:02x}")

    def byte(self, value):
        return int(self.ask(f"x{value:02x}", 2), 16)

    def bit(self, value):
        return int(self.ask(f"b{value:x}", 1))

    # ---- the audio codec ----
    def codec_link(self, on, left=False, right=False):
        self.tell(f"l{on << 2 | right << 1 | left:x}")

    def codec_write(self, register, value):
        self.tell(f"w{register:02x}{value:04x}")

    def codec_read(self, register):
        """The register's value, or None if the codec did not answer for it."""
        line = self.ask(f"r{register:02x}", 7)
        return int(line[3:], 16) if line[0] == "1" and int(line[1:3], 16) == register else None

    def codec_peaks(self):
        """What came from the codec's converters since the last call: whether it says it is ready, the
        left channel's (highest, lowest) sample, the right's, and the frames that had samples."""
        line = self.ask("p", 21)
        signed = lambda text: int(text, 16) - (0x10000 if int(text, 16) & 0x8000 else 0)
        four = [signed(line[1 + 4 * at:5 + 4 * at]) for at in range(4)]
        return bool(int(line[0], 16) & 1), (four[0], four[1]), (four[2], four[3]), int(line[17:], 16)

    def since_load(self, name):
        """The channel's first level after the FPGA was loaded, its level now, and the seconds from the
        load to its first rise and its first fall, None for what has not happened."""
        line = self.ask(f"z{INDEX[name]:02x}", 17)
        flags = int(line[0], 16)
        seconds = lambda text: int(text, 16) / 20e6
        return {"first": flags >> 3 & 1, "now": flags & 1,
                "rose": seconds(line[1:9]) if flags & 4 else None, "fell": seconds(line[9:17]) if flags & 2 else None}

    def digits(self, left=None, right=None):
        self.tell("d00000" if left is None else f"d1{left:02x}{right:02x}")


def hertz(name, count):
    return count * board.DIVIDED if name in dict(board.INTERNAL) else count


def frequency(hz):
    if hz >= 1e6:
        return f"{hz / 1e6:.6f} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3f} kHz"
    return f"{hz} Hz"


# ---- passive -------------------------------------------------------------------------------------------

def snapshot(bench):
    bench.look()
    counts = bench.counts_from_now()
    time.sleep(0.3)
    level, low, high = bench.look()
    # The two pads with nothing on them say which bitstream this is.
    marks = level["TP59"], level["TP60"]
    variant = {(1, 1): "pullup", (0, 0): "pulldown"}.get(marks, "unknown")
    return {"variant": variant, "level": level, "seen_low": low, "seen_high": high,
            "hertz": {name: hertz(name, counts[name]) for name in NAMES}}


def command_snapshot(bench, args):
    shot = snapshot(bench)
    json.dump(shot, open(args.values[0], "w"), indent=1)
    busy = {name: hz for name, hz in shot["hertz"].items() if hz}
    print(f"the {shot['variant']} bitstream; {len(busy)} of {NCH} channels are moving")
    for name, hz in busy.items():
        print(f"  {name:16} {frequency(hz)}")


def classify(name, up, down):
    moving = max(up["hertz"][name], down["hertz"][name])
    both = lambda shot: shot["seen_low"][name] and shot["seen_high"][name]
    if moving or both(up) or both(down):
        if up["hertz"][name] and down["hertz"][name]:
            return f"driven: toggles, {frequency(up['hertz'][name])}"
        return f"toggles with one pull only: {frequency(up['hertz'][name])} pulled up, {frequency(down['hertz'][name])} pulled down"
    a, b = up["level"][name], down["level"][name]
    if (a, b) == (1, 0):
        return "follows the pull: nothing drives it"
    if (a, b) == (1, 1):
        return "held high"
    if (a, b) == (0, 0):
        return "held low"
    return "low when pulled up, high when pulled down"


SAME_PULL = {name for name, _, _, kind in board.CHANNELS if kind.endswith("+down")}


def command_pulls(bench, args):
    up, down = (json.load(open(path)) for path in args.values[:2])
    if up["variant"] != "pullup" or down["variant"] != "pulldown":
        sys.exit(f"the first file must be from the pull-up bitstream and the second from the pull-down one; "
                 f"these are {up['variant']} and {down['variant']}")
    result = {}
    group = None
    for name in NAMES:
        if name in dict(board.INTERNAL):
            hz = up["hertz"][name]
            result[name] = f"{frequency(hz)}" if hz else "no clock"
        elif name in SAME_PULL:
            result[name] = f"reads {up['level'][name]}; pulled down in both bitstreams, so that it stays off"
        else:
            result[name] = classify(name, up, down)
        if board.GROUP[name] != group:
            group = board.GROUP[name]
            print(f"\n{group}")
        print(f"  {name:16} {result[name]}")
    if len(args.values) > 2:
        json.dump(result, open(args.values[2], "w"), indent=1)


def command_clocks(bench, args):
    counts = bench.counts_from_now()
    for name in NAMES:
        if counts[name] >= 1000:
            print(f"  {name:16} {frequency(hertz(name, counts[name]))}")


# Lines that carry the LAN's traffic while the PHY has a link: a watch would show little else.
TRAFFIC = {"MAC_RX_DV", "MAC_RX_ER", "MAC_RXD0", "MAC_RXD1", "MAC_RXD2", "MAC_RXD3", "MAC_CRS", "MAC_COL"}


def command_watch(bench, args):
    seconds = float(args.values[0]) if args.values else 60
    everything = "all" in args.values[1:]
    busy = {name for name, count in bench.counts_from_now().items() if count > 50}
    if not everything:
        busy |= TRAFFIC
    print(f"watching for {seconds:g} s (Ctrl-C ends it sooner). Left out: clocks"
          + ("" if everything else ", and the Ethernet PHY's receive lines (watch SECONDS all shows them)"))
    last = bench.look()[0]
    end = time.monotonic() + seconds
    try:
        while time.monotonic() < end:
            time.sleep(0.05)
            level, low, high = bench.look()
            for name in NAMES:
                if name in busy:
                    continue
                if level[name] != last[name]:
                    print(f"{time.strftime('%H:%M:%S')}  {name:16} {last[name]} -> {level[name]}", flush=True)
                elif low[name] and high[name]:
                    print(f"{time.strftime('%H:%M:%S')}  {name:16} a pulse, back at {level[name]}", flush=True)
            last = level
    except KeyboardInterrupt:
        print()
    print("the watch is over")


def command_set(bench, args):
    drivable = [name for name, _, _, kind in board.CHANNELS if kind]
    if len(args.values) != 2 or args.values[0] not in drivable or args.values[1] not in ("0", "1", "z"):
        sys.exit("set NAME 0|1|z, where NAME is one of: " + " ".join(drivable))
    name, value = args.values
    bench.look()
    if value == "z":
        bench.release(name)
    else:
        bench.drive(name, int(value))
    time.sleep(1.0)
    level, low, high = bench.look()
    resets = [reset for reset in ("PORn", "SYS_RSTn") if low[reset]]
    print(f"{name} is {'let go' if value == 'z' else 'driven ' + value}, and reads {level[name]}"
          + (f"; {' and '.join(resets)} went low in the second after" if resets else ""))


def command_boot(bench, args):
    rows = {name: bench.since_load(name) for name in NAMES}
    moved = {name: row for name, row in rows.items() if row["rose"] is not None or row["fell"] is not None}
    when = lambda t: "          -" if t is None else f"{t * 1e3:11.4f}"
    print("since the FPGA was loaded, in the order of the first change; times in ms")
    print(f"  {'channel':16} first  rose at     fell at      now")
    first_change = lambda row: min(t for t in (row["rose"], row["fell"]) if t is not None)
    for name, row in sorted(moved.items(), key=lambda item: first_change(item[1])):
        print(f"  {name:16} {row['first']}      {when(row['rose'])} {when(row['fell'])}  {row['now']}")
    still = [name for name in NAMES if name not in moved]
    print(f"never moved: {sum(rows[n]['first'] for n in still)} channels high, "
          f"{sum(1 - rows[n]['first'] for n in still)} low")
    if args.values:
        json.dump(rows, open(args.values[0], "w"), indent=1)


def command_buttons(bench, args):
    quiet = {name for name, count in bench.counts_from_now().items() if count > 50}
    asked = ["SW1", "SW2", "SW3", "SW4", "SW5", "SW6", "SW7", "SW8", "HW_RST_SW1 (hardware reset)",
             "PWR_SW1 (the small power button, not the POWER_SW1 switch)"]
    for button in asked:
        input(f"\nPress Enter, then press and release {button} within 10 s (or just wait, to skip it) ")
        rest = bench.look()[0]
        seen = {}
        end = time.monotonic() + 10
        while time.monotonic() < end and not seen:
            time.sleep(0.1)
            level, low, high = bench.look()
            for name in NAMES:
                if name not in quiet and (level[name] != rest[name] or (low[name] and high[name])):
                    seen[name] = rest[name]
        time.sleep(1.0)     # the release, and any bounce
        bench.look()
        if not seen:
            print(f"  {button}: nothing moved")
        for name, at_rest in seen.items():
            print(f"  {button}: {name} is {at_rest} at rest and {1 - at_rest} while pressed")


# ---- I2C -----------------------------------------------------------------------------------------------

class I2c:
    def __init__(self, bench):
        self.b = bench
        bench.release("I2C_SCL", "I2C_SDA")

    def scl(self, v):
        self.b.release("I2C_SCL") if v else self.b.drive("I2C_SCL", 0)

    def sda(self, v):
        self.b.release("I2C_SDA") if v else self.b.drive("I2C_SDA", 0)

    def start(self):
        self.sda(1); self.scl(1); self.sda(0); self.scl(0)

    def stop(self):
        self.sda(0); self.scl(1); self.sda(1)

    def write(self, value):
        for at in range(7, -1, -1):
            self.sda(value >> at & 1); self.scl(1); self.scl(0)
        self.sda(1); self.scl(1)
        ack = self.b.level("I2C_SDA") == 0
        self.scl(0)
        return ack

    def read(self, ack):
        value = 0
        self.sda(1)
        for _ in range(8):
            self.scl(1)
            value = value << 1 | self.b.level("I2C_SDA")
            self.scl(0)
        self.sda(0 if ack else 1); self.scl(1); self.scl(0); self.sda(1)
        return value


def test_i2c(bench):
    out = {}
    bus = I2c(bench)
    idle = bench.level("I2C_SCL"), bench.level("I2C_SDA")
    out["idle"] = idle
    print(f"idle: SCL {idle[0]}, SDA {idle[1]}")
    if idle != (1, 1):
        print("the bus is not high when let go: no pull-up, or something holds it")
    found = []
    for address in range(0x08, 0x78):
        bus.start()
        if bus.write(address << 1):
            found.append(address)
        bus.stop()
    out["answering"] = [f"{a:#04x}" for a in found]
    print("answering:", ", ".join(f"{a:#04x}" for a in found) or "nobody")
    for address in found:
        if 0x50 <= address <= 0x57:
            # A two-byte address, as a 128 kbit part takes it; then a read from there. Nothing is written:
            # the write is cut short after the address.
            bus.start(); bus.write(address << 1); bus.write(0); bus.write(0)
            bus.start(); bus.write(address << 1 | 1)
            data = bytes(bus.read(at < 31) for at in range(32))
            bus.stop()
            out[f"{address:#04x}"] = data.hex()
            print(f"{address:#04x}, bytes 0 to 31: {data.hex(' ')}")
    bench.release("I2C_SCL", "I2C_SDA")
    return out


# ---- MDIO ----------------------------------------------------------------------------------------------

class Mdio:
    """Clause 22 management frames, a bit a clock. The data line is read while the clock is high, which is
    after the PHY has put out the bit that a station would take at the next rise: so a read's turnaround
    zero comes one clock early, and where it is found says where the data is."""

    def __init__(self, bench, clock, data):
        self.b, self.clock, self.data = bench, clock, data
        bench.clocked(clock, data, data)
        bench.drive(clock, 0)

    def _out(self, bits):
        self.b.drive(self.data, 1)
        for at in range(0, len(bits), 8):
            chunk = bits[at:at + 8]
            if len(chunk) == 8:
                self.b.byte(int(chunk, 2))
            else:
                for bit in chunk:
                    self.b.bit(int(bit))

    def read(self, phy, register):
        self._out("1" * 32 + "0110" + f"{phy:05b}{register:05b}")
        self.b.release(self.data)
        got = [self.b.bit(1) for _ in range(19)]
        start = 1 if got[0] == 0 else 2 if got[1] == 0 else None
        if start is None:
            return None             # nobody pulled the line low for the turnaround
        return int("".join(map(str, got[start:start + 16])), 2)

    def write(self, phy, register, value):
        self._out("1" * 32 + "0101" + f"{phy:05b}{register:05b}" + "10" + f"{value:016b}")
        self.b.release(self.data)
        self.b.bit(1)

    def done(self):
        self.b.release(self.clock, self.data)


PHY_NAMES = {0x0181B88: "Davicom DM9161", 0x0181B8A: "Davicom DM9161A", 0x0181B8B: "Davicom DM9161B or C",
             0x01410E8: "Marvell 88E1119R"}


def scan_mdio(bench, clock, data, label):
    out = {}
    bus = Mdio(bench, clock, data)
    print(f"{label}: idle level of {data} is {bench.level(data)}")
    for phy in range(32):
        id1 = bus.read(phy, 2)
        if id1 is None or id1 == 0xFFFF:
            continue
        id2 = bus.read(phy, 3)
        registers = [bus.read(phy, r) for r in range(7)]
        ident = (id1 << 16 | (id2 or 0)) >> 4
        name = PHY_NAMES.get(ident, "")
        print(f"  address {phy}: ID {id1:04x} {id2:04x} {name}")
        print("    registers 0 to 6:", " ".join("----" if r is None else f"{r:04x}" for r in registers))
        out[str(phy)] = {"id": f"{id1:04x} {id2:04x}", "name": name,
                         "registers": ["" if r is None else f"{r:04x}" for r in registers]}
    if not out:
        print("  nobody answers")
    bus.done()
    return out


def test_mdio(bench):
    return {"10/100": scan_mdio(bench, "MAC_MDC", "MAC_MDIO", "the 10/100 PHY's bus (MAC_MDC, MAC_MDIO)"),
            "gigabit": scan_mdio(bench, "PHY_MDC", "PHY_MDIO", "the gigabit PHY's bus (PHY_MDC, PHY_MDIO)")}


# ---- Ethernet ------------------------------------------------------------------------------------------

BOARD_MAC = bytes.fromhex("02adb0000001")       # locally administered: nobody's, and so the board's


class Mac:
    """The workbench's frame sender and receiver. A frame here is what is between the start delimiter
    and the check sequence; the board adds the check sequence going out, and checks it coming in."""

    def __init__(self, bench, address=BOARD_MAC, only_own=True):
        self.b, self.address, self.loaded = bench, address, {}
        bench.tell("".join(f"q{at:x}{byte:02x}" for at, byte in enumerate(address)) + f"q6{int(only_own):02x}")

    def status(self):
        line = self.b.ask("e", 29)
        state, flags = int(line[0], 16), int(line[1], 16)
        return {"sending": bool(state & 4), "armed": bool(state & 2), "holding": bool(state & 1),
                "over": bool(flags & 4), "damaged": bool(flags & 2), "crc": bool(flags & 1),
                "length": int(line[2:5], 16), "seen": int(line[5:13], 16), "good": int(line[13:21], 16),
                "bad": int(line[21:29], 16)}

    def arm(self):
        self.b.tell("a")

    def send(self, frame, times=1):
        """The frame, padded to the 60 bytes a frame must have; only the bytes that changed are told."""
        frame = frame + bytes(max(0, 60 - len(frame)))
        text = "".join(f"m{at:03x}{byte:02x}" for at, byte in enumerate(frame) if self.loaded.get(at) != byte)
        self.loaded.update(enumerate(frame))
        self.b.tell(text + f"n{len(frame):03x}{times:04x}")

    def wait_sent(self, seconds=5.0):
        end = time.monotonic() + seconds
        while self.status()["sending"]:
            if time.monotonic() > end:
                return False
        return True

    def held(self, length):
        """The first bytes of the frame that is held, its check sequence last if all of it is asked for."""
        data = b""
        for at in range(0, length, 32):
            data += bytes.fromhex(self.b.ask(f"o{at:03x}", 64))
        return data[:length]

    def take(self, seconds):
        """The next frame that passes, with its check sequence taken off, and how it came; or nothing."""
        end = time.monotonic() + seconds
        while True:
            state = self.status()
            if state["holding"]:
                data = self.held(state["length"])
                self.arm()
                return data[:-4], state
            if time.monotonic() > end:
                return None, state


def ones_sum(data):
    """The Internet checksum."""
    data = data + bytes(len(data) % 2)
    total = sum(int.from_bytes(data[at:at + 2], "big") for at in range(0, len(data), 2))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def arp_frame(operation, sender_mac, sender_ip, target_mac, target_ip, to):
    return (to + sender_mac + b"\x08\x06" + b"\x00\x01\x08\x00\x06\x04" + operation.to_bytes(2, "big")
            + sender_mac + sender_ip + target_mac + target_ip)


def ipv4_packet(source, to, protocol, payload):
    head = (b"\x45\x00" + (20 + len(payload)).to_bytes(2, "big") + b"\x00\x00\x40\x00" + bytes([64, protocol])
            + b"\x00\x00" + source + to)
    return head[:10] + ones_sum(head).to_bytes(2, "big") + head[12:] + payload


def udp_datagram(source, to, source_port, to_port, data):
    length = (8 + len(data)).to_bytes(2, "big")
    head = source_port.to_bytes(2, "big") + to_port.to_bytes(2, "big") + length
    check = ones_sum(source + to + b"\x00\x11" + length + head + b"\x00\x00" + data) or 0xFFFF
    return head + check.to_bytes(2, "big") + data


class Station:
    """The board as a host on the LAN, with the host's side of it here: it answers ARP for its address,
    ICMP echo requests, and sends back any UDP datagram to where it came from."""

    def __init__(self, mac, ip):
        self.m, self.ip, self.did, self.damaged = mac, ip, {}, 0

    def note(self, what):
        self.did[what] = self.did.get(what, 0) + 1
        return what

    def step(self, quick=False):
        """One look at the receiver, and the answer to what is there. What it was, or nothing. With
        quick, a frame too long to be ARP is let go unread: for when frames come by the thousand."""
        state = self.m.status()
        if not state["holding"]:
            return None
        if quick and state["length"] != 64:
            self.m.arm()
            return self.note("passed over")
        frame = self.m.held(state["length"])[:-4]
        self.m.arm()
        if not state["crc"] or state["damaged"] or state["over"]:
            self.damaged += 1
            return self.note("damaged")
        ethertype, source_mac = frame[12:14], frame[6:12]
        if ethertype == b"\x08\x06" and frame[20:22] == b"\x00\x01" and frame[38:42] == self.ip:
            self.m.send(arp_frame(2, self.m.address, self.ip, frame[22:28], frame[28:32], source_mac))
            return self.note("ARP request")
        if ethertype != b"\x08\x00" or len(frame) < 34 or frame[30:34] != self.ip:
            return self.note("other")
        head = (frame[14] & 15) * 4
        packet = frame[14:14 + int.from_bytes(frame[16:18], "big")]
        if ones_sum(packet[:head]) != 0:
            return self.note("IPv4 header checksum wrong")
        source, body = packet[12:16], packet[head:]
        if packet[9] == 1 and body[0] == 8:
            if ones_sum(body) != 0:
                return self.note("ICMP checksum wrong")
            answer = b"\x00\x00\x00\x00" + body[4:]
            answer = answer[:2] + ones_sum(answer).to_bytes(2, "big") + answer[4:]
            self.m.send(self.m.address.join((source_mac, b"\x08\x00")) + ipv4_packet(self.ip, source, 1, answer))
            return self.note("echo request")
        if packet[9] == 17:
            length = int.from_bytes(body[4:6], "big")
            if body[6:8] != b"\x00\x00" and ones_sum(source + self.ip + b"\x00\x11" + body[4:6] + body[:length]) != 0:
                return self.note("UDP checksum wrong")
            answer = udp_datagram(self.ip, source, int.from_bytes(body[2:4], "big"), int.from_bytes(body[0:2], "big"), body[8:length])
            self.m.send(self.m.address.join((source_mac, b"\x08\x00")) + ipv4_packet(self.ip, source, 17, answer))
            return self.note("UDP datagram")
        return self.note("other")


def phy_ready(bench):
    """The 10/100 PHY's bus and address if it can carry frames, and what it says of the link."""
    bus = Mdio(bench, "MAC_MDC", "MAC_MDIO")
    phy = next((p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)), None)
    if phy is None:
        print("no PHY answers on MAC_MDC and MAC_MDIO")
        bus.done()
        return None, None, {"phy": None}
    read = lambda r: bus.read(phy, r) or 0
    control = read(0)
    out = {"phy": phy, "control_found": f"{control:04x}"}
    if control & 0x0400:
        # The PHY reads its address and its mode from its MII outputs as it leaves reset, which it does
        # when the FPGA is loaded. With this design's pull-ups on them it comes up at address 31, isolated,
        # in RMII and as a repeater, and writing its registers back does not bring its clocks or a link.
        print(f"PHY at address {phy}: control {control:04x}: isolated from the MII, as the pull-up bitstream "
              f"leaves it. Load the pull-down bitstream for this test.")
        bus.done()
        out["isolated"] = True
        return None, None, out
    read(1)
    status = read(1)            # the link bit latches low: the second read is now
    partner = read(5)
    link = bool(status & 4)
    out.update(control=f"{control:04x}", status=f"{status:04x}", partner=f"{partner:04x}", link=link)
    print(f"PHY at address {phy}: control {control:04x}, status {status:04x}, link partner {partner:04x}")
    best = next((text for bit, text in ((0x100, "100BASE-TX full duplex"), (0x80, "100BASE-TX half duplex"),
                                         (0x40, "10BASE-T full duplex"), (0x20, "10BASE-T half duplex")) if partner & bit), "nothing")
    out["best"] = best
    print(f"link {'up' if link else 'DOWN'}, autonegotiation {'complete' if status & 0x20 else 'not complete'}; "
          f"the best the partner offers is {best}")
    counts = bench.counts_from_now()
    out["hertz"] = {name: counts[name] for name in ("MAC_RX_CLK", "MAC_TX_CLK", "MAC_RX_DV", "MAC_CRS")}
    print(f"MAC_RX_CLK {frequency(counts['MAC_RX_CLK'])}, MAC_TX_CLK {frequency(counts['MAC_TX_CLK'])}, "
          f"MAC_PHY_LINKSTS reads {bench.level('MAC_PHY_LINKSTS')}; frames in the last second: {counts['MAC_RX_DV']}")
    if counts["MAC_TX_CLK"] == 0:
        print("The PHY gives no MII clock: it left reset with the pull-up bitstream's pulls on its outputs. "
              "Load the pull-down bitstream for this test.")
        bus.done()
        out["isolated"] = True
        return None, None, out
    return bus, phy, out


def test_eth(bench):
    bus, phy, out = phy_ready(bench)
    if bus is None:
        return out
    read = lambda r: bus.read(phy, r) or 0
    address = lambda b: ":".join(f"{x:02x}" for x in b)

    # A frame from the wire, whoever it is for. What it says is the LAN's business, and is not kept.
    if out["link"]:
        mac = Mac(bench, only_own=False)
        mac.arm()
        data, state = mac.take(15)
        if data is None:
            print("no frame from the wire in 15 s")
            out["received"] = False
        else:
            out["received"] = state["crc"] and not state["damaged"]
            print(f"a frame from the wire: {state['length']} bytes, its check sequence {'right' if out['received'] else 'WRONG'}; "
                  f"to {address(data[0:6])} from {address(data[6:12])}, type {data[12:14].hex()}")

    # A frame out, and back through the PHY's loopback, which keeps it off the wire.
    # The PHY takes about a second to lock onto its own transmitter; a frame sent before that is lost.
    mac = Mac(bench, only_own=True)
    sent = BOARD_MAC + BOARD_MAC + b"\x88\xb5" + bytes(at * 7 & 255 for at in range(186))
    bus.write(phy, 0, 0x6100)       # loopback, 100 Mbit/s, full duplex, no autonegotiation
    for _ in range(5):
        time.sleep(0.5)
        mac.arm()
        mac.send(sent)
        data, state = mac.take(0.3)
        if data is not None:
            break
    if data is None:
        print("loopback: nothing came back")
        out["loopback_ok"] = False
    else:
        same = data == sent and state["crc"] and not state["damaged"]
        out["loopback_ok"] = same
        print(f"loopback: {len(data)} bytes came back, {'the ones sent, the check sequence right' if same else 'NOT as sent'}")
        if not same:
            print("  sent", sent.hex(), "\n  got ", data.hex(), "\n  ", state)
    bus.write(phy, 0, 0x1200)       # autonegotiation on, and started again
    for _ in range(40):
        time.sleep(0.25)
        read(1)
        if read(1) & 4:
            break
    print(f"autonegotiation started again; link {'up' if read(1) & 4 else 'still down after 10 s'}")
    bus.done()
    return out


def host_address(want=None):
    """This host's IPv4 address on the LAN, its prefix length and its interface."""
    if want is None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))         # sends nothing: the address the default route would use
        want = probe.getsockname()[0]
        probe.close()
    for link in json.loads(subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True).stdout):
        for entry in link.get("addr_info", ()):
            if entry.get("local") == want:
                return want, entry["prefixlen"], link["ifname"]
    sys.exit(f"this host has no address {want}")


def udp_counters():
    lines = [line.split() for line in open("/proc/net/snmp") if line.startswith("Udp:")]
    return dict(zip(lines[0][1:], map(int, lines[1][1:])))


def test_net(bench, args):
    """The board as a station, with this host its only correspondent."""
    host_text, prefix, interface = host_address(args.values[1] if len(args.values) > 1 else None)
    port = int(args.values[2]) if len(args.values) > 2 else 5201
    host_ip = socket.inet_aton(host_text)
    bus, phy, out = phy_ready(bench)
    if bus is None:
        return out
    bus.done()
    if not out["link"]:
        print("no link: nothing to do")
        return out
    print(f"this host: {host_text}/{prefix} on {interface}; UDP port {port}")
    mac = Mac(bench, only_own=True)
    mac.arm()
    address = lambda b: ":".join(f"{x:02x}" for x in b)
    dotted = socket.inet_ntoa
    nobody, everybody = bytes(6), b"\xff" * 6

    # An address for the board: the one given, or one that nobody answers an ARP probe for (RFC 5227: a
    # request from no address, which changes nobody's tables).
    def probe(candidate):
        for _ in range(2):
            mac.send(arp_frame(1, BOARD_MAC, bytes(4), nobody, candidate, everybody))
            data, _ = mac.take(0.6)
            if data is not None and data[12:14] == b"\x08\x06" and data[28:32] == candidate:
                return data[22:28]
        return None

    if args.values:
        board_ip = socket.inet_aton(args.values[0])
        if probe(board_ip):
            sys.exit(f"{args.values[0]} is somebody's already")
    else:
        network = int.from_bytes(host_ip, "big") >> (32 - prefix) << (32 - prefix)
        last = network + (1 << (32 - prefix)) - 1
        board_ip = None
        for candidate in range(last - 5, last - 30, -1):
            candidate = candidate.to_bytes(4, "big")
            if candidate == host_ip:
                continue
            owner = probe(candidate)
            if owner is None:
                board_ip = candidate
                break
            print(f"  {dotted(candidate)} is taken")
        if board_ip is None:
            sys.exit("no free address found; give one")
    board_text = dotted(board_ip)
    print(f"the board: {board_text}, {address(BOARD_MAC)}; nobody answered an ARP probe for that address")
    station = Station(mac, board_ip)

    # ARP: the board asks for the host. The answer says the host hears the board and the board the host,
    # and leaves the board's address in the host's table, so that the host need not ask.
    def ask_host():
        for _ in range(3):
            mac.send(arp_frame(1, BOARD_MAC, board_ip, nobody, host_ip, everybody))
            data, _ = mac.take(1.0)
            if data is not None and data[12:14] == b"\x08\x06" and data[20:22] == b"\x00\x02" and data[28:32] == host_ip:
                return data[22:28]
        return None

    host_mac = ask_host()
    if host_mac is None:
        print(f"{host_text} does not answer the board's ARP request: is the board on that LAN?")
        out["arp"] = False
        return out
    out["arp"] = True
    print(f"ARP: the board asked for {host_text} and was answered")

    def serve(until, quick=False):
        while not until():
            station.step(quick)

    # Ping, from the host.
    for size, count, gap in ((56, 10, "0.2"), (1472, 3, "1")):
        ping = subprocess.Popen(["ping", "-n", "-c", str(count), "-i", gap, "-W", "3", "-s", str(size), "-M", "do",
                                 "-I", host_text, board_text], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        serve(lambda: ping.poll() is not None)
        text = ping.stdout.read().strip().splitlines()
        summary = [line for line in text if "transmitted" in line or line.startswith("rtt")]
        got = next((line.split(",")[1].strip() for line in text if "transmitted" in line), "?")
        out[f"ping_{size}"] = f"{count} sent, {got}"
        print(f"ping, {size} bytes of data: " + "; ".join(summary))

    # UDP: datagrams of every size to the board, which sends each back. The receiver holds one frame, and
    # a frame that comes while it holds another is counted and lost: so what the host sent of its own
    # accord (it asks again for the board's address every half minute or so) is answered first.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 << 20)
    sock.bind((host_text, port))
    sock.setblocking(False)
    rng = random.Random(1)
    sizes, back = (1, 17, 18, 19, 100, 511, 512, 1000, 1471, 1472) * 2, 0
    for size in sizes:
        data = rng.randbytes(size)
        while station.step() is not None:
            pass
        sock.sendto(data, (board_text, 7))
        end = time.monotonic() + 3
        answer = None
        while answer is None and time.monotonic() < end:
            station.step()
            try:
                answer, _ = sock.recvfrom(4096)
            except BlockingIOError:
                pass
        back += answer == data
        if answer != data:
            print(f"  a datagram of {size} bytes: " + ("nothing came back" if answer is None else "came back CHANGED"))
    out["udp_echo"] = f"{back} of {len(sizes)}"
    print(f"UDP there and back: {back} of {len(sizes)} datagrams came back as sent (1 to 1472 bytes)")

    # Frames by the thousand from the board: one datagram, sent over and over with the gap the standard
    # asks for and no more. The host's kernel checks every frame's three checksums before it counts one.
    for size, count in ((1472, 20000), (18, 20000)):
        data = rng.randbytes(size)
        frame = host_mac + BOARD_MAC + b"\x08\x00" + ipv4_packet(board_ip, host_ip, 17, udp_datagram(board_ip, host_ip, port, port, data))
        ask_host()
        before = udp_counters()
        wire = (max(len(frame), 60) + 4 + 8 + 14) * 8 / 100e6       # the frame, its check sequence, the preamble, the gap
        mac.send(frame, count)
        got = wrong = 0
        started = time.monotonic()
        end = started + count * wire + 3
        last = started
        while got + wrong < count and time.monotonic() < end:
            try:
                answer = sock.recv(4096)
            except BlockingIOError:
                continue
            last = time.monotonic()
            if answer == data:
                got += 1
            else:
                wrong += 1
        mac.wait_sent()
        after = udp_counters()
        errors = {name: after[name] - before[name] for name in ("InErrors", "RcvbufErrors", "InCsumErrors") if after[name] != before[name]}
        out[f"board_to_host_{size}"] = f"{got} of {count}"
        print(f"board to host, {count} frames of {max(len(frame), 60) + 4} bytes: {got} came whole, {wrong} came changed, "
              f"in {last - started:.2f} s ({got * (len(frame) + 4) * 8 / max(last - started, 1e-3) / 1e6:.1f} Mbit/s of frames)"
              + (f"; the host's UDP counters moved: {errors}" if errors else ""))

    # And to the board, which counts the frames to its address that came whole and the ones that did not.
    # No faster than 60 Mbit/s: the host's own port is faster than the board's, and what a switch has no
    # room for it drops, which would say nothing about the board.
    for size, count in ((1472, 10000), (18, 20000)):
        data = rng.randbytes(size)
        ask_host()
        while station.step() is not None:
            pass
        before = mac.status()
        interval = max((size + 42 + 24) * 8 / 60e6, 1 / 20000)
        started = time.perf_counter()
        for at in range(count):
            while time.perf_counter() < started + at * interval:
                pass
            try:
                sock.sendto(data, (board_text, 9))
            except BlockingIOError:
                time.sleep(0.001)
                sock.sendto(data, (board_text, 9))
        took = time.perf_counter() - started
        end = time.monotonic() + 0.5
        serve(lambda: time.monotonic() > end, quick=True)
        after = mac.status()
        good, bad = after["good"] - before["good"], after["bad"] - before["bad"]
        out[f"host_to_board_{size}"] = f"{good} whole and {bad} not, of {count}"
        print(f"host to board, {count} datagrams of {size} bytes in {took:.2f} s: the board counted {good} frames "
              f"to its address whole and {bad} damaged" + ("" if good == count else f" ({good - count:+d}: the host's own ARP, or frames lost)"))
    sock.close()
    out["station"] = dict(station.did)
    print("what the board's side answered:", ", ".join(f"{count} {what}" for what, count in sorted(station.did.items())))
    return out


# ---- SD ------------------------------------------------------------------------------------------------

def crc7(data):
    crc = 0
    for byte in data:
        for at in range(7, -1, -1):
            crc = (crc << 1) ^ (0x09 if (crc >> 6 ^ byte >> at) & 1 else 0)
            crc &= 0x7F
    return crc


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (crc << 1 ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


class SdSpi:
    def __init__(self, bench):
        self.b = bench
        bench.clocked("SD_CLK", "SD_CMD", "SD_DAT0")
        bench.drive("SD_CLK", 0)
        bench.drive("SD_CMD", 1)
        bench.drive("SD_DAT3", 1)

    def idle(self, count):
        for _ in range(count):
            self.b.byte(0xFF)

    def command(self, index, argument, more=0):
        """R1, and the bytes after it. The card stays selected if a data block follows (more < 0)."""
        self.b.drive("SD_DAT3", 0)
        self.b.byte(0xFF)
        frame = bytes([0x40 | index]) + argument.to_bytes(4, "big")
        for byte in frame + bytes([crc7(frame) << 1 | 1]):
            self.b.byte(byte)
        r1 = 0xFF
        for _ in range(10):
            r1 = self.b.byte(0xFF)
            if not r1 & 0x80:
                break
        rest = bytes(self.b.byte(0xFF) for _ in range(max(more, 0)))
        if more >= 0:
            self.deselect()
        return r1, rest

    def deselect(self):
        self.b.drive("SD_DAT3", 1)
        self.b.byte(0xFF)

    def block(self, length):
        """A data block after a read command: its bytes, and whether its CRC was right."""
        for _ in range(200):
            token = self.b.byte(0xFF)
            if token != 0xFF:
                break
        if token != 0xFE:
            self.deselect()
            return None, False
        data = bytes(self.b.byte(0xFF) for _ in range(length))
        crc = self.b.byte(0xFF) << 8 | self.b.byte(0xFF)
        self.deselect()
        return data, crc == crc16(data)

    def read(self, address):
        """A block of 512 bytes, and whether its CRC was right; nothing if the card refused."""
        r1, _ = self.command(17, address, -1)
        if r1 != 0:
            self.deselect()
            return None, False
        return self.block(512)

    def write(self, address, data, spoil=False):
        """Writes a block, with a wrong CRC if spoil. What the card said of the data (5: taken, 11: its
        CRC was wrong, 13: not written) and its status after, 0 if all is well."""
        r1, _ = self.command(24, address, -1)
        if r1 != 0:
            self.deselect()
            return None, r1
        self.b.byte(0xFF)
        self.b.byte(0xFE)
        for byte in data:
            self.b.byte(byte)
        check = crc16(data) ^ int(spoil)
        self.b.byte(check >> 8)
        self.b.byte(check & 0xFF)
        said = 0xFF
        for _ in range(8):
            said = self.b.byte(0xFF)
            if said & 0x11 == 0x01:
                break
        end = time.monotonic() + 3
        while self.b.byte(0xFF) != 0xFF and time.monotonic() < end:
            pass                    # the card holds its line low while it writes
        self.deselect()
        r1, r2 = self.command(13, 0, 1)
        return said & 0x1F, r1 << 8 | r2[0]

    def done(self):
        self.b.release("SD_CLK", "SD_CMD", "SD_DAT3")


def sd_blocks(csd):
    """The card's size in blocks of 512 bytes, from its CSD."""
    if csd[0] >> 6 == 1:
        return (((csd[7] & 0x3F) << 16 | csd[8] << 8 | csd[9]) + 1) * 1024
    c_size = (csd[6] & 3) << 10 | csd[7] << 2 | csd[8] >> 6
    mult = (csd[9] & 3) << 1 | csd[10] >> 7
    return ((c_size + 1) << (mult + 2) << (csd[5] & 0xF)) // 512


def sd_spi_start(bench):
    """The card brought up over SPI and told to check every CRC: the card, what one block's address is
    multiplied by, its size in blocks, its CID. Or why not. Its supply is switched off first: a card that
    has been used on its own bus does not always take SPI's first command."""
    bench.release("SD_DAT3")
    bench.drive("SD_CLK", 0)
    bench.drive("SD_CMD", 0)
    bench.drive("SD_PWR_ON", 0)
    time.sleep(1.5)
    bench.drive("SD_PWR_ON", 1)
    time.sleep(0.5)
    card = SdSpi(bench)
    card.idle(10)
    for _ in range(3):
        r1, _ = card.command(0, 0)
        if r1 == 0x01:
            break
    if r1 != 0x01:
        card.done()
        return f"CMD0 answers {r1:02x}"
    r1, r7 = card.command(8, 0x1AA, 4)
    v2 = r1 == 0x01 and r7[2:] == b"\x01\xaa"
    end = time.monotonic() + 3
    while True:
        card.command(55, 0)
        r1, _ = card.command(41, 0x40000000 if v2 else 0)
        if r1 == 0 or time.monotonic() > end:
            break
    if r1 != 0:
        card.done()
        return f"ACMD41 answers {r1:02x}"
    r1, ocr = card.command(58, 0, 4)
    step = 1 if ocr[0] & 0x40 else 512
    r1, _ = card.command(59, 1)
    if r1 != 0:
        card.done()
        return f"CMD59, which turns the CRC checks on, answers {r1:02x}"
    if step != 1:
        card.command(16, 512)
    r1, _ = card.command(9, 0, -1)
    csd, good_csd = card.block(16) if r1 == 0 else (None, False)
    r1, _ = card.command(10, 0, -1)
    cid, good_cid = card.block(16) if r1 == 0 else (None, False)
    if not (good_csd and good_cid):
        card.done()
        return "the CSD or the CID did not come whole"
    return card, step, sd_blocks(csd), cid


def crc16_bits(bits):
    crc = 0
    for bit in bits:
        crc = (crc << 1 ^ (0x1021 if (crc >> 15 ^ bit) & 1 else 0)) & 0xFFFF
    return crc


def bits_value(bits):
    return int("".join(map(str, bits)), 2) if bits else 0


class SdBus:
    """The card on its own bus: commands and answers on CMD, a block on the four data lines at once.

    A command goes out a byte a time and its answer comes in the same way. A block that is read is read
    four times, a line each time, since a byte's clocks sample one line. The command's answer and the
    line are then clocked by hand, a bit a time, until both the answer is in and the line has started:
    the card does not wait for its answer to end before it starts the block (the one tried here started
    18 clocks after its answer did, 30 before the answer's end). A block that is written is put on the
    lines by hand, a clock at a time."""

    LINES = ("SD_DAT0", "SD_DAT1", "SD_DAT2", "SD_DAT3")

    def __init__(self, bench):
        self.b, self.rca = bench, 0
        bench.release(*self.LINES)
        bench.drive("SD_CLK", 0)
        bench.drive("SD_CMD", 1)
        bench.clocked("SD_CLK", "SD_CMD", "SD_CMD")

    def idle(self, count):
        self.b.drive("SD_CMD", 1)
        for _ in range(count):
            self.b.byte(0xFF)

    def _send(self, index, argument):
        frame = bytes([0x40 | index]) + argument.to_bytes(4, "big")
        self.b.clocked("SD_CLK", "SD_CMD", "SD_CMD")
        self.b.drive("SD_CMD", 1)
        for byte in frame + bytes([crc7(frame) << 1 | 1]):
            self.b.byte(byte)
        self.b.release("SD_CMD")         # the board's pull-up holds it high until the card answers

    def command(self, index, argument, length=48):
        """The answer's bits, from its start bit; nothing if none came. length 0: no answer is expected."""
        self._send(index, argument)
        if not length:
            self.b.byte(0xFF)
            return []
        bits = []
        for _ in range(length // 8 + 10):
            value = self.b.byte(0xFF)
            bits += [value >> (7 - at) & 1 for at in range(8)]
            if 0 in bits and len(bits) - bits.index(0) >= length:
                break
        if 0 not in bits or len(bits) - bits.index(0) < length:
            return None
        self.b.byte(0xFF)
        return bits[bits.index(0):][:length]

    @staticmethod
    def answer(bits, index=None):
        """A 48-bit answer's 32 bits, if it is whole: the command it answers, its CRC."""
        if not bits or len(bits) != 48:
            return None
        head = bits_value(bits[:40]).to_bytes(5, "big")
        if index is not None and (bits_value(bits[2:8]) != index or crc7(head) != bits_value(bits[40:47])):
            return None
        return bits_value(bits[8:40])

    def tick(self, *names):
        """One clock by hand; the named lines' levels while it is high."""
        self.b.drive("SD_CLK", 1)
        levels = [self.b.level(name) for name in names]
        self.b.drive("SD_CLK", 0)
        return levels

    def read_line(self, address, line):
        """CMD17's answer, and what one data line carried: 1024 bits, 16 of CRC, the end bit."""
        self._send(17, address)
        answer, bits, started = [], [], False
        for _ in range(3000):
            command, level = self.tick("SD_CMD", line)
            if (answer or command == 0) and len(answer) < 48:
                answer.append(command)
            if started:
                bits.append(level)
            elif level == 0:
                started = True
            if started and len(answer) == 48:
                break
        if not started or self.answer(answer, 17) is None:
            return None
        self.b.clocked("SD_CLK", "SD_CMD", line)
        while len(bits) < 1041:
            value = self.b.byte(0xFF)
            bits += [value >> (7 - at) & 1 for at in range(8)]
        self.b.clocked("SD_CLK", "SD_CMD", "SD_CMD")
        self.b.byte(0xFF)
        return bits[:1041]

    def read(self, address):
        """A block, and for each line whether its CRC was right; nothing if a line did not start."""
        lines = [self.read_line(address, line) for line in self.LINES]
        if any(line is None for line in lines):
            return None, [line is not None for line in lines]
        good = [crc16_bits(line[:1024]) == bits_value(line[1024:1040]) and line[1040] == 1 for line in lines]
        data = bytearray()
        for at in range(0, 1024, 2):
            data.append(sum(lines[n][at] << (4 + n) | lines[n][at + 1] << n for n in range(4)))
        return bytes(data), good

    def write(self, address, data, spoil=None):
        """Writes a block; spoil names a line whose CRC is sent wrong. What the card said of it on DAT0
        (2: taken, 5: a CRC was wrong), and its status after."""
        if self.answer(self.command(24, address), 24) is None:
            return None, None
        index = lambda name: INDEX[name]
        streams = []
        for n, line in enumerate(self.LINES):
            bits = [data[at // 2] >> ((4 if at % 2 == 0 else 0) + n) & 1 for at in range(1024)]
            check = crc16_bits(bits) ^ int(spoil == line)
            streams.append([0] + bits + [check >> (15 - at) & 1 for at in range(16)] + [1])
        clock = f"c{index('SD_CLK'):02x}3c{index('SD_CLK'):02x}2"
        text = ["".join(f"c{index(line):02x}3" for line in self.LINES), clock * 2]
        now = [1, 1, 1, 1]
        for at in range(len(streams[0])):
            for n, line in enumerate(self.LINES):
                if streams[n][at] != now[n]:
                    now[n] = streams[n][at]
                    text.append(f"c{index(line):02x}{2 | now[n]:x}")
            text.append(clock)
        self.b.tell("".join(text))
        self.b.release(*self.LINES)
        seen = []
        for _ in range(64):
            (level,) = self.tick("SD_DAT0")
            if seen or level == 0:
                seen.append(level)
            if len(seen) == 5:
                break
        self.b.clocked("SD_CLK", "SD_CMD", "SD_DAT0")
        end = time.monotonic() + 3
        while self.b.byte(0xFF) != 0xFF and time.monotonic() < end:
            pass                    # busy: DAT0 low
        status = self.answer(self.command(13, self.rca << 16), 13)
        return (bits_value(seen[1:4]) if len(seen) == 5 else None), status

    def done(self):
        self.b.release("SD_CLK", "SD_CMD", *self.LINES)


def sd_bus_start(bench):
    """The card brought up on its own bus, four bits wide: the bus and the card's CID. Or why not. A card
    that has been used over SPI stays so until its supply has been off, so that comes first."""
    bench.release(*SdBus.LINES)
    bench.drive("SD_CLK", 0)
    bench.drive("SD_CMD", 0)
    bench.drive("SD_PWR_ON", 0)
    time.sleep(1.5)
    bench.drive("SD_PWR_ON", 1)
    time.sleep(0.5)
    bus = SdBus(bench)
    bus.idle(10)
    bus.command(0, 0, 0)
    if bus.answer(bus.command(8, 0x1AA), 8) != 0x1AA:
        return "CMD8 is not answered"
    end = time.monotonic() + 3
    while True:
        bus.command(55, 0)
        ocr = bus.answer(bus.command(41, 0x40FF8000))
        if ocr is None:
            return "ACMD41 is not answered"
        if ocr >> 31 or time.monotonic() > end:
            break
    if not ocr >> 31:
        return "the card stays busy"
    cid = bus.command(2, 0, 136)
    if cid is None:
        return "CMD2 is not answered"
    address = bus.answer(bus.command(3, 0), 3)
    if address is None:
        return "CMD3 is not answered"
    bus.rca = address >> 16
    if bus.answer(bus.command(7, bus.rca << 16), 7) is None:
        return "CMD7 is not answered"
    bus.command(55, bus.rca << 16)
    if bus.answer(bus.command(6, 2), 6) is None:
        return "ACMD6, which widens the bus to four lines, is not answered"
    if not ocr >> 30 & 1:
        bus.command(16, 512)
    return bus, bits_value(cid[8:136]).to_bytes(16, "big"), 1 if ocr >> 30 & 1 else 512


def test_sdrw(bench, args):
    """Blocks read, written, read back and put back, both ways of talking to the card.

    What a block held is written to a file before the block is touched, and the file is removed when
    every block is back as it was: a test that stops halfway leaves the means to undo it. A block sent
    with a wrong CRC, which the card must refuse, comes last on each bus and is followed by the card's
    supply being switched off and on: the card tried here (a 32 GB SanDisk) said 'CRC wrong' on its
    four-bit bus as it should and kept the block, but from then on wrote each block it was given one
    write late, until its supply had been off."""
    count = int(args.values[0]) if args.values else 8
    keep_path = args.values[1] if len(args.values) > 1 else "sdrw-kept.json"
    out = {}
    if bench.level("SD_CDn"):
        print("no card in the socket")
        return out
    if bench.level("SD_WPn"):
        print("SD_WPn reads 1: the card's switch is at 'lock'")
    started = sd_spi_start(bench)
    if isinstance(started, str):
        print("over SPI the card does not come up:", started)
        return out
    card, step, blocks, cid = started
    print(f"over SPI: {blocks} blocks of 512 bytes ({blocks * 512 / 1e9:.1f} GB); the card checks every CRC from here on")
    first, good = card.read(0)
    if first is None or not good:
        print("block 0 does not come whole")
        card.done()
        return out
    print(f"block 0 read, its CRC right; it {'ends in 55 AA, as a partition table does' if first[510:] == bytes([0x55, 0xAA]) else 'does not end in 55 AA'}")
    rng = random.Random(blocks)
    chosen = sorted({1, blocks // 2, blocks - 1} | set(rng.sample(range(2, blocks - 1), max(count - 3, 0))))
    kept, failed, clock = {}, [], time.monotonic()
    for block in chosen:
        original, good = card.read(block * step)
        if original is None or not good:
            failed.append(f"block {block}: not read whole, and left alone")
            continue
        kept[block] = original
    json.dump({str(block): data.hex() for block, data in kept.items()}, open(keep_path, "w"))
    print(f"what {len(kept)} blocks hold is in {keep_path} until they are back as they were")
    for block in kept:
        pattern = random.Random(block).randbytes(512)
        said, status = card.write(block * step, pattern)
        back, good = card.read(block * step)
        again, status_again = card.write(block * step, kept[block])
        last, good_last = card.read(block * step)
        if said != 5 or status or back != pattern or not good:
            failed.append(f"block {block}: written, the card said {said} and status {status}; read back {'right' if back == pattern else 'WRONG'}")
        if again != 5 or status_again or last != kept[block] or not good_last:
            failed.append(f"block {block}: NOT put back as it was (the card said {again}, status {status_again})")
    took = time.monotonic() - clock
    out["spi_blocks"], out["spi_failed"] = len(kept), list(failed)
    print(f"over SPI: {len(kept)} blocks, from block {chosen[0]} to block {chosen[-1]}, each read, written with a pattern, "
          f"read back, and put back as it was, in {took:.0f} s: " + ("all right" if not failed else "; ".join(failed)))
    middle = list(kept)[len(kept) // 2]
    said, status = card.write(middle * step, bytes(512), spoil=True)
    back, good = card.read(middle * step)
    out["spi_refuses_bad_crc"] = said == 11 and back == kept[middle]
    print(f"over SPI: a block sent with a wrong CRC: the card said {said} (11 is 'CRC wrong'), and the block is "
          f"{'as it was' if back == kept[middle] else 'CHANGED'}")
    if back != kept[middle]:
        failed.append(f"block {middle}: changed by a write the card should have refused")
    card.done()

    started = sd_bus_start(bench)
    if isinstance(started, str):
        print("on its own bus the card does not come up:", started)
        bench.release("SD_CLK", "SD_CMD", "SD_PWR_ON", *SdBus.LINES)
        return out
    bus, cid_bus, step = started
    out["cid_same"] = cid_bus == cid
    print(f"on its own bus, four lines wide: the card answers at address {bus.rca:04x}; its CID is "
          f"{'the one SPI gave' if cid_bus == cid else 'NOT the one SPI gave: ' + cid_bus.hex() + ' and ' + cid.hex()}")
    before = len(failed)
    tried = list(kept)[:2]
    for block in tried:
        original, good = bus.read(block * step)
        if original != kept[block] or not all(good):
            failed.append(f"block {block}: on four lines read {'right' if original == kept[block] else 'WRONG'}, the lines' CRCs {good}; left alone")
            continue
        pattern = random.Random(block + 1).randbytes(512)
        said, status = bus.write(block * step, pattern)
        back, good = bus.read(block * step)
        again, status_again = bus.write(block * step, kept[block])
        last, good_last = bus.read(block * step)
        if said != 2 or back != pattern or not all(good):
            failed.append(f"block {block}: written on four lines, the card said {said}; read back {'right' if back == pattern else 'WRONG'}, CRCs {good}")
        if again != 2 or last != kept[block] or not all(good_last):
            failed.append(f"block {block}: NOT put back as it was on four lines (the card said {again})")
    print(f"on its own bus: blocks {tried}, each read on four lines as SPI read it with every line's CRC right, written, "
          f"read back and put back: " + ("all right" if len(failed) == before else "; ".join(failed[before:])))
    # Last, the block the card must refuse; then its supply off and on, and the block looked at again.
    block = tried[-1]
    said, _ = bus.write(block * step, bytes(512), spoil="SD_DAT2")
    bus.done()
    started = sd_bus_start(bench)
    if isinstance(started, str):
        failed.append("the card does not come up again: " + started)
    else:
        bus, _, step = started
        still, good = bus.read(block * step)
        out["bus_refuses_bad_crc"] = said == 5 and still == kept[block]
        print(f"on its own bus: a block sent with SD_DAT2's CRC wrong: the card said {said} (5 is 'CRC wrong'), and after its "
              f"supply was off and on the block is {'as it was' if still == kept[block] else 'CHANGED'}")
        if still != kept[block]:
            failed.append(f"block {block}: changed by a write the card should have refused")
        bus.done()
    out["failed"] = failed
    bench.release("SD_PWR_ON")
    if failed:
        print(f"NOT all right; what the blocks held is still in {keep_path}")
    else:
        os.remove(keep_path)
        print("all right: SD_CMD and SD_DAT0 to SD_DAT3 carry data both ways, and every block is as it was")
    return out


def test_sd(bench):
    out = {}
    print(f"SD_CDn reads {bench.level('SD_CDn')}, SD_WPn reads {bench.level('SD_WPn')}, SD_PWR_ON reads {bench.level('SD_PWR_ON')} let go")
    out.update(cd=bench.level("SD_CDn"), wp=bench.level("SD_WPn"), power_idle=bench.level("SD_PWR_ON"))
    # What the card's lines read with nothing but the weak pull-up on them, at each level of SD_PWR_ON: a
    # card with no supply holds its lines down through its input protection, so this says which level is on
    # without talking to a card that may not be powered.
    # Switching the supply on can dip the board's 3.3 V enough for its reset chips to act (PORn, SYS_RSTn),
    # which also resets the Ethernet PHY: that is looked for and said.
    lines = ("SD_CMD", "SD_DAT0", "SD_DAT1", "SD_DAT2", "SD_DAT3")
    bench.look()
    for power in (0, 1):
        bench.drive("SD_PWR_ON", power)
        time.sleep(1.0)
        levels = [bench.level(name) for name in lines]
        resets = [name for name in ("PORn", "SYS_RSTn") if bench.look()[1][name]]
        out[f"lines_power_{power}"] = levels
        out[f"resets_power_{power}"] = resets
        print(f"SD_PWR_ON driven {power}: CMD, DAT0, DAT1, DAT2, DAT3 read {levels}"
              + (f"; {' and '.join(resets)} went low" if resets else ""))
    card = SdSpi(bench)
    answered = None
    for power in (1, 0):
        bench.drive("SD_PWR_ON", power)
        time.sleep(0.5)
        card.idle(10)
        for _ in range(3):
            r1, _ = card.command(0, 0)
            if r1 == 0x01:
                break
        print(f"with SD_PWR_ON driven {power}: CMD0 answers {r1:02x}" + (", the card is idle" if r1 == 0x01 else ""))
        out[f"cmd0_power_{power}"] = f"{r1:02x}"
        if r1 == 0x01:
            answered = power
            break
    if answered is None:
        print("no card answers")
        card.done()
        bench.release("SD_PWR_ON")
        return out
    out["power_on_level"] = answered
    r1, r7 = card.command(8, 0x1AA, 4)
    v2 = r1 == 0x01 and r7[2:] == b"\x01\xaa"
    print(f"CMD8: {r1:02x} {r7.hex()}" + (", a version 2 card, 2.7 to 3.6 V" if v2 else ""))
    end = time.monotonic() + 3
    while True:
        card.command(55, 0)
        r1, _ = card.command(41, 0x40000000 if v2 else 0)
        if r1 == 0 or time.monotonic() > end:
            break
    if r1 != 0:
        print(f"the card did not leave idle: ACMD41 answers {r1:02x}")
        card.done()
        return out
    r1, ocr = card.command(58, 0, 4)
    high_capacity = bool(ocr[0] & 0x40)
    print(f"initialized; OCR {ocr.hex()}" + (", high capacity" if high_capacity else ", standard capacity"))
    out["ocr"] = ocr.hex()
    for index, what in ((10, "CID"), (9, "CSD")):
        r1, _ = card.command(index, 0, -1)
        data, good = card.block(16) if r1 == 0 else (None, False)
        if data is None:
            print(f"{what}: no data (R1 {r1:02x})")
            continue
        out[what] = data.hex()
        out[what + "_crc_ok"] = good
        print(f"{what}: {data.hex()}, CRC {'right' if good else 'WRONG'}")
        if what == "CID":
            print(f"  maker {data[0]:02x}, OEM {data[1:3].decode('ascii', 'replace')!r}, product {data[3:8].decode('ascii', 'replace')!r}, "
                  f"made {2000 + (data[13] & 0xF) * 16 + (data[14] >> 4)}-{data[14] & 0xF:02}")
        elif data[0] >> 6 == 1:
            size = ((data[7] & 0x3F) << 16 | data[8] << 8 | data[9]) + 1
            out["bytes"] = size * 512 * 1024
            print(f"  {out['bytes'] / 1e9:.1f} GB ({size} units of 512 KiB)")
        else:
            c_size = (data[6] & 3) << 10 | data[7] << 2 | data[8] >> 6
            mult = (data[9] & 3) << 1 | data[10] >> 7
            size = (c_size + 1) << (mult + 2) << (data[5] & 0xF)
            print(f"  {size / 1e6:.0f} MB")
            out["bytes"] = size
    card.done()
    print(f"SD_PWR_ON left driven {answered}; DAT1 reads {bench.level('SD_DAT1')}, DAT2 reads {bench.level('SD_DAT2')}")
    return out


# ---- the rest ------------------------------------------------------------------------------------------

CODECS = {0x414C4770: "Realtek ALC203"}
CODEC_REGISTERS = ((0x00, "reset"), (0x02, "master volume"), (0x04, "headphone volume"), (0x18, "PCM out volume"),
                   (0x1A, "record select"), (0x1C, "record gain"), (0x26, "power-down and status"),
                   (0x28, "extended audio ID"), (0x2A, "extended audio control"), (0x2C, "DAC sample rate"),
                   (0x32, "ADC sample rate"), (0x7C, "vendor ID 1"), (0x7E, "vendor ID 2"))


def codec_start(bench):
    """The codec out of a reset, with the link running. SYNC and SDATA_OUT must be low as the reset ends,
    or the codec takes it as an order to enter a test mode: the link is off until then."""
    bench.codec_link(False)
    bench.drive("AC97_RSTn", 0)
    time.sleep(0.05)
    bench.drive("AC97_RSTn", 1)
    time.sleep(0.05)
    bench.codec_link(True)
    for _ in range(20):
        time.sleep(0.05)
        status = bench.codec_read(0x26)
        if status is not None and status & 0xF == 0xF:
            break
    return status


def codec_stop(bench):
    bench.codec_link(False)
    bench.drive("AC97_RSTn", 0)
    bench.release("AC97_RSTn")


def test_audio(bench):
    out = {}
    bench.codec_link(False)
    for reset_n in (0, 1):
        bench.drive("AC97_RSTn", reset_n)
        counts = bench.counts_from_now()
        out[f"bclk_reset_n_{reset_n}"] = counts["AC97_BCLK"]
        print(f"AC97_RSTn driven {reset_n}: AC97_BCLK {frequency(counts['AC97_BCLK'])}")
    status = codec_start(bench)
    if status is None:
        print("the codec answers nothing on the link")
        codec_stop(bench)
        return out
    print(f"the link is up; status {status:04x}: " + ", ".join(
        f"{what} {'ready' if status >> bit & 1 else 'NOT ready'}" for bit, what in ((0, "ADC"), (1, "DAC"), (2, "mixer"), (3, "reference"))))
    registers = {index: bench.codec_read(index) for index, _ in CODEC_REGISTERS}
    out["registers"] = {f"{index:02x}": None if value is None else f"{value:04x}" for index, value in registers.items()}
    for index, what in CODEC_REGISTERS:
        value = registers[index]
        print(f"  {index:02x} {what:24} " + ("no answer" if value is None else f"{value:04x}"))
    if registers[0x7C] is not None and registers[0x7E] is not None:
        ident = registers[0x7C] << 16 | registers[0x7E]
        letters = "".join(chr(ident >> shift & 0xFF) for shift in (24, 16, 8))
        print(f"vendor {letters!r}, ID {ident:08x}: {CODECS.get(ident & ~0xF, 'not one this script knows')}")
        out["id"] = f"{ident:08x}"
    bench.codec_write(0x02, 0x9F1F)
    back = bench.codec_read(0x02)
    out["write"] = back == 0x9F1F
    print(f"master volume written 9f1f, reads {'nothing' if back is None else format(back, '04x')}")

    # The tones through the DAC, the mixer and the ADC, with every output muted: PCM into the mixer at
    # 0 dB, the ADC recording the mixer's sum at 0 dB.
    bench.codec_write(0x02, 0x8000)
    bench.codec_write(0x04, 0x8000)
    bench.codec_write(0x18, 0x0808)
    bench.codec_write(0x1A, 0x0505)
    bench.codec_write(0x1C, 0x0000)
    out["loop"] = {}
    for label, left, right in (("silence", False, False), ("both tones", True, True), ("the left tone", True, False),
                               ("the right tone", False, True)):
        bench.codec_link(True, left, right)
        time.sleep(0.2)
        bench.codec_peaks()
        time.sleep(0.5)
        ready, (lmax, lmin), (rmax, rmin), frames = bench.codec_peaks()
        swing = lambda high, low: (high - low) // 2 if high >= low else 0
        out["loop"][label] = {"left": [lmin, lmax], "right": [rmin, rmax], "frames": frames}
        print(f"  {label:15} the ADC's left channel swings {swing(lmax, lmin):5} around {(lmax + lmin) // 2:6}, "
              f"the right {swing(rmax, rmin):5} around {(rmax + rmin) // 2:6}; {frames} frames")
    print("  (a tone is a square wave of 4096 either side of zero; full scale is 32767)")
    codec_stop(bench)
    return out


def command_tone(bench, args):
    seconds = float(args.values[0]) if args.values else 10
    status = codec_start(bench)
    if status is None:
        sys.exit("the codec answers nothing on the link")
    for register in (0x02, 0x04, 0x18):         # master, headphone, PCM out: 12 dB down, not muted
        bench.codec_write(register, 0x0808)
    bench.codec_link(True, True, True)
    print(f"for {seconds:g} s: 1 kHz on the left channel and 500 Hz on the right, at the codec's line and headphone outputs")
    time.sleep(seconds)
    codec_stop(bench)


def test_sw12(bench):
    out = {}
    for address, strap in (("FA23", "RS0"), ("FA24", "RS1")):
        seen = []
        for value in (0, 1, 0, 1):
            bench.drive(address, value)
            time.sleep(0.02)
            seen.append(bench.level(strap))
        bench.release(address)
        follows = seen == [0, 1, 0, 1]
        out[strap] = "follows " + address if follows else f"does not follow {address}: reads {seen}"
        print(f"{strap} with {address} driven 0, 1, 0, 1: reads {seen}: {'tied together' if follows else 'not tied'}")
    return out


def test_rts(bench):
    seen = []
    for value in (1, 0, 1, 0):
        bench.drive("S1_RTS", value)
        time.sleep(0.1)
        seen.append(int(bench.port.cts))
    bench.release("S1_RTS")
    print(f"S1_RTS driven 1, 0, 1, 0: the host's CTS reads {seen}")
    print(f"the host's RTS at the board (S1_CTS) reads {bench.level('HOST_RTS')}; the host has RTS {'asserted' if bench.port.rts else 'off'}")
    return {"cts": seen}


def command_digits(bench, args):
    print("Each segment is lit alone for a moment: A is the top bar, then clockwise B to F, G the middle bar, P the point.")
    for digit, word in (("left", 1), ("right", 0)):
        for at, segment in enumerate("ABCDEFGP"):
            value = 1 << at
            bench.digits(value if word else 0, 0 if word else value)
            input(f"  the {digit} digit should show segment {segment} only; Enter for the next ")
    bench.digits()
    print('Back to "L" on the left digit and "r" on the right.')


def command_auto(bench, args):
    results = {}
    for name, test in (("i2c", test_i2c), ("mdio", test_mdio), ("eth", test_eth), ("sd", test_sd),
                       ("audio", test_audio), ("sw12", test_sw12), ("rts", test_rts)):
        print(f"\n==== {name} ====")
        results[name] = test(bench)
    if args.values:
        json.dump(results, open(args.values[0], "w"), indent=1)


COMMANDS = {
    "snapshot": command_snapshot, "pulls": command_pulls, "clocks": command_clocks, "watch": command_watch,
    "buttons": command_buttons, "digits": command_digits, "auto": command_auto, "boot": command_boot, "set": command_set,
    "tone": command_tone,
    "i2c": lambda b, a: test_i2c(b), "mdio": lambda b, a: test_mdio(b), "eth": lambda b, a: test_eth(b),
    "sd": lambda b, a: test_sd(b), "audio": lambda b, a: test_audio(b), "sw12": lambda b, a: test_sw12(b),
    "rts": lambda b, a: test_rts(b), "net": test_net, "sdrw": test_sdrw,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--port", default=os.environ.get("BOARD_PORT"), help="the board's serial port (default $BOARD_PORT)")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("values", nargs="*")
    args = parser.parse_args()
    if args.command == "pulls":
        return command_pulls(None, args)        # two files, and no board
    if not args.port:
        parser.error("no serial port: give -p or set BOARD_PORT")
    COMMANDS[args.command](Bench(args.port), args)


if __name__ == "__main__":
    main()
