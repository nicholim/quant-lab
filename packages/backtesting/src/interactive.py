"""Interactive Bokeh charts for backtest results.

Produces standalone, self-contained HTML files with pan/zoom/hover — useful for
exploring an equity curve and its drawdown without a notebook. Static matplotlib
charts remain available in ``analytics``; this is the interactive alternative.
"""

from bokeh.layouts import column
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure, output_file, save


def _equity_source(analytics) -> ColumnDataSource:
    eq = analytics.equity
    equity = eq["equity"]
    drawdown = equity / equity.cummax() - 1.0
    return ColumnDataSource(
        {
            "date": eq.index,
            "equity": equity.values,
            "cash": eq["cash"].values if "cash" in eq.columns else equity.values * 0,
            "drawdown": drawdown.values,
            "zero": [0.0] * len(eq),
        }
    )


def plot_equity_bokeh(
    analytics, save_path: str = "equity_curve.html", title: str = "Equity Curve"
) -> str:
    """Write an interactive equity-curve HTML file; returns the path."""
    source = _equity_source(analytics)
    # bokeh's type stubs omit `x_axis_type`/`tools`, which figure() accepts at runtime.
    p = figure(  # type: ignore[call-arg]
        x_axis_type="datetime",
        height=420,
        width=960,
        title=title,
        tools="pan,box_zoom,wheel_zoom,reset,save",
    )
    p.line("date", "equity", source=source, line_width=2, color="navy", legend_label="Equity")
    p.add_tools(
        HoverTool(
            tooltips=[("Date", "@date{%F}"), ("Equity", "@equity{$0,0}"), ("Cash", "@cash{$0,0}")],
            formatters={"@date": "datetime"},
            mode="vline",
        )
    )
    p.xaxis.axis_label = "Date"
    p.yaxis.axis_label = "Portfolio Value ($)"
    p.legend.location = "top_left"
    output_file(save_path, title=title)
    save(p)
    return save_path


def plot_performance_bokeh(
    analytics, save_path: str = "performance.html", title: str = "Backtest Performance"
) -> str:
    """Write a two-panel interactive HTML (equity + linked drawdown); returns the path."""
    source = _equity_source(analytics)

    # bokeh's type stubs omit `x_axis_type`/`tools`, which figure() accepts at runtime.
    equity = figure(  # type: ignore[call-arg]
        x_axis_type="datetime",
        height=360,
        width=960,
        title=title,
        tools="pan,box_zoom,wheel_zoom,reset,save",
    )
    equity.line("date", "equity", source=source, line_width=2, color="navy")
    equity.add_tools(
        HoverTool(
            tooltips=[("Date", "@date{%F}"), ("Equity", "@equity{$0,0}")],
            formatters={"@date": "datetime"},
            mode="vline",
        )
    )
    equity.yaxis.axis_label = "Equity ($)"

    # bokeh's type stubs omit `x_axis_type`/`tools`, which figure() accepts at runtime.
    drawdown = figure(  # type: ignore[call-arg]
        x_axis_type="datetime",
        height=220,
        width=960,
        title="Drawdown",
        x_range=equity.x_range,  # linked panning/zoom
        tools="pan,box_zoom,wheel_zoom,reset,save",
    )
    drawdown.varea(
        x="date", y1="drawdown", y2="zero", source=source, fill_color="firebrick", fill_alpha=0.35
    )
    drawdown.line("date", "drawdown", source=source, color="firebrick", line_width=1)
    drawdown.yaxis.axis_label = "Drawdown"
    drawdown.xaxis.axis_label = "Date"

    output_file(save_path, title=title)
    save(column(equity, drawdown))
    return save_path
