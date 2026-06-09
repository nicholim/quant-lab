"""Unified Plotly Dash dashboard: optimize a portfolio, then backtest it.

Pick tickers / dates / objective, click Run, and the app (1) optimizes the
portfolio with the optimization engine (efficient frontier, weights, Monte Carlo
VaR/CVaR) and (2) runs a walk-forward rebalancing backtest of that objective
through the backtesting framework (equity, drawdown, beta/alpha) — the engine
feeding the backtester, end to end.

Run:  python dashboard.py   (then open http://127.0.0.1:8050)

The figure/metric builders below are pure functions of already-computed data, so
they are unit-tested without launching a server.
"""

import os
from urllib.parse import quote

import plotly.graph_objects as go
import plotly.io as pio
from dash import Dash, Input, Output, State, dcc, html
from portfolio_optimization_engine.analysis import run_analysis
from portfolio_optimization_engine.config import AnalysisConfig

from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.datastore import DataStore
from src.execution import SimulatedExecution
from src.market_data import MarketDataError, _offline_enabled
from src.portfolio import Portfolio
from src.strategy import OptimizationRebalanceStrategy

# --- Shared visual identity (kept in sync with assets/dashboard.css) ---
# Same brand DNA as the showcase site: near-black ink, paper, one signal-red
# accent. Semantic green/red carry P&L; the accent is reserved for the chosen
# objective and interactive state, never decoration.

INK = "#1d1f24"
INK_SOFT = "#565a63"
POS = "#1f9d63"
NEG = "#cf3a22"
ACCENT = "#cf3a22"
MUTED = "#9aa0a8"
GRID = "#e9ebee"
SANS = "Hanken Grotesk, -apple-system, Segoe UI, system-ui, sans-serif"
MONO = "Spline Sans Mono, SFMono-Regular, Menlo, monospace"

# Inline candlestick logo for the header (white wicks + one red body), echoing
# the showcase favicon. Inlined as a data URI so there is no extra asset fetch.
_LOGO_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="8" fill="#1f2127"/>'
    '<g stroke="#eef1f5" stroke-width="2.1" stroke-linecap="round">'
    '<line x1="10" y1="7" x2="10" y2="25"/><line x1="16" y1="5" x2="16" y2="27"/></g>'
    '<rect x="7.7" y="12.5" width="4.6" height="8.5" rx="1.3" fill="#eef1f5"/>'
    '<rect x="13.7" y="9.5" width="4.6" height="13" rx="1.3" fill="#cf3a22"/>'
    '<line x1="23" y1="11" x2="23" y2="21" stroke="#eef1f5" stroke-width="2.1" '
    'stroke-linecap="round"/>'
    '<rect x="20.7" y="13.5" width="4.6" height="5.5" rx="1.3" fill="#eef1f5"/>'
    "</svg>"
)
_LOGO_DATA_URI = "data:image/svg+xml;utf8," + quote(_LOGO_SVG)

# Restrained sequential scale for the random-portfolio cloud (pale -> ink),
# replacing the rainbow Viridis so the chart reads on-brand.
_SHARPE_SCALE = [[0.0, "#d9dbde"], [0.5, "#9aa0a8"], [1.0, INK]]

# A single Plotly template so every figure shares fonts, gridlines, margins and
# legend placement. Registered under "quantlab" and applied per-figure.
_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=dict(family=SANS, color=INK, size=13),
        title=dict(font=dict(size=16, color=INK), x=0.01, xanchor="left"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        colorway=[INK, POS, ACCENT, "#6b7280", MUTED, "#3f4248"],
        xaxis=dict(
            gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, ticks="outside", automargin=True
        ),
        yaxis=dict(
            gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, ticks="outside", automargin=True
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=72, r=24, t=56, b=52),
        hoverlabel=dict(font=dict(family=MONO, size=12)),
    )
)
pio.templates["quantlab"] = _TEMPLATE
_TPL = "quantlab"

# Maps the dashboard dropdown value -> the key ``run_analysis`` uses in its
# ``results`` dict (see analysis._OBJECTIVE_METHODS). Every value here MUST be a
# valid run_analysis objective so ``analysis["results"][OBJECTIVE_TO_KEY[obj]]``
# always resolves; the dashboard calls run_analysis with objective="all", which
# computes all of these. ``hrp`` (Hierarchical Risk Parity) is solver-free and a
# valid run_analysis objective, so it slots in like the rest.
OBJECTIVE_TO_KEY = {
    "sharpe": "max_sharpe",
    "min_vol": "min_vol",
    "risk_parity": "risk_parity",
    "sortino": "sortino",
    "min_cvar": "min_cvar",
    "hrp": "hrp",
}

