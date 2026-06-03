import argparse
import os
from collections.abc import Sequence

from src.backtest import Backtest
from src.data_handler import (
    CSVDataHandler,
    DataHandler,
    YFinanceDataHandler,
)
from src.datastore import DataStore
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import (
    CrossSectionalMomentum,
    LongShortMomentum,
    MeanReversion,
    MovingAverageCrossover,
    OptimizationRebalanceStrategy,
)


def run_strategy(name: str, tickers, start, end, strategy, store, capital=100_000, benchmark="SPY"):
    print(f"\n{'=' * 60}")
    print(f"Strategy: {name}")
    print(f"{'=' * 60}")

    data = YFinanceDataHandler(tickers, start, end, store=store)
    portfolio = Portfolio(initial_capital=capital, position_size_pct=0.15)
    execution = SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005)

    bt = Backtest(
        data, strategy, portfolio, execution, strategy_name=name, store=store, benchmark=benchmark
    )
    analytics = bt.run()
    analytics.generate_report()
    return analytics


# --- CLI plumbing (testable, additive) ------------------------------------
#
# The functions below are factored out of the argparse entry point so they can
# be unit-tested without a live network. ``run_demo`` preserves the original
# zero-argument behavior (the four-strategy DuckDB showcase); the new flags only
# change behavior when explicitly passed.

