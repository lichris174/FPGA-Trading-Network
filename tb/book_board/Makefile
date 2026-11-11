# cocotb tests for synth/book_board.v (serial replay protocol, book, report line), checked
# against golden/hw_book.py with the SDRAM model from tb/sdram. Run from this folder with
# `make`, or from the repo root with `make test`.

SIM ?= verilator
TOPLEVEL_LANG = verilog

REPO_ROOT := $(abspath ../..)

# 2^14 buckets keeps the table clear at reset short; 8 clocks per serial bit (the
# receiver's minimum) keeps it fast. The board runs 32 (2 Mbaud at 64.8 MHz).
export BOOK_BUCKET_BITS ?= 14
export BOARD_CLKS_PER_BIT ?= 8
export SDRAM_INIT_WAIT ?= 100
export SDRAM_REF_INTERVAL ?= 500

VERILOG_SOURCES += $(REPO_ROOT)/rtl/itch/itch_parser.v $(REPO_ROOT)/rtl/common/fifo.v \
                   $(REPO_ROOT)/rtl/sdram/sdram_ctrl.v $(REPO_ROOT)/rtl/book/book_levels.v \
                   $(REPO_ROOT)/rtl/book/order_book.v $(REPO_ROOT)/rtl/book/itch_book.v \
                   $(REPO_ROOT)/synth/uart_rx.v $(REPO_ROOT)/synth/uart_tx.v $(REPO_ROOT)/synth/book_board.v
COMPILE_ARGS += -I$(REPO_ROOT)/rtl/itch
EXTRA_ARGS += -GBUCKET_BITS=$(BOOK_BUCKET_BITS) -GCLKS_PER_BIT=$(BOARD_CLKS_PER_BIT) -GINIT_WAIT=$(SDRAM_INIT_WAIT) \
              -GREF_INTERVAL=$(SDRAM_REF_INTERVAL) -GBEAT_BIT=4 -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND

COCOTB_TOPLEVEL = book_board
COCOTB_TEST_MODULES = test_book_board

# The tests import the golden model from the repo root, the protocol from scripts/
# and the SDRAM model from tb/sdram.
export PYTHONPATH := $(REPO_ROOT):$(REPO_ROOT)/scripts:$(REPO_ROOT)/tb/sdram:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
