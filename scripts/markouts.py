"""How much do resting (maker) orders keep of the spread once the price moves on?

    python scripts/markouts.py FILE SYMBOL data/raw/aapl.npz data/raw/results/mlp16_int8.json

Replays the day through the golden feature engine and records every
execution of a displayed resting order (E and C messages) during
regular hours. For each fill, in cents per share, share-weighted:

    half-spread  what the maker earned against the mid just before the fill
    adverse      how far the mid moved against the maker k rows later
    kept         half-spread minus adverse, before any rebate

Fills are split by whether they emptied their price level (an order
joining at the back of the queue mostly fills this way) and, on the
validation rows only, by what the int8 model predicted just before the
fill. The test day is never opened here.
"""

import argparse
import json

import numpy as np

from golden.features import FeatureEngine
from golden.itch.parser import decode
from golden.itch.tools import filter_stock
from train.data import DOWN, FLAT, LABEL_K, UP, split
from train.quantize import forward, from_json

HORIZONS = (10, 50, 100)


def collect_fills(path, symbol):
    """Rows (row before the fill, maker side +1/-1, price, shares, emptied level 0/1) and the row count."""
    eng = FeatureEngine()
    n_rows, fills = 0, []
    for raw in filter_stock(path, symbol):
        m = decode(raw)
        fill = None
        if m["message_type"] in ("E", "C") and eng.market_open and eng.trading and n_rows > 0:
            # Look the order up before the book applies the execution: a full fill removes it.
            o = eng.book.orders.get(m["order_reference_number"])
            if o is not None:
                price = m["execution_price"] if m["message_type"] == "C" else o.price
                fill = (n_rows - 1, 1 if o.side == "B" else -1, price, m["executed_shares"], o.side, o.price)
        if eng.step(m) is not None:
            n_rows += 1
        if fill is not None:
            r, s, price, shares, side, level = fill
            fills.append((r, s, price, shares, int(eng.book.levels[side].get(level, 0) == 0)))
    return np.array(fills, dtype=np.int64).reshape(-1, 5), n_rows


def print_row(name, mask, f, mid2):
    r, s, price, shares, _ = f[mask].T
    if shares.sum() == 0:
        print(f"{name:<34}no fills")
        return
    w = shares.astype(float)
    mid0 = mid2[r] / 2
    half = s * (mid0 - price)
    cols = [f"{name:<34}{mask.sum():>9,}{np.average(half, weights=w) / 100:>9.3f}"]
    for h in HORIZONS:
        adverse = -s * (mid2[r + h] / 2 - mid0)
        cols.append(f"{np.average(adverse, weights=w) / 100:>9.3f} /{np.average(half - adverse, weights=w) / 100:>7.3f}")
    print("".join(cols))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python scripts/markouts.py")
    p.add_argument("file", help="ITCH file the .npz was built from")
    p.add_argument("symbol")
    p.add_argument("train_day", help=".npz from golden.dataset")
    p.add_argument("model", help="_int8.json from train.quantize")
    a = p.parse_args(argv)

    f, n_rows = collect_fills(a.file, a.symbol)
    d = np.load(a.train_day)
    mid2 = d["mid2"]
    if len(mid2) != n_rows:
        raise SystemExit(f"{a.train_day} has {len(mid2):,} rows but the replay gave {n_rows:,}; wrong file?")
    f = f[f[:, 0] + max(HORIZONS) < n_rows]
    r, s, _, shares, emptied = f.T

    q = from_json(json.loads(open(a.model).read()))
    _, va = split(len(d[f"label_k{LABEL_K}"]))
    in_va = r >= va.start
    pred = np.full(len(r), -1)
    pred[in_va] = forward(q, d["features"][r[in_va]]).argmax(axis=1)
    warned = ((s == 1) & (pred == DOWN)) | ((s == -1) & (pred == UP))
    favour = ((s == 1) & (pred == UP)) | ((s == -1) & (pred == DOWN))

    print(f"{len(f):,} maker fills, {shares.sum():,} shares. Cents per share, share-weighted.\n")
    print(f"{'':<34}{'fills':>9}{'half-spr':>9}" + "".join(f"{'adv / kept k=' + str(h):>18}" for h in HORIZONS))
    rows = [
        ("all fills, whole day", np.ones(len(f), bool)),
        ("  fill left level intact", emptied == 0),
        ("  fill emptied the level", emptied == 1),
        (None, None),
        ("validation rows, all", in_va),
        ("  model warned (against maker)", in_va & warned),
        ("  model flat", in_va & (pred == FLAT)),
        ("  model in maker's favour", in_va & favour),
        ("  warned, level emptied", in_va & warned & (emptied == 1)),
        ("  not warned, level emptied", in_va & ~warned & (emptied == 1)),
    ]
    for name, mask in rows:
        if name is None:
            print()
        else:
            print_row(name, mask, f, mid2)


if __name__ == "__main__":
    main()
