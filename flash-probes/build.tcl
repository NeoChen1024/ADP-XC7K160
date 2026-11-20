# Synthesis, place and route of one flash probe. run.sh runs it in the work directory.
#
#   vivado -mode batch -source build.tcl -tclargs <part> <top> <source directory>
lassign $argv part top dir
read_verilog $dir/$top.v
read_xdc $dir/$top.xdc
synth_design -top $top -part $part
opt_design
place_design
route_design
report_timing_summary -file timing.rpt
report_io -file io.rpt
report_drc -file drc.rpt
write_bitstream -force $top.bit
