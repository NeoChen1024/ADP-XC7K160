# The U17 probe: the user SPI flash on bank 34 (1.8 V), and U4's three control pins held high.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]
set_property -dict {PACKAGE_PIN C23 IOSTANDARD LVCMOS33} [get_ports bpi_cs_n]
set_property -dict {PACKAGE_PIN M17 IOSTANDARD LVCMOS33} [get_ports bpi_oe_n]
set_property -dict {PACKAGE_PIN L18 IOSTANDARD LVCMOS33} [get_ports bpi_we_n]
set_property -dict {PACKAGE_PIN Y5 IOSTANDARD LVCMOS18} [get_ports flash_cs_n]
set_property -dict {PACKAGE_PIN AA5 IOSTANDARD LVCMOS18} [get_ports flash_clk]
set_property -dict {PACKAGE_PIN AC6 IOSTANDARD LVCMOS18} [get_ports flash_mosi]
set_property -dict {PACKAGE_PIN AC4 IOSTANDARD LVCMOS18} [get_ports flash_miso]
set_property -dict {PACKAGE_PIN AB5 IOSTANDARD LVCMOS18} [get_ports flash_wp_n]
set_property -dict {PACKAGE_PIN AB6 IOSTANDARD LVCMOS18} [get_ports flash_hold_n]

# TCK is the only clock. 50 MHz is far above what the cable gives (about 1.4 Mbit/s with xvc-pico).
# The flash's pins have no timing constraint: its clock is TCK's low half, so every bit has half a period on each side.
create_clock -name tck -period 20.000 [get_pins bscan/TCK]
