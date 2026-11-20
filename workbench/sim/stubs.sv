// What the simulation needs of the Xilinx primitives: clocks that tick.
`timescale 1ps/1ps
module BUFG (input logic I, output logic O);
    assign O = I;
endmodule
module IBUFDS (input logic I, input logic IB, output logic O);
    assign O = I;
endmodule
module IBUFDS_GTE2 (input logic I, input logic IB, input logic CEB, output logic O, output logic ODIV2);
    assign O = I;
    assign ODIV2 = 1'b0;
endmodule
module MMCME2_BASE #(
    parameter real CLKIN1_PERIOD = 0.0, parameter int DIVCLK_DIVIDE = 1,
    parameter real CLKFBOUT_MULT_F = 1.0, parameter real CLKOUT0_DIVIDE_F = 1.0, parameter int CLKOUT1_DIVIDE = 1
) (
    input logic CLKIN1, input logic CLKFBIN, input logic PWRDWN, input logic RST,
    output logic CLKFBOUT, output logic CLKFBOUTB, output logic CLKOUT0, output logic CLKOUT0B,
    output logic CLKOUT1, output logic CLKOUT1B, output logic CLKOUT2, output logic CLKOUT2B,
    output logic CLKOUT3, output logic CLKOUT3B, output logic CLKOUT4, output logic CLKOUT5, output logic CLKOUT6,
    output logic LOCKED
);
    assign CLKFBOUT = CLKIN1;
    initial CLKOUT0 = 1'b0;
    always #1250 CLKOUT0 = !CLKOUT0;        // 400 MHz
    initial CLKOUT1 = 1'b0;
    always #5000 CLKOUT1 = !CLKOUT1;        // 100 MHz, its edges on the other's
    assign LOCKED = 1'b1;
endmodule
