# Synthesis, place and route of itch_parser on the Tang Nano 20K's GW2AR-18.
#
#   D:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin\gw_sh.exe synth/gowin/itch_parser.tcl
#
# Use forward slashes in the script path: gw_sh is a Tcl shell and
# treats backslashes as escapes.
#
# Output goes to synth/gowin/build/ (not committed). Reports:
#   impl/gwsynthesis/itch_parser.rpt.html   resources after synthesis
#   impl/pnr/itch_parser.rpt.txt            resources after place and route
#   impl/pnr/itch_parser.tr.html            timing (Fmax, slack)

set here [file normalize [file dirname [info script]]]
set root [file normalize "$here/../.."]
set build "$here/build"
file mkdir $build
cd $build

set_device -device_version C GW2AR-LV18QN88C8/I7

add_file "$root/rtl/itch/itch_parser.v"
add_file "$root/synth/itch_parser_top.v"
add_file "$root/synth/itch_parser.sdc"

set_option -top_module itch_parser_top
set_option -include_path "$root/rtl/itch"
set_option -output_base_name itch_parser
set_option -verilog_std v2001

run all
