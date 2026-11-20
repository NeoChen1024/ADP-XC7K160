import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
bus = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
found = [p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)]
print("answers at", found)
P = found[0]
def state(tag):
    regs = [bus.read(P, r) for r in (0, 1, 4, 5, 16, 17, 18)]
    c = b.counts_from_now(); lv = b.look()[0]
    print(tag, " ".join(f"{r:04x}" for r in regs), "RX_CLK", c["MAC_RX_CLK"], "TX_CLK", c["MAC_TX_CLK"], "LINKSTS", lv["MAC_PHY_LINKSTS"], "COL", lv["MAC_COL"])
state("as loaded        ")
bus.write(P, 0, 0x8000); time.sleep(1.0)
found = [p for p in range(32) if bus.read(p, 2) not in (None, 0xFFFF)]
print("after a software reset, answers at", found)
P = found[0]
state("after soft reset ")
bus.write(P, 16, 0x0414); bus.write(P, 0, 0x1200)
for n in range(3):
    time.sleep(3); state(f"MII, not isolated, {3 * n + 3} s")
bus.done()
