"""Classification metrics in plain numpy.

Macro-F1 is the headline number: the F1 score of each class (down,
flat, up) averaged with equal weight. "Flat" is the most common label,
so accuracy rewards a model that always says flat; macro-F1 doesn't.
"""

import numpy as np

N_CLASSES = 3


def confusion(y, p, n=N_CLASSES):
    """Counts with rows = true class, columns = predicted class."""
    return np.bincount(np.asarray(y) * n + np.asarray(p), minlength=n * n).reshape(n, n)


def accuracy(y, p):
    return float(np.mean(np.asarray(y) == np.asarray(p)))


def recall(y, p):
    c = confusion(y, p)
    return c.diagonal() / np.maximum(c.sum(axis=1), 1)


def precision(y, p):
    c = confusion(y, p)
    return c.diagonal() / np.maximum(c.sum(axis=0), 1)


def macro_f1(y, p):
    pr, rc = precision(y, p), recall(y, p)
    denom = pr + rc
    f1 = np.where(denom > 0, 2 * pr * rc / np.where(denom > 0, denom, 1), 0.0)
    return float(f1.mean())


def report(y, p):
    """One dict of everything, for printing and saving."""
    return {
        "accuracy": accuracy(y, p),
        "macro_f1": macro_f1(y, p),
        "recall_down_flat_up": recall(y, p).tolist(),
        "predicted_down_flat_up": np.bincount(p, minlength=N_CLASSES).tolist(),
    }
