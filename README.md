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
| `golden/` | Python golden model: ITCH spec, parser, order book |
| `tests/` | pytest tests for the golden model |
| `rtl/` | Verilog for the pipeline, one subfolder per stage |
| `tb/` | cocotb testbenches, mirroring `rtl/` |
| `.github/workflows/` | Runs every test on each push |

Folders for data, the backtest and the board files get added in the iteration that first needs them.

## Running the tests

Requires Python 3.10+, Verilator 5.036 or newer, and the Python packages in `pyproject.toml`.

```
pip install -e .
make test
```

## Using the golden model on a real file

Download a sample file from emi.nasdaq.com/ITCH (it can stay gzipped), then:

```
python -m golden.itch.tools stats  FILE                 # message counts; every message must decode
python -m golden.itch.tools filter FILE AAPL out.itch   # one stock, same format
python -m golden.itch.tools replay FILE AAPL            # run the order book, print final top of book
python -m golden.dataset FILE AAPL data/raw/aapl.npz    # feature table and up/flat/down labels
```

## Roadmap

| # | Iteration | Done when |
|---|---|---|
| 0 | Setup: repo skeleton, toolchain, first cocotb test | `make test` passes locally and in CI |
| 1 | Python ITCH parser | Sample file decodes, message counts by type, filtered to one stock |
| 2 | Python order book | Best bid/ask matches MeatPy on the same file |
| 3 | Features and labels | Feature table and up/flat/down labels generated |
| 4 | Training and quantization | Logistic regression vs MLP on a held-out day; accuracy vs model size chart |
| 5 | Backtest | Simulated P&L with spread and fees charged |
| 6 | Verilog ITCH parser | Matches iteration 1 on every message |
| 7 | Verilog order book | Matches iteration 2 after every message |
| 8 | Verilog features and model | Bit-exact match with iterations 3 and 4 |
| 9 | Tang Nano 20K | UART load, replay, real latency numbers |
