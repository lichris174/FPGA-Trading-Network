# Synthesis, place and route of parser, FIFO and order book with the SDRAM and the PLL (synth/itch_book_top.v).
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/itch_book.tcl
#
# Use forward slashes in the script path. Bitstream:
# synth/gowin/build_itch_book/impl/pnr/itch_book.fs. Load it into SRAM with
#   programmer_cli.exe --device GW2AR-18C --run 2 --fsFile <that file>

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build_itch_book"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/itch/itch_parser.v"
add_file "$root/rtl/common/fifo.v"
add_file "$root/rtl/sdram/sdram_ctrl.v"
add_file "$root/rtl/book/book_levels.v"
add_file "$root/rtl/book/order_book.v"
add_file "$root/rtl/book/itch_book.v"
add_file "$root/synth/itch_book_top.v"
add_file "$root/synth/itch_book.cst"
add_file "$root/synth/itch_book.sdc"

set_option -top_module itch_book_top
set_option -output_base_name itch_book
set_option -include_path "$root/rtl/itch"
set_option -verilog_std v2001

run all
