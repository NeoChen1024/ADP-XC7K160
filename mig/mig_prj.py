#!/usr/bin/env python3
"""Writes MIG's project file, mig.prj, for the ADP-XC7K160's DDR3: two 256M x16 chips as one 32-bit
memory, on the pins the board's manual lists (UM098 section 2.11, ../pins.csv).

    mig_prj.py pins.csv [rate] >mig.prj

The rate is DDR3-800, 1066, 1300, 1600 or 1800, 1600 if none is given; ../docs/ddr3.md says which of them
ran on the board, and how. prj/ has what this writes for each, and generate.sh runs MIG on it.
MIG's own limits are for the part, the package and the speed grade it is told: an FBG676 at -2 is allowed
1066 at most. The faster rates name the FFG676, which has the same pins, and for 1600 and 1800 the 2.0 V
auxiliary I/O supply that the FBG does not have. The design is then built for the part on the board, so
the timing report is that part's; what MIG would not promise is the eye at the pins.

A Micron part of the same organization stands in for the PieceMakers PMF512816BBR-MBDN, an 1866 bin.
"""
import csv
import re
import sys

# tCK in ps, CL, CWL, the package MIG is told, its auxiliary I/O supply, Micron's speed suffix.
# With the 200 MHz oscillator MIG takes only periods it can make from it: 1500 and 1072 ps are refused.
RATES = {
    800:  (2500, 6, 5, "fbg676", "1.8V", "125"),
    1066: (1875, 7, 6, "fbg676", "1.8V", "125"),
    1300: (1538, 9, 7, "ffg676", "1.8V", "107"),
    1600: (1250, 11, 8, "ffg676", "2.0V", "107"),
    1800: (1111, 13, 9, "ffg676", "2.0V", "107"),
}
GRADE = "-2"

rate = int(sys.argv[2]) if len(sys.argv) > 2 else 1600
if rate not in RATES:
    sys.exit(f"no DDR3-{rate}: one of {', '.join(map(str, RATES))}")
TCK, CL, CWL, PACKAGE, AUX_IO, SPEED = RATES[rate]

pins = {}
for r in csv.DictReader(open(sys.argv[1])):
    s = r["manual_signal"]
    if not s.startswith("DDR3_") and not s.startswith("SYSCLK"):
        continue
    assert re.fullmatch(r"[A-Z]{1,2}\d{1,2}", r["pin"]), r["pin"]
    for pat, name in ((r"DDR3_DQ(\d+)", "ddr3_dq[{}]"), (r"DDR3_DM(\d)", "ddr3_dm[{}]"), (r"DDR3_A(\d+)", "ddr3_addr[{}]"),
                      (r"DDR3_BA(\d)", "ddr3_ba[{}]"), (r"DDR3_DQS(\d)_P", "ddr3_dqs_p[{}]"), (r"DDR3_DQS(\d)_N", "ddr3_dqs_n[{}]")):
        m = re.fullmatch(pat, s)
        if m:
            pins[name.format(int(m[1]))] = r["pin"]
            break
    else:
        pins[{"DDR3_RASn": "ddr3_ras_n", "DDR3_CASn": "ddr3_cas_n", "DDR3_Wen": "ddr3_we_n", "DDR3_CSn": "ddr3_cs_n[0]",
              "DDR3_CKE0": "ddr3_cke[0]", "DDR3_ODT0": "ddr3_odt[0]", "DDR3_CK_P": "ddr3_ck_p[0]", "DDR3_CK_N": "ddr3_ck_n[0]",
              "DDR3_RST_B": "ddr3_reset_n", "SYSCLK_P": "sys_clk_p", "SYSCLK_N": "sys_clk_n"}[s]] = r["pin"]
assert len(pins) == 73, len(pins)

def std(name):
    if name.startswith("ddr3_dqs"): return "DIFF_SSTL15_T_DCI"
    if name.startswith("ddr3_dq["): return "SSTL15_T_DCI"
    if name.startswith("ddr3_ck_"): return "DIFF_SSTL15"
    if name == "ddr3_reset_n": return "LVCMOS15"
    if name.startswith("sys_clk"): return "DIFF_SSTL15"
    return "SSTL15"

lines = []
for name in sorted(pins):
    clk = name.startswith("sys_clk")
    lines.append(f'      <Pin IN_TERM="" IOSTANDARD="{std(name)}" PADName="{pins[name]}" SLEW="{"" if clk else "FAST"}" '
                 f'VCCAUX_IO="{"DONTCARE" if clk else "HIGH" if AUX_IO == "2.0V" else "NORMAL"}" name="{name}"/>')

