"""8-bit version of a trained model, and the integer forward pass the Verilog has to match.

    python -m train.fit data/raw/aapl.npz --model mlp16
    python -m train.quantize data/raw/aapl.npz --model mlp16

Every scale is a power of two, so every rescale is a shift. A value in
Qf format is an integer standing for value / 2^f.

    inputs       raw golden features, shifted to Q0.7 and clamped to int8:
                 a0 = clamp(x >> (s - 7)), s from train.data.scale_shifts.
                 These are the values the float model saw (x / 2^s), times 128
    weights      int8 with one fraction per layer: W_q = round(W * 2^fw)
    bias         wider integer at the accumulator's format, f_in + fw,
                 so it is added once with no shift
    accumulator  bias plus the int8 x int8 products. Width from
                 accumulator_bits()
    hidden       shift the accumulator down to the next layer's format
                 (rounding), then ReLU and clamp in one step: [0, 127]
    output       the last layer's accumulators are the scores. No softmax:
                 the decision is the argmax (ties go to the lower class),
                 confidence is top minus second

Rounding shift: (x + 2^(k-1)) >> k, one add before the shift. A plain
shift rounds every value down, which biases each layer by half a step.

Each hidden layer's output format is calibrated on the training rows:
the finest one that keeps the ACT_PCT percentile of its ReLU outputs
under 128. The test day is never opened here.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from .data import apply_scale, load, split
from .metrics import macro_f1, report

IN_FRAC = 7
ACT_PCT = 99.99
INT8_MIN, INT8_MAX = -128, 127


def rshift_round(x, k):
    """x / 2^k rounded half up for k > 0, x * 2^-k for k < 0. k may be per column."""
    x = np.asarray(x, dtype=np.int64)
    k = np.asarray(k, dtype=np.int64)
    down = np.maximum(k, 0)
    return ((x << np.maximum(-k, 0)) + ((1 << down) >> 1)) >> down


def quantize_inputs(X, in_shifts):
    return np.clip(rshift_round(X, in_shifts), INT8_MIN, INT8_MAX)


def weight_frac(W):
    """Largest fw such that every round(W * 2^fw) fits in int8."""
    m = float(np.abs(W).max())
    if m == 0:
        return 0
    fw = int(np.floor(np.log2(INT8_MAX / m)))
    while np.round(m * 2.0 ** fw) > INT8_MAX:
        fw -= 1
    return fw


def quantize(layers, shifts, X_train, pct=ACT_PCT):
    """Integer model from float layers [(W [out, in], b [out]), ...] and the input shifts.

    X_train is raw integer features, used only to calibrate hidden layer formats.
    """
    in_shifts = np.asarray(shifts, dtype=np.int64) - IN_FRAC
    a = quantize_inputs(X_train, in_shifts)
    f_in, out = IN_FRAC, []
    for i, (W, b) in enumerate(layers):
        fw = weight_frac(W)
        Wq = np.round(np.asarray(W) * 2.0 ** fw).astype(np.int64)
        bq = np.round(np.asarray(b) * 2.0 ** (f_in + fw)).astype(np.int64)
        layer = {"W": Wq, "b": bq, "fw": fw, "shift": None}
        if i < len(layers) - 1:
            acc = a @ Wq.T + bq
            top = max(int(np.ceil(np.percentile(np.maximum(acc, 0), pct))), 1)
            layer["shift"] = top.bit_length() - IN_FRAC
            a = np.clip(rshift_round(acc, layer["shift"]), 0, INT8_MAX)
            f_in = f_in + fw - layer["shift"]
        out.append(layer)
    return {"in_shifts": in_shifts, "layers": out}


def forward(q, X, keep=False):
    """Integer scores [n, classes] from raw features. keep=True also returns each layer's int8 input."""
    a = quantize_inputs(X, q["in_shifts"])
    acts = []
    for layer in q["layers"]:
        acts.append(a)
        acc = a @ layer["W"].T + layer["b"]
        if layer["shift"] is None:
            return (acc, acts) if keep else acc
        a = np.clip(rshift_round(acc, layer["shift"]), 0, INT8_MAX)


