import os, sys, time
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import workbench as w
PORT = next((a for a in sys.argv[1:] if a.startswith("/dev/")), os.environ.get("BOARD_PORT", "/dev/ttyS0"))
b = w.Bench(PORT)
def cmd(index, arg, read_bytes):
    frame = bytes([0x40 | index]) + arg.to_bytes(4, "big")
    frame += bytes([w.crc7(frame) << 1 | 1])
    b.drive("SD_CMD", 1)
    for x in frame: b.byte(x)
    b.release("SD_CMD")
    return bytes(b.byte(0xFF) for _ in range(read_bytes))
for power in (1, 0):
    b.drive("SD_PWR_ON", power); time.sleep(1.0)
    b.release("SD_DAT3")
    b.clocked("SD_CLK", "SD_CMD", "SD_CMD")
    b.drive("SD_CLK", 0); b.drive("SD_CMD", 1)
    for _ in range(12): b.byte(0xFF)
    r0 = cmd(0, 0, 2)
    r8 = cmd(8, 0x1AA, 10)
    r55 = cmd(55, 0, 10)
    r41 = cmd(41, 0x40FF8000, 10)
    print(f"power {power}: after CMD0 {r0.hex()} | CMD8 {r8.hex()} | CMD55 {r55.hex()} | ACMD41 {r41.hex()}")
    b.release("SD_CLK", "SD_CMD")
b.release("SD_PWR_ON")
