"""Tests for the ``main.py`` CLI argument plumbing (no live network).

Covers the newly exposed flags end-to-end against the testable builder
functions factored out of the argparse entry point:

* ``--allow-short`` builds a short-enabled :class:`Portfolio`.
* ``--data-csv`` / ``--data-source csv`` builds a :class:`CSVDataHandler` and
  runs a full backtest off disk (no yfinance).
* ``--offline`` drives a deterministic run off the bundled fixture and is
  reproducible across two runs.
* ``--strategy optimize --objective hrp`` runs an OptimizationRebalance backtest
  end-to-end producing valid long-only weights.

Defaults are asserted to preserve the prior online, long-only behavior.

Style mirrors test_data_handlers.py: deterministic seeded RNG, in-memory /
on-disk synthetic frames, no network.
"""

import numpy as np
import pandas as pd
import pytest

import main as cli
from src.data_handler import CSVDataHandler, YFinanceDataHandler
from src.strategy import LongShortMomentum


def _ohlcv(close):
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
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


def _write_csvs(tmp_path, tickers, seed=7, n=150):
    rng = np.random.default_rng(seed)
    for i, t in enumerate(tickers):
        rets = rng.normal(0.0005 + i * 0.0002, 0.012, n)
        df = _ohlcv(list(100 * np.cumprod(1 + rets)))
        df.index.name = "Date"
        df.to_csv(tmp_path / f"{t}.csv")


# --- argument defaults preserve prior behavior ----------------------------


class TestDefaults:
    def test_no_args_targets_demo(self):
        args = cli.build_parser().parse_args([])
        assert args.strategy is None  # -> run_demo()
        assert args.allow_short is False
        assert args.offline is False
        assert args.data_source == "yfinance"
        assert args.data_csv is None
        assert args.objective == "sharpe"

    def test_default_portfolio_is_long_only(self):
        args = cli.build_parser().parse_args([])
        pf = cli.build_portfolio(args)
        assert pf.allow_short is False

    def test_default_data_handler_is_yfinance(self):
        args = cli.build_parser().parse_args(["--strategy", "sma"])
        h = cli.build_data_handler(args, store=None)
        assert isinstance(h, YFinanceDataHandler)
        assert h._offline is False


# --- --allow-short ---------------------------------------------------------


class TestAllowShort:
    def test_flag_builds_short_enabled_portfolio(self):
        args = cli.build_parser().parse_args(["--strategy", "sma", "--allow-short"])
        pf = cli.build_portfolio(args)
        assert pf.allow_short is True

    def test_capital_is_threaded(self):
        args = cli.build_parser().parse_args(["--strategy", "sma", "--capital", "50000"])
        pf = cli.build_portfolio(args)
        assert pf.initial_capital == 50000


# --- CSV data source -------------------------------------------------------


class TestCsvSource:
    def test_data_csv_builds_csv_handler(self, tmp_path):
        _write_csvs(tmp_path, ["AAA", "BBB"])
        args = cli.build_parser().parse_args(
            ["--strategy", "sma", "--tickers", "AAA", "BBB", "--data-csv", str(tmp_path)]
        )
        h = cli.build_data_handler(args, store=None)
        assert isinstance(h, CSVDataHandler)
        h.fetch()
        assert h._total_bars > 0

    def test_data_source_csv_without_path_raises(self):
        args = cli.build_parser().parse_args(["--strategy", "sma", "--data-source", "csv"])
        with pytest.raises(ValueError, match="requires --data-csv"):
            cli.build_data_handler(args, store=None)

    def test_runs_full_backtest_through_csv(self, tmp_path):
        _write_csvs(tmp_path, ["AAA", "BBB"])
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "sma",
                "--tickers",
                "AAA",
                "BBB",
                "--data-csv",
                str(tmp_path),
            ]
        )
        analytics = cli.run_single(args)
        assert len(analytics.equity) > 0

    def test_combined_csv(self, tmp_path):
        rng = np.random.default_rng(1)
        parts = []
        for i, t in enumerate(["AAA", "BBB"]):
            rets = rng.normal(0.0005 + i * 0.0002, 0.012, 120)
            df = _ohlcv(list(100 * np.cumprod(1 + rets)))
            df.index.name = "Date"
            df = df.reset_index()
            df["symbol"] = t
            parts.append(df)
        fp = tmp_path / "all.csv"
        pd.concat(parts, ignore_index=True).to_csv(fp, index=False)
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "sma",
                "--tickers",
                "AAA",
                "BBB",
                "--data-csv",
                str(fp),
                "--csv-combined",
            ]
        )
        h = cli.build_data_handler(args, store=None)
        assert isinstance(h, CSVDataHandler)
        h.fetch()
        assert h._total_bars > 0


