import json
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine import export as exp
from portfolio_optimization_engine.metrics import compute_metrics
from portfolio_optimization_engine.optimizer import PortfolioResult


@dataclass
class FakeConfig:
    tickers: list
    output_dir: str
    export_format: str = "both"


@pytest.fixture
def sample():
    tickers = ["A", "B", "C"]
    results = {
        "max_sharpe": PortfolioResult(
            weights=np.array([0.5, 0.3, 0.2]),
            expected_return=0.12,
            volatility=0.18,
            sharpe_ratio=0.55,
            sortino_ratio=0.8,
            cvar=0.03,
            objective="sharpe",
        ),
        "min_vol": PortfolioResult(
            weights=np.array([0.2, 0.5, 0.3]),
            expected_return=0.08,
            volatility=0.10,
            sharpe_ratio=0.60,
            sortino_ratio=0.9,
            cvar=0.02,
            objective="min_volatility",
        ),
    }
    rng = np.random.default_rng(0)
    daily = pd.Series(rng.normal(0.0005, 0.01, 300))
    metrics = {name: compute_metrics(daily) for name in results}
    mc_summary = {"portfolio": "max_sharpe", "var_95": 12000.0, "cvar_95": 15000.0}
    return tickers, results, metrics, mc_summary


def test_weights_dataframe_shape(sample):
    tickers, results, _, _ = sample
    df = exp.results_to_dataframe(results, tickers)
    assert list(df.index) == tickers
    assert set(df.columns) == {"max_sharpe", "min_vol"}
    assert df["max_sharpe"].sum() == pytest.approx(1.0)


def test_summary_dataframe_has_metrics(sample):
    _, results, metrics, _ = sample
    df = exp.summary_to_dataframe(results, metrics)
    assert "max_drawdown" in df.columns
    assert "sharpe_ratio" in df.columns
    assert set(df.index) == {"max_sharpe", "min_vol"}


def test_csv_export_roundtrip(tmp_path, sample):
    tickers, results, metrics, _ = sample
    paths = exp.export_results_csv(results, tickers, metrics, tmp_path, "stamp")
    assert len(paths) == 2
    for p in paths:
        assert p.exists()
    weights = pd.read_csv(paths[0], index_col=0)
    assert weights.loc["A", "max_sharpe"] == pytest.approx(0.5)


def test_json_export_roundtrip(tmp_path, sample):
    tickers, results, metrics, mc = sample
    cfg = FakeConfig(tickers=tickers, output_dir=str(tmp_path))
    path = exp.export_results_json(cfg, results, tickers, metrics, mc, tmp_path, "stamp")
    assert path.exists()
    doc = json.loads(path.read_text())
    assert doc["portfolios"]["max_sharpe"]["weights"]["A"] == pytest.approx(0.5)
    assert doc["monte_carlo"]["var_95"] == 12000.0
    assert doc["portfolios"]["max_sharpe"]["metrics"] is not None


def test_numpy_types_serialize(tmp_path, sample):
    tickers, results, metrics, mc = sample
    cfg = FakeConfig(tickers=tickers, output_dir=str(tmp_path))
    # should not raise despite np.float64 / np.ndarray inputs
    path = exp.export_results_json(cfg, results, tickers, metrics, mc, tmp_path, "s")
    json.loads(path.read_text())  # valid JSON


def test_dispatch_none_returns_empty(tmp_path, sample):
    tickers, results, metrics, mc = sample
    cfg = FakeConfig(tickers=tickers, output_dir=str(tmp_path), export_format="none")
    assert exp.export(cfg, results, tickers, metrics, mc) == []


def test_dispatch_both_writes_three_files(tmp_path, sample):
    tickers, results, metrics, mc = sample
    cfg = FakeConfig(tickers=tickers, output_dir=str(tmp_path), export_format="both")
    paths = exp.export(cfg, results, tickers, metrics, mc)
    assert len(paths) == 3
    assert all(p.exists() for p in paths)


def test_inf_metric_becomes_null(tmp_path):
    # Calmar inf (no drawdown) should serialize as null, not crash
    tickers = ["A"]
    results = {
        "p": PortfolioResult(
            np.array([1.0]), 0.1, 0.1, 1.0, sortino_ratio=float("inf"), cvar=0.0, objective="x"
        )
    }
    metrics = {"p": compute_metrics(np.full(252, 0.001))}  # calmar = inf
    cfg = FakeConfig(tickers=tickers, output_dir=str(tmp_path))
    path = exp.export_results_json(cfg, results, tickers, metrics, {}, tmp_path, "s")
    doc = json.loads(path.read_text())
    assert doc["portfolios"]["p"]["metrics"]["calmar_ratio"] is None
