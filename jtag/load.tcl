# Configures the FPGA over JTAG, through a Xilinx Virtual Cable server. Nothing is written to a flash.
#
#   vivado -mode batch -source load.tcl -tclargs <bitstream> <host:port>
lassign $argv bit xvc
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
set_property PROGRAM.FILE $bit $device
program_hw_devices $device
refresh_hw_device -update_hw_probes false $device
puts "DONE=[get_property REGISTER.IR.BIT5_DONE $device]"
close_hw_target
disconnect_hw_server
close_hw_manager
