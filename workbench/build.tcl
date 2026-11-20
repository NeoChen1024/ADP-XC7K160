# Synthesis, place and route of the workbench, and its two bitstreams. build.sh runs it in the work
# directory.
#
#   vivado -mode batch -source build.tcl -tclargs <part> <constraints> <channel constraints> <source> ...
set sources [lassign $argv part constraints pins]
set top adp_xc7k160_workbench
read_verilog -sv $sources
read_xdc $constraints
read_xdc $pins

set_msg_config -id {Synth 8-3848} -new_severity ERROR
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
write_bitstream -force workbench-pullup.bit

# The same design with every channel's pull-up turned into a pull-down: a pull is a bit of the pin's
# configuration, and nothing is placed or routed again for it.
set pulled [get_ports -filter {PULLTYPE == PULLUP}]
puts "[llength $pulled] channels change their pull"
set_property PULLTYPE PULLDOWN $pulled
write_bitstream -force workbench-pulldown.bit
