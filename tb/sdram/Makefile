# cocotb tests for rtl/sdram/sdram_ctrl.v, checked by the timing model in sdram_model.py.
# Run from this folder with `make`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

# Short power-up wait so the tests don't spend 20,000 clocks on it; the
# model reads the same values from the environment.
export SDRAM_INIT_WAIT ?= 100
export SDRAM_REF_INTERVAL ?= 780

VERILOG_SOURCES += $(REPO_ROOT)/rtl/sdram/sdram_ctrl.v
# SDRAM_PARAMS adds overrides, e.g. SDRAM_PARAMS=-GT_RCD=1 to check the model catches a violation.
EXTRA_ARGS += -GINIT_WAIT=$(SDRAM_INIT_WAIT) -GREF_INTERVAL=$(SDRAM_REF_INTERVAL) $(SDRAM_PARAMS)
# -G values arrive as 32-bit constants; the RTL itself lints clean with -Wall.
EXTRA_ARGS += -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

COCOTB_TOPLEVEL = sdram_ctrl
COCOTB_TEST_MODULES = test_sdram_ctrl

include $(shell cocotb-config --makefiles)/Makefile.sim
