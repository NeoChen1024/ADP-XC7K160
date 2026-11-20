# Synthesis, place and route of the DDR3 test for the ADP-XC7K160, without a project. build.sh runs it
# in the work directory.
#
#   vivado -mode batch -source build.tcl -tclargs <top> <part> <MIG's user_design> <constraints> <source> ...
set sources [lassign $argv top part mig constraints]

set files {}
foreach dir {clocking controller ecc ip_top phy ui} { lappend files {*}[glob $mig/rtl/$dir/*.v] }
lappend files $mig/rtl/mig.v $mig/rtl/mig_mig.v
read_verilog $files
read_verilog -sv $sources
# MIG's constraints first: they place and type the DDR3's pins and the 200 MHz clock's, and make that clock.
read_xdc $mig/constraints/mig.xdc
read_xdc $constraints

synth_design -top $top -part $part
opt_design
place_design
phys_opt_design
route_design

report_timing_summary -file timing.rpt
report_utilization -file utilization.rpt
report_io -file io.rpt
report_drc -file drc.rpt
report_clocks -file clocks.rpt
write_bitstream -force $top.bit
