# For the workbench as it was before it could send whole frames (design adb00002): the t command and
# the 128-nibble capture this uses are gone. `workbench.py eth` does what this found out.
import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
bus = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
P = 0
def state(tag):
    regs = [bus.read(P, r) for r in (0, 1, 16, 17, 18, 21)]
    print(tag, " ".join("----" if r is None else f"{r:04x}" for r in regs))
def burst(tag):
    b.counts_from_now()
    first, _ = b.counts()
    b.tell("a"); time.sleep(0.1)
    for _ in range(5):
        b.tell("t"); time.sleep(0.05)
    while True:
        time.sleep(0.2)
        sec, c = b.counts()
        if (sec - first) % 256 >= 2: break
    st, nib = w.capture(b)
    print(tag, {n: c[n] for n in ("MAC_TX_CLK", "MAC_RX_CLK", "MAC_RX_DV", "MAC_CRS", "MAC_COL", "MAC_RXD0", "MAC_RX_ER")}, "capture", st, nib[:40])
state("found     ")
for value in (0x6100, 0x4100, 0x6000, 0x5100):
    bus.write(P, 0, value); time.sleep(1.5)
    state(f"wrote {value:04x}")
    burst(f"      {value:04x}")
bus.write(P, 0, 0x1200)
time.sleep(4); state("restored  ")
bus.done()
