# Workbench: measure the board

`workbench/` is a small design without MIG, and a host script. It answers the question "what is on
this pin". The build is for -1 and takes about four minutes: 5,909 LUT, 19,960 FF and two RAMB18.

Each of 109 pins is one channel. The FPGA samples each channel at 400 MHz. It records the level,
whether the pin was low or high since the last read, and the number of rising edges in each second.
The host reads these through the UART (115200 baud). The host can also drive the small number of pins that the
board gives to the FPGA to drive.

The commands run from the root of the repository:

```sh
sh workbench/build.sh                                   # the products go into ./adp-workbench
cp workbench/prebuilt/*.bit /some/writable/directory/   # or use these (-1, Vivado 2025.2.1); load.sh writes its logs beside the bitstream
sh jtag/load.sh /some/writable/directory/workbench-pullup.bit
export BOARD_PORT=/dev/ttyS0
python3 workbench/workbench.py snapshot up.json         # load the pull-down bitstream and do it again into down.json
python3 workbench/workbench.py pulls up.json down.json  # each pin: driven, or follows the pull
python3 workbench/workbench.py auto                     # I2C, MDIO, Ethernet, SD, audio, SW12, RTS
python3 workbench/workbench.py boot                     # run it directly after a load: first level and first change of each pin
python3 workbench/workbench.py tone 10                  # 1 kHz left and 500 Hz right to the codec outputs, to hear
python3 workbench/workbench.py buttons                  # interactive: push each button in turn
python3 workbench/workbench.py digits                   # interactive: each segment lights in turn
python3 workbench/workbench.py watch 60                 # each change of a pin in 60 seconds
python3 workbench/workbench.py set SD_PWR_ON 0          # drive one drivable pin and hold it (0, 1, or z to release)
python3 workbench/workbench.py net                      # SENDS FRAMES TO THE LAN: the board is a host and talks to this host
python3 workbench/workbench.py sdrw                     # WRITES TO THE SD CARD: read, write, read back and restore blocks
```

- **The two bitstreams have the same routing.** They differ only in the weak pull on the channels
  (`PULLUP` or `PULLDOWN`). If the value of a pin follows the pull, nothing drives the pin. If the
  value is the same in both, something drives the pin, or the board has a stronger pull than the
  FPGA. The workbench also measures pins with a known state, as a check of the
  method: SW1 to SW4, `FA25`, `VRN` and `VRP` of bank 33, and two empty test points.
- The Ethernet tests need the pull-down bitstream (the straps of
  [docs/ethernet.md](../docs/ethernet.md)). The other tests work
  with both.
- Safety limits:
  - A pin with an unknown connection is only read and never driven.
  - The I2C EEPROM is only read.
  - The three control pins of U4 stay high. U17 is not touched.
  - The tests of `auto` do not write to the SD card: they initialize it and read the CID and the
    CSD. Their Ethernet transmission goes only through the loopback of the PHY, and not to the
    line.
- Only two commands change something outside the board. Each must be given explicitly:
  - `net [board IP [host IP [UDP port]]]` sends frames to the LAN. First it sends ARP broadcasts.
    An ARP probe (RFC 5227) finds an address that no station uses. An ARP request then asks for
    the address of the host. All other frames go only to the host that runs the test. The board
    also sends frames that are not answers. For these, the host needs a UDP port that the firewall
    permits and that no program uses (5201 by default). Ping and the UDP echo do not need it.
  - `sdrw [blocks [keep file]]` writes to the SD card, and then writes the original contents back.
    Before the first write, it stores the original contents of the blocks in a file
    (`./sdrw-kept.json` by default). It deletes the file only after all blocks are restored. If
    the test stops in the middle, the original contents are in that file. The test writes block 1.
    On a card with one partition, that is usually the boot sector of the file system.
- `workbench/sim/run.sh` simulates the command protocol with Verilator.
- `workbench/results/` has the measurements of 2026-11-20.
- `workbench/prebuilt/` has the two bitstreams of that date and their build reports (-1, 400 MHz
  sampling, setup slack +0.092 ns).
- The FPGA sends and receives the Ethernet frames:
  - `m` and `n` put a frame into the buffer and send it. The FPGA adds the preamble and the FCS,
    and can send the frame up to 65535 times in sequence.
  - `q` sets the MAC address of the board, and whether it receives only frames to that address.
  - `a`, `e` and `o` release the receiver, read the status and the counters, and read the one frame
    that the receiver holds.
  - The host script does the layers above (ARP, ICMP, UDP) through the UART. Thus it is slow, and
    the receiver holds only one frame: while it holds one, it only counts the frames that arrive.
- `workbench/probes/` has the one-time scripts that found some conclusions of `docs/ethernet.md`
  and `docs/sd-card.md` (the straps of the PHY, the loopback, the SD card). The usage is
  `python3 workbench/probes/probe_strap.py [port]`. They are kept so that the work can be done
  again; they are not tools. `probe_loop.py` and `probe_loop2.py` use the commands of an earlier
  design (`adb00002`) and do not run on the present bitstream.
- The start of `rtl/adp_xc7k160_workbench.sv` gives the command protocol. `board.py` is the channel
  table, and also makes the XDC.