_STRATEGIES = ("sma", "mean_reversion", "momentum", "long_short", "optimize")
_OBJECTIVES = (
    "sharpe",
    "min_vol",
    "min_cvar",
    "risk_parity",
    "sortino",
    "hrp",
    "max_return_target_vol",
    "min_vol_target_return",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    With NO arguments the CLI runs the original demo (``run_demo``); passing
    ``--strategy`` switches to a single configurable backtest. All data-source
    and portfolio flags default to the prior online, long-only behavior.
    """
    p = argparse.ArgumentParser(
        prog="backtesting",
        description="Event-driven backtester CLI. Run with no args for the demo, "
        "or pass --strategy to configure a single backtest.",
    )
    p.add_argument(
        "--strategy",
        choices=_STRATEGIES,
        default=None,
        help="Run a single strategy instead of the multi-strategy demo.",
    )
    p.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT", "JPM"], help="Ticker symbols.")
    p.add_argument("--start", default="2020-01-01", help="Start date (YYYY-MM-DD).")
    p.add_argument("--end", default="2024-01-01", help="End date (YYYY-MM-DD).")
    p.add_argument("--capital", type=float, default=100_000, help="Initial capital.")

    # Data source.
    p.add_argument(
        "--data-source",
        choices=("yfinance", "csv"),
        default="yfinance",
        help="Where to load OHLCV from (default: yfinance).",
    )
    p.add_argument(
        "--data-csv",
        default=None,
        help="CSV path. A directory (one <TICKER>.csv per ticker) unless "
        "--csv-combined is set, in which case a single file with a 'symbol' "
        "column. Implies --data-source csv. Each CSV needs OHLC columns "
        "(case-insensitive; Volume optional) and a Date/Datetime/Timestamp "
        "column or a parseable first column.",
    )
    p.add_argument(
        "--csv-combined",
        action="store_true",
        help="Treat --data-csv as a single combined file with a 'symbol' column "
        "instead of one file per ticker.",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Use the bundled deterministic OHLCV fixture instead of hitting "
        "yfinance (equivalent to BACKTESTING_OFFLINE=1). Makes a run fully "
        "reproducible with no network access.",
    )

    # Portfolio.
    p.add_argument(
        "--allow-short",
        action="store_true",
        help="Enable native short selling (long/short backtest). Default off "
        "(long-only, byte-identical to prior behavior).",
    )

    # Optimization-rebalance knobs (used only when --strategy optimize).
    p.add_argument(
        "--objective",
        choices=_OBJECTIVES,
        default="sharpe",
        help="Optimizer objective for --strategy optimize (default: sharpe).",
    )
    p.add_argument("--lookback", type=int, default=252, help="Trailing window (bars).")
    p.add_argument("--rebalance-freq", type=int, default=21, help="Rebalance interval (bars).")
    p.add_argument(
        "--target",
        type=float,
        default=None,
        help="Target for max_return_target_vol (annual vol cap) / "
        "min_vol_target_return (min annual return).",
    )
    return p


def build_data_handler(args: argparse.Namespace, store: DataStore | None) -> DataHandler:
    """Construct the data handler from parsed args.

    ``--data-csv`` (or ``--data-source csv``) loads via :class:`CSVDataHandler`;
    otherwise :class:`YFinanceDataHandler` is used. ``--offline`` is threaded
    into the yfinance handler so the bundled fixture drives the run; for the CSV
    source offline is irrelevant (no network is touched either way).
    """
    use_csv = args.data_source == "csv" or args.data_csv is not None
    if use_csv:
        if args.data_csv is None:
            raise ValueError("--data-source csv requires --data-csv PATH")
        return CSVDataHandler(
            list(args.tickers),
            args.data_csv,
            start_date=args.start,
            end_date=args.end,
            per_ticker=not args.csv_combined,
        )
    return YFinanceDataHandler(
        list(args.tickers), args.start, args.end, store=store, offline=args.offline
    )


def build_portfolio(args: argparse.Namespace) -> Portfolio:
    """Construct the portfolio, threading ``--allow-short`` through.

    The ``long_short`` demo strategy emits negative target weights, so it is run
    with shorting enabled regardless of the flag (the whole point is to exercise
    the short path); for every other strategy ``--allow-short`` controls it.
    """
    allow_short = args.allow_short or args.strategy == "long_short"
    return Portfolio(
        initial_capital=args.capital,
        position_size_pct=0.15,
        allow_short=allow_short,
    )


def build_strategy(args: argparse.Namespace):
    """Build the requested single strategy from parsed args."""
    if args.strategy == "sma":
        return MovingAverageCrossover(short_window=20, long_window=50)
    if args.strategy == "mean_reversion":
        return MeanReversion(lookback=20, entry_z=2.0, exit_z=0.5)
    if args.strategy == "momentum":
        return CrossSectionalMomentum(
            list(args.tickers), lookback=126, top_k=1, rebalance_freq=args.rebalance_freq
        )
    if args.strategy == "long_short":
        return LongShortMomentum(
            list(args.tickers), lookback=126, top_k=1, rebalance_freq=args.rebalance_freq
        )
    if args.strategy == "optimize":
        return OptimizationRebalanceStrategy(
            list(args.tickers),
            lookback=args.lookback,
            rebalance_freq=args.rebalance_freq,
            objective=args.objective,
            target=args.target,
        )
    raise ValueError(f"Unknown strategy: {args.strategy!r}")


def run_single(args: argparse.Namespace, store: DataStore | None = None):
    """Run one configurable backtest from parsed args and print a report.

    Honors --offline by also setting BACKTESTING_OFFLINE for the duration so the
    DuckDB store's fetch path serves the fixture too (the handler-level flag only
    covers the no-store path).
    """
    prev_offline = os.environ.get("BACKTESTING_OFFLINE")
    if args.offline:
        os.environ["BACKTESTING_OFFLINE"] = "1"
    try:
        data = build_data_handler(args, store)
        portfolio = build_portfolio(args)
        strategy = build_strategy(args)
        name = f"{args.strategy}"
        if args.strategy == "optimize":
            name += f" ({args.objective})"
        bt = Backtest(
            data,
            strategy,
            portfolio,
            SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005),
            strategy_name=name,
            store=store,
        )
        analytics = bt.run()
        analytics.generate_report()
        return analytics
    finally:
        if args.offline:
            if prev_offline is None:
                os.environ.pop("BACKTESTING_OFFLINE", None)
            else:
                os.environ["BACKTESTING_OFFLINE"] = prev_offline


def run_demo() -> None:
    """The original four-strategy DuckDB showcase (unchanged behavior)."""
    tickers = ["AAPL", "MSFT", "JPM"]
    start = "2020-01-01"
    end = "2024-01-01"

    # Initialize DuckDB store (caches market data + persists results)
    store = DataStore("data/backtests.duckdb")

    # Strategy 1: SMA Crossover
    run_strategy(
        "SMA Crossover (20/50)",
        tickers,
        start,
        end,
        MovingAverageCrossover(short_window=20, long_window=50),
        store=store,
    )

    # Strategy 2: Mean Reversion
    run_strategy(
        "Mean Reversion (z=2.0)",
        tickers,
        start,
        end,
        MeanReversion(lookback=20, entry_z=2.0, exit_z=0.5),
        store=store,
    )

    # Strategy 3: MPT walk-forward rebalancing (calls the optimization engine).
    # Re-optimizes max-Sharpe weights monthly on a trailing 1-year window,
    # executed net of slippage + commission — the canonical "does MPT survive
    # costs and drift?" experiment that neither repo can run alone.
    run_strategy(
        "MPT Rebalance (max-Sharpe, monthly)",
        tickers,
        start,
        end,
        OptimizationRebalanceStrategy(tickers, lookback=252, rebalance_freq=21, objective="sharpe"),
        store=store,
    )

    # Strategy 4: Cross-sectional momentum — monthly, hold the single strongest
    # trailing performer (top_k=1), ranked across the universe.
    run_strategy(
        "Cross-Sectional Momentum (top-1, monthly)",
        tickers,
        start,
        end,
        CrossSectionalMomentum(tickers, lookback=126, top_k=1, rebalance_freq=21),
        store=store,
    )

    # Cross-strategy comparison from DuckDB
    print(f"\n{'=' * 60}")
    print("STRATEGY COMPARISON (from DuckDB)")
    print(f"{'=' * 60}")
    comparison = store.compare_strategies()
    print(comparison.to_string(index=False))

    # Example SQL query: trades with biggest slippage
    print(f"\n{'=' * 60}")
    print("TOP 5 TRADES BY SLIPPAGE (SQL query)")
    print(f"{'=' * 60}")
    top_slippage = store.query("""
        SELECT t.symbol, t.direction, t.quantity, t.price,
               ROUND(t.slippage, 2) AS slippage,
               r.strategy_name
        FROM trades t
        JOIN backtest_runs r ON t.run_id = r.run_id
        ORDER BY t.slippage DESC
        LIMIT 5
    """)
    print(top_slippage.to_string(index=False))

    store.close()


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point.

    With no ``--strategy`` flag, runs the original multi-strategy demo. With
    ``--strategy`` set, runs a single configurable backtest (data source,
    long/short, offline, objective all selectable).
    """
    args = build_parser().parse_args(argv)
    if args.strategy is None:
        run_demo()
        return
    run_single(args)


if __name__ == "__main__":
    main()
