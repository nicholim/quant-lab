# Quant Monorepo

[![CI](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![C++17](https://img.shields.io/badge/C%2B%2B-17-00599C.svg)](https://en.cppreference.com/w/cpp/17)

A collection of quantitative-finance projects in one repo: an event-driven
backtester, an MPT portfolio optimizer, an options pricer, a real-time
market-data ingestion daemon, and a C++ limit-order-book matching engine — plus
a static showcase site.

> **New here?** Read [`ARCHITECTURE.md`](ARCHITECTURE.md) for how it all fits
> together, or jump to [`docs/getting-started.md`](docs/getting-started.md) for a
> clone-to-running-in-5-minutes guide.

## Run it (one command)

The root `Makefile` is self-documenting — run `make` (or `make help`) to list
every target. One shared `.venv`, one command per app.

```bash
make setup              # build ONE shared .venv, install all 5 packages + dev deps
                        # (installs portfolio-optimization FIRST, then the rest)

make run-options        # options-pricing Streamlit app   -> http://localhost:8501
make run-backtest       # backtesting Dash dashboard       -> http://localhost:8050
make run-optimizer-api  # portfolio FastAPI demo (uvicorn) -> http://localhost:8000 (/docs)
make run-market-data    # market-data ingestion daemon     (headless; persists to DuckDB)
make run-showcase       # showcase site (Vite dev server)

make test               # all Python pytest suites + the C++ ctest
make lint format typecheck   # ruff + ruff format + mypy across all packages
```

Prefer containers? The whole stack runs with no local Python:

```bash
docker compose up --build    # or: make docker-up
# 8501 options · 8050 backtesting · 8000 portfolio-api · 6379 redis
# market-data runs as a worker (STORAGE_BACKEND=duckdb — no external DB needed)
```

Or open the repo in a **dev container / GitHub Codespace** — `.devcontainer/`
provisions Python 3.11 + the C++ toolchain + Node and runs `make setup` on create.

Full walkthrough (including what each UI shows): [`docs/getting-started.md`](docs/getting-started.md).

## Layout

```
packages/
├── portfolio-optimization/   MPT optimizer + CLI + FastAPI + Streamlit UI  (PyPortfolioOpt-class)
├── backtesting/              event-driven backtester + Dash dashboard       (backtrader-class)
│                               └─ depends on portfolio-optimization (shared metrics)
├── market-data/              async ingestion daemon + Streamlit monitor     (cryptofeed-class)
│                               (ws→normalize→Redis→DuckDB/Timescale)
└── options-pricing/          Black-Scholes / binomial / Greeks + Streamlit  (py_vollib-class)
cpp/
└── order-book/               C++17 price-time-priority matching engine + Python viz  (ABIDES-class)
apps/
└── showcase-site/            static Vite landing page (Netlify)
```

All four Python packages now ship a web UI: options-pricing and backtesting were
polished, and **portfolio-optimization** (`streamlit_app.py`) and **market-data**
(`monitor.py`) gained first-class UIs alongside their CLI / API / daemon.

Each project keeps its own `README.md`, `requirements.txt`/`pyproject.toml`, and
`CONTRIBUTING.md`. Repo-wide tooling lives at the root: one `LICENSE`,
`.editorconfig`, `.pre-commit-config.yaml`, CI workflow, and deploy blueprints.

## The one cross-package dependency

`packages/backtesting` depends on `packages/portfolio-optimization` (its
`OptimizationRebalanceStrategy` calls the optimizer, and both share the
`metrics` module as the single source of truth for Sharpe/Sortino/drawdown). In
this monorepo that's a co-located editable install — `requirements.txt` carries
`-e ../portfolio-optimization`, which resolves the same way locally, in CI, and
on Render.

## Quick start (manual, per-package)

Prefer `make setup` (above) for a one-shot shared environment. The manual path
below is for working inside a single package:

```bash
# Optimizer (and its consumer, the backtester)
pip install -e packages/portfolio-optimization
cd packages/backtesting && pip install -r requirements.txt && pytest

# Any standalone package
cd packages/options-pricing && pip install -r requirements.txt && pytest
cd packages/market-data     && pip install -r requirements.txt && pytest

# C++ order book
cmake -S cpp/order-book -B cpp/order-book/build -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/order-book/build && ctest --test-dir cpp/order-book/build

# Showcase site
cd apps/showcase-site && npm ci && npm run build
```

## Tooling

- **Lint/format**: `ruff` (+ `ruff format`); **types**: `mypy`; **tests**: `pytest`
  with per-package coverage gates. C++: `clang-format` + GoogleTest via `ctest`.
- **Pre-commit**: `pip install pre-commit && pre-commit install`.
- **CI** (`.github/workflows/ci.yml`): one workflow runs every package's
  lint + type-check + tests, the C++ build + ctest, and the showcase build.

## Deploy

- **Render** (`render.yaml`): one blueprint defines all runnable services
  (backtesting Dash app, options Streamlit app, portfolio FastAPI demo, and the
  market-data Docker worker), each scoped with `rootDir`. The blueprint is tuned
  so every demo runs with **no external database**:
  - The **market-data worker** defaults to `STORAGE_BACKEND=duckdb` and needs
    only the managed Key Value (Redis) — wired in the blueprint — plus the
    container's writable disk (ephemeral on the free plan). External TimescaleDB
    is now **optional**: set `STORAGE_BACKEND=timescale` + `DATABASE_URL` only if
    you want durable Timescale storage.
  - The **portfolio FastAPI demo** optimizes a returns matrix POSTed by the
    caller, so it does no network fetch.
  - The **options** and **backtesting** apps fetch live market data (yfinance /
    Finnhub) but ship bundled fixtures and `*_OFFLINE` flags so cloud egress
    limits never hard-fail the demo — see each package's `DEPLOY.md`.
- **Netlify** (`netlify.toml`): builds and publishes `apps/showcase-site`.

Secrets the user sets in the Render dashboard: `FINNHUB_API_KEY` (optional, for
live options spot) and `DATABASE_URL` (only if switching market-data to
Timescale). See each package's `README.md` / `DEPLOY.md` for service-specific notes,
and [`ARCHITECTURE.md`](ARCHITECTURE.md#deployment-topology) for the full topology.

## License

MIT — see [LICENSE](LICENSE).
