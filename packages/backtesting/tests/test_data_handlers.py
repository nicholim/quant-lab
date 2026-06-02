"""Tests for the offline data handlers and the wired optimizer objectives.

Covers (no live network):

* ``CSVDataHandler`` (per-ticker files and one combined file) and
  ``DataFrameDataHandler`` produce bar/event semantics IDENTICAL to a
  ``YFinanceDataHandler`` fed the same synthetic frames — asserted via
  ``iter_bars`` count, ``get_latest_bars`` windowing, ``get_resampled_bars``
  no-look-ahead, ``get_current_bar``, ``get_current_price`` and
  ``get_next_open``.
* Column normalization (case-insensitive, Volume optional, date-column
  promotion) and date slicing.
* ``OptimizationRebalanceStrategy`` driving the three newly wired objectives
  (``sortino``, ``max_return_target_vol``, ``min_vol_target_return``)
  end-to-end, producing valid long-only weights summing to 1.

Style mirrors test_edge_cases.py: deterministic seeded RNG, in-memory frames.
"""

import numpy as np
import pandas as pd
import pytest

from src.backtest import Backtest
from src.data_handler import (
    CSVDataHandler,
    DataFrameDataHandler,
    YFinanceDataHandler,
)
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import OptimizationRebalanceStrategy

# --- synthetic data helpers ----------------------------------------------


def _ohlcv(close, *, open_=None):
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    if open_ is None:
        open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "Open": open_,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1000,
        },
        index=idx,
    )


def _synthetic_frames(seed=7, n=120):
    rng = np.random.default_rng(seed)
    frames = {}
    for i, t in enumerate(["AAA", "BBB"]):
        rets = rng.normal(0.0005 + i * 0.0002, 0.012, n)
        frames[t] = _ohlcv(list(100 * np.cumprod(1 + rets)))
    return frames


def _yf_handler(frames):
    """A YFinanceDataHandler with frames pre-injected (no network)."""

    class MemoryHandler(YFinanceDataHandler):
        def fetch(self):
            self._finalize_data(self._injected)

    h = MemoryHandler(list(frames), "2021-01-01", "2021-12-31")
    h._injected = frames
    h.fetch()
    return h


# --- parity: a handler must match YFinanceDataHandler at every read --------


def _assert_parity(ref, other, tickers):
    """Drive ref and other through identical pointer positions; compare reads."""
    assert other._total_bars == ref._total_bars
    # iter_bars yields the same number of MarketEvents in the same order.
    ref_events = list(ref.iter_bars())
    other_events = list(other.iter_bars())
    assert len(ref_events) == len(other_events)
    assert [(e.symbol) for e in ref_events] == [(e.symbol) for e in other_events]
    assert [e.timestamp for e in ref_events] == [e.timestamp for e in other_events]

    for idx in (0, 1, 5, ref._total_bars // 2, ref._total_bars):
        ref._current_idx = idx
        other._current_idx = idx
        for t in tickers:
            # check_freq=False: the index *freq* attr is metadata, not data — a
            # CSV round-trip drops it (as real yfinance data also has freq=None),
            # but the bar values/timestamps are identical, which is what matters.
            pd.testing.assert_frame_equal(
                ref.get_latest_bars(t, 10),
                other.get_latest_bars(t, 10),
                check_freq=False,
                check_exact=False,
            )
            # Scalars compared with a tolerance: a CSV round-trip loses ~1ulp of
            # float precision (genuine serialization loss, not a handler bug).
            assert ref.get_current_price(t) == pytest.approx(other.get_current_price(t))
            rb, ob = ref.get_current_bar(t), other.get_current_bar(t)
            if rb is None:
                assert ob is None
            else:
                assert ob is not None
                assert rb.keys() == ob.keys()
                for k in rb:
                    assert rb[k] == pytest.approx(ob[k])
            assert ref.get_next_open(t) == pytest.approx(other.get_next_open(t))
            pd.testing.assert_frame_equal(
                ref.get_resampled_bars(t, "W", 4),
                other.get_resampled_bars(t, "W", 4),
                check_freq=False,
                check_exact=False,
            )


class TestDataFrameDataHandlerParity:
    def test_matches_yfinance_semantics(self):
        frames = _synthetic_frames()
        ref = _yf_handler(frames)
        df_handler = DataFrameDataHandler(frames)
        df_handler.fetch()  # no-op, already finalized
        _assert_parity(ref, df_handler, ["AAA", "BBB"])

    def test_infers_dates_from_index(self):
        frames = _synthetic_frames(n=30)
        h = DataFrameDataHandler(frames)
        assert h.start_date == "2021-01-01"
        # 30 business days from 2021-01-01.
        assert h.end_date == str(frames["AAA"].index.max().date())

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            DataFrameDataHandler({})

    def test_lowercase_columns_and_missing_volume(self):
        idx = pd.date_range("2021-01-01", periods=5, freq="B")
        df = pd.DataFrame(
            {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5},
            index=idx,
        )
        h = DataFrameDataHandler({"Z": df})
        h._current_idx = 5
        bars = h.get_latest_bars("Z", 5)
        assert list(bars.columns) == ["Open", "High", "Low", "Close", "Volume"]
        assert (bars["Volume"] == 0.0).all()
        assert h.get_current_price("Z") == 10.5

    def test_date_column_promoted_to_index(self):
        df = pd.DataFrame(
            {
                "Date": ["2021-01-04", "2021-01-05", "2021-01-06"],
                "Open": [1.0, 2.0, 3.0],
                "High": [1.5, 2.5, 3.5],
                "Low": [0.5, 1.5, 2.5],
                "Close": [1.2, 2.2, 3.2],
                "Volume": [100, 200, 300],
            }
        )
        h = DataFrameDataHandler({"Z": df})
        h._current_idx = 3
        bars = h.get_latest_bars("Z", 3)
        assert isinstance(bars.index, pd.DatetimeIndex)
        assert bars.index[0] == pd.Timestamp("2021-01-04")

    def test_missing_ohlc_column_raises(self):
        df = pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0]})
        with pytest.raises(ValueError, match="missing required column"):
            DataFrameDataHandler({"Z": df})

    def test_rows_sorted_by_time(self):
        df = pd.DataFrame(
            {
                "Date": ["2021-01-06", "2021-01-04", "2021-01-05"],
                "Open": [3.0, 1.0, 2.0],
                "High": [3.0, 1.0, 2.0],
                "Low": [3.0, 1.0, 2.0],
                "Close": [3.0, 1.0, 2.0],
            }
        )
        h = DataFrameDataHandler({"Z": df})
        h._current_idx = 3
        closes = list(h.get_latest_bars("Z", 3)["Close"])
        assert closes == [1.0, 2.0, 3.0]