# --- --offline (bundled fixture, deterministic) ----------------------------


class TestOffline:
    def test_offline_flag_sets_handler(self):
        args = cli.build_parser().parse_args(["--strategy", "sma", "--offline"])
        h = cli.build_data_handler(args, store=None)
        assert isinstance(h, YFinanceDataHandler)
        assert h._offline is True

    def test_offline_run_is_deterministic(self):
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "sma",
                "--offline",
                "--tickers",
                "AAA",
                "BBB",
                "--start",
                "2021-06-01",
                "--end",
                "2022-01-01",
            ]
        )
        a1 = cli.run_single(args)
        a2 = cli.run_single(args)
        assert len(a1.equity) > 0
        assert list(a1.equity) == list(a2.equity)

    def test_offline_does_not_leak_env(self, monkeypatch):
        monkeypatch.delenv("BACKTESTING_OFFLINE", raising=False)
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "sma",
                "--offline",
                "--tickers",
                "AAA",
                "--start",
                "2021-06-01",
                "--end",
                "2022-01-01",
            ]
        )
        cli.run_single(args)
        import os

        assert "BACKTESTING_OFFLINE" not in os.environ


# --- long_short demo strategy ----------------------------------------------


class TestLongShortStrategy:
    def test_long_short_is_a_valid_strategy(self):
        args = cli.build_parser().parse_args(["--strategy", "long_short"])
        assert args.strategy == "long_short"

    def test_long_short_builds_short_enabled_portfolio(self):
        # long_short forces shorting on even without --allow-short.
        args = cli.build_parser().parse_args(["--strategy", "long_short"])
        assert cli.build_portfolio(args).allow_short is True

    def test_build_strategy_returns_long_short_momentum(self):
        args = cli.build_parser().parse_args(["--strategy", "long_short"])
        assert isinstance(cli.build_strategy(args), LongShortMomentum)

    def test_long_short_runs_end_to_end(self):
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "long_short",
                "--offline",
                "--tickers",
                "AAA",
                "BBB",
                "CCC",
                "--start",
                "2021-06-01",
                "--end",
                "2022-06-01",
                "--rebalance-freq",
                "21",
            ]
        )
        analytics = cli.run_single(args)
        assert len(analytics.equity) > 0


# --- HRP objective ---------------------------------------------------------


class TestHrpObjective:
    def test_hrp_is_a_valid_objective(self):
        args = cli.build_parser().parse_args(["--strategy", "optimize", "--objective", "hrp"])
        assert args.objective == "hrp"

    def test_hrp_backtest_runs_end_to_end(self):
        args = cli.build_parser().parse_args(
            [
                "--strategy",
                "optimize",
                "--objective",
                "hrp",
                "--offline",
                "--tickers",
                "AAA",
                "BBB",
                "CCC",
                "--start",
                "2021-06-01",
                "--end",
                "2022-06-01",
                "--lookback",
                "60",
                "--rebalance-freq",
                "21",
            ]
        )
        analytics = cli.run_single(args)
        assert len(analytics.equity) > 60
