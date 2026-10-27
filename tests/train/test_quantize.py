"""Tests for train/quantize.py: shifts, int8 ranges and agreement with the float model."""

import numpy as np
import pytest

from train.quantize import (
    IN_FRAC, accumulator_bits, forward, from_json, predict, quantize,
    quantize_inputs, rshift_round, to_json, weight_frac,
)


def float_forward(layers, X, shifts):
    a = X / (2.0 ** shifts)
    for i, (W, b) in enumerate(layers):
        a = a @ W.T + b
        if i < len(layers) - 1:
            a = np.maximum(a, 0)
    return a


def random_net(rng, sizes):
    return [(rng.normal(scale=0.5, size=(o, i)), rng.normal(scale=0.1, size=o))
            for i, o in zip(sizes, sizes[1:])]


def raw_features(rng, n, shifts):
    """Integer features with roughly unit spread after dividing by 2^shift."""
    return np.round(rng.normal(scale=0.4, size=(n, len(shifts))) * 2.0 ** shifts).astype(np.int64)


def test_rshift_round_rounds_half_up():
    x = np.array([5, 6, 7, -5, -6, -7])
    assert rshift_round(x, 2).tolist() == [1, 2, 2, -1, -1, -2]   # 1.25 1.5 1.75 -1.25 -1.5 -1.75


def test_rshift_round_zero_and_negative_shift():
    x = np.array([3, -3])
    assert rshift_round(x, 0).tolist() == [3, -3]
    assert rshift_round(x, -2).tolist() == [12, -12]


def test_rshift_round_per_column_shift():
    x = np.array([[8, 8, 8]])
    assert rshift_round(x, np.array([1, 0, -1])).tolist() == [[4, 8, 16]]


def test_quantize_inputs_matches_float_scaling_and_clamps():
    shifts = np.array([10, 9])
    X = np.array([[512, -256], [1 << 20, -(1 << 20)]])
    q = quantize_inputs(X, shifts - IN_FRAC)
    assert q[0].tolist() == [64, -64]          # 0.5 and -0.5 in Q0.7
    assert q[1].tolist() == [127, -128]        # clamped to int8


@pytest.mark.parametrize("m, fw", [(0.5, 7), (1.0, 6), (0.004, 14), (100.0, 0), (300.0, -2)])
def test_weight_frac_is_the_largest_that_fits(m, fw):
    W = np.array([[m, -m / 3]])
    assert weight_frac(W) == fw
    assert np.round(m * 2.0 ** fw) <= 127
    assert np.round(m * 2.0 ** (fw + 1)) > 127


def test_weights_are_int8_and_activations_stay_in_range():
    rng = np.random.default_rng(0)
    shifts = np.array([10, 15, 9, 9])
    q = quantize(random_net(rng, [4, 8, 3]), shifts, raw_features(rng, 2000, shifts))
    for layer in q["layers"]:
        assert layer["W"].min() >= -128 and layer["W"].max() <= 127
    _, acts = forward(q, raw_features(rng, 2000, shifts), keep=True)
    assert acts[0].min() >= -128 and acts[0].max() <= 127
    assert acts[1].min() >= 0 and acts[1].max() <= 127


@pytest.mark.parametrize("sizes", [[4, 3], [4, 8, 3], [4, 16, 8, 3]])
def test_integer_model_agrees_with_float(sizes):
    rng = np.random.default_rng(1)
    shifts = np.array([10, 15, 9, 9])
    layers = random_net(rng, sizes)
    q = quantize(layers, shifts, raw_features(rng, 5000, shifts))
    X = raw_features(rng, 5000, shifts)
    want = float_forward(layers, X, shifts).argmax(axis=1)
    assert (predict(q, X) == want).mean() > 0.95


def test_logreg_scores_track_float_logits():
    rng = np.random.default_rng(2)
    shifts = np.array([10, 15, 9, 9])
    layers = random_net(rng, [4, 3])
    q = quantize(layers, shifts, raw_features(rng, 100, shifts))
    X = raw_features(rng, 1000, shifts) // 4      # stay away from the input clamp
    scale = 2.0 ** (IN_FRAC + q["layers"][0]["fw"])
    err = np.abs(forward(q, X) / scale - float_forward(layers, X, shifts))
    assert err.max() < 0.1


def test_forward_is_pure_integer():
    rng = np.random.default_rng(3)
    shifts = np.array([10, 15, 9, 9])
    q = quantize(random_net(rng, [4, 8, 3]), shifts, raw_features(rng, 500, shifts))
    assert forward(q, raw_features(rng, 10, shifts)).dtype == np.int64


def test_accumulator_bits_bound_the_worst_case():
    rng = np.random.default_rng(4)
    shifts = np.array([10, 15, 9, 9])
    q = quantize(random_net(rng, [4, 8, 3]), shifts, raw_features(rng, 2000, shifts))
    bits = accumulator_bits(q)
    # Extreme inputs: every feature pinned at either clamp.
    X = (np.array(list(np.ndindex(*[2] * 4))) * 2 - 1) * (1 << 30)
    _, acts = forward(q, X, keep=True)
    for layer, a, b in zip(q["layers"], acts, bits):
        acc = a @ layer["W"].T + layer["b"]
        assert np.abs(acc).max() < 1 << (b - 1)


def test_json_round_trip_gives_identical_scores():
    rng = np.random.default_rng(5)
    shifts = np.array([10, 15, 9, 9])
    q = quantize(random_net(rng, [4, 8, 3]), shifts, raw_features(rng, 500, shifts))
    X = raw_features(rng, 200, shifts)
    assert np.array_equal(forward(from_json(to_json(q)), X), forward(q, X))


@pytest.mark.parametrize("name", ["logreg", "mlp16", "mlp32x16"])
def test_float_layers_reads_saved_models_in_order(name):
    torch = pytest.importorskip("torch")
    from train.fit import build_model
    from train.quantize import float_layers

    model = build_model(name)
    layers = float_layers(model.state_dict())
    X = np.random.default_rng(6).normal(size=(50, 9))
    want = model(torch.from_numpy(X.astype(np.float32))).detach().numpy()
    assert float_forward(layers, X, np.zeros(9)) == pytest.approx(want, abs=1e-5)