# Distinct symbol per objective; color is assigned at plot time (the chosen
# objective in accent red, the rest muted ink) so the legend reads calm.
_MARKER_SYMBOL = {
    "max_sharpe": "star",
    "min_vol": "circle",
    "risk_parity": "diamond",
    "sortino": "triangle-up",
    "min_cvar": "square",
    "hrp": "cross",
}


# --- Figure builders (pure) ---


def frontier_figure(frontier, results, chosen=None) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=frontier["volatility"],
            y=frontier["return"],
            mode="markers",
            marker=dict(
                size=5,
                color=frontier["sharpe"],
                colorscale=_SHARPE_SCALE,
                colorbar=dict(title="Sharpe", outlinewidth=0, thickness=12),
                opacity=0.45,
            ),
            name="Random portfolios",
            hovertemplate="vol %{x:.1%}<br>ret %{y:.1%}<extra></extra>",
        )
    )
    # The other objectives collapse into ONE muted "Other objectives" legend
    # entry (not six rainbow rows), and the chosen objective is a single red
    # star on top — so the legend reads as three clean items.
    others = [(n, r) for n, r in results.items() if n != chosen]
    if others:
        fig.add_trace(
            go.Scatter(
                x=[r.volatility for _, r in others],
                y=[r.expected_return for _, r in others],
                mode="markers",
                marker=dict(
                    size=10,
                    symbol=[_MARKER_SYMBOL.get(n, "circle") for n, _ in others],
                    color=MUTED,
                    line=dict(width=1, color="white"),
                ),
                name="Other objectives",
                text=[n.replace("_", " ").title() for n, _ in others],
                hovertemplate="<b>%{text}</b><br>vol %{x:.1%}<br>ret %{y:.1%}<extra></extra>",
            )
        )
    if chosen in results:
        res = results[chosen]
        label = chosen.replace("_", " ").title()
        fig.add_trace(
            go.Scatter(
                x=[res.volatility],
                y=[res.expected_return],
                mode="markers",
                marker=dict(
                    size=18,
                    symbol=_MARKER_SYMBOL.get(chosen, "star"),
                    color=ACCENT,
                    line=dict(width=1.5, color="white"),
                ),
                name=f"{label} (selected)",
                hovertemplate=f"<b>{label}</b><br>vol %{{x:.1%}}<br>ret %{{y:.1%}}<extra></extra>",
            )
        )
    fig.update_layout(
        title="Efficient frontier — risk vs. return",
        xaxis_title="Annualized volatility (σ)",
        yaxis_title="Annualized return",
        xaxis_tickformat=".0%",
        yaxis_tickformat=".0%",
        height=460,
        template=_TPL,
    )
    return fig


