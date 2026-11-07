# Synthesis, place and route of the order book with the SDRAM and the PLL (synth/order_book_top.v).
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/order_book.tcl
#
# Use forward slashes in the script path. Bitstream:
# synth/gowin/build_book_full/impl/pnr/order_book.fs. Load it into SRAM with
#   programmer_cli.exe --device GW2AR-18C --run 2 --fsFile <that file>

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build_book_full"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/sdram/sdram_ctrl.v"
add_file "$root/rtl/book/book_levels.v"
add_file "$root/rtl/book/order_book.v"
add_file "$root/synth/order_book_top.v"
add_file "$root/synth/order_book.cst"
add_file "$root/synth/order_book.sdc"

set_option -top_module order_book_top
set_option -output_base_name order_book
set_option -verilog_std v2001

run all
