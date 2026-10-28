# Decision Log

A running record of the design choices I've made on this project and why. Newest entries go at the bottom.

---

## 2026-10-18: Simulation first, board last

I'm building and verifying everything in simulation before touching the Tang Nano 20K. Simulation shows me every signal on every cycle, so logic bugs are much easier to find there. The board only matters once the pipeline is correct, and at that point it's about timing and real latency numbers.

## 2026-10-18: Board-specific code stays in one folder

The pipeline itself (`rtl/`) takes a byte stream in and puts decisions out. It has no idea it's running on a Tang Nano. The UART, SDRAM and pin assignments will live in `boards/tang_nano_20k/` as a wrapper around it. That way most of the work runs in Verilator with no hardware, and if I move to a board with Ethernet later, I only add a new board folder.

## 2026-10-18: Plain Verilog 2005, not SystemVerilog

I know Verilog syntax better, and Yosys (the open-source synthesis tool for the Gowin chip) only supports part of SystemVerilog. Verilator handles SystemVerilog fine, but I don't want code that simulates and then fails to synthesize. The cost is losing structs and packages, which would be nice for message fields. I'll live without them.

## 2026-10-18: ITCH byte layouts defined once, in Python

When I get to the parser, the byte offsets for each message type will be written once in Python, and a script will generate a Verilog header from them. If I hand-copy offsets into both languages, they'll drift apart eventually and my tests will fail for reasons that have nothing to do with the logic.

## 2026-10-18: Build in iterations, Python before Verilog

I'm building the whole chain in Python first (parser, order book, features, model), then rebuilding each stage in Verilog. Every Verilog block then has a working Python version to be checked against. The order has to be parser, then book, then features, then model, since each stage feeds the next.

## 2026-10-19: Small model, no attention

The model will be logistic regression or a small MLP, not a transformer. The limit is the 48 hardware multipliers: each one does one multiply per clock cycle. A tiny MLP needs a few hundred multiplies per decision, which is a handful of cycles. Even a minimal transformer would need around 15,000 multiplies per decision, which works out to microseconds instead of nanoseconds. At that point a CPU could do the same job and there's no reason to use an FPGA.

The model still sees past data. The weights come from training on hours of history, and several features are running summaries (order flow imbalance, recent trade direction, lagged imbalance). I choose the summaries by hand instead of having attention learn them.

I'll test this rather than assume it. In iteration 4 I'll train models of increasing size and plot held-out accuracy against parameter count. If accuracy flattens early, the small model isn't giving anything up.

## 2026-10-20: Confidence threshold, chosen honestly

The model only trades when it's confident; otherwise it outputs HOLD. Raising the threshold raises accuracy on the trades it takes. Two rules keep that honest:

1. I choose the threshold on the validation day, never the test day.
2. I always report the number of trades next to the accuracy, as a curve, along with P&L after the spread and fees. High accuracy on 20 trades doesn't mean much.

## 2026-10-20: First MLP spec

- Inputs: about 12 features (imbalance, microprice minus mid, spread, order flow imbalance at three decay rates, signed trade volume, lagged imbalance, recent mid change)
- Baseline 12 → 16 → 3 (about 260 parameters), stretch 12 → 32 → 16 → 3 (about 1,000)
- ReLU between layers
- Outputs: up, flat, down. No softmax in hardware. Confidence is the gap between the top score and the second-best.
- 8-bit weights and activations, wider accumulators, rescale between layers with a bit shift
- Imbalance needs a division, so I'll use a reciprocal lookup table in block RAM
- Train in PyTorch with class weights, split strictly by time

## 2026-10-20: Top of book first

The order book will track only the best bid and best ask to start. Tracking the top 3 levels makes the Verilog book much harder, so multi-level features wait for a later iteration.

## 2026-10-22: The golden model is strict

The Python parser and order book raise an error on anything unexpected: an unknown message type, a length that disagrees with the spec, an unknown or duplicate order reference, or a cancel larger than the order. A golden model that skips bad input quietly would let bugs through to the hardware comparison. If a real file trips one of these, I want to know why before going further.

