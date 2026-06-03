"""Streamlit live-monitor for the market-data ingestion daemon.

The daemon (``main.py``) is headless: it streams trades off an exchange and
persists normalized trades + 1-minute OHLCV bars through a ``StorageBackend``
(DuckDB by default). This module is a *read-only* companion that makes that
pipeline visible -- a quant can SEE the most recent trades, the rolled-up OHLCV
bars, and a price/volume chart for a chosen symbol, turning the daemon into
something understandable and demoable.

Design constraints (deliberate):

* It is a **reader of the store**, never an ingester. It reuses the existing
  read API only -- it builds the same ``StorageBackend`` the daemon uses (via
  ``build_storage``) and pulls history through ``Pipeline.replay()``. It opens
  no WebSocket and changes no pipeline / protocol / adapter code.
* It works fully **offline / with an empty store**. On a fresh clone the DuckDB
  file is usually missing or empty, so the monitor synthesizes a deterministic
  sample (seeded) and shows a clear banner that it is sample data, not live
  store data. The UI therefore always renders something.
* No heavy new dependency: a manual "Refresh" button (Streamlit reruns the
  script) drives updates. ``plotly`` is used for the price/volume chart.

Run it with::

    cd packages/market-data && streamlit run monitor.py

Coverage note: like ``main.py``, this file lives at the package root (outside
``[tool.coverage.run] source = ["src"]``), so the AppTest smoke test exercises
it without diluting the ``src`` coverage gate.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.config import Config
from src.pipeline import Pipeline

# Column order matches the normalized schema the StorageBackend round-trips.
_TRADE_COLUMNS = ["time", "symbol", "price", "quantity", "side", "exchange"]
_OHLCV_COLUMNS = ["time", "symbol", "open", "high", "low", "close", "volume", "trade_count"]

# A long lookback so "recent" trades are found regardless of when the daemon
# last ran; the row-limit / window sliders then narrow what is displayed.
_DEFAULT_LOOKBACK = timedelta(days=3650)


# --------------------------------------------------------------------------
# Store access (reuses the existing read API only)
# --------------------------------------------------------------------------


async def _read_store(
    config: Config, symbol: str, start: datetime, end: datetime
) -> tuple[list[dict], list[dict]]:
    """Read trades + OHLCV bars for ``symbol`` via the pipeline's replay API.

    Connects the configured ``StorageBackend`` read-only, drains
    ``Pipeline.replay()`` for both sources, then disconnects. Returns
    ``(trades, bars)`` as plain dicts (oldest-first, as ``replay`` yields).
    """
    pipeline = Pipeline(config)
    await pipeline.storage.connect()
    try:
        await pipeline.storage.init_schema()
        trades = [t async for t in pipeline.replay(symbol, start, end, source="trades")]
        bars = [b async for b in pipeline.replay(symbol, start, end, source="ohlcv")]
    finally:
        await pipeline.storage.disconnect()
    return trades, bars


def load_store_data(
    config: Config, symbol: str, start: datetime, end: datetime
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synchronous wrapper around :func:`_read_store` returning DataFrames.

    Bridges Streamlit's sync script model to the daemon's async store API via
    ``asyncio.run``. Returns ``(trades_df, ohlcv_df)`` with the normalized
    column order; either may be empty.
    """
    trades, bars = asyncio.run(_read_store(config, symbol, start, end))
    return _trades_frame(trades), _ohlcv_frame(bars)


def _trades_frame(trades: list[dict]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=_TRADE_COLUMNS)
    df = pd.DataFrame(trades)
    df = df.reindex(columns=_TRADE_COLUMNS)
    return df.sort_values("time").reset_index(drop=True)


