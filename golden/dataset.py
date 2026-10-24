"""Build the feature table and labels for one stock from an ITCH file.

    python -m golden.dataset FILE SYMBOL OUT.npz

Replays the stock through the feature engine and writes a numpy .npz
with these arrays:

    features        int64, one row per valid book update, columns in
                    feature_names order
    feature_names   column names
    timestamp       nanoseconds since midnight for each row
    mid2            bid + ask for each row
    label_k10, ...  up (+1) / flat (0) / down (-1) for each horizon k.
                    Shorter than the table by k: label i belongs to row i

Then prints a summary: the label mix for each horizon (expect mostly
flat), each feature's range (this sets bit widths in the Verilog),
and how well imbalance alone lines up with the next move.
"""

import argparse

import numpy as np

from .features import FEATURE_NAMES, FeatureEngine
from .itch.parser import decode
from .itch.tools import filter_stock
from .labels import label

HORIZONS = (10, 50, 100)


def build(path, symbol):
    engine = FeatureEngine()
    rows = []
    for msg in filter_stock(path, symbol):
        r = engine.step(decode(msg))
        if r is not None:
            rows.append(r)
    out = {
        "features": np.array([r.features for r in rows], dtype=np.int64).reshape(-1, len(FEATURE_NAMES)),
        "feature_names": np.array(FEATURE_NAMES),
        "timestamp": np.array([r.timestamp for r in rows], dtype=np.int64),
        "mid2": np.array([r.mid2 for r in rows], dtype=np.int64),
    }
    for k in HORIZONS:
        out[f"label_k{k}"] = label(out["mid2"], k)
    return out


def summary(d):
    n = len(d["mid2"])
    lines = [f"rows {n:,}"]
    if n == 0:
        return "\n".join(lines)
    t0, t1 = d["timestamp"][0], d["timestamp"][-1]
    lines.append(f"time {t0 / 3.6e12:.3f} h to {t1 / 3.6e12:.3f} h after midnight")
    lines.append("")
    lines.append("labels        down     flat       up")
    for k in HORIZONS:
        lab = d[f"label_k{k}"]
        if len(lab):
            frac = [np.mean(lab == v) for v in (-1, 0, 1)]
            lines.append(f"label_k{k:<4} {frac[0]:7.1%}  {frac[1]:7.1%}  {frac[2]:7.1%}")
    lines.append("")
    lines.append(f"{'feature':<18}{'min':>14}{'max':>14}  bits")
    for i, name in enumerate(FEATURE_NAMES):
        col = d["features"][:, i]
        lo, hi = int(col.min()), int(col.max())
        bits = max(abs(lo), abs(hi)).bit_length() + 1   # signed
        lines.append(f"{name:<18}{lo:>14,}{hi:>14,}  {bits}")
    lines.append("")
    imb = d["features"][:, FEATURE_NAMES.index("imbalance")]
    for k in HORIZONS:
        lab = d[f"label_k{k}"]
        moved = lab != 0
        if moved.any():
            agree = np.mean(np.sign(imb[:len(lab)][moved]) == lab[moved])
            lines.append(f"sign(imbalance) matches the next non-flat move, k={k}: {agree:.1%}")
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m golden.dataset")
    p.add_argument("file")
    p.add_argument("symbol")
    p.add_argument("out")
    a = p.parse_args(argv)
    d = build(a.file, a.symbol)
    np.savez(a.out, **d)
    print(summary(d))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
