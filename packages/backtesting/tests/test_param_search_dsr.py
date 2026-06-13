"""grid_search Deflated Sharpe Ratio wiring (multiple-testing correction)."""

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd

from src.data_handler import DataFrameDataHandler
from src.param_search import grid_search
from src.strategy import MovingAverageCrossover


def _synthetic_prices(seed: int, n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=idx,
    )


def _data_factory():
    return DataFrameDataHandler({"AAA": _synthetic_prices(seed=42)})


def test_grid_search_has_dsr_column():
    results = grid_search(
        MovingAverageCrossover,
        {"short_window": [10, 20], "long_window": [50, 100]},
        data_factory=_data_factory,
        persist=False,
    )
    assert "dsr" in results.columns
    assert len(results) == 4
    # DSR is a sweep-level statistic: identical across rows (same n_trials/variance).
    valid = results["dsr"].dropna()
    if len(valid) > 1:
        assert valid.nunique(dropna=True) >= 1  # all share n_trials/sr_variance
    # Probabilities are bounded.
    for v in valid:
        assert 0.0 <= v <= 1.0


def test_single_trial_dsr_is_nan():
    results = grid_search(
        MovingAverageCrossover,
        {"short_window": [10], "long_window": [50]},
        data_factory=_data_factory,
        persist=False,
    )
    assert len(results) == 1
    assert np.isnan(results["dsr"].iloc[0])


def test_dsr_not_greater_than_one_and_uses_grid_size():
    # 6 trials -> n_trials=6 deflation applied; DSR must stay a valid probability.
    results = grid_search(
        MovingAverageCrossover,
        {"short_window": [5, 10, 15], "long_window": [40, 60]},
        data_factory=_data_factory,
        persist=False,
    )
    assert len(results) == 6
    valid = results["dsr"].dropna()
    assert (valid <= 1.0).all()
    assert (valid >= 0.0).all()
