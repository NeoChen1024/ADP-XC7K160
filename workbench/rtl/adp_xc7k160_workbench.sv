// A workbench for the ADP-XC7K160: it watches pins, and does to them what the host says, over the
// RS-232 port at 115200 (114943). board.py has the channels, workbench.py the host's side.
//
// Every channel is sampled at 400 MHz, through a synchronizer: its level, whether it has been low
// and whether it has been high since the host last looked, and how often it rose in each second of
// the 20 MHz oscillator. So a clock of up to 150 MHz or so is measured without being used as one, a
// button's bounce is counted, and a pulse of 5 ns or more is not missed. The clocks on dedicated
// inputs are divided by 16 on their own clock first.
//
// Nothing is driven but the three lines that keep the configuration flash deselected, the two
// digits, the audio codec's two inputs and the Ethernet PHY's transmit lines, all of them lines the
// board gives the FPGA to drive, until the host turns a channel's driver on; and only the channels
// in wb::CAN_DRIVE have one. A frame goes to the PHY, and so to the wire, only when the host sends one.
//
// Commands are a letter and a fixed number of hex digits; an answer is hex digits and a line feed.
// A byte that fits nowhere is dropped, so what a port sends when it is opened does no harm.
//
//   i            the design's number and its channel count
//   c II V       channel II: drive it (V bit 1) to V bit 0. An open-drain channel only pulls low
//   g II         channel II's level
//   s            every channel's level, seen low and seen high; the two seen sets start again
//   f            the second's number, then every channel's rises in the last whole second
//   k CC OO II   name a clock channel, a data-out channel and a data-in channel for x and b
//   x HH         eight bits out on OO, most significant first, a clock on CC for each; the bits read
//                on II while the clock was high come back
//   b V          the same for one bit
//   d E HHLL     the digits' segments, lit by a 1, the left digit first, when E is 1
//   m AAA DD     byte AAA of the frame to send
//   n LLL RRRR   send the first LLL bytes of it RRRR times, each with a preamble before and its check
//                sequence after, and the gap between frames that the standard asks for
//   q A DD       the receiver's own address, byte A of six; with A at 6, whether only frames to that
//                address are taken (DD bit 0) or every frame
//   a            let go of the frame that is held: the next one that passes is kept
//   e            the receiver: four bits (0, sending, armed, holding a frame), four of the frame held
//                (0, longer than the 2047 bytes there is room for, damaged, its check sequence right),
//                its length with the check sequence, and three counts since configuration: frames
//                seen, frames that passed and were whole, frames that passed and were not
//   o AAA        32 bytes of the frame held, from byte AAA
//   z II         channel II since the FPGA was configured: four bits (its first level, whether it has
//                risen, whether it has fallen, its level now), then when it first rose and when it
//                first fell, in cycles of the 20 MHz oscillator
//   l V          the audio codec's link: on (V bit 2), a 1 kHz square wave to the left channel (bit 0),
//                500 Hz to the right (bit 1). Off, SYNC and SDATA_OUT are low, as the codec's reset wants
//   w RR DDDD    write the codec's register RR
//   r RR         read the codec's register RR: a 1 if it answered, the register it says it read, the value
//   p            what the codec's converters sent since the last p: two bits (the link answered, the
//                codec says it is ready), the left channel's highest and lowest sample, the right's, and
//                the number of frames with samples
module adp_xc7k160_workbench
    import wb::*;
