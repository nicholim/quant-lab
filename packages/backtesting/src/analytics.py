import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from portfolio_optimization_engine.metrics import (
    compute_metrics,
    probabilistic_sharpe_ratio,
)
from tabulate import tabulate

# Trading days per year — the annualization convention used across the package.
PERIODS_PER_YEAR = 252


class PerformanceAnalytics:
    """Compute and display portfolio performance metrics.

    Risk/return ratios (Sharpe, Sortino, Calmar, drawdown, annualized return and
    volatility) are delegated to portfolio_optimization_engine.metrics so this
    backtester and the optimization engine report the SAME numbers for the same
    return series. Trade-level analytics (round-trip P&L, win rate, profit
    factor) and plotting are computed locally.
    """

    def __init__(
        self,
        equity_df: pd.DataFrame,
        trade_df: pd.DataFrame,
        risk_free_rate: float = 0.02,
        benchmark_returns: pd.Series | None = None,
        allow_short: bool = False,
    ):
        self.equity = equity_df
        self.trades = trade_df
        self.risk_free_rate = risk_free_rate
        # When True, round-trip P&L uses signed FIFO so short round-trips
        # (sell-to-open -> buy-to-cover) are matched. Default False preserves the
        # long-only matching exactly (a SELL with no open long is dropped).
        self.allow_short = allow_short
        self.returns = self.equity["equity"].pct_change().dropna()
        self._pm = (
            compute_metrics(
                self.returns,
                benchmark=benchmark_returns,
                risk_free_rate=risk_free_rate,
            )
            if len(self.returns) > 0
            else None
        )

    def beta(self) -> float | None:
        """Beta vs the benchmark (None if no benchmark was provided)."""
        return self._pm.beta if self._pm else None

    def alpha(self) -> float | None:
        """Annualized Jensen's alpha vs the benchmark (None if no benchmark)."""
        return self._pm.alpha if self._pm else None

    def total_return(self) -> float:
        return (self.equity["equity"].iloc[-1] / self.equity["equity"].iloc[0]) - 1

    def annualized_return(self) -> float:
        """Geometric annualized return (CAGR), from the shared metrics module."""
        return self._pm.cagr if self._pm else 0.0

    def annualized_volatility(self) -> float:
        return self._pm.annualized_volatility if self._pm else 0.0

    def sharpe_ratio(self) -> float:
        return self._pm.sharpe_ratio if self._pm else 0.0

    def sortino_ratio(self) -> float:
        return self._pm.sortino_ratio if self._pm else 0.0

    def max_drawdown(self) -> float:
        return self._pm.max_drawdown if self._pm else 0.0

    def max_drawdown_duration(self) -> int:
        """Max drawdown duration in trading days."""
        cumulative = (1 + self.returns).cumprod()
        rolling_max = cumulative.cummax()
        underwater = cumulative < rolling_max

        max_dur = 0
        current_dur = 0
        for is_dd in underwater:
            if is_dd:
                current_dur += 1
                max_dur = max(max_dur, current_dur)
            else:
                current_dur = 0
        return max_dur

    def calmar_ratio(self) -> float:
        return self._pm.calmar_ratio if self._pm else 0.0

    def probabilistic_sharpe_ratio(self, benchmark_sr: float = 0.0) -> float:
        """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado).

        Probability that the strategy's TRUE (per-period) Sharpe exceeds
        ``benchmark_sr`` (default 0, i.e. that the Sharpe is positive), accounting
        for the length of the return series and its skew/kurtosis. The annualized
        Sharpe reported elsewhere is de-annualized here so the closed form sees a
        per-observation SR consistent with ``n``. Returns 0.0 with < 2 returns.
        """
        if self._pm is None or len(self.returns) < 2:
            return 0.0
        n = len(self.returns)
        # De-annualize: sharpe_ratio() is annualized by sqrt(PERIODS_PER_YEAR).
        per_period_sr = self.sharpe_ratio() / np.sqrt(PERIODS_PER_YEAR)
        skew = float(pd.Series(self.returns).skew())
        # pandas kurt() is EXCESS kurtosis; the closed form wants non-excess.
        kurt = float(pd.Series(self.returns).kurt()) + 3.0
        if np.isnan(skew):
            skew = 0.0
        if np.isnan(kurt):
            kurt = 3.0
        return probabilistic_sharpe_ratio(per_period_sr, benchmark_sr, n, skew, kurt)

    def _compute_round_trip_pnl(self) -> list[float]:
        """Match trades per symbol into round-trip P&L via FIFO lot accounting.

        Long-only (``allow_short`` False, the default): identical to the original
        behavior — BUYs open lots, SELLs close them FIFO with
        ``(sell - entry) * qty``, and a SELL with no open long is dropped.

        With ``allow_short`` True the FIFO is fully signed: a trade in the same
        direction as the open side (or on an empty book) opens a lot; an opposite
        trade closes lots FIFO and, after the book empties, the remainder opens a
        new lot on the other side. Closing a LONG lot earns ``(exit - entry)*qty``;
        closing a SHORT lot earns ``(entry - cover)*qty`` (sold high, covered low
        is a profit).
        """
        if self.trades.empty:
            return []
        if not self.allow_short:
            return self._round_trip_pnl_long_only()
        return self._round_trip_pnl_signed()

    def _round_trip_pnl_long_only(self) -> list[float]:
        pnl_list: list[float] = []
        open_positions: dict[str, list[dict]] = {}

        for _, trade in self.trades.iterrows():
            sym = trade["symbol"]
            if sym not in open_positions:
                open_positions[sym] = []

            if trade["direction"] == "BUY":
                open_positions[sym].append({"qty": trade["quantity"], "price": trade["price"]})
            elif trade["direction"] == "SELL" and open_positions[sym]:
                remaining = trade["quantity"]
                sell_price = trade["price"]
                while remaining > 0 and open_positions[sym]:
                    entry = open_positions[sym][0]
                    matched = min(remaining, entry["qty"])
                    pnl_list.append((sell_price - entry["price"]) * matched)
                    entry["qty"] -= matched
                    remaining -= matched
                    if entry["qty"] <= 0:
                        open_positions[sym].pop(0)
        return pnl_list

    def _round_trip_pnl_signed(self) -> list[float]:
        """Signed FIFO: handles both long (buy-then-sell) and short
        (sell-then-cover) round trips, including flips through flat."""
        pnl_list: list[float] = []
        # Per symbol: FIFO list of open lots {qty>0, price, side=+1 long / -1 short}.
        books: dict[str, list[dict]] = {}

        for _, trade in self.trades.iterrows():
            sym = trade["symbol"]
            book = books.setdefault(sym, [])
            trade_side = 1 if trade["direction"] == "BUY" else -1
            remaining = trade["quantity"]
            price = trade["price"]

            # Close lots whose side is opposite this trade, FIFO.
            while remaining > 0 and book and book[0]["side"] == -trade_side:
                lot = book[0]
                matched = min(remaining, lot["qty"])
                if lot["side"] == 1:  # closing a long with a SELL
                    pnl_list.append((price - lot["price"]) * matched)
                else:  # closing a short with a BUY
                    pnl_list.append((lot["price"] - price) * matched)
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 0:
                    book.pop(0)

            # Any remainder opens/extends a lot on this trade's side.
            if remaining > 0:
                book.append({"qty": remaining, "price": price, "side": trade_side})
        return pnl_list

    def win_rate(self) -> float:
        """Fraction of round-trip trades that were profitable."""
        pnl = self._compute_round_trip_pnl()
        if not pnl:
            return 0.0
        wins = sum(1 for p in pnl if p > 0)
        return wins / len(pnl)

    def profit_factor(self) -> float:
        """Gross profit / gross loss from round-trip trades."""
        pnl = self._compute_round_trip_pnl()
        if not pnl:
            return 0.0
        gross_profit = sum(p for p in pnl if p > 0)
        gross_loss = abs(sum(p for p in pnl if p < 0))
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    def _round_trips(self) -> list[dict]:
        """Round trips as records (not just P&L) for trade-level analytics.

        Mirrors the FIFO matching of :meth:`_compute_round_trip_pnl` but records,
        per closed leg, its signed P&L, entry/exit timestamps, entry/exit prices,
        symbol, side (+1 long / -1 short), and matched quantity. Used for holding
        period and MAE/MFE; the hot event loop is untouched (this is post-hoc).
        """
        if self.trades.empty:
            return []
        trips: list[dict] = []
        books: dict[str, list[dict]] = {}
        for _, trade in self.trades.iterrows():
            sym = trade["symbol"]
            book = books.setdefault(sym, [])
            trade_side = 1 if trade["direction"] == "BUY" else -1
            remaining = trade["quantity"]
            price = trade["price"]
            ts = trade.get("timestamp")

            if not self.allow_short:
                # Long-only: only SELLs close, only against open BUYs.
                if trade_side == 1:
                    book.append({"qty": remaining, "price": price, "ts": ts, "side": 1})
                    continue
                while remaining > 0 and book:
                    lot = book[0]
                    matched = min(remaining, lot["qty"])
                    trips.append(
                        {
                            "symbol": sym,
                            "side": 1,
                            "qty": matched,
                            "entry_price": lot["price"],
                            "exit_price": price,
                            "entry_ts": lot["ts"],
                            "exit_ts": ts,
                            "pnl": (price - lot["price"]) * matched,
                        }
                    )
                    lot["qty"] -= matched
                    remaining -= matched
                    if lot["qty"] <= 0:
                        book.pop(0)
                continue

            # Signed FIFO.
            while remaining > 0 and book and book[0]["side"] == -trade_side:
                lot = book[0]
                matched = min(remaining, lot["qty"])
                if lot["side"] == 1:
                    pnl = (price - lot["price"]) * matched
                else:
                    pnl = (lot["price"] - price) * matched
                trips.append(
                    {
                        "symbol": sym,
                        "side": lot["side"],
                        "qty": matched,
                        "entry_price": lot["price"],
                        "exit_price": price,
                        "entry_ts": lot["ts"],
                        "exit_ts": ts,
                        "pnl": pnl,
                    }
                )
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 0:
                    book.pop(0)
            if remaining > 0:
                book.append({"qty": remaining, "price": price, "ts": ts, "side": trade_side})
        return trips

    def avg_win(self) -> float:
        """Average P&L of winning round trips (0.0 if none)."""
        wins = [p for p in self._compute_round_trip_pnl() if p > 0]
        return float(np.mean(wins)) if wins else 0.0

    def avg_loss(self) -> float:
        """Average P&L of losing round trips, as a negative number (0.0 if none)."""
        losses = [p for p in self._compute_round_trip_pnl() if p < 0]
        return float(np.mean(losses)) if losses else 0.0

    def expectancy(self) -> float:
        """Expected P&L per round trip = mean of all round-trip P&Ls (0.0 if none)."""
        pnl = self._compute_round_trip_pnl()
        return float(np.mean(pnl)) if pnl else 0.0

    def payoff_ratio(self) -> float:
        """Avg win / |avg loss|. inf if there are wins but no losses; 0.0 if none."""
        aw, al = self.avg_win(), self.avg_loss()
        if al == 0.0:
            return float("inf") if aw > 0 else 0.0
        return aw / abs(al)

    def avg_holding_period(self) -> float:
        """Mean round-trip holding period in days (0.0 if unavailable)."""
        trips = self._round_trips()
        durations = []
        for t in trips:
            entry_ts, exit_ts = t.get("entry_ts"), t.get("exit_ts")
            if entry_ts is None or exit_ts is None:
                continue
            try:
                delta = pd.Timestamp(exit_ts) - pd.Timestamp(entry_ts)
            except (TypeError, ValueError):
                continue
            durations.append(delta.total_seconds() / 86400.0)
        return float(np.mean(durations)) if durations else 0.0

    def exposure_time(self) -> float:
        """Fraction of equity-curve bars during which a position was held.

        Derived from the equity DataFrame post-hoc: a bar counts as "in market"
        when the bar return is non-zero OR the equity differs from the prior bar.
        Falls back to 0.0 when there is no usable equity series.
        """
        if "equity" not in self.equity or len(self.equity) < 2:
            return 0.0
        eq = self.equity["equity"].to_numpy(dtype=float)
        # A bar is "in market" if the portfolio value moved relative to the prior
        # bar (cash-only periods have a flat curve, modulo the risk-free drift we
        # do not model here). This is a path-derived proxy, computed off-loop.
        moved = np.abs(np.diff(eq)) > 1e-9
        return float(np.mean(moved))

    def mae_mfe(self) -> pd.DataFrame:
        """Per-round-trip Max Adverse / Max Favorable Excursion.

        For each closed round trip, scan the equity-curve price path between entry
        and exit timestamps and report the worst (MAE) and best (MFE) excursion of
        the position's mark-to-market relative to entry, expressed in P&L units
        (signed by side). Computed post-hoc from the existing path — never in the
        event loop. Returns an empty frame when no usable timestamps/path exist.
        """
        trips = self._round_trips()
        if not trips or "equity" not in self.equity:
            return pd.DataFrame(columns=["symbol", "side", "pnl", "mae", "mfe"])

        # Price path proxy: prefer a per-symbol close if present, else the equity
        # curve normalized. We use the equity index timestamps to slice windows.
        idx = self.equity.index
        rows = []
        for t in trips:
            entry_ts, exit_ts = t.get("entry_ts"), t.get("exit_ts")
            mae = mfe = 0.0
            if entry_ts is not None and exit_ts is not None:
                try:
                    window = self.equity.loc[
                        (idx >= pd.Timestamp(entry_ts)) & (idx <= pd.Timestamp(exit_ts))
                    ]
                except (TypeError, ValueError):
                    window = self.equity.iloc[0:0]
                if not window.empty:
                    price_col = "close" if "close" in window else "equity"
                    path = window[price_col].to_numpy(dtype=float)
                    # Excursion vs entry price, signed by side, scaled by qty.
                    excursion = (path - t["entry_price"]) * t["side"] * t["qty"]
                    mfe = float(np.max(excursion))
                    mae = float(np.min(excursion))
            rows.append(
                {
                    "symbol": t["symbol"],
                    "side": t["side"],
                    "pnl": t["pnl"],
                    "mae": mae,
                    "mfe": mfe,
                }
            )
        return pd.DataFrame(rows)

    def trade_stats(self) -> dict[str, float]:
        """All trade-level statistics in one dict (for reports/UIs)."""
        return {
            "win_rate": self.win_rate(),
            "profit_factor": self.profit_factor(),
            "avg_win": self.avg_win(),
            "avg_loss": self.avg_loss(),
            "expectancy": self.expectancy(),
            "payoff_ratio": self.payoff_ratio(),
            "avg_holding_period": self.avg_holding_period(),
            "exposure_time": self.exposure_time(),
        }

    def generate_report(self) -> None:
        """Print formatted performance summary."""
        metrics = [
            ["Total Return", f"{self.total_return():.2%}"],
            ["Annualized Return", f"{self.annualized_return():.2%}"],
            ["Annualized Volatility", f"{self.annualized_volatility():.2%}"],
            ["Sharpe Ratio", f"{self.sharpe_ratio():.2f}"],
            ["Prob. Sharpe (PSR)", f"{self.probabilistic_sharpe_ratio():.2%}"],
            ["Sortino Ratio", f"{self.sortino_ratio():.2f}"],
            ["Max Drawdown", f"{self.max_drawdown():.2%}"],
            ["Max DD Duration", f"{self.max_drawdown_duration()} days"],
            ["Calmar Ratio", f"{self.calmar_ratio():.2f}"],
            ["Total Trades", f"{len(self.trades)}"],
        ]
        if self.beta() is not None:
            metrics.append(["Beta", f"{self.beta():.2f}"])
            metrics.append(["Alpha (ann.)", f"{self.alpha():.2%}"])
        print("\n" + "=" * 50)
        print("PERFORMANCE REPORT")
        print("=" * 50)
        print(tabulate(metrics, headers=["Metric", "Value"], tablefmt="simple"))
        print("=" * 50)

        stats = self.trade_stats()
        trade_rows = [
            ["Win Rate", f"{stats['win_rate']:.2%}"],
            ["Profit Factor", f"{stats['profit_factor']:.2f}"],
            ["Avg Win", f"{stats['avg_win']:.2f}"],
            ["Avg Loss", f"{stats['avg_loss']:.2f}"],
            ["Expectancy", f"{stats['expectancy']:.2f}"],
            ["Payoff Ratio", f"{stats['payoff_ratio']:.2f}"],
            ["Avg Holding (days)", f"{stats['avg_holding_period']:.1f}"],
            ["Exposure Time", f"{stats['exposure_time']:.2%}"],
        ]
        print("\nTRADE STATISTICS")
        print("-" * 50)
        print(tabulate(trade_rows, headers=["Metric", "Value"], tablefmt="simple"))
        print("=" * 50)

    def plot_equity_curve(self, save_path: str | None = None) -> None:
        fig, ax = plt.subplots(figsize=(14, 6))
        ax.plot(self.equity.index, self.equity["equity"], linewidth=1.5, color="steelblue")
        ax.set_title("Equity Curve", fontsize=14)
        ax.set_xlabel("Date")
        ax.set_ylabel("Portfolio Value ($)")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()

    def plot_drawdown(self, save_path: str | None = None) -> None:
        cumulative = (1 + self.returns).cumprod()
        rolling_max = cumulative.cummax()
        drawdown = (cumulative - rolling_max) / rolling_max

        fig, ax = plt.subplots(figsize=(14, 4))
        ax.fill_between(drawdown.index, drawdown.values, 0, color="red", alpha=0.3)
        ax.plot(drawdown.index, drawdown.values, color="red", linewidth=0.8)
        ax.set_title("Drawdown", fontsize=14)
        ax.set_xlabel("Date")
        ax.set_ylabel("Drawdown")
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()

    def plot_monthly_heatmap(self, save_path: str | None = None) -> None:
        monthly = self.returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)
        monthly_df = pd.DataFrame(
            {
                "year": monthly.index.year,
                "month": monthly.index.month,
                "return": monthly.values,
            }
        )
        pivot = monthly_df.pivot_table(values="return", index="year", columns="month")
        pivot.columns = [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ][: len(pivot.columns)]

        fig, ax = plt.subplots(figsize=(14, 6))
        sns.heatmap(pivot, annot=True, fmt=".1%", cmap="RdYlGn", center=0, ax=ax, linewidths=0.5)
        ax.set_title("Monthly Returns Heatmap", fontsize=14)
        plt.tight_layout()
        if save_path:
            plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.show()
