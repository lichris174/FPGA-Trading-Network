"""Up / flat / down labels: what the mid-price did next.

Row t gets the sign of mid2[t + k] - mid2[t], where k counts rows
(valid book updates), not time. This is the only place in the golden
model that looks forward, and it only ever reads mid2, never features.

The last k rows have nothing k rows ahead of them, so label() returns
len(mid2) - k labels: label i belongs to row i.
"""

import numpy as np

DOWN, FLAT, UP = -1, 0, 1


def label(mid2, k):
    if k < 1:
        raise ValueError(f"horizon must be at least 1, got {k}")
    mid2 = np.asarray(mid2, dtype=np.int64)
    if len(mid2) <= k:
        return np.zeros(0, dtype=np.int8)
    return np.sign(mid2[k:] - mid2[:-k]).astype(np.int8)
