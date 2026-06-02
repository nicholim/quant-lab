from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.datastore import DataStore
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import (
    CrossSectionalMomentum,
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


def main() -> None:
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


if __name__ == "__main__":
    main()
