# Improvements Ledger

This is the **continuity log** for the self-improving agent system. Every agent run appends
here so successive runs build on prior work instead of repeating it. Newest entries on top.

Format per entry:
```
## YYYY-MM-DD — <agent> — <repo>
- What changed (commits/branch)
- Test/CI status
- Next steps / TODO it discovered
```

---

## Backlog (prioritized, cross-repo)

These are the known gaps as of the system's creation. Agents claim items from here and move
them to the changelog below when done.

### P0 — foundational hygiene (all repos) — ✅ DONE 2026-06-01 (all 5 repos)
- [x] `LICENSE` (MIT) — all 5 repos
- [x] `.github/workflows/ci.yml` — lint + type-check + tests — all 5 repos
- [x] `.editorconfig`, ruff + mypy config — all Python repos
- [x] `pre-commit` config — all repos
- [x] `clang-format` + GoogleTest/ctest — `order-book-simulator` (35 GoogleTests now wired into ctest)

### P1 — testing depth — ✅ DONE 2026-06-01
- [x] Coverage reporting (`pytest-cov`) + gate — all Python repos (gates 80–95%; achieved 84–99%)
- [x] C++ unit tests for matching engine — `order-book-simulator` (35 GoogleTests, ctest 35/35)
- [x] Edge-case tests for the thin suites — `market-data-pipeline` (12→71, found+fixed a reconnect-loop bug), `options-pricing-calculator` (36→106)

### P1 — deployment (Netlify + Render hybrid) — ✅ DONE 2026-06-01 (configs written; not deployed)
- [x] Create `showcase-site/` (Vite static) + `netlify.toml`
- [x] `render.yaml` — `backtesting-framework` (Dash web service)
- [x] `render.yaml` — `options-pricing-calculator` (Streamlit) + DEPLOY.md
- [x] FastAPI demo wrapper + `render.yaml` — `portfolio-optimization-engine`
- [x] `Dockerfile` + `render.yaml` (worker) — `market-data-pipeline` (Docker build verified)

### P2 — docs & benchmarks — ✅ DONE 2026-06-01
- [x] README badges + architecture + "vs. <equivalent>" section — all repos
- [x] `CONTRIBUTING.md` — all repos
- [~] Benchmarks — backtester throughput benchmark added (real numbers); options accuracy is test-enforced (parity/convergence/reference values); optimizer↔backtester metrics parity test-enforced. A formal optimizer-vs-PyPortfolioOpt benchmark + C++ matching-engine throughput harness remain open (see feature backlog A5 items).

### P2 — feature comprehensiveness — gap analysis ✅ DONE 2026-06-01 (feature-architect, all 5 repos)
Per-repo prioritized "do these next" (analysis only — NOT yet implemented; awaiting user go-ahead). Full analyses in the changelog below.

- **backtesting-framework** (vs backtrader/vectorbt/backtesting.py/zipline): already strong (multi-asset, limit/stop/OCO, trailing stops, walk-forward all exist).
  1. `CSVDataHandler`/`DataFrameDataHandler` — kill the hard yfinance dep, enable offline/custom/intraday data (S, high impact, no API risk).
  2. Wire the 3 unused optimizer objectives (`sortino`, `max_return_target_vol`, `min_vol_target_return`) into `OptimizationRebalanceStrategy` (S, uses contract only).
  3. Native short selling — biggest real gap vs backtrader/zipline (L; guard FIFO P&L parity + keep metrics in the shared module).
