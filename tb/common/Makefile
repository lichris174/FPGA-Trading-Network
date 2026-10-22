# cocotb tests for the shared building blocks in rtl/common/.
# Run from this folder with `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

VERILOG_SOURCES += $(REPO_ROOT)/rtl/common/cycle_counter.v

COCOTB_TOPLEVEL = cycle_counter
COCOTB_TEST_MODULES = test_cycle_counter

# Small counter so the wraparound test is quick.
COMPILE_ARGS += -GWIDTH=8

include $(shell cocotb-config --makefiles)/Makefile.sim
