import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
bus = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
def dump(tag):
    regs = [bus.read(31, r) for r in list(range(0, 7)) + list(range(16, 19))]
    bus.read(31, 1); st = bus.read(31, 1)
    c = b.counts_from_now()
    lv = b.look()[0]
    print(tag, " ".join("----" if r is None else f"{r:04x}" for r in regs), "| status", f"{st:04x}",
          "| RX_CLK", c["MAC_RX_CLK"], "TX_CLK", c["MAC_TX_CLK"], "CRS", c["MAC_CRS"], "RX_DV", c["MAC_RX_DV"],
          "RXD0", c["MAC_RXD0"], "COL", c["MAC_COL"], "levels", "".join(str(lv[n]) for n in ("MAC_RX_CLK","MAC_TX_CLK","MAC_RX_DV","MAC_RX_ER","MAC_RXD0","MAC_RXD1","MAC_RXD2","MAC_RXD3","MAC_CRS","MAC_COL")))
dump("as found   ")
bus.write(31, 16, 0x0d14 & ~0x0100)
time.sleep(0.5)
dump("RMII bit off")
bus.write(31, 16, 0x0d14 & ~0x0900)
time.sleep(0.5)
dump("and bit 11 off")
bus.done()
