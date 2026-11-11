# cocotb tests for rtl/book/, checked against the hardware book model in golden/hw_book.py.
# Run from this folder with `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

VERILOG_SOURCES += $(REPO_ROOT)/rtl/book/book_levels.v

COCOTB_TOPLEVEL = book_levels
COCOTB_TEST_MODULES = test_book_levels

# The tests import the golden model from the repo root.
export PYTHONPATH := $(REPO_ROOT):$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
