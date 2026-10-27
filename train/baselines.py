"""Rules that need no training. A model has to beat these to be worth anything."""

import numpy as np

from .data import FLAT


def always_flat(X, names):
    return np.full(len(X), FLAT, dtype=np.int64)


def sign_of_imbalance(X, names):
    """Up when more shares wait to buy than to sell, down when fewer, flat when equal."""
    imb = X[:, names.index("imbalance")]
    return (np.sign(imb) + 1).astype(np.int64)


BASELINES = {"always flat": always_flat, "sign of imbalance": sign_of_imbalance}
