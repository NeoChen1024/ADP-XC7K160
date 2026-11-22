#
# Ethernet MAC with DMA for the ADP-XC7K160 (litex/README.md, litex/linux/README.md).
#
# The MAC core of LiteEth (preamble, CRC, padding, gap) with a DMA front end on an AXI4 master, in
# place of the slot memory of LiteEth. The frames go between the MAC and the main RAM without the
# CPU. The AXI master goes to the coherent DMA bus of NaxRiscv.
#
#   rx  MAC -> byte FIFO (4 KiB) -> address filter -> AXI writes into a buffer that the driver gave.
#       The frame starts 2 bytes after the buffer address, so that its IP header is 4-byte
#       aligned. After the frame, at the next 8-byte boundary, the engine writes the time of the
#       SFD of the frame: the nanoseconds in bytes 0-3, the low 32 bits of the seconds in bytes 4-7.
#       The engine sums the frame as 16-bit little-endian words from byte 0, for
#       CHECKSUM_COMPLETE. At the end it pushes the length, an error bit and the sum into the
#       completion FIFO. If the byte FIFO has no room for a frame of MAX_FRAME bytes at its start,
#       the engine drops the frame and counts it. A frame that the filter refuses gets no
#       completion, and its buffer stays for the next frame.
#   tx  The driver pushes descriptors: the address and length of a piece, a bit for the last piece
#       of a frame, and the checksum request of the first piece. The engine reads the pieces with
#       AXI reads, at any byte address, into one of two frame buffers (2 KiB each). It sums the bytes
#       from csum_start, and writes ~sum at csum_start + csum_offset, as CHECKSUM_PARTIAL asks.
#       Then it sends the frame, and counts it in tx_done. If the first piece asks for a
#       timestamp, the time of the SFD of the frame goes into the TX timestamp FIFO.
#
# The AXI transfers are single 64-bit beats, one at a time. 100 Mbit/s needs one beat in 64
# cycles at 100 MHz.
#
# PTP clock: seconds (48 bits) and nanoseconds, incremented by `incr` (nanoseconds with 32
# fraction bits) in each cycle of sys. The SFD events of the MII come from ADPEthPHYMII. The
# clock stamps each one with a resolution of a quarter cycle of sys: a cycle of sys4x.
#
# CSRs (the Linux driver litex/linux/adp-ethmac.c depends on their order):
#   rx_buf      write: push the address of a free buffer (8-byte aligned, MAX_FRAME + 16 bytes)
#   rx_bufs     the number of free buffers that the engine holds
#   rx_stat     bit 31: a completion is ready; bit 16: error; bits 10-0: length
#   rx_csum     the sum of the completion
#   rx_pop      write: remove the completion
#   rx_dropped  the number of frames dropped for lack of room
#   tx_addr     the address of the next piece
#   tx_ctrl     write: push a descriptor with tx_addr. Bits 10-0: length; bit 11: last piece;
#               bit 12: checksum; bit 13: UDP (a sum of 0 is sent as 0xffff); bit 14: timestamp;
#               bits 23-16: csum_start; bits 31-24: csum_offset
#   tx_room     the number of free descriptor entries
#   tx_done     the number of frames sent, modulo 2**16
#   ev_*        bit 0 rx: a completion is ready (level); bit 1 tx: a frame was sent
#   core_*      the CSRs of the MAC core of LiteEth
# Then, in ADPEthMAC only:
#   ptp_*       the PTP clock (PTPClock)
#   txts_*      the TX timestamp FIFO
#   filt_*      the address filter
#   rxdiag_*    counters of the MII receive path (MIIRXAligned)

from functools import reduce
from operator import xor

from migen import *
from migen.genlib.cdc import MultiReg, PulseSynchronizer

from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSR, CSRStatus, CSRStorage
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel, EventSourcePulse

MAX_FRAME  = 1536
DESC_DEPTH = 64
BUF_DEPTH  = 256  # free buffers: 32 ms of frames at 100 Mbit/s
COMP_DEPTH = 256
TS_DEPTH   = 64   # timestamps of the frames in the byte FIFO
QUARTERS   = (0, 3, 2, 1)  # EventStamp: from the simulation, see its docstring
N_ADDR     = 4    # exact addresses of the filter


def crc32_le_byte(crc, byte):
    """One byte of the CRC-32 of Ethernet, bit-reflected and without the final inversion: the
    ether_crc_le() of Linux. crc and byte are migen values; returns the next crc."""
    state = [{("c", i)} for i in range(32)]
    for j in range(8):
        fb = state[0] ^ {("d", j)}
        state = [state[i + 1] if i < 31 else set() for i in range(32)]
        for i in range(32):
            if (0xedb88320 >> i) & 1:
                state[i] = state[i] ^ fb
    return Cat(*[reduce(xor, [crc[i] if k == "c" else byte[i] for k, i in sorted(s)]) for s in state])


# MII PHY with a receive path aligned on the SFD ---------------------------------------------------

