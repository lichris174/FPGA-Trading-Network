"""Train a model on one day and score it on that day's validation rows.

    pip install -e ".[train]"
    python -m train.fit data/raw/aapl.npz --model logreg

Prints the baselines and the model on the validation rows, then saves
the weights, input shifts and metrics to data/raw/results/. The test
day is never opened here.

Training: cross-entropy weighted by inverse class frequency (so the
model can't win by always saying flat), Adam, mini-batches, a fixed
seed. After each epoch the model is scored on validation macro-F1 and
the best epoch is kept; training stops after `patience` epochs
without improvement.
"""

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .baselines import BASELINES
from .data import apply_scale, load, scale_shifts, split
from .metrics import N_CLASSES, macro_f1, report

N_FEATURES = 9

# Hidden layer widths per model. ReLU between layers: in hardware it is a
# sign-bit check, no lookup table.
MODELS = {
    "logreg": (),
    "mlp8": (8,),
    "mlp16": (16,),
    "mlp32x16": (32, 16),
}


def build_model(name, n_features=N_FEATURES):
    if name not in MODELS:
        raise ValueError(f"unknown model {name!r}, expected one of {list(MODELS)}")
    layers, width = [], n_features
    for h in MODELS[name]:
        layers += [nn.Linear(width, h), nn.ReLU()]
        width = h
    layers.append(nn.Linear(width, N_CLASSES))
    return layers[0] if len(layers) == 1 else nn.Sequential(*layers)


def n_params(model):
    return sum(p.numel() for p in model.parameters())


def class_weights(y):
    counts = np.bincount(y, minlength=N_CLASSES)
    return len(y) / (N_CLASSES * np.maximum(counts, 1))


def predict(model, X, batch=1 << 16):
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(X), batch):
            out.append(model(torch.from_numpy(X[i:i + batch])).argmax(dim=1).numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int64)


def train_model(model, Xtr, ytr, Xva, yva, epochs=30, batch=4096, lr=1e-3, patience=5, seed=0):
    """Returns (model at its best validation epoch, per-epoch history)."""
    torch.manual_seed(seed)
    gen = torch.Generator().manual_seed(seed)
    # Re-initialise so the result depends only on the seed, not on when the model was built.
    for m in model.modules():
        if hasattr(m, "reset_parameters"):
            m.reset_parameters()
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(class_weights(ytr), dtype=torch.float32))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    Xt, yt = torch.from_numpy(Xtr), torch.from_numpy(ytr.astype(np.int64))

    history, best, best_state, since_best = [], -1.0, None, 0
    for epoch in range(epochs):
        model.train()
        total = 0.0
        for idx in torch.randperm(len(Xt), generator=gen).split(batch):
            opt.zero_grad()
            loss = loss_fn(model(Xt[idx]), yt[idx])
            loss.backward()
            opt.step()
            total += loss.item() * len(idx)
        f1 = macro_f1(yva, predict(model, Xva))
        history.append({"epoch": epoch, "train_loss": total / len(Xt), "val_macro_f1": f1})
        if f1 > best:
            best, since_best = f1, 0
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            since_best += 1
            if since_best >= patience:
                break
    model.load_state_dict(best_state)
    return model, history


def fmt(name, r):
    rec = " / ".join(f"{x:.1%}" for x in r["recall_down_flat_up"])
    return f"{name:<20}{r['accuracy']:>9.1%}{r['macro_f1']:>10.3f}   {rec}"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m train.fit")
    p.add_argument("train_day", help=".npz from golden.dataset")
    p.add_argument("--model", default="logreg", choices=list(MODELS))
    p.add_argument("--epochs", type=int, default=30)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="data/raw/results")
    a = p.parse_args(argv)

    X, y, names = load(a.train_day)
    tr, va = split(len(y))
    shifts = scale_shifts(X[tr])
    Xtr, Xva = apply_scale(X[tr], shifts), apply_scale(X[va], shifts)
    print(f"train rows {len(Xtr):,}   validation rows {len(Xva):,}")
    print(f"train labels down/flat/up {np.bincount(y[tr], minlength=3) / len(Xtr)}")
    print(f"input shifts {dict(zip(names, shifts.tolist()))}\n")

    results = {}
    print(f"{'validation':<20}{'accuracy':>9}{'macro-F1':>10}   recall down / flat / up")
    for name, rule in BASELINES.items():
        results[name] = report(y[va], rule(X[va], names))
        print(fmt(name, results[name]))

    model, history = train_model(build_model(a.model, len(names)), Xtr, y[tr], Xva, y[va],
                                 epochs=a.epochs, seed=a.seed)
    results[a.model] = report(y[va], predict(model, Xva))
    print(fmt(a.model, results[a.model]))
    best = max(history, key=lambda h: h["val_macro_f1"])
    print(f"\n{len(history)} epochs, best validation macro-F1 at epoch {best['epoch']}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # Plain lists, so train.quantize can load this with weights_only=True.
    torch.save({"model": a.model, "state": model.state_dict(), "shifts": shifts.tolist(), "features": names},
               out / f"{a.model}.pt")
    (out / f"{a.model}.json").write_text(json.dumps(
        {"results": results, "history": history, "shifts": shifts.tolist(), "seed": a.seed}, indent=2))
    print(f"saved {out / a.model}.pt and .json")


if __name__ == "__main__":
    main()
