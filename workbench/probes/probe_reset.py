#!/usr/bin/env python3
"""Does HW_RST_SW1 reset the Ethernet PHY? A register of the PHY is given a value it does not have after
a reset, then the button is waited for, and the register read again.

    probe_reset.py [port] [seconds to wait]
"""
import os
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w  # noqa: E402
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))

MARK = 0x0081           # what the PHY advertises: 100BASE-TX half duplex alone. A reset makes it 01e1.
wait = next((float(a) for a in sys.argv[1:] if a.replace(".", "").isdigit()), 60.0)

bench = w.Bench(PORT)
bus = w.Mdio(bench, "MAC_MDC", "MAC_MDIO")
phy = next((p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)), None)
if phy is None:
    sys.exit("no PHY answers")
bus.write(phy, 4, MARK)
print(f"PHY at address {phy}: register 4 written {MARK:04x}, reads {bus.read(phy, 4):04x}")
print(f"press and release HW_RST_SW1 within {wait:g} s ...")
bench.look()
end = time.monotonic() + wait
pressed = False
while time.monotonic() < end and not pressed:
    time.sleep(0.1)
    level, low, high = bench.look()
    pressed = bool(low["SYS_RSTn"])
if not pressed:
    print("SYS_RSTn did not go low")
else:
    time.sleep(1.5)
    after = [p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)]
    print(f"SYS_RSTn went low. The PHY now answers at {after}; register 4 reads "
          + ", ".join(f"{bus.read(p, 4):04x}" for p in after)
          + f": {'the PHY was reset' if after and bus.read(after[0], 4) != MARK else 'the PHY was NOT reset'}")
if phy in [p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)]:
    bus.write(phy, 4, 0x01E1)
bus.done()
