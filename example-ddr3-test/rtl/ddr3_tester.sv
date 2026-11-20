// Memory test over MIG's native interface: write every burst, read every burst back, compare, repeat.
// Each 32-bit word is xorshift32(word address ^ seed), a bijection, so a burst written to or read from the
// wrong address shows as an error. Odd passes invert every second word, so all DQ lines switch between beats.
module ddr3_tester #(
    parameter int ADDR_WIDTH = 29,                  // in 32-bit words; a burst is 8 of them
    localparam int BURST_BITS = ADDR_WIDTH - 3
) (
    input  logic                  clk,
    input  logic                  rst,
    input  logic                  calib_done,

    output logic [ADDR_WIDTH-1:0] app_addr,
    output logic [2:0]            app_cmd,
    output logic                  app_en,
    input  logic                  app_rdy,
    output logic [255:0]          app_wdf_data,
    output logic                  app_wdf_wren,
    output logic                  app_wdf_end,
    input  logic                  app_wdf_rdy,
    input  logic [255:0]          app_rd_data,
    input  logic                  app_rd_data_valid,

    input  logic                  inject,           // a change makes the next comparison expect a wrong bit

    output logic [15:0]           passes,
    output logic [1:0]            phase,            // 0 waiting for calibration, 1 writing, 2 reading
    output logic [BURST_BITS-1:0] position,
    output logic [31:0]           error_bursts,
    output logic [31:0]           error_mask,       // DQ lines that have been wrong
    output logic [BURST_BITS-1:0] first_error
);
    function automatic logic [31:0] xorshift32(input logic [31:0] v);
        logic [31:0] x;
        x = v;
        x = x ^ (x << 13);
        x = x ^ (x >> 17);
        x = x ^ (x << 5);
        return x;
    endfunction

    function automatic logic [255:0] pattern(input logic [BURST_BITS-1:0] burst, input logic [31:0] seed,
                                             input logic toggling);
        logic [255:0] data;
        for (int i = 0; i < 8; i++) begin
            if (toggling && i[0])
                data[32 * i +: 32] = ~data[32 * (i - 1) +: 32];
            else
                data[32 * i +: 32] = xorshift32(32'({burst, 3'(i)}) ^ seed);
        end
        return data;
    endfunction

    typedef enum logic [2:0] {WAIT_CALIB, W_PREPARE, W_DATA, W_CMD, R_CMD, R_DRAIN} state_t;
    state_t state;
    logic [BURST_BITS-1:0] burst;       // the one being written, or the next read to issue
    logic [BURST_BITS:0]   returned;    // read bursts that have come back
    logic [31:0]           seed;
    logic                  toggling;
    logic [3:0]            drain;

    assign toggling     = passes[0];
    assign app_addr     = {burst, 3'b000};
    assign app_cmd      = state == R_CMD ? 3'b001 : 3'b000;
    assign app_en       = state == W_CMD || state == R_CMD;
    assign app_wdf_wren = state == W_DATA;
    assign app_wdf_end  = state == W_DATA;
    assign phase        = state == WAIT_CALIB ? 2'd0 : (state == R_CMD || state == R_DRAIN) ? 2'd2 : 2'd1;
    assign position     = state == R_DRAIN ? '1 : burst;

    // The comparison, two stages behind the read data.
    logic                  inject_seen, inject_pending;
    logic                  s1_valid, s2_valid;
    logic [255:0]          s1_data, s1_expected;
    logic [BURST_BITS-1:0] s1_burst, s2_burst;
    logic [31:0]           s2_wrong;

    always_ff @(posedge clk) begin
        s1_valid    <= app_rd_data_valid;
        s1_data     <= app_rd_data;
        s1_burst    <= returned[BURST_BITS-1:0];
        s1_expected <= pattern(returned[BURST_BITS-1:0], seed, toggling) ^ 256'(inject_pending);

        s2_valid <= s1_valid;
        s2_burst <= s1_burst;
        begin
            logic [31:0] fold;
            fold = '0;
            for (int i = 0; i < 8; i++)
                fold |= s1_data[32 * i +: 32] ^ s1_expected[32 * i +: 32];
            s2_wrong <= fold;
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state          <= WAIT_CALIB;
            burst          <= '0;
            returned       <= '0;
            seed           <= 32'h1234_5678;
            passes         <= '0;
            error_bursts   <= '0;
            error_mask     <= '0;
            first_error    <= '0;
            inject_seen    <= inject;
            inject_pending <= 1'b0;
        end else begin
            if (app_rd_data_valid) begin
                returned       <= returned + 1'b1;
                inject_pending <= 1'b0;
            end
            // After the clear: a request that arrives while a burst returns waits for the next one.
            if (inject != inject_seen) begin
                inject_seen    <= inject;
                inject_pending <= 1'b1;
            end
            if (s2_valid && s2_wrong != '0) begin
                if (error_bursts == '0) first_error <= s2_burst;
                if (error_bursts != '1) error_bursts <= error_bursts + 32'd1;
                error_mask <= error_mask | s2_wrong;
            end

            case (state)
                WAIT_CALIB: if (calib_done) state <= W_PREPARE;
                W_PREPARE: begin
                    app_wdf_data <= pattern(burst, seed, toggling);
                    state        <= W_DATA;
                end
                W_DATA: if (app_wdf_rdy) state <= W_CMD;
                W_CMD: if (app_rdy) begin
                    burst <= burst + 1'b1;
                    state <= burst == '1 ? R_CMD : W_PREPARE;
                end
                R_CMD: if (app_rdy) begin
                    burst <= burst + 1'b1;
                    if (burst == '1) begin
                        state <= R_DRAIN;
                        drain <= '0;
                    end
                end
                R_DRAIN: if (returned[BURST_BITS]) begin
                    // Every burst is back; let the comparison finish before the seed changes.
                    drain <= drain + 4'd1;
                    if (drain == 4'd8) begin
                        returned <= '0;
                        passes   <= passes + 16'd1;
                        seed     <= xorshift32(seed) ^ 32'h9E37_79B9;
                        state    <= W_PREPARE;
                    end
                end
                default: state <= WAIT_CALIB;
            endcase
        end
    end
endmodule
