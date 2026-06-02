"""Backtest throughput benchmark on synthetic data (no network, no DuckDB).

Measures how many (bar x symbol) market events the event loop processes per
second, end to end: data handler -> strategy -> portfolio -> execution ->
fill -> analytics. Uses a deterministic random-walk price series so the run is
reproducible and never touches yfinance or disk.

Run:
    python benchmarks/throughput.py
    python benchmarks/throughput.py --bars 5040 --symbols 10 --repeat 5

The number reported is "events/sec" where one event == one (bar, symbol) pair,
which is the unit the event loop actually iterates over (see backtest.py:
iter_bars yields one MarketEvent per symbol per bar).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Allow running as `python benchmarks/throughput.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import MovingAverageCrossover


class SyntheticDataHandler(YFinanceDataHandler):
    """Offline data handler: a deterministic geometric random walk per symbol.

    Reuses every read method of YFinanceDataHandler (get_latest_bars,
    get_next_open, iter_bars, ...) and only replaces ``fetch`` so the loop runs
    identically to a real backtest but without any I/O.
    """

    def __init__(self, n_bars: int, symbols: list[str], seed: int = 7):
        super().__init__(symbols, "2010-01-01", "2030-01-01", store=None)
        self._n_bars = n_bars
        self._seed = seed

    def fetch(self) -> None:
        rng = np.random.default_rng(self._seed)
        index = pd.bdate_range("2010-01-01", periods=self._n_bars)
        for i, ticker in enumerate(self.tickers):
            rets = rng.normal(0.0003, 0.012, size=self._n_bars)
            close = 100.0 * np.exp(np.cumsum(rets)) * (1.0 + 0.05 * i)
            opens = close * (1.0 + rng.normal(0, 0.001, size=self._n_bars))
            high = np.maximum(opens, close) * (
                1.0 + np.abs(rng.normal(0, 0.003, size=self._n_bars))
            )
            low = np.minimum(opens, close) * (1.0 - np.abs(rng.normal(0, 0.003, size=self._n_bars)))
            vol = rng.integers(1_000_000, 5_000_000, size=self._n_bars)
            self._data[ticker] = pd.DataFrame(
                {"Open": opens, "High": high, "Low": low, "Close": close, "Volume": vol},
                index=index,
            )
        self._total_bars = self._n_bars


def run_once(n_bars: int, symbols: list[str]) -> tuple[float, int]:
    data = SyntheticDataHandler(n_bars, symbols)
    portfolio = Portfolio(initial_capital=100_000, position_size_pct=0.15)
    execution = SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005)
    bt = Backtest(
        data,
        MovingAverageCrossover(short_window=20, long_window=50),
        portfolio,
        execution,
        strategy_name="",  # empty -> no DuckDB persistence
        store=None,
        benchmark=None,
    )
    t0 = time.perf_counter()
    bt.run()
    elapsed = time.perf_counter() - t0
    events = n_bars * len(symbols)
    return elapsed, events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bars", type=int, default=2520, help="bars per symbol (default ~10y daily)"
    )
    parser.add_argument("--symbols", type=int, default=5, help="number of synthetic symbols")
    parser.add_argument("--repeat", type=int, default=3, help="timed repetitions")
    args = parser.parse_args()

    symbols = [f"SYM{i}" for i in range(args.symbols)]
    print(
        f"Backtest throughput: {args.bars} bars x {len(symbols)} symbols "
        f"= {args.bars * len(symbols):,} events, {args.repeat} runs\n"
    )

    best_eps = 0.0
    for r in range(args.repeat):
        elapsed, events = run_once(args.bars, symbols)
        eps = events / elapsed
        best_eps = max(best_eps, eps)
        print(f"  run {r + 1}: {elapsed:.3f}s  ->  {eps:,.0f} events/sec")

    print(
        f"\nBest: {best_eps:,.0f} events/sec "
        f"({args.bars * len(symbols) / best_eps * 1000:.1f} ms for the full backtest)"
    )


if __name__ == "__main__":
    main()
