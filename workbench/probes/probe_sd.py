import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
for n in ("SD_CLK", "SD_CMD", "SD_DAT3", "SD_PWR_ON"):
    got = []
    for v in (0, 1, 0, 1):
        b.drive(n, v); time.sleep(0.02); got.append(b.level(n))
    b.release(n); time.sleep(0.02)
    print(n, "driven 0,1,0,1 reads", got, "let go reads", b.level(n))
names = ("SD_CMD", "SD_DAT0", "SD_DAT1", "SD_DAT2", "SD_DAT3", "SD_CDn", "SD_WPn")
for power in (0, 1, 0, 1):
    b.drive("SD_PWR_ON", power); time.sleep(1.0)
    card = w.SdSpi(b)
    card.idle(12)
    b.drive("SD_DAT3", 0)
    frame = bytes([0x40, 0, 0, 0, 0, 0x95])
    back = [b.byte(x) for x in frame]
    after = [b.byte(0xFF) for _ in range(12)]
    b.drive("SD_DAT3", 1); b.byte(0xFF)
    print(f"power {power}: while sending", bytes(back).hex(), "after", bytes(after).hex(), {n: b.level(n) for n in names})
    card.done()
b.release("SD_PWR_ON")