def predict(q, X):
    return forward(q, X).argmax(axis=1)


def accumulator_bits(q):
    """Signed bits each layer's accumulator needs for any input, not just the data seen."""
    bits, amax = [], -INT8_MIN
    for layer in q["layers"]:
        bound = int((np.abs(layer["W"]).sum(axis=1) * amax + np.abs(layer["b"])).max())
        bits.append(bound.bit_length() + 1)
        amax = INT8_MAX
    return bits


def to_json(q):
    return {
        "in_shifts": q["in_shifts"].tolist(),
        "layers": [{"W": layer["W"].tolist(), "b": layer["b"].tolist(), "fw": layer["fw"], "shift": layer["shift"]}
                   for layer in q["layers"]],
    }


def from_json(d):
    return {
        "in_shifts": np.array(d["in_shifts"], dtype=np.int64),
        "layers": [{"W": np.array(layer["W"], dtype=np.int64), "b": np.array(layer["b"], dtype=np.int64),
                    "fw": layer["fw"], "shift": layer["shift"]} for layer in d["layers"]],
    }


def float_layers(state):
    """[(W, b), ...] in order from a saved nn.Linear or nn.Sequential state dict."""
    keys = sorted({k.rsplit(".", 1)[0] if "." in k else "" for k in state}, key=lambda p: int(p) if p else 0)
    return [(state[f"{p}.weight" if p else "weight"].numpy().astype(np.float64),
             state[f"{p}.bias" if p else "bias"].numpy().astype(np.float64)) for p in keys]


def main(argv=None):
    import torch

    from .fit import build_model, fmt
    from .fit import predict as predict_float

    p = argparse.ArgumentParser(prog="python -m train.quantize")
    p.add_argument("train_day", help=".npz from golden.dataset")
    p.add_argument("--model", default="mlp16")
    p.add_argument("--results", default="data/raw/results", help="where train.fit saved the model")
    a = p.parse_args(argv)

    ckpt_path = Path(a.results) / f"{a.model}.pt"
    if not ckpt_path.exists():
        raise SystemExit(f"{ckpt_path} not found, run python -m train.fit {a.train_day} --model {a.model} first")
    ckpt = torch.load(ckpt_path, weights_only=True)
    shifts = np.array(ckpt["shifts"], dtype=np.int64)

    X, y, names = load(a.train_day)
    if names != list(ckpt["features"]):
        raise SystemExit(f"feature names in {a.train_day} don't match the ones {a.model} was trained on")
    tr, va = split(len(y))

    model = build_model(ckpt["model"], len(names))
    model.load_state_dict(ckpt["state"])
    q = quantize(float_layers(ckpt["state"]), shifts, X[tr])

    p_float = predict_float(model, apply_scale(X[va], shifts))
    p_int = predict(q, X[va])
    results = {"float": report(y[va], p_float), "int8": report(y[va], p_int)}
    agree = float((p_float == p_int).mean())
    bits = accumulator_bits(q)

    print(f"{'validation':<20}{'accuracy':>9}{'macro-F1':>10}   recall down / flat / up")
    for k, r in results.items():
        print(fmt(f"{a.model} {k}", r))
    print(f"\nint8 agrees with float on {agree:.2%} of validation rows")
    print(f"macro-F1 change {macro_f1(y[va], p_int) - macro_f1(y[va], p_float):+.4f}")
    for i, (layer, b) in enumerate(zip(q["layers"], bits)):
        print(f"layer {i}: {layer['W'].shape[1]:>2} -> {layer['W'].shape[0]:<2}  fw {layer['fw']:>2}  "
              f"out shift {layer['shift']}  accumulator {b} bits")

    out = Path(a.results) / f"{a.model}_int8.json"
    out.write_text(json.dumps({"model": ckpt["model"], "features": names, **to_json(q),
                               "accumulator_bits": bits, "agreement": agree, "results": results}, indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    main()
