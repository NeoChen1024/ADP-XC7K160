# Generates MIG, the DDR3 controller, from mig.prj in the current directory. generate.sh runs it, with
# the part mig.prj names: MIG refuses a project whose part or speed grade is another.
#
#   vivado -mode batch -source mig.tcl -tclargs <part>
set part [lindex $argv 0]
create_project -force mig ./proj -part $part
create_ip -name mig_7series -vendor xilinx.com -library ip -version 4.2 -module_name mig
set_property -dict [list \
    CONFIG.XML_INPUT_FILE [file normalize mig.prj] \
    CONFIG.RESET_BOARD_INTERFACE Custom \
    CONFIG.MIG_DONT_TOUCH_PARAM Custom \
    CONFIG.BOARD_MIG_PARAM Custom] [get_ips mig]
generate_target all [get_ips mig]
