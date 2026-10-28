"""Confidence threshold and P&L after the spread and fees, using the int8 model.

    python -m train.backtest data/raw/aapl.npz --model mlp16
    python -m train.backtest data/raw/aapl.npz --model mlp16 --test data/raw/aapl_day2.npz

Decision. The model's three integer scores give a prediction (argmax)
and a confidence (top score minus second). It trades only when the
prediction is up or down and the confidence is at least a threshold T.
Everything else is HOLD.

Trade. One share, held for k rows, the same horizon as the labels. Up
buys at the ask and sells at the bid k rows later; down sells at the
bid and buys back at the ask. So every trade crosses the spread twice
and pays the fee twice. One position at a time: a signal while a trade
is open is ignored. Prices are ITCH units (1 = $0.0001, 100 = 1 cent).

Not modelled: latency, queue position, partial fills, market impact.
Each one makes real results worse than these.

Threshold. Chosen on the training day's validation rows only: the
highest net P&L per trade among thresholds with at least MIN_TRADES
trades. --test then runs that frozen threshold on the test day, which
is the only time this project opens it.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from .data import FLAT, LABEL_K, UP, split
from .quantize import forward, from_json

FEE = 30                     # $0.003 per share per side, the usual taker fee
MIN_TRADES = 200
QUANTILES = (0, 0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.998, 0.999)


def bid_ask(mid2, spread):
    """mid2 = bid + ask and spread = ask - bid, so both sides come back exactly."""
    return (mid2 - spread) // 2, (mid2 + spread) // 2


def confidence(scores):
    """(prediction, top score minus second) per row. argmax ties go to the lower class."""
    pred = scores.argmax(axis=1)
    top2 = np.sort(scores, axis=1)[:, -2:]
    return pred, top2[:, 1] - top2[:, 0]


def simulate(pred, conf, bid, ask, threshold, k=LABEL_K, fee=FEE):
    """Trade every eligible signal, one position at a time. Returns a dict of stats and per-trade P&L."""
    n = len(pred)
    rows = np.flatnonzero((pred != FLAT) & (conf >= threshold) & (np.arange(n) + k < n))
    entries, free_at = [], 0
    for i in rows:
        if i >= free_at:
            entries.append(int(i))
            free_at = i + k
    e = np.array(entries, dtype=np.int64)
    side = np.where(pred[e] == UP, 1, -1)
    x = e + k
    mid_move = ((bid[x] + ask[x]) - (bid[e] + ask[e])) / 2
    gross = side * mid_move
    net = np.where(side == 1, bid[x] - ask[e], bid[e] - ask[x]) - 2 * fee
    return {
        "threshold": int(threshold),
        "signals": int(len(rows)),
        "trades": int(len(e)),
        "right": int((gross > 0).sum()),
        "wrong": int((gross < 0).sum()),
        "flat": int((gross == 0).sum()),
        "gross_per_trade": float(gross.mean()) if len(e) else 0.0,
        "net_per_trade": float(net.mean()) if len(e) else 0.0,
        "entries": entries,
        "gross": gross.tolist(),
        "net": net.tolist(),
    }


def curve(pred, conf, bid, ask, thresholds, k=LABEL_K, fee=FEE):
    return [simulate(pred, conf, bid, ask, t, k, fee) for t in thresholds]


def choose_threshold(points, min_trades=MIN_TRADES):
    ok = [p for p in points if p["trades"] >= min_trades]
    if not ok:
        raise ValueError(f"no threshold gives {min_trades} trades")
    return max(ok, key=lambda p: p["net_per_trade"])


def summary(p):
    """The per-trade lists are long, so the saved results keep only this."""
    out = {key: v for key, v in p.items() if key not in ("entries", "gross", "net")}
    t = max(p["trades"], 1)
    out["hit_rate"] = p["right"] / t
    out["right_of_moved"] = p["right"] / max(p["right"] + p["wrong"], 1)
    out["net_total"] = float(np.sum(p["net"]))
    return out


def day_signals(q, path, rows=None):
    """(pred, conf, bid, ask) for a day's rows from `rows.start` to the end of the file."""
    d = np.load(path)
    start = 0 if rows is None else rows.start
    X, mid2 = d["features"][start:], d["mid2"][start:]
    spread = X[:, list(d["feature_names"]).index("spread")]
    pred, conf = confidence(forward(q, X))
    return (pred, conf, *bid_ask(mid2, spread))


def cents(units):
    return f"{units / 100:+.2f}c"


