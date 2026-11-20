#!/usr/bin/env python3
"""What switching the SD card's supply does to the board's two resets. SD_PWR_ON is driven low, then
high, a few times, with the supply left off for a different time each round; after each edge PORn and
SYS_RSTn are looked at.

    probe_sd_power.py [port]
"""
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w  # noqa: E402
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))

bench = w.Bench(PORT)
print(f"SD_CDn reads {bench.level('SD_CDn')} (0 with a card in the socket)")
bench.drive("SD_PWR_ON", 1)
time.sleep(1.0)
bench.look()
for off in (0.02, 0.2, 1.0, 3.0):
    for value, wait in ((0, off), (1, 1.0)):
        bench.drive("SD_PWR_ON", value)
        time.sleep(wait)
        level, low, high = bench.look()
        resets = [name for name in ("PORn", "SYS_RSTn") if low[name]]
        print(f"SD_PWR_ON driven {value}, then {wait:g} s: " + (" and ".join(resets) + " went low" if resets else "no reset"))
bench.release("SD_PWR_ON")
