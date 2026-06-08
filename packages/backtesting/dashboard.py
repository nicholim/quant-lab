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

NAVY = "#1f3b73"
POS = "#1b8a5a"
NEG = "#c0392b"
ACCENT = "#c79a3a"
INK = "#0f1b2d"
GRID = "#e3e8ef"

# A single Plotly template so every figure shares fonts, gridlines, margins and
# legend placement. Registered under "quantlab" and applied per-figure.
_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        font=dict(
            family="-apple-system, Segoe UI, Inter, system-ui, sans-serif", color=INK, size=12
        ),
        title=dict(font=dict(size=15, color=INK), x=0.01, xanchor="left"),
        paper_bgcolor="white",
        plot_bgcolor="white",
        colorway=[NAVY, POS, ACCENT, NEG, "#7d6cc4", "#3a8fb7"],
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, ticks="outside"),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, ticks="outside"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=24, t=52, b=48),
        hoverlabel=dict(font=dict(family="SFMono-Regular, Menlo, monospace", size=12)),
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

_MARKER = {
    "max_sharpe": ("star", "#d62728"),
    "min_vol": ("star", "#1f77b4"),
    "risk_parity": ("diamond", "#2ca02c"),
    "sortino": ("diamond", "#ff7f0e"),
    "min_cvar": ("diamond", "#9467bd"),
    "hrp": ("diamond", "#8c564b"),
}


# --- Figure builders (pure) ---


def frontier_figure(frontier, results) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scattergl(
            x=frontier["volatility"],
            y=frontier["return"],
            mode="markers",
            marker=dict(
                size=5,
                color=frontier["sharpe"],
                colorscale="Viridis",
                colorbar=dict(title="Sharpe"),
                opacity=0.5,
            ),
            name="Random portfolios",
            hovertemplate="vol %{x:.1%}<br>ret %{y:.1%}<extra></extra>",
        )
    )
    for name, res in results.items():
        symbol, color = _MARKER.get(name, ("circle", "black"))
        fig.add_trace(
            go.Scatter(
                x=[res.volatility],
                y=[res.expected_return],
                mode="markers",
                marker=dict(size=15, symbol=symbol, color=color, line=dict(width=1, color="white")),
                name=name.replace("_", " ").title(),
            )
        )
    fig.update_layout(
        title="Efficient Frontier — risk vs. return",
        xaxis_title="Annualized Volatility (σ)",
        yaxis_title="Annualized Return",
        xaxis_tickformat=".0%",
        yaxis_tickformat=".0%",
        height=480,
        template=_TPL,
    )
    return fig


def weights_figure(result, tickers) -> go.Figure:
    pairs = [(t, w) for t, w in zip(tickers, result.weights, strict=False) if abs(w) > 0.005]
    fig = go.Figure(
        go.Bar(
            x=[t for t, _ in pairs],
            y=[w for _, w in pairs],
            marker_color=NAVY,
            hovertemplate="%{x}: %{y:.1%}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Optimal Weights (allocation by asset)",
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
            line=dict(color=NAVY, width=2),
            name="Equity",
            hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Equity Curve — portfolio value over time",
        xaxis_title="Date",
        yaxis_title="Portfolio Value (USD)",
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
            fillcolor="rgba(192, 57, 43, 0.12)",
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
    rows = [
        ["Total Return", f"{analytics.total_return():.2%}"],
        ["CAGR", f"{analytics.annualized_return():.2%}"],
        ["Sharpe", f"{analytics.sharpe_ratio():.2f}"],
        ["Sortino", f"{analytics.sortino_ratio():.2f}"],
        ["Max Drawdown", f"{analytics.max_drawdown():.2%}"],
        ["Calmar", f"{analytics.calmar_ratio():.2f}"],
        ["Total Trades", f"{len(analytics.trades)}"],
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


def _control_group(legend: str, children: list) -> html.Fieldset:
    return html.Fieldset(
        className="control-group",
        children=[html.Legend(legend, className="control-group__legend"), *children],
    )


def _header() -> html.Header:
    offline = _offline_enabled(False)
    return html.Header(
        className="app-header",
        children=[
            html.Div(
                className="app-header__brand",
                children=[
                    html.Span("QUANT", className="app-header__mark"),
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
                title="Data source mode",
                children=[
                    html.Span(
                        className="status-dot status-dot--offline" if offline else "status-dot"
                    ),
                    html.Span("Offline (sample data)" if offline else "Live data"),
                ],
            ),
        ],
    )


def _sidebar() -> html.Aside:
    return html.Aside(
        className="sidebar",
        children=[
            html.H3("Controls"),
            _control_group(
                "Universe & Window",
                [
                    _field(
                        "Tickers",
                        dcc.Input(id="tickers", value="AAPL, MSFT, JPM, AMZN", type="text"),
                        "Comma-separated symbols, e.g. AAPL, MSFT, JPM",
                    ),
                    _field("Start date", dcc.Input(id="start", value="2020-01-01", type="text")),
                    _field("End date", dcc.Input(id="end", value="2024-01-01", type="text")),
                    _field(
                        "Risk-free rate",
                        dcc.Input(id="rf", value=0.02, type="number", step=0.005, min=0, max=0.2),
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
            html.Div(id="status", className="status-line"),
        ],
    )


def _empty_state(title: str, body: str) -> html.Div:
    return html.Div(
        className="empty-state",
        children=[html.H4(title), html.Div(body)],
    )


def _placeholder_figure(message: str = "No results to display.") -> go.Figure:
    """A clean, axis-free canvas with one centered hint.

    Used as the INITIAL figure for every graph and as the empty/error return
    from the callback, so a chart never shows bare default Plotly ``-1..6`` axes
    before (or instead of) real data. Mirrors the ``.empty-state`` pattern for
    the panels, applied to the plot surface.
    """
    fig = go.Figure(layout=go.Layout(template=_TPL))
    fig.update_layout(
        height=320,
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
                font=dict(size=13, color="#5a6678"),  # --ink-soft: ≥4.5:1 on white
            )
        ],
    )
    return fig


def build_app() -> Dash:
    app = Dash(__name__, title="Portfolio Lab — Optimizer + Backtester")
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
                                color=NAVY,
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
                                                                "Set inputs on the left, then "
                                                                "press Run analysis.",
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
                                                    id="bt-metrics",
                                                    style={"maxWidth": "460px"},
                                                ),
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
            frontier_figure(analysis["frontier"], analysis["results"]),
            weights_figure(chosen, tickers),
            [
                html.Div("Optimized portfolio", className="panel__title"),
                _table(optimization_metric_rows(analysis, key)),
            ],
            headline_cards(analytics),
            equity_figure(analytics),
            drawdown_figure(analytics),
            [
                html.Div("Out-of-sample backtest", className="panel__title"),
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
