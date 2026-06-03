"""Configuration: AnalysisConfig dataclass + argparse CLI + optional JSON config.

Precedence (lowest to highest): dataclass defaults -> JSON config file (--config)
-> explicit CLI flags. No third-party config dependency (JSON only).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, fields
from datetime import datetime

OBJECTIVE_CHOICES = (
    "sharpe",
    "min_vol",
    "risk_parity",
    "sortino",
    "min_cvar",
    "min_cdar",
    "hrp",
    "both",
    "all",
)
EXPORT_CHOICES = ("csv", "json", "both", "none")


@dataclass
class AnalysisConfig:
    tickers: list[str] = field(
        default_factory=lambda: ["AAPL", "GOOGL", "MSFT", "AMZN", "JPM", "GS"]
    )
    start_date: str = "2020-01-01"
    end_date: str = "2024-01-01"
    risk_free_rate: float = 0.02
    objective: str = "both"
    num_portfolios: int = 5000
    monte_carlo_sims: int = 10_000
    monte_carlo_days: int = 252
    initial_value: float = 100_000
    benchmark: str | None = None
    output_dir: str = "results"
    export_format: str = "both"
    no_plots: bool = False
    random_state: int | None = None
    offline: bool = False


def _validate(config: AnalysisConfig) -> AnalysisConfig:
    if not config.tickers:
        raise ValueError("At least one ticker is required")
    for label, value in (("start_date", config.start_date), ("end_date", config.end_date)):
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except (ValueError, TypeError) as err:
            raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from err
    if config.start_date >= config.end_date:
        raise ValueError("start_date must be before end_date")
    if config.num_portfolios <= 0:
        raise ValueError("num_portfolios must be positive")
    if config.objective not in OBJECTIVE_CHOICES:
        raise ValueError(f"objective must be one of {OBJECTIVE_CHOICES}")
    if config.export_format not in EXPORT_CHOICES:
        raise ValueError(f"export_format must be one of {EXPORT_CHOICES}")
    return config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="portfolio-optimizer",
        description="Modern Portfolio Theory optimization engine.",
    )
    p.add_argument("--config", help="Path to a JSON config file (CLI flags override it)")
    p.add_argument("--tickers", nargs="+", help="Ticker symbols, e.g. AAPL MSFT JPM")
    p.add_argument("--start-date", dest="start_date")
    p.add_argument("--end-date", dest="end_date")
    p.add_argument("--risk-free-rate", dest="risk_free_rate", type=float)
    p.add_argument("--objective", choices=OBJECTIVE_CHOICES)
    p.add_argument("--num-portfolios", dest="num_portfolios", type=int)
    p.add_argument("--monte-carlo-sims", dest="monte_carlo_sims", type=int)
    p.add_argument("--monte-carlo-days", dest="monte_carlo_days", type=int)
    p.add_argument("--initial-value", dest="initial_value", type=float)
    p.add_argument("--benchmark", help="Benchmark ticker for beta/alpha, e.g. SPY")
    p.add_argument("--output-dir", dest="output_dir")
    p.add_argument("--export-format", dest="export_format", choices=EXPORT_CHOICES)
    p.add_argument("--no-plots", dest="no_plots", action="store_true", default=None)
    p.add_argument("--random-state", dest="random_state", type=int)
    p.add_argument(
        "--offline",
        dest="offline",
        action="store_true",
        default=None,
        help="Use the bundled price fixture instead of the network (also PORTFOLIO_OFFLINE=1)",
    )
    return p


def parse_args(argv=None) -> AnalysisConfig:
    """Parse CLI args (with optional JSON config) into a validated AnalysisConfig."""
    parser = build_parser()
    namespace = parser.parse_args(argv)

    valid = {f.name for f in fields(AnalysisConfig)}
    values: dict = {}

    # 1) JSON config file (lowest precedence after dataclass defaults)
    if namespace.config:
        with open(namespace.config) as f:
            file_values = json.load(f)
        unknown = set(file_values) - valid
        if unknown:
            raise ValueError(f"Unknown keys in config file: {sorted(unknown)}")
        values.update(file_values)

    # 2) explicit CLI flags override (argparse leaves unset flags as None)
    for key, val in vars(namespace).items():
        if key == "config" or key not in valid:
            continue
        if val is not None:
            values[key] = val

    return _validate(AnalysisConfig(**values))
