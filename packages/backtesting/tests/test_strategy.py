"""Tests for the LongShortMomentum demo strategy.

Covers:
* `_rank` emits BOTH positive (long) and negative (short) target weights.
* A FULL backtest with `allow_short=True` actually opens short (negative)
  positions and computes round-trip P&L through the signed FIFO path.
* In a long-only portfolio (`allow_short=False`) the same strategy clamps the
  short leg to flat (no negative positions, no error).

Style mirrors TestCrossSectionalMomentum: deterministic seeded RNG, in-memory
YFinanceDataHandler subclass (no network).
"""

import numpy as np
import pandas as pd

from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import LongShortMomentum


def _handler(drifts, n=200):
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    rng = np.random.default_rng(7)
    data = {}
    for t, drift in drifts.items():
        rets = rng.normal(drift, 0.01, n)
        close = 100 * np.cumprod(1 + rets)
        open_ = np.concatenate([[100.0], close[:-1]])
        data[t] = pd.DataFrame(
            {
                "Open": open_,
                "High": np.maximum(close, open_) * 1.01,
                "Low": np.minimum(close, open_) * 0.99,
                "Close": close,
                "Volume": 1000,
            },
            index=idx,
        )

    class MemoryHandler(YFinanceDataHandler):
        def fetch(self):
            pass

    h = MemoryHandler(list(drifts), "2021-01-01", "2021-12-31")
    h._data = data
    h._total_bars = n
    return h


def _min_position(trades: pd.DataFrame, symbol: str) -> int:
    """Reconstruct the minimum net signed position for ``symbol`` from the trade log."""
    rows = trades[trades["symbol"] == symbol]
    pos = 0
    lo = 0
    for _, r in rows.iterrows():
        pos += r["quantity"] if r["direction"] == "BUY" else -r["quantity"]
        lo = min(lo, pos)
    return lo


class TestLongShortRanking:
    def test_emits_both_long_and_short_weights(self):
        # WIN strongest, LAG weakest, MID in the middle (flat at top_k=1).
        h = _handler({"WIN": 0.004, "MID": 0.0005, "LAG": -0.003})
        h.fetch()
        h._current_idx = 130
        strat = LongShortMomentum(["WIN", "MID", "LAG"], lookback=60, top_k=1, weight=0.5)
        targets = strat._rank(h)
        assert targets["WIN"] == 0.5  # long the winner
        assert targets["LAG"] == -0.5  # short the laggard
        assert targets["MID"] == 0.0  # neither leg
        # there is at least one positive AND one negative target
        assert any(w > 0 for w in targets.values())
        assert any(w < 0 for w in targets.values())

    def test_top_k_capped_to_avoid_overlap(self):
        # 3 tickers, requested top_k=2 -> capped to 1 (3 // 2) so legs don't overlap.
        strat = LongShortMomentum(["A", "B", "C"], lookback=60, top_k=2)
        assert strat.top_k == 1


class TestLongShortBacktest:
    def test_short_positions_open_with_allow_short(self):
        h = _handler({"WIN": 0.004, "LAG": -0.003})
        portfolio = Portfolio(initial_capital=100_000, allow_short=True)
        strat = LongShortMomentum(["WIN", "LAG"], lookback=60, top_k=1, rebalance_freq=21)
        Backtest(h, strat, portfolio, SimulatedExecution()).run()

        # The laggard should have been shorted at some point -> a negative
        # position must appear in the trade log (a SELL opening beyond flat).
        trades = pd.DataFrame(portfolio.trade_log)
        assert not trades.empty
        # LAG was sold to open a short
        assert (trades["symbol"] == "LAG").any()
        sells = trades[(trades["symbol"] == "LAG") & (trades["direction"] == "SELL")]
        assert not sells.empty
        # An actual negative (short) position existed during the run.
        assert _min_position(trades, "LAG") < 0

    def test_round_trip_pnl_computed(self):
        h = _handler({"WIN": 0.004, "LAG": -0.003})
        portfolio = Portfolio(initial_capital=100_000, allow_short=True)
        strat = LongShortMomentum(["WIN", "LAG"], lookback=60, top_k=1, rebalance_freq=21)
        analytics = Backtest(h, strat, portfolio, SimulatedExecution()).run()
        # The analytics report computes round-trip P&L without error.
        pnls = analytics._compute_round_trip_pnl()
        assert isinstance(pnls, list)
        # equity curve was produced
        assert not analytics.equity.empty

    def test_long_only_portfolio_clamps_short_leg(self):
        h = _handler({"WIN": 0.004, "LAG": -0.003})
        portfolio = Portfolio(initial_capital=100_000, allow_short=False)
        strat = LongShortMomentum(["WIN", "LAG"], lookback=60, top_k=1, rebalance_freq=21)
        Backtest(h, strat, portfolio, SimulatedExecution()).run()
        # No position ever went negative: the short target was clamped to flat.
        trades = pd.DataFrame(portfolio.trade_log)
        for sym in ("WIN", "LAG"):
            assert _min_position(trades, sym) >= 0 if not trades.empty else True
