from pathlib import Path

import duckdb
import pandas as pd

from .market_data import download_ohlcv


class DataStore:
    """DuckDB-backed local store for market data, trade logs, and backtest results.

    Caches yfinance downloads so subsequent runs are instant.
    Persists backtest results for cross-strategy comparison via SQL.
    """

    def __init__(self, db_path: str = "data/backtests.duckdb"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol     VARCHAR NOT NULL,
                date       DATE NOT NULL,
                open       DOUBLE,
                high       DOUBLE,
                low        DOUBLE,
                close      DOUBLE,
                volume     BIGINT,
                PRIMARY KEY (symbol, date)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id          INTEGER PRIMARY KEY,
                strategy_name   VARCHAR NOT NULL,
                tickers         VARCHAR NOT NULL,
                start_date      DATE NOT NULL,
                end_date        DATE NOT NULL,
                initial_capital DOUBLE NOT NULL,
                total_return    DOUBLE,
                annualized_return DOUBLE,
                sharpe_ratio    DOUBLE,
                sortino_ratio   DOUBLE,
                max_drawdown    DOUBLE,
                total_trades    INTEGER,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Benchmark-relative metrics (added later; migrate existing DBs in place).
        self.conn.execute("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS beta DOUBLE")
        self.conn.execute("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS alpha DOUBLE")
        self.conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS seq_run_id START 1
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                run_id     INTEGER NOT NULL,
                timestamp  TIMESTAMP NOT NULL,
                symbol     VARCHAR NOT NULL,
                direction  VARCHAR NOT NULL,
                quantity   INTEGER NOT NULL,
                price      DOUBLE NOT NULL,
                commission DOUBLE NOT NULL,
                slippage   DOUBLE NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_curve (
                run_id    INTEGER NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                equity    DOUBLE NOT NULL,
                cash      DOUBLE NOT NULL
            )
        """)

    # --- Market Data Cache ---

    def fetch_ohlcv(
        self, symbol: str, start_date: str, end_date: str, offline: bool = False
    ) -> pd.DataFrame:
        """Return OHLCV data, fetching from yfinance only if not cached.

        DuckDB is the primary cache; on a miss the resilient network layer
        (:func:`src.market_data.download_ohlcv`) fetches with retries/backoff
        or serves the bundled offline fixture (``offline`` arg or the
        ``BACKTESTING_OFFLINE`` env flag)."""
        cached = self.conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM ohlcv
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date
        """,
            [symbol, start_date, end_date],
        ).fetchdf()

        if len(cached) > 0:
            # Check if we have roughly the right amount of data
            cached["date"] = pd.to_datetime(cached["date"])
            cached = cached.set_index("date")
            cached.columns = ["Open", "High", "Low", "Close", "Volume"]
            return cached

        # Download (resilient network layer) and cache
        df = download_ohlcv(symbol, start_date, end_date, offline=offline)

        # Insert into DuckDB
        insert_df = df.reset_index().rename(columns={"Date": "date"})
        insert_df["symbol"] = symbol
        insert_df.columns = [c.lower() for c in insert_df.columns]

        self.conn.execute("""
            INSERT OR REPLACE INTO ohlcv (symbol, date, open, high, low, close, volume)
            SELECT symbol, date, open, high, low, close, volume
            FROM insert_df
        """)

        return df

    def has_cached_data(self, symbol: str, start_date: str, end_date: str) -> bool:
        """Check if OHLCV data is already cached."""
        row = self.conn.execute(
            """
            SELECT COUNT(*) FROM ohlcv
            WHERE symbol = ? AND date >= ? AND date <= ?
        """,
            [symbol, start_date, end_date],
        ).fetchone()
        assert row is not None  # COUNT(*) always returns exactly one row
        return bool(row[0] > 0)

    # --- Backtest Results ---

    def save_backtest(
        self,
        strategy_name: str,
        tickers: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float,
        metrics: dict,
        equity_df: pd.DataFrame,
        trade_df: pd.DataFrame,
    ) -> int:
        """Persist a backtest run and return the run_id."""
        row = self.conn.execute("SELECT nextval('seq_run_id')").fetchone()
        assert row is not None  # nextval always returns exactly one row
        run_id = row[0]

        self.conn.execute(
            """
            INSERT INTO backtest_runs
                (run_id, strategy_name, tickers, start_date, end_date,
                 initial_capital, total_return, annualized_return,
                 sharpe_ratio, sortino_ratio, max_drawdown, total_trades,
                 beta, alpha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            [
                run_id,
                strategy_name,
                ",".join(tickers),
                start_date,
                end_date,
                initial_capital,
                metrics.get("total_return"),
                metrics.get("annualized_return"),
                metrics.get("sharpe_ratio"),
                metrics.get("sortino_ratio"),
                metrics.get("max_drawdown"),
                metrics.get("total_trades"),
                metrics.get("beta"),
                metrics.get("alpha"),
            ],
        )

        # Save equity curve
        if not equity_df.empty:
            eq = equity_df.reset_index()
            eq["run_id"] = run_id
            self.conn.execute("""
                INSERT INTO equity_curve (run_id, timestamp, equity, cash)
                SELECT run_id, timestamp, equity, cash FROM eq
            """)

        # Save trades
        if not trade_df.empty:
            tr = trade_df.copy()
            tr["run_id"] = run_id
            self.conn.execute("""
                INSERT INTO trades (run_id, timestamp, symbol, direction, quantity, price, commission, slippage)
                SELECT run_id, timestamp, symbol, direction, quantity, price, commission, slippage FROM tr
            """)

        return run_id

    # --- Query Interface ---

    def compare_strategies(self) -> pd.DataFrame:
        """Compare all backtest runs side-by-side."""
        return self.conn.execute("""
            SELECT
                run_id,
                strategy_name,
                tickers,
                start_date,
                end_date,
                ROUND(total_return * 100, 2)       AS "return_%",
                ROUND(annualized_return * 100, 2)   AS "ann_return_%",
                ROUND(sharpe_ratio, 2)               AS sharpe,
                ROUND(sortino_ratio, 2)              AS sortino,
                ROUND(max_drawdown * 100, 2)         AS "max_dd_%",
                ROUND(beta, 2)                       AS beta,
                ROUND(alpha * 100, 2)                AS "alpha_%",
                total_trades,
                created_at
            FROM backtest_runs
            ORDER BY sharpe_ratio DESC
        """).fetchdf()

    def get_trades(self, run_id: int) -> pd.DataFrame:
        """Get all trades for a specific backtest run."""
        return self.conn.execute(
            """
            SELECT timestamp, symbol, direction, quantity, price, commission, slippage
            FROM trades
            WHERE run_id = ?
            ORDER BY timestamp
        """,
            [run_id],
        ).fetchdf()

    def get_equity_curve(self, run_id: int) -> pd.DataFrame:
        """Get equity curve for a specific backtest run."""
        return self.conn.execute(
            """
            SELECT timestamp, equity, cash
            FROM equity_curve
            WHERE run_id = ?
            ORDER BY timestamp
        """,
            [run_id],
        ).fetchdf()

    def query(self, sql: str) -> pd.DataFrame:
        """Execute arbitrary SQL against the backtest database."""
        return self.conn.execute(sql).fetchdf()

    def export_parquet(self, table: str, path: str) -> None:
        """Export a table to Parquet format."""
        self.conn.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")

    def close(self) -> None:
        self.conn.close()
