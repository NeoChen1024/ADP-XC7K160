# For the workbench as it was before it could send whole frames (design adb00002): the t command and
# the 128-nibble capture this uses are gone. `workbench.py eth` does what this found out.
import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
bus = w.Mdio(b, "MAC_MDC", "MAC_MDIO")
bus.write(0, 0, 0x6100); time.sleep(1.5)
names = ("MAC_RX_DV", "MAC_RX_ER", "MAC_CRS", "MAC_COL", "MAC_RXD0", "MAC_RXD1", "MAC_RXD2", "MAC_RXD3")
end = time.monotonic() + 3.2
sent = 0
while time.monotonic() < end:
    b.tell("t"); sent += 1; time.sleep(0.05)
sec, c = b.counts()
print("sent", sent, "in 3.2 s; last whole second:", {n: c[n] for n in names})
time.sleep(2.2)
sec, c = b.counts()
print("quiet second:", {n: c[n] for n in names})
b.tell("a"); time.sleep(0.2); b.tell("t"); time.sleep(0.2)
st, nib = w.capture(b)
print("capture", st, len(nib), nib)
bus.write(0, 0, 0x1200); bus.done()
