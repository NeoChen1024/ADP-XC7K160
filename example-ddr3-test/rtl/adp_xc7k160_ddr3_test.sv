// A test of the ADP-XC7K160's DDR3 on its own: MIG calibrates the two x16 chips, ddr3_tester runs over
// all of them, and the state goes out of the RS-232 port as a line of hex every 0.42 s, at 115200 (114943).
// The reporting runs from the 20 MHz oscillator, so it still talks when the 200 MHz clock, the MMCM or the
// calibration does not come up. ddr3_test.py reads the lines.
//
// Commands in: 'r' resets MIG, which calibrates again, and 'e' makes the tester expect one wrong bit, to
// show that an error would be counted.
module adp_xc7k160_ddr3_test (
    input  logic        osc_20mhz,
    output logic        uart_tx_pin,
    input  logic        uart_rx_pin,
    output logic        bpi_cs_n,
    output logic        bpi_oe_n,
    output logic        bpi_we_n,

    input  logic        sys_clk_p,
    input  logic        sys_clk_n,
    output logic [14:0] ddr3_addr,
    output logic [2:0]  ddr3_ba,
    output logic        ddr3_ras_n,
    output logic        ddr3_cas_n,
    output logic        ddr3_we_n,
    output logic        ddr3_reset_n,
    output logic [0:0]  ddr3_ck_p,
    output logic [0:0]  ddr3_ck_n,
    output logic [0:0]  ddr3_cke,
    output logic [0:0]  ddr3_cs_n,
    output logic [3:0]  ddr3_dm,
    output logic [0:0]  ddr3_odt,
    inout  wire  [31:0] ddr3_dq,
    inout  wire  [3:0]  ddr3_dqs_p,
    inout  wire  [3:0]  ddr3_dqs_n
);
    // The configuration flash stays deselected.
    assign bpi_cs_n = 1'b1;
    assign bpi_oe_n = 1'b1;
    assign bpi_we_n = 1'b1;

    logic clk;
    BUFG osc_buf (.I(osc_20mhz), .O(clk));
    localparam logic [13:0] CYCLES_PER_BIT = 14'd174;    // 20 MHz / 174 = 114943 baud

    logic [3:0] por = '0;
    logic rst = 1'b1;
    always_ff @(posedge clk) begin
        if (por != '1) por <= por + 4'd1;
        rst <= por != '1;
    end

    // ---- MIG and the tester, on ui_clk -------------------------------------------------------------------
    logic         sys_rst_n;
    logic         ui_clk, ui_rst, calib;
    logic [28:0]  app_addr;
    logic [27:0]  word_addr;
    logic [2:0]   app_cmd;
    logic         app_en, app_rdy, app_wdf_wren, app_wdf_end, app_wdf_rdy, app_rd_data_valid;
    logic [255:0] app_wdf_data, app_rd_data;

    mig u_mig (
        .ddr3_addr, .ddr3_ba, .ddr3_cas_n, .ddr3_ck_n, .ddr3_ck_p, .ddr3_cke, .ddr3_ras_n, .ddr3_reset_n,
        .ddr3_we_n, .ddr3_dq, .ddr3_dqs_n, .ddr3_dqs_p, .ddr3_cs_n, .ddr3_dm, .ddr3_odt,
        .init_calib_complete(calib),
        .app_addr, .app_cmd, .app_en, .app_wdf_data, .app_wdf_end, .app_wdf_wren,
        .app_rd_data, .app_rd_data_end(), .app_rd_data_valid, .app_rdy, .app_wdf_rdy,
        .app_sr_req(1'b0), .app_ref_req(1'b0), .app_zq_req(1'b0),
        .app_sr_active(), .app_ref_ack(), .app_zq_ack(),
        .ui_clk, .ui_clk_sync_rst(ui_rst),
        .app_wdf_mask('0),
        .device_temp(),
        .sys_clk_p, .sys_clk_n,
        .sys_rst(sys_rst_n)
    );

    logic        inject_toggle = 1'b0;
    logic [2:0]  inject_sync;
    logic [15:0] passes;
    logic [1:0]  phase;
    logic [24:0] position, first_error;
    logic [31:0] error_bursts, error_mask;
    always_ff @(posedge ui_clk) inject_sync <= {inject_sync[1:0], inject_toggle};

    // Two 4 Gb chips are 2^28 words of 32 bits; the bit above them in MIG's address is the rank.
    assign app_addr = {1'b0, word_addr};
    ddr3_tester #(.ADDR_WIDTH(28)) tester (
        .clk(ui_clk), .rst(ui_rst), .calib_done(calib),
        .app_addr(word_addr), .app_cmd, .app_en, .app_rdy, .app_wdf_data, .app_wdf_wren, .app_wdf_end, .app_wdf_rdy,
        .app_rd_data, .app_rd_data_valid,
        .inject(inject_sync[2]),
        .passes, .phase, .position, .error_bursts, .error_mask, .first_error
    );

    // A snapshot for the slow side: taken when its request toggles, read there long after it settled.
    localparam int SNAP = 16 + 4 + 28 + 32 + 32 + 28;
    logic            snap_req = 1'b0, snap_ack = 1'b0;
    logic [2:0]      req_sync;
    logic [SNAP-1:0] snap;
    always_ff @(posedge ui_clk) begin
        req_sync <= {req_sync[1:0], snap_req};
        if (req_sync[2] != snap_ack) begin
            snap     <= {passes, 2'b00, phase, 3'b000, position, error_bursts, error_mask, 3'b000, first_error};
            snap_ack <= req_sync[2];
        end
    end

    // ---- Control and reporting, on the 20 MHz oscillator -------------------------------------------------
    logic [2:0] rx_sync = '1, calib_sync = '0, ui_rst_sync = '1, ack_sync = '0;
    always_ff @(posedge clk) begin
        rx_sync     <= {rx_sync[1:0], uart_rx_pin};
        calib_sync  <= {calib_sync[1:0], calib};
        ui_rst_sync <= {ui_rst_sync[1:0], ui_rst};
        ack_sync    <= {ack_sync[1:0], snap_ack};
    end

    logic       rx_valid;
    logic [7:0] rx_data;
    uart_rx u_rx (.clk, .rst, .cycles_per_bit(CYCLES_PER_BIT), .rx(rx_sync[2]), .valid(rx_valid), .data(rx_data));

    // sys_rst is held for 52 ms; the time from its release to calibration is counted in milliseconds.
    logic [20:0] hold = '0;
    logic [14:0] ms_divider;
    logic [15:0] calib_ms;
    logic [7:0]  resets = '0;
    assign sys_rst_n = hold[20];
    always_ff @(posedge clk) begin
        if (!hold[20]) begin
            hold       <= hold + 21'd1;
            ms_divider <= '0;
            calib_ms   <= '0;
        end else if (!calib_sync[2]) begin
            ms_divider <= ms_divider == 15'd19999 ? '0 : ms_divider + 15'd1;
            if (ms_divider == 15'd19999 && calib_ms != '1) calib_ms <= calib_ms + 16'd1;
        end
        if (rx_valid && rx_data == "r") begin
            hold   <= '0;
            resets <= resets + 8'd1;
        end
        if (rx_valid && rx_data == "e") inject_toggle <= !inject_toggle;
    end

    logic [7:0] rx_count = '0, rx_last = '0;
    always_ff @(posedge clk)
        if (rx_valid) begin
            rx_count <= rx_count + 8'd1;
            rx_last  <= rx_data;
        end

    logic [22:0] timer = '0;
    logic        tick;
    always_ff @(posedge clk) timer <= timer + 23'd1;
    assign tick = timer == '0;              // every 0.42 s

    logic            ui_alive = 1'b0;
    logic [SNAP-1:0] held = '0;
    always_ff @(posedge clk)
        if (tick) begin
            ui_alive <= ack_sync[2] == snap_req;
            if (ack_sync[2] == snap_req) held <= snap;
            snap_req <= !snap_req;
        end

    localparam int DIGITS = (4 + 8 + 16 + 16 + SNAP) / 4;
    localparam int LINE = DIGITS + 4;
    logic [4*DIGITS-1:0] status;
    assign status = {ui_alive, ui_rst_sync[2], calib_sync[2], sys_rst_n, resets, calib_ms, rx_count, rx_last, held};

    function automatic logic [7:0] hex(input logic [3:0] v);
        return v < 4'd10 ? 8'h30 + 8'(v) : 8'h57 + 8'(v);
    endfunction

    logic [5:0] at;
    logic       sending = 1'b0, tx_ready;
    logic [7:0] tx_data;
    always_comb begin
        if (at == 6'd0) tx_data = "D";
        else if (at == 6'd1) tx_data = " ";
        else if (at == 6'(LINE - 2)) tx_data = 8'h0D;
        else if (at == 6'(LINE - 1)) tx_data = 8'h0A;
        else tx_data = hex(status[4 * (DIGITS - 1 - (32'(at) - 2)) +: 4]);
    end
    always_ff @(posedge clk) begin
        if (rst) sending <= 1'b0;
        else if (tick) begin
            sending <= 1'b1;
            at      <= '0;
        end else if (sending && tx_ready) begin
            if (at == 6'(LINE - 1)) sending <= 1'b0;
            else at <= at + 6'd1;
        end
    end
    uart_tx u_tx (.clk, .rst, .cycles_per_bit(CYCLES_PER_BIT), .valid(sending), .data(tx_data), .ready(tx_ready), .tx(uart_tx_pin));
endmodule