#(
    parameter int SECOND     = 20_000_000,   // the oscillator's cycles in what the counts are counted over
    parameter int HALF_CLOCK = 20            // a quarter of a bit of x and b, less one cycle: 238 kHz
) (
    input  logic            osc_20mhz,
    input  logic            uart_rx_pin,
    output logic            uart_tx_pin,
    output logic            bpi_cs_n,
    output logic            bpi_oe_n,
    output logic            bpi_we_n,
    output logic [7:0]      digit_high_n,
    output logic [7:0]      digit_low_n,
    output logic            ac97_sync,
    output logic            ac97_sdo,
    output logic [3:0]      mac_txd,
    output logic            mac_tx_en,
    input  logic            sys_clk_p,
    input  logic            sys_clk_n,
    input  logic            ckout1_p,
    input  logic            ckout1_n,
    input  logic            pcie_clk_p,
    input  logic            pcie_clk_n,
    inout  wire  [NPIN-1:0] pin
);
    // The configuration flash stays deselected.
    assign bpi_cs_n = 1'b1;
    assign bpi_oe_n = 1'b1;
    assign bpi_we_n = 1'b1;

    // ---- clocks ----
    logic clk, fast, fast_mmcm, slow, slow_mmcm, feedback;
    BUFG osc_buf (.I(osc_20mhz), .O(clk));
    logic [NCH-1:0] raw;
    MMCME2_BASE #(
        .CLKIN1_PERIOD(50.0), .DIVCLK_DIVIDE(1), .CLKFBOUT_MULT_F(60.0), .CLKOUT0_DIVIDE_F(3.0),
        .CLKOUT1_DIVIDE(12)
    ) fast_mmcm_i (
        .CLKIN1(clk), .CLKFBIN(feedback), .CLKFBOUT(feedback), .CLKFBOUTB(), .CLKOUT0(fast_mmcm), .CLKOUT0B(),
        .CLKOUT1(slow_mmcm), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .LOCKED(), .PWRDWN(1'b0), .RST(1'b0)
    );
    BUFG fast_buf (.I(fast_mmcm), .O(fast));
    BUFG slow_buf (.I(slow_mmcm), .O(slow));        // 100 MHz, its edges on the 400 MHz clock's

    // Each dedicated clock counts on itself; bit 3 is a sixteenth of it.
    logic sys_in, sys_clk, ckout1_in, ckout1_clk, pcie_in, pcie_clk;
    logic [3:0] sys_div = '0, ckout1_div = '0, pcie_div = '0;
    IBUFDS sys_ibuf (.I(sys_clk_p), .IB(sys_clk_n), .O(sys_in));
    BUFG sys_bufg (.I(sys_in), .O(sys_clk));
    IBUFDS_GTE2 ckout1_ibuf (.I(ckout1_p), .IB(ckout1_n), .CEB(1'b0), .O(ckout1_in), .ODIV2());
    BUFG ckout1_bufg (.I(ckout1_in), .O(ckout1_clk));
    IBUFDS_GTE2 pcie_ibuf (.I(pcie_clk_p), .IB(pcie_clk_n), .CEB(1'b0), .O(pcie_in), .ODIV2());
    BUFG pcie_bufg (.I(pcie_in), .O(pcie_clk));
    always_ff @(posedge sys_clk) sys_div <= sys_div + 4'd1;
    always_ff @(posedge ckout1_clk) ckout1_div <= ckout1_div + 4'd1;
    always_ff @(posedge pcie_clk) pcie_div <= pcie_div + 4'd1;

    // ---- the pins ----
    logic [NPIN-1:0] drive = '0, value = '0;
    for (genvar i = 0; i < NPIN; i++) begin : g_pin
        if (CAN_DRIVE[i]) begin : g_driven
            if (OPEN_DRAIN[i]) assign pin[i] = drive[i] && !value[i] ? 1'b0 : 1'bz;
            else               assign pin[i] = drive[i] ? value[i] : 1'bz;
        end
    end

    // ---- sampling, at 400 MHz ----
    assign raw = {pcie_div[3], ckout1_div[3], sys_div[3], pin};
    (* ASYNC_REG = "TRUE" *) logic [NCH-1:0] meta, level;
    logic [NCH-1:0] earlier, rose, seen_low, seen_high;
    logic [27:0]    count [NCH], counted [NCH];

    logic second_toggle = 1'b0, look_toggle = 1'b0;     // from the 20 MHz side
    (* ASYNC_REG = "TRUE" *) logic [2:0] second_sync, look_sync;
    logic second_tick, look_tick;
    always_ff @(posedge fast) begin
        meta        <= raw;
        level       <= meta;
        earlier      <= level;
        rose        <= level & ~earlier;
        second_sync <= {second_sync[1:0], second_toggle};
        look_sync   <= {look_sync[1:0], look_toggle};
        second_tick <= second_sync[2] != second_sync[1];
        look_tick   <= look_sync[2] != look_sync[1];
        if (look_tick) begin
            seen_low  <= ~level;
            seen_high <= level;
        end else begin
            seen_low  <= seen_low | ~level;
            seen_high <= seen_high | level;
        end
    end
    for (genvar i = 0; i < NCH; i++) begin : g_count
        always_ff @(posedge fast) begin
            if (second_tick) begin
                counted[i] <= count[i];
                count[i]   <= 28'(rose[i]);
            end else if (rose[i]) begin
                count[i] <= count[i] + 28'd1;
            end
        end
    end

    // ---- MII: a frame out, and frames in ----
    // What the PHY's two clocks do is seen at 400 MHz, as any channel's level is, and no faster clock
    // is made of them. Receive: the PHY's data is still for 10 ns either side of its clock's rise, and
    // the samples are 2.5 ns apart, so the data in the sample where the clock first reads high is the
    // nibble. Transmit: a MAC's data changes within 25 ns of the PHY's clock rising, and is taken at
    // the next rise, 40 ns on at 100 Mbit/s; the rise is seen here two or three samples late.
    //
    // Only that much is done at 400 MHz. A nibble is 40 ns, so the frames themselves are put together
    // and taken apart on a 100 MHz clock from the same MMCM, whose edges are edges of the fast one: a
    // toggle says that a nibble came, or that one went and the next is wanted.
    logic       rx_rise, tx_rise, rx_tick = 1'b0, tx_tick = 1'b0;
    logic [5:0] rx_now, rx_held;            // RX_ER, RX_DV, RXD
    logic [4:0] tx_next = '0, tx_out = '0;  // TX_EN, TXD: for the next rise, and on the pins
    assign mac_tx_en = tx_out[4];
    assign mac_txd   = tx_out[3:0];
    always_ff @(posedge fast) begin
        rx_rise <= level[CH_MAC_RX_CLK] && !earlier[CH_MAC_RX_CLK];
        tx_rise <= level[CH_MAC_TX_CLK] && !earlier[CH_MAC_TX_CLK];
        rx_now  <= {level[CH_MAC_RX_ER], level[CH_MAC_RX_DV],
                    level[CH_MAC_RXD3], level[CH_MAC_RXD2], level[CH_MAC_RXD1], level[CH_MAC_RXD0]};
        if (rx_rise) begin
            rx_held <= rx_now;
            rx_tick <= !rx_tick;
        end
        if (tx_rise) begin
            tx_out  <= tx_next;
            tx_tick <= !tx_tick;
        end
    end

    // The frame check sequence: the CRC-32 of the frame, a nibble at a time, least significant bit first.
    // A frame taken with its own check sequence leaves CRC_WHOLE.
    localparam logic [31:0] CRC_WHOLE = 32'hDEBB20E3;
    function automatic logic [31:0] crc_nibble(input logic [31:0] crc, input logic [3:0] data);
        logic [31:0] c;
        c = crc;
        for (int i = 0; i < 4; i++) c = {1'b0, c[31:1]} ^ ((c[0] ^ data[i]) ? 32'hEDB88320 : 32'h0);
        return c;
    endfunction

    // From the 20 MHz side, and still when they are used here.
    logic [47:0] cfg_mac = '0;
    logic        cfg_filter = 1'b0;
    logic [10:0] cfg_last = '0;             // the frame to send: its last byte's place
    logic [15:0] cfg_repeat = '0;
    logic        arm_toggle = 1'b0, send_toggle = 1'b0, stat_toggle = 1'b0;
    (* ASYNC_REG = "TRUE" *) logic [2:0] arm_sync, send_sync, stat_sync;

    logic       rx_tick_s = 1'b0, rx_tick_d = 1'b0, tx_tick_s = 1'b0, tx_tick_d = 1'b0, rx_strobe, tx_strobe;
    logic [5:0] rx_s;
    assign rx_strobe = rx_tick_s != rx_tick_d;
    assign tx_strobe = tx_tick_s != tx_tick_d;
    always_ff @(posedge slow) begin
        rx_tick_s <= rx_tick;
        rx_tick_d <= rx_tick_s;
        rx_s      <= rx_held;
        tx_tick_s <= tx_tick;
        tx_tick_d <= tx_tick_s;
        arm_sync  <= {arm_sync[1:0], arm_toggle};
        send_sync <= {send_sync[1:0], send_toggle};
        stat_sync <= {stat_sync[1:0], stat_toggle};
    end

    // Receive. Every frame is counted; one that passes the filter is kept, whole or not, if none is
    // held, and then held until the host lets go of it.
    typedef enum logic [2:0] {RX_QUIET, RX_IDLE, RX_PREAMBLE, RX_DATA, RX_SKIP} rx_state_t;
    rx_state_t   rx_state = RX_QUIET;
    logic [31:0] rx_crc;
    logic [10:0] rx_len;
    logic [3:0]  rx_low;
    logic        rx_half, rx_damaged, rx_over, rx_match, rx_taking;
    logic        armed = 1'b0, holding = 1'b0;
    logic [10:0] held_len = '0;
    logic [2:0]  held_flags = '0;
    logic [31:0] rx_seen = '0, rx_good = '0, rx_bad = '0;
    logic        rx_we = 1'b0, rx_pass, rx_whole;
    logic [10:0] rx_waddr, rx_raddr = '0;
    logic [7:0]  rx_wdata, rx_rq, rx_byte_in, mac_byte;
    logic [7:0]  rx_ram [2048];
    always_ff @(posedge slow) if (rx_we) rx_ram[rx_waddr] <= rx_wdata;
    always_ff @(posedge clk) rx_rq <= rx_ram[rx_raddr];

    assign rx_byte_in = {rx_s[3:0], rx_low};
    assign rx_pass    = !cfg_filter || (rx_match && rx_len >= 11'd6);
    assign rx_whole   = rx_crc == CRC_WHOLE && !rx_damaged && !rx_half && !rx_over;
    always_comb begin
        case (rx_len[2:0])
            3'd0:    mac_byte = cfg_mac[47:40];
            3'd1:    mac_byte = cfg_mac[39:32];
            3'd2:    mac_byte = cfg_mac[31:24];
            3'd3:    mac_byte = cfg_mac[23:16];
            3'd4:    mac_byte = cfg_mac[15:8];
            default: mac_byte = cfg_mac[7:0];
        endcase
    end
    always_ff @(posedge slow) begin
        rx_we <= 1'b0;
        if (rx_strobe) begin
            case (rx_state)
                RX_QUIET: if (!rx_s[4]) rx_state <= RX_IDLE;         // not from the middle of a frame
                RX_IDLE, RX_PREAMBLE:
                    if (!rx_s[4]) begin
                        rx_state <= RX_IDLE;
                    end else if (rx_s[3:0] == 4'h5) begin
                        rx_state <= RX_PREAMBLE;
                    end else if (rx_s[3:0] == 4'hD && rx_state == RX_PREAMBLE) begin
                        rx_state   <= RX_DATA;
                        rx_crc     <= '1;
                        rx_len     <= '0;
                        rx_half    <= 1'b0;
                        rx_damaged <= 1'b0;
                        rx_over    <= 1'b0;
                        rx_match   <= 1'b1;
                        rx_taking  <= armed && !holding;
                    end else begin
                        rx_state <= RX_SKIP;
                    end
                RX_DATA:
                    if (rx_s[4]) begin
                        rx_crc  <= crc_nibble(rx_crc, rx_s[3:0]);
                        rx_half <= !rx_half;
                        if (rx_s[5]) rx_damaged <= 1'b1;
                        if (!rx_half) begin
                            rx_low <= rx_s[3:0];
                        end else begin
                            if (rx_len == 11'd2047) begin
                                rx_over <= 1'b1;
                            end else begin
                                rx_we    <= rx_taking;
                                rx_waddr <= rx_len;
                                rx_wdata <= rx_byte_in;
                                rx_len   <= rx_len + 11'd1;
                            end
                            if (rx_len < 11'd6 && rx_byte_in != mac_byte) rx_match <= 1'b0;
                        end
                    end else begin
                        rx_state <= RX_IDLE;
                        rx_seen  <= rx_seen + 32'd1;
                        if (rx_pass) begin
                            if (rx_whole) rx_good <= rx_good + 32'd1;
                            else          rx_bad  <= rx_bad + 32'd1;
                            if (rx_taking) begin
                                holding    <= 1'b1;
                                held_len   <= rx_len;
                                held_flags <= {rx_over, rx_damaged || rx_half, rx_crc == CRC_WHOLE};
                            end
                        end
                    end
                default: if (!rx_s[4]) rx_state <= RX_IDLE;          // RX_SKIP: what was not a preamble
            endcase
        end
        if (arm_sync[2] != arm_sync[1]) begin
            armed   <= 1'b1;
            holding <= 1'b0;
        end
    end

    // Transmit. What is put on tx_next at a strobe goes to the pins at the PHY clock's next rise.
    typedef enum logic [2:0] {TX_IDLE, TX_PRE, TX_DATA, TX_FCS, TX_GAP} tx_state_t;
    tx_state_t   tx_state = TX_IDLE;
    logic [5:0]  tx_pos;
    logic [10:0] tx_raddr = '0, tx_count;
    logic [15:0] tx_left;
    logic [31:0] tx_crc;
    logic        tx_half, tx_busy = 1'b0;
    logic        tx_we = 1'b0;
    logic [10:0] tx_waddr;
    logic [7:0]  tx_wdata, tx_q;
    logic [3:0]  tx_nibble;
    logic [7:0]  tx_ram [2048];
    always_ff @(posedge clk) if (tx_we) tx_ram[tx_waddr] <= tx_wdata;
    always_ff @(posedge slow) tx_q <= tx_ram[tx_raddr];

    assign tx_nibble = tx_half ? tx_q[7:4] : tx_q[3:0];
    always_ff @(posedge slow) begin
        if (tx_strobe) begin
            case (tx_state)
                TX_PRE: begin
                    tx_next <= {1'b1, tx_pos == 6'd15 ? 4'hD : 4'h5};
                    tx_pos  <= tx_pos + 6'd1;
                    if (tx_pos == 6'd15) begin
                        tx_state <= TX_DATA;
                        tx_crc   <= '1;
                        tx_half  <= 1'b0;
                        tx_count <= '0;
                    end
                end
                TX_DATA: begin
                    tx_next <= {1'b1, tx_nibble};
                    tx_crc  <= crc_nibble(tx_crc, tx_nibble);
                    tx_half <= !tx_half;
                    if (tx_half) begin
                        // The next byte is read in the cycle after this, three cycles before it is wanted.
                        tx_raddr <= tx_raddr + 11'd1;
                        tx_count <= tx_count + 11'd1;
                        if (tx_count == cfg_last) begin
                            tx_state <= TX_FCS;
                            tx_pos   <= '0;
                        end
                    end
                end
                TX_FCS: begin
                    tx_next <= {1'b1, ~tx_crc[3:0]};
                    tx_crc  <= {4'h0, tx_crc[31:4]};
                    tx_pos  <= tx_pos + 6'd1;
                    if (tx_pos == 6'd7) begin
                        tx_state <= TX_GAP;
                        tx_pos   <= '0;
                        tx_raddr <= '0;
                    end
                end
                // 28 nibbles of nothing: the standard wants 24 between frames.
                TX_GAP: begin
                    tx_next <= '0;
                    tx_pos  <= tx_pos + 6'd1;
                    if (tx_pos == 6'd27) begin
                        if (tx_left <= 16'd1) begin
                            tx_state <= TX_IDLE;
                            tx_busy  <= 1'b0;
                        end else begin
                            tx_left  <= tx_left - 16'd1;
                            tx_state <= TX_PRE;
                            tx_pos   <= '0;
                        end
                    end
                end
                default: tx_next <= '0;
            endcase
        end
        if (send_sync[2] != send_sync[1] && !tx_busy) begin
            tx_busy  <= 1'b1;
            tx_state <= TX_PRE;
            tx_pos   <= '0;
            tx_raddr <= '0;
            tx_left  <= cfg_repeat;
        end
    end

    // The receiver's state and counts, taken together when the host asks.
    logic [115:0] stat_hold = '0;
    logic         stat_ack = 1'b0;
    always_ff @(posedge slow) begin
        if (stat_sync[2] != stat_sync[1]) begin
            stat_hold <= {1'b0, tx_busy, armed, holding, 1'b0, held_flags, 1'b0, held_len, rx_seen, rx_good, rx_bad};
            stat_ack  <= !stat_ack;
        end
    end

    // ---- the audio codec's link, at 400 MHz, on the codec's 12.288 MHz bit clock as the samples show it ----
    // A frame is 256 bits, 48000 of them a second. SYNC is high for sixteen bits, from one bit before the
    // frame's first; the tag, a register's address, its data and two samples of 20 bits are the first 96
    // bits each way. Both sides change their line as the clock rises and take the other's as it falls.
    logic [2:0]  ac_ctl = '0;                       // from the 20 MHz side: tone left, tone right, link on
    logic [23:0] ac_command = '0;                   // a read, the register, the data
    logic        ac_request_toggle = 1'b0, ac_peak_toggle = 1'b0;
    (* ASYNC_REG = "TRUE" *) logic [2:0] ac_request_sync, ac_peak_sync;
    (* ASYNC_REG = "TRUE" *) logic [2:0] ac_ctl_meta, ac_ctl_here;

    logic        ac_rise, ac_fall, ac_in;
    logic [7:0]  ac_at = 8'd128;                    // the bit on the lines
    logic [95:0] ac_out = '0, ac_shift = '0, ac_now, ac_frame, ac_got = '0;
    // What the bit on the lines says of the next edge, worked out in the sixteen cycles before it; and a
    // frame that has come in is looked at over the cycles after, with thousands to spare.
    logic        ac_first = 1'b0, ac_last = 1'b0, ac_sync_next = 1'b0, ac_got_valid = 1'b0, ac_match = 1'b0;
    logic        ac_sync_out = 1'b0, ac_sdo_out = 1'b0;
    logic [23:0] ac_cmd = '0;
    logic        ac_pending = 1'b0, ac_awaiting = 1'b0, ac_done_toggle = 1'b0, ac_peak_ack = 1'b0, ac_ready = 1'b0;
    logic [22:0] ac_reply = '0;
    logic [5:0]  tone_left_at = '0;                 // 48 frames a period
    logic [6:0]  tone_right_at = '0;                // 96
    logic [19:0] tone_left, tone_right;
    logic               pcm_left_valid = 1'b0, pcm_right_valid = 1'b0, pcm_frame = 1'b0;
    logic               left_above = 1'b0, left_below = 1'b0, right_above = 1'b0, right_below = 1'b0;
    logic signed [15:0] pcm_left, pcm_right;
    logic signed [15:0] max_left = 16'sh8000, min_left = 16'sh7FFF, max_right = 16'sh8000, min_right = 16'sh7FFF;
    logic [15:0]        ac_frames = '0;
    logic [80:0]        ac_hold = '0;

    assign ac97_sync = ac_sync_out;
    assign ac97_sdo  = ac_sdo_out;
    // An eighth of full scale, so that nothing after the converter clips.
    assign tone_left  = !ac_ctl_here[0] ? 20'd0 : tone_left_at >= 6'd24 ? 20'hF0000 : 20'h10000;
    assign tone_right = !ac_ctl_here[1] ? 20'd0 : tone_right_at >= 7'd48 ? 20'hF0000 : 20'h10000;
    assign ac_frame = {1'b1, ac_pending, ac_pending, 2'b11, 11'b0,
                       ac_cmd[23:16], 12'b0,
                       ac_cmd[23] ? 16'b0 : ac_cmd[15:0], 4'b0,
                       tone_left, tone_right};
    assign ac_now = {ac_shift[94:0], ac_in};
    always_ff @(posedge fast) begin
        ac_request_sync <= {ac_request_sync[1:0], ac_request_toggle};
        ac_peak_sync    <= {ac_peak_sync[1:0], ac_peak_toggle};
        ac_ctl_meta     <= ac_ctl;
        ac_ctl_here     <= ac_ctl_meta;
        ac_rise         <= level[CH_AC97_BCLK] && !earlier[CH_AC97_BCLK];
        ac_fall         <= !level[CH_AC97_BCLK] && earlier[CH_AC97_BCLK];
        ac_in           <= level[CH_AC97_SDI];
        pcm_left_valid  <= 1'b0;
        pcm_right_valid <= 1'b0;
        pcm_frame       <= 1'b0;

        ac_first        <= ac_at == 8'd255;
        ac_last         <= ac_at == 8'd95;
        ac_sync_next    <= ac_at >= 8'd254 || ac_at < 8'd14;
        ac_got_valid    <= 1'b0;
        ac_match        <= ac_got_valid && ac_awaiting && ac_got[95:93] == 3'b111 && ac_got[78:72] == ac_cmd[22:16];

        if (!ac_ctl_here[2]) begin
            ac_at       <= 8'd128;
            ac_sync_out <= 1'b0;
            ac_sdo_out  <= 1'b0;
            ac_awaiting <= 1'b0;
        end else begin
            if (ac_rise) begin
                ac_at       <= ac_at + 8'd1;
                ac_sync_out <= ac_sync_next;
                if (ac_first) begin
                    ac_sdo_out <= ac_frame[95];
                    ac_out     <= {ac_frame[94:0], 1'b0};
                    if (ac_pending) begin
                        ac_pending  <= 1'b0;
                        ac_awaiting <= ac_cmd[23];
                    end
                    tone_left_at  <= tone_left_at == 6'd47 ? 6'd0 : tone_left_at + 6'd1;
                    tone_right_at <= tone_right_at == 7'd95 ? 7'd0 : tone_right_at + 7'd1;
                end else begin
                    ac_sdo_out <= ac_out[95];
                    ac_out     <= {ac_out[94:0], 1'b0};
                end
            end
            if (ac_fall) begin
                ac_shift <= ac_now;
                if (ac_last) begin
                    ac_got       <= ac_now;
                    ac_got_valid <= 1'b1;
                end
            end
            if (ac_got_valid) begin
                ac_ready        <= ac_got[95];
                pcm_left        <= ac_got[39:24];
                pcm_right       <= ac_got[19:4];
                pcm_left_valid  <= ac_got[95] && ac_got[92];
                pcm_right_valid <= ac_got[95] && ac_got[91];
                pcm_frame       <= ac_got[95] && (ac_got[92] || ac_got[91]);
            end
            if (ac_match) begin
                ac_reply       <= {ac_got[78:72], ac_got[59:44]};
                ac_awaiting    <= 1'b0;
                ac_done_toggle <= !ac_done_toggle;
            end
        end
        if (ac_request_sync[2] != ac_request_sync[1]) begin
            ac_cmd     <= ac_command;
            ac_pending <= 1'b1;
        end

        // A sample is compared in one cycle and kept in the next: it stays for a frame, 8000 cycles.
        left_above  <= pcm_left_valid && pcm_left > max_left;
        left_below  <= pcm_left_valid && pcm_left < min_left;
        right_above <= pcm_right_valid && pcm_right > max_right;
        right_below <= pcm_right_valid && pcm_right < min_right;
        if (left_above)  max_left  <= pcm_left;
        if (left_below)  min_left  <= pcm_left;
        if (right_above) max_right <= pcm_right;
        if (right_below) min_right <= pcm_right;
        if (pcm_frame && ac_frames != 16'hFFFF) ac_frames <= ac_frames + 16'd1;
        if (ac_peak_sync[2] != ac_peak_sync[1]) begin
            ac_hold     <= {ac_ready, max_left, min_left, max_right, min_right, ac_frames};
            max_left    <= 16'sh8000;
            min_left    <= 16'sh7FFF;
            max_right   <= 16'sh8000;
            min_right   <= 16'sh7FFF;
            ac_frames   <= '0;
            ac_peak_ack <= !ac_peak_ack;
        end
    end

    // ---- the host's side, on the 20 MHz oscillator ----
    localparam logic [13:0] CYCLES_PER_BIT = 14'd174;    // 20 MHz / 174 = 114943 baud

    logic [3:0] por = '0;
    logic rst = 1'b1;
    always_ff @(posedge clk) begin
        if (por != '1) por <= por + 4'd1;
        rst <= por != '1;
    end

    // The second is counted here; the counts are taken over there a few cycles after its toggle.
    logic [24:0] second_at = '0;
    logic [7:0]  seconds = '0;
    logic        counts_still;
    always_ff @(posedge clk) begin
        if (second_at == 25'(SECOND - 1)) begin
            second_at     <= '0;
            second_toggle <= !second_toggle;
            seconds       <= seconds + 8'd1;
        end else begin
            second_at <= second_at + 25'd1;
        end
        counts_still <= second_at >= 25'd8;
    end

    // Since the FPGA was configured: every channel's first level and the times of its first two changes,
    // which is how a reset that the board gives as the design starts is seen at all. On this clock, which
    // runs from the first moment; the 400 MHz one waits for its MMCM.
    (* ASYNC_REG = "TRUE" *) logic [NCH-1:0] boot_meta, boot_level;
    logic [NCH-1:0] boot_before, boot_first = '0, rose_once = '0, fell_once = '0;
    logic [1:0]     boot_settle = '0;
    logic [31:0]    uptime = '0;
    logic [31:0]    first_rise [NCH], first_fall [NCH];
    always_ff @(posedge clk) begin
        boot_meta   <= raw;
        boot_level  <= boot_meta;
        boot_before <= boot_level;
        if (boot_settle != 2'd3)           boot_settle <= boot_settle + 2'd1;
        else if (uptime != 32'hFFFF_FFFF) uptime <= uptime + 32'd1;
        if (boot_settle == 2'd2) boot_first <= boot_level;
        // One block for all the channels, and the two sets cleared here rather than by their initial
        // value: with a block a channel, Vivado takes a bit that is only ever set for a constant 1.
        for (int i = 0; i < NCH; i++) begin
            if (boot_level[i] && !boot_before[i] && !rose_once[i]) begin
                rose_once[i]  <= 1'b1;
                first_rise[i] <= uptime;
            end
            if (!boot_level[i] && boot_before[i] && !fell_once[i]) begin
                fell_once[i]  <= 1'b1;
                first_fall[i] <= uptime;
            end
        end
        if (boot_settle != 2'd3) begin
            rose_once <= '0;
            fell_once <= '0;
        end
    end

    // What the host waits for from the other clocks: the codec's answer, its peaks, the receiver's state.
    (* ASYNC_REG = "TRUE" *) logic [2:0] ac_done_sync, ac_ack_sync, stat_ack_sync;
    logic        ac_base, ac_came;
    logic [1:0]  ac_which;
    logic [16:0] ac_timeout;
    assign ac_came = (ac_which == 2'd2 ? stat_ack_sync[2] : ac_which == 2'd1 ? ac_ack_sync[2] : ac_done_sync[2]) != ac_base;
    always_ff @(posedge clk) begin
        ac_done_sync  <= {ac_done_sync[1:0], ac_done_toggle};
        ac_ack_sync   <= {ac_ack_sync[1:0], ac_peak_ack};
        stat_ack_sync <= {stat_ack_sync[1:0], stat_ack};
    end

    (* ASYNC_REG = "TRUE" *) logic [NCH-1:0] level_meta, level_here;
    (* ASYNC_REG = "TRUE" *) logic [2:0] rx_sync = '1;
    always_ff @(posedge clk) begin
        level_meta <= level;
        level_here <= level_meta;
        rx_sync    <= {rx_sync[1:0], uart_rx_pin};
    end

    logic       rx_valid, tx_ready;
    logic [7:0] rx_byte;
    uart_rx u_rx (.clk, .rst, .cycles_per_bit(CYCLES_PER_BIT), .rx(rx_sync[2]), .valid(rx_valid), .data(rx_byte));

    function automatic logic is_hex(input logic [7:0] c);
        return (c >= "0" && c <= "9") || (c >= "a" && c <= "f") || (c >= "A" && c <= "F");
    endfunction
    function automatic logic [3:0] from_hex(input logic [7:0] c);
        return c <= "9" ? c[3:0] : c[3:0] + 4'd9;
    endfunction
    function automatic logic [7:0] to_hex(input logic [3:0] v);
        return v < 4'd10 ? 8'h30 + 8'(v) : 8'h57 + 8'(v);
    endfunction

    // An answer is shifted out from the top of one register, a nibble a character.
    localparam int ROUND = 4 * ((NCH + 3) / 4);
    localparam int COUNTS = 8 + 28 * NCH;
    localparam int ANSWER = COUNTS > 524 ? COUNTS : 524;
    localparam int NIBBLES = $clog2(ANSWER / 4 + 1);

    logic [28*NCH-1:0] counts_flat;
    for (genvar i = 0; i < NCH; i++) begin : g_flat
        assign counts_flat[28 * (NCH - 1 - i) +: 28] = counted[i];      // channel 0 first
    end

    typedef enum logic [2:0] {IDLE, ARGS, RUN, CLOCKING, COUNTS_WAIT, CODEC_WAIT, ANSWERING, RAM_READ} state_t;
    state_t             state = IDLE;
    logic [7:0]         command;
    logic [2:0]         args_left;
    logic [27:0]        args;
    logic [5:0]         read_at;
    logic [255:0]       chunk;
    logic [ANSWER-1:0]  answer;
    logic [NIBBLES-1:0] answer_left;
    logic [7:0]         clock_channel = '0, out_channel = '0, in_channel = '0;
    logic [7:0]         shift_out, shift_in;
    logic [3:0]         bits_left;
    logic [1:0]         phase;
    logic [5:0]         wait_left;
    logic               digits_given = 1'b0;
    logic [15:0]        digits = '0;

    logic [7:0] tx_data;
    assign tx_data = answer_left == '0 ? 8'h0A : to_hex(answer[ANSWER-1 -: 4]);
    uart_tx u_tx (.clk, .rst, .cycles_per_bit(CYCLES_PER_BIT), .valid(state == ANSWERING), .data(tx_data),
                  .ready(tx_ready), .tx(uart_tx_pin));

    function automatic logic [2:0] args_of(input logic [7:0] c);
        case (c)
            "c": return 3'd3;
            "g": return 3'd2;
            "k": return 3'd6;
            "x": return 3'd2;
            "b": return 3'd1;
            "d": return 3'd5;
            "z": return 3'd2;
            "l": return 3'd1;
            "w": return 3'd6;
            "r": return 3'd2;
            "m": return 3'd5;
            "n": return 3'd7;
            "q": return 3'd3;
            "o": return 3'd3;
            default: return 3'd0;
        endcase
    endfunction
    function automatic logic known(input logic [7:0] c);
        return c == "i" || c == "c" || c == "g" || c == "s" || c == "f" || c == "k" || c == "x" || c == "b" ||
               c == "d" || c == "a" || c == "e" || c == "z" || c == "l" || c == "w" || c == "r" || c == "p" ||
               c == "m" || c == "n" || c == "q" || c == "o";
    endfunction

    always_ff @(posedge clk) begin
        tx_we <= 1'b0;
        if (rst) begin
            state <= IDLE;
        end else begin
            case (state)
                IDLE: if (rx_valid && known(rx_byte)) begin
                    command   <= rx_byte;
                    args_left <= args_of(rx_byte);
                    state     <= args_of(rx_byte) == 3'd0 ? RUN : ARGS;
                end
                ARGS: if (rx_valid) begin
                    if (!is_hex(rx_byte)) begin
                        state <= IDLE;
                    end else begin
                        args      <= {args[23:0], from_hex(rx_byte)};
                        args_left <= args_left - 3'd1;
                        if (args_left == 3'd1) state <= RUN;
                    end
                end
                RUN: begin
                    state <= ANSWERING;
                    case (command)
                        "i": begin
                            answer[ANSWER-1 -: 40] <= {MAGIC, 8'(NCH)};
                            answer_left            <= NIBBLES'(10);
                        end
                        "c": begin
                            if (args[11:4] < 8'(NPIN)) begin
                                value[args[11:4]] <= args[0];
                                drive[args[11:4]] <= args[1];
                            end
                            state <= IDLE;
                        end
                        "g": begin
                            answer[ANSWER-1 -: 4] <= {3'b000, args[7:0] < 8'(NCH) && level_here[args[7:0]]};
                            answer_left           <= NIBBLES'(1);
                        end
                        "s": begin
                            answer[ANSWER-1 -: 3*ROUND] <= {ROUND'(level_here), ROUND'(seen_low), ROUND'(seen_high)};
                            answer_left                 <= NIBBLES'(3 * ROUND / 4);
                            look_toggle                 <= !look_toggle;
                        end
                        "f": state <= COUNTS_WAIT;
                        "k": begin
                            clock_channel <= args[23:16];
                            out_channel   <= args[15:8];
                            in_channel    <= args[7:0];
                            state         <= IDLE;
                        end
                        "x", "b": begin
                            shift_out <= command == "x" ? args[7:0] : {args[0], 7'b0};
                            bits_left <= command == "x" ? 4'd8 : 4'd1;
                            phase     <= 2'd0;
                            wait_left <= '0;
                            state     <= CLOCKING;
                        end
                        "d": begin
                            digits_given <= args[16];
                            digits       <= args[15:0];
                            state        <= IDLE;
                        end
                        "a": begin
                            arm_toggle <= !arm_toggle;
                            state      <= IDLE;
                        end
                        "m": begin
                            tx_we    <= 1'b1;
                            tx_waddr <= args[18:8];
                            tx_wdata <= args[7:0];
                            state    <= IDLE;
                        end
                        "n": begin
                            if (args[26:16] != '0) begin
                                cfg_last    <= args[26:16] - 11'd1;
                                cfg_repeat  <= args[15:0];
                                send_toggle <= !send_toggle;
                            end
                            state <= IDLE;
                        end
                        "q": begin
                            case (args[11:8])
                                4'd0:    cfg_mac[47:40] <= args[7:0];
                                4'd1:    cfg_mac[39:32] <= args[7:0];
                                4'd2:    cfg_mac[31:24] <= args[7:0];
                                4'd3:    cfg_mac[23:16] <= args[7:0];
                                4'd4:    cfg_mac[15:8]  <= args[7:0];
                                4'd5:    cfg_mac[7:0]   <= args[7:0];
                                4'd6:    cfg_filter     <= args[0];
                                default: ;
                            endcase
                            state <= IDLE;
                        end
                        "o": begin
                            rx_raddr <= args[10:0];
                            read_at  <= '0;
                            state    <= RAM_READ;
                        end
                        "z": begin
                            if (args[7:0] < 8'(NCH))
                                answer[ANSWER-1 -: 68] <= {boot_first[args[7:0]], rose_once[args[7:0]], fell_once[args[7:0]],
                                                           boot_level[args[7:0]], first_rise[args[7:0]], first_fall[args[7:0]]};
                            else
                                answer[ANSWER-1 -: 68] <= '0;
                            answer_left <= NIBBLES'(17);
                        end
                        "l": begin
                            ac_ctl <= args[2:0];
                            state  <= IDLE;
                        end
                        "w": begin
                            ac_command        <= {1'b0, args[22:0]};
                            ac_request_toggle <= !ac_request_toggle;
                            state             <= IDLE;
                        end
                        "r": begin
                            ac_command        <= {1'b1, args[6:0], 16'b0};
                            ac_request_toggle <= !ac_request_toggle;
                            ac_which          <= 2'd0;
                            ac_base           <= ac_done_sync[2];
                            ac_timeout        <= '1;
                            state             <= CODEC_WAIT;
                        end
                        "p": begin
                            ac_peak_toggle <= !ac_peak_toggle;
                            ac_which       <= 2'd1;
                            ac_base        <= ac_ack_sync[2];
                            ac_timeout     <= '1;
                            state          <= CODEC_WAIT;
                        end
                        default: begin      // "e"
                            stat_toggle <= !stat_toggle;
                            ac_which    <= 2'd2;
                            ac_base     <= stat_ack_sync[2];
                            ac_timeout  <= '1;
                            state       <= CODEC_WAIT;
                        end
                    endcase
                end
                // A bit: the data, half a clock; the clock high, half a clock; the level read and the
                // clock low, half a clock.
                CLOCKING: begin
                    if (wait_left != '0) begin
                        wait_left <= wait_left - 6'd1;
                    end else begin
                        wait_left <= 6'(HALF_CLOCK);
                        phase     <= phase + 2'd1;
                        case (phase)
                            2'd0: if (out_channel < 8'(NPIN)) value[out_channel] <= shift_out[7];
                            2'd1: if (clock_channel < 8'(NPIN)) value[clock_channel] <= 1'b1;
                            2'd2: begin
                                shift_in  <= {shift_in[6:0], in_channel < 8'(NCH) && level_here[in_channel]};
                                shift_out <= {shift_out[6:0], 1'b0};
                                bits_left <= bits_left - 4'd1;
                                if (clock_channel < 8'(NPIN)) value[clock_channel] <= 1'b0;
                            end
                            default: begin
                                phase <= 2'd0;
                                if (bits_left == 4'd0) begin
                                    if (command == "x") begin
                                        answer[ANSWER-1 -: 8] <= shift_in;
                                        answer_left           <= NIBBLES'(2);
                                    end else begin
                                        answer[ANSWER-1 -: 4] <= {3'b000, shift_in[0]};
                                        answer_left           <= NIBBLES'(1);
                                    end
                                    state <= ANSWERING;
                                end
                            end
                        endcase
                    end
                end
                // The codec answers a read in the frame after, 42 us at most; with no bit clock, never.
                CODEC_WAIT: begin
                    ac_timeout <= ac_timeout - 17'd1;
                    if (ac_came || ac_timeout == '0) begin
                        if (ac_which == 2'd2) begin
                            answer[ANSWER-1 -: 116] <= stat_hold;
                            answer_left             <= NIBBLES'(29);
                        end else if (ac_which == 2'd1) begin
                            answer[ANSWER-1 -: 84] <= {2'b00, ac_came, ac_hold};
                            answer_left            <= NIBBLES'(21);
                        end else begin
                            answer[ANSWER-1 -: 28] <= {3'b000, ac_came, 1'b0, ac_reply};
                            answer_left            <= NIBBLES'(7);
                        end
                        state <= ANSWERING;
                    end
                end
                // A byte of the frame held comes a cycle after its place is given.
                RAM_READ: begin
                    rx_raddr <= rx_raddr + 11'd1;
                    read_at  <= read_at + 6'd1;
                    if (read_at != 6'd0 && read_at != 6'd33) chunk <= {chunk[247:0], rx_rq};
                    if (read_at == 6'd33) begin
                        answer[ANSWER-1 -: 256] <= chunk;
                        answer_left             <= NIBBLES'(64);
                        state                   <= ANSWERING;
                    end
                end
                COUNTS_WAIT: if (counts_still) begin
                    answer[ANSWER-1 -: COUNTS] <= {seconds, counts_flat};
                    answer_left                <= NIBBLES'(COUNTS / 4);
                    state                      <= ANSWERING;
                end
                ANSWERING: if (tx_ready) begin
                    if (answer_left == '0) begin
                        state <= IDLE;
                    end else begin
                        answer      <= {answer[ANSWER-5:0], 4'b0};
                        answer_left <= answer_left - NIBBLES'(1);
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end

    // ---- the digits ----
    // A segment is lit by a low. Left alone they read "L" on the left digit, 7SEG2
    // (docs/buttons-displays-resets.md), and "r" on the other: A is bit 0, G bit 6, the point bit 7.
    assign digit_high_n = ~(digits_given ? digits[15:8] : 8'b0011_1000);
    assign digit_low_n  = ~(digits_given ? digits[7:0]  : 8'b0101_0000);
endmodule
