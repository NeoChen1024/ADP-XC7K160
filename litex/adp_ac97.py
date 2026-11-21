#
# AC97 controller for the ALC203 codec of the ADP-XC7K160 (docs/audio.md, litex/README.md).
#
# The codec drives BIT_CLK (12.288 MHz). The controller sends SYNC and SDATA_OUT on the rising
# edge, and samples SDATA_IN on the falling edge. A frame has 256 bits: slot 0 (the tag, 16 bits)
# and slots 1 to 12 (20 bits each). The controller uses slots 0 to 4: the tag, the command address,
# the command data, and the left and right PCM samples. The rate is 48 kHz (no VRA).
#
# Data: two DMA engines of LiteX in loop mode, on ring buffers in the main RAM. A 32-bit word is
# one stereo frame of S16_LE: the left sample in bits 15:0, the right sample in bits 31:16.
#   playback  WishboneDMAReader -> AsyncFIFO -> slots 3 and 4 of the output frames
#   capture   slots 3 and 4 of the input frames -> AsyncFIFO -> WishboneDMAWriter
# Each engine raises an interrupt after each period (a number of words that the CSR gives).
#
# CSRs (the Linux driver litex/linux/adp-ac97.c depends on their order):
#   ctrl        bit 0 reset: 1 holds the codec in reset (AC97_RSTn low). It is 1 after power-on.
#   cmd         addr (7 bits), data (16 bits), read (1 bit); a write of cmd_go sends it.
#   cmd_go      write 1: send the command in the next frame.
#   status      busy, timeout, codec_ready
#   reply       the data of the last read
#   play_*      the CSRs of the DMA reader (base, length, enable, done, loop, offset, error)
#   play_period the period in words
#   rec_*       the CSRs of the DMA writer
#   rec_period  the period in words
#   ev_*        the interrupt events: bit 0 playback period, bit 1 capture period

from migen import *
from migen.genlib.cdc import MultiReg
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import LiteXModule
from litex.soc.cores.dma import WishboneDMAReader, WishboneDMAWriter
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSR, CSRField, CSRStatus, CSRStorage
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourcePulse

# AC-link (BIT_CLK domain) ------------------------------------------------------------------------

class ACLink(LiteXModule):
    """The frames of the AC-link. All the logic runs in the "ac97" domain (rising edge of BIT_CLK).

    sdi is the SDATA_IN bit that a register sampled at the falling edge before this rising edge.
    """
    def __init__(self):
        self.sync_o = Signal()
        self.sdo    = Signal()
        self.sdi    = Signal()

        self.play = stream.Endpoint([("data", 32)])  # sink: one stereo frame per AC97 frame
        self.rec  = stream.Endpoint([("data", 32)])  # source: one stereo frame per AC97 frame
        self.rec_enable = Signal()

        self.cmd_valid  = Signal()  # level: a command waits
        self.cmd_read   = Signal()
        self.cmd_addr   = Signal(7)
        self.cmd_data   = Signal(16)
        self.cmd_taken  = Signal()  # pulse: the command is in the frame that starts now

        self.status_valid = Signal()  # pulse: an input frame with a valid status address
        self.status_addr  = Signal(7)
        self.status_data  = Signal(16)
        self.codec_ready  = Signal()

        # # #

        self.cnt = cnt = Signal(8)  # the bit that is on the lines now
        nxt = Signal(8)
        out = Signal(96)
        inp = Signal(96)
        self.comb += nxt.eq(cnt + 1)

        # The output frame, MSB first: tag, slot 1, slot 2, slot 3, slot 4.
        send_cmd = self.cmd_valid
        tag = Signal(16)
        self.comb += [
            tag[15].eq(1),                             # valid frame
            tag[14].eq(send_cmd),                      # slot 1: command address
            tag[13].eq(send_cmd & ~self.cmd_read),     # slot 2: command data
            tag[12].eq(1),                             # slot 3: PCM left (zero if no data)
            tag[11].eq(1),                             # slot 4: PCM right
        ]
        slot1 = Mux(send_cmd, Cat(Replicate(0, 12), self.cmd_addr, self.cmd_read), 0)
        slot2 = Mux(send_cmd & ~self.cmd_read, Cat(Replicate(0, 4), self.cmd_data), 0)
        left  = Mux(self.play.valid, self.play.data[0:16],  0)
        right = Mux(self.play.valid, self.play.data[16:32], 0)
        frame = Cat(Replicate(0, 4), right, Replicate(0, 4), left, slot2, slot1, tag)

        start = nxt == 0
        self.comb += [
            self.play.ready.eq(start),
            self.cmd_taken.eq(start & send_cmd),
        ]
        self.sync.ac97 += [
            cnt.eq(nxt),
            # SYNC is high for 16 bits: the last bit of the frame before, and bits 0 to 14.
            self.sync_o.eq((nxt == 255) | (nxt < 15)),
            If(start,
                out.eq(frame),
                self.sdo.eq(frame[95]),
            ).Else(
                out.eq(Cat(0, out[0:95])),
                self.sdo.eq(out[94]),
            ),
            # The bit that was sampled at the falling edge of bit cnt.
            inp.eq(Cat(self.sdi, inp[0:95])),
        ]

        # After the rising edge that ends bit 95, inp holds slots 0 to 4 of the input frame.
        got = Signal()
        in_tag = inp[80:96]
        self.sync.ac97 += got.eq(cnt == 95)
        self.comb += [
            self.status_addr.eq(inp[72:79]),
            self.status_data.eq(inp[44:60]),
            self.status_valid.eq(got & in_tag[15] & in_tag[14]),
            self.rec.data.eq(Cat(inp[24:40], inp[4:20])),
            self.rec.valid.eq(got & self.rec_enable & in_tag[15] & in_tag[12] & in_tag[11]),
        ]
        self.sync.ac97 += If(got, self.codec_ready.eq(in_tag[15]))

