# Quant Monorepo

[![CI](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![C++17](https://img.shields.io/badge/C%2B%2B-17-00599C.svg)](https://en.cppreference.com/w/cpp/17)

A collection of quantitative-finance projects in one repo: an event-driven
backtester, an MPT portfolio optimizer, an options pricer, a real-time
market-data ingestion daemon, and a C++ limit-order-book matching engine — plus
a static showcase site.

## Layout

```
packages/
├── portfolio-optimization/   MPT optimizer + CLI + FastAPI demo  (PyPortfolioOpt-class)
├── backtesting/              event-driven backtester + Dash app   (backtrader-class)
│                               └─ depends on portfolio-optimization (shared metrics)
├── market-data/              async ingestion daemon (ws→Redis→TimescaleDB)  (cryptofeed-class)
└── options-pricing/          Black-Scholes / binomial / Greeks + Streamlit   (py_vollib-class)
cpp/
└── order-book/               C++17 price-time-priority matching engine + Python viz  (ABIDES-class)
apps/
└── showcase-site/            static Vite landing page (Netlify)
```

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

## Quick start

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
  market-data Docker worker), each scoped with `rootDir`. The market-data worker
  needs a managed Key Value (Redis) — wired in the blueprint — and an **external**
  TimescaleDB (`DATABASE_URL`), since Render's Postgres lacks the Timescale extension.
- **Netlify** (`netlify.toml`): builds and publishes `apps/showcase-site`.

See each package's `README.md` / `DEPLOY.md` for service-specific notes.

## License

MIT — see [LICENSE](LICENSE).