## 2026-10-22: Filter by stock locate, not symbol

Only Add Order messages carry the stock symbol. Executions, cancels, deletes and replaces carry only the stock locate code, which is assigned per day in the Stock Directory (R) message. The filter looks up the locate for the chosen symbol and keeps every message with that locate, plus system events.

## 2026-10-22: The book tracks every order, even though features use top of book

Knowing the best bid and ask isn't enough on its own. When the last order at the best price goes away, the book has to know the next best price, and cancels and executions name an order, not a price. So the golden book keeps every live order and the total shares at every price level. The features only read the top level. How to fit this into block RAM (renumbering order references on the PC, a price-indexed level array) gets decided in iteration 7.

## 2026-10-22: Features are integers from the start

The Verilog feature stage has to match the Python one bit for bit, so `golden/features.py` uses only what the FPGA can do cheaply: add, subtract, compare, arithmetic shift, a few multiplies and one lookup table. No floats and no division. Writing floats first and converting later would mean writing the features twice, and the second version would be the one that counts.

- Mid-price is kept doubled (`mid2 = bid + ask`) so it never needs a divide by two.
- Imbalance is Q1.15. The division uses a reciprocal table: shift the total to exactly 11 bits, shift the size of the difference by the same amount, multiply by the table entry, then put the sign back. Working on the size and restoring the sign keeps `imbalance(b, a) == -imbalance(a, b)` exactly, which shifting a negative number would break. Worst-case error is about 0.1% of full scale. The table has 1,024 entries of 17 bits, one block RAM, and the multiply is 12 x 17 bits, which fits one 18 x 18 multiplier.
- Microprice is used as `2 * (weighted mid - mid) = spread * imbalance >> 15`, so it reuses the imbalance and costs one multiply.
- Running averages (order flow imbalance, trade flow) decay as `x += e - (x >> s)`, which is a shift and two adds. OFI uses three rates (s = 3, 6, 9).

## 2026-10-22: Which book updates become rows

A row is written for every message that changes the book, but only when the market is in regular hours (System Event Q to M), the stock is trading (last Trading Action state T) and the book has both sides without being locked or crossed. The running state only advances on those updates, so the pre-market, halts and broken books can't leak into it. The first 10 valid updates only fill the lag history.

Rows are written on every book change, not only when the top of book changes. That gives more rows and matches how the hardware will run: once per message.

## 2026-10-22: Labels count book updates, not time

A row's label is the sign of the mid-price change over the next k rows (k = 10, 50, 100). Counting updates instead of seconds means the hardware never needs timestamps for this. The labeller only ever reads `mid2`, and it is the only code that looks forward. A test checks that feature rows don't change when more messages are appended after them.

First full day (AAPL, 1,141,322 rows): at k = 10 the labels are about 24% down, 52% flat, 24% up, and the sign of imbalance matches the next non-flat move 55% of the time. At k = 50 and 100, "flat" drops below 22% and imbalance alone falls under 50%, so its information is short-lived. Every feature fits in 18 bits signed.

## 2026-10-27: Nine features, not twelve

The first MLP spec guessed about 12 inputs. The feature list it names comes to 9 once the three OFI decay rates are counted as three features, so the model has 9 inputs. Everything below uses 9.

## 2026-10-27: mlp16 is the model

I trained four sizes, three seeds each, on the first 70% of the AAPL training day and scored them on the rest (validation macro-F1):

| Model | Parameters | Macro-F1 |
|---|---|---|
| sign of imbalance (rule) | 0 | 0.304 |
| logreg | 30 | 0.350 to 0.358 |
| mlp8 | 107 | 0.415 to 0.430 |
| mlp16 | 211 | 0.426 to 0.435 |
| mlp32x16 | 899 | 0.431 to 0.442 |

Accuracy flattens at mlp16, so the small model gives up almost nothing, as the "small model, no attention" entry predicted. mlp16 (9 → 16 → 3) is the one going into hardware.

## 2026-10-27: int8 number formats

