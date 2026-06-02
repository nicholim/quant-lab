"""Export optimization results and performance metrics to CSV / JSON.

Generated artifacts are written under an output directory (default ``results/``,
which is gitignored). JSON converts numpy scalar/array types to native Python so
``json.dump`` can serialize them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def ensure_output_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _to_jsonable(obj):
    """Recursively convert numpy types / arrays / dataclasses to JSON-native."""
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, float) and (np.isinf(obj) or np.isnan(obj)):
        return None  # JSON has no inf/nan; represent as null
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    return obj


def results_to_dataframe(results: dict, tickers: list[str]) -> pd.DataFrame:
    """Weights matrix: rows = ticker, columns = portfolio name."""
    data = {name: dict(zip(tickers, res.weights, strict=False)) for name, res in results.items()}
    return pd.DataFrame(data).reindex(tickers)


def summary_to_dataframe(results: dict, metrics: dict | None) -> pd.DataFrame:
    """Per-portfolio summary: optimizer stats plus performance metrics."""
    rows = {}
    for name, res in results.items():
        row = {
            "expected_return": res.expected_return,
            "volatility": res.volatility,
            "sharpe_ratio": res.sharpe_ratio,
            "sortino_ratio": res.sortino_ratio,
            "cvar": res.cvar,
            "objective": res.objective,
        }
        if metrics and name in metrics:
            row.update(metrics[name].as_dict())
        rows[name] = row
    return pd.DataFrame(rows).T


def export_results_csv(results, tickers, metrics, output_dir, stamp: str) -> list[Path]:
    out = ensure_output_dir(output_dir)
    weights_path = out / f"portfolio_weights_{stamp}.csv"
    summary_path = out / f"portfolio_summary_{stamp}.csv"
    results_to_dataframe(results, tickers).to_csv(weights_path)
    summary_to_dataframe(results, metrics).to_csv(summary_path)
    return [weights_path, summary_path]


def export_results_json(
    config, results, tickers, metrics, mc_summary, output_dir, stamp: str
) -> Path:
    out = ensure_output_dir(output_dir)
    json_path = out / f"portfolio_results_{stamp}.json"

    portfolios = {}
    for name, res in results.items():
        portfolios[name] = {
            "weights": dict(zip(tickers, res.weights, strict=False)),
            "expected_return": res.expected_return,
            "volatility": res.volatility,
            "sharpe_ratio": res.sharpe_ratio,
            "sortino_ratio": res.sortino_ratio,
            "cvar": res.cvar,
            "objective": res.objective,
            "metrics": metrics.get(name).as_dict() if metrics and name in metrics else None,
        }

    document = {
        "generated_at": stamp,
        "config": asdict(config)
        if is_dataclass(config) and not isinstance(config, type)
        else config,
        "portfolios": portfolios,
        "monte_carlo": mc_summary,
    }
    with open(json_path, "w") as f:
        json.dump(_to_jsonable(document), f, indent=2)
    return json_path


def export(config, results, tickers, metrics, mc_summary) -> list[Path]:
    """Dispatch on ``config.export_format`` ("csv" | "json" | "both" | "none")."""
    fmt = getattr(config, "export_format", "both")
    if fmt == "none":
        return []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = getattr(config, "output_dir", "results")
    paths: list[Path] = []
    if fmt in ("csv", "both"):
        paths.extend(export_results_csv(results, tickers, metrics, output_dir, stamp))
    if fmt in ("json", "both"):
        paths.append(
            export_results_json(config, results, tickers, metrics, mc_summary, output_dir, stamp)
        )
    return paths
