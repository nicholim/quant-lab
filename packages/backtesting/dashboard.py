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
from dash import Dash, Input, Output, State, dcc, html
from portfolio_optimization_engine.analysis import run_analysis
from portfolio_optimization_engine.config import AnalysisConfig

from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.datastore import DataStore
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import OptimizationRebalanceStrategy

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
        title="Efficient Frontier",
        xaxis_title="Annualized Volatility",
        yaxis_title="Annualized Return",
        xaxis_tickformat=".0%",
        yaxis_tickformat=".0%",
        height=480,
        template="plotly_white",
    )
    return fig


def weights_figure(result, tickers) -> go.Figure:
    pairs = [(t, w) for t, w in zip(tickers, result.weights, strict=False) if abs(w) > 0.005]
    fig = go.Figure(
        go.Bar(x=[t for t, _ in pairs], y=[w for _, w in pairs], marker_color="#4c78a8")
    )
    fig.update_layout(
        title="Optimal Weights", yaxis_tickformat=".0%", height=360, template="plotly_white"
    )
    return fig


def equity_figure(analytics) -> go.Figure:
    eq = analytics.equity
    fig = go.Figure(
        go.Scatter(
            x=eq.index,
            y=eq["equity"],
            mode="lines",
            line=dict(color="#1f3b73", width=2),
            name="Equity",
        )
    )
    fig.update_layout(
        title="Backtest Equity Curve",
        yaxis_title="Portfolio Value ($)",
        height=380,
        template="plotly_white",
    )
    return fig


def drawdown_figure(analytics) -> go.Figure:
    eq = analytics.equity["equity"]
    dd = eq / eq.cummax() - 1.0
    fig = go.Figure(
        go.Scatter(x=eq.index, y=dd, fill="tozeroy", line=dict(color="firebrick"), name="Drawdown")
    )
    fig.update_layout(title="Drawdown", yaxis_tickformat=".0%", height=260, template="plotly_white")
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
    )
    return run_analysis(config), OBJECTIVE_TO_KEY[objective]


def backtest_optimized(tickers, start, end, objective, store=None, allow_short=False):
    """Walk-forward rebalancing backtest of the chosen objective via the engine.

    ``allow_short`` (default False = long-only, unchanged) threads straight into
    ``Portfolio(allow_short=...)``. The optimizer emits long-only target weights,
    so a short-enabled portfolio only matters once a rebalance reduces a holding
    below a prior allocation; defaulting it off preserves prior behavior.
    """
    data = YFinanceDataHandler(tickers, start, end, store=store)
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

_INPUT = {"width": "100%", "padding": "6px", "marginBottom": "10px"}


def build_app() -> Dash:
    app = Dash(__name__, title="Portfolio Optimizer + Backtester")
    app.layout = html.Div(
        style={"display": "flex", "fontFamily": "system-ui, sans-serif"},
        children=[
            # Sidebar
            html.Div(
                style={
                    "width": "280px",
                    "padding": "20px",
                    "background": "#f4f6f8",
                    "minHeight": "100vh",
                },
                children=[
                    html.H3("Portfolio Lab"),
                    html.Label("Tickers (comma-separated)"),
                    dcc.Input(
                        id="tickers", value="AAPL, MSFT, JPM, AMZN", type="text", style=_INPUT
                    ),
                    html.Label("Start date"),
                    dcc.Input(id="start", value="2020-01-01", type="text", style=_INPUT),
                    html.Label("End date"),
                    dcc.Input(id="end", value="2024-01-01", type="text", style=_INPUT),
                    html.Label("Risk-free rate"),
                    dcc.Input(id="rf", value=0.02, type="number", step=0.005, style=_INPUT),
                    html.Label("Objective"),
                    dcc.Dropdown(
                        id="objective",
                        value="sharpe",
                        clearable=False,
                        style={"marginBottom": "14px"},
                        options=[
                            {
                                "label": k.replace("_", " ").upper()
                                if k == "hrp"
                                else k.replace("_", " ").title(),
                                "value": k,
                            }
                            for k in OBJECTIVE_TO_KEY
                        ],
                    ),
                    dcc.Checklist(
                        id="allow-short",
                        options=[{"label": " Allow short selling", "value": "short"}],
                        value=[],  # default off = long-only (unchanged behavior)
                        style={"marginBottom": "14px"},
                    ),
                    html.Button(
                        "Run",
                        id="run",
                        n_clicks=0,
                        style={
                            "width": "100%",
                            "padding": "10px",
                            "background": "#1f3b73",
                            "color": "white",
                            "border": "none",
                            "cursor": "pointer",
                        },
                    ),
                    html.Div(
                        id="status",
                        style={"marginTop": "12px", "color": "#666", "fontSize": "13px"},
                    ),
                ],
            ),
            # Main
            html.Div(
                style={"flex": 1, "padding": "20px"},
                children=[
                    dcc.Loading(
                        type="default",
                        children=dcc.Tabs(
                            [
                                dcc.Tab(
                                    label="Optimization",
                                    children=[
                                        html.Div(
                                            style={"display": "flex", "gap": "20px"},
                                            children=[
                                                html.Div(
                                                    dcc.Graph(id="frontier"), style={"flex": 2}
                                                ),
                                                html.Div(
                                                    id="opt-metrics",
                                                    style={"flex": 1, "paddingTop": "40px"},
                                                ),
                                            ],
                                        ),
                                        dcc.Graph(id="weights"),
                                    ],
                                ),
                                dcc.Tab(
                                    label="Backtest",
                                    children=[
                                        html.Div(
                                            id="bt-metrics",
                                            style={"maxWidth": "420px", "paddingTop": "10px"},
                                        ),
                                        dcc.Graph(id="equity"),
                                        dcc.Graph(id="drawdown"),
                                    ],
                                ),
                            ]
                        ),
                    ),
                ],
            ),
        ],
    )

    @app.callback(
        Output("frontier", "figure"),
        Output("weights", "figure"),
        Output("opt-metrics", "children"),
        Output("equity", "figure"),
        Output("drawdown", "figure"),
        Output("bt-metrics", "children"),
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
        try:
            store = DataStore("data/dashboard.duckdb")
            try:
                analysis, key = optimize(tickers, start, end, float(rf), objective)
                analytics = backtest_optimized(
                    tickers, start, end, objective, store=store, allow_short=allow_short
                )
            finally:
                store.close()
        except Exception as exc:  # surface errors in the UI rather than 500ing
            empty = go.Figure()
            return empty, empty, "", empty, empty, "", f"Error: {exc}"

        chosen = analysis["results"][key]
        return (
            frontier_figure(analysis["frontier"], analysis["results"]),
            weights_figure(chosen, tickers),
            [html.H4("Optimized portfolio"), _table(optimization_metric_rows(analysis, key))],
            equity_figure(analytics),
            drawdown_figure(analytics),
            [html.H4("Out-of-sample backtest"), _table(backtest_metric_rows(analytics))],
            f"Done: optimized {len(tickers)} assets and backtested '{objective}'"
            f"{' (short selling enabled)' if allow_short else ''}.",
        )

    return app


app = build_app()
server = app.server  # WSGI entry point for gunicorn: `gunicorn dashboard:server`


if __name__ == "__main__":
    # Local dev server. In production use gunicorn (see DEPLOY.md).
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 8050)), debug=True)
