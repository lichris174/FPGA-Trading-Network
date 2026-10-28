# cocotb tests for rtl/itch/itch_parser.v, checked against golden/itch/parser.py.
# Run from this folder with `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

VERILOG_SOURCES += $(REPO_ROOT)/rtl/itch/itch_parser.v
COMPILE_ARGS += -I$(REPO_ROOT)/rtl/itch

COCOTB_TOPLEVEL = itch_parser
COCOTB_TEST_MODULES = test_itch_parser

# The tests import the golden model from the repo root.
export PYTHONPATH := $(REPO_ROOT):$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
