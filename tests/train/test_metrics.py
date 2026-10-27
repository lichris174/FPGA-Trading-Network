"""Tests for train/metrics.py and train/baselines.py."""

import numpy as np
import pytest

from train.baselines import always_flat, sign_of_imbalance
from train.metrics import accuracy, confusion, macro_f1, recall


def test_confusion_rows_are_truth_columns_are_prediction():
    y = np.array([0, 0, 1, 2, 2, 2])
    p = np.array([0, 1, 1, 2, 2, 0])
    assert confusion(y, p).tolist() == [[1, 1, 0],
                                        [0, 1, 0],
                                        [1, 0, 2]]


def test_accuracy_recall_and_macro_f1_by_hand():
    y = np.array([0, 0, 1, 2, 2, 2])
    p = np.array([0, 1, 1, 2, 2, 0])
    assert accuracy(y, p) == pytest.approx(4 / 6)
    assert recall(y, p).tolist() == pytest.approx([1 / 2, 1, 2 / 3])
    # Per class F1: down 2*(.5*.5)/(1) = .5, flat 2*(.5*1)/1.5 = 2/3, up 2*(1*2/3)/(5/3) = .8
    assert macro_f1(y, p) == pytest.approx((0.5 + 2 / 3 + 0.8) / 3)


def test_macro_f1_punishes_always_predicting_the_majority():
    y = np.array([1] * 90 + [0] * 5 + [2] * 5)
    p = np.ones_like(y)
    assert accuracy(y, p) == pytest.approx(0.9)
    assert macro_f1(y, p) < 0.35


def test_class_never_predicted_counts_as_zero_f1():
    y = np.array([0, 1, 2])
    p = np.array([0, 0, 0])
    assert macro_f1(y, p) == pytest.approx((0.5 + 0 + 0) / 3)


def test_baselines():
    names = ["spread", "imbalance"]
    X = np.array([[100, 500], [100, -3], [100, 0]])
    assert always_flat(X, names).tolist() == [1, 1, 1]
    assert sign_of_imbalance(X, names).tolist() == [2, 0, 1]
