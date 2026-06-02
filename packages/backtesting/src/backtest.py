from .analytics import PerformanceAnalytics
from .data_handler import DataHandler
from .datastore import DataStore
from .execution import ExecutionHandler
from .portfolio import Portfolio
from .strategy import Strategy


class Backtest:
    """Main backtest engine orchestrating the event loop."""

    def __init__(
        self,
        data_handler: DataHandler,
        strategy: Strategy,
        portfolio: Portfolio,
        execution: ExecutionHandler,
        strategy_name: str = "",
        store: DataStore | None = None,
        benchmark: str | None = None,
    ):
        self.data = data_handler
        self.strategy = strategy
        self.portfolio = portfolio
        self.execution = execution
        self.strategy_name = strategy_name
        self._store = store
        self.benchmark = benchmark

    def run(self) -> PerformanceAnalytics:
        """Execute the backtest and return performance analytics."""
        print("Fetching data...")
        self.data.fetch()

        print("Running backtest...")
        last_timestamp = None
        for market_event in self.data.iter_bars():
            # 1. Mark to market ONCE per bar. iter_bars yields one event per
            #    symbol per bar, so guard against recording the equity snapshot
            #    multiple times per timestamp (which would inject zero-returns
            #    and distort all downstream metrics).
            if market_event.timestamp != last_timestamp:
                self.portfolio.update_market(self.data, market_event.timestamp)
                last_timestamp = market_event.timestamp

                # 1b. Fill any resting LIMIT/STOP orders against this bar.
                for pending_fill in self.execution.check_pending(self.data, market_event.timestamp):
                    self.portfolio.process_fill(pending_fill)

                # 1c. Protective exits (stop-loss / take-profit / trailing) are
                #     checked once per bar, before strategy signals.
                for exit_order in self.portfolio.check_exits(self.data, market_event.timestamp):
                    exit_fill = self.execution.execute_order(exit_order, self.data)
                    if exit_fill is not None:
                        self.portfolio.process_fill(exit_fill)

            # 2. Strategy generates signal
            signal = self.strategy.calculate_signals(market_event, self.data)
            if signal is None:
                continue

            # 3. Portfolio converts signal to order
            order = self.portfolio.process_signal(signal, self.data)
            if order is None:
                continue

            # 4. Execution handler fills the order
            fill = self.execution.execute_order(order, self.data)
            if fill is None:
                continue

            # 5. Portfolio updates from fill
            self.portfolio.process_fill(fill)

        print("Backtest complete.")

        equity_df = self.portfolio.get_equity_df()
        trade_df = self.portfolio.get_trade_df()
        benchmark_returns = self._fetch_benchmark_returns()
        analytics = PerformanceAnalytics(
            equity_df,
            trade_df,
            benchmark_returns=benchmark_returns,
            allow_short=getattr(self.portfolio, "allow_short", False),
        )

        # Persist results to DuckDB
        if self._store and self.strategy_name:
            metrics = {
                "total_return": analytics.total_return(),
                "annualized_return": analytics.annualized_return(),
                "sharpe_ratio": analytics.sharpe_ratio(),
                "sortino_ratio": analytics.sortino_ratio(),
                "max_drawdown": analytics.max_drawdown(),
                "total_trades": len(trade_df),
                "beta": analytics.beta(),
                "alpha": analytics.alpha(),
            }
            run_id = self._store.save_backtest(
                strategy_name=self.strategy_name,
                tickers=self.data.tickers,
                start_date=self.data.start_date,
                end_date=self.data.end_date,
                initial_capital=self.portfolio.initial_capital,
                metrics=metrics,
                equity_df=equity_df,
                trade_df=trade_df,
            )
            print(f"Results saved to DuckDB (run_id={run_id})")

        return analytics

    def _fetch_benchmark_returns(self):
        """Daily returns of the buy-and-hold benchmark, or None if not requested."""
        if not self.benchmark:
            return None
        try:
            if self._store is not None:
                prices = self._store.fetch_ohlcv(
                    self.benchmark, self.data.start_date, self.data.end_date
                )["Close"]
            else:
                from .market_data import download_ohlcv

                df = download_ohlcv(
                    self.benchmark,
                    self.data.start_date,
                    self.data.end_date,
                )
                prices = df["Close"]
        except Exception:
            return None
        return prices.pct_change().dropna()
