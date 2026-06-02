import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from portfolio_optimization_engine.metrics import compute_metrics
from tabulate import tabulate


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

    def generate_report(self) -> None:
        """Print formatted performance summary."""
        metrics = [
            ["Total Return", f"{self.total_return():.2%}"],
            ["Annualized Return", f"{self.annualized_return():.2%}"],
            ["Annualized Volatility", f"{self.annualized_volatility():.2%}"],
            ["Sharpe Ratio", f"{self.sharpe_ratio():.2f}"],
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
