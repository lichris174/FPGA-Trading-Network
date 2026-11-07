# cocotb tests for rtl/book/order_book.v, checked against golden/hw_book.py with the
# SDRAM model from tb/sdram. Run from this folder with `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

# 2^14 buckets so clearing the table at reset takes about 200,000 clocks
# instead of 3.4 million, and the 15,000-message slice still fits without
# filling the stash. The tests build the Python model with the same size.
export BOOK_BUCKET_BITS ?= 14
export SDRAM_INIT_WAIT ?= 100
export SDRAM_REF_INTERVAL ?= 500

VERILOG_SOURCES += $(REPO_ROOT)/rtl/sdram/sdram_ctrl.v $(REPO_ROOT)/rtl/book/book_levels.v $(REPO_ROOT)/rtl/book/order_book.v
EXTRA_ARGS += -GBUCKET_BITS=$(BOOK_BUCKET_BITS) -GINIT_WAIT=$(SDRAM_INIT_WAIT) -GREF_INTERVAL=$(SDRAM_REF_INTERVAL) \
              -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

COCOTB_TOPLEVEL = order_book
COCOTB_TEST_MODULES = test_order_book

# The tests import the golden model from the repo root and the SDRAM model from tb/sdram.
export PYTHONPATH := $(REPO_ROOT):$(REPO_ROOT)/tb/sdram:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
