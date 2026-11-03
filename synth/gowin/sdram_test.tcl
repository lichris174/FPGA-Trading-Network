# Synthesis, place and route of the SDRAM board test (synth/sdram_test_top.v).
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/sdram_test.tcl
#
# Use forward slashes in the script path. Bitstream:
# synth/gowin/build_sdram/impl/pnr/sdram_test.fs. Load it into SRAM with
#   programmer_cli.exe --device GW2AR-18C --run 2 --fsFile <that file>

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build_sdram"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/sdram/sdram_ctrl.v"
add_file "$root/synth/uart_tx.v"
add_file "$root/synth/sdram_memtest.v"
add_file "$root/synth/sdram_test_top.v"
add_file "$root/synth/sdram_test.cst"
add_file "$root/synth/sdram_test.sdc"

set_option -top_module sdram_test_top
set_option -output_base_name sdram_test
set_option -verilog_std v2001

run all
