from abc import ABC, abstractmethod

import numpy as np
import pandas as pd
from portfolio_optimization_engine.optimizer import PortfolioOptimizer

from .data_handler import DataHandler
from .events import Direction, MarketEvent, SignalEvent


class Strategy(ABC):
    """Abstract base class for trading strategies."""

    @abstractmethod
    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None: ...


class MovingAverageCrossover(Strategy):
    """Simple moving average crossover strategy."""

    def __init__(self, short_window: int = 20, long_window: int = 50):
        self.short_window = short_window
        self.long_window = long_window
        self._prev_signal: dict[str, Direction] = {}

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        bars = data.get_latest_bars(event.symbol, self.long_window + 1)
        if len(bars) < self.long_window:
            return None

        short_ma = bars["Close"].rolling(self.short_window).mean().iloc[-1]
        long_ma = bars["Close"].rolling(self.long_window).mean().iloc[-1]

        prev = self._prev_signal.get(event.symbol, Direction.HOLD)

        if short_ma > long_ma and prev != Direction.BUY:
            self._prev_signal[event.symbol] = Direction.BUY
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                strength=1.0,
            )
        elif short_ma < long_ma and prev != Direction.SELL:
            self._prev_signal[event.symbol] = Direction.SELL
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.SELL,
                strength=1.0,
            )
        return None


class MeanReversion(Strategy):
    """Z-score based mean reversion strategy."""

    def __init__(self, lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5):
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self._position: dict[str, Direction] = {}

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        bars = data.get_latest_bars(event.symbol, self.lookback + 1)
        if len(bars) < self.lookback:
            return None

        prices = bars["Close"]
        mean = prices.rolling(self.lookback).mean().iloc[-1]
        std = prices.rolling(self.lookback).std().iloc[-1]
        if std < 1e-8:
            return None

        z = (prices.iloc[-1] - mean) / std
        pos = self._position.get(event.symbol, Direction.HOLD)

        if z < -self.entry_z and pos != Direction.BUY:
            self._position[event.symbol] = Direction.BUY
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                strength=min(abs(z) / self.entry_z, 2.0),
            )
        elif z > self.entry_z and pos != Direction.SELL:
            self._position[event.symbol] = Direction.SELL
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.SELL,
                strength=min(abs(z) / self.entry_z, 2.0),
            )
        elif abs(z) < self.exit_z and pos != Direction.HOLD:
            self._position[event.symbol] = Direction.HOLD
            direction = Direction.SELL if pos == Direction.BUY else Direction.BUY
            return SignalEvent(
                timestamp=event.timestamp, symbol=event.symbol, direction=direction, strength=1.0
            )
        return None


class MomentumStrategy(Strategy):
    """Cross-sectional momentum: buy top performers, sell laggards."""

    def __init__(self, lookback: int = 60):
        self.lookback = lookback
        self._last_signal: dict[str, Direction] = {}

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        bars = data.get_latest_bars(event.symbol, self.lookback + 1)
        if len(bars) < self.lookback:
            return None

        returns = bars["Close"].pct_change(self.lookback).iloc[-1]
        if np.isnan(returns):
            return None

        if returns > 0.05 and self._last_signal.get(event.symbol) != Direction.BUY:
            self._last_signal[event.symbol] = Direction.BUY
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                strength=min(returns * 10, 2.0),
            )
        elif returns < -0.05 and self._last_signal.get(event.symbol) != Direction.SELL:
            self._last_signal[event.symbol] = Direction.SELL
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.SELL,
                strength=1.0,
            )
        return None


