# cocotb tests for rtl/common/fifo.v. Run from this folder with `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

VERILOG_SOURCES += $(REPO_ROOT)/rtl/common/fifo.v

COCOTB_TOPLEVEL = fifo
COCOTB_TEST_MODULES = test_fifo

# A 4-entry RAM so the tests fill and overflow it quickly.
COMPILE_ARGS += -GWIDTH=16 -GADDR_BITS=2

include $(shell cocotb-config --makefiles)/Makefile.sim
