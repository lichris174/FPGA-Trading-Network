"""Accuracy vs model size: train every model in MODELS with a few seeds.

    python -m train.sweep data/raw/aapl.npz

Same split, scaling and training as train.fit. Each model is trained
once per seed so the chart shows seed-to-seed spread, not one lucky
run. Validation rows only; the test day is never opened here. Writes
sweep.json and sweep.png to --out.
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .baselines import BASELINES
from .data import apply_scale, load, scale_shifts, split
from .fit import MODELS, build_model, n_params, predict, train_model
from .metrics import report


def run(path, seeds, epochs):
    X, y, names = load(path)
    tr, va = split(len(y))
    shifts = scale_shifts(X[tr])
    Xtr, Xva = apply_scale(X[tr], shifts), apply_scale(X[va], shifts)

    baselines = {name: report(y[va], rule(X[va], names)) for name, rule in BASELINES.items()}
    models = {}
    for name in MODELS:
        runs = []
        for seed in seeds:
            t0 = time.time()
            model, history = train_model(build_model(name, len(names)), Xtr, y[tr], Xva, y[va],
                                         epochs=epochs, seed=seed)
            r = report(y[va], predict(model, Xva))
            r.update(seed=seed, epochs_run=len(history), seconds=round(time.time() - t0, 1))
            runs.append(r)
            print(f"{name:<10} seed {seed}  macro-F1 {r['macro_f1']:.4f}  ({r['seconds']}s)", flush=True)
        models[name] = {"params": n_params(build_model(name, len(names))), "runs": runs}
    return {"baselines": baselines, "models": models, "shifts": shifts.tolist(), "seeds": list(seeds)}


def plot(result, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6.5, 4))
    for name, m in result["models"].items():
        f1 = [r["macro_f1"] for r in m["runs"]]
        ax.scatter([m["params"]] * len(f1), f1, color="#2a6fdb", alpha=0.4, s=18)
        ax.scatter(m["params"], np.mean(f1), color="#2a6fdb", s=50, zorder=3)
        ax.annotate(name, (m["params"], np.mean(f1)), textcoords="offset points", xytext=(6, -12), fontsize=9)
    for (name, b), style in zip(result["baselines"].items(), (":", "--")):
        ax.axhline(b["macro_f1"], color="#888", linestyle=style, linewidth=1)
        ax.text(0.99, b["macro_f1"], name, transform=ax.get_yaxis_transform(),
                ha="right", va="bottom", fontsize=8, color="#666")
    ax.set_xscale("log")
    ax.set_xlabel("parameters (log scale)")
    ax.set_ylabel("validation macro-F1")
    ax.set_title("Model size vs macro-F1, k = 10 (dots = seeds, large dot = mean)", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m train.sweep")
    p.add_argument("train_day", help=".npz from golden.dataset")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--out", default="data/raw/results")
    a = p.parse_args(argv)

    result = run(a.train_day, range(a.seeds), a.epochs)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sweep.json").write_text(json.dumps(result, indent=2))
    plot(result, out / "sweep.png")

    print(f"\n{'model':<20}{'params':>7}{'macro-F1 mean':>15}{'± std':>8}{'accuracy':>10}")
    for name, b in result["baselines"].items():
        print(f"{name:<20}{0:>7}{b['macro_f1']:>15.3f}{'':>8}{b['accuracy']:>10.1%}")
    for name, m in result["models"].items():
        f1 = np.array([r["macro_f1"] for r in m["runs"]])
        acc = np.mean([r["accuracy"] for r in m["runs"]])
        print(f"{name:<20}{m['params']:>7}{f1.mean():>15.3f}{f1.std():>8.3f}{acc:>10.1%}")
    print(f"\nsaved {out / 'sweep.json'} and {out / 'sweep.png'}")


if __name__ == "__main__":
    main()
