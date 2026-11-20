# Writes package-dump.csv, every pin of the XC7K160T's FBG676 package as Vivado has it: its bank, its
# function and its delay in the package. vivado -mode batch -nojournal -nolog -source package-dump.tcl
create_project -in_memory -part xc7k160tfbg676-2
set_property design_mode PinPlanning [current_fileset]
open_io_design -name io
set f [open package-dump.csv w]
puts $f "pin,bank,pin_func,min_delay_ps,max_delay_ps"
foreach p [get_package_pins] {
    puts $f "[get_property NAME $p],[get_property BANK $p],[get_property PIN_FUNC $p],[get_property MIN_DELAY $p],[get_property MAX_DELAY $p]"
}
close $f
