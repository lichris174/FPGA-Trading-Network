# Synthesis, place and route of book_levels on the Tang Nano 20K's GW2AR-18.
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/book_levels.tcl
#
# Use forward slashes in the script path: gw_sh is a Tcl shell and
# treats backslashes as escapes.
#
# Output goes to synth/gowin/build_book/ (not committed). Reports:
#   impl/gwsynthesis/book_levels.rpt.html   resources after synthesis
#   impl/pnr/book_levels.rpt.txt            resources after place and route
#   impl/pnr/book_levels.tr.html            timing (Fmax, slack)

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build_book"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/book/book_levels.v"
add_file "$root/synth/book_levels_top.v"
add_file "$root/synth/book_levels.sdc"

set_option -top_module book_levels_top
set_option -output_base_name book_levels
set_option -verilog_std v2001

run all