class MIIRXAligned(LiteXModule):
    """MII receive (in eth_rx) that makes the bytes from the nibble after the SFD.

    LiteEthPHYMIIRX pairs the nibbles from the first nibble with RX_DV. If the PHY gives an even
    count of preamble nibbles, all bytes of the frame are wrong. This path waits for the nibble D
    after one or more nibbles 5, gives the MAC core the byte 0xd5, and then the bytes of the frame.
    A burst without SFD gives no byte. A frame that ends with half a byte loses that nibble.
    """
    def __init__(self, pads):
        self.source = source = stream.Endpoint([("data", 8), ("be", 1), ("error", 1)])
        self.sfd       = Signal()   # toggles at each SFD
        self.no_sfd    = Signal()   # pulse: a burst ended without SFD
        self.bad_pre   = Signal()   # pulse: a nibble other than 5 before the SFD
        self.dribble   = Signal()   # pulse: a frame ended with half a byte
        self.pre_len   = Signal(5)  # nibbles 5 before the last SFD (saturates at 31)
        self.burst_len = Signal(8)  # nibbles of the last burst without SFD (saturates at 255)

        # # #

        dv = Signal()
        d  = Signal(4)
        self.sync += [dv.eq(pads.rx_dv), d.eq(pads.rx_data)]

        npre = Signal(5)
        nb   = Signal(8)
        lo   = Signal(4)
        half = Signal()
        held = Signal(8)
        have = Signal()
        self.comb += source.be.eq(1)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(nb, 1),
            NextValue(npre, 1),
            If(dv,
                If(d == 0x5,
                    NextState("PRE"),
                ).Else(
                    self.bad_pre.eq(1),
                    NextState("SKIP"),
                ),
            ),
        )
        fsm.act("PRE",
            If(nb != 255, NextValue(nb, nb + 1)),
            If(~dv,
                self.no_sfd.eq(1),
                NextValue(self.burst_len, nb),
                NextState("IDLE"),
            ).Elif(d == 0x5,
                If(npre != 31, NextValue(npre, npre + 1)),
            ).Elif(d == 0xd,
                NextValue(self.sfd, ~self.sfd),
                NextValue(self.pre_len, npre),
                source.valid.eq(1),
                source.data.eq(0xd5),
                NextValue(half, 0),
                NextValue(have, 0),
                NextState("DATA"),
            ).Else(
                self.bad_pre.eq(1),
                NextState("SKIP"),
            ),
        )
        # A byte goes out when the next one is complete, so that the end of RX_DV can mark the
        # last one.
        fsm.act("DATA",
            If(dv,
                NextValue(half, ~half),
                If(~half,
                    NextValue(lo, d),
                ).Else(
                    NextValue(held, Cat(lo, d)),
                    NextValue(have, 1),
                    If(have,
                        source.valid.eq(1),
                        source.data.eq(held),
                    ),
                ),
            ).Else(
                self.dribble.eq(half),
                source.valid.eq(1),
                source.last.eq(1),
                source.data.eq(held),
                source.error.eq(~have),
                NextState("IDLE"),
            ),
        )
        fsm.act("SKIP",
            If(~dv, NextState("IDLE")),
        )


class MIITXSFD(LiteXModule):
    """Toggles sfd (in eth_tx) at the edge that puts the SFD nibble on TXD."""
    def __init__(self, en, data):
        self.sfd = Signal()

        # # #

        in_pre = Signal(reset=1)
        self.sync += If(~en,
            in_pre.eq(1),
        ).Elif(in_pre & (data == 0xd),
            in_pre.eq(0),
            self.sfd.eq(~self.sfd),
        )


class ADPEthPHYMII(LiteXModule):
    """LiteEthPHYMII with MIIRXAligned in place of LiteEthPHYMIIRX, and the SFD events.

    The CSRs are the CSRs of LiteEthPHYMII: crg_reset, mdio_w, mdio_r.
    """
    dw            = 8
    tx_clk_freq   = 25e6
    rx_clk_freq   = 25e6
    tx_gap_cycles = 24  # 96 bit times in nibbles, as LiteEthPHYMII

    def __init__(self, clock_pads, pads, with_hw_init_reset=True):
        from liteeth.phy.common import LiteEthPHYMDIO
        from liteeth.phy.mii import LiteEthPHYMIICRG, LiteEthPHYMIITX
        self.crg = LiteEthPHYMIICRG(clock_pads, pads, with_hw_init_reset)
        self.tx  = ClockDomainsRenamer("eth_tx")(LiteEthPHYMIITX(pads))
        self.rx  = ClockDomainsRenamer("eth_rx")(MIIRXAligned(pads))
        self.sink, self.source = self.tx.sink, self.rx.source
        # The pads of TXD and TX_EN take the output of the converter at the next edge.
        self.tx_sfd = ClockDomainsRenamer("eth_tx")(
            MIITXSFD(self.tx.converter.source.valid, self.tx.converter.source.data))
        if hasattr(pads, "mdc"):
            self.mdio = LiteEthPHYMDIO(pads)


# PTP clock ----------------------------------------------------------------------------------------

