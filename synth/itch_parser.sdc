// Target clock for the parser on its own: 100 MHz.
create_clock -name clk -period 10 -waveform {0 5} [get_ports {clk}]