# AC97 controller (sys domain) --------------------------------------------------------------------

class AC97(LiteXModule):
    def __init__(self, pads, bus_play, bus_rec, sim=False):
        self.cd_ac97 = ClockDomain()
        self.link = link = ACLink()

        # Control and command CSRs.
        self.ctrl = CSRStorage(fields=[
            CSRField("reset", size=1, reset=1, description="1 holds the codec in reset (AC97_RSTn low)."),
        ])
        self.cmd = CSRStorage(fields=[
            CSRField("addr", size=7,  description="Codec register (an even number)."),
            CSRField("data", size=16, description="Data for a write."),
            CSRField("read", size=1,  description="1: read the register; 0: write it."),
        ])
        self.cmd_go = CSR()
        self.status = CSRStatus(fields=[
            CSRField("busy",        size=1, description="The last command is not complete."),
            CSRField("timeout",     size=1, description="The last read got no reply in 16 frames."),
            CSRField("codec_ready", size=1, description="Bit 15 of slot 0 from the codec."),
        ])
        self.reply = CSRStatus(16, description="Data of the last read.")

        # # #

        reset = self.ctrl.fields.reset

        # BIT_CLK: the pin is not clock capable (K26), so a BUFG takes it from the general routing.
        if sim:
            self.comb += self.cd_ac97.clk.eq(pads.bit_clk)
            sdi_fall = Signal()
            self.cd_ac97n = ClockDomain(reset_less=True)
            self.comb += self.cd_ac97n.clk.eq(~pads.bit_clk)
            self.sync.ac97n += sdi_fall.eq(pads.sdata_in)
        else:
            self.specials += Instance("BUFG", i_I=pads.bit_clk, o_O=self.cd_ac97.clk)
            sdi_fall = Signal()
            self.specials += Instance("FDRE",
                p_INIT         = 0,
                p_IS_C_INVERTED = 1,
                i_C  = self.cd_ac97.clk,
                i_CE = 1,
                i_R  = 0,
                i_D  = pads.sdata_in,
                o_Q  = sdi_fall,
            )
        self.comb += link.sdi.eq(sdi_fall)
        # BIT_CLK stops while the codec is in reset; the domain leaves reset after the clock starts.
        self.specials += AsyncResetSynchronizer(self.cd_ac97, reset)

        # SYNC and SDATA_OUT must be low while AC97_RSTn rises, or the codec enters a test mode.
        self.comb += [
            pads.reset_n.eq(~reset),
            pads.sync.eq(link.sync_o & ~reset),
            pads.sdata_out.eq(link.sdo & ~reset),
        ]

        # Commands: the fields stay stable while busy; a toggle crosses to the BIT_CLK domain.
        busy        = Signal()
        timeout     = Signal()
        req_toggle  = Signal()
        done_toggle = Signal()
        done_sys    = Signal()
        done_prev   = Signal()
        reply_ac97  = Signal(16)
        timeout_ac97 = Signal()
        self.specials += MultiReg(done_toggle, done_sys)
        self.sync += [
            done_prev.eq(done_sys),
            If(self.cmd_go.re & ~busy,
                busy.eq(1),
                req_toggle.eq(~req_toggle),
            ).Elif(done_sys != done_prev,
                busy.eq(0),
                timeout.eq(timeout_ac97),
                self.reply.status.eq(reply_ac97),
            ),
            If(reset, busy.eq(0)),
        ]

        req_ac97  = Signal()
        req_prev  = Signal()
        pending   = Signal()
        awaiting  = Signal()
        wait_left = Signal(5)
        self.specials += MultiReg(req_toggle, req_ac97, "ac97")
        self.comb += [
            link.cmd_valid.eq(pending),
            link.cmd_read.eq(self.cmd.fields.read),
            link.cmd_addr.eq(self.cmd.fields.addr[0:7]),
            link.cmd_data.eq(self.cmd.fields.data),
        ]
        frame_start = Signal()
        self.comb += frame_start.eq(link.play.ready)
        self.sync.ac97 += [
            req_prev.eq(req_ac97),
            If(req_ac97 != req_prev, pending.eq(1)),
            If(link.cmd_taken,
                pending.eq(0),
                If(self.cmd.fields.read,
                    awaiting.eq(1),
                    wait_left.eq(16),
                ).Else(
                    timeout_ac97.eq(0),
                    done_toggle.eq(~done_toggle),
                ),
            ),
            If(awaiting,
                If(link.status_valid & (link.status_addr == self.cmd.fields.addr[0:7]),
                    awaiting.eq(0),
                    reply_ac97.eq(link.status_data),
                    timeout_ac97.eq(0),
                    done_toggle.eq(~done_toggle),
                ).Elif(frame_start,
                    If(wait_left == 0,
                        awaiting.eq(0),
                        timeout_ac97.eq(1),
                        done_toggle.eq(~done_toggle),
                    ).Else(
                        wait_left.eq(wait_left - 1),
                    ),
                ),
            ),
        ]
        codec_ready = Signal()
        self.specials += MultiReg(link.codec_ready, codec_ready)
        self.comb += [
            self.status.fields.busy.eq(busy),
            self.status.fields.timeout.eq(timeout),
            self.status.fields.codec_ready.eq(codec_ready & ~reset),
        ]

        # Playback: DMA reader -> AsyncFIFO -> AC-link.
        self.play = play = WishboneDMAReader(bus_play, with_byteswap=False, fifo_depth=16)
        play.add_csr()
        play_fifo = stream.AsyncFIFO([("data", 32)], depth=8, buffered=False)
        play_fifo = ClockDomainsRenamer({"write": "sys", "read": "ac97"})(play_fifo)
        self.play_fifo = play_fifo
        self.comb += [
            play.source.connect(play_fifo.sink),
            play_fifo.source.connect(link.play),
        ]

        # Capture: AC-link -> AsyncFIFO -> DMA writer. A frame is lost if the FIFO is full.
        self.rec = rec = WishboneDMAWriter(bus_rec, with_byteswap=False)
        rec.add_csr()
        rec_fifo = stream.AsyncFIFO([("data", 32)], depth=8, buffered=False)
        rec_fifo = ClockDomainsRenamer({"write": "ac97", "read": "sys"})(rec_fifo)
        self.rec_fifo = rec_fifo
        rec_enable = Signal()
        self.specials += MultiReg(rec.enable, rec_enable, "ac97")
        self.comb += [
            link.rec_enable.eq(rec_enable),
            link.rec.connect(rec_fifo.sink, omit={"ready"}),
            rec_fifo.source.connect(rec.sink),
        ]

        # Interrupt after each period of each direction.
        self.play_period = CSRStorage(32, description="Playback period in words (stereo frames).")
        self.rec_period  = CSRStorage(32, description="Capture period in words (stereo frames).")
        self.ev = EventManager()
        self.ev.playback = EventSourcePulse(description="A playback period was read from the RAM.")
        self.ev.capture  = EventSourcePulse(description="A capture period was written to the RAM.")
        self.ev.finalize()

        def period_counter(enable, step, period, trigger):
            count = Signal(32)
            self.sync += [
                trigger.eq(0),
                If(~enable,
                    count.eq(0),
                ).Elif(step,
                    If(count + 1 >= period,
                        count.eq(0),
                        trigger.eq(1),
                    ).Else(
                        count.eq(count + 1),
                    ),
                ),
            ]
        play_step = play.sink.valid & play.sink.ready
        rec_step  = rec.sink.valid & rec.sink.ready
        period_counter(play.enable, play_step, self.play_period.storage, self.ev.playback.trigger)
        period_counter(rec.enable,  rec_step,  self.rec_period.storage,  self.ev.capture.trigger)
