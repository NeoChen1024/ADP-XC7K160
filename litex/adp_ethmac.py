#
# Ethernet MAC with DMA for the ADP-XC7K160 (litex/README.md, litex/linux/README.md).
#
# The MAC core of LiteEth (preamble, CRC, padding, gap) with a DMA front end on an AXI4 master, in
# place of the slot memory of LiteEth. The frames go between the MAC and the main RAM without the
# CPU. The AXI master goes to the coherent DMA bus of NaxRiscv.
#
#   rx  MAC -> byte FIFO (4 KiB) -> AXI writes into a buffer that the driver gave.
#       The frame starts 2 bytes after the buffer address, so that its IP header is 4-byte
#       aligned. The engine sums the frame as 16-bit little-endian words from byte 0, for
#       CHECKSUM_COMPLETE. At the end it pushes the length, an error bit and the sum into the
#       completion FIFO. If the byte FIFO has no room for a frame of MAX_FRAME bytes at its start,
#       the engine drops the frame and counts it.
#   tx  The driver pushes descriptors: the address and length of a piece, a bit for the last piece
#       of a frame, and the checksum request of the first piece. The engine reads the pieces with
#       AXI reads, at any byte address, into one of two frame buffers (2 KiB each). It sums the bytes
#       from csum_start, and writes ~sum at csum_start + csum_offset, as CHECKSUM_PARTIAL asks.
#       Then it sends the frame, and counts it in tx_done.
#
# The AXI transfers are single 64-bit beats, one at a time. 100 Mbit/s needs one beat in 64
# cycles at 100 MHz.
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
#               bit 12: checksum; bit 13: UDP (a sum of 0 is sent as 0xffff);
#               bits 23-16: csum_start; bits 31-24: csum_offset
#   tx_room     the number of free descriptor entries
#   tx_done     the number of frames sent, modulo 2**16
#   ev_*        bit 0 rx: a completion is ready (level); bit 1 tx: a frame was sent

from migen import *

from litex.gen import LiteXModule
from litex.soc.interconnect import stream
from litex.soc.interconnect.csr import CSR, CSRStatus, CSRStorage
from litex.soc.interconnect.csr_eventmanager import EventManager, EventSourceLevel, EventSourcePulse

MAX_FRAME  = 1536
DESC_DEPTH = 64
BUF_DEPTH  = 256  # free buffers: 32 ms of frames at 100 Mbit/s
COMP_DEPTH = 256


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

        # # #

        # Admission into the byte FIFO: a whole frame, or nothing.
        fifo = stream.SyncFIFO([("data", 8), ("error", 1)], fifo_depth, buffered=True)
        self.submodules += fifo
        in_frame = Signal()
        dropping = Signal()
        count    = Signal(11)
        room     = Signal()
        self.comb += [
            sink.ready.eq(1),
            room.eq(fifo.level <= fifo_depth - MAX_FRAME - 4),
            fifo.sink.data.eq(sink.data),
            fifo.sink.last.eq(sink.last),
            # A frame longer than MAX_FRAME is cut after MAX_FRAME bytes and its last byte, and
            # gets the error bit.
            fifo.sink.error.eq(sink.error | (count == MAX_FRAME)),
            fifo.sink.valid.eq(sink.valid & Mux(in_frame, ~dropping, room) &
                ((count < MAX_FRAME) | sink.last)),
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
        error  = Signal()
        emit   = Signal()   # word is complete and waits for wfifo
        eof    = Signal()
        start  = Signal()
        take   = Signal()   # a byte goes from the FIFO into word
        sent   = Signal()   # word goes into wfifo

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(fifo.source.valid & self.buf.valid,
                self.buf.ready.eq(1),
                start.eq(1),
                NextState("RUN"),
            ),
        )
        fsm.act("RUN",
            If(emit,
                wfifo.sink.valid.eq(1),
                sent.eq(wfifo.sink.ready),
                If(wfifo.sink.ready & eof, NextState("WAIT")),
            ).Elif(fifo.source.valid,
                fifo.source.ready.eq(1),
                take.eq(1),
            ),
        )
        fsm.act("WAIT",
            If(~wfifo.source.valid & writer.idle,
                comp.valid.eq(1),
                If(comp.ready, NextState("IDLE")),
            ),
        )
        self.comb += [
            wfifo.sink.addr.eq(addr),
            wfifo.sink.data.eq(word),
            wfifo.sink.strb.eq(strb),
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
                error.eq(0),
                emit.eq(0),
                eof.eq(0),
            ),
            If(take,
                Case(k, {i: [word[8*i:8*i + 8].eq(fifo.source.data), strb[i].eq(1)] for i in range(8)}),
                k.eq(k + 1),
                length.eq(length + 1),
                csum.eq(csum + Mux(length[0], fifo.source.data << 8, fifo.source.data)),
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
            ("udp", 1), ("csum_start", 8), ("csum_offset", 8)])
        self.source = source = stream.Endpoint([("data", 8), ("error", 1)])  # To the MAC.
        self.sent   = Signal()  # One cycle for each frame sent.

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
        cstart    = Signal(8)
        coff      = Signal(8)
        first     = Signal(reset=1)  # the next piece starts a frame
        last_desc = Signal()
        fold1     = Signal(17)
        fold2     = Signal(16)
        check     = Signal(16)
        patch     = Signal(11)  # where the checksum goes
        patch_1   = Signal(11)
        sendq     = stream.SyncFIFO([("half", 1), ("length", 11)], 2)
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
        self.comb += self.sent.eq(sent_q)


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
            descq.sink.csum_start.eq(self._tx_ctrl.storage[16:24]),
            descq.sink.csum_offset.eq(self._tx_ctrl.storage[24:32]),
            descq.source.connect(self.tx.desc),
            self._tx_room.status.eq(DESC_DEPTH - descq.level),
        ]
        tx_done = Signal(16)
        self.sync += If(self.tx.sent, tx_done.eq(tx_done + 1))
        self.comb += self._tx_done.status.eq(tx_done)

        self.ev = EventManager()
        self.ev.rx = EventSourceLevel(description="A receive completion is ready.")
        self.ev.tx = EventSourcePulse(description="A frame was sent.")
        self.ev.finalize()
        self.comb += [
            self.ev.rx.trigger.eq(compq.source.valid),
            self.ev.tx.trigger.eq(self.tx.sent),
        ]


class ADPEthMAC(EthDMA):
    """The MAC core of LiteEth with the DMA front end. The CSRs and the events are at the top."""
    def __init__(self, phy, axi):
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
