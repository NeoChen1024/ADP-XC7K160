// The workbench's commands, against a board that is a few wires: a 25 MHz clock on both MII clocks,
// the PHY's transmit lines looped to its receive lines (with a nibble spoiled when the test says so),
// SD_CMD looped to SD_DAT0, and an audio codec that has registers and sends back the samples it is given.
`timescale 1ps/1ps
module tb;
    import wb::*;
    localparam int BIT = 174 * 50_000;      // ps
    logic osc = 0, sys = 0, ref100 = 0, mii = 0;
    always #25000 osc = !osc;
    always #2500 sys = !sys;
    always #5000 ref100 = !ref100;
    always #20000 mii = !mii;

    logic rx = 1, tx;
    logic [3:0] txd;
    logic tx_en;
    wire [NPIN-1:0] pin;
    logic [NPIN-1:0] board = '1;            // what the pulls and the board give, where nothing drives
    logic [NPIN-1:0] board_drives = '0;
    for (genvar i = 0; i < NPIN; i++) begin : g
        assign pin[i] = board_drives[i] ? board[i] : 1'bz;
        pullup (pin[i]);
    end
    localparam int SD_CMD = 35, SD_DAT0 = 36, FA23 = 25, SW5 = 12;

    // The PHY, looped back, a little after its clock.
    logic [3:0] rxd; logic dv;
    logic spoil = 0;
    int   nibble_at = 0, gap = 1000, least_gap = 1000;
    always @(negedge mii) begin
        nibble_at = tx_en ? nibble_at + 1 : 0;
        rxd <= txd ^ 4'(spoil && nibble_at == 40);
        dv  <= tx_en;
        // The shortest stretch of nothing between two frames.
        if (tx_en) begin
            if (gap > 0 && gap < least_gap) least_gap = gap;
            gap = 0;
        end else gap = gap + 1;
    end
    always_comb begin
        board = '1;
        board[CH_MAC_RX_CLK] = mii;
        board[CH_MAC_TX_CLK] = mii;
        board[CH_MAC_RX_DV] = dv;
        board[CH_MAC_RX_ER] = 1'b0;
        board[CH_MAC_RXD0] = rxd[0]; board[CH_MAC_RXD1] = rxd[1]; board[CH_MAC_RXD2] = rxd[2]; board[CH_MAC_RXD3] = rxd[3];
        board[SD_DAT0] = pin[SD_CMD];
        board[SW5] = sw5;
        board[CH_AC97_BCLK] = bclk;
        board[CH_AC97_SDI] = sdi;
    end
    logic sw5 = 1;
    initial begin
        board_drives = '0;
        board_drives[CH_MAC_RX_CLK] = 1; board_drives[CH_MAC_TX_CLK] = 1; board_drives[CH_MAC_RX_DV] = 1; board_drives[CH_MAC_RX_ER] = 1;
        board_drives[CH_MAC_RXD0] = 1; board_drives[CH_MAC_RXD1] = 1; board_drives[CH_MAC_RXD2] = 1; board_drives[CH_MAC_RXD3] = 1;
        board_drives[SD_DAT0] = 1; board_drives[SW5] = 1;
        board_drives[CH_AC97_BCLK] = 1; board_drives[CH_AC97_SDI] = 1;
    end

    // The codec: a 12.288 MHz bit clock while it is out of reset. It takes SYNC and SDATA_OUT as its
    // clock falls, and changes SDATA_IN as it rises. A read is answered in the next frame.
    localparam int AC97_RSTn = CH_AC97_BCLK - 1;
    logic        bclk = 0, sdi = 0, ac_sync, ac_sdo, sync_was = 0;
    int          bit_at = 1000;
    logic [95:0] codec_in = '0, codec_out = '0;
    logic [15:0] codec_register [128];
    logic        reply = 0;
    logic [6:0]  reply_index = '0;
    logic [19:0] loop_left = '0, loop_right = '0;
    initial begin
        for (int i = 0; i < 128; i++) codec_register[i] = 16'h0000;
        codec_register[7'h7C] = 16'h414C;
        codec_register[7'h7E] = 16'h4770;
    end
    always #40690 bclk = pin[AC97_RSTn] === 1'b1 ? !bclk : 1'b0;
    always @(negedge bclk) begin
        sync_was <= ac_sync;
        if (ac_sync && !sync_was) begin
            bit_at = -1;      // the frame's first bit is in the next bit_at
            codec_out = {1'b1, reply, reply, 2'b11, 11'b0, 1'b0, reply_index, 12'b0, codec_register[reply_index], 4'b0,
                         loop_left, loop_right};
            reply = 0;
        end else begin
            if (bit_at >= 0 && bit_at < 96) codec_in[95 - bit_at] = ac_sdo;
            if (bit_at == 95 && codec_in[95]) begin
                if (codec_in[94] && codec_in[79]) begin
                    reply = 1;
                    reply_index = codec_in[78:72];
                end else if (codec_in[94] && codec_in[93]) begin
                    codec_register[codec_in[78:72]] = codec_in[59:44];
                end
                if (codec_in[92]) loop_left = codec_in[39:20];
                if (codec_in[91]) loop_right = codec_in[19:0];
            end
        end
    end
    always @(posedge bclk) begin
        bit_at = bit_at + 1;
        sdi <= bit_at >= 0 && bit_at < 96 ? codec_out[95 - bit_at] : 1'b0;
    end

    adp_xc7k160_workbench #(.SECOND(20_000), .HALF_CLOCK(4)) dut (
        .osc_20mhz(osc), .uart_rx_pin(rx), .uart_tx_pin(tx), .bpi_cs_n(), .bpi_oe_n(), .bpi_we_n(),
        .digit_high_n(), .digit_low_n(), .ac97_sync(ac_sync), .ac97_sdo(ac_sdo), .mac_txd(txd), .mac_tx_en(tx_en),
        .sys_clk_p(sys), .sys_clk_n(!sys), .ckout1_p(ref100), .ckout1_n(!ref100), .pcie_clk_p(1'b0), .pcie_clk_n(1'b1),
        .pin(pin)
    );

    task automatic send(input string text);
        for (int at = 0; at < text.len(); at++) begin
            logic [7:0] c = text[at];
            rx = 0; #BIT;
            for (int b = 0; b < 8; b++) begin rx = c[b]; #BIT; end
            rx = 1; #BIT;
        end
    endtask
    string line;
    task automatic receive;
        logic [7:0] c;
        line = "";
        forever begin
            @(negedge tx);
            #(BIT + BIT / 2);
            for (int b = 0; b < 8; b++) begin c[b] = tx; #BIT; end
            if (c == 8'h0A) break;
            line = {line, string'(c)};
        end
    endtask
    int failed = 0;
    localparam int ROUND = 4 * ((NCH + 3) / 4), DIGIT = (ROUND - 1 - SW5) / 4;
    logic [3:0] lv, lo, hi;
    int rx_clk, sysclk, ref_clk, none;
    task automatic expect_line(input string what, input string want);
        receive();
        if (line != want) begin
            failed++;
            $display("FAIL %s: got %s, want %s", what, line, want);
        end else $display("ok   %s: %s", what, line);
    endtask
    // A frame of 60 bytes, and its check sequence worked out here, bit by bit.
    logic [7:0] frame [64];
    function automatic logic [31:0] crc32_of(input int count);
        logic [31:0] crc = '1;
        for (int at = 0; at < count; at++) begin
            crc = crc ^ 32'(frame[at]);
            for (int b = 0; b < 8; b++) crc = crc[0] ? (crc >> 1) ^ 32'hEDB88320 : crc >> 1;
        end
        return ~crc;
    endfunction
    function automatic string frame_hex(input int from);
        string text = "";
        for (int at = from; at < from + 32; at++) text = {text, $sformatf("%02x", frame[at])};
        return text;
    endfunction
    logic [31:0] fcs;
    function automatic string field(input int index, input int width);     // of the f answer, after the second
        return line.substr(2 + index * width, 2 + index * width + width - 1);
    endfunction

    initial begin
        #2_000_000;
        fork send("\377zzi"); expect_line("ident after junk", $sformatf("%08x%02x", MAGIC, NCH)); join
        // A driver.
        if (pin[FA23] !== 1'b1) begin failed++; $display("FAIL FA23 should float high"); end
        send($sformatf("c%02x2", FA23)); #1_000_000;
        if (pin[FA23] !== 1'b0) begin failed++; $display("FAIL FA23 should be driven low, is %b", pin[FA23]); end
        fork send($sformatf("g%02x", FA23)); expect_line("level of FA23 driven low", "0"); join
        send($sformatf("c%02x3", FA23)); #1_000_000;
        fork send($sformatf("g%02x", FA23)); expect_line("level of FA23 driven high", "1"); join
        send($sformatf("c%02x0", FA23));
        // A channel that cannot be driven is not.
        send($sformatf("c%02x2", SW5)); #1_000_000;
        if (pin[SW5] !== 1'b1) begin failed++; $display("FAIL SW5 was driven"); end
        // Seen low and high: a pulse on SW5 between two looks.
        fork send("s"); receive(); join
        sw5 = 0; #200_000; sw5 = 1; #1_000_000;
        fork send("s"); receive(); join
        lv = 4'(line.substr(DIGIT, DIGIT).atohex());
        lo = 4'(line.substr(ROUND / 4 + DIGIT, ROUND / 4 + DIGIT).atohex());
        hi = 4'(line.substr(ROUND / 2 + DIGIT, ROUND / 2 + DIGIT).atohex());
        if (!(lv[SW5 % 4] && lo[SW5 % 4] && hi[SW5 % 4])) begin failed++; $display("FAIL SW5 pulse: %s", line); end
        else $display("ok   SW5 pulse seen: level 1, seen low, seen high");
        fork send("s"); receive(); join
        lo = 4'(line.substr(ROUND / 4 + DIGIT, ROUND / 4 + DIGIT).atohex());
        if (lo[SW5 % 4]) begin failed++; $display("FAIL SW5 seen low again: %s", line); end
        else $display("ok   seen low starts again");
        // Bits out and in, SD_CMD looped to SD_DAT0, clocked on SD_CLK.
        send($sformatf("k%02x%02x%02x", SD_CMD - 1, SD_CMD, SD_DAT0));
        send($sformatf("c%02x2c%02x3", SD_CMD - 1, SD_CMD));
        fork send("xa5"); expect_line("byte exchanged", "a5"); join
        fork send("x3c"); expect_line("byte exchanged", "3c"); join
        fork send("b1"); expect_line("bit exchanged", "1"); join
        fork send("b0"); expect_line("bit exchanged", "0"); join
        // Counts: wait for a whole second of the simulation's, 1 ms.
        #2_500_000_000;
        fork send("f"); receive(); join
        begin
            rx_clk = field(CH_MAC_RX_CLK, 7).atohex(); sysclk = field(NPIN, 7).atohex(); ref_clk = field(NPIN + 1, 7).atohex();
            none = field(NPIN + 2, 7).atohex();
            $display("counts in 1 ms: MAC_RX_CLK %0d, SYSCLK/16 %0d, CKOUT1/16 %0d, PCIE_CLK/16 %0d", rx_clk, sysclk, ref_clk, none);
            if (rx_clk < 24999 || rx_clk > 25001 || sysclk < 12499 || sysclk > 12501 || ref_clk < 6249 || ref_clk > 6251 || none != 0)
                begin failed++; $display("FAIL counts"); end
        end
        // Frames out and back. The receiver is given an address, and the frame is to it.
        frame[0] = 8'h02; frame[1] = 8'had; frame[2] = 8'hb0; frame[3] = 8'h00; frame[4] = 8'h00; frame[5] = 8'h01;
        frame[6] = 8'h02; frame[7] = 8'had; frame[8] = 8'hb0; frame[9] = 8'h00; frame[10] = 8'h00; frame[11] = 8'h02;
        frame[12] = 8'h88; frame[13] = 8'hb5;
        for (int at = 0; at < 46; at++) frame[14 + at] = 8'(at * 7 + 3);
        fcs = crc32_of(60);
        if (fcs != 32'h7e28d9f0) begin failed++; $display("FAIL the test's own CRC: %08x", fcs); end
        for (int at = 0; at < 4; at++) frame[60 + at] = fcs[8 * at +: 8];
        for (int at = 0; at < 6; at++) send($sformatf("q%1x%02x", at, frame[at]));
        send("q601");
        for (int at = 0; at < 60; at++) send($sformatf("m%03x%02x", at, frame[at]));
        fork send("e"); expect_line("the receiver before anything", "00000000000000000000000000000"); join
        send("a"); #100_000_000; send("n03c0001"); #20_000_000;
        fork send("e"); expect_line("a frame to the receiver's address is held, whole", "31040000000010000000100000000"); join
        fork send("o000"); expect_line("its first 32 bytes", frame_hex(0)); join
        fork send("o020"); expect_line("its last 32 bytes, with the check sequence", frame_hex(32)); join
        // To another address: counted as seen, and not kept.
        send("m00503"); send("a"); #100_000_000; send("n03c0001"); #20_000_000;
        fork send("e"); expect_line("a frame to another address is passed over", "21040000000020000000100000000"); join
        send("q600"); #100_000_000; send("n03c0001"); #20_000_000;
        fork send("e"); expect_line("and kept with the filter off", "31040000000030000000200000000"); join
        // A nibble spoiled on the way: kept, and said to be wrong.
        send("q601"); send("m00501"); send("a"); #100_000_000;
        spoil = 1; send("n03c0001"); #20_000_000; spoil = 0;
        fork send("e"); expect_line("a spoiled frame", "30040000000040000000200000001"); join
        // Five in a row, with the gap the standard wants between them.
        least_gap = 1000;
        send("a"); #100_000_000; send("n03c0005"); #60_000_000;
        fork send("e"); expect_line("five frames in a row", "31040000000090000000700000001"); join
        if (least_gap < 24 || least_gap > 40) begin failed++; $display("FAIL the gap between frames: %0d nibbles", least_gap); end
        else $display("ok   the gap between frames: %0d nibbles", least_gap);
        // Since configuration: SW5 was high, fell once and rose once.
        fork send($sformatf("z%02x", SW5)); receive(); join
        if (line.substr(0, 0) != "f" || line.substr(1, 8) == "00000000" || line.substr(9, 16) == "00000000")
            begin failed++; $display("FAIL since configuration, SW5: %s", line); end
        else $display("ok   SW5 since configuration: %s", line);
        fork send($sformatf("z%02x", SW5 + 1)); expect_line("SW6 since configuration: high, and never moved", "90000000000000000"); join
        // The codec: no answer while it is in reset, then its registers, and the tones through it and back.
        fork send("r7c"); expect_line("a read with the codec in reset", "0000000"); join
        if (ac_sync !== 1'b0 || ac_sdo !== 1'b0) begin failed++; $display("FAIL the link is not low while off"); end
        send($sformatf("c%02x3", AC97_RSTn)); #5_000_000; send("l4"); #100_000_000;
        fork send("r7c"); expect_line("the codec's first identity register", "17c414c"); join
        fork send("r7e"); expect_line("the codec's second identity register", "17e4770"); join
        send("w02abcd");
        fork send("r02"); expect_line("a register written and read", "102abcd"); join
        fork send("p"); receive(); join
        #3_000_000_000;
        fork send("p"); receive(); join
        if (line.substr(0, 16) != "30000000000000000") begin failed++; $display("FAIL silence: %s", line); end
        else $display("ok   silence back from the codec: %s", line);
        send("l7"); #1_000_000_000;
        fork send("p"); receive(); join
        #4_000_000_000;
        fork send("p"); receive(); join
        if (line.substr(0, 16) != "31000f0001000f000") begin failed++; $display("FAIL tones: %s", line); end
        else $display("ok   both tones back from the codec: %s", line);
        send("l5"); #3_000_000_000;
        fork send("p"); receive(); join
        #3_000_000_000;
        fork send("p"); receive(); join
        if (line.substr(0, 16) != "31000f00000000000") begin failed++; $display("FAIL left tone only: %s", line); end
        else $display("ok   the left tone alone: %s", line);
        send("l0"); #2_000_000;
        if (ac_sync !== 1'b0 || ac_sdo !== 1'b0) begin failed++; $display("FAIL the link is not low after it is turned off"); end
        if (failed == 0) $display("PASS"); else $display("FAILED");
        $finish;
    end
    initial begin #1_000_000_000_000; $display("TIMEOUT"); $finish; end
endmodule
