"""Streamlit front-end for the portfolio-optimization-engine.

A polished, professional UI that makes the optimizer usable without the CLI. It
is a *demo surface*: it never reimplements the optimization math. Every result
flows through the existing public API:

* :class:`PortfolioOptimizer` driven via the same injected-returns contract the
  backtester / FastAPI demo use (set ``.returns`` / ``.mean_returns`` /
  ``.cov_matrix``, then call a zero-arg ``optimize_*`` method) for the offline
  sample and uploaded/entered returns paths;
* :func:`run_analysis` for the live yfinance path and the "All objectives" view;
* :meth:`PortfolioOptimizer.solved_efficient_frontier` for the true frontier;
* :meth:`PortfolioOptimizer.optimize_black_litterman` /
  ``black_litterman_returns`` for the Black-Litterman mini-form.

It works fully OFFLINE using the bundled price fixture
(``portfolio_optimization_engine/data/sample_prices.csv``) and degrades
gracefully (never a raw traceback) when a live fetch fails.

This module is intentionally OUTSIDE ``[tool.coverage.run] source`` (which is
scoped to ``portfolio_optimization_engine``), matching how ``api/`` and
``main.py`` are handled, so the UI is AppTest-exercised without diluting the
package coverage gate.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless: never require a display (AppTest / Render)

from dataclasses import dataclass  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from portfolio_optimization_engine.analysis import (  # noqa: E402
    _OBJECTIVE_METHODS,
    compute_portfolio_returns,
)
from portfolio_optimization_engine.data_cache import (  # noqa: E402
    MarketDataError,
    _load_sample_prices,
)
from portfolio_optimization_engine.metrics import compute_metrics  # noqa: E402
from portfolio_optimization_engine.optimizer import (  # noqa: E402
    PortfolioOptimizer,
    PortfolioResult,
)

# --- objective registry -----------------------------------------------------
# Maps a UI label -> (optimizer method name, needs-a-target flag). The first six
# match ``config.OBJECTIVE_CHOICES`` (the zero-arg objectives); the last two are
# the target-based methods that exist on the optimizer but take one extra arg.
ACCENT = "#1f4e79"
POSITIVE = "#2e7d57"


@dataclass(frozen=True)
class Objective:
    label: str
    method: str
    target: str | None = None  # None | "vol" | "return"


OBJECTIVES: dict[str, Objective] = {
    "sharpe": Objective("Maximize Sharpe ratio", "optimize_sharpe"),
    "min_vol": Objective("Minimize volatility", "optimize_min_volatility"),
    "risk_parity": Objective("Risk parity", "optimize_risk_parity"),
    "sortino": Objective("Maximize Sortino ratio", "optimize_sortino"),
    "min_cvar": Objective("Minimize CVaR (tail risk)", "optimize_min_cvar"),
    "hrp": Objective("Hierarchical Risk Parity", "optimize_hrp"),
    "max_return_target_vol": Objective(
        "Max return @ target volatility", "optimize_max_return_target_vol", target="vol"
    ),
    "min_vol_target_return": Objective(
        "Min volatility @ target return", "optimize_min_vol_target_return", target="return"
    ),
}

#: The six zero-arg objectives must use the exact methods ``run_analysis`` calls
#: (the ``analysis._OBJECTIVE_METHODS`` registry), so the UI's "All objectives"
#: view stays a faithful mirror of ``run_analysis(objective="all")``. Keys differ
#: (UI uses ``sharpe``/``min_vol``; analysis uses ``max_sharpe``/``min_vol``) but
#: the underlying optimizer method names are identical.
_ZERO_ARG_METHODS = {o.method for o in OBJECTIVES.values() if o.target is None}
assert _ZERO_ARG_METHODS == set(_OBJECTIVE_METHODS.values()), (
    "UI objective methods drifted from analysis._OBJECTIVE_METHODS"
)


# --- data layer (importable + testable, no Streamlit calls) -----------------


def sample_prices() -> pd.DataFrame:
    """The bundled offline price fixture (Date-indexed, ticker columns)."""
    return _load_sample_prices()


def returns_from_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns, matching ``PortfolioOptimizer.calculate_returns``."""
    return prices.pct_change().dropna()