Every scale is a power of two, so every rescale is a shift.

- Inputs: each raw golden feature is shifted to Q0.7 and clamped to int8. The shift per feature comes from the training rows.
- Weights: int8, one power-of-two scale per layer.
- Biases: stored at the accumulator's scale, so they are added once with no shift.
- Hidden layers: rounding shift (add half a step, then shift), then ReLU and clamp to [0, 127] in one step. A plain shift rounds everything down and biases each layer by half a step.
- Output: the last layer's accumulators are the scores. The decision is the argmax, ties going to the lower class. Confidence is top score minus second.

Quantizing changes macro-F1 by less than 0.002 for every model, and the int8 and float predictions agree on 97 to 99% of validation rows. Worst-case accumulators need 18 bits. `train/quantize.py` is the reference the Verilog model has to match bit for bit.

## 2026-10-27: Paying the spread can't win at this horizon

The backtest trades 1 share whenever the model says up or down with confidence at least T, holds for 10 rows, buys at the ask, sells at the bid and pays $0.003 per share each way.

On the validation rows the model is right on 70 to 81% of trades where the price moved, but it makes only 0.2 to 0.4 cents per trade before costs, and loses 2.2 to 5.2 cents after them. Higher thresholds lose more, because the model is most confident when the spread is wide. The rule fixed in advance (best net per trade with at least 200 trades) picked T = 0. The test day, run once with T frozen: 69% right of moves, +0.18 cents before costs, -3.32 cents after.

Even perfect foresight loses at 10 rows: the mid moves 0.36 cents on average, and essentially no row on the training day (0.0% to one decimal) moves more than the spread and fees. Perfect foresight only breaks even somewhere between 100 and 1,000 rows (2 to 20 seconds), where these features say little.

So this project is measured on prediction accuracy, bit-exact hardware and latency, not P&L.

## 2026-10-27: Collecting the spread doesn't save it either

Resting orders collect the spread instead of paying it, but they get filled just before the price moves against them (adverse selection). `scripts/markouts.py` measured this on all 48,709 resting-order fills of the training day. Makers earn 0.89 cents per share against the mid and keep +0.09 after 10 rows, -0.27 after 50 and -0.38 after 100. Fills that empty their price level, which is how an order joining at the back of the queue mostly fills, keep -0.14, -0.43 and -0.51.

The model barely helps. Fills it warned about move only 0.1 to 0.3 cents further against the maker, and when a fill empties the level, warned and unwarned fills lose about the same. With the rebate (about 0.2 cents) a back-of-queue maker still loses about 0.3 cents per share, before counting the delay on cancels. Winning needs the front of the queue or signals from other markets, both out of scope. I'm keeping BUY / SELL / HOLD.

## 2026-10-28: The Verilog parser takes one byte per clock

The parser reads the same length-prefixed stream as the sample files, one byte per clock, and pulses `msg_valid` the cycle after a message's last byte with every field the book needs. Real HFT parsers take 8 to 64 bytes per clock from a 10G MAC, but UART replay delivers far less than a byte per cycle, and one byte per clock is much easier to get right. Widening the datapath is a later optimisation, with a before-and-after latency number.

- Offsets come from `rtl/itch/itch_spec.vh`, generated from `golden/itch/spec.py`. A pytest check fails if the header is stale.
- The stock filter is in hardware: a locate register, plus system events, which apply to every stock. It costs one comparator.
- A decoded type with the wrong length pulses `msg_error` and is dropped, the hardware version of the golden parser's strictness. Other types are skipped by their length prefix.
- `data/vectors/aapl_15k.itch`, the first 15,000 AAPL messages of a day, is committed so CI tests real data. It is continuous so the order book testbench can reuse it.

All 14,823 decoded messages in the slice match the golden parser, also with random gaps in the input. On the GW2AR-18, after place and route, the parser takes 514 LUTs and 369 registers (3% of each) and reaches 144 MHz against a 100 MHz target. The synthesis wrapper folds the outputs into a checksum because the parser has about 330 output bits and the package has 66 pins.
