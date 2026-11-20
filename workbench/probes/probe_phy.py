import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
bus = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
def dump(tag):
    regs = [bus.read(31, r) for r in list(range(0, 7)) + list(range(16, 25))]
    bus.read(31, 1); st = bus.read(31, 1)
    c = b.counts_from_now()
    print(tag, " ".join("----" if r is None else f"{r:04x}" for r in regs), "| status", f"{st:04x}",
          "| RX_CLK", c["MAC_RX_CLK"], "TX_CLK", c["MAC_TX_CLK"], "CRS", c["MAC_CRS"], "LINKSTS", b.level("MAC_PHY_LINKSTS"),
          "PWRDWN pin reads", b.level("PHY_PWRDWN"))
    bus2 = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
dump("as found       ")
for v in (1, 0):
    b.drive("PHY_PWRDWN", v)
    time.sleep(4)
    dump(f"PWRDWN driven {v}")
b.release("PHY_PWRDWN")
time.sleep(4)
dump("PWRDWN let go  ")
lv, lo, hi = b.look()
print({n: lv[n] for n in w.NAMES if w.board.GROUP[n] == "eth"})
