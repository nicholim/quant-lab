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

**Done so far (2026-06-02):** `packages/options-pricing` prices REAL option chains from free data —
chains/expirations via **yfinance** (keyless), spot via **Finnhub** (`FINNHUB_API_KEY`, free tier) with
yfinance fallback, `price_chain` adds `model_price`/`our_iv`/`mispricing`, real IV smile/surface plots,
`--offline`/`OPTIONS_PRICING_OFFLINE=1` bundled-fixture fallback, CLI + Streamlit "Live market" tab, local
`.env` support. 139 tests, 99% cov; `FINNHUB_API_KEY` wired into `render.yaml` as a `sync:false` secret.
Then (RESUME items 1 & 2, this pass): **`packages/backtesting`** and **`packages/portfolio-optimization`**
got the same production posture — a single shared resilient yfinance layer (retry + exponential backoff on
transient/rate-limit/timeout, typed `MarketDataError`) beneath the existing DuckDB/pickle caches, plus a
bundled offline fixture + `BACKTESTING_OFFLINE` / `PORTFOLIO_OFFLINE` (and `--offline`) escape hatch so
demos never hard-fail on Render egress (backtesting 126 tests/87% cov; portfolio 167 tests/95% cov). And
**`packages/market-data`**'s Render Docker-worker deploy story was verified — render.yaml/Dockerfile
confirmed correct, and the worker now FAILS FAST with one actionable log line (not a 20-frame traceback)
when Redis/Timescale are unreachable (76 tests/99% cov). Cross-package metrics+optimizer contract intact.

**Do next (in priority order):**
1. Optional: add `.env`/python-dotenv support to `backtesting` / `portfolio-optimization` / `market-data`
   for parity (options-pricing has it), so cloud secrets can live in a file.
2. Optional polish: wire the new `BACKTESTING_OFFLINE` / `PORTFOLIO_OFFLINE` flags into `render.yaml` (and
   portfolio's FastAPI demo) for a guaranteed-deterministic cloud showcase; expose `--offline` through
   backtesting's `main.py`/dashboard end-to-end. `deploy-engineer`.
3. Move to the **P2 feature comprehensiveness** backlog (see IMPROVEMENTS.md) now that data resilience is
   done across all data-fetching packages — e.g. order-book **pybind11 bindings** (highest-leverage), the
   portfolio **solved efficient frontier / HRP / Ledoit-Wolf** picks, or options **higher-order Greeks /
   Black-76**. `feature-architect`.

**Housekeeping:** a stale orphan branch `feature/agent-improvements-stale-orphan` (unrelated history)
can be deleted once the current `feature/agent-improvements` is confirmed good and pushed.

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