class PTPClock(LiteXModule):
    """Seconds (48 bits) and nanoseconds in sys, and the quarter of the sys cycle in sys4x.

    ctrl (write): bit 0 copies the time into snap_*; bit 1 loads set_*; bit 2 adds adj (signed
    nanoseconds, less than 10**9 in magnitude). incr_hi:incr is the increment of each cycle in
    nanoseconds with 32 fraction bits; incr_nom_hi:incr_nom is its value for sys_clk_freq.
    """
    def __init__(self, sys_clk_freq):
        nom = round((10**9 << 32) / sys_clk_freq)
        assert nom < 2**40
        self.sec  = Signal(48)
        self.ns   = Signal(30)
        self.incr = Signal(40)
        self.ph   = Signal(2)   # sys4x: the quarter of the sys cycle (see EventStamp)

        self._ctrl        = CSR(3)
        self._snap_sec_hi = CSRStatus(16)
        self._snap_sec    = CSRStatus(32)
        self._snap_ns     = CSRStatus(32)
        self._set_sec_hi  = CSRStorage(16)
        self._set_sec     = CSRStorage(32)
        self._set_ns      = CSRStorage(32)
        self._adj         = CSRStorage(32)
        self._incr_hi     = CSRStorage(8,  reset=nom >> 32)
        self._incr        = CSRStorage(32, reset=nom & 0xffffffff)
        self._incr_nom_hi = CSRStatus(8,   reset=nom >> 32)
        self._incr_nom    = CSRStatus(32,  reset=nom & 0xffffffff)

        # # #

        latch = Signal()
        load  = Signal()
        adj   = Signal()
        self.comb += [
            latch.eq(self._ctrl.re & self._ctrl.r[0]),
            load.eq(self._ctrl.re & self._ctrl.r[1]),
            adj.eq(self._ctrl.re & self._ctrl.r[2]),
            self.incr.eq(Cat(self._incr.storage, self._incr_hi.storage)),
        ]

        # The carry of the fraction goes into the nanoseconds one cycle later.
        frac  = Signal(32)
        carry = Signal()
        fsum  = Signal(33)
        delta = Signal((32, True))
        nsum  = Signal((33, True))
        self.comb += [
            fsum.eq(frac + self.incr[:32]),
            If(adj, delta.eq(self._adj.storage)),
            nsum.eq(self.ns + self.incr[32:] + carry + delta),
        ]
        self.sync += [
            If(load,
                self.sec.eq(Cat(self._set_sec.storage, self._set_sec_hi.storage)),
                self.ns.eq(self._set_ns.storage),
                frac.eq(0),
                carry.eq(0),
            ).Else(
                frac.eq(fsum[:32]),
                carry.eq(fsum[32]),
                If(nsum < 0,
                    self.ns.eq(nsum + 10**9),
                    self.sec.eq(self.sec - 1),
                ).Elif(nsum >= 10**9,
                    self.ns.eq(nsum - 10**9),
                    self.sec.eq(self.sec + 1),
                ).Else(
                    self.ns.eq(nsum),
                ),
            ),
            If(latch,
                self._snap_sec_hi.status.eq(self.sec[32:]),
                self._snap_sec.status.eq(self.sec[:32]),
                self._snap_ns.status.eq(self.ns),
            ),
        ]

        # The quarter: sys4x samples a bit that toggles in each sys cycle (the clocks come from one
        # MMCM, with aligned edges).
        tog = Signal()
        t1  = Signal()
        t2  = Signal()
        self.sync += tog.eq(~tog)
        self.sync.sys4x += [
            t1.eq(tog),
            t2.eq(t1),
            If(t1 != t2, self.ph.eq(1)).Else(self.ph.eq(self.ph + 1)),
        ]


class EventStamp(LiteXModule):
    """The time of the edges of an asynchronous toggle, with a resolution of a sys4x cycle.

    valid pulses in sys; sec and ns are the stamp, a constant time after the edge of the toggle
    plus 0 to 1 cycle of sys4x (the synchronizer). quarters[p] is the count of sys4x cycles to
    subtract for the quarter p of the capture; the simulation of litex/adp_ethmac.py gives it.
    """
    def __init__(self, clock, toggle, quarters=QUARTERS):
        self.valid = Signal()
        self.sec   = Signal(48)
        self.ns    = Signal(30)

        # # #

        s      = Signal()
        s_d    = Signal()
        cap_t  = Signal()
        cap_ph = Signal(2)
        self.specials += MultiReg(toggle, s, "sys4x")
        self.sync.sys4x += [
            s_d.eq(s),
            If(s != s_d,
                cap_t.eq(~cap_t),
                cap_ph.eq(clock.ph),
            ),
        ]

        # sys4x changes what sys samples only at the edges between two sys edges: ph is 2 just
        # before an edge of sys. Thus no value goes from sys4x to sys at an edge of both.
        out_t  = Signal()
        out_ph = Signal(2)
        self.sync.sys4x += If(clock.ph != 2, out_t.eq(cap_t), out_ph.eq(cap_ph))

        c1  = Signal()
        c2  = Signal()
        self.p1 = p1 = Signal(2)
        m   = Signal(2)
        self.new = new = Signal()
        sec1 = Signal(48)
        ns1  = Signal(30)
        sub  = Signal(42)
        self.comb += m.eq(Array(Constant(q, 2) for q in quarters)[p1])
        self.sync += [
            c1.eq(out_t),
            p1.eq(out_ph),
            c2.eq(c1),
            new.eq(c1 != c2),
            sec1.eq(clock.sec),
            ns1.eq(clock.ns),
            sub.eq(Mux(m[0], clock.incr, 0) + Mux(m[1], clock.incr << 1, 0)),
        ]
        diff = Signal((32, True))
        self.comb += diff.eq(ns1 - sub[34:])
        self.sync += [
            self.valid.eq(new),
            If(new,
                If(diff < 0,
                    self.ns.eq(diff + 10**9),
                    self.sec.eq(sec1 - 1),
                ).Else(
                    self.ns.eq(diff),
                    self.sec.eq(sec1),
                ),
            ),
        ]


