# Quant Workspace — Agent Registry & Conventions

This file is the **single source of truth** that every improvement agent reads first.
It describes the repos, the conventions they must follow, the deployment topology, and
the standing rules. Keep it accurate — agents trust it instead of re-discovering everything.

> Working directory: the repository root (this monorepo).
> GitHub owner: `nicholim`.
> **As of 2026-06-01 this is a single MONOREPO** — one git repo at the workspace root (was 5
> separate repos + a showcase). Projects now live under `packages/`, `cpp/`, `apps/` — see `README.md`.
> Repo-wide tooling (LICENSE, ruff/mypy, pre-commit, `.github/workflows/ci.yml`, `render.yaml`,
> `netlify.toml`) is unified at the root; each project keeps its own README/requirements/CONTRIBUTING.

---

## ▶ RESUME HERE — next improvement pass (read this first)

> Theme the user is driving: **make the monorepo feel production / POC, not educational** —
> wire REAL live market data (free sources) into the apps and harden them for cloud deploy.
> All work goes on the `feature/agent-improvements` branch (off `main`); never push.

**Done (2026-06-02): the ENTIRE P0/P1/P2 backlog is complete + features are wired end-to-end.** Beyond the
foundational hygiene/tests/docs/deploy (P0/P1) and the live-data resilience layer (resilient yfinance +
offline fixtures + Finnhub spot w/ 401 warning across the data packages), this session finished ALL P2
feature-comprehensiveness picks across all 5 packages and made them reachable from CLI/API/UI/sim:
- **options-pricing** (201 tests): higher-order Greeks (vanna/volga/charm), Black-76, vectorized batch
  price/greeks/IV, true solved IV surface.
- **portfolio-optimization** (251 tests): solved efficient frontier, Ledoit-Wolf shrinkage, HRP,
  Black-Litterman — all exposed in the CLI + FastAPI (`/optimize/black-litterman` + example).
- **backtesting** (191 tests): CSV/DataFrame handlers, 7 wired optimizer objectives, native short selling
  (opt-in, long-only parity proven); CLI `--allow-short/--data-csv/--offline/--objective hrp`.
- **market-data** (173 tests): pluggable StorageBackend (Timescale + **DuckDB**, the deploy default),
  `replay()`, OHLCV final-bar fix, backpressure cap, pluggable ExchangeAdapter (**Binance + Coinbase**).
- **cpp/order-book** (53 C++ + 41 py): IOC/FOK/post-only, **pybind11 bindings**, throughput/latency
  benchmark, and `simulator.py` now drives the REAL C++ engine through the binding.
Total **910 tests** green; ruff/format/mypy clean; cross-package contract intact. `render.yaml` deploys
market-data on Redis+disk alone (DuckDB default, no external Timescale). Branches cleaned to just
`feature/agent-improvements` + `main`. **NOTHING PUSHED** — 43 commits await the user's push.

**Do next — optional polish only (no formal backlog items remain):**
1. Dashboard reachability: a Dash `--allow-short` checkbox + `hrp` objective dropdown (needs the shared
   `OBJECTIVE_TO_KEY`/`run_analysis` path extended); surface the vectorized IV surface in the Streamlit app.
2. More market-data exchange adapters (Kraken/Bitstamp are ~1 class each); a `--exchange` CLI flag.
3. A native C++ micro-benchmark to isolate the matching path from pybind11 overhead.
4. `.env`/python-dotenv parity for the non-options packages.
First action for the user: **push the branch**, then connect Render Blueprint + Netlify.

---

## The 5 repos

| Path | Stack | Type | Entry points | Popular equivalent |
|------|-------|------|--------------|--------------------|
| `packages/backtesting` | Python 3.10+, Dash, DuckDB, pandas | Event-driven backtester + web dashboard + CLI | `main.py` (CLI), `dashboard.py` (Dash) | backtrader, vectorbt, backtesting.py, zipline-reloaded |
| `packages/market-data` | Python 3.11, asyncio, websockets, Redis, TimescaleDB | Streaming ingestion daemon | `main.py` | cryptofeed, ccxt-pro, ArcticDB |
| `packages/options-pricing` | Python 3.10+, NumPy/SciPy, Streamlit, Plotly | Pricing library + Streamlit app + CLI | `app.py` (Streamlit), `main.py` (CLI) | QuantLib, py_vollib, mibian |
| `cpp/order-book` | C++17 (core) + Python (viz) | Matching-engine library + simulator/visualizer | `src/main.cpp`, `python/simulator.py` | ABIDES, mbt-gym |
| `packages/portfolio-optimization` | Python 3.10+, scipy, pandas | MPT optimization library + CLI + FastAPI demo | `main.py`, `api/app.py`, package `portfolio_optimization_engine/` | PyPortfolioOpt, riskfolio-lib, skfolio, cvxpy |
| `apps/showcase-site` | Vite static (vanilla JS) | Portfolio landing page (Netlify) | `index.html`, `src/main.js` | — |

