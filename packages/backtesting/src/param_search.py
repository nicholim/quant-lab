"""Parameter optimization: grid-search a strategy and visualize the surface.

Each parameter combination is a full backtest, persisted to DuckDB like any
other run (so results are queryable via SQL and survive across sessions). The
DuckDB OHLCV cache means only the first combination hits the network.
"""

import itertools
from collections.abc import Callable

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns

from .backtest import Backtest
from .data_handler import DataHandler
from .datastore import DataStore
from .execution import SimulatedExecution
from .portfolio import Portfolio
from .strategy import Strategy


def grid_search(
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list],
    data_factory: Callable[[], DataHandler],
    store: DataStore | None = None,
    capital: float = 100_000,
    benchmark: str | None = None,
    commission_pct: float = 0.001,
    slippage_pct: float = 0.0005,
    name_prefix: str = "grid",
    persist: bool = True,
) -> pd.DataFrame:
    """Backtest ``strategy_factory(**combo)`` over the Cartesian product of params.

    Args:
        strategy_factory: called with each parameter combo as kwargs, returns a Strategy.
        param_grid: mapping of constructor kwarg name -> list of values to sweep.
        data_factory: zero-arg callable returning a FRESH DataHandler per run.
        store: optional DuckDB store for caching + run persistence.
        benchmark: optional benchmark ticker for beta/alpha.
        persist: if False, runs use the store's OHLCV cache but are NOT recorded
            as backtest_runs (used by walk-forward to avoid polluting the table).

    Returns:
        One row per combination: the parameter values plus sharpe, sortino,
        total_return, max_drawdown, calmar, and total_trades.
    """
    keys = list(param_grid)
    rows = []
    for combo_values in itertools.product(*(param_grid[k] for k in keys)):
        combo = dict(zip(keys, combo_values, strict=False))
        strategy = strategy_factory(**combo)
        label = (
            (f"{name_prefix} " + ", ".join(f"{k}={v}" for k, v in combo.items())) if persist else ""
        )

        analytics = Backtest(
            data_factory(),
            strategy,
            Portfolio(initial_capital=capital),
            SimulatedExecution(commission_pct=commission_pct, slippage_pct=slippage_pct),
            strategy_name=label,
            store=store,
            benchmark=benchmark,
        ).run()

        row = dict(combo)
        row.update(
            {
                "sharpe": analytics.sharpe_ratio(),
                "sortino": analytics.sortino_ratio(),
                "total_return": analytics.total_return(),
                "max_drawdown": analytics.max_drawdown(),
                "calmar": analytics.calmar_ratio(),
                "total_trades": len(analytics.trades),
            }
        )
        rows.append(row)

    return pd.DataFrame(rows)


def walk_forward(
    strategy_factory: Callable[..., Strategy],
    param_grid: dict[str, list],
    data_factory: Callable[[str, str], DataHandler],
    start: str,
    end: str,
    is_months: int = 24,
    oos_months: int = 12,
    metric: str = "sharpe",
    store: DataStore | None = None,
    capital: float = 100_000,
    benchmark: str | None = None,
    commission_pct: float = 0.001,
    slippage_pct: float = 0.0005,
) -> pd.DataFrame:
    """Rolling walk-forward optimization (out-of-sample parameter selection).

    On each step, grid-search the in-sample window, pick the best params by
    ``metric`` (higher is better), then evaluate ONLY those params on the
    following out-of-sample window. The window slides forward by ``oos_months``.
    Reporting out-of-sample performance avoids the overfitting of a single
    full-sample grid search.

    Args:
        data_factory: callable (start, end) -> fresh DataHandler for that range.
        start, end: overall calendar bounds (YYYY-MM-DD).
        is_months / oos_months: in-sample and out-of-sample window lengths.

    Returns:
        One row per OOS window: window bounds, the chosen params, and the
        out-of-sample sharpe / total_return / max_drawdown / total_trades.
    """

    def _fmt(ts):
        return ts.strftime("%Y-%m-%d")

    def _original(key, value):
        """Recover the original grid value (a pandas row upcasts ints to float)."""
        for g in param_grid[key]:
            if g == value:
                return g  # preserves the original int/float/str type
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return value

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    is_off, oos_off = pd.DateOffset(months=is_months), pd.DateOffset(months=oos_months)

    rows = []
    is_start = start_ts
    while True:
        is_end = is_start + is_off
        oos_start, oos_end = is_end, min(is_end + oos_off, end_ts)
        if oos_start >= end_ts:
            break

        def _is_data_factory(s: pd.Timestamp = is_start, e: pd.Timestamp = is_end) -> DataHandler:
            return data_factory(_fmt(s), _fmt(e))

        is_results = grid_search(
            strategy_factory,
            param_grid,
            data_factory=_is_data_factory,
            store=store,
            capital=capital,
            commission_pct=commission_pct,
            slippage_pct=slippage_pct,
            persist=False,
        )
        if is_results.empty or is_results[metric].isna().all():
            is_start = is_start + oos_off
            continue

        best = is_results.sort_values(metric, ascending=False).iloc[0]
        best_params = {k: _original(k, best[k]) for k in param_grid}

        oos = Backtest(
            data_factory(_fmt(oos_start), _fmt(oos_end)),
            strategy_factory(**best_params),
            Portfolio(initial_capital=capital),
            SimulatedExecution(commission_pct=commission_pct, slippage_pct=slippage_pct),
            strategy_name=f"WF OOS {_fmt(oos_start)}",
            store=store,
            benchmark=benchmark,
        ).run()

        rows.append(
            {
                "is_start": _fmt(is_start),
                "is_end": _fmt(is_end),
                "oos_start": _fmt(oos_start),
                "oos_end": _fmt(oos_end),
                **best_params,
                "oos_sharpe": oos.sharpe_ratio(),
                "oos_return": oos.total_return(),
                "oos_max_drawdown": oos.max_drawdown(),
                "oos_trades": len(oos.trades),
            }
        )
        is_start = is_start + oos_off

    return pd.DataFrame(rows)


def heatmap(
    results: pd.DataFrame,
    x_param: str,
    y_param: str,
    metric: str = "sharpe",
    save_path: str | None = None,
) -> pd.DataFrame:
    """Pivot grid-search results over two parameters and plot ``metric`` as a heatmap.

    Returns the pivot table (also useful headless).
    """
    pivot = results.pivot(index=y_param, columns=x_param, values=metric)
    fig, ax = plt.subplots(figsize=(1.5 * len(pivot.columns) + 4, 1.2 * len(pivot.index) + 3))
    sns.heatmap(pivot, annot=True, fmt=".2f", cmap="RdYlGn", center=0, ax=ax, linewidths=0.5)
    ax.set_title(f"{metric} across {x_param} x {y_param}", fontsize=13)
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.show()
    return pivot
