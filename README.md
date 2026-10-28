# FPGA Trading Network

A tick-to-decision trading pipeline built in Verilog for the Sipeed Tang Nano 20K.

Recorded Nasdaq ITCH 5.0 market data is replayed into the FPGA, which parses each message, rebuilds the order book, computes a few signals, runs a small quantized neural network, and outputs BUY, SELL or HOLD. A cycle counter measures how many clock cycles each decision takes.

Every hardware block is checked message by message against a Python reference model (the "golden model") using cocotb and Verilator. Everything runs in simulation first. The physical board comes last.

This is a learning project. It is not meant to trade real money.

For the full background (markets, ITCH, the model, tradeoffs) see [docs/overview.md](docs/overview.md). Design decisions are logged in [docs/decisions.md](docs/decisions.md).

## Layout

| Folder | Purpose |
|---|---|
| `docs/` | Project overview and decision log |
| `golden/` | Python golden model: ITCH spec, parser, order book, features, labels |
| `train/` | Training, int8 quantization and the backtest |
| `scripts/` | One-off checks: MeatPy cross-check, maker markouts |
| `tests/` | pytest tests for the golden model and training code |
| `rtl/` | Verilog for the pipeline, one subfolder per stage |
| `tb/` | cocotb testbenches, mirroring `rtl/` |
| `synth/` | Gowin synthesis scripts, wrappers and timing constraints |
| `data/vectors/` | Small slices of real ITCH data for the testbenches (committed) |
| `.github/workflows/` | Runs every test on each push |

Full market data files and training results live in `data/raw/`, which is not committed. Folders for the board files get added in the iteration that first needs them.

## Running the tests

Requires Python 3.10+, Verilator 5.036 or newer, and the Python packages in `pyproject.toml`.

```
pip install -e .
make test
```

Make and Verilator can't build in a path with spaces. If the repo path has one, link it somewhere without and point the build there, for example in WSL:

```
ln -s "/mnt/c/path with spaces/FPGA_Trading_Network" ~/ftn
cd ~/ftn/tb/itch
make REPO_ROOT=$HOME/ftn SIM_BUILD=$HOME/sim_build/itch
```

Add `VERILATOR_TRACE=1` (with a fresh `SIM_BUILD` folder) to write `dump.vcd` for GTKWave.

## Synthesis

With the Gowin IDE installed (Windows), from the repo root. Use forward slashes in the script path:

```
gw_sh.exe synth/gowin/itch_parser.tcl
```

Reports go to `synth/gowin/build/impl/`: `pnr/itch_parser.rpt.html` for resources, `pnr/itch_parser_tr_content.html` for timing.

## Using the golden model on a real file

Download a sample file from emi.nasdaq.com/ITCH (it can stay gzipped), then:

```
python -m golden.itch.tools stats  FILE                 # message counts; every message must decode
python -m golden.itch.tools filter FILE AAPL out.itch   # one stock, same format
python -m golden.itch.tools replay FILE AAPL            # run the order book, print final top of book
python -m golden.dataset FILE AAPL data/raw/aapl.npz    # feature table and up/flat/down labels
python -m golden.itch.tools head FILE 15000 data/vectors/aapl_15k.itch   # test vector slice
python -m golden.itch.gen_verilog                       # regenerate rtl/itch/itch_spec.vh from spec.py
```

## Training and the backtest

Needs PyTorch: `pip install -e ".[train]"`. Results go to `data/raw/results/`. The test day is only opened by the last command.

```
python -m train.sweep    data/raw/aapl.npz                  # macro-F1 vs model size, several seeds
python -m train.fit      data/raw/aapl.npz --model mlp16    # train one model
python -m train.quantize data/raw/aapl.npz --model mlp16    # int8 model, the reference for the Verilog
python -m train.backtest data/raw/aapl.npz --model mlp16    # confidence threshold and P&L on validation
python -m train.backtest data/raw/aapl.npz --model mlp16 --test data/raw/aapl_day2.npz   # test day, once
```

Results so far are in [docs/decisions.md](docs/decisions.md): mlp16 (211 parameters) is right on about 70 to 80% of moves, loses nothing when quantized to int8, and can't beat the spread at a 10-update horizon.

## Roadmap

| # | Iteration | Done when | Status |
|---|---|---|---|
| 0 | Setup: repo skeleton, toolchain, first cocotb test | `make test` passes locally and in CI | Done |
| 1 | Python ITCH parser | Sample file decodes, message counts by type, filtered to one stock | Done |
| 2 | Python order book | Best bid/ask matches MeatPy on the same file | Done |
| 3 | Features and labels | Feature table and up/flat/down labels generated | Done |
| 4 | Training and quantization | Logistic regression vs MLP on a held-out day; accuracy vs model size chart | Done |
| 5 | Backtest | Simulated P&L with spread and fees charged | Done |
| 6 | Verilog ITCH parser | Matches iteration 1 on every message | Done |
| 7 | Verilog order book | Matches iteration 2 after every message | Next |
| 8 | Verilog features and model | Bit-exact match with iterations 3 and 4 | |
| 9 | Tang Nano 20K | UART load, replay, real latency numbers | |
