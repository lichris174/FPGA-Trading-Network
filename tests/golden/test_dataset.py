"""End-to-end test of the dataset builder on a synthetic file."""

import numpy as np

from golden.dataset import HORIZONS, build, main
from golden.features import FEATURE_NAMES, LAG
from golden.itch.parser import encode, write_frames


def synthetic_day(path, n_updates=200):
    msgs = [
        encode({"message_type": "R", "stock_locate": 1, "stock": "AAPL"}),
        encode({"message_type": "S", "event_code": "Q"}),
        encode({"message_type": "A", "stock_locate": 1, "order_reference_number": 1,
                "buy_sell_indicator": "B", "shares": 100, "stock": "AAPL", "price": 500000}),
    ]
    for i in range(n_updates):
        # Asks step between 50.01 and 50.03, so the mid moves both ways.
        msgs.append(encode({"message_type": "A", "stock_locate": 1, "timestamp": 1000 + i,
                            "order_reference_number": 10 + i, "buy_sell_indicator": "S",
                            "shares": 5, "stock": "AAPL", "price": 500100 + 100 * (i % 3)}))
        if i % 3 == 0 and i:
            msgs.append(encode({"message_type": "D", "stock_locate": 1, "timestamp": 1000 + i,
                                "order_reference_number": 10 + i - 3}))
    with open(path, "wb") as f:
        write_frames(f, msgs)


def test_build_shapes_and_alignment(tmp_path):
    path = tmp_path / "day.itch"
    synthetic_day(path)
    d = build(path, "AAPL")
    n = len(d["mid2"])
    assert n > 100
    assert d["features"].shape == (n, len(FEATURE_NAMES))
    assert d["timestamp"].shape == (n,)
    for k in HORIZONS:
        lab = d[f"label_k{k}"]
        assert len(lab) == max(0, n - k)
        assert set(np.unique(lab)) <= {-1, 0, 1}
        # Label i compares row i with row i + k.
        assert np.array_equal(lab, np.sign(d["mid2"][k:] - d["mid2"][:-k]))


def test_main_writes_npz(tmp_path, capsys):
    path = tmp_path / "day.itch"
    out = tmp_path / "aapl.npz"
    synthetic_day(path)
    main([str(path), "AAPL", str(out)])
    d = np.load(out)
    assert list(d["feature_names"]) == list(FEATURE_NAMES)
    assert d["features"].dtype == np.int64
    text = capsys.readouterr().out
    assert "rows" in text and "label_k10" in text and "imbalance" in text
    assert d["features"].shape[0] == len(d["mid2"]) > LAG
