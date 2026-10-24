"""Tests for golden/labels.py."""

import numpy as np
import pytest

from golden.labels import DOWN, FLAT, UP, label


def test_up_flat_down():
    mid2 = np.array([10, 12, 12, 8, 8, 10])
    # k = 1: compare each row with the next one; the last row has no label.
    assert label(mid2, 1).tolist() == [UP, FLAT, DOWN, FLAT, UP]


def test_horizon_compares_k_rows_ahead_only():
    mid2 = np.array([10, 99, 0, 10, 11, 9])
    # Row 0 vs row 3 (10 -> 10): the swings in between do not count.
    assert label(mid2, 3).tolist() == [FLAT, DOWN, UP]


def test_horizon_longer_than_data_gives_nothing():
    assert label(np.array([1, 2, 3]), 3).tolist() == []


def test_bad_horizon():
    with pytest.raises(ValueError):
        label(np.array([1, 2]), 0)
