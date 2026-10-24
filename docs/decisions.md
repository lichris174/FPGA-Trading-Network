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
