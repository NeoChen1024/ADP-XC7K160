# Pins from UM098 section 2.11 (../pins.csv), checked against the xc7k160tfbg676 package. The DDR3's
# and the 200 MHz clock's are in MIG's own constraints, which build.tcl reads before this file.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]

set_property -dict {PACKAGE_PIN G24 IOSTANDARD LVCMOS33} [get_ports osc_20mhz]
create_clock -name osc_20mhz -period 50.000 [get_ports osc_20mhz]

# The RS-232 port, S1 in the manual.
set_property -dict {PACKAGE_PIN T17 IOSTANDARD LVCMOS33} [get_ports uart_tx_pin]
set_property -dict {PACKAGE_PIN R18 IOSTANDARD LVCMOS33} [get_ports uart_rx_pin]

# The configuration flash's chip select, output enable and write enable.
set_property -dict {PACKAGE_PIN C23 IOSTANDARD LVCMOS33} [get_ports bpi_cs_n]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports bpi_oe_n]
set_property -dict {PACKAGE_PIN L18 IOSTANDARD LVCMOS33} [get_ports bpi_we_n]

# The DCI reference resistors are on bank 33; the data bytes are in bank 32, whose own the manual does
# not list. MIG does not write this line itself.
set_property DCI_CASCADE {32} [get_iobanks 33]

set_false_path -from [get_ports uart_rx_pin]
set_false_path -to [get_ports {uart_tx_pin bpi_cs_n bpi_oe_n bpi_we_n}]

# The oscillator's side and MIG's clocks meet only through synchronizers and a snapshot that is held.
set_clock_groups -asynchronous -group [get_clocks osc_20mhz] -group [get_clocks -include_generated_clocks sys_clk_p]