class OptimizationRebalanceStrategy(Strategy):
    """Walk-forward MPT rebalancing driven by the portfolio-optimization-engine.

    Every ``rebalance_freq`` bars, re-optimize target weights on the trailing
    ``lookback`` window of returns (using only data available up to the current
    bar — no look-ahead) and emit target-weight signals for every ticker. The
    engine is called long-only, so emitted weights are always executable by the
    backtester. This produces an out-of-sample, cost-aware equity curve of the
    optimized portfolio.

    objective: one of "sharpe" (default), "min_vol", "min_cvar", "risk_parity",
    "sortino", "hrp", "max_return_target_vol", "min_vol_target_return". The last
    two are constrained objectives that need a target: pass ``target`` as the
    annual volatility cap (for "max_return_target_vol") or the minimum annual
    return (for "min_vol_target_return"). ``target`` is ignored by the other
    objectives. An unknown objective falls back to "sharpe".

    "hrp" (Hierarchical Risk Parity, López de Prado 2016) is solver-free and
    derives long-only, fully-invested weights directly from the trailing
    covariance structure via the optimizer's zero-arg ``optimize_hrp()`` (uses
    the injected ``cov_matrix``), so it is robust when the covariance is
    ill-conditioned. Black-Litterman is deliberately NOT exposed here: it
    requires investor views (``P``/``Q`` matrices) to differ from the
    equilibrium prior, and this walk-forward strategy has no view-generation
    mechanism — with no views BL collapses to the prior, so it would add a heavy
    dependency for no behavioral difference.
    """

    _TARGET_OBJECTIVES = ("max_return_target_vol", "min_vol_target_return")

    def __init__(
        self,
        tickers: list[str],
        lookback: int = 252,
        rebalance_freq: int = 21,
        objective: str = "sharpe",
        risk_free_rate: float = 0.02,
        target: float | None = None,
    ):
        self.tickers = list(tickers)
        self.lookback = lookback
        self.rebalance_freq = rebalance_freq
        self.objective = objective
        self.risk_free_rate = risk_free_rate
        if objective in self._TARGET_OBJECTIVES and target is None:
            raise ValueError(
                f"objective {objective!r} requires a `target` (annual volatility cap "
                "for max_return_target_vol, minimum annual return for "
                "min_vol_target_return)"
            )
        self.target = target
        self._bar = 0
        self._last_rebalance = -(10**9)
        self._rebalance_now = False
        self._targets: dict[str, float] | None = None

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        # The first ticker's event marks the start of a new bar.
        if event.symbol == self.tickers[0]:
            self._bar += 1
            self._rebalance_now = (
                self._bar >= self.lookback + 1
                and (self._bar - self._last_rebalance) >= self.rebalance_freq
            )
            if self._rebalance_now:
                targets = self._compute_targets(data)
                if targets is not None:
                    self._targets = targets
                    self._last_rebalance = self._bar
                else:
                    self._rebalance_now = False

        if self._rebalance_now and self._targets and event.symbol in self._targets:
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                target_weight=self._targets[event.symbol],
            )
        return None

    def _compute_targets(self, data: DataHandler) -> dict[str, float] | None:
        """Optimize long-only target weights on the trailing window. None on failure."""
        closes = {}
        for ticker in self.tickers:
            bars = data.get_latest_bars(ticker, self.lookback + 1)
            if len(bars) < self.lookback:
                return None
            closes[ticker] = bars["Close"]

        prices = pd.DataFrame(closes)[self.tickers].dropna()
        returns = prices.pct_change().dropna()
        if len(returns) < 2:
            return None

        # Inject the trailing returns directly (no network fetch, no look-ahead).
        opt = PortfolioOptimizer(
            self.tickers, "1900-01-01", "1900-01-02", risk_free_rate=self.risk_free_rate
        )
        opt.returns = returns
        opt.mean_returns = returns.mean() * 252
        opt.cov_matrix = returns.cov() * 252

        try:
            if self.objective == "min_cvar":
                result = opt.optimize_min_cvar()
            elif self.objective == "risk_parity":
                result = opt.optimize_risk_parity()
            elif self.objective == "min_vol":
                result = opt.optimize_min_volatility()
            elif self.objective == "sortino":
                result = opt.optimize_sortino()
            elif self.objective == "hrp":
                result = opt.optimize_hrp()
            elif self.objective == "max_return_target_vol":
                result = opt.optimize_max_return_target_vol(float(self.target))  # type: ignore[arg-type]
            elif self.objective == "min_vol_target_return":
                # Clamp the requested return to what is achievable long-only on
                # this window so a too-high target degrades to the max-return
                # corner instead of raising and skipping the rebalance.
                max_achievable = float(opt.mean_returns.max())
                target = min(float(self.target), max_achievable)  # type: ignore[arg-type]
                result = opt.optimize_min_vol_target_return(target)
            else:
                result = opt.optimize_sharpe()
        except Exception:
            return None

        return {t: float(w) for t, w in zip(self.tickers, result.weights, strict=False)}


