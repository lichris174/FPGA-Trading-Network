// Target clock: the system clock the book shares with the SDRAM controller, 64.8 MHz.
create_clock -name clk -period 15.432 -waveform {0 7.716} [get_ports {clk}]
