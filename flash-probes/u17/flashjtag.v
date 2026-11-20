// The user flash, U17, on the JTAG chain's USER1 data register: what is shifted in goes to the flash,
// most significant bit of a command first, and what the flash answers comes back. A probe for reading
// the flash out. It passes on any command: it is dump.py that sends read commands only.
//
// The flash's clock is TCK's low half, so that it takes a bit half a cycle after the TAP did and
// changes its answer half a cycle before the TAP's next sample, whichever edge the TAP samples at.
module flashjtag (
    output wire bpi_cs_n, bpi_oe_n, bpi_we_n,
    output wire flash_cs_n, flash_clk, flash_mosi,
    input  wire flash_miso,
    output wire flash_wp_n, flash_hold_n
);
    wire tck_pin, tck, tdi, sel, shift;
    BSCANE2 #(.JTAG_CHAIN(1)) bscan (
        .TCK(tck_pin), .TDI(tdi), .TDO(flash_miso), .SEL(sel), .SHIFT(shift),
        .CAPTURE(), .DRCK(), .RESET(), .RUNTEST(), .TMS(), .UPDATE());
    BUFG tck_buf (.I(tck_pin), .O(tck));

    reg shifted = 1'b0, held = 1'b0, mosi = 1'b0;
    always @(posedge tck) begin
        shifted <= sel && shift;       // this edge shifted a bit
        mosi    <= tdi;
    end
    always @(negedge tck) held <= shifted;

    assign flash_clk    = !tck && shifted;
    assign flash_cs_n   = !(shifted || held);
    assign flash_mosi   = mosi;
    assign flash_wp_n   = 1'b1;
    assign flash_hold_n = 1'b1;
    assign {bpi_cs_n, bpi_oe_n, bpi_we_n} = 3'b111;
endmodule
