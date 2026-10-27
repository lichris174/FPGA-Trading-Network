"""Tests for train/data.py: label alignment, time split and input scaling."""

import numpy as np
import pytest

from train.data import DOWN, FLAT, UP, apply_scale, load, scale_shifts, split


def fake_npz(tmp_path, n=100, k=10):
    rng = np.random.default_rng(0)
    mid2 = np.cumsum(rng.integers(-1, 2, n))
    path = tmp_path / "day.npz"
    np.savez(path,
             features=rng.integers(-1000, 1000, (n, 9)),
             feature_names=np.array([f"f{i}" for i in range(9)]),
             timestamp=np.arange(n), mid2=mid2,
             label_k10=np.sign(mid2[k:] - mid2[:-k]).astype(np.int8))
    return path, mid2


def test_load_drops_unlabelled_rows_and_maps_classes(tmp_path):
    path, mid2 = fake_npz(tmp_path)
    X, y, names = load(path, k=10)
    assert X.shape == (90, 9) and y.shape == (90,)
    assert names[0] == "f0"
    # Classes are 0 = down, 1 = flat, 2 = up.
    expected = np.sign(mid2[10:] - mid2[:-10]) + 1
    assert np.array_equal(y, expected)
    assert (DOWN, FLAT, UP) == (0, 1, 2)


def test_split_is_in_time_order_with_a_gap():
    tr, va = split(1000, train_frac=0.7, gap=10)
    assert tr == slice(0, 700)
    assert va == slice(710, 1000)


def test_split_rejects_tiny_data():
    with pytest.raises(ValueError):
        split(15, train_frac=0.7, gap=10)


def test_scale_shifts_use_training_rows_only():
    X = np.zeros((200, 2), dtype=np.int64)
    X[:100, 0] = 1000      # training rows: column 0 around 1000
    X[100:, 0] = 10**9     # huge values later must not affect the shifts
    X[:100, 1] = 3
    shifts = scale_shifts(X[:100])
    assert shifts.tolist() == [10, 2]      # 2^10 = 1024 >= 1000, 2^2 = 4 >= 3


def test_apply_scale_maps_typical_values_into_unit_range():
    rng = np.random.default_rng(1)
    X = rng.integers(-50_000, 50_000, (1000, 3))
    Z = apply_scale(X, scale_shifts(X))
    assert Z.dtype == np.float32
    assert np.mean(np.abs(Z) <= 1.0) >= 0.99
    assert np.abs(Z).max() > 0.25          # not squashed towards zero


def test_all_zero_column_gets_shift_zero():
    shifts = scale_shifts(np.zeros((10, 1), dtype=np.int64))
    assert shifts.tolist() == [0]
