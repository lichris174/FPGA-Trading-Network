# Top-level build commands.
#
#   make test    run every cocotb testbench under tb/
#   make clean   remove simulation build output

TB_DIRS := $(sort $(dir $(wildcard tb/*/Makefile)))

.PHONY: test clean

test:
	@set -e; for d in $(TB_DIRS); do \
		echo "==> $$d"; \
		$(MAKE) -C $$d; \
		if grep -q '<failure' $$d/results.xml; then \
			echo "FAILED: $$d"; exit 1; \
		fi; \
	done

clean:
	@for d in $(TB_DIRS); do \
		$(MAKE) -C $$d clean >/dev/null 2>&1 || true; \
		rm -rf $$d/sim_build $$d/results.xml $$d/__pycache__; \
	done