class TestCSVDataHandlerParity:
    def test_per_ticker_matches_yfinance(self, tmp_path):
        frames = _synthetic_frames()
        for t, df in frames.items():
            out = df.copy()
            out.index.name = "Date"
            out.to_csv(tmp_path / f"{t}.csv")
        ref = _yf_handler(frames)
        csv_handler = CSVDataHandler(["AAA", "BBB"], tmp_path)
        csv_handler.fetch()
        _assert_parity(ref, csv_handler, ["AAA", "BBB"])

    def test_combined_file_matches_yfinance(self, tmp_path):
        frames = _synthetic_frames()
        parts = []
        for t, df in frames.items():
            out = df.copy()
            out.index.name = "Date"
            out = out.reset_index()
            out["symbol"] = t
            parts.append(out)
        combined = pd.concat(parts, ignore_index=True)
        fp = tmp_path / "all.csv"
        combined.to_csv(fp, index=False)
        ref = _yf_handler(frames)
        csv_handler = CSVDataHandler(["AAA", "BBB"], fp, per_ticker=False)
        csv_handler.fetch()
        _assert_parity(ref, csv_handler, ["AAA", "BBB"])

    def test_date_slicing(self, tmp_path):
        frames = _synthetic_frames(n=60)
        for t, df in frames.items():
            out = df.copy()
            out.index.name = "Date"
            out.to_csv(tmp_path / f"{t}.csv")
        h = CSVDataHandler(["AAA", "BBB"], tmp_path, start_date="2021-01-15", end_date="2021-02-15")
        h.fetch()
        for t in ("AAA", "BBB"):
            full = frames[t]
            expected = full[
                (full.index >= pd.Timestamp("2021-01-15"))
                & (full.index <= pd.Timestamp("2021-02-15"))
            ]
            assert len(h._data[t]) == len(expected)

    def test_missing_file_raises(self, tmp_path):
        h = CSVDataHandler(["NOPE"], tmp_path)
        with pytest.raises(FileNotFoundError):
            h.fetch()

    def test_per_ticker_requires_directory(self, tmp_path):
        fp = tmp_path / "x.csv"
        fp.write_text("Date,Open,High,Low,Close\n2021-01-01,1,1,1,1\n")
        h = CSVDataHandler(["X"], fp)
        with pytest.raises(ValueError, match="directory"):
            h.fetch()

    def test_combined_requires_symbol_column(self, tmp_path):
        fp = tmp_path / "all.csv"
        pd.DataFrame(
            {"Date": ["2021-01-01"], "Open": [1], "High": [1], "Low": [1], "Close": [1]}
        ).to_csv(fp, index=False)
        h = CSVDataHandler(["X"], fp, per_ticker=False)
        with pytest.raises(ValueError, match="symbol"):
            h.fetch()

    def test_combined_unknown_ticker_raises(self, tmp_path):
        fp = tmp_path / "all.csv"
        pd.DataFrame(
            {
                "Date": ["2021-01-01"],
                "symbol": ["AAA"],
                "Open": [1],
                "High": [1],
                "Low": [1],
                "Close": [1],
            }
        ).to_csv(fp, index=False)
        h = CSVDataHandler(["ZZZ"], fp, per_ticker=False)
        with pytest.raises(ValueError, match="No rows"):
            h.fetch()

    def test_runs_full_backtest_through_csv(self, tmp_path):
        from src.strategy import MovingAverageCrossover

        frames = _synthetic_frames(n=120)
        for t, df in frames.items():
            out = df.copy()
            out.index.name = "Date"
            out.to_csv(tmp_path / f"{t}.csv")
        h = CSVDataHandler(["AAA", "BBB"], tmp_path)
        analytics = Backtest(
            h,
            MovingAverageCrossover(short_window=5, long_window=20),
            Portfolio(initial_capital=100_000),
            SimulatedExecution(),
        ).run()
        assert len(analytics.equity) > 0


