# Getting started — clone to running in 5 minutes

This monorepo bundles five quant projects behind one developer experience. Pick
the path that fits you:

1. **[Local with `make`](#1-local-with-make)** — one shared `.venv`, one command per app.
2. **[Docker Compose](#2-docker-compose)** — the whole stack, no Python install needed.
3. **[Dev container / Codespaces](#3-dev-container--codespaces)** — opens ready-to-run.

For the big picture (how the projects fit together and where they deploy) see
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

---

## 1. Local with `make`

Prerequisites: **Python 3.11+**, and (for the C++ order book) **cmake** + a C++17
compiler. The root `Makefile` is self-documenting — `make` or `make help` lists
every target.

```bash
git clone https://github.com/nicholim/quant-lab.git
cd quant-lab

make setup        # builds ONE shared .venv and installs all 5 packages + dev deps
                  # (installs portfolio-optimization FIRST, then the rest)
```

`make setup` runs `scripts/bootstrap.sh`, which creates `.venv/` at the repo root
and pip-installs every package editable into it. After that, run any app with a
single target:

| Command | Launches | URL / behavior |
|---|---|---|
| `make run-options` | options-pricing **Streamlit** app | http://localhost:8501 |
| `make run-backtest` | backtesting **Dash** dashboard | http://localhost:8050 |
| `make run-optimizer-api` | portfolio **FastAPI** demo (uvicorn) | http://localhost:8000 (`/docs`, `/health`) |
| `make run-optimizer-ui` | portfolio optimizer **Streamlit** UI | http://localhost:8502 |
| `make run-market-data` | market-data ingestion **daemon** | headless; streams + persists to DuckDB |
| `make run-market-monitor` | market-data live-monitor **Streamlit** UI | http://localhost:8503 |
| `make run-showcase` | showcase site (Vite dev server) | Node 18+; prints the dev URL |

Ports are overridable, e.g. `make run-options OPTIONS_PORT=8600`.

> **Note:** `make run-market-data` starts the *daemon* (headless). To **see** the
> data it captured, launch the read-only Streamlit monitor (see
> [market-data](#market-data-streamlit-monitor) below).

Other useful targets:

```bash
make test          # all Python pytest suites + C++ ctest
make test-py       # Python suites only
make test-cpp      # build the C++ order book + run ctest
make lint          # ruff check across all packages
make format        # ruff format (write); make format-check for CI parity
make typecheck     # mypy across all packages
make build-orderbook   # cmake configure + build the C++ engine (Release)
make clean         # remove .venv, C++ build dir, and caches
```

---

## The four web UIs — what each one does

### options-pricing (Streamlit) — `make run-options` → :8501

```bash
make run-options
# or:  cd packages/options-pricing && streamlit run app.py
```

Three tabs: **Calculator** (live `S,K,T,r,σ` sliders, Black-Scholes vs binomial
EU/American, Greeks table, payoff/delta charts, IV solver), **Live market** (fetch
and price a real option chain, IV smile), and **IV surface** (multi-expiry solved
IV surface + per-expiry smile + vectorized batch pricing). Falls back to a bundled
sample chain offline (the "Offline sample" checkbox or `OPTIONS_PRICING_OFFLINE=1`),
so it never hard-fails without a network.

### backtesting (Dash) — `make run-backtest` → :8050

```bash
make run-backtest
# or:  cd packages/backtesting && python dashboard.py
```

Pick tickers / dates / objective in the control panel, click **Run**: it
optimizes the portfolio (frontier, weights, Monte Carlo VaR/CVaR) then runs a
walk-forward rebalancing backtest of that objective, showing KPI cards, equity
curve, drawdown, and beta/alpha. Includes an `hrp` objective and an "Allow short
selling" checkbox. Surfaces data-fetch failures as in-UI errors (not a 500).

### portfolio-optimization (Streamlit) — *new UI*

```bash
make run-optimizer-ui
# or:  cd packages/portfolio-optimization && streamlit run streamlit_app.py
```

Choose an input source (bundled offline sample, uploaded/entered returns, or a
live yfinance fetch), pick any of the 8 objectives, view the **solved efficient
frontier**, and run a Black-Litterman mini-form (prior → views → posterior →
weights). Works fully offline using the bundled price fixture and degrades
gracefully (never a raw traceback) when a live fetch fails. The FastAPI demo
(`make run-optimizer-api`) is the same engine over HTTP.

### market-data (Streamlit monitor) — *new UI*

```bash
make run-market-monitor
# or:  cd packages/market-data && streamlit run monitor.py
```

A **read-only** companion to the daemon: it shows the most recent trades, the
rolled-up 1-minute OHLCV bars, and a price/volume chart for a chosen symbol. It
reads the same `StorageBackend` the daemon writes (DuckDB by default) via
`Pipeline.replay()` — it opens no WebSocket. On a fresh/empty store it synthesizes
a deterministic seeded sample and shows a clear "sample data" banner, so it always
renders something. Run the daemon (`make run-market-data`) first to populate real
data, then refresh the monitor.

---

## 2. Docker Compose

No local Python needed — `docker compose` builds and runs the stack. Uses the
root [`docker-compose.yml`](../docker-compose.yml) and the per-service Dockerfiles
under [`docker/`](../docker).

```bash
docker compose up --build      # or: make docker-up
```

Published ports (host → container):

| Port | Service | URL |
|---|---|---|
| 8501 | options-pricing (Streamlit) | http://localhost:8501 |
| 8050 | backtesting (Dash) | http://localhost:8050 |
| 8000 | portfolio-api (FastAPI) | http://localhost:8000 (`/docs`, `/health`) |
| 6379 | redis | cache used by market-data |

`market-data` runs as a background worker (no published port) with
`STORAGE_BACKEND=duckdb`, so it needs **no external TimescaleDB** — only the
bundled Redis. Tick/bar data persists to a named Docker volume.

```bash
docker compose config -q       # validate the compose file
docker compose down -v         # stop + remove volumes  (or: make docker-down)
```

Optional: set `FINNHUB_API_KEY` in your shell before `up` to enable live options
spot quotes (otherwise it falls back to yfinance / the bundled sample).

---

## 3. Dev container / Codespaces

Open the repo in VS Code (**Reopen in Container**) or a GitHub Codespace. The
[`.devcontainer/devcontainer.json`](../.devcontainer/devcontainer.json) provisions
a Python 3.11 base with the C++ toolchain (gcc/g++, cmake, clang-format) and Node
LTS, then runs `make setup` automatically on create.

Ports 8501 / 8050 / 8000 / 6379 are forwarded and labeled, so once the container
is up you can `make run-options` (etc.) and open the forwarded URL. The Python
interpreter is pre-pointed at `.venv/bin/python` and Ruff is configured.

---

## Where to go next

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — system context, data-flow diagrams, deploy topology, design decisions.
- Each package's `README.md` — library API, CLI flags, the "vs. <popular tool>" comparison, and benchmarks.
- Each package's `DEPLOY.md` — Render / Netlify hosting notes.