def print_curve(points):
    print(f"{'threshold':>10}{'signals':>10}{'trades':>8}{'hit rate':>10}{'flat':>7}{'right/moved':>13}"
          f"{'gross/trade':>13}{'net/trade':>11}")
    for p in points:
        t = max(p["trades"], 1)
        moved = max(p["right"] + p["wrong"], 1)
        print(f"{p['threshold']:>10}{p['signals']:>10,}{p['trades']:>8,}{p['right'] / t:>10.1%}"
              f"{p['flat'] / t:>7.0%}{p['right'] / moved:>13.1%}"
              f"{cents(p['gross_per_trade']):>13}{cents(p['net_per_trade']):>11}")


def plot(points, chosen, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    trades = [p["trades"] for p in points]
    hit = [p["right"] / max(p["trades"], 1) for p in points]
    gross = [p["gross_per_trade"] / 100 for p in points]
    net = [p["net_per_trade"] / 100 for p in points]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(10, 4))
    a1.plot(trades, hit, "o-", color="#2a6fdb")
    a1.set_ylabel("hit rate (price moved our way)")
    a2.plot(trades, gross, "o-", color="#2a6fdb", label="before costs (mid to mid)")
    a2.plot(trades, net, "o-", color="#d1495b", label="after spread and fees")
    a2.axhline(0, color="#888", linewidth=1)
    a2.set_ylabel("P&L per trade, cents per share")
    a2.legend(fontsize=8)
    for ax in (a1, a2):
        ax.set_xscale("log")
        ax.set_xlabel("trades (log scale, fewer = higher threshold)")
        ax.axvline(chosen["trades"], color="#888", linestyle=":", linewidth=1)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle(f"Confidence threshold, validation rows (dotted = chosen, T = {chosen['threshold']})",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m train.backtest")
    p.add_argument("train_day", help=".npz the model was trained on")
    p.add_argument("--model", default="mlp16")
    p.add_argument("--results", default="data/raw/results", help="where train.quantize saved the model")
    p.add_argument("--fee", type=int, default=FEE, help="per share per side, ITCH price units")
    p.add_argument("--min-trades", type=int, default=MIN_TRADES)
    p.add_argument("--test", help="test day .npz; runs the threshold chosen on validation, once")
    a = p.parse_args(argv)

    qpath = Path(a.results) / f"{a.model}_int8.json"
    if not qpath.exists():
        raise SystemExit(f"{qpath} not found, run python -m train.quantize {a.train_day} --model {a.model} first")
    saved = json.loads(qpath.read_text())
    q = from_json(saved)

    n_labels = len(np.load(a.train_day)[f"label_k{LABEL_K}"])
    _, va = split(n_labels)
    pred, conf, bid, ask = day_signals(q, a.train_day, va)
    moving = conf[pred != FLAT]
    thresholds = sorted({int(np.quantile(moving, x)) for x in QUANTILES})
    points = curve(pred, conf, bid, ask, thresholds, fee=a.fee)
    chosen = choose_threshold(points, a.min_trades)

    print(f"validation rows, {a.model} int8, hold {LABEL_K} rows, fee {cents(a.fee)} per share per side\n")
    print_curve(points)
    print(f"\nchosen threshold T = {chosen['threshold']} "
          f"(best net per trade with at least {a.min_trades} trades)")
    out = {"model": a.model, "k": LABEL_K, "fee": a.fee, "min_trades": a.min_trades,
           "chosen": summary(chosen), "validation": [summary(x) for x in points]}

    if a.test:
        tpred, tconf, tbid, task = day_signals(q, a.test)
        t = simulate(tpred, tconf, tbid, task, chosen["threshold"], fee=a.fee)
        always = simulate(tpred, tconf, tbid, task, 0, fee=a.fee)
        print(f"\ntest day {a.test}, threshold frozen at T = {chosen['threshold']}\n")
        print_curve([always, t] if chosen["threshold"] else [t])
        total = np.sum(t["net"]) / 1e4
        print(f"\ntest day total after costs: {'-' if total < 0 else ''}${abs(total):,.2f} "
              f"trading 1 share per signal, {t['trades']:,} trades")
        out["test"] = {"file": a.test, "chosen": summary(t), "no_threshold": summary(always)}

    out_dir = Path(a.results)
    (out_dir / "threshold.json").write_text(json.dumps(out, indent=2))
    plot(points, chosen, out_dir / "threshold.png")
    print(f"\nsaved {out_dir / 'threshold.json'} and {out_dir / 'threshold.png'}")


if __name__ == "__main__":
    main()
