# Vivado flow and problems that were found

The designs here use a non-project batch flow; `example-ddr3-test/build.tcl` is a short example.
Each item below cost time to find **[measured, Vivado 2025.2.1]**:

- **Vivado writes a bitstream when timing fails.** Always check `report_timing_summary`. `build.sh`
  does.
- **Vivado reads some SystemVerilog differently from a simulator, and gives only a warning.** For
  example, a member of a function result (`decode(word).is_move`) becomes a constant 0. To stop
  this class of error, make `Synth 8-3848` (a net without a driver) an error:
  `set_msg_config -id {Synth 8-3848} -new_severity ERROR`.
- Two write statements to one LUT RAM are inferred as "read, then write", and the RAM doubles.
- **If separate `always_ff` blocks of a generate loop assign the bits of one vector, the initial
  value of the declaration is lost.** Vivado then takes a bit that is only set, and never cleared,
  as a constant 1 (`Synth 8-3333 propagating constant 1`, only an INFO). A simulator starts it at
  the initial value 0. The cure is one `always_ff` with a `for` loop and an explicit clear.
- The slack says only that timing passed. It does not say how much margin the design has: the tool
  stops when timing passes. A change of one line of RTL can move the slack by 0.1 ns. After each
  RTL change, build again before you trust the clock. The same netlist gives the same result each
  time.
- At 400 MHz, the clock skew between clock regions can be 0.29 ns. That is more than the margin of
  a path. A pblock that keeps the most critical logic in one clock region (for example
  `CLOCKREGION_X0Y0`) makes the timing close reliably.
- `place_design -directive ExtraTimingOpt` rescued a design that failed by 0.034 ns.
- `opt_design -directive NoBramPowerOpt`: by default, Vivado adds power gates to the enables of the
  block RAMs. That is more logic on a critical path.
- Block RAM at 400 MHz: without the output register (`DO_REG`), the data comes about 1.8 ns after
  the clock. The address and enable routes are also long. One design gave two cycles to each side of
  the block RAM (multicycle constraints). Each port is enabled only in the cycle in which the RAM
  must sample. An enable that is high while the address comes late can corrupt the RAM (UG473).
- For small storage with low latency (tens to hundreds of entries), distributed RAM (LUT RAM) is
  faster than flip-flops with a large mux.
- This device has many LUTs. If a shared adder or mux makes a path long, make a copy of the logic
  first.
