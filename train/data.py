"""Load a day's feature table, split it by time and scale the inputs.

Classes: 0 = down, 1 = flat, 2 = up (the golden labels -1 / 0 / +1,
shifted by one so they can index PyTorch outputs).

Split. Within one day, the first train_frac of rows train and the rest
validate, with `gap` rows dropped in between. A row's label looks k
rows ahead, so without the gap the last training labels would be
computed from validation-period prices. The test day is a separate
file and nothing here opens it.

Scaling. Each feature is divided by a power of two, chosen from the
training rows so that 99% of them land in [-1, 1]. In hardware that is
a free bit shift, where a mean and standard deviation would need a
subtract and a divide per feature.
"""

import math

import numpy as np

DOWN, FLAT, UP = 0, 1, 2
LABEL_K = 10


def load(path, k=LABEL_K):
    """Return (X int64 [n, features], y classes [n], feature names) for rows that have a label."""
    d = np.load(path)
    y = d[f"label_k{k}"].astype(np.int64) + 1
    X = d["features"][:len(y)]
    return X, y, [str(s) for s in d["feature_names"]]


def split(n, train_frac=0.7, gap=LABEL_K):
    """Slices for (train, validation) over n rows in time order."""
    cut = int(n * train_frac)
    if cut < 1 or cut + gap >= n:
        raise ValueError(f"{n} rows is too few to split with a gap of {gap}")
    return slice(0, cut), slice(cut + gap, n)


def scale_shifts(X_train, pct=99):
    """Per-feature shift s: the smallest power of two 2^s at or above the pct-th percentile of |x|."""
    shifts = []
    for col in np.abs(X_train).T:
        p = math.ceil(np.percentile(col, pct))
        shifts.append((p - 1).bit_length() if p >= 1 else 0)
    return np.array(shifts, dtype=np.int64)


def apply_scale(X, shifts):
    return (X / (2.0 ** shifts)).astype(np.float32)
