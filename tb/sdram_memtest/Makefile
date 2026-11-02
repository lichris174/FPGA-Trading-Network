# cocotb test for synth/sdram_memtest.v (the SDRAM board test) against tb/sdram/sdram_model.py,
# with 16 groups instead of 2^18 and a fast UART so a full read-latency sweep runs in seconds.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

export SDRAM_INIT_WAIT ?= 100
export SDRAM_REF_INTERVAL ?= 780
export MEMTEST_CLKS_PER_BIT ?= 8

VERILOG_SOURCES += $(REPO_ROOT)/rtl/sdram/sdram_ctrl.v $(REPO_ROOT)/synth/uart_tx.v $(REPO_ROOT)/synth/sdram_memtest.v
EXTRA_ARGS += -GGROUP_BITS=4 -GCLKS_PER_BIT=$(MEMTEST_CLKS_PER_BIT) -GINIT_WAIT=$(SDRAM_INIT_WAIT) \
              -GREF_INTERVAL=$(SDRAM_REF_INTERVAL) -GBEAT_BIT=4 -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

COCOTB_TOPLEVEL = sdram_memtest
COCOTB_TEST_MODULES = test_sdram_memtest

export PYTHONPATH := $(REPO_ROOT)/tb/sdram:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