# AXI single-beat writer and reader ----------------------------------------------------------------

class AXIWriter(LiteXModule):
    """One 64-bit write at a time. sink.ready comes with the write response."""
    def __init__(self, axi):
        self.sink = sink = stream.Endpoint([("addr", 32), ("data", 64), ("strb", 8)])
        self.idle = Signal()

        aw_done = Signal()
        w_done  = Signal()
        aw_ok   = Signal()
        w_ok    = Signal()
        self.comb += [
            axi.aw.addr.eq(sink.addr),
            axi.aw.len.eq(0),
            axi.aw.size.eq(3),
            axi.aw.burst.eq(1),
            axi.w.data.eq(sink.data),
            axi.w.strb.eq(sink.strb),
            axi.w.last.eq(1),
            aw_ok.eq(aw_done | (axi.aw.valid & axi.aw.ready)),
            w_ok.eq(w_done | (axi.w.valid & axi.w.ready)),
        ]
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            self.idle.eq(~sink.valid),
            NextValue(aw_done, 0),
            NextValue(w_done, 0),
            If(sink.valid, NextState("REQ")),
        )
        fsm.act("REQ",
            axi.aw.valid.eq(~aw_done),
            axi.w.valid.eq(~w_done),
            NextValue(aw_done, aw_ok),
            NextValue(w_done, w_ok),
            If(aw_ok & w_ok, NextState("RESP")),
        )
        fsm.act("RESP",
            axi.b.ready.eq(1),
            If(axi.b.valid,
                sink.ready.eq(1),
                NextState("IDLE"),
            ),
        )


class AXIReader(LiteXModule):
    """One 64-bit read at a time. sink.ready comes with source.ready, when the data is taken."""
    def __init__(self, axi):
        self.sink   = sink   = stream.Endpoint([("addr", 32)])
        self.source = source = stream.Endpoint([("data", 64)])

        self.comb += [
            axi.ar.addr.eq(sink.addr),
            axi.ar.len.eq(0),
            axi.ar.size.eq(3),
            axi.ar.burst.eq(1),
            source.data.eq(axi.r.data),
        ]
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid, NextState("AR")),
        )
        fsm.act("AR",
            axi.ar.valid.eq(1),
            If(axi.ar.ready, NextState("R")),
        )
        fsm.act("R",
            source.valid.eq(axi.r.valid),
            axi.r.ready.eq(source.ready),
            If(axi.r.valid & source.ready,
                sink.ready.eq(1),
                NextState("IDLE"),
            ),
        )


# Receive ------------------------------------------------------------------------------------------