class TrendFilteredMA(Strategy):
    """Daily SMA crossover, gated by a higher-timeframe (weekly) trend filter.

    Demonstrates multi-timeframe data: entries require both a daily fast/slow
    SMA cross-up AND the weekly close above its weekly SMA. Exits on a daily
    cross-down or when the weekly trend turns down.
    """

    def __init__(self, short_window: int = 20, long_window: int = 50, weekly_trend: int = 10):
        self.short_window = short_window
        self.long_window = long_window
        self.weekly_trend = weekly_trend
        self._prev_signal: dict[str, Direction] = {}

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        bars = data.get_latest_bars(event.symbol, self.long_window + 1)
        if len(bars) < self.long_window:
            return None
        short_ma = bars["Close"].rolling(self.short_window).mean().iloc[-1]
        long_ma = bars["Close"].rolling(self.long_window).mean().iloc[-1]

        # Higher-timeframe (weekly) trend filter.
        weekly = data.get_resampled_bars(event.symbol, "W", self.weekly_trend + 1)
        if len(weekly) < self.weekly_trend:
            trend_up = True  # not enough weekly history yet -> don't block
        else:
            weekly_ma = weekly["Close"].rolling(self.weekly_trend).mean().iloc[-1]
            trend_up = weekly["Close"].iloc[-1] > weekly_ma

        prev = self._prev_signal.get(event.symbol, Direction.HOLD)
        if short_ma > long_ma and trend_up and prev != Direction.BUY:
            self._prev_signal[event.symbol] = Direction.BUY
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                strength=1.0,
            )
        if (short_ma < long_ma or not trend_up) and prev == Direction.BUY:
            self._prev_signal[event.symbol] = Direction.SELL
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.SELL,
                strength=1.0,
            )
        return None


class CrossSectionalMomentum(Strategy):
    """True cross-sectional momentum: rank ALL symbols at each rebalance and hold
    the top-K equal-weighted, exiting the rest.

    Unlike ``MomentumStrategy`` (per-symbol absolute thresholds), this ranks
    symbols against each other at a single timestamp — "buy the winners, sell the
    laggards." Coordination across symbols uses the first-ticker-of-bar pattern;
    target weights are emitted so the portfolio holds an equal-weight winner
    basket (long-only).
    """

    def __init__(
        self,
        tickers: list[str],
        lookback: int = 60,
        top_k: int = 1,
        rebalance_freq: int = 21,
    ):
        self.tickers = list(tickers)
        self.lookback = lookback
        self.top_k = max(1, min(top_k, len(self.tickers)))
        self.rebalance_freq = rebalance_freq
        self._bar = 0
        self._last_rebalance = -(10**9)
        self._rebalance_now = False
        self._targets: dict[str, float] | None = None

    def calculate_signals(self, event: MarketEvent, data: DataHandler) -> SignalEvent | None:
        if event.symbol == self.tickers[0]:
            self._bar += 1
            self._rebalance_now = (
                self._bar >= self.lookback + 1
                and (self._bar - self._last_rebalance) >= self.rebalance_freq
            )
            if self._rebalance_now:
                targets = self._rank(data)
                if targets is not None:
                    self._targets = targets
                    self._last_rebalance = self._bar
                else:
                    self._rebalance_now = False

        if self._rebalance_now and self._targets and event.symbol in self._targets:
            return SignalEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                direction=Direction.BUY,
                target_weight=self._targets[event.symbol],
            )
        return None

    def _rank(self, data: DataHandler) -> dict[str, float] | None:
        """Equal-weight target for the top-K trailing performers; 0 for the rest."""
        returns = {}
        for ticker in self.tickers:
            bars = data.get_latest_bars(ticker, self.lookback + 1)
            if len(bars) < self.lookback:
                return None
            r = bars["Close"].pct_change(self.lookback).iloc[-1]
            if pd.isna(r):
                return None
            returns[ticker] = r

        winners = sorted(returns, key=lambda t: returns[t], reverse=True)[: self.top_k]
        weight = 1.0 / len(winners)
        return {t: (weight if t in winners else 0.0) for t in self.tickers}