print(f'''<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<Project NoOfControllers="1">
  <ModuleName>mig</ModuleName>
  <dci_inouts_inputs>1</dci_inouts_inputs>
  <dci_inputs>1</dci_inputs>
  <Debug_En>OFF</Debug_En>
  <DataDepth_En>1024</DataDepth_En>
  <LowPower_En>ON</LowPower_En>
  <XADC_En>Enabled</XADC_En>
  <TargetFPGA>xc7k160t-{PACKAGE}/{GRADE}</TargetFPGA>
  <Version>4.2</Version>
  <SystemClock>Differential</SystemClock>
  <ReferenceClock>Use System Clock</ReferenceClock>
  <SysResetPolarity>ACTIVE LOW</SysResetPolarity>
  <BankSelectionFlag>FALSE</BankSelectionFlag>
  <InternalVref>0</InternalVref>
  <dci_hr_inouts_inputs>50 Ohms</dci_hr_inouts_inputs>
  <dci_cascade>1</dci_cascade>
  <Controller number="0">
    <MemoryDevice>DDR3_SDRAM/Components/MT41J256m16XX-{SPEED}</MemoryDevice>
    <TimePeriod>{TCK}</TimePeriod>
    <VccAuxIO>{AUX_IO}</VccAuxIO>
    <PHYRatio>4:1</PHYRatio>
    <InputClkFreq>200</InputClkFreq>
    <UIExtraClocks>0</UIExtraClocks>
    <MMCM_VCO>800</MMCM_VCO>
    <MMCMClkOut0> 1.000</MMCMClkOut0>
    <MMCMClkOut1>1</MMCMClkOut1>
    <MMCMClkOut2>1</MMCMClkOut2>
    <MMCMClkOut3>1</MMCMClkOut3>
    <MMCMClkOut4>1</MMCMClkOut4>
    <DataWidth>32</DataWidth>
    <DeepMemory>1</DeepMemory>
    <DataMask>1</DataMask>
    <ECC>Disabled</ECC>
    <Ordering>Normal</Ordering>
    <BankMachineCnt>4</BankMachineCnt>
    <CustomPart>FALSE</CustomPart>
    <NewPartName></NewPartName>
    <RowAddress>15</RowAddress>
    <ColAddress>10</ColAddress>
    <BankAddress>3</BankAddress>
    <MemoryVoltage>1.5V</MemoryVoltage>
    <UserMemoryAddressMap>BANK_ROW_COLUMN</UserMemoryAddressMap>
    <PinSelection>
{chr(10).join(lines)}
    </PinSelection>
    <System_Clock>
      <Pin Bank="33" PADName="{pins["sys_clk_p"]}/{pins["sys_clk_n"]}(CC_P/N)" name="sys_clk_p/n"/>
    </System_Clock>
    <System_Control>
      <Pin Bank="Select Bank" PADName="No connect" name="sys_rst"/>
      <Pin Bank="Select Bank" PADName="No connect" name="init_calib_complete"/>
      <Pin Bank="Select Bank" PADName="No connect" name="tg_compare_error"/>
    </System_Control>
    <TimingParameters>
      <Parameters tcke="7.5" tfaw="50" tras="37.5" trcd="15" trefi="7.8" trfc="260" trp="15" trrd="10" trtp="7.5" twtr="7.5"/>
    </TimingParameters>
    <mrBurstLength name="Burst Length">8 - Fixed</mrBurstLength>
    <mrBurstType name="Read Burst Type and Length">Sequential</mrBurstType>
    <mrCasLatency name="CAS Latency">{CL}</mrCasLatency>
    <mrMode name="Mode">Normal</mrMode>
    <mrDllReset name="DLL Reset">No</mrDllReset>
    <mrPdMode name="DLL control for precharge PD">Slow Exit</mrPdMode>
    <emrDllEnable name="DLL Enable">Enable</emrDllEnable>
    <emrOutputDriveStrength name="Output Driver Impedance Control">RZQ/7</emrOutputDriveStrength>
    <emrMirrorSelection name="Address Mirroring">Disable</emrMirrorSelection>
    <emrCSSelection name="Controller Chip Select Pin">Enable</emrCSSelection>
    <emrRTT name="RTT (nominal) - On Die Termination (ODT)">RZQ/4</emrRTT>
    <emrPosted name="Additive Latency (AL)">0</emrPosted>
    <emrOCD name="Write Leveling Enable">Disabled</emrOCD>
    <emrDQS name="TDQS enable">Enabled</emrDQS>
    <emrRDQS name="Qoff">Output Buffer Enabled</emrRDQS>
    <mr2PartialArraySelfRefresh name="Partial-Array Self Refresh">Full Array</mr2PartialArraySelfRefresh>
    <mr2CasWriteLatency name="CAS write latency">{CWL}</mr2CasWriteLatency>
    <mr2AutoSelfRefresh name="Auto Self Refresh">Enabled</mr2AutoSelfRefresh>
    <mr2SelfRefreshTempRange name="High Temparature Self Refresh Rate">Normal</mr2SelfRefreshTempRange>
    <mr2RTTWR name="RTT_WR - Dynamic On Die Termination (ODT)">Dynamic ODT off</mr2RTTWR>
    <PortInterface>NATIVE</PortInterface>
  </Controller>
</Project>''')
