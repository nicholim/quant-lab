# Portfolio Optimization Engine

[![CI](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

A focused, dependency-light Modern Portfolio Theory optimizer: efficient frontier, six
optimization objectives, flexible weight constraints, Monte Carlo risk projection, and a
standalone performance-metrics module — usable as a library, a CLI, or a demo HTTP API.

### Why this exists

Built on `numpy` / `pandas` / `scipy` only (no `cvxpy` solver stack), the engine stays small
and easy to read while covering the objectives most asset-allocation work needs. Its
`metrics` module is deliberately I/O-free and importable on its own, which is why the sibling
[`backtesting-framework`](../backtesting-framework) depends one-way on this package
(`OptimizationRebalanceStrategy` drives the optimizer; Sharpe/Sortino/drawdown definitions are
a shared source of truth). It is a clear MPT reference implementation and a teaching-grade
codebase, not an institutional solver framework. See ["vs. the popular tools"](#vs-the-popular-tools).

## Architecture

```mermaid
graph TD
    CLI[CLI / config.py] --> RUN[analysis.run_analysis]
    YF[Market Data<br/>yfinance] --> CACHE[data_cache<br/>local pickle]
    CACHE --> RET[Returns &amp; Covariance<br/>252-day annualized]
    RUN --> RET
    CON[Constraints<br/>bounds / groups / shorting] --> OPT
    RET --> OPT[PortfolioOptimizer<br/>scipy SLSQP + R-U LP]
    OPT --> OBJ{Objectives}
    OBJ --> O1[Max Sharpe]
    OBJ --> O2[Min Vol]
    OBJ --> O3[Risk Parity]
    OBJ --> O4[Max Sortino]
    OBJ --> O5[Min CVaR]
    OBJ --> O6[Target ret/vol]
    RET --> MET[metrics.py<br/>CAGR · DD · Sortino · Calmar<br/>Omega · beta/alpha]
    OPT --> MC[Monte Carlo<br/>VaR / CVaR]
    OPT --> VIZ[Visualization]
    RUN --> EXP[export<br/>CSV / JSON]
    MET -.->|importable standalone| EXT([external backtest framework])

    style YF fill:#3b82f6,color:#fff
    style CACHE fill:#3b82f6,color:#fff
    style OPT fill:#06b6d4,color:#fff
    style OBJ fill:#06b6d4,color:#fff
    style MET fill:#10b981,color:#fff
    style MC fill:#ef4444,color:#fff
    style EXT fill:#9467bd,color:#fff
```

## Features

- **Efficient Frontier** — Generate and visualize the risk-return tradeoff across thousands of random portfolios
- **Multiple optimization objectives:**
  - **Max Sharpe** — tangency portfolio maximizing risk-adjusted return (SLSQP)
  - **Min Volatility** — global minimum variance portfolio
  - **Risk Parity** — equalizes each asset's risk contribution
  - **Max Sortino** — maximizes return per unit of downside deviation
  - **Min CVaR** — minimizes historical expected shortfall via the Rockafellar–Uryasev linear program
  - **Target-based** — max return for a target volatility, or min volatility for a target return
- **Flexible constraints** — per-asset and per-group min/max weight bounds, optional shorting
- **Performance metrics** — CAGR, max drawdown, Sortino, Calmar, Omega, plus beta/alpha vs a benchmark
- **Monte Carlo Simulation** — Project portfolio value using geometric Brownian motion with VaR and CVaR estimation
- **Result export** — write results and metrics to CSV / JSON for downstream tools (e.g. a backtest framework)
- **Correlation, weights, drawdown & returns visualizations**

## Technical Highlights

- **Mathematically correct** — Proper Itô calculus formulation for GBM drift term `(μ - ½σ²)dt`, annualized covariance via 252 trading-day convention
- **Constrained optimization** — SLSQP with weight-sum and non-negativity constraints, convergence validation on every solve
- **Reproducible results** — All random processes accept `random_state` parameter for deterministic backtesting
- **VaR & CVaR** — Both parametric risk measures computed from full simulation distribution, not approximation
- **Dirichlet sampling** — Efficient frontier uses Dirichlet distribution to guarantee valid portfolio weights (sum to 1, all non-negative)

## vs. the popular tools

Honest positioning against the well-known Python portfolio libraries. Capabilities below
reflect each project's documented behavior — this engine intentionally does **less** than the
big frameworks, but stays small, readable, and dependency-light.

| Capability | **This engine** | PyPortfolioOpt | riskfolio-lib | skfolio | cvxpy |
|---|:---:|:---:|:---:|:---:|:---:|
| Max Sharpe / min volatility | Yes | Yes | Yes | Yes | DIY |
| Risk parity | Yes (equal risk contribution) | HRP only | Yes (RP + HRP/HERC) | Yes (RP + clustering) | DIY |
| Sortino / semivariance | Yes (max Sortino) | Yes (semivariance frontier) | Yes | Yes | DIY |
| CVaR / expected shortfall | Yes (Rockafellar–Uryasev LP) | Yes (`EfficientCVaR`) | Yes (many tail measures) | Yes | DIY |
| Per-asset & group weight bounds, shorting | Yes | Yes | Yes | Yes | DIY |
| Black–Litterman / factor / clustering models | No | Black–Litterman | Extensive | Extensive (sklearn estimators) | DIY |
| Walk-forward / purged cross-validation | No | No | Limited | Yes (its headline feature) | No |
| Monte Carlo VaR/CVaR projection | Yes (GBM) | No | No | No | No |
| Solver stack | scipy `SLSQP` + `linprog` | cvxpy | cvxpy | cvxpy | (is the solver) |
| Core dependencies | numpy/pandas/scipy | + cvxpy | + cvxpy | + sklearn/cvxpy | cvxpy |

**What this engine does well:** a compact, readable MPT reference — six objectives, flexible
constraints, an empirical-CVaR LP, and a self-contained `metrics` + Monte Carlo layer with no
heavy solver dependency. The efficient frontier here is a **Dirichlet random-portfolio cloud**
for visualization, not a swept convex frontier.

**What it intentionally does not do:** Black–Litterman, factor models, hierarchical/clustering
allocation, machine-learning estimators, or leakage-safe cross-validation. For those, reach for
[`riskfolio-lib`](https://github.com/dcajasn/Riskfolio-Lib) or
[`skfolio`](https://github.com/skfolio/skfolio); for a battle-tested classical toolkit, see
[`PyPortfolioOpt`](https://github.com/robertmartin8/PyPortfolioOpt); to hand-roll arbitrary convex
objectives, use [`cvxpy`](https://www.cvxpy.org/) directly.

**Who it's for:** anyone who wants a transparent MPT implementation to read, extend, or embed
(e.g. as the rebalancing engine behind a backtester) without pulling in a full convex-solver
stack. For ecosystem context, see [`awesome-quant`](https://github.com/wilsonfreitas/awesome-quant).

## Tech Stack

- **Python 3.10+**
- **pandas** — Data manipulation and time series
- **NumPy** — Numerical computations
- **scipy** — Constrained optimization (SLSQP)
- **matplotlib / seaborn** — Visualization
- **yfinance** — Historical market data

## Quick Start

```bash
git clone https://github.com/nicholim/quant-lab.git
cd portfolio-optimization-engine

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
pip install -e .            # makes the package importable from anywhere

python main.py              # full analysis on the default tickers (needs network)
python examples/quickstart_offline.py   # runnable end-to-end, no network
```

The offline example ([`examples/quickstart_offline.py`](examples/quickstart_offline.py)) injects
a synthetic returns matrix and walks the full workflow — all six objectives, the efficient
frontier, the metrics module, and a Monte Carlo VaR/CVaR projection — without any Yahoo Finance
call, so it always reproduces.

## Command-line usage

`main.py` is a thin CLI over `run_analysis`. Inputs are configurable via flags
(or a JSON config file); flags override file values.

```bash
# Run every objective, compute beta/alpha vs SPY, export CSV+JSON, skip plots
python main.py \
  --tickers AAPL MSFT JPM \
  --start-date 2021-01-01 --end-date 2023-01-01 \
  --objective all --benchmark SPY \
  --export-format both --no-plots

# From a config file
python main.py --config my_run.json
```

Key flags: `--objective {sharpe,min_vol,risk_parity,sortino,min_cvar,both,all}`,
`--benchmark TICKER`, `--export-format {csv,json,both,none}`, `--output-dir`,
`--num-portfolios`, `--risk-free-rate`, `--random-state`, `--no-plots`, `--offline`.
Exports are written to `--output-dir` (default `results/`, gitignored).

### Resilient data layer (cache / retry / offline)

All network access lives in one place — `portfolio_optimization_engine/data_cache.py`.
Both the optimizer's price fetch and the analysis layer's benchmark fetch route through
`fetch_close_prices`, which adds, on top of the existing on-disk pickle cache (primary;
keyed by tickers/dates, overridable via `POE_CACHE_DIR`):

- **Retry + exponential backoff** on transient / rate-limit / timeout errors from yfinance.
- A typed `MarketDataError` on final failure (no raw network exception leaks out).
- A **graceful offline fallback**: pass `--offline` (CLI), `offline=True` (library), or set
  `PORTFOLIO_OFFLINE=1` to serve a small bundled price fixture
  (`portfolio_optimization_engine/data/sample_prices.csv`, tickers
  `AAPL/MSFT/GOOGL/AMZN/SPY`) instead of hitting the network — so demos never hard-fail on
  restricted cloud egress. Offline results are not written to the on-disk cache.

```bash
# Fully offline demo (no network), beta/alpha vs the bundled SPY fixture
python main.py --tickers AAPL MSFT GOOGL --benchmark SPY --offline --no-plots
```

## Example Output

```
============================================================
Portfolio Optimization Engine
============================================================

Fetching data for AAPL, GOOGL, MSFT, AMZN, JPM, GS...
Generating efficient frontier (5000 portfolios)...
Optimizing portfolios...

------------------------------------------------------------
MAX SHARPE RATIO PORTFOLIO
------------------------------------------------------------
  Expected Return:  28.01%
  Volatility:       30.02%
  Sharpe Ratio:     0.87
  Weights:
    AAPL  : 53.63%
    AMZN  : 12.42%
    GS    : 33.96%

------------------------------------------------------------
MINIMUM VOLATILITY PORTFOLIO
------------------------------------------------------------
  Expected Return:  20.77%
  Volatility:       27.25%
  Sharpe Ratio:     0.69

Running Monte Carlo simulation (10,000 paths)...

  1-Year VaR (95%):  $22,528
  1-Year CVaR (95%): $31,335
```

## Usage

```python
from portfolio_optimization_engine.optimizer import PortfolioOptimizer
from portfolio_optimization_engine.monte_carlo import MonteCarloSimulator

# Initialize optimizer with tickers and date range
optimizer = PortfolioOptimizer(
    tickers=["AAPL", "GOOGL", "MSFT", "JPM", "GS"],
    start_date="2020-01-01",
    end_date="2024-01-01",
    risk_free_rate=0.02,
)
optimizer.fetch_data()
optimizer.calculate_returns()

# Generate efficient frontier
frontier = optimizer.efficient_frontier(num_portfolios=5000)

# Find optimal portfolios — every objective shares the same constraint kwargs
max_sharpe = optimizer.optimize_sharpe()
min_vol = optimizer.optimize_min_volatility()
risk_parity = optimizer.optimize_risk_parity()
sortino = optimizer.optimize_sortino()
min_cvar = optimizer.optimize_min_cvar(confidence=0.95)

# Flexible constraints: cap AAPL at 30%, limit a tech group to 50%, allow shorting
constrained = optimizer.optimize_sharpe(
    max_weights={"AAPL": 0.30},
    groups={"tech": (["AAPL", "MSFT"], 0.0, 0.50)},
)

# Target-based optimization
risk_budgeted = optimizer.optimize_max_return_target_vol(target_vol=0.20)

print(f"Max Sharpe: Return={max_sharpe.expected_return:.2%}, Vol={max_sharpe.volatility:.2%}, Sharpe={max_sharpe.sharpe_ratio:.2f}")

# Standalone performance metrics (importable by a separate backtest framework)
from portfolio_optimization_engine.metrics import compute_metrics
from portfolio_optimization_engine.analysis import compute_portfolio_returns

daily = compute_portfolio_returns(optimizer.returns, max_sharpe.weights)
m = compute_metrics(daily, risk_free_rate=0.02)
print(f"CAGR={m.cagr:.2%}, MaxDD={m.max_drawdown:.2%}, Sortino={m.sortino_ratio:.2f}, Calmar={m.calmar_ratio:.2f}")

# Monte Carlo simulation on optimal portfolio
mc = MonteCarloSimulator(
    expected_return=max_sharpe.expected_return,
    volatility=max_sharpe.volatility,
    initial_value=100_000,
)
mc.simulate(num_simulations=10_000, num_days=252)
print(f"VaR 95%: ${mc.calculate_var(0.95):,.0f}")
print(f"CVaR 95%: ${mc.calculate_cvar(0.95):,.0f}")
```

## Project Structure

```
portfolio-optimization-engine/
├── main.py                 # Thin CLI wrapper (parse args → run_analysis → report/export/plot)
├── pyproject.toml          # Installable package (pip install -e .)
├── requirements.txt
├── requirements-api.txt    # Extra deps for the optional FastAPI demo (adds to requirements.txt)
├── api/app.py              # Thin FastAPI demo wrapper (calls the public API; no logic duplicated)
├── render.yaml             # Render Blueprint for the FastAPI demo
├── examples/               # Runnable workflows (quickstart_offline.py — no network)
├── tests/                  # pytest suite (147 tests, ~95% coverage)
└── portfolio_optimization_engine/   # importable package
    ├── optimizer.py         # PortfolioOptimizer (frontier, all objectives, flexible constraints)
    ├── data_cache.py        # On-disk price cache (avoids repeat yfinance downloads)
    ├── monte_carlo.py       # MonteCarloSimulator (GBM, VaR, CVaR)
    ├── metrics.py           # Standalone performance metrics (CAGR, drawdown, Sortino, Calmar, …)
    ├── config.py            # AnalysisConfig + argparse CLI + JSON config
    ├── analysis.py          # run_analysis orchestration + console report
    ├── export.py            # CSV / JSON result export
    └── visualization.py     # Plotting (frontier, correlation, weights, returns, drawdown)
```

## Deploy (Render) — optional FastAPI demo

A thin, optional FastAPI wrapper (`api/app.py`) exposes the optimizer as a demo
HTTP API. It does **not** change the library or CLI — it only *calls* the existing
`PortfolioOptimizer` public API, so the cross-repo contract is unaffected.

Endpoints:

| Method | Path          | Purpose                                                       |
|--------|---------------|---------------------------------------------------------------|
| GET    | `/health`     | Liveness probe (Render health check).                         |
| GET    | `/objectives` | List supported objectives.                                    |
| POST   | `/optimize`   | Body: `{tickers, returns (T×n daily), objective, risk_free_rate}` → weights + metrics. |

Supported objectives: `sharpe`, `min_volatility`, `risk_parity`, `sortino`, `min_cvar`.

Run locally:

```bash
pip install -r requirements-api.txt && pip install -e .
uvicorn api.app:app --reload          # docs at http://127.0.0.1:8000/docs
```

Deploy on Render (Blueprint — `render.yaml` is committed):

1. Push this repo to GitHub.
2. Render dashboard → **New → Blueprint** → select this repo. Render reads `render.yaml`:
   - build: `pip install -r requirements-api.txt && pip install -e .`
   - start: `uvicorn api.app:app --host 0.0.0.0 --port $PORT`
   - health check: `/health`, free plan, `autoDeploy: false` (deploy on demand).
3. Click **Apply** to create the service. No secrets/env vars are required (the demo
   takes returns in the request body, so no Yahoo Finance / network access is needed).

## Testing & quality

```bash
pip install -e ".[test]"
pytest                       # 147 tests, branch coverage gated at 90% (~95% actual)
ruff check . && ruff format --check .
```

The suite (`tests/`) covers the optimizer objectives, all constraint shapes
(per-asset/group bounds, shorting, target vol/return feasibility), degenerate/singular-covariance
inputs, the config/CLI/JSON precedence, export, and Monte Carlo guards. Two contracts are
explicitly test-enforced rather than benchmarked against an external library:

- **Metrics parity** — `metrics.py` (Sharpe/Sortino/drawdown/…) is the shared source of truth with
  the sibling `backtesting-framework`; `tests/test_optimizer_edge.py` asserts the optimizer's
  inline statistics agree with the standalone `metrics` functions.
- **Injected-returns API contract** — the exact pattern the backtester and the FastAPI demo use
  (`set .returns/.mean_returns/.cov_matrix → optimize_*`) is covered, so the public API the
  downstream repo imports cannot drift silently.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full dev setup, commit conventions, and PR checklist.

## License

MIT