def _ohlcv_frame(bars: list[dict]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    df = pd.DataFrame(bars)
    df = df.reindex(columns=_OHLCV_COLUMNS)
    return df.sort_values("time").reset_index(drop=True)


# --------------------------------------------------------------------------
# Deterministic synthetic sample (offline / empty-store fallback)
# --------------------------------------------------------------------------


def synthesize_sample(
    symbol: str, n_minutes: int = 120, seed: int = 7
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build a deterministic trade + OHLCV sample so the UI always renders.

    Uses a seeded sinusoid + small pseudo-random walk (pure ``math``, no extra
    deps) so the same ``symbol``/``seed`` always yields identical data -- the
    offline demo and its smoke test are reproducible. Trades are then rolled up
    into 1-minute OHLCV bars exactly as the live schema expects.
    """
    base_price = 100.0 + (sum(ord(c) for c in symbol) % 400)
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=n_minutes)

    trades: list[dict] = []
    rng = seed
    for minute in range(n_minutes):
        minute_start = start + timedelta(minutes=minute)
        drift = math.sin(minute / 9.0) * base_price * 0.012
        # ~3-6 trades per minute, deterministically.
        n_trades = 3 + (minute % 4)
        for _ in range(n_trades):
            rng = (rng * 1103515245 + 12345) & 0x7FFFFFFF
            jitter = ((rng % 1000) / 1000.0 - 0.5) * base_price * 0.004
            price = round(base_price + drift + jitter, 2)
            qty = round(0.05 + (rng % 50) / 100.0, 4)
            side = "buy" if (rng >> 3) % 2 == 0 else "sell"
            trades.append(
                {
                    "time": minute_start + timedelta(seconds=int(rng % 60)),
                    "symbol": symbol,
                    "price": price,
                    "quantity": qty,
                    "side": side,
                    "exchange": "sample",
                }
            )

    trades_df = pd.DataFrame(trades).sort_values("time").reset_index(drop=True)
    ohlcv_df = _rollup_ohlcv(trades_df, symbol)
    return trades_df, ohlcv_df


def _rollup_ohlcv(trades_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Roll trades into 1-minute OHLCV bars (mirrors TickNormalizer's logic)."""
    if trades_df.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    df = trades_df.copy()
    df["minute"] = pd.to_datetime(df["time"]).dt.floor("min")
    grouped = df.groupby("minute")
    bars = grouped.agg(
        open=("price", "first"),
        high=("price", "max"),
        low=("price", "min"),
        close=("price", "last"),
        volume=("quantity", "sum"),
        trade_count=("price", "count"),
    ).reset_index()
    bars = bars.rename(columns={"minute": "time"})
    bars["symbol"] = symbol
    return bars.reindex(columns=_OHLCV_COLUMNS).sort_values("time").reset_index(drop=True)


# --------------------------------------------------------------------------
# Formatting + metric helpers
# --------------------------------------------------------------------------


def _fmt_price(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"${value:,.2f}"


def _fmt_qty(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:,.4f}"


def compute_metrics(trades_df: pd.DataFrame) -> dict[str, float | int | None]:
    """Derive headline metrics from the trade frame (defensive on empties).

    Returns last price, a spread proxy (recent high-low range), trade count,
    and total volume. The "spread proxy" stands in for a real bid/ask spread
    (the trade tape has no quotes) -- it is the high-low range of the most
    recent trades, a cheap liquidity-tightness signal.
    """
    if trades_df.empty:
        return {"last_price": None, "spread_proxy": None, "trade_count": 0, "volume": None}
    last_price = float(trades_df["price"].iloc[-1])
    recent = trades_df.tail(50)["price"]
    spread_proxy = float(recent.max() - recent.min())
    return {
        "last_price": last_price,
        "spread_proxy": spread_proxy,
        "trade_count": int(len(trades_df)),
        "volume": float(trades_df["quantity"].sum()),
    }


def build_price_chart(ohlcv_df: pd.DataFrame, trades_df: pd.DataFrame, symbol: str) -> go.Figure:
    """Price line + volume bars for ``symbol`` (prefers OHLCV; falls back to tape).

    Uses the rolled-up OHLCV close as the price line and bar volume as bars.
    If no bars exist yet (e.g. < 1 minute of data) it falls back to the raw
    trade tape so the chart is never blank when there is any data at all.
    """
    fig = go.Figure()
    if not ohlcv_df.empty:
        x = pd.to_datetime(ohlcv_df["time"])
        fig.add_trace(
            go.Scatter(
                x=x,
                y=ohlcv_df["close"],
                name="Close",
                mode="lines",
                line=dict(color="#3da9fc", width=2),
                yaxis="y1",
            )
        )
        fig.add_trace(
            go.Bar(
                x=x,
                y=ohlcv_df["volume"],
                name="Volume",
                marker=dict(color="rgba(61,169,252,0.25)"),
                yaxis="y2",
            )
        )
    elif not trades_df.empty:
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(trades_df["time"]),
                y=trades_df["price"],
                name="Trade price",
                mode="lines+markers",
                line=dict(color="#3da9fc", width=1),
                marker=dict(size=3),
            )
        )

    fig.update_layout(
        title=f"{symbol.upper()} - price & volume",
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=48, b=10),
        height=420,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(title="Price (USD)"),
        yaxis2=dict(title="Volume", overlaying="y", side="right", showgrid=False),
        xaxis=dict(title=None),
    )
    return fig


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------


def _default_symbol(config: Config) -> str:
    return config.symbols[0] if config.symbols else "btcusdt"


