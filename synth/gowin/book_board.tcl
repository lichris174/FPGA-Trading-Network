# Synthesis, place and route of the order book replay board (synth/book_board_top.v).
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/book_board.tcl
#
# Use forward slashes in the script path. Bitstream:
# synth/gowin/build_book_board/impl/pnr/book_board.fs. Load it into SRAM with
#   programmer_cli.exe --device GW2AR-18C --run 2 --fsFile <that file>

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build_book_board"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/itch/itch_parser.v"
add_file "$root/rtl/common/fifo.v"
add_file "$root/rtl/sdram/sdram_ctrl.v"
add_file "$root/rtl/book/book_levels.v"
add_file "$root/rtl/book/order_book.v"
add_file "$root/rtl/book/itch_book.v"
add_file "$root/synth/uart_rx.v"
add_file "$root/synth/uart_tx.v"
add_file "$root/synth/book_board.v"
add_file "$root/synth/book_board_top.v"
add_file "$root/synth/book_board.cst"
add_file "$root/synth/book_board.sdc"

set_option -top_module book_board_top
set_option -output_base_name book_board
set_option -include_path "$root/rtl/itch"
set_option -verilog_std v2001

run all