# --- newly wired optimizer objectives -------------------------------------


class TestWiredOptimizerObjectives:
    def _handler3(self, seed=0):
        n = 150
        rng = np.random.default_rng(seed)
        frames = {}
        for i, t in enumerate(["AAA", "BBB", "CCC"]):
            rets = rng.normal(0.0005 + i * 0.0002, 0.012, n)
            frames[t] = _ohlcv(list(100 * np.cumprod(1 + rets)))
        return DataFrameDataHandler(frames)

    def test_hrp_returns_long_only_simplex(self):
        h = self._handler3()
        h._current_idx = 80
        strat = OptimizationRebalanceStrategy(["AAA", "BBB", "CCC"], lookback=60, objective="hrp")
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6
        assert all(w >= -1e-9 for w in targets.values())

    def test_hrp_full_backtest_runs(self):
        h = self._handler3()
        portfolio = Portfolio(initial_capital=100_000)
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, rebalance_freq=21, objective="hrp"
        )
        analytics = Backtest(h, strat, portfolio, SimulatedExecution()).run()
        assert len(analytics.equity) > 60
        assert all(q >= 0 for q in portfolio.positions.values())

    def test_sortino_returns_long_only_simplex(self):
        h = self._handler3()
        h._current_idx = 80
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, objective="sortino"
        )
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6
        assert all(w >= -1e-9 for w in targets.values())

    def test_max_return_target_vol_returns_valid_weights(self):
        h = self._handler3()
        h._current_idx = 80
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, objective="max_return_target_vol", target=0.25
        )
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6
        assert all(w >= -1e-9 for w in targets.values())

    def test_min_vol_target_return_returns_valid_weights(self):
        h = self._handler3()
        h._current_idx = 80
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, objective="min_vol_target_return", target=0.10
        )
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6
        assert all(w >= -1e-9 for w in targets.values())

    def test_min_vol_target_return_clamps_overshoot(self):
        # A wildly high target should not raise/skip; it clamps to max-achievable.
        h = self._handler3()
        h._current_idx = 80
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, objective="min_vol_target_return", target=99.0
        )
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6

    def test_target_objective_requires_target(self):
        with pytest.raises(ValueError, match="requires a `target`"):
            OptimizationRebalanceStrategy(["AAA", "BBB"], objective="max_return_target_vol")
        with pytest.raises(ValueError, match="requires a `target`"):
            OptimizationRebalanceStrategy(["AAA", "BBB"], objective="min_vol_target_return")

    def test_sortino_full_backtest_runs(self):
        h = self._handler3()
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, rebalance_freq=21, objective="sortino"
        )
        analytics = Backtest(
            h, strat, Portfolio(initial_capital=100_000), SimulatedExecution()
        ).run()
        assert len(analytics.equity) > 60
        portfolio = analytics  # equity present
        assert portfolio is not None

    def test_max_return_target_vol_full_backtest_runs(self):
        h = self._handler3()
        portfolio = Portfolio(initial_capital=100_000)
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"],
            lookback=60,
            rebalance_freq=21,
            objective="max_return_target_vol",
            target=0.25,
        )
        analytics = Backtest(h, strat, portfolio, SimulatedExecution()).run()
        assert len(analytics.equity) > 60
        assert all(q >= 0 for q in portfolio.positions.values())