def parse_returns_csv(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce an uploaded CSV into a clean numeric daily-returns frame.

    Accepts either a returns matrix or a price matrix is NOT assumed here -- the
    UI lets the user pick. This drops a leading date/index column if present and
    keeps only numeric columns.
    """
    df = raw.copy()
    # Drop an obvious date/index first column (non-numeric or named like a date).
    first = df.columns[0]
    if df[first].dtype == object or str(first).lower() in ("date", "index", "unnamed: 0", ""):
        df = df.drop(columns=[first])
    df = df.apply(pd.to_numeric, errors="coerce").dropna(how="all", axis=1).dropna()
    if df.shape[1] < 2:
        raise ValueError("Need at least 2 numeric asset columns.")
    if df.shape[0] < 2:
        raise ValueError("Need at least 2 rows (periods) of data.")
    return df


def build_optimizer(returns: pd.DataFrame, risk_free_rate: float) -> PortfolioOptimizer:
    """Injected-returns optimizer (the backtester/FastAPI contract, no network)."""
    tickers = list(returns.columns)
    opt = PortfolioOptimizer(tickers, "1970-01-01", "1970-01-02", risk_free_rate=risk_free_rate)
    opt.returns = returns
    opt.mean_returns = returns.mean() * 252
    opt.cov_matrix = returns.cov() * 252
    return opt


def run_objective(
    opt: PortfolioOptimizer, key: str, *, target_value: float | None = None
) -> PortfolioResult:
    """Dispatch to the existing ``optimize_*`` method for ``key``.

    Target-based objectives receive their single positional arg; everything else
    is the zero-arg contract. No optimizer logic is duplicated.
    """
    spec = OBJECTIVES[key]
    method = getattr(opt, spec.method)
    if spec.target is not None:
        if target_value is None:
            raise ValueError(f"{spec.label} requires a target value.")
        return method(float(target_value))
    return method()


def metrics_row(returns: pd.DataFrame, weights: np.ndarray, risk_free_rate: float):
    """Shared-metrics panel for the optimal portfolio (same path as the CLI)."""
    port = compute_portfolio_returns(returns, weights)
    return compute_metrics(port, risk_free_rate=risk_free_rate)


# --- plotting (returns Plotly figures; no Streamlit calls) ------------------


def weights_figure(tickers: list[str], weights: np.ndarray) -> go.Figure:
    """Horizontal bar of portfolio weights, largest at the top."""
    order = np.argsort(weights)
    labels = [tickers[i] for i in order]
    vals = [float(weights[i]) for i in order]
    fig = go.Figure(
        go.Bar(
            x=vals,
            y=labels,
            orientation="h",
            marker_color=ACCENT,
            text=[f"{v:.1%}" for v in vals],
            textposition="outside",
            cliponaxis=False,
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=30, t=10, b=10),
        height=max(180, 42 * len(tickers)),
        xaxis=dict(title="Weight", tickformat=".0%"),
        yaxis=dict(title=""),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def frontier_figure(
    solved: pd.DataFrame, cloud: pd.DataFrame, optimal: PortfolioResult, label: str
) -> go.Figure:
    """Solved efficient frontier with the random cloud behind and optimal marked."""
    fig = go.Figure()
    if not cloud.empty:
        fig.add_trace(
            go.Scatter(
                x=cloud["volatility"],
                y=cloud["return"],
                mode="markers",
                marker=dict(
                    size=4,
                    color=cloud["sharpe"],
                    colorscale="Blues",
                    opacity=0.45,
                    colorbar=dict(title="Sharpe"),
                ),
                name="Random portfolios",
                hovertemplate="vol %{x:.2%}<br>ret %{y:.2%}<extra></extra>",
            )
        )
    if not solved.empty:
        fig.add_trace(
            go.Scatter(
                x=solved["volatility"],
                y=solved["return"],
                mode="lines",
                line=dict(color=ACCENT, width=3),
                name="Efficient frontier",
                hovertemplate="vol %{x:.2%}<br>ret %{y:.2%}<extra></extra>",
            )
        )
    fig.add_trace(
        go.Scatter(
            x=[optimal.volatility],
            y=[optimal.expected_return],
            mode="markers",
            marker=dict(size=15, color=POSITIVE, symbol="star", line=dict(color="white", width=1)),
            name=label,
            hovertemplate=f"{label}<br>vol %{{x:.2%}}<br>ret %{{y:.2%}}<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
        xaxis=dict(title="Annualized volatility", tickformat=".0%"),
        yaxis=dict(title="Annualized return", tickformat=".0%"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def bl_shift_figure(tickers: list[str], prior: pd.Series, posterior: pd.Series) -> go.Figure:
    """Prior -> posterior expected-return shift per asset."""
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=tickers,
            y=[float(prior[t]) for t in tickers],
            name="Prior (equilibrium)",
            marker_color="#9bb4cc",
        )
    )
    fig.add_trace(
        go.Bar(
            x=tickers,
            y=[float(posterior[t]) for t in tickers],
            name="Posterior (with views)",
            marker_color=ACCENT,
        )
    )
    fig.update_layout(
        barmode="group",
        margin=dict(l=10, r=10, t=10, b=10),
        height=360,
        yaxis=dict(title="Annualized excess return", tickformat=".1%"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


# --- Streamlit UI -----------------------------------------------------------


def _load_returns_from_sidebar() -> tuple[pd.DataFrame, str]:
    """Resolve the chosen input source into a (returns, note) pair.

    Never raises into the page: live-fetch failures fall back to the sample with
    a visible warning. Returns the daily-returns frame and a short status note.
    """
    st.sidebar.subheader("1. Data source")
    source = st.sidebar.radio(
        "Input source",
        ["Bundled sample (offline)", "Upload CSV", "Live tickers (yfinance)"],
        help="The sample works fully offline. Live fetch falls back to the sample on failure.",
    )

    if source == "Bundled sample (offline)":
        prices = sample_prices()
        return returns_from_prices(prices), "Using the bundled offline sample fixture."

    if source == "Upload CSV":
        up = st.sidebar.file_uploader("Returns or price CSV", type=["csv"])
        kind = st.sidebar.radio("CSV contains", ["Daily returns", "Prices"], horizontal=True)
        if up is None:
            st.sidebar.info("Upload a CSV, or the sample is used until you do.")
            return returns_from_prices(sample_prices()), "No file yet — showing the sample."
        raw = pd.read_csv(up)
        frame = parse_returns_csv(raw)
        if kind == "Prices":
            frame = returns_from_prices(frame)
        return frame, f"Loaded {frame.shape[1]} assets x {frame.shape[0]} periods from CSV."

    # Live tickers
    raw_tickers = st.sidebar.text_input("Tickers (space-separated)", "AAPL MSFT GOOGL AMZN")
    start = st.sidebar.text_input("Start date", "2022-01-01")
    end = st.sidebar.text_input("End date", "2024-01-01")
    tickers = [t.strip().upper() for t in raw_tickers.replace(",", " ").split() if t.strip()]
    if len(tickers) < 2:
        st.sidebar.warning("Enter at least 2 tickers; showing the sample meanwhile.")
        return returns_from_prices(sample_prices()), "Need >= 2 tickers — showing the sample."
    try:
        opt = PortfolioOptimizer(tickers, start, end)
        opt.fetch_data()
        opt.calculate_returns()
        return opt.returns, f"Fetched {len(tickers)} tickers live via yfinance."
    except (MarketDataError, ValueError) as exc:
        st.sidebar.error(f"Live fetch failed — using the sample instead.\n\n{exc}")
        return returns_from_prices(sample_prices()), "Live fetch failed — fell back to the sample."


def _metric_cards(result: PortfolioResult) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric(
        "Expected return",
        f"{result.expected_return:.2%}",
        help="Annualized expected return of the optimal weights.",
    )
    c2.metric("Volatility", f"{result.volatility:.2%}", help="Annualized standard deviation.")
    c3.metric("Sharpe", f"{result.sharpe_ratio:.2f}", help="Excess return per unit of total risk.")
    sortino = "n/a" if result.sortino_ratio is None else f"{result.sortino_ratio:.2f}"
    c4.metric("Sortino", sortino, help="Excess return per unit of downside risk.")
    cvar = "n/a" if result.cvar is None else f"{result.cvar:.2%}"
    c5.metric("CVaR (95%)", cvar, help="In-sample expected daily tail loss.")


def all_objectives_table(returns: pd.DataFrame, risk_free_rate: float) -> pd.DataFrame:
    """Compare every zero-arg objective on the same returns (offline-safe).

    This mirrors ``run_analysis(objective="all")`` -- it walks the same set of
    zero-arg objectives over a single optimizer and pairs each with the shared
    ``compute_metrics`` path. We dispatch on the injected-returns optimizer
    directly (rather than calling ``run_analysis``, which would re-fetch data
    over the network) so it works for uploaded/live returns fully offline. The
    two target-based objectives are excluded here (they need a parameter).
    """
    opt = build_optimizer(returns, risk_free_rate)
    rows = []
    for key, spec in OBJECTIVES.items():
        if spec.target is not None:
            continue
        res = run_objective(opt, key)
        perf = metrics_row(returns, res.weights, risk_free_rate)
        rows.append(
            {
                "Objective": spec.label,
                "Return": res.expected_return,
                "Volatility": res.volatility,
                "Sharpe": res.sharpe_ratio,
                "Sortino": res.sortino_ratio if res.sortino_ratio is not None else float("nan"),
                "CVaR": res.cvar if res.cvar is not None else float("nan"),
                "Max drawdown": perf.max_drawdown,
            }
        )
    return pd.DataFrame(rows).set_index("Objective")


def _all_objectives_view(returns: pd.DataFrame, risk_free_rate: float) -> None:
    """Render the all-objectives comparison table."""
    table = all_objectives_table(returns, risk_free_rate)
    st.dataframe(
        table.style.format(
            {
                "Return": "{:.2%}",
                "Volatility": "{:.2%}",
                "Sharpe": "{:.2f}",
                "Sortino": "{:.2f}",
                "CVaR": "{:.2%}",
                "Max drawdown": "{:.2%}",
            }
        ),
        use_container_width=True,
    )


def _black_litterman_view(returns: pd.DataFrame, risk_free_rate: float) -> None:
    tickers = list(returns.columns)
    st.caption(
        "Express a simple view; the optimizer blends it with the market-implied "
        "equilibrium prior (Black-Litterman) and re-optimizes on the posterior."
    )
    col1, col2, col3 = st.columns([2, 1, 1])
    asset = col1.selectbox("View on", tickers)
    direction = col2.selectbox("Stance", ["Bullish", "Bearish"])
    strength = col3.slider(
        "Annual view return",
        0.0,
        0.40,
        0.15,
        0.01,
        help="Magnitude of the absolute view on the chosen asset.",
    )
    opt = build_optimizer(returns, risk_free_rate)
    n = len(tickers)
    p = np.zeros((1, n))
    p[0, tickers.index(asset)] = 1.0
    q = np.array([strength if direction == "Bullish" else -strength])
    try:
        prior = opt.black_litterman_returns()
        posterior = opt.black_litterman_returns(p, q)
        result = opt.optimize_black_litterman(p, q)
    except (ValueError, np.linalg.LinAlgError) as exc:
        st.error(f"Black-Litterman could not be computed: {exc}")
        return
    st.plotly_chart(bl_shift_figure(tickers, prior, posterior), use_container_width=True)
    st.markdown("**Posterior max-Sharpe weights**")
    st.plotly_chart(weights_figure(tickers, result.weights), use_container_width=True)


def main() -> None:
    st.set_page_config(
        page_title="Portfolio Optimization Engine",
        page_icon=":bar_chart:",
        layout="wide",
    )
    st.title("Portfolio Optimization Engine")
    st.caption(
        "Modern Portfolio Theory: efficient frontier, multi-objective optimization, "
        "and Black-Litterman — driven entirely by the library's public API."
    )

    returns, note = _load_returns_from_sidebar()

    st.sidebar.subheader("2. Objective")
    keys = list(OBJECTIVES)
    obj_key = st.sidebar.selectbox(
        "Optimization objective",
        keys,
        format_func=lambda k: OBJECTIVES[k].label,
        help="Matches the engine's optimize_* methods.",
    )
    spec = OBJECTIVES[obj_key]

    st.sidebar.subheader("3. Parameters")
    risk_free_rate = st.sidebar.slider(
        "Risk-free rate", 0.0, 0.10, 0.02, 0.005, help="Annual; feeds Sharpe / Sortino / metrics."
    )
    target_value: float | None = None
    if spec.target == "vol":
        target_value = st.sidebar.slider("Target volatility", 0.05, 0.60, 0.20, 0.01)
    elif spec.target == "return":
        target_value = st.sidebar.slider("Target return", 0.02, 0.50, 0.15, 0.01)
    lookback = st.sidebar.slider(
        "Lookback (most recent periods)",
        30,
        int(len(returns)),
        int(len(returns)),
        10,
        help="Trim to the most recent N return periods used for estimation.",
    )
    if lookback < len(returns):
        returns = returns.iloc[-lookback:]

    st.info(note)

    if returns.shape[1] < 2 or returns.shape[0] < 2:
        st.warning("Need at least 2 assets and 2 periods to optimize.")
        return

    tickers = list(returns.columns)
    opt = build_optimizer(returns, risk_free_rate)

    try:
        with st.spinner("Optimizing portfolio..."):
            result = run_objective(opt, obj_key, target_value=target_value)
            perf = metrics_row(returns, result.weights, risk_free_rate)
    except (ValueError, np.linalg.LinAlgError) as exc:
        st.error(f"Optimization could not be completed: {exc}")
        st.caption("Try a different objective, target, or a longer lookback window.")
        return

    tab_opt, tab_frontier, tab_all, tab_bl = st.tabs(
        ["Optimal portfolio", "Efficient frontier", "All objectives", "Black-Litterman"]
    )

    with tab_opt:
        st.subheader(spec.label)
        _metric_cards(result)
        left, right = st.columns([3, 2])
        with left:
            st.markdown("**Allocation**")
            st.plotly_chart(weights_figure(tickers, result.weights), use_container_width=True)
        with right:
            st.markdown("**Weights**")
            wdf = pd.DataFrame({"Weight": result.weights}, index=tickers)
            st.dataframe(wdf.style.format({"Weight": "{:.2%}"}), use_container_width=True)
            st.metric(
                "Max drawdown",
                f"{perf.max_drawdown:.2%}",
                help="Worst peak-to-trough on the in-sample equity curve.",
            )
            st.metric("CAGR", f"{perf.cagr:.2%}")

    with tab_frontier:
        st.subheader("Solved efficient frontier")
        st.caption(
            "The solid line is the true solved frontier (min-vol per target return); "
            "the cloud is random portfolios, colored by Sharpe. The star is your optimal point."
        )
        try:
            with st.spinner("Solving the efficient frontier..."):
                solved = opt.solved_efficient_frontier(n_points=40)
                cloud = opt.efficient_frontier(num_portfolios=2500, random_state=7)
            st.plotly_chart(
                frontier_figure(solved, cloud, result, spec.label), use_container_width=True
            )
        except (ValueError, np.linalg.LinAlgError) as exc:
            st.error(f"Frontier could not be computed: {exc}")

    with tab_all:
        st.subheader("All objectives compared")
        st.caption("Each zero-arg objective optimized on the same returns (offline-safe).")
        with st.spinner("Running all objectives..."):
            _all_objectives_view(returns, risk_free_rate)

    with tab_bl:
        st.subheader("Black-Litterman views")
        _black_litterman_view(returns, risk_free_rate)


if __name__ == "__main__":
    main()