**Cross-package dependency:** `packages/backtesting` depends one-way on `packages/portfolio-optimization`
(`-e ../portfolio-optimization` in its requirements; `OptimizationRebalanceStrategy` uses the optimizer).
The `metrics` module is a shared source of truth (Sharpe/Sortino/drawdown) — keep it consistent.
In the monorepo the optimizer is co-located, so the editable path resolves locally, in CI, and on Render
(no git-URL install needed).

---

## Conventions

- **Python**: 3.10+ (3.11 for the pipeline). Format/lint with **ruff**, type-check with **mypy** (gradual; `ignore_missing_imports`). Test with **pytest**. Pin deps in `requirements.txt`.
- **C++** (order-book): C++17, CMake, format with **clang-format**, test with **GoogleTest** via `ctest`.
- **Docs**: README with status badges (CI, license, python version), an architecture section, a Quick Start, and a "vs. <popular equivalent>" comparison. Add `CONTRIBUTING.md` and `LICENSE` (MIT).
- **CI**: GitHub Actions at `.github/workflows/ci.yml` — lint + type-check + tests on push/PR to `main`.
- **Commits**: small, conventional (`feat:`, `test:`, `ci:`, `docs:`, `chore:`).

---

## Deployment topology — Netlify + Render hybrid

Netlify cannot natively host these (they are backends/daemons/libraries). So:

- **Netlify** hosts a new **`showcase-site/`** (own repo) — a static portfolio landing page presenting all 5 projects with architecture diagrams, screenshots, and "Live demo" links.
- **Render** hosts the runnable Python apps:
  - `packages/backtesting` → web service (gunicorn + Dash).
  - `packages/options-pricing` → Streamlit web service (or Streamlit Community Cloud; both documented).
  - `packages/portfolio-optimization` → thin FastAPI demo wrapper.
  - `packages/market-data` → Docker background worker (needs Redis + an external TimescaleDB).
  - `cpp/order-book` → not a service; embed a static visualization in the showcase (WASM compile = future work).
  - All wired in one root `render.yaml` via per-service `rootDir`.
- Showcase "Live demo" buttons point at the Render URLs once deployed.

---

## STANDING RULES (every agent must obey)

1. **Work on a feature branch, never commit to `main`.** This is now ONE monorepo: create/switch to `feature/agent-improvements` at the repo root before editing, and confirm a clean tree first. (Scope edits to one package per logical change; keep commits small.)
2. **Bash working dir does NOT persist between your separate tool calls.** Never `cd` in one call and assume it holds in the next. Use **absolute paths**, `git -C <path> ...`, or chain in one call: `cd <repo-root>/packages/<name> && <cmd>`.
3. **Run tests before declaring done.** Python: `pytest`. C++: build + `ctest`. Report results honestly, including failures.
4. **Never `git push`, open a PR, or touch GitHub/Netlify/Render accounts.** A subagent cannot ask the user for confirmation (no interactive prompts), so the rule is simply: **stop after local commits on the feature branch and report what needs the user's action** (push, account connect). Leave the click to the human.
5. **Don't break the cross-repo contract** (`metrics` parity; the optimizer API the backtester imports).
6. **Log your work** in `IMPROVEMENTS.md` — append what you did and what's next, so the next run builds on it.
7. **Prefer reuse over rewrite.** These repos already work; improve incrementally.
8. **Return a concise summary**, not raw dumps — many-agent runs that each return verbose output blow up context.

---

## Subagent execution constraints (important — from Claude Code docs)

- **Subagents cannot spawn other subagents** and cannot use `AskUserQuestion`/plan-mode tools. So **orchestration and any user clarification must happen in the main conversation** (e.g. the `/improve-quant` command or you, directly). `quant-orchestrator` therefore *plans and recommends*; it does not call the specialists itself — the main thread invokes them.
- Subagents start **fresh** with no conversation history. That's why every agent reads `AGENTS.md` + `IMPROVEMENTS.md` first.

---

## Domain accuracy caveats (for docs-writer & feature-architect — don't fabricate competitor capabilities)

- **py_vollib / mibian**: vanilla **European** Black-Scholes/Black-76/BSM only — NOT American/exotic/Heston/vol-surface/Monte-Carlo.
- **backtesting.py**: single-instrument, no live trading/broker integration, slippage not first-class.
- **vectorbt**: event-driven simulation + expanded order types are **PRO (commercial)**, not in the OSS edition; OSS is vectorized.
- **mbt-gym**: model-based (stochastic fill/intensity models, Avellaneda-Stoikov) — **not** a price-time-priority matching engine. **ABIDES** is the one with real LOB matching + latency modeling.
- **QuantLib** is the institutional reference (American/exotic/Heston/MC/FD) — good to benchmark our options pricer against it.
- Reference index for the ecosystem: `awesome-quant` (github.com/wilsonfreitas/awesome-quant).

---

## How to drive the agents

- Pick a specialist from the `/agents` menu (`repo-hygiene`, `test-engineer`, `deploy-engineer`, `docs-writer`, `feature-architect`).
- For a coordinated multi-repo pass, run the **`/improve-quant`** command (it runs in the main thread and can delegate to specialists). `quant-orchestrator` is a survey-and-plan helper whose recommendations the main thread then executes.