class RXEngine(LiteXModule):
    def __init__(self, writer, fifo_depth=4096):
        self.sink    = sink = stream.Endpoint([("data", 8), ("error", 1)])  # From the MAC; always ready.
        self.buf     = stream.Endpoint([("addr", 32)])                      # Free buffers.
        self.comp    = comp = stream.Endpoint([("length", 11), ("error", 1), ("csum", 32)])
        self.dropped = Signal(32)
        self.ts      = Signal(64)   # the time of the latest SFD: ns in bits 31-0, seconds above

        # The address filter. addr[i] has the first byte of the address in bits 47-40. A
        # multicast address passes if bit crc[31:26] of hash is set, crc = ether_crc_le(6, addr).
        self.promisc  = Signal(reset=1)
        self.allmulti = Signal()
        self.bcast    = Signal(reset=1)
        self.addr     = [Signal(48) for _ in range(N_ADDR)]
        self.addr_en  = Signal(N_ADDR)
        self.hash     = Signal(64)
        self.filtered = Signal(32)

        # # #

        # Admission into the byte FIFO: a whole frame, or nothing. Each admitted frame pushes the
        # time of its SFD: the latest SFD at its first byte (frames are much longer than the delay
        # from the SFD to the first byte).
        fifo = stream.SyncFIFO([("data", 8), ("error", 1)], fifo_depth, buffered=True)
        tsq  = stream.SyncFIFO([("ts", 64)], TS_DEPTH, buffered=True)
        self.submodules += fifo, tsq
        in_frame = Signal()
        dropping = Signal()
        count    = Signal(11)
        room     = Signal()
        self.comb += [
            sink.ready.eq(1),
            room.eq((fifo.level <= fifo_depth - MAX_FRAME - 4) & tsq.sink.ready),
            fifo.sink.data.eq(sink.data),
            fifo.sink.last.eq(sink.last),
            # A frame longer than MAX_FRAME is cut after MAX_FRAME bytes and its last byte, and
            # gets the error bit.
            fifo.sink.error.eq(sink.error | (count == MAX_FRAME)),
            fifo.sink.valid.eq(sink.valid & Mux(in_frame, ~dropping, room) &
                ((count < MAX_FRAME) | sink.last)),
            tsq.sink.valid.eq(sink.valid & ~in_frame & room),
            tsq.sink.ts.eq(self.ts),
        ]
        self.sync += If(sink.valid,
            If(~in_frame,
                dropping.eq(~room),
                If(~room, self.dropped.eq(self.dropped + 1)),
            ),
            in_frame.eq(~sink.last),
            If(sink.last,
                count.eq(0),
            ).Elif(count != MAX_FRAME,
                count.eq(count + 1),
            ),
        )

        # Bytes -> 64-bit words with byte enables -> writer.
        wfifo = stream.SyncFIFO([("addr", 32), ("data", 64), ("strb", 8)], 4)
        self.submodules += wfifo
        self.comb += wfifo.source.connect(writer.sink)

        addr   = Signal(32)
        word   = Signal(64)
        strb   = Signal(8)
        k      = Signal(3)
        length = Signal(11)
        csum   = Signal(32)
        crc    = Signal(32)
        error  = Signal()
        emit   = Signal()   # word is complete and waits for wfifo
        eof    = Signal()
        start  = Signal()
        take   = Signal()   # a byte goes from the FIFO into word
        sent   = Signal()   # word goes into wfifo
        trail  = Signal()   # the timestamp goes into wfifo

        # The decision of the filter, with the 6th byte of the frame.
        byte    = fifo.source.data
        dst     = Cat(byte, word[48:56], word[40:48], word[32:40], word[24:32], word[16:24])
        crc_n   = Signal(32)
        accept  = Signal()
        mcast   = Signal()
        bcast   = Signal()
        hit     = Signal()
        match   = Signal(N_ADDR)
        self.comb += [
            crc_n.eq(crc32_le_byte(crc, byte)),
            mcast.eq(word[16]),
            bcast.eq(dst == 2**48 - 1),
            hit.eq(Array(self.hash[i] for i in range(64))[crc_n[26:]]),
            match.eq(Cat(*[self.addr_en[i] & (self.addr[i] == dst) for i in range(N_ADDR)])),
            accept.eq(self.promisc |
                (bcast & self.bcast) |
                (mcast & ~bcast & (self.allmulti | hit)) |
                (~mcast & (match != 0))),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        # The buffer leaves the free list only with the completion.
        fsm.act("IDLE",
            If(fifo.source.valid & self.buf.valid,
                start.eq(1),
                NextState("RUN"),
            ),
        )
        fsm.act("RUN",
            If(emit,
                wfifo.sink.valid.eq(1),
                sent.eq(wfifo.sink.ready),
                If(wfifo.sink.ready & eof, NextState("TRAIL")),
            ).Elif(fifo.source.valid,
                fifo.source.ready.eq(1),
                take.eq(1),
                If((length == 5) & ~accept,
                    If(fifo.source.last,
                        NextState("DROP"),
                    ).Else(
                        NextState("DISCARD"),
                    ),
                ),
            ),
        )
        fsm.act("TRAIL",
            trail.eq(1),
            wfifo.sink.valid.eq(1),
            If(wfifo.sink.ready,
                tsq.source.ready.eq(1),
                NextState("WAIT"),
            ),
        )
        fsm.act("WAIT",
            If(~wfifo.source.valid & writer.idle,
                comp.valid.eq(1),
                If(comp.ready,
                    self.buf.ready.eq(1),
                    NextState("IDLE"),
                ),
            ),
        )
        fsm.act("DISCARD",
            fifo.source.ready.eq(1),
            If(fifo.source.valid & fifo.source.last, NextState("DROP")),
        )
        fsm.act("DROP",
            tsq.source.ready.eq(1),
            NextValue(self.filtered, self.filtered + 1),
            NextState("IDLE"),
        )
        self.comb += [
            wfifo.sink.addr.eq(addr),
            wfifo.sink.data.eq(Mux(trail, tsq.source.ts, word)),
            wfifo.sink.strb.eq(Mux(trail, 0xff, strb)),
            comp.length.eq(length),
            comp.error.eq(error),
            comp.csum.eq(csum),
        ]
        self.sync += [
            If(start,
                addr.eq(self.buf.addr),
                strb.eq(0),
                k.eq(2),       # 2 bytes before the frame
                length.eq(0),
                csum.eq(0),
                crc.eq(0xffffffff),
                error.eq(0),
                emit.eq(0),
                eof.eq(0),
            ),
            If(take,
                Case(k, {i: [word[8*i:8*i + 8].eq(byte), strb[i].eq(1)] for i in range(8)}),
                k.eq(k + 1),
                length.eq(length + 1),
                csum.eq(csum + Mux(length[0], byte << 8, byte)),
                crc.eq(crc_n),
                error.eq(error | fifo.source.error),
                If(fifo.source.last | (k == 7),
                    emit.eq(1),
                    eof.eq(fifo.source.last),
                ),
            ),
            If(sent,
                addr.eq(addr + 8),
                strb.eq(0),
                emit.eq(0),
            ),
        ]


# Transmit -----------------------------------------------------------------------------------------

class TXEngine(LiteXModule):
    def __init__(self, reader):
        self.desc   = desc = stream.Endpoint([("addr", 32), ("length", 11), ("eof", 1), ("csum", 1),
            ("udp", 1), ("ts", 1), ("csum_start", 8), ("csum_offset", 8)])
        self.source = source = stream.Endpoint([("data", 8), ("error", 1)])  # To the MAC.
        self.sent   = Signal()  # One cycle for each frame sent.
        self.start  = Signal()  # One cycle for each frame that goes to the MAC.
        self.start_ts = Signal()  # With start: the frame asks for a timestamp.

        # # #

        mem = Memory(8, 4096)
        wp  = mem.get_port(write_capable=True)
        rp  = mem.get_port()
        self.specials += mem, wp, rp

        # Gather: pieces -> frame buffer `half`.
        half      = Signal()
        inflight  = Signal(2)   # frames in the buffers, not sent completely
        wptr      = Signal(11)
        waddr     = Signal(29)  # word address of the next read
        j         = Signal(3)   # byte in the word
        left      = Signal(11)
        rword     = Signal(64)
        sum_      = Signal(32)
        do_csum   = Signal()
        udp       = Signal()
        want_ts   = Signal()
        cstart    = Signal(8)
        coff      = Signal(8)
        first     = Signal(reset=1)  # the next piece starts a frame
        last_desc = Signal()
        fold1     = Signal(17)
        fold2     = Signal(16)
        check     = Signal(16)
        patch     = Signal(11)  # where the checksum goes
        patch_1   = Signal(11)
        sendq     = stream.SyncFIFO([("half", 1), ("length", 11), ("ts", 1)], 2)
        self.submodules += sendq
        sent_q    = Signal()

        self.comb += [
            fold1.eq(sum_[:16] + sum_[16:]),
            fold2.eq(fold1[:16] + fold1[16]),
            check.eq(Mux(udp & (fold2 == 0xffff), 0xffff, ~fold2)),
            patch.eq(cstart + coff),
            patch_1.eq(patch + 1),
            reader.sink.addr.eq(Cat(Constant(0, 3), waddr)),
        ]

        self.gfsm = gfsm = FSM(reset_state="IDLE")
        gfsm.act("IDLE",
            If(desc.valid & (~first | (inflight != 2)),
                NextValue(waddr, desc.addr[3:]),
                NextValue(j, desc.addr[:3]),
                NextValue(left, desc.length),
                NextValue(last_desc, desc.eof),
                If(first,
                    NextValue(do_csum, desc.csum),
                    NextValue(udp, desc.udp),
                    NextValue(want_ts, desc.ts),
                    NextValue(cstart, desc.csum_start),
                    NextValue(coff, desc.csum_offset),
                    NextValue(wptr, 0),
                    NextValue(sum_, 0),
                    NextValue(first, 0),
                ),
                desc.ready.eq(1),
                If(desc.length == 0,
                    If(desc.eof, NextState("PATCH0")),
                ).Else(
                    NextState("READ"),
                ),
            ),
        )
        gfsm.act("READ",
            reader.sink.valid.eq(1),
            reader.source.ready.eq(1),
            If(reader.source.valid,
                NextValue(rword, reader.source.data),
                NextState("UNPACK"),
            ),
        )
        byte = Signal(8)
        self.comb += Case(j, {i: byte.eq(rword[8*i:8*i + 8]) for i in range(8)})
        gfsm.act("UNPACK",
            wp.adr.eq(Cat(wptr, half)),
            wp.dat_w.eq(byte),
            wp.we.eq(1),
            NextValue(wptr, wptr + 1),
            NextValue(left, left - 1),
            NextValue(j, j + 1),
            If(wptr >= cstart,
                NextValue(sum_, sum_ + Mux((wptr - cstart)[0], byte, byte << 8)),
            ),
            If(left == 1,
                If(last_desc,
                    NextState("PATCH0"),
                ).Else(
                    NextState("IDLE"),
                ),
            ).Elif(j == 7,
                NextValue(waddr, waddr + 1),
                NextState("READ"),
            ),
        )
        # The checksum, big-endian, at csum_start + csum_offset.
        gfsm.act("PATCH0",
            If(do_csum,
                wp.adr.eq(Cat(patch, half)),
                wp.dat_w.eq(check[8:]),
                wp.we.eq(1),
            ),
            NextState("PATCH1"),
        )
        gfsm.act("PATCH1",
            If(do_csum,
                wp.adr.eq(Cat(patch_1, half)),
                wp.dat_w.eq(check[:8]),
                wp.we.eq(1),
            ),
            NextState("QUEUE"),
        )
        gfsm.act("QUEUE",
            sendq.sink.valid.eq(1),
            sendq.sink.half.eq(half),
            sendq.sink.length.eq(wptr),
            sendq.sink.ts.eq(want_ts),
            If(sendq.sink.ready,
                NextValue(half, ~half),
                NextValue(first, 1),
                NextState("IDLE"),
            ),
        )
        self.sync += inflight.eq(inflight + (sendq.sink.valid & sendq.sink.ready) - sent_q)

        # Send: frame buffer -> MAC. The read port has one cycle of latency.
        rptr = Signal(11)
        self.sfsm = sfsm = FSM(reset_state="IDLE")
        sfsm.act("IDLE",
            rp.adr.eq(Cat(Constant(0, 11), sendq.source.half)),
            NextValue(rptr, 0),
            If(sendq.source.valid & (sendq.source.length != 0),
                self.start.eq(1),
                NextState("SEND"),
            ).Elif(sendq.source.valid,
                sendq.source.ready.eq(1),
                sent_q.eq(1),
            ),
        )
        sfsm.act("SEND",
            source.valid.eq(1),
            source.data.eq(rp.dat_r),
            source.last.eq(rptr == sendq.source.length - 1),
            rp.adr.eq(Cat(Mux(source.ready, rptr + 1, rptr)[:11], sendq.source.half)),
            If(source.ready,
                NextValue(rptr, rptr + 1),
                If(source.last,
                    sendq.source.ready.eq(1),
                    sent_q.eq(1),
                    NextState("IDLE"),
                ),
            ),
        )
        self.comb += [
            self.sent.eq(sent_q),
            self.start_ts.eq(sendq.source.ts),
        ]


# DMA front end with its CSRs ----------------------------------------------------------------------

class EthDMA(LiteXModule):
    """The front end alone, with 8-bit streams to and from a MAC core: rx_sink, tx_source."""
    def __init__(self, axi):
        self.writer = AXIWriter(axi)
        self.reader = AXIReader(axi)
        self.rx     = RXEngine(self.writer)
        self.tx     = TXEngine(self.reader)
        self.rx_sink   = self.rx.sink
        self.tx_source = self.tx.source
        self.tx_count  = Signal(16)

        self._rx_buf     = CSRStorage(32, description="Write: push the address of a free buffer.")
        self._rx_bufs    = CSRStatus(16,  description="Free buffers that the engine holds.")
        self._rx_stat    = CSRStatus(32,  description="Bit 31 ready, bit 16 error, bits 10-0 length.")
        self._rx_csum    = CSRStatus(32,  description="Sum of the frame of the completion.")
        self._rx_pop     = CSR(1)
        self._rx_dropped = CSRStatus(32,  description="Frames dropped for lack of room.")
        self._tx_addr    = CSRStorage(32, description="Address of the next piece.")
        self._tx_ctrl    = CSRStorage(32, description="Write: push a descriptor with tx_addr.")
        self._tx_room    = CSRStatus(8,   description="Free descriptor entries.")
        self._tx_done    = CSRStatus(16,  description="Frames sent, modulo 2**16.")

        # # #

        bufq = stream.SyncFIFO([("addr", 32)], BUF_DEPTH, buffered=True)
        compq = stream.SyncFIFO([("length", 11), ("error", 1), ("csum", 32)], COMP_DEPTH, buffered=True)
        descq = stream.SyncFIFO(self.tx.desc.description, DESC_DEPTH, buffered=True)
        self.submodules += bufq, compq, descq
        self.comb += [
            bufq.sink.valid.eq(self._rx_buf.re),
            bufq.sink.addr.eq(self._rx_buf.storage),
            bufq.source.connect(self.rx.buf),
            self._rx_bufs.status.eq(bufq.level),

            self.rx.comp.connect(compq.sink),
            self._rx_stat.status.eq(Cat(compq.source.length, Constant(0, 5), compq.source.error,
                Constant(0, 14), compq.source.valid)),
            self._rx_csum.status.eq(compq.source.csum),
            compq.source.ready.eq(self._rx_pop.re),
            self._rx_dropped.status.eq(self.rx.dropped),

            descq.sink.valid.eq(self._tx_ctrl.re),
            descq.sink.addr.eq(self._tx_addr.storage),
            descq.sink.length.eq(self._tx_ctrl.storage[0:11]),
            descq.sink.eof.eq(self._tx_ctrl.storage[11]),
            descq.sink.csum.eq(self._tx_ctrl.storage[12]),
            descq.sink.udp.eq(self._tx_ctrl.storage[13]),
            descq.sink.ts.eq(self._tx_ctrl.storage[14]),
            descq.sink.csum_start.eq(self._tx_ctrl.storage[16:24]),
            descq.sink.csum_offset.eq(self._tx_ctrl.storage[24:32]),
            descq.source.connect(self.tx.desc),
            self._tx_room.status.eq(DESC_DEPTH - descq.level),
        ]
        self.sync += If(self.tx.sent, self.tx_count.eq(self.tx_count + 1))
        self.comb += self._tx_done.status.eq(self.tx_count)

        self.ev = EventManager()
        self.ev.rx = EventSourceLevel(description="A receive completion is ready.")
        self.ev.tx = EventSourcePulse(description="A frame was sent.")
        self.ev.finalize()
        self.comb += [
            self.ev.rx.trigger.eq(compq.source.valid),
            self.ev.tx.trigger.eq(self.tx.sent),
        ]


# CSRs after the MAC core --------------------------------------------------------------------------

class TXTimestamps(LiteXModule):
    """The TX timestamp FIFO. stat: bit 31 an entry is ready, bits 15-0 the frame (its tx_done
    value before it was sent). sec, ns: its time. pop (write): remove it. lost: entries lost
    because the FIFO was full."""
    def __init__(self, depth=16):
        self.push  = Signal()
        self.index = Signal(16)
        self.sec   = Signal(32)
        self.ns    = Signal(30)

        self._stat = CSRStatus(32)
        self._sec  = CSRStatus(32)
        self._ns   = CSRStatus(32)
        self._pop  = CSR(1)
        self._lost = CSRStatus(32)

        # # #

        q = stream.SyncFIFO([("index", 16), ("sec", 32), ("ns", 30)], depth, buffered=True)
        self.submodules += q
        self.comb += [
            q.sink.valid.eq(self.push),
            q.sink.index.eq(self.index),
            q.sink.sec.eq(self.sec),
            q.sink.ns.eq(self.ns),
            self._stat.status.eq(Cat(q.source.index, Constant(0, 15), q.source.valid)),
            self._sec.status.eq(q.source.sec),
            self._ns.status.eq(q.source.ns),
            q.source.ready.eq(self._pop.re),
        ]
        self.sync += If(self.push & ~q.sink.ready, self._lost.status.eq(self._lost.status + 1))


class RXFilterCSRs(LiteXModule):
    """ctrl: bit 0 promiscuous, bit 1 all multicast, bit 2 broadcast, bits 7-4 the addresses
    that are on. addrN_hi: bytes 0 and 1 of address N (byte 0 in bits 15-8); addrN: bytes 2 to 5
    (byte 2 in bits 31-24). hash_hi:hash: the bits of the multicast hash. dropped: frames that the
    filter refused."""
    def __init__(self, rx):
        self._ctrl = CSRStorage(8, reset=0b101)
        for i in range(N_ADDR):
            setattr(self, f"_addr{i}_hi", CSRStorage(16, name=f"addr{i}_hi"))
            setattr(self, f"_addr{i}",    CSRStorage(32, name=f"addr{i}"))
        self._hash_hi = CSRStorage(32)
        self._hash    = CSRStorage(32)
        self._dropped = CSRStatus(32)

        # # #

        self.comb += [
            rx.promisc.eq(self._ctrl.storage[0]),
            rx.allmulti.eq(self._ctrl.storage[1]),
            rx.bcast.eq(self._ctrl.storage[2]),
            rx.addr_en.eq(self._ctrl.storage[4:4 + N_ADDR]),
            rx.hash.eq(Cat(self._hash.storage, self._hash_hi.storage)),
            self._dropped.status.eq(rx.filtered),
        ]
        for i in range(N_ADDR):
            self.comb += rx.addr[i].eq(Cat(getattr(self, f"_addr{i}").storage,
                                           getattr(self, f"_addr{i}_hi").storage))


class RXDiagCSRs(LiteXModule):
    """Counters of MIIRXAligned: bursts without SFD, preambles with a wrong nibble, frames with
    half a byte at the end. last: bits 4-0 the nibbles 5 before the last SFD, bits 15-8 the
    nibbles of the last burst without SFD."""
    def __init__(self, rx):
        self._no_sfd  = CSRStatus(32)
        self._bad_pre = CSRStatus(32)
        self._dribble = CSRStatus(32)
        self._last    = CSRStatus(16)

        # # #

        for name, pulse in (("no_sfd", rx.no_sfd), ("bad_pre", rx.bad_pre), ("dribble", rx.dribble)):
            ps = PulseSynchronizer("eth_rx", "sys")
            self.submodules += ps
            counter = getattr(self, f"_{name}").status
            self.comb += ps.i.eq(pulse)
            self.sync += If(ps.o, counter.eq(counter + 1))
        pre_len   = Signal(5)
        burst_len = Signal(8)
        self.specials += MultiReg(rx.pre_len, pre_len), MultiReg(rx.burst_len, burst_len)
        self.comb += self._last.status.eq(Cat(pre_len, Constant(0, 3), burst_len))


class ADPEthMAC(EthDMA):
    """The MAC core of LiteEth with the DMA front end, the PTP clock and the address filter. The
    CSRs and the events of the front end are at the top. phy is an ADPEthPHYMII."""
    def __init__(self, phy, axi, sys_clk_freq):
        from liteeth.mac.core import LiteEthMACCore
        EthDMA.__init__(self, axi)
        self.core = LiteEthMACCore(phy=phy, dw=8, with_preamble_crc=True)
        rx, tx = self.core.source, self.core.sink
        self.comb += [
            self.rx_sink.valid.eq(rx.valid),
            self.rx_sink.last.eq(rx.last),
            self.rx_sink.data.eq(rx.data),
            self.rx_sink.error.eq(rx.error),
            rx.ready.eq(self.rx_sink.ready),
            tx.valid.eq(self.tx_source.valid),
            tx.last.eq(self.tx_source.last),
            tx.data.eq(self.tx_source.data),
            tx.be.eq(1),
            tx.error.eq(0),
            self.tx_source.ready.eq(tx.ready),
        ]

        # PTP clock and the stamps of the SFDs.
        self.ptp = PTPClock(sys_clk_freq)
        self.rx_stamp = EventStamp(self.ptp, phy.rx.sfd)
        self.tx_stamp = EventStamp(self.ptp, phy.tx_sfd.sfd)
        self.sync += If(self.rx_stamp.valid,
            self.rx.ts.eq(Cat(self.rx_stamp.ns, Constant(0, 2), self.rx_stamp.sec[:32])),
        )

        # TX timestamps: each frame that goes to the MAC core gives one SFD, in order.
        self.txts = TXTimestamps()
        pend = stream.SyncFIFO([("ts", 1), ("index", 16)], 4)
        self.submodules += pend
        self.comb += [
            pend.sink.valid.eq(self.tx.start),
            pend.sink.ts.eq(self.tx.start_ts),
            pend.sink.index.eq(self.tx_count),
            pend.source.ready.eq(self.tx_stamp.valid),
            self.txts.push.eq(self.tx_stamp.valid & pend.source.valid & pend.source.ts),
            self.txts.index.eq(pend.source.index),
            self.txts.sec.eq(self.tx_stamp.sec[:32]),
            self.txts.ns.eq(self.tx_stamp.ns),
        ]

        self.filt   = RXFilterCSRs(self.rx)
        self.rxdiag = RXDiagCSRs(phy.rx)