def main() -> None:
    st.set_page_config(
        page_title="Market-Data Live Monitor",
        page_icon="📈",
        layout="wide",
    )

    config = Config()

    st.title("Market-Data Live Monitor")
    st.caption(
        "Read-only view of the ingestion daemon's store -- recent trades, "
        "1-minute OHLCV bars, and price/volume. The daemon owns ingestion; "
        "this is a reader."
    )

    # --- Sidebar controls --------------------------------------------------
    with st.sidebar:
        st.header("Controls")
        symbols = config.symbols or ["btcusdt"]
        symbol = st.selectbox("Symbol", options=symbols, index=0)
        # Allow a custom symbol not in the configured list.
        custom = st.text_input("...or custom symbol", value="").strip().lower()
        if custom:
            symbol = custom

        window_label = st.select_slider(
            "Time window",
            options=["1h", "6h", "24h", "7d", "30d", "All"],
            value="All",
        )
        row_limit = st.slider("Max rows shown", min_value=10, max_value=500, value=100, step=10)
        st.button("Refresh", type="primary", use_container_width=True)
        st.divider()
        st.caption(
            f"Backend: `{config.storage_backend}`"
            + (f"  \nPath: `{config.duckdb_path}`" if config.storage_backend == "duckdb" else "")
        )

    end = datetime.now(UTC)
    window_map = {
        "1h": timedelta(hours=1),
        "6h": timedelta(hours=6),
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "All": _DEFAULT_LOOKBACK,
    }
    start = end - window_map[window_label]

    # --- Load (store first, synthetic fallback) ---------------------------
    is_sample = False
    load_error: str | None = None
    trades_df = _trades_frame([])
    ohlcv_df = _ohlcv_frame([])

    with st.spinner("Reading store..."):
        try:
            trades_df, ohlcv_df = load_store_data(config, symbol, start, end)
        except Exception as exc:  # noqa: BLE001 - surface a friendly message, never a traceback
            load_error = f"{type(exc).__name__}: {exc}"

    if load_error is not None or (trades_df.empty and ohlcv_df.empty):
        is_sample = True
        trades_df, ohlcv_df = synthesize_sample(symbol)

    # --- Status banner -----------------------------------------------------
    if is_sample and load_error is not None:
        st.warning(
            "Showing **deterministic sample data** -- the configured store could "
            f"not be read ({load_error}). Start the daemon (`python main.py`) to "
            "populate the store with live trades.",
            icon="⚠️",
        )
    elif is_sample:
        st.info(
            "Showing **deterministic sample data** -- the store is empty for this "
            "symbol/window. Start the daemon (`python main.py`) to ingest live "
            "trades, then Refresh.",
            icon="🧪",
        )
    else:
        st.success(
            f"Live store data -- {len(trades_df):,} trades, {len(ohlcv_df):,} OHLCV bars "
            f"for `{symbol}` in window `{window_label}`.",
            icon="✅",
        )

    # --- Metric cards ------------------------------------------------------
    metrics = compute_metrics(trades_df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Last price", _fmt_price(metrics["last_price"]))
    c2.metric("Spread proxy (50-trade range)", _fmt_price(metrics["spread_proxy"]))
    c3.metric("Trades", f"{metrics['trade_count']:,}")
    c4.metric("Volume", _fmt_qty(metrics["volume"]))

    # --- Chart -------------------------------------------------------------
    if trades_df.empty and ohlcv_df.empty:
        st.info("No data to chart yet.")
    else:
        st.plotly_chart(build_price_chart(ohlcv_df, trades_df, symbol), use_container_width=True)

    # --- Tables ------------------------------------------------------------
    left, right = st.columns(2)
    with left:
        st.subheader("Recent trades")
        if trades_df.empty:
            st.info("No trades for this symbol/window.")
        else:
            view = trades_df.sort_values("time", ascending=False).head(row_limit).copy()
            view["price"] = view["price"].map(lambda v: f"{v:,.2f}")
            view["quantity"] = view["quantity"].map(lambda v: f"{v:,.4f}")
            st.dataframe(view, use_container_width=True, hide_index=True)

    with right:
        st.subheader("OHLCV bars (1m)")
        if ohlcv_df.empty:
            st.info("No OHLCV bars yet (need >= 1 minute of trades).")
        else:
            view = ohlcv_df.sort_values("time", ascending=False).head(row_limit).copy()
            for col in ("open", "high", "low", "close"):
                view[col] = view[col].map(lambda v: f"{v:,.2f}")
            view["volume"] = view["volume"].map(lambda v: f"{v:,.4f}")
            st.dataframe(view, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
else:
    # Streamlit executes the script top-to-bottom (no __main__), so render on
    # import too -- this is the path the `streamlit run` and AppTest harness use.
    main()
