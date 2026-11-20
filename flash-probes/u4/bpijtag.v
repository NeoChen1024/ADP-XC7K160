// The configuration flash, U4, read through the JTAG chain's USER1 data register. A scan shifts in a
// 26-bit word address, least significant bit first, and then shifts out the words from there on, 16
// bits each, least significant first, a word every 16 TCK. A probe for reading the flash out.
//
// Nothing here can write the flash: its write enable is tied high, and the data pins are inputs only.
// FA25 is driven, which the board otherwise pulls low, so both halves can be read.
module bpijtag (
    output wire        bpi_cs_n, bpi_oe_n, bpi_we_n,
    output wire [25:0] fa,
    input  wire [15:0] fdq
);
    wire tck_pin, tck, tdi, sel, shift;
    reg [15:0] out = 16'h0;
    BSCANE2 #(.JTAG_CHAIN(1)) bscan (
        .TCK(tck_pin), .TDI(tdi), .TDO(out[0]), .SEL(sel), .SHIFT(shift),
        .CAPTURE(), .DRCK(), .RESET(), .RUNTEST(), .TMS(), .UPDATE());
    BUFG tck_buf (.I(tck_pin), .O(tck));

    reg [25:0] addr = 26'h0;
    reg [4:0]  head = 5'h0;
    reg [3:0]  bits = 4'h0;
    reg        have = 1'b0;
    reg [15:0] data = 16'h0;
    always @(posedge tck) begin
        data <= fdq;                    // the address has stood for 15 cycles when this is taken
        if (!(sel && shift)) begin
            head <= 5'h0;
            have <= 1'b0;
        end else if (!have) begin
            addr <= {tdi, addr[25:1]};
            head <= head + 5'h1;
            bits <= 4'h0;
            if (head == 5'd25) have <= 1'b1;
        end else begin
            bits <= bits + 4'h1;
            if (bits == 4'hF) begin
                out  <= data;
                addr <= addr + 26'h1;
            end else begin
                out <= {1'b0, out[15:1]};
            end
        end
    end

    assign fa = addr;
    assign {bpi_cs_n, bpi_oe_n} = 2'b00;
    assign bpi_we_n = 1'b1;
endmodule
