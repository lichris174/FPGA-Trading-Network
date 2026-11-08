# cocotb tests for rtl/book/itch_book.v (parser -> FIFO -> order book), checked against
# golden/hw_book.py with the SDRAM model from tb/sdram. Run from this folder with `make`,
# or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

# 2^14 buckets keeps the table clear at reset short; see tb/order_book/Makefile.
export BOOK_BUCKET_BITS ?= 14
export SDRAM_INIT_WAIT ?= 100
export SDRAM_REF_INTERVAL ?= 500

VERILOG_SOURCES += $(REPO_ROOT)/rtl/itch/itch_parser.v $(REPO_ROOT)/rtl/common/fifo.v \
                   $(REPO_ROOT)/rtl/sdram/sdram_ctrl.v $(REPO_ROOT)/rtl/book/book_levels.v \
                   $(REPO_ROOT)/rtl/book/order_book.v $(REPO_ROOT)/rtl/book/itch_book.v
COMPILE_ARGS += -I$(REPO_ROOT)/rtl/itch
EXTRA_ARGS += -GBUCKET_BITS=$(BOOK_BUCKET_BITS) -GINIT_WAIT=$(SDRAM_INIT_WAIT) -GREF_INTERVAL=$(SDRAM_REF_INTERVAL) \
              -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

COCOTB_TOPLEVEL = itch_book
COCOTB_TEST_MODULES = test_itch_book

# The tests import the golden model from the repo root and the SDRAM model from tb/sdram.
export PYTHONPATH := $(REPO_ROOT):$(REPO_ROOT)/tb/sdram:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