- **market-data-pipeline** (vs cryptofeed/ccxt-pro/ArcticDB): honest single-stream Binance-trades daemon.
  1. Pluggable `ExchangeAdapter` protocol + a 2nd exchange (M, high — the headline gap).
  2. Pluggable `StorageBackend` + Parquet/DuckDB sink — decouples from Timescale (which Render can't host), makes the demo runnable (M, high).
  3. `replay(symbol, start, end)` from store — read API already exists; turns the daemon into a research feeder (S–M, high). Also fix the OHLCV roll-up (drops final bar, needs ≥2 trades) and add a bounded-buffer/backpressure cap.
- **options-pricing-calculator** (vs QuantLib/py_vollib/mibian): already beats py_vollib/mibian (American via binomial, dividend yield).
  1. Higher-order Greeks (vanna/volga/charm) — closed-form, ~30 LOC (S, high).
  2. Black-76 futures-options pricer — completes the vollib "core three" (S, high).
  3. Vectorized/batch pricing API — enables IV chains + a *real* IV surface (M, high). NB: `plot_volatility_surface` is mislabeled — it plots **price** at constant σ; rename or upgrade to a true IV surface. Defer Heston/exotics/SVI (QuantLib-scale).
- **order-book-simulator** (vs ABIDES/mbt-gym): correct C++ price-time-priority engine, but a black box driven only by `main.cpp`; the Python sim and engine never touch.
  1. **pybind11 bindings** — foundational; makes the engine programmable, unblocks everything else, lets tests drop subprocess-stdout parsing (M, very high).
  2. IOC/FOK/post-only order types — pure match-loop variants, no new data structures (S, high).
  3. Throughput/latency benchmark harness (S–M). Strategic L-effort path to "ABIDES-lite": discrete-event latency clock → agent-based participants (sequence after pybind11). WASM core for the showcase is a separate medium-effort demo win.
- **portfolio-optimization-engine** (vs PyPortfolioOpt/riskfolio/skfolio/cvxpy): scipy SLSQP+linprog; all top picks are **additive / API-safe** (the backtester's injected-returns + zero-arg `optimize_*` + `PortfolioResult.weights` contract stays intact).
  1. True *solved* efficient frontier (sweep existing `optimize_min_vol_target_return`) — replaces the random Dirichlet cloud "toy" gap (S, high).
  2. `optimize_hrp()` Hierarchical Risk Parity — marquee feature needing no solver, fits scipy-only ethos, slots into the backtester's pattern for free (M, high).
  3. Ledoit-Wolf covariance shrinkage (opt-in, default off to preserve parity) (S, high). Runner-up: Black-Litterman (M). Defer cvxpy backend (heavy dep, scope creep).

---

## Changelog

## 2026-06-02 — main thread (user-requested) — packages/options-pricing (Finnhub key validation)
- Branch `feature/agent-improvements` (1 `feat:` commit; NOT pushed). Live-tested the user's newly added
  `FINNHUB_API_KEY` and found the live Finnhub path was returning 401 — the value in the local (gitignored)
  `.env` had a duplicated `FINNHUB_API_KEY=` prefix (a paste error), so the app sent `FINNHUB_API_KEY=<key>`
  as the token. `get_spot` still "worked" only because it silently fell back to yfinance, masking the bad key.
  Fixed the `.env` locally; confirmed live Finnhub spot for AAPL/MSFT/SPY and a full `price_chain` with
  `our_iv` solving to ~23–26%.
- To stop a misconfigured key from hiding behind the fallback going forward: `_finnhub_spot` now detects a
  401/403 (key SET but rejected) and logs ONE actionable `logging.warning` (raw token, no quotes, no
  `FINNHUB_API_KEY=` prefix) before falling back, throttled to once per process (reset by `clear_cache()`).
  No behavior change when the key is valid or unset.
- Tests: +2 (139 -> 141), mocked via `caplog` + `_FakeResp(401/403)` — warn-then-fallback and warn-once.
  `market_data.py` stays **100%** covered; full suite **141 passed, 99.27% cov** (gate 95 met); ruff +
  ruff-format + mypy clean. **User action for deploy:** paste only the raw key as the Render
  `FINNHUB_API_KEY` secret value (no name prefix / quotes).

## 2026-06-02 — feature-architect — packages/portfolio-optimization (resilient yfinance)
- Branch `feature/agent-improvements` (NOT pushed). RESUME-HERE item 1 for portfolio: gave the package's
  yfinance use the same production posture options-pricing has. Purely a data-fetch resilience change —
  optimizer public API + metrics + backtester injected-returns contract untouched.
- **`data_cache.py`** is now the SINGLE network entry point. Added `fetch_close_prices()` (resilient
  `yf.download(...)["Close"]`): retry + exponential backoff (`MAX_RETRIES=3`, `BACKOFF_BASE=0.5`) on
  transient/rate-limit/timeout errors (heuristic `_is_transient`), fail-fast on non-transient, typed
  `MarketDataError` on final failure / empty result. `download_close_prices` now calls it on cache miss
  (on-disk pickle cache kept as PRIMARY, behavior intact; offline results are NOT cached).
- **`analysis._fetch_benchmark`** no longer calls `yf.download` directly — it imports nothing from yfinance
  and routes through `data_cache.fetch_close_prices`, so all network logic lives in one place.
- **Offline fallback** mirroring `OPTIONS_PRICING_OFFLINE`: env flag `PORTFOLIO_OFFLINE=1` + `offline=`
  param (threaded through `AnalysisConfig.offline`, `--offline` CLI flag, `PortfolioOptimizer(offline=)`).
  Ships a deterministic fixture `portfolio_optimization_engine/data/sample_prices.csv`
  (AAPL/MSFT/GOOGL/AMZN/SPY, ~260 b-days). Un-ignored via a package `.gitignore` negation overriding BOTH
  the root `data/` rule and the package's own `*.csv` rule; added `[tool.setuptools.package-data]` so it
  ships in the wheel.
- **Tests:** +20 (147 -> 167), all mocked / no live network: success-after-retry, rate-limit retry,
  retries-exhausted -> MarketDataError, non-transient fail-fast (1 call), empty-result, offline arg/env
  (single+multi ticker, falsey env values), unknown-ticker-offline raises, offline-not-cached vs
  online-cached, benchmark-via-shared-layer, run_analysis offline end-to-end, `--offline` CLI flag.
  Updated existing `test_analysis.py` fixture to patch `fetch_close_prices` (benchmark no longer uses yf
  directly). **Coverage 94.74% -> 95.18%** (gate `--cov-fail-under=90` met; `data_cache.py` 99%).
- **Gate:** pytest 167 passed; `ruff check` clean; `ruff format --check` clean; `mypy` clean (9 files).
  Verified `python main.py --tickers AAPL MSFT GOOGL --benchmark SPY --offline --no-plots` runs fully offline.
- **Follow-ups:** wire `PORTFOLIO_OFFLINE` / `--offline` into the FastAPI demo + `render.yaml` if a
  guaranteed-deterministic cloud demo is wanted (deploy-engineer). Optional `.env`/python-dotenv parity not
  added here.

## 2026-06-02 — feature-architect/test-engineer — packages/backtesting (resilient yfinance layer)
- Branch `feature/agent-improvements` (NOT pushed). Gave backtesting's yfinance usage the same production
  posture as options-pricing. Addresses RESUME-HERE priority item 1 (backtesting half).
- **New `src/market_data.py`** — single shared `download_ohlcv(symbol, start, end, *, offline, max_attempts,
  backoff_base, timeout)` resilient network layer beneath the existing DuckDB cache. Retries transient
  network/rate-limit errors (timeout/429/5xx/connection markers) with exponential backoff, raises a typed
  `MarketDataError` on final failure (and on empty frames after retries), passes a `timeout=` to `yf.download`,
  and flattens the single-ticker MultiIndex. Offline fallback via `offline=True` arg or `BACKTESTING_OFFLINE`
  env flag (mirrors options-pricing's `OPTIONS_PRICING_OFFLINE`) serves a bundled deterministic fixture.
- **Routed all THREE call sites through it** (logic lives in ONE place): `datastore.fetch_ohlcv` (now takes
  `offline=`; DuckDB stays the PRIMARY cache, helper is the network layer beneath it on miss), the
  `YFinanceDataHandler.fetch` no-store path (added `offline=` ctor param), and the `Backtest` benchmark
  download (~backtest.py:118). Removed the duplicated `yf.download` + MultiIndex-droplevel from all three.
- **Bundled fixture** `src/data/sample_ohlcv.csv` (260 deterministic business-day bars from 2021-06-01,
  symbol-agnostic). Un-ignored via a package `.gitignore` negation (`!src/data/`, `!src/data/*.csv`) — copied
  the options-pricing approach so the root `data/` ignore doesn't swallow it (verified with `git check-ignore`).
- **Tests:** +23 (103 -> 126), all mocked via a fake `yfinance` module (monkeypatch `sys.modules`), NO live
  network: success, retry-then-success on transient, exhausting attempts, non-transient = no retry, empty-frame
  retry/raise, empty-then-success, MultiIndex flatten, offline arg + env flag + env-string parsing, fixture
  date-slice + empty-window raise, and integration through DataStore (offline -> fixture -> DuckDB cache hit,
  no network) and YFinanceDataHandler no-store offline. Coverage **84.62% -> 86.96%** (gate `--cov-fail-under=80`
  met; `market_data.py` 100%). `ruff check` + `ruff format --check` + `mypy` all clean.
- **Cross-package contract intact:** metrics module + optimizer API untouched — this is purely a data-fetch
  resilience change. Online/not-offline behavior unchanged (DuckDB cache path identical).
- **Follow-ups:** (1) the portfolio-optimization half of RESUME-HERE item 1 is still open (apply the same
  posture to its yfinance use). (2) Optionally wire `BACKTESTING_OFFLINE` into `render.yaml` for a guaranteed
  deterministic cloud demo (left off by default so live data works when egress allows). (3) `main.py`/dashboard
  could expose an `--offline` flag end-to-end.

## 2026-06-02 — deploy-engineer — packages/market-data (Render worker deploy verify + degradation fix)
- Branch `feature/agent-improvements` (commits: 1 `fix:` src/pipeline.py, 1 `test:`, 1 `docs:` README; NOT
  pushed). Verified the Docker background-worker deploy story end-to-end and hardened the no-infra path.
- **Config confirmed (no change):** root `render.yaml` `market-data-pipeline` worker parses with pyyaml —
  `type: worker`, `runtime: docker`, `plan: free`, `rootDir: packages/market-data`, `dockerfilePath:
  ./Dockerfile`, `autoDeploy: false`, NO healthCheckPath (correct for a worker). `REDIS_URL` is injected
  `fromService` the managed `market-data-cache` Key Value add-on; `DATABASE_URL` is `sync: false` (external
  Timescale secret); WS_URL/SYMBOLS/LOG_LEVEL/BATCH_SIZE/FLUSH_INTERVAL_SECONDS hardcoded. Dockerfile is
  `python:3.11-slim`, non-root uid 10001 (`appuser`), layer-cached deps, `CMD ["python","main.py"]`;
  `.dockerignore` excludes tests/caches/docs. `python main.py --help` and `import main` both clean.
- **Degradation verdict: now FAILS FAST + cleanly (was a raw traceback).** Confirmed `Pipeline.start()`
  connects Redis (`ping`) then Timescale (`create_pool` + `init_schema`) with NO try/except, so an
  unreachable `REDIS_URL`/`DATABASE_URL` previously crashed `main.py` with a ~20-frame redis/asyncio
  traceback. **`fix:`** wrapped both connect steps so the worker logs ONE actionable line
  (`Could not connect to Redis at <url> … Set REDIS_URL …` / analogous for TimescaleDB/`DATABASE_URL`)
  and re-raises → non-zero exit → Render shows a clean log and restarts. Verified live against dead ports
  (Redis `:6390`, PG `:5499`): exactly one ERROR line, no traceback noise. Intentional: an ingester with
  no store is a no-op, so booting "successfully" and dropping every trade would be worse than failing loudly.
- **Docker:** the `docker` binary exists but the daemon is NOT running in this env, so a fresh `docker build`
  could not be re-run; the prior pass (2026-06-01) verified the build + non-root run. Re-verified everything
  else via local import/run instead (noted honestly).
- **Tests:** added 2 mocked lifecycle tests (Redis-down, Timescale-down) in `test_pipeline_integration.py`
  using the existing in-memory fakes — assert the actionable log + re-raise + fail-fast isolation, no live
  infra. **pytest 76 passed (was 74), 98.80% cov** (gate 85). `ruff check` clean, `ruff format` clean (16
  files), `mypy` clean (7 files).
- **Docs:** README "Deployment" — added a "Behavior without live infra" subsection documenting the fail-fast
  contract, the Timescale-extension caveat (plain Postgres fails at `create_hypertable` in `init_schema`),
  the WS auto-reconnect resilience once connected, and that config lives only in the root `render.yaml`
  (no per-package file; fixed a stale "optional plain-Postgres path" pointer). Bumped stale test count 71→76.
- **User actions to deploy (the click is yours):** (1) push the monorepo to GitHub; (2) Render → New →
  Blueprint → select repo → Apply (creates the worker + the `market-data-cache` Key Value add-on, injects
  `REDIS_URL`); (3) provision an EXTERNAL TimescaleDB (Timescale Cloud / Aiven free tier) and set the
  `DATABASE_URL` secret in the worker's Environment (it is `sync:false`, so Render prompts); (4) optionally
  adjust `WS_URL`/`SYMBOLS`. Until `DATABASE_URL` is set to a reachable Timescale, the worker will fail-fast
  on boot by design.

## 2026-06-02 — main thread (user-requested) — packages/options-pricing (.env support)
- Branch `feature/agent-improvements` (1 `feat:` commit, `73e5a73`; NOT pushed). Added local `.env` support
  so `FINNHUB_API_KEY` can live in a file instead of being exported: `_load_dotenv_once()` in
  `src/market_data.py` calls python-dotenv at import (idempotent; real env vars override `.env`). Added
  `python-dotenv>=1.0.0` to requirements, a `.env.example`, a root `.gitignore` rule (`.env` ignored,
  `!.env.example` tracked), a README note (export OR `.env`), and a test (`market_data.py` stays 100% cov).
- Verified: `.env` loads at import (key picked up), `.env` is gitignored, `python main.py --symbol AAPL --offline`
  still works, **pytest 139 passed (was 138), 99.25% cov**, ruff/format/mypy clean.
- Also: recorded a **"▶ RESUME HERE"** block at the top of `AGENTS.md` so the next `/improve-quant` pass
  continues automatically (next targets: harden yfinance in backtesting + portfolio with cache/retry/offline
  fallback; verify market-data WS Render deploy). Stale orphan branch `feature/agent-improvements-stale-orphan`
  can be deleted once the current branch is pushed.

## 2026-06-02 — docs-writer — packages/options-pricing (README live-data docs)
- Branch `feature/agent-improvements` (1 `docs:` commit; NOT pushed). README only — no `src/`, `app.py`,
  `main.py`, `render.yaml`, or tests touched.
- Added a dedicated **"Live market data"** README section: data-source split table (chains/expirations ->
  yfinance keyless; spot -> Finnhub free tier via `FINNHUB_API_KEY` with automatic yfinance fallback;
  `our_iv` solved by this lib from market mid, yfinance IV kept as `market_iv`), the public surface
  (`get_spot`/`list_expirations`/`get_option_chain`/`price_chain`/`clear_cache`/`sample_expiry`/`MarketDataError`),
  `export FINNHUB_API_KEY=...` (optional), the `--offline` / `OPTIONS_PRICING_OFFLINE=1` escape hatch, and a
  one-line pointer to DEPLOY.md for the Render secret. Folded the older brief "Price a real option chain"
  subsection into it.
- **Real output captured & pasted** (not fabricated): ran `OPTIONS_PRICING_OFFLINE=1 python main.py --symbol
  AAPL --offline` (Spot $195.00, expiry 2026-07-17, T 0.1205y, strike/mid/model/our_iv/mkt_iv/mispr table)
  and a `price_chain("AAPL", ..., offline=True)` snippet showing `model_price`/`our_iv`/`mispricing` columns.
- **"vs QuantLib / py_vollib / mibian"** table: added two rows — "Prices REAL live option chains" (Yes here;
  No for py_vollib/mibian = no data layer; No for QuantLib = no built-in free feed) and "Per-contract IV
  solved from live mid" — plus a framing bullet making the live-data + mispricing view the differentiator vs
  the textbook calculators. Stayed within AGENTS caveats: no exotics/Heston/MC/FD claimed.
- Updated stale test count 106 -> 138 (badge + "Why this exists" + Correctness checks + pytest comment);
  bumped "~3 files" -> "~4 files"; refreshed Project Structure tree (added `market_data.py`,
  `data/sample_chain.csv`, updated visualizer line).
- Verified all snippets/signatures against `src/market_data.py`, `main.py`, `app.py` (Live market tab at
  `st.tabs([...])`), `greeks_visualizer.py` (`plot_price_surface` + alias, `plot_market_iv_smile/_surface`),
  and DEPLOY.md before writing. User action: none beyond eventual push.

## 2026-06-02 — deploy-engineer — packages/options-pricing (live-data deploy config)
- Branch `feature/agent-improvements` (2 commits: 1 `chore:` render.yaml, 1 `docs:` DEPLOY.md; NOT pushed).
  Config + docs only — no `src/` or `app.py` logic touched.
- **Root `render.yaml`** (`options-pricing-calculator` service): appended `FINNHUB_API_KEY` with `sync: false`
  (Render secret, never hardcoded) to the existing `envVars`. Start command, `healthCheckPath: /_stcore/health`,
  `PYTHON_VERSION: "3.11"`, and `rootDir: packages/options-pricing` left intact. Also added a commented-out
  `OPTIONS_PRICING_OFFLINE=1` stub (deliberately NOT enabled — see tradeoff below). YAML re-validated with pyyaml.
- **DEPLOY.md:** new "Live market data" section — chains/expirations keyless via yfinance; spot via Finnhub
  (free tier) when `FINNHUB_API_KEY` set, else yfinance fallback; how to set the secret in the Render dashboard
  post-Blueprint; the cloud rate-limit caveat and the `OPTIONS_PRICING_OFFLINE` / `--offline` escape hatch. Also
  noted the service config lives only in the root `render.yaml` (no per-package file).
- **Offline decision:** did NOT default `OPTIONS_PRICING_OFFLINE=1` on Render — that would hide the live feature
  behind the static fixture. The app's per-request fallback to `sample_chain.csv` already prevents crashes, so the
  default lets live data work when egress allows and degrades quietly otherwise; the flag is documented for anyone
  wanting a guaranteed-deterministic showcase.
- **User action:** after Blueprint apply, set `FINNHUB_API_KEY` (free key from finnhub.io) in the Render dashboard
  -> options-pricing-calculator -> Environment. Optional; spot falls back to yfinance if unset.
- Branch `feature/agent-improvements` (4 commits: 2 `feat:`, 1 `test:`, 1 `docs:`; NOT pushed). Turned the
  pure-math toy into a tool that prices REAL options from FREE live data.
- **New `src/market_data.py`** (typed, 100% covered): `get_spot` (Finnhub `/quote` primary when
  `FINNHUB_API_KEY` set, yfinance `fast_info`/history fallback, per-symbol process cache), `list_expirations`,
  `get_option_chain` (normalized to `strike,bid,ask,mid,last,market_iv,volume,open_interest`; mid=(bid+ask)/2 when
  both>0 else last), `price_chain` (adds `model_price` via existing `black_scholes_price`, `our_iv` solved from
  market mid via existing `implied_volatility` — we do NOT trust yfinance's IV, kept as `market_iv`, and
  `mispricing=model_price-mid`). `MarketDataError`; offline escape hatch via `offline=True` or
  `OPTIONS_PRICING_OFFLINE=1`.
- **Data-source split (decided, intentional):** chains+expirations -> yfinance (only free full-chain source);
  spot -> Finnhub primary (free tier has real-time quotes, reliable from cloud IPs; NO chains) with yfinance
  fallback. Key from env, never hardcoded.
- **Offline fixture** `src/data/sample_chain.csv` (AAPL-like, BS-consistent at ~45d so `our_iv` round-trips on any
  run date). Un-ignored via a package `.gitignore` negation (root ignores `data/`). Demo never hard-crashes offline.
- **Fixed the mislabeled surface:** `plot_volatility_surface` (plotted PRICE at constant sigma) renamed to
  `plot_price_surface` + kept as a back-compat alias; added the REAL `plot_market_iv_smile` and
  `plot_market_iv_surface` (IV vs strike[/expiry] from a live/sample chain).
- **Entry points:** `main.py --symbol/--expiry/--type/--offline` (no-arg textbook demo UNCHANGED); `app.py` now has
  Calculator + Live market tabs (fetch+price chain, IV smile, graceful offline fallback).
- **Tests:** +32 (106 -> 138), all mocked (monkeypatch `yfinance.Ticker` + `requests.get`, NO live network):
  Finnhub-primary, all fallback paths, normalization mid logic, price_chain math (offline + synthetic),
  MarketDataError, offline fixture/env flag, headless IV-smile/surface smoke. **Coverage 98.63% -> 99.23%**
  (`--cov-fail-under=95` still met; market_data.py 100%). ruff check + format clean, mypy clean. `python main.py`
  and `python main.py --symbol AAPL --offline` both verified; Streamlit boots offline with no errors.
- **NEXT pass:** deploy-engineer must add `FINNHUB_API_KEY` as a Render secret (`sync:false`) on the
  options-pricing Streamlit service so live spot works from the cloud IP. docs-writer should document the full live
  flow (env var, offline fallback, the model_price/our_iv/mispricing columns) in the README "vs" section.
- **Render note:** yfinance egress from cloud IPs can be rate-limited, which is exactly why spot uses Finnhub and
  why the bundled offline fixture + `OPTIONS_PRICING_OFFLINE` flag exist — the deployed demo should degrade to the
  sample chain rather than erroring.

## 2026-06-01 — monorepo migration — ALL projects
- Consolidated the 5 separate repos + showcase-site into a SINGLE monorepo (fresh history, nothing was pushed yet). New layout: `packages/{portfolio-optimization,backtesting,market-data,options-pricing}`, `cpp/order-book`, `apps/showcase-site`.
- Unified root tooling: one `LICENSE`, `.editorconfig`, `.gitignore`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml` (matrix over packages + C++ ctest job + showcase build), `render.yaml` (all services via `rootDir`), `netlify.toml`. Removed the per-package duplicates.
- The backtester↔optimizer friction is GONE: `packages/backtesting/requirements.txt` now uses `-e ../portfolio-optimization` (co-located), resolving identically locally / in CI / on Render — no more git-URL install or `requirements-deploy.txt` workaround.
- Inner Python package name `portfolio_optimization_engine` kept, so zero import rewiring. Removed a stale tracked `cpp/order-book/build/` (8.7M of CMake artifacts with old absolute paths baked in) and gitignored build outputs.
- Fixed 4 options mypy errors (matplotlib-stub: 3D-axes methods + numpy-bool `where`) with narrow `# type: ignore`, so mypy is now clean+blocking across ALL packages. Reformatted options + order-book to satisfy `ruff format --check`.
- Verified in new layout: portfolio 147 / backtesting 103 / market-data 74 / options 106 / order-book 22 Python + 35 C++ — ALL pass; ruff + ruff-format + mypy clean everywhere. Safety backup at /tmp/quant-premonorepo-backup.tgz.
- Done (follow-up): updated all `.claude/agents/*.md` + `.claude/commands/improve-quant.md` references from old per-repo names to monorepo paths (e.g. `backtesting-framework`→`packages/backtesting`, `order-book-simulator`→`cpp/order-book`). The whole `.claude/` is now gitignored as local tooling (it carries machine-specific absolute paths), so it isn't published but still drives local `/improve-quant` runs against the monorepo.

## 2026-06-01 — code-review + fixes — market-data-pipeline, order-book-simulator
- Ran a high-effort, recall-biased code review (1 reviewer per repo) over all 6 `feature/agent-improvements` branches. Result: branches are clean; reviewers built + ran every suite. Only ONE substantive finding.
- **Fix (market-data-pipeline, `4275ad3`):** the earlier reconnect fix (`ce5ac45`) reset the retry counter only on message delivery, so a healthy-but-quiet stream that dropped would exhaust `max_retries` and stop the daemon. Now `_reconnect()` also resets the budget when the dropped connection had been alive >= `_STABLE_CONNECTION_SECONDS` (60s, > one keepalive cycle); immediate flaps / failed connects still climb to `max_retries`. +3 unit tests (71->74), 98.8% cov, ruff/mypy/format all green.
- **Fix (order-book-simulator, `37f8c75`):** corrected a false test docstring ("never call plt.show()" -> no-op under Agg). 11/11 viz tests pass.
- Non-blocking notes for later: portfolio `api/app.py` (FastAPI demo) has no tests and is excluded from the coverage gate's source (logic was hand-verified, API contract intact); backtesting's new `DataHandler` ABC abstractmethods would break any *external* subclass (internal code fine). No action taken on these.

## 2026-06-01 — repo-hygiene (lint/type cleanup) — portfolio-optimization-engine
- Branch `feature/agent-improvements` (4 commits: 2 `style:`, `chore:`, `ci:`; not pushed). NO behavioral changes.
- Ruff 24 -> 0: 10 I001 import-sorts + `ruff format` (autofix), then B905 (`zip(..., strict=False)`, lengths always equal), B904 (`raise ... from err`), E501 (metrics docstring wrap), E702 (formatter-split). Mypy 12 -> 0: Optional guards on injected `returns`/`mean_returns`/`cov_matrix` via 6 narrow `# type: ignore[union-attr/arg-type]` (data set before call), `_min_cvar_lp` local list typing/renames, and `plt.cm.Set3` -> `plt.get_cmap("Set3")` (typed, equivalent).
- Flipped CI ruff step to BLOCKING (removed `continue-on-error`); ruff now clean+green. mypy kept non-blocking in CI (now clean locally).
- Verified: `ruff check .`/`ruff format --check`/`mypy` all clean, **pytest 147 passed, 94.74% cov**. Public API confirmed unchanged (diff vs main = only format/annotation/ignore-comment + private `_min_cvar_lp` rename). Metrics math untouched.

## 2026-06-01 — repo-hygiene (lint/type cleanup) — market-data-pipeline
- Branch `feature/agent-improvements` (3 commits: `style:` ruff, `chore:` mypy, `ci:`; not pushed). No behavioral changes.
- Ruff 16 -> 0: autofixed I001/F401/UP017/UP035 + `ruff format`; manually wrapped 9 E501 (main/config/normalizer/pipeline/storage). `_retry_count` reset logic untouched.
- Mypy 12 -> 0 (no `# type: ignore` needed): annotated `_ws: ClientConnection | None`; added `assert ... is not None` narrowing guards before `_client`/`_pool` use in cache.py/storage.py (methods already require a live connection).
- CI: ruff step was already blocking (now green); also made mypy step blocking since it's now clean. **pytest 71 passed, 98.73% cov.**

## 2026-06-01 — repo-hygiene (lint/type cleanup) — backtesting-framework
- Branch `feature/agent-improvements` (2 commits: 1 `style:` ruff format, 1 `chore:` mypy fixes, 1 `ci:`; not pushed).
- `ruff format` normalized 15 files (pure formatting); `ruff check .` stays clean.
- mypy **25 -> 0**: declared DataHandler ABC's full interface (resolved 13 attr-defined), None-guarded
  DuckDB `fetchone()` in datastore, typed `strategy_factory` as `Callable[..., Strategy]` + annotated
  walk-forward closure, direct-index `_rank` sort key. Only `# type: ignore` added: 3x `[call-arg]` on
  bokeh `figure()` (stubs omit `x_axis_type`/`tools`, valid at runtime). No behavior/metrics/optimizer-path changes.
- Flipped CI mypy AND ruff-format-check to **blocking** (both fully green). pytest **103 passed** (84.6% cov);
  editable optimizer dep verified importable.

## 2026-06-01 — feature-architect — all 5 repos (analysis only, no code changes)
- Ran a per-repo feature gap analysis vs popular equivalents (analysis only — nothing implemented, no branches touched). Prioritized "do these next" picks aggregated into the **P2 feature comprehensiveness** backlog section above.
- Key findings: backtesting-framework is already feature-rich (multi-asset/limit/stop/OCO/trailing/walk-forward exist) — top add is a CSV/DataFrame data handler; order-book-simulator's foundational gap is **pybind11 bindings** (engine is a black box driven only by `main.cpp`); portfolio-engine's top picks (solved frontier, HRP, Ledoit-Wolf shrinkage) are all API-safe/additive; options top picks are quick closed-form wins (higher-order Greeks, Black-76, vectorization) — defer Heston/exotics; market-data top picks are pluggable exchange adapters + storage backends + replay.
- Caveat flagged for implementers: options `plot_volatility_surface` is mislabeled (plots price at constant σ, not an IV surface). Next: pick items per repo and dispatch feature-architect with named features to implement.

## 2026-06-01 — docs-writer — options-pricing-calculator
- Branch `feature/agent-improvements` (1 `docs:` commit, not pushed). Rewrote README: status badges
  (CI/MIT/py3.10+/ruff/106-tests/~99%-cov), one-liner + "Why this exists", cleaned architecture mermaid
  (+ q, + CLI/Streamlit sinks), feature table, Quick Start (library import + `python main.py` + `streamlit
  run app.py`), scope-accurate "vs QuantLib / py_vollib / mibian" table (American=Yes here & QuantLib, No
  for py_vollib/mibian per AGENTS caveats; no exotics/Heston/MC/FD claimed). Added "Correctness checks"
  citing existing tests (Hull/closed-form ref values, put-call parity 1e-9, binomial->BS convergence, FD
  Greeks, American>=European, IV round-trips) as correctness not perf. Added `CONTRIBUTING.md`.
- Verified snippets against real entry points: `python main.py` (CLI matches; header "European CALL/PUT"),
  library import (2.4779/0.3772/0.0380/6.4275/0.2531), `pytest` 106 passed ~98.6% cov. No production code
  touched. User action: none beyond eventual push.

## 2026-06-01 — docs-writer — backtesting-framework
- Branch `feature/agent-improvements` (3 `docs:` commits, not pushed). No production code changed.
- README: added CI/MIT/Python/ruff badges, one-line value prop + "Why this exists", an event-loop
  walkthrough + component table, and an honest "vs. backtrader/vectorbt(OSS)/backtesting.py/zipline-reloaded"
  table (grounded in AGENTS.md caveats: vectorbt OSS = vectorized + unmaintained, event-driven/orders are PRO;
  backtesting.py single-instrument; no live trading here). Added `CONTRIBUTING.md`.
- Added `benchmarks/throughput.py` (synthetic random walk, no network/DuckDB). Verified real numbers on
  arm64/CPython 3.12: ~4,980 events/sec (2520 bars x5 sym, ~2.5s); ~4,490 events/sec (5040x1). All README
  imports/signatures verified; `dashboard.server` is Flask. pytest 103 passed, 84.7% cov; ruff clean.

## 2026-06-01 — docs-writer — portfolio-optimization-engine
- Branch `feature/agent-improvements` (1 `docs:` commit, not pushed). No production code / public API touched.
- README: added CI/MIT/Python/ruff badges + one-liner + "Why this exists"; added accurate "vs. the popular
  tools" table (PyPortfolioOpt/riskfolio-lib/skfolio/cvxpy — checked docs, no fabricated capabilities; noted
  frontier is a Dirichlet random cloud, scipy SLSQP+linprog not cvxpy); added Testing & quality section noting
  test-enforced metrics parity + injected-returns API contract (no fabricated benchmarks).
- Added `CONTRIBUTING.md` (dev setup, tests/lint/mypy, commit conventions, PR checklist, public-API contract
  warning) and `examples/quickstart_offline.py` (runnable end-to-end, no network — verified; ruff clean).
- Verified: library + FastAPI `/optimize` snippets run; **pytest 147 passed, coverage 94.72%**. User action: none.

## 2026-06-01 — docs-writer — order-book-simulator
- Branch `feature/agent-improvements` (2 `docs:` commits, not pushed; no production code touched).
- Reworked README: badges (CI/MIT/C++17/Python3.10+/clang-format/ruff), one-liner + "why", C++ core vs
  Python-layer architecture, full Quick Start (cmake build + run demo + simulator/visualizer + ctest + pytest),
  and an honest "vs. ABIDES / mbt-gym" table grounded in the caveat (this = real price-time-priority matching
  engine; mbt-gym = model-based/no matching; ABIDES = LOB matching + discrete-event latency). Added `CONTRIBUTING.md`.
- Fixed doc drift: README claimed "stop orders" but `OrderType` is `MARKET, LIMIT` only — corrected + Roadmap
  note. Example output re-synced to the actual `order_book_demo` run. **Verified: cmake build OK, ctest 35/35,
  pytest 22/22 (97% cov).** No benchmark fabricated (demo too small; noted as roadmap). User action: none.

## 2026-06-01 — docs-writer — market-data-pipeline
- Branch `feature/agent-improvements` (1 `docs:` commit, not pushed). No production code touched.
- README: added CI/MIT/python-3.11/ruff badges, one-liner + "Why this exists", a numbered async
  data-flow narrative (WS client → normalizer → cache → buffer/batch → TimescaleDB), fixed the config
  table (added `LOG_LEVEL`, corrected `DATABASE_URL` default to match `config.py`), an honest "vs.
  cryptofeed / ccxt-pro / ArcticDB" table (this is a single-stream ingest+store daemon, not a
  multi-exchange client lib or DB engine; flagged ccxt-pro as paid), and a Development section.
- Added `CONTRIBUTING.md` (dev setup, ruff/mypy/pytest gates, mocks-not-live-services testing via
  conftest fakes, branch + Conventional Commits, PR checklist).
- Verified: `python main.py --help` and the README normalize example run as documented; **pytest 71
  passed, 98.69% coverage**; ruff/mypy present locally. No benchmarks fabricated. User action: push.

## 2026-06-01 — deploy-engineer — portfolio-optimization-engine
- Branch `feature/agent-improvements` (3 commits: 1 `feat:`, 1 `chore:`, 1 `docs:`; not pushed).
- Added thin FastAPI demo wrapper `api/app.py` (GET `/health`, GET `/objectives`, POST `/optimize`) that
  only *calls* the existing `PortfolioOptimizer` public API via the backtester's injected-returns contract
  (set `.returns`/`.mean_returns`/`.cov_matrix`, then `optimize_*`) — no logic duplicated, public API untouched.
  Extra deps isolated in `requirements-api.txt` (core `requirements.txt` undisturbed). Added `render.yaml`
  (free web service; build `pip install -r requirements-api.txt && pip install -e .`; start
  `uvicorn api.app:app --host 0.0.0.0 --port $PORT`; health `/health`; autoDeploy off) + README deploy steps.
- Verified: import OK, uvicorn boots + `/health` 200, TestClient covers all 5 objectives (weights sum=1) +
  422 paths; **pytest 147 passed, coverage 94.72%** (gate 90% met; api/ outside `--cov`). User action: push +
  connect on Render (Blueprint → Apply); no secrets/env vars needed.

## 2026-06-01 — deploy-engineer — market-data-pipeline
- Branch `feature/agent-improvements` (2 commits: 1 `feat:` Dockerfile + `.dockerignore`, 1 `chore:` render.yaml + README deploy docs; not pushed).
- Added single-stage `python:3.11-slim` Dockerfile (non-root uid 10001, layer-cached deps, `CMD python main.py`) and `render.yaml` as a Docker **background worker** (`type: worker`, no health check). Wires `REDIS_URL` from a managed Render Key Value add-on; `DATABASE_URL` is a `sync:false` secret because Render Postgres lacks TimescaleDB — user must supply an external Timescale Cloud/Aiven URL.
- **Verified:** `docker build` succeeds; image runs `python main.py --help`, imports `main`/`Pipeline`/`Config` with no live services, and runs as non-root. render.yaml YAML validated.
- Next: actual deploy is user's click (push repo, connect Blueprint, set DATABASE_URL secret).

## 2026-06-01 — deploy-engineer — showcase-site (NEW repo)
- Created `showcase-site/` as its own git repo: **Vite** vanilla-JS static landing page (Node 22 available,
  so Vite chosen over hand-written HTML). Initial scaffold committed on `main` (unavoidable for a new repo);
  `feature/agent-improvements` branched off it. Not pushed; no remote/Netlify touched.
- Presents all 5 projects from a single source of truth (`src/projects.js`): description, stack badges,
  "vs <equivalent>" positioning, GitHub link. Live-demo buttons point at **placeholder** `<repo>.onrender.com`
  URLs flagged "demo URL = TODO". order-book-simulator shown as a static SVG depth chart (no demo button).
- Added `netlify.toml` (build `npm run build`, publish `dist`, NODE_VERSION 22, SPA redirect), `.nvmrc`, README
  with Netlify connect + `gh repo create` steps. **Verified: `npm install` + `npm run build` succeed** (dist ~10kB).
- Next: user creates GitHub remote + connects Netlify; fill real Render URLs into `src/projects.js` after deploy.

## 2026-06-01 — deploy-engineer — options-pricing-calculator
- Branch `feature/agent-improvements` (2 commits: 1 `feat:` render.yaml, 1 `docs:` DEPLOY.md; not pushed).
- Added `render.yaml` (free web service, python runtime) starting `streamlit run app.py` bound to
  `$PORT`/`0.0.0.0` with `--server.headless --server.enableCORS false --server.enableXsrfProtection false
  --browser.gatherUsageStats false`; health check `/_stcore/health`, `PYTHON_VERSION=3.11`. Verified all
  six flags exist in installed Streamlit 1.46.1. `DEPLOY.md` documents both Render Blueprint and Streamlit
  Community Cloud paths. No source touched; app needs no secrets. User action: push repo, then Render ->
  New -> Blueprint, or share.streamlit.io -> New app.

## 2026-06-01 — deploy-engineer — backtesting-framework
- Branch `feature/agent-improvements` (1 `chore:` commit, not pushed). Added `render.yaml`: free-plan
  Python web service, `healthCheckPath: /`, build `pip install -r requirements-deploy.txt`, start
  `gunicorn dashboard:server --bind 0.0.0.0:$PORT --workers 2 --timeout 120` (matches the existing Procfile).
- Verified `dashboard.server` resolves to a Flask WSGI app (correct gunicorn target). Cross-repo dep handled
  via the pre-existing `requirements-deploy.txt` (optimizer from git URL) so local `-e ../...` dev is unbroken.
- User action: push engine repo to GitHub first, connect Render to this repo, set `PYTHON_VERSION` (pinned 3.11.9).

## 2026-06-01 — test-engineer — market-data-pipeline
- Branch `feature/agent-improvements` (3 commits: 1 `fix:`, 2 `test:`, not pushed). Added pytest-cov +
  pytest-asyncio (requirements) and `[tool.pytest.ini_options]` (asyncio_mode=auto) + `[tool.coverage]`
  branch mode in pyproject with `--cov-fail-under=85` gate (current ~99%).
- New tests **+59 (12 -> 71 passing)** via in-memory fakes (FakeWebSocket/FakeRedis/FakePool, no live infra):
  ws malformed/partial JSON, callback isolation, backoff cap + reconnect-to-max_retries; cache round-trips/
  capped lists/pubsub; storage column-order/arg passing; normalizer ms->UTC + OHLCV minute boundaries/
  carry-over/multi-symbol; pipeline batch-flush boundary, flush-failure restore, lifecycle. Coverage 31% -> 98.69%.
- **fix:** `websocket_client.connect()` reset `_retry_count` on bare TCP connect, so a flapping
  (connect-then-drop) stream reconnected forever (infinite loop). Now reset inside `_consume()` after a
  message arrives. Pre-existing ruff (1 UP035 in websocket_client + src findings) and mypy debt left untouched.

## 2026-06-01 — test-engineer — order-book-simulator
- Branch `feature/agent-improvements` (4 commits: 2 `test:`, 1 `ci:`, 1 `fix:`; not pushed).
- Added GoogleTest via CMake FetchContent (v1.15.2) + `gtest_discover_tests`; replaced the
  `demo_smoke` placeholder with `tests/test_order_book.cpp` (35 C++ tests: price-time priority,
  partial fills, market vs limit, cancel/modify, crossing spread, empty-book invariants,
  depth queries, multi-symbol routing). Added pytest-cov gate (`--cov-fail-under=80`) + 11 Python
  viz/simulator edge tests (headless Agg).
- Status: **ctest 35/35**, **pytest 22/22** (was 11), Python coverage **35% -> 97%**; ruff + mypy clean.
- `fix:` removed pre-existing unused imports + ambiguous `l` (F401/I001/E741) that were making the
  blocking CI `ruff check python tests` step red. Gaps: 3 uncovered `if save_path` else-branches (plt.show()).

## 2026-06-01 — test-engineer — options-pricing-calculator
- Branch `feature/agent-improvements` (3 `test:` commits, not pushed). No production code changed.
- Added pytest-cov branch coverage to pyproject (`[tool.pytest.ini_options]` + `[tool.coverage]`,
  `--cov-fail-under=95`) and pinned `pytest-cov>=7.0.0`; filtered the Agg "non-interactive" warning.
- New tests (+70, 36 -> 106): `test_accuracy.py` (reference values vs Hull/closed-form, parity grid,
  deep ITM/OTM + zero-T/zero-sigma Greek limits, binomial->BS convergence + build_tree, IV robustness)
  and `test_greeks_visualizer.py` (headless Agg smoke). Coverage 46% -> 98.6% (branch); visualizer 0->97%,
  binomial_tree 57->100%. No pre-existing failures found. CI ruff/mypy untouched and still green.

## 2026-06-01 — test-engineer — portfolio-optimization-engine
- Branch `feature/agent-improvements` (4 conventional `test:` commits, not pushed). No production code touched.
- Added pytest-cov (requirements + test extra) with `[tool.pytest.ini_options]`/`[tool.coverage]` in pyproject; gate `--cov-fail-under=90` (current ~95%).
- New tests (+76, 71 -> 147 passing): `test_config.py` (config validation/CLI/JSON precedence), `test_analysis.py` (run_analysis end-to-end, downloads monkeypatched offline), `test_optimizer_edge.py` (degenerate/singular-cov inputs, target-vol/return feasibility, metrics-module parity, backtester injected-returns API contract), `test_visualization.py` + `test_monte_carlo_edge.py` (headless Agg smoke + MC guards).
- Coverage 59% -> 94.72% (branch). Lifted config/analysis/visualization/monte_carlo from 0-53% to 93-100%. Pre-existing 3 ruff I001 (old test imports) + 12 mypy errors untouched (out of scope, non-blocking in CI).
- Gaps: metrics.py 88% (a few one-line guards/`inf` branches), export.py 90%, optimizer.py 97% (rare solver-failure raise lines).

## 2026-06-01 — test-engineer — backtesting-framework
- Branch `feature/agent-improvements` (2 `test:` commits, not pushed). Added pytest-cov reporting:
  branch coverage of `src/` via `[tool.pytest.ini_options]` + `[tool.coverage]` in pyproject
  (omit `interactive.py` UI), pinned `pytest-cov==7.1.0`, `--cov-fail-under=80` gate.
- Added `tests/test_edge_cases.py` (24 tests): degenerate/empty + single-bar analytics, trade
  analytics edge cases (inf profit factor, FIFO partial round trips, naked sell), metrics parity +
  known drawdown/underwater duration, execution cost decomposition + STOP slippage, MeanReversion/
  Momentum (were untested), and OptimizationRebalance across all 4 objectives + failure paths.
- Test status: **103 passed (was 79)**; coverage **78.4% -> 84.7%** (branch mode); ruff clean.
  strategy.py 67->86%, analytics.py 62->72%. No production code changed. Cross-repo metrics contract preserved.
- Remaining gaps: analytics plotting (152-196) + backtest benchmark-fetch (112-128) intentionally uncovered.

## 2026-06-01 — repo-hygiene — backtesting-framework
- Branch `feature/agent-improvements` (off clean `main`; 6 small conventional commits, not pushed).
- Added `LICENSE` (MIT, "Copyright (c) 2026 nicholim"), `.editorconfig`, `pyproject.toml`
  (ruff line-length 100 / select E,F,I,UP,B with E501 deferred to the formatter; mypy py3.10 gradual,
  `ignore_missing_imports`, files = `src`), `.pre-commit-config.yaml` (ruff + ruff-format +
  trailing-whitespace/eof/check-yaml/check-toml/large-files; mypy at `manual` stage),
  `.github/workflows/ci.yml` (matrix py3.10/3.11/3.12; checks out sibling optimizer repo so the
  editable dep resolves; ruff check blocking, ruff-format + mypy non-blocking, pytest).
- Pinned `requirements.txt` to installed versions; preserved `-e ../portfolio-optimization-engine`.
- Applied only safe ruff autofixes + trivial manual fixes (sorted/removed imports, removed dead vars,
  `zip(..., strict=False)`, renamed ambiguous `l`). No behavioral changes.
- Test status: **ruff check . clean; pytest 79 passed**. mypy reports 25 pre-existing typing errors
  (DataHandler attrs, bokeh figure kwargs, param_search lambdas/object args) — left as known debt,
  non-blocking in CI. ruff/mypy installed via pip for local verification.
- Deferred: P1 mypy debt cleanup + pytest-cov gate; P1 `render.yaml` (Dash web service); P2 README badges.

## 2026-06-01 — repo-hygiene — options-pricing-calculator
- Branch `feature/agent-improvements` (off clean `main`). 5 conventional commits, not pushed.
- Added `LICENSE` (MIT, "Copyright (c) 2026 nicholim"), `.editorconfig`, `pyproject.toml`
  (ruff: line-length 100, select E,F,I,UP,B; mypy: gradual, ignore_missing_imports, files=["src"]),
  `.pre-commit-config.yaml` (ruff + ruff-format + trailing-whitespace/eof/yaml/large-files),
  `.github/workflows/ci.yml` (matrix py3.10/3.11/3.12; ruff check + mypy non-blocking + pytest on push/PR to main).
- Pinned `pytest>=8.0.0`; other deps already carried `>=` lower bounds (left as-is, no downgrade).
- Applied ruff auto-fixes + format to existing source so CI's blocking `ruff check .` stays green
  (import sorting, removed unused `pytest` import, `zip(..., strict=False)`, line wraps). No logic changes.
- Test status: **ruff check . clean, mypy clean (4 files), pytest 36 passed**. ruff/mypy run via `uvx` (not installed system-wide).
- Deferred: P1 pytest-cov gate + edge-case tests; P1 `render.yaml` for Streamlit deploy.

## 2026-06-01 — repo-hygiene — market-data-pipeline
- Branch `feature/agent-improvements` (off clean `main`; 6 conventional commits, not pushed).
- Added `LICENSE` (MIT, "Copyright (c) 2026 nicholim"), `.editorconfig`, `pyproject.toml`
  (ruff line-length 100 / select E,F,I,UP,B + mypy py3.11 gradual, `ignore_missing_imports`,
  files = `src`), `.pre-commit-config.yaml` (ruff + ruff-format + trailing-whitespace/eof/check-yaml),
  `.github/workflows/ci.yml` (py3.11: ruff check, mypy non-blocking, pytest). Pinned `requirements.txt`.
- Test status: **pytest 12 passed**. ruff reports 26 pre-existing findings (10 E501, 16 auto-fixable:
  I001/UP017/UP035/F401); mypy reports 12 pre-existing errors in `src/cache.py` + `src/websocket_client.py`
  (mypy step is `continue-on-error`). Left unfixed per hygiene-only scope; no source touched.
- Deferred: ruff/mypy cleanup (P1); edge-case tests for the thin suite (P1); Dockerfile + render.yaml (P1).

## 2026-06-01 — repo-hygiene — portfolio-optimization-engine
- Branch `feature/agent-improvements` (5 conventional commits, not pushed).
- Added `LICENSE` (MIT, "Copyright (c) 2026 nicholim"), `.editorconfig`, `.pre-commit-config.yaml`
  (ruff + ruff-format + trailing-whitespace/eof/yaml/toml hooks), `.github/workflows/ci.yml`
  (matrix py3.10/3.11/3.12: ruff + mypy non-blocking + pytest).
- Extended existing `pyproject.toml` with ruff (line-length 100, select E,F,I,UP,B) and mypy
  (ignore_missing_imports, gradual, files = package only). Pinned `requirements.txt` to installed
  versions. Ignored tool caches in `.gitignore`. No public API touched.
- Test status: **pytest 71 passed** (1 deprecation warning from dateutil). ruff reports 23 pre-existing
  lint issues (10 auto-fixable import sorts; rest B905/E501/E702/B904 needing source edits) — left
  unfixed per hygiene-only scope, so ruff step is `continue-on-error`. mypy reports 12 pre-existing
  type errors (also non-blocking).
- Deferred: clean up the 23 ruff + 12 mypy findings (P1, needs care re: optimizer public API);
  pytest-cov gate (P1).

## 2026-06-01 — repo-hygiene — order-book-simulator
- Branch `feature/agent-improvements` (5 small commits, not pushed). Added OSS hygiene baseline (P0):
  `LICENSE` (MIT, "Copyright (c) 2026 nicholim"), `.editorconfig` (C++ + Python, LF/UTF-8/final newline),
  `.clang-format` (LLVM-based, C++17, 4-space, col 100), `pyproject.toml` (ruff line 100 select E,F,I,UP,B;
  gradual mypy `ignore_missing_imports`, targets `python/`), `.pre-commit-config.yaml`
  (trailing-whitespace/eof/check-yaml/large-files + ruff + ruff-format + clang-format),
  `.github/workflows/ci.yml` (C++ job: cmake configure/build + ctest; Python job: ruff+mypy+pytest on 3.11/3.12).
  Pinned `python/requirements.txt` to installed versions. Added `enable_testing()` + a `demo_smoke` ctest to
  `CMakeLists.txt` so ctest is green now (GoogleTests TODO-marked for test-engineer).
- Test/CI status: C++ cmake build PASS, `ctest` PASS (1/1 demo_smoke). `pytest` PASS (11/11). ruff/mypy/clang-format
  not installed locally (configs written; CI will exercise them).
- Next steps: author GoogleTest unit tests for the matching engine and register via `gtest_discover_tests`;
  then drop the smoke-test placeholder. README badges/CONTRIBUTING (P2) still pending.

## 2026-06-01 — system bootstrap
- Created the agent system: 6 subagents under `.claude/agents/`, `/improve-quant` command, `AGENTS.md`, this ledger.
- No repo changes yet — first improvement pass is the next step.
