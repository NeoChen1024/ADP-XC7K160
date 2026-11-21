# Writes a bitstream into the configuration flash U4 through XVC, verifies it, and then
# reconfigures the FPGA from U4. flash_u4.sh runs it; read the rules there and in AGENTS.md.
#
#   vivado -mode batch -source flash_u4.tcl -tclargs <mcs> <prm> <host:port>
lassign $argv mcs prm xvc
open_hw_manager
connect_hw_server
# A new hw_server has not finished scanning the chain when the first open looks for devices.
if {[catch {open_hw_target -xvc_url $xvc}]} {
    set opened 0
    foreach attempt {1 2 3 4 5} {
        after 1000
        catch {close_hw_target}
        if {![catch {open_hw_target [lindex [get_hw_targets] 0]}]} { set opened 1; break }
    }
    if {!$opened} { error "no device on the XVC target $xvc" }
}
set device [lindex [get_hw_devices] 0]
current_hw_device $device
set cfgmem [create_hw_cfgmem -hw_device $device [lindex [get_cfgmem_parts {28f00am29ew-bpi-x16}] 0]]
# Erase only the blocks that the file uses: the rest of U4 keeps its contents.
set_property PROGRAM.ADDRESS_RANGE {use_file} $cfgmem
set_property PROGRAM.FILES [list $mcs] $cfgmem
set_property PROGRAM.PRM_FILE $prm $cfgmem
set_property PROGRAM.BPI_RS_PINS {none} $cfgmem
# The pins that the programming design does not use get the same weak pull-down as an unused pin of
# a normal design. A pull-up on the MII pins would isolate the Ethernet PHY (docs/ethernet.md).
set_property PROGRAM.UNUSED_PIN_TERMINATION {pull-down} $cfgmem
set_property PROGRAM.BLANK_CHECK 0 $cfgmem
set_property PROGRAM.ERASE 1 $cfgmem
set_property PROGRAM.CFG_PROGRAM 1 $cfgmem
set_property PROGRAM.VERIFY 1 $cfgmem
set_property PROGRAM.CHECKSUM 0 $cfgmem
create_hw_bitstream -hw_device $device [get_property PROGRAM.HW_CFGMEM_BITFILE $device]
program_hw_devices $device
refresh_hw_device $device
program_hw_cfgmem -hw_cfgmem $cfgmem
puts "U4_PROGRAMMED_AND_VERIFIED"
# PROGRAM_B: the FPGA loads the new contents of U4, as at power-on.
boot_hw_device $device
after 3000
refresh_hw_device -update_hw_probes false $device
puts "DONE=[get_property REGISTER.IR.BIT5_DONE $device]"
close_hw_target
disconnect_hw_server
close_hw_manager
