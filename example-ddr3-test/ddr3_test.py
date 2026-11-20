#!/usr/bin/env python3
"""Reads the ADP-XC7K160's DDR3 test, rtl/adp_xc7k160_ddr3_test.sv, on the board's serial port, and
sends it commands. Build and load the test first: build.sh, then ../jtag/load.sh.

    ddr3_test.py watch [seconds]     show the state as it changes
    ddr3_test.py check [resets]      an injected error must be counted; then reset MIG that many
                                     times, and each calibration must be followed by two passes
                                     over all of the memory with no error
    ddr3_test.py inject [count]      inject errors one after another: each must be counted once

The port is $BOARD_PORT or -p. A pass writes and reads back 1 GiB, about a second and a half at DDR3-800.
"""
import argparse
import os
import sys
import time

import serial

# The status line: "D ", these fields in hex, CR LF.
FIELDS = (("flags", 1), ("resets", 2), ("calib_ms", 4), ("rx_count", 2), ("rx_last", 2), ("passes", 4), ("phase", 1),
          ("position", 7), ("error_bursts", 8), ("error_mask", 8), ("first_error", 7))
PHASES = {0: "waiting", 1: "writing", 2: "reading"}
UI_ALIVE, UI_RST, CALIBRATED, SYS_RST_N = 8, 4, 2, 1
BURSTS = 1 << 25


def decode(line):
    if not line.startswith("D ") or len(line) != 2 + sum(digits for _, digits in FIELDS):
        return None
    try:
        out, at = {}, 2
        for name, digits in FIELDS:
            out[name] = int(line[at:at + digits], 16)
            at += digits
        return out
    except ValueError:
        return None


class Board:
    def __init__(self, path):
        self.port = serial.Serial(path, 115200, timeout=0.2)
        # A CH347 hands over what it took in while the port was closed, and then stays that far behind
        # for good; a change of bit rate empties it. Harmless for any other adapter.
        for rate in (9600, 115200):
            self.port.baudrate = rate
            time.sleep(0.1)
        self.port.reset_input_buffer()
        self.buffer = b""

    def lines(self, seconds):
        """Status lines, for this long."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.buffer += self.port.read(256)
            while b"\n" in self.buffer:
                raw, self.buffer = self.buffer.split(b"\n", 1)
                status = decode(raw.decode("ascii", "replace").strip())
                if status:
                    yield status

    def wait_for(self, test, seconds):
        return next((status for status in self.lines(seconds) if test(status)), None)

    def send(self, command):
        self.port.write(command)


def describe(status):
    flags = status["flags"]
    return (f"ui_clk {'alive' if flags & UI_ALIVE else 'DEAD'}  ui_rst {flags >> 2 & 1}  calibrated {flags >> 1 & 1}  "
            f"resets {status['resets']}  calibration {status['calib_ms']} ms  passes {status['passes']}  "
            f"{PHASES.get(status['phase'], '?')} {100 * status['position'] / BURSTS:5.1f}%  "
            f"errors {status['error_bursts']}  mask {status['error_mask']:08x}  first {status['first_error']:#x}")


def watch(board, seconds):
    last = None
    for status in board.lines(seconds):
        shown = (status["flags"], status["resets"], status["passes"], status["error_bursts"], status["error_mask"])
        if shown != last:
            print(time.strftime("%H:%M:%S"), describe(status))
        last = shown
    if last is None:
        sys.exit("no status line: is the test loaded?")


def check(board, resets):
    failed = 0
    before = board.wait_for(lambda s: s["phase"] == 2, 10)
    if before is None:
        sys.exit("no status line from a read phase: is the test loaded, and calibrated?")
    board.send(b"e")
    after = board.wait_for(lambda s: s["error_bursts"] != before["error_bursts"], 10)
    if after is None or after["error_bursts"] != before["error_bursts"] + 1:
        failed += 1
        print("the injected error was not counted once:", after and describe(after))
    else:
        print(f"an injected error is counted: mask {after['error_mask']:08x}")

    times = []
    for number in range(resets):
        old = next(board.lines(2))
        board.send(b"r")
        # A line on its way when the reset lands still carries the old run: see the new one start first.
        started = board.wait_for(lambda s: s["resets"] != old["resets"] and s["passes"] == 0 and s["flags"] & CALIBRATED, 10)
        done = started and board.wait_for(lambda s: s["flags"] & CALIBRATED and s["passes"] >= 2, 30)
        if done is None:
            failed += 1
            print(f"reset {number}: no calibration and two passes in time")
        elif done["error_bursts"]:
            failed += 1
            print(f"reset {number}: {describe(done)}")
        else:
            times.append(done["calib_ms"])
    if times:
        print(f"{len(times)} of {resets} calibrations good, in {min(times)} to {max(times)} ms, two clean passes after each")
    sys.exit(1 if failed else 0)


def inject(board, count):
    first = next(board.lines(5), None)
    if first is None:
        sys.exit("no status line: is the test loaded?")
    last = first
    for _ in range(count):
        # One a pass and a bit: an injection is taken by the next burst read, so two in one write phase are one.
        board.wait_for(lambda s: s["phase"] == 2, 10)
        board.send(b"e")
        last = board.wait_for(lambda s: s["phase"] == 1, 10) or last
    last = next(board.lines(2), last)
    counted = last["error_bursts"] - first["error_bursts"]
    print(f"{count} injected, {counted} counted, over {last['passes'] - first['passes']} passes")
    sys.exit(0 if counted == count else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--port", default=os.environ.get("BOARD_PORT"), help="the board's serial port (default $BOARD_PORT)")
    parser.add_argument("command", choices=["watch", "check", "inject"])
    parser.add_argument("count", nargs="?", type=float, help="seconds for watch (10), resets for check (10), errors for inject (10)")
    args = parser.parse_args()
    if not args.port:
        parser.error("no serial port: give -p or set BOARD_PORT")
    board = Board(args.port)
    count = 10 if args.count is None else args.count
    if args.command == "watch":
        watch(board, count)
    elif args.command == "check":
        check(board, int(count))
    else:
        inject(board, int(count))


if __name__ == "__main__":
    main()
