# The workbench's own pins; the channels' are in pins.xdc, which board.py writes. Pins from UM098
# section 2.11 (../pins.csv), checked against the xc7k160tfbg676 package.
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

# 7SEG2, then 7SEG1; A to G, then the point.
set_property -dict {PACKAGE_PIN W24 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[0]}]
set_property -dict {PACKAGE_PIN AB26 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[1]}]
set_property -dict {PACKAGE_PIN AC26 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[2]}]
set_property -dict {PACKAGE_PIN Y25 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[3]}]
set_property -dict {PACKAGE_PIN Y26 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[4]}]
set_property -dict {PACKAGE_PIN AD21 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[5]}]
set_property -dict {PACKAGE_PIN AD23 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[6]}]
set_property -dict {PACKAGE_PIN AB24 IOSTANDARD LVCMOS33} [get_ports {digit_high_n[7]}]
set_property -dict {PACKAGE_PIN V26 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[0]}]
set_property -dict {PACKAGE_PIN W25 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[1]}]
set_property -dict {PACKAGE_PIN W26 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[2]}]
set_property -dict {PACKAGE_PIN V21 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[3]}]
set_property -dict {PACKAGE_PIN W21 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[4]}]
set_property -dict {PACKAGE_PIN AA25 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[5]}]
set_property -dict {PACKAGE_PIN AB25 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[6]}]
set_property -dict {PACKAGE_PIN W23 IOSTANDARD LVCMOS33} [get_ports {digit_low_n[7]}]

# The audio codec's frame sync and data in, held low.
set_property -dict {PACKAGE_PIN R26 IOSTANDARD LVCMOS33} [get_ports ac97_sync]
set_property -dict {PACKAGE_PIN K25 IOSTANDARD LVCMOS33} [get_ports ac97_sdo]

# The 10/100 PHY's transmit side.
set_property -dict {PACKAGE_PIN AB22 IOSTANDARD LVCMOS33} [get_ports {mac_txd[0]}]
set_property -dict {PACKAGE_PIN AC22 IOSTANDARD LVCMOS33} [get_ports {mac_txd[1]}]
set_property -dict {PACKAGE_PIN AB21 IOSTANDARD LVCMOS33} [get_ports {mac_txd[2]}]
set_property -dict {PACKAGE_PIN AC21 IOSTANDARD LVCMOS33} [get_ports {mac_txd[3]}]
set_property -dict {PACKAGE_PIN AD24 IOSTANDARD LVCMOS33} [get_ports mac_tx_en]

# The 200 MHz oscillator as the manual draws it, LVDS, in a bank at 1.5 V: an input only, with no
# termination inside the FPGA. Whether that works is one of the things this design is for.
set_property -dict {PACKAGE_PIN AA10 IOSTANDARD LVDS} [get_ports sys_clk_p]
set_property -dict {PACKAGE_PIN AB10 IOSTANDARD LVDS} [get_ports sys_clk_n]
create_clock -name sys_clk -period 5.000 [get_ports sys_clk_p]

# The two reference clock inputs of the transceivers.
set_property PACKAGE_PIN D6 [get_ports ckout1_p]
set_property PACKAGE_PIN D5 [get_ports ckout1_n]
set_property PACKAGE_PIN H6 [get_ports pcie_clk_p]
set_property PACKAGE_PIN H5 [get_ports pcie_clk_n]
create_clock -name ckout1 -period 10.000 [get_ports ckout1_p]
create_clock -name pcie_clk -period 10.000 [get_ports pcie_clk_p]

# Every clock meets the others only through synchronizers, or in registers that are still when they
# are read: the 400 MHz one is made from the oscillator, but nothing is taken on the strength of that.
# The 100 MHz clock is the exception: it comes from the same MMCM as the 400 MHz one, and the few
# registers between those two are timed as between any two related clocks.
set_clock_groups -asynchronous \
    -group [get_clocks osc_20mhz] \
    -group [get_clocks -of_objects [get_pins {fast_mmcm_i/CLKOUT0 fast_mmcm_i/CLKOUT1}]] \
    -group [get_clocks sys_clk] -group [get_clocks ckout1] -group [get_clocks pcie_clk]
