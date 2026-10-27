"""Smoke tests for train/fit.py. Skipped where PyTorch isn't installed."""

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from train.fit import build_model, class_weights, n_params, predict, train_model  # noqa: E402
from train.metrics import macro_f1  # noqa: E402


def separable(n, rng):
    """Class decided by the sign of feature 0, with a flat band in the middle."""
    X = rng.normal(size=(n, 9)).astype(np.float32)
    y = np.where(X[:, 0] > 0.5, 2, np.where(X[:, 0] < -0.5, 0, 1))
    return X, y


def test_logreg_learns_an_obvious_pattern():
    rng = np.random.default_rng(0)
    Xtr, ytr = separable(4000, rng)
    Xva, yva = separable(1000, rng)
    model, history = train_model(build_model("logreg"), Xtr, ytr, Xva, yva, epochs=30, batch=64, lr=1e-2, seed=0)
    assert macro_f1(yva, predict(model, Xva)) > 0.8
    assert len(history) >= 1 and "val_macro_f1" in history[0]


def test_training_is_reproducible_with_a_seed():
    rng = np.random.default_rng(0)
    Xtr, ytr = separable(2000, rng)
    Xva, yva = separable(500, rng)
    a, _ = train_model(build_model("logreg"), Xtr, ytr, Xva, yva, epochs=3, seed=1)
    b, _ = train_model(build_model("logreg"), Xtr, ytr, Xva, yva, epochs=3, seed=1)
    assert np.array_equal(predict(a, Xva), predict(b, Xva))


def test_class_weights_are_inverse_frequency():
    y = np.array([1] * 8 + [0] + [2])
    w = class_weights(y)
    # n / (classes * count): 10 / (3 * 1), 10 / (3 * 8), 10 / (3 * 1)
    assert w.tolist() == pytest.approx([10 / 3, 10 / 24, 10 / 3])


def test_logreg_shape():
    m = build_model("logreg")
    assert sum(p.numel() for p in m.parameters()) == 9 * 3 + 3


@pytest.mark.parametrize("name, params", [
    ("mlp8", 9 * 8 + 8 + 8 * 3 + 3),
    ("mlp16", 9 * 16 + 16 + 16 * 3 + 3),
    ("mlp32x16", 9 * 32 + 32 + 32 * 16 + 16 + 16 * 3 + 3),
])
def test_mlp_shapes(name, params):
    m = build_model(name)
    assert n_params(m) == params
    assert m(torch.zeros(5, 9)).shape == (5, 3)


def test_mlp_learns_an_obvious_pattern():
    rng = np.random.default_rng(0)
    Xtr, ytr = separable(4000, rng)
    Xva, yva = separable(1000, rng)
    model, _ = train_model(build_model("mlp16"), Xtr, ytr, Xva, yva, epochs=30, batch=64, lr=1e-2, seed=0)
    assert macro_f1(yva, predict(model, Xva)) > 0.8


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        build_model("transformer")