def weights_figure(result, tickers) -> go.Figure:
    pairs = [(t, w) for t, w in zip(tickers, result.weights, strict=False) if abs(w) > 0.005]
    fig = go.Figure(
        go.Bar(
            x=[t for t, _ in pairs],
            y=[w for _, w in pairs],
            marker_color=INK,
            hovertemplate="%{x}: %{y:.1%}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Optimal weights — allocation by asset",
        xaxis_title="Asset",
        yaxis_title="Portfolio weight",
        yaxis_tickformat=".0%",
        height=360,
        template=_TPL,
    )
    return fig


def equity_figure(analytics) -> go.Figure:
    eq = analytics.equity
    fig = go.Figure(
        go.Scatter(
            x=eq.index,
            y=eq["equity"],
            mode="lines",
            line=dict(color=INK, width=2),
            name="Equity",
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Equity curve — portfolio value over time",
        xaxis_title="Date",
        yaxis_title="Portfolio value (USD)",
        yaxis_tickprefix="$",
        yaxis_tickformat=",.0f",
        height=380,
        template=_TPL,
    )
    return fig


def drawdown_figure(analytics) -> go.Figure:
    eq = analytics.equity["equity"]
    dd = eq / eq.cummax() - 1.0
    fig = go.Figure(
        go.Scatter(
            x=eq.index,
            y=dd,
            fill="tozeroy",
            line=dict(color=NEG, width=1.5),
            fillcolor="rgba(207, 58, 34, 0.10)",
            name="Drawdown",
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.1%}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Drawdown — peak-to-trough decline",
        xaxis_title="Date",
        yaxis_title="Drawdown",
        yaxis_tickformat=".0%",
        height=260,
        template=_TPL,
    )
    return fig


def optimization_metric_rows(analysis, key) -> list[list[str]]:
    res = analysis["results"][key]
    m = analysis["metrics"][key]
    mc = analysis["mc_summary"]
    rows = [
        ["Objective", str(res.objective)],
        ["Expected Return", f"{res.expected_return:.2%}"],
        ["Volatility", f"{res.volatility:.2%}"],
        ["Sharpe", f"{res.sharpe_ratio:.2f}"],
    ]
    if res.sortino_ratio is not None:
        rows.append(["Sortino", f"{res.sortino_ratio:.2f}"])
    rows += [
        ["CAGR", f"{m.cagr:.2%}"],
        ["Max Drawdown", f"{m.max_drawdown:.2%}"],
        ["1Y VaR (95%)", f"${mc['var_95']:,.0f}"],
        ["1Y CVaR (95%)", f"${mc['cvar_95']:,.0f}"],
    ]
    return rows


def backtest_metric_rows(analytics) -> list[list[str]]:
    # Total Return / Sharpe / Sortino / Max Drawdown are the headline cards above;
    # this table carries the *additional* metrics so nothing is shown twice.
    rows = [
        ["CAGR", f"{analytics.annualized_return():.2%}"],
        ["Calmar", f"{analytics.calmar_ratio():.2f}"],
        ["Total trades", f"{len(analytics.trades)}"],
    ]
    if analytics.beta() is not None:
        rows += [["Beta", f"{analytics.beta():.2f}"], ["Alpha (ann.)", f"{analytics.alpha():.2%}"]]
    return rows


def _metric_card(label: str, value: str, tone: str = "") -> html.Div:
    """One headline KPI card. ``tone`` in {"", "pos", "neg"} colors the value."""
    cls = "metric-card__value"
    if tone:
        cls += f" metric-card__value--{tone}"
    return html.Div(
        className="metric-card",
        children=[
            html.Div(label, className="metric-card__label"),
            html.Div(value, className=cls),
        ],
    )


def headline_cards(analytics) -> html.Div:
    """The four KPIs shown above the charts: total return, Sharpe, Sortino, max DD.

    Pure function of the already-computed analytics object — uses the same
    ``metrics``-backed accessors as the tables, just surfaced as cards with
    sign-aware coloring and consistent number formatting.
    """
    total = analytics.total_return()
    sharpe = analytics.sharpe_ratio()
    sortino = analytics.sortino_ratio()
    max_dd = analytics.max_drawdown()
    return html.Div(
        className="metric-cards",
        children=[
            _metric_card("Total Return", f"{total:+.2%}", "pos" if total >= 0 else "neg"),
            _metric_card("Sharpe Ratio", f"{sharpe:.2f}", "pos" if sharpe >= 0 else "neg"),
            _metric_card("Sortino Ratio", f"{sortino:.2f}", "pos" if sortino >= 0 else "neg"),
            _metric_card("Max Drawdown", f"{max_dd:.2%}", "neg"),
        ],
    )


def _table(rows: list[list[str]]):
    return html.Table(
        [html.Tbody([html.Tr([html.Td(c) for c in row]) for row in rows])],
        style={"width": "100%", "borderCollapse": "collapse"},
        className="metric-table",
    )


# --- Compute (called by the callback) ---


def optimize(tickers, start, end, risk_free_rate, objective):
    """Run the engine optimization; returns (analysis dict, result key)."""
    config = AnalysisConfig(
        tickers=tickers,
        start_date=start,
        end_date=end,
        risk_free_rate=risk_free_rate,
        objective="all",
        num_portfolios=2000,
        no_plots=True,
        export_format="none",
        # Honor offline for the optimizer half too, so a single BACKTESTING_OFFLINE=1
        # offlines the WHOLE dashboard (the optimizer uses the portfolio engine's own
        # fetch, which otherwise only reads PORTFOLIO_OFFLINE).
        offline=_offline_enabled(False),
    )
    return run_analysis(config), OBJECTIVE_TO_KEY[objective]


def backtest_optimized(tickers, start, end, objective, store=None, allow_short=False):
    """Walk-forward rebalancing backtest of the chosen objective via the engine.

    ``allow_short`` (default False = long-only, unchanged) threads straight into
    ``Portfolio(allow_short=...)``. The optimizer emits long-only target weights,
    so a short-enabled portfolio only matters once a rebalance reduces a holding
    below a prior allocation; defaulting it off preserves prior behavior.
    """
    data = YFinanceDataHandler(tickers, start, end, store=store, offline=_offline_enabled(False))
    strategy = OptimizationRebalanceStrategy(
        tickers, lookback=252, rebalance_freq=21, objective=objective
    )
    return Backtest(
        data,
        strategy,
        Portfolio(initial_capital=100_000, allow_short=allow_short),
        SimulatedExecution(),
        strategy_name=f"Dashboard {objective}",
        store=store,
        benchmark="SPY",
    ).run()


# --- App ---


def objective_options() -> list[dict[str, str]]:
    """Dropdown options for the objective selector (label/value pairs).

    Kept as a named helper so the wiring is unit-testable and the ``hrp``
    label stays distinct (acronym, upper-cased) from the title-cased rest.
    """
    return [
        {
            "label": k.replace("_", " ").upper() if k == "hrp" else k.replace("_", " ").title(),
            "value": k,
        }
        for k in OBJECTIVE_TO_KEY
    ]


def _field(label: str, control, hint: str | None = None) -> html.Div:
    """A labelled control with an optional caption/tooltip line."""
    children = [html.Label(label), control]
    if hint:
        children.append(html.Span(hint, className="hint"))
    return html.Div(className="field", children=children)


def _control_group(legend: str, children: list) -> html.Div:
    return html.Div(
        className="control-group",
        children=[
            html.Div(legend, className="control-group__legend"),
            html.Div(className="control-group__body", children=children),
        ],
    )


def _header() -> html.Header:
    offline = _offline_enabled(False)
    return html.Header(
        className="app-header",
        children=[
            html.Div(
                className="app-header__brand",
                children=[
                    html.Img(src=_LOGO_DATA_URI, className="app-header__logo", alt="Portfolio Lab"),
                    html.Div(
                        children=[
                            html.P("Portfolio Lab", className="app-header__title"),
                            html.P(
                                "Optimize a portfolio, then walk-forward backtest it.",
                                className="app-header__subtitle",
                            ),
                        ]
                    ),
                ],
            ),
            html.Div(
                className="app-header__status",
                title=(
                    "This demo runs on bundled historical sample data, so results "
                    "always render (it is not a live market feed)."
                    if offline
                    else "Results use market data fetched from yfinance at run time."
                ),
                children=[
                    html.Span(
                        className="status-dot status-dot--sample" if offline else "status-dot"
                    ),
                    html.Span("Sample data" if offline else "Market data"),
                ],
            ),
        ],
    )


def _sidebar() -> html.Aside:
    return html.Aside(
        className="sidebar",
        children=[
            html.Div("Controls", className="sidebar__heading"),
            html.Div(
                className="sidebar__body",
                children=[
                    _control_group(
                        "Universe & Window",
                        [
                            _field(
                                "Tickers",
                                dcc.Input(id="tickers", value="AAPL, MSFT, JPM, AMZN", type="text"),
                                "Comma-separated symbols, e.g. AAPL, MSFT, JPM",
                            ),
                            _field(
                                "Start date", dcc.Input(id="start", value="2020-01-01", type="text")
                            ),
                            _field(
                                "End date", dcc.Input(id="end", value="2024-01-01", type="text")
                            ),
                            _field(
                                "Risk-free rate",
                                dcc.Input(
                                    id="rf", value=0.02, type="number", step=0.005, min=0, max=0.2
                                ),
                                "Annual, decimal (0.02 = 2%). Used for Sharpe.",
                            ),
                        ],
                    ),
                    _control_group(
                        "Strategy",
                        [
                            _field(
                                "Objective",
                                dcc.Dropdown(
                                    id="objective",
                                    value="sharpe",
                                    clearable=False,
                                    className="dash-dropdown",
                                    options=objective_options(),
                                ),
                                "What the optimizer maximizes/targets at each rebalance.",
                            ),
                            _field(
                                "Position constraints",
                                dcc.Checklist(
                                    id="allow-short",
                                    options=[{"label": " Allow short selling", "value": "short"}],
                                    value=[],  # default off = long-only (unchanged behavior)
                                ),
                                "Off = long-only (default). On = signed positions permitted.",
                            ),
                        ],
                    ),
                    _control_group(
                        "Rebalance",
                        [
                            html.Div(
                                className="hint",
                                children="Walk-forward: 252-day lookback, rebalanced every 21 "
                                "trading days, $100,000 initial capital, benchmarked vs. SPY.",
                            )
                        ],
                    ),
                    html.Button("Run analysis", id="run", n_clicks=0, className="btn-run"),
                ],
            ),
            html.Div(id="status", className="status-line"),
        ],
    )


def _empty_state(title: str, body: str, steps: list[str] | None = None) -> html.Div:
    children = [html.H4(title), html.Div(body)]
    if steps:
        children.append(
            html.Ol(
                className="empty-state__steps",
                children=[html.Li(s) for s in steps],
            )
        )
    return html.Div(className="empty-state", children=children)


def _placeholder_figure(message: str = "No results to display.") -> go.Figure:
    """A clean, axis-free canvas with one centered hint.

    Used as the INITIAL figure for every graph and as the empty/error return
    from the callback, so a chart never shows bare default Plotly ``-1..6`` axes
    before (or instead of) real data. Mirrors the ``.empty-state`` pattern for
    the panels, applied to the plot surface.
    """
    fig = go.Figure(layout=go.Layout(template=_TPL))
    fig.update_layout(
        height=240,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        margin=dict(l=24, r=24, t=24, b=24),
        annotations=[
            dict(
                text=message,
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=13, color="#71757e"),  # --ink-faint: ≥4.5:1 on white
            )
        ],
    )
    return fig


def build_app() -> Dash:
    app = Dash(
        __name__,
        title="Portfolio Lab — Optimizer + Backtester",
        external_stylesheets=[
            "https://fonts.googleapis.com/css2?"
            "family=Hanken+Grotesk:wght@400;500;600;700&"
            "family=Spline+Sans+Mono:wght@400;500;600&display=swap"
        ],
    )
    app.layout = html.Div(
        children=[
            _header(),
            html.Div(
                className="app-shell",
                children=[
                    _sidebar(),
                    html.Main(
                        className="content",
                        children=[
                            html.Div(id="alert"),
                            dcc.Loading(
                                type="default",
                                color=ACCENT,
                                children=dcc.Tabs(
                                    [
                                        dcc.Tab(
                                            label="Optimization",
                                            className="dash-tab",
                                            children=[
                                                html.Div(
                                                    "Efficient frontier and the optimal "
                                                    "weights for the selected objective.",
                                                    className="section-title",
                                                ),
                                                html.Div(
                                                    style={"display": "flex", "gap": "18px"},
                                                    children=[
                                                        html.Div(
                                                            className="panel",
                                                            style={"flex": 2},
                                                            children=dcc.Graph(
                                                                id="frontier",
                                                                figure=_placeholder_figure(
                                                                    "Run an analysis to plot the "
                                                                    "efficient frontier."
                                                                ),
                                                                config={"displayModeBar": False},
                                                            ),
                                                        ),
                                                        html.Div(
                                                            id="opt-metrics",
                                                            style={"flex": 1},
                                                            children=_empty_state(
                                                                "No analysis yet",
                                                                "Optimized weights and risk "
                                                                "metrics appear here once you run.",
                                                                steps=[
                                                                    "Enter tickers and a date "
                                                                    "window.",
                                                                    "Pick an optimization "
                                                                    "objective.",
                                                                    "Press Run analysis.",
                                                                ],
                                                            ),
                                                        ),
                                                    ],
                                                ),
                                                html.Div(
                                                    className="panel",
                                                    children=dcc.Graph(
                                                        id="weights",
                                                        figure=_placeholder_figure(
                                                            "Optimized weights appear here "
                                                            "after you run."
                                                        ),
                                                        config={"displayModeBar": False},
                                                    ),
                                                ),
                                            ],
                                        ),
                                        dcc.Tab(
                                            label="Backtest",
                                            className="dash-tab",
                                            children=[
                                                html.Div(
                                                    "Out-of-sample walk-forward performance.",
                                                    className="section-title",
                                                ),
                                                html.Div(id="headline"),
                                                html.Div(
                                                    className="panel",
                                                    children=dcc.Graph(
                                                        id="equity",
                                                        figure=_placeholder_figure(
                                                            "Run an analysis to see the "
                                                            "equity curve."
                                                        ),
                                                        config={"displayModeBar": False},
                                                    ),
                                                ),
                                                html.Div(
                                                    className="panel",
                                                    children=dcc.Graph(
                                                        id="drawdown",
                                                        figure=_placeholder_figure(
                                                            "Drawdown appears here after you run."
                                                        ),
                                                        config={"displayModeBar": False},
                                                    ),
                                                ),
                                                html.Div(
                                                    id="bt-metrics",
                                                    style={"maxWidth": "520px"},
                                                ),
                                            ],
                                        ),
                                    ]
                                ),
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    @app.callback(
        Output("frontier", "figure"),
        Output("weights", "figure"),
        Output("opt-metrics", "children"),
        Output("headline", "children"),
        Output("equity", "figure"),
        Output("drawdown", "figure"),
        Output("bt-metrics", "children"),
        Output("alert", "children"),
        Output("status", "children"),
        Input("run", "n_clicks"),
        State("tickers", "value"),
        State("start", "value"),
        State("end", "value"),
        State("rf", "value"),
        State("objective", "value"),
        State("allow-short", "value"),
        prevent_initial_call=True,
    )
    def _run(_n, tickers_raw, start, end, rf, objective, allow_short_value):
        tickers = [t.strip().upper() for t in (tickers_raw or "").split(",") if t.strip()]
        allow_short = "short" in (allow_short_value or [])
        empty = _placeholder_figure()

        if not tickers:
            alert = html.Div(
                "Enter at least one ticker symbol to run an analysis.",
                className="alert alert--info",
            )
            empties = _empty_state("No analysis yet", "Set your inputs and press Run analysis.")
            return empty, empty, empties, "", empty, empty, empties, alert, "Waiting for input."

        try:
            store = DataStore("data/dashboard.duckdb")
            try:
                analysis, key = optimize(tickers, start, end, float(rf), objective)
                analytics = backtest_optimized(
                    tickers, start, end, objective, store=store, allow_short=allow_short
                )
            finally:
                store.close()
        except MarketDataError as exc:
            # Data-layer failure (bad ticker, no rows, network down): a clean,
            # actionable in-UI alert instead of a stack trace / HTTP 500.
            alert = html.Div(
                [
                    html.Strong("Could not load market data. "),
                    f"{exc} ",
                    html.Span(
                        "Check the symbols/dates, or set BACKTESTING_OFFLINE=1 to use "
                        "the bundled sample data.",
                        className="hint",
                    ),
                ],
                className="alert alert--error",
            )
            es = _empty_state("Data unavailable", "Resolve the issue above and run again.")
            return empty, empty, es, "", empty, empty, es, alert, "Data fetch failed."
        except Exception as exc:  # surface any other error in the UI rather than 500ing
            alert = html.Div(
                [html.Strong("Run failed. "), str(exc)], className="alert alert--error"
            )
            es = _empty_state("Something went wrong", "See the message above.")
            return empty, empty, es, "", empty, empty, es, alert, "Error."

        chosen = analysis["results"][key]
        return (
            frontier_figure(analysis["frontier"], analysis["results"], chosen=key),
            weights_figure(chosen, tickers),
            [
                html.Div("Optimized portfolio", className="panel__title"),
                _table(optimization_metric_rows(analysis, key)),
            ],
            headline_cards(analytics),
            equity_figure(analytics),
            drawdown_figure(analytics),
            [
                html.Div("Additional metrics", className="panel__title"),
                _table(backtest_metric_rows(analytics)),
            ],
            None,  # clear any prior alert on success
            f"Done: optimized {len(tickers)} assets and backtested '{objective}'"
            f"{' (short selling enabled)' if allow_short else ''}.",
        )

    return app


app = build_app()
server = app.server  # WSGI entry point for gunicorn: `gunicorn dashboard:server`


if __name__ == "__main__":
    # Local dev server. In production use gunicorn (see DEPLOY.md).
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8050)), debug=True)
