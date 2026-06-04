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
  1. [x] `CSVDataHandler`/`DataFrameDataHandler` — DONE 2026-06-02 (offline/custom/intraday data, no yfinance dep).
  2. [x] Wire the 3 unused optimizer objectives (`sortino`, `max_return_target_vol`, `min_vol_target_return`) into `OptimizationRebalanceStrategy` — DONE 2026-06-02.
  3. [x] Native short selling — biggest real gap vs backtrader/zipline — DONE 2026-06-02 (opt-in
     `Portfolio(allow_short=True)`; signed positions + short cash accounting + inverse MtM +
     short-side exits + signed-FIFO round-trip P&L; default-off long-only proven byte-identical).
- **market-data-pipeline** (vs cryptofeed/ccxt-pro/ArcticDB): honest single-stream Binance-trades daemon.
  1. [x] Pluggable `ExchangeAdapter` protocol + a 2nd exchange (M, high — the headline gap). DONE 2026-06-02 (`ExchangeAdapter` Protocol in `src/adapters.py` + `BinanceAdapter`/`CoinbaseAdapter`, `EXCHANGE` config, default binance byte-identical).
  2. [x] Pluggable `StorageBackend` + Parquet/DuckDB sink — decouples from Timescale (which Render can't host), makes the demo runnable (M, high). DONE 2026-06-02 (`StorageBackend` protocol + `DuckDBStorage`, `STORAGE_BACKEND` config, default timescale).
  3. [x] `replay(symbol, start, end)` from store + OHLCV final-bar fix + bounded-buffer/backpressure cap — DONE 2026-06-02 (`Pipeline.replay()` async generator over the StorageBackend read API; `TickNormalizer.flush()`/`flush_all()` so single-trade + final bars emit; `MAX_BUFFER_SIZE` block-then-drop-oldest cap).
- **options-pricing-calculator** (vs QuantLib/py_vollib/mibian): already beats py_vollib/mibian (American via binomial, dividend yield).
  1. Higher-order Greeks (vanna/volga/charm) — closed-form, ~30 LOC (S, high).
  2. Black-76 futures-options pricer — completes the vollib "core three" (S, high).
  3. [x] Vectorized/batch pricing API — enables IV chains + a *real* IV surface — DONE 2026-06-02
     (`black_scholes_price_vec` / `greeks_vec` / `implied_volatility_vec` + `solve_iv_surface` /
     `plot_solved_iv_surface`). Heston/exotics/SVI still deferred (QuantLib-scale).
- **order-book-simulator** (vs ABIDES/mbt-gym): correct C++ price-time-priority engine, but a black box driven only by `main.cpp`; the Python sim and engine never touch.
  1. [x] **pybind11 bindings** — DONE 2026-06-02 (engine now programmable from Python; tests dropped subprocess-stdout parsing; FetchContent pybind11 `_orderbook` module re-exported via `python/orderbook/`).
  2. IOC/FOK/post-only order types — pure match-loop variants, no new data structures (S, high).
  3. [x] Throughput/latency benchmark harness — DONE 2026-06-02 (`benchmarks/bench.py` drives the engine via the binding; real M2 Pro numbers in the changelog entry). Strategic L-effort path to "ABIDES-lite": discrete-event latency clock → agent-based participants. WASM core for the showcase is a separate medium-effort demo win.
- **portfolio-optimization-engine** (vs PyPortfolioOpt/riskfolio/skfolio/cvxpy): scipy SLSQP+linprog; all top picks are **additive / API-safe** (the backtester's injected-returns + zero-arg `optimize_*` + `PortfolioResult.weights` contract stays intact).
  1. [x] True *solved* efficient frontier (sweep existing `optimize_min_vol_target_return`) — DONE 2026-06-02 (`solved_efficient_frontier`).
  2. [x] `optimize_hrp()` Hierarchical Risk Parity — DONE 2026-06-02 (solver-free, scipy `linkage`; ADDITIVE).
  3. [x] Ledoit-Wolf covariance shrinkage (opt-in, default off to preserve parity) — DONE 2026-06-02 (`calculate_returns(shrinkage=...)`). [x] Runner-up Black-Litterman (M) — DONE 2026-06-02 (`black_litterman.py` + `optimize_black_litterman`). Defer cvxpy backend (heavy dep, scope creep). All four portfolio P2 picks now DONE.

### P3 — competitive features (NEXT ROUND — user-requested 2026-06-03, deferred from the team-usability pass)
The user asked to do DX/UI/docs first (DONE 2026-06-03) and tackle *new competitive features* next. These are
the next set of "what comparable OSS/products have that we lack" picks, distilled from the prior
feature-architect gap analyses + the AGENTS.md domain caveats. **Run a fresh `feature-architect` gap analysis
per package first to confirm/re-prioritize before implementing — don't trust this list blindly.** Candidate
high-value picks (additive, contract-safe — verify each against the live API):
- **options-pricing** (vs QuantLib): [x] Monte-Carlo pricer + variance reduction — DONE 2026-06-03;
  [x] SVI vol-surface fit on the existing solved IV surface — DONE 2026-06-03. Still open: SABR fit,
  American/exotic via PDE/finite-difference, Heston (QuantLib-scale stretch goal).
- **portfolio-optimization** (vs PyPortfolioOpt/riskfolio/skfolio/cvxpy): [x] CDaR convex objective — DONE
  2026-06-03; [x] transaction-cost-aware rebalancing — DONE 2026-06-03. (CVaR was already done in P2.)
  **cvxpy backend deferred** (gap analysis: its convex wins — sector caps/turnover/shorting — are already
  reachable in scipy; only cardinality/MIQP is genuinely cvxpy-only; heavy dep → gate behind an optional
  extra later if ever wanted).
- **backtesting** (vs backtrader/zipline): [x] LongShortMomentum demo strategy exercising the `allow_short`
  path — DONE 2026-06-03; [x] commission/slippage model library — DONE 2026-06-03. (parameter-sweep grid
  ALREADY existed in `param_search.py` — only an optional `--sweep` CLI flag remains.) Still open:
  multi-asset portfolio-level analytics in the dashboard; a borrow-fee model for realistic short P&L.
- **market-data** (vs cryptofeed/ccxt-pro): [x] an **order-book/L2 depth** stream (not just trades) — the headline
  gap vs cryptofeed — DONE 2026-06-04 (`BinanceDepthAdapter`, opt-in `ENABLE_DEPTH`); [x] multi-symbol fan-out per
  connection — DONE 2026-06-04. Still open: an expanding-batch adapter contract (Kraken/Bitstamp batches → multiple
  Trades, flagged in prior passes); Coinbase `level2`/Kraken `book` depth adapters.
- **cpp/order-book** (vs ABIDES): [x] the strategic "ABIDES-lite" path — a discrete-event latency clock +
  agent-based participants — DONE 2026-06-04 (`python/abides_lite.py`). Still open: a **WASM** core for an in-browser
  showcase demo (medium-effort showcase win; needs emscripten).
- **cross-cutting:** wire the showcase "Live demo" buttons to the deployed Render URLs once the user deploys;
  optional `mkdocs`/site build for the docs (deferred this round to avoid a new build dep — Mermaid-in-Markdown
  was chosen instead).

---

## Changelog

## 2026-06-04 — main thread (/improve-quant) — thorough recheck of the P3 work + 2 hardening fixes
- **Theme:** user asked for a thorough recheck of the 2026-06-04 P3 features (market-data L2 depth + order-book
  ABIDES-lite), "not just pytest". Ran two deep `code-reviewer` subagents (one per changeset, parallel) PLUS
  main-thread runtime/edge-case probing against the real engine + real `Config` objects. Verdict: **no real
  correctness bugs.** All on `feature/agent-improvements` (NOT pushed); 2 small `fix:` commits + this note.
- **Empirically verified (by execution, not just tests):** abides_lite determinism (same-seed byte-identical incl.
  full arrival sequence; diff-seed divergent); latency reordering real (799). **Refuted reviewer "C2"** (zero-price
  market fills): the C++ engine does NOT rest unmatched MARKET orders (`best_bid` stays `None`; a later limit does
  not match a phantom $0 order) — so the GTC default for market orders is harmless. Market-data trades-only parity
  (`ENABLE_DEPTH` unset → no depth adapter, `normalize_depth`→`None`); single-symbol depth symbol resolves via the
  hint; multi vs single fan-out URL forms correct. Reviewer market-data "Criticals" (#1 `with_symbol_hint` return
  discarded, #2 `depth_client` stores trades URL) confirmed HARMLESS (in-place mutation works; adapter `ws_url()`
  overrides at connect).
- **Two low-risk hardening fixes applied (with tests):**
  - **order-book** `SimulationKernel.run(until=)`: was popping-then-discarding the first out-of-window event, so a
    resumed `run(until=...)` lost it. Now pushes it back before breaking (kernel is resumable in time slices).
    `max_events` was already safe (breaks before popping); the demo path uses `max_events` → output unchanged (799).
    pytest 59→**60**, coverage 98.91%; ctest **53/53**.
  - **market-data** depth task: the opt-in depth connection was fire-and-forget — a crash only surfaced as asyncio's
    "Task exception was never retrieved" warning. Added an `add_done_callback` observer that retrieves + logs an
    actionable error (ignores expected cancellation on stop); trades feed unaffected. pytest 273→**277**, 98.16%.
- **Not changed (deliberate):** `count_latency_reorderings` adjacent-pair scan undercounts true inversions (reviewer
  I1) — left as-is because it's a demo metric and changing it would change the reported headline number; the
  "evidence of reordering" claim stays directionally honest. MARKET-order TIF left GTC (engine never rests them).
- **Verification (REAL, main thread):** order-book **60 py** + **53/53 ctest** + demo OK; market-data **277**
  (98.16%); ruff/format/mypy clean on both. Other 3 packages untouched.
- **User actions:** unchanged — still **NOTHING PUSHED**; push `feature/agent-improvements` (now 5 commits past the
  prior squash base) when ready, then connect Render + Netlify.

## 2026-06-04 — main thread (/improve-quant) — P3 competitive features COMPLETE: market-data L2 depth + order-book ABIDES-lite
- **Theme:** the **final P3 round** — the 2 packages left untouched after 2026-06-03 (market-data, cpp/order-book),
  user-confirmed picks (market-data: L2 depth + multi-symbol; order-book: ABIDES-lite, **not** WASM). Two parallel
  `feature-architect` subagents on disjoint file sets (Python `packages/market-data` vs C++/Python `cpp/order-book`);
  subagents did NOT commit or edit this ledger/badges — the main thread owns commits + ledger to avoid races. Main
  thread **independently re-ran both suites + the C++ build + runtime demos** before committing. Two conventional
  commits on `feature/agent-improvements` (**NOT pushed**). NO new dependencies; NO existing public signature changed.
- **market-data (vs cryptofeed): 273 tests (was 222), coverage 98.14% (gate 85%).**
  - NEW `BookLevel`/`BookUpdate` types (`src/normalizer.py`) — the depth analogue of `Trade`, with `best_bid`/
    `best_ask` price properties. `TickNormalizer` gained an optional `depth_adapter` + `normalize_depth()` (returns
    `None` when no depth adapter is set ⇒ trades-only path **byte-identical**, proven in tests).
  - NEW `DepthAdapter` Protocol + `BinanceDepthAdapter` (`src/adapters.py`): the `<sym>@depth20@100ms` **partial-book**
    stream (self-contained top-N snapshots — no diff/sequence/REST-bootstrap bookkeeping, the reason chosen over
    incremental `@depth`/Coinbase `level2`). Binance's best-first bid/ask ordering maps straight onto `BookUpdate`.
    `supports_depth()`/`build_depth_adapter()` + `_DEPTH_ADAPTERS` registry (binance only today). `with_symbol_hint()`
    stamps the symbol on the lean single-symbol `/ws/<stream>` payload; multi-symbol uses the combined-stream
    `/stream?streams=…` endpoint whose `{"stream":…,"data":{…}}` wrapper supplies the symbol.
  - Threaded additively: `StorageBackend` gained `insert_book`/`query_book` (new `book` table in both `DuckDBStorage`
    — bids/asks as JSON, also Parquet-exported — and `TimeSeriesStorage` hypertable/JSONB); `RedisCache` gained
    `set_book`/`get_book` (`book:<symbol>`); `Pipeline` runs a SECOND `MarketDataClient` + `_on_depth_message`
    (cache→publish→persist) as a concurrent task, gated behind `ENABLE_DEPTH` / `--enable-depth` (off by default).
  - Multi-symbol fan-out per connection made explicit + tested (distinct symbols → distinct cache keys / independent
    OHLCV accumulators over one connection); single configured symbol stays byte-identical.
  - NEW tests `test_depth.py` (incl. end-to-end FakeWebSocket depth replay + single-symbol trades parity) +
    `test_fanout.py`; extended `test_cli.py`/`test_storage.py`/`test_duckdb_storage.py`. README depth+fan-out sections
    added; test badge 222→273.
- **cpp/order-book (vs ABIDES): ctest 53/53 unchanged; pytest 59 (was 41), coverage 98.91% (gate 80%), abides_lite 99%.**
  - NEW `python/abides_lite.py` — discrete-event sim layer **in Python** (deliberate: the C++ `OrderBook` matching
    stays the single source of truth, untouched; the layer only schedules *when* agent orders reach the book via the
    existing pybind11 binding + reads state back — no matching reimplemented, so GoogleTests stay at 53).
    `SimulationKernel`: min-heap event queue keyed by integer-ns sim time, WAKEUP/ARRIVAL processed strictly in time
    order (FIFO tie-break by insertion seq). Per-agent one-way `latency` ⇒ a WAKEUP at `t` emits orders ARRIVING at
    `t+latency`, so arrival order reflects latency not decision order — the headline ABIDES-distinguishing capability.
    Agents: `NoiseAgent` (ZI random) + `MarketMakerAgent` (symmetric POST_ONLY quotes around the live engine mid).
  - Latency reordering DEMONSTRATED: `count_latency_reorderings()`; seed-42 demo (4000 events → 2249 arrivals, 998
    trades) reports **799** reorderings; a deterministic unit test proves a fast-but-late-deciding agent arrives
    before a slow-but-early one. NEW `tests/test_abides_lite.py` (+18: kernel ordering, latency, agent behavior,
    determinism under fixed seed). README "ABIDES-lite / agent simulation" section + honest vs-ABIDES caveats (lite:
    no ITCH/OUCH, no message bus, no per-agent compute time, not 1000s-of-agents scale; NO stochastic-intensity/
    Avellaneda-Stoikov — fills come only from the real engine). Python test count 41→59.
- **Verification (REAL, main thread re-ran):** market-data **273** (98.14%), order-book **53/53 ctest** + **59 py**
  (98.91%); ruff/format/mypy clean on both. Runtime smokes: ABIDES-lite demo runs (799 reorderings); depth
  normalization + both URL fan-out forms + `supports_depth` gating + trades-only parity all verified by hand. The
  other 3 packages were NOT touched (git-confirmed) — options 238 / portfolio 285 / backtesting 228 stand from
  2026-06-03; cross-package contract (backtester↔optimizer, shared `metrics`) intact.
- **P3 IS NOW COMPLETE for all 5 packages.** Remaining items are all OPTIONAL polish (see below).
- **User actions:** still **NOTHING PUSHED** — push `feature/agent-improvements` when ready, then connect Render
  Blueprint + Netlify. **Optional follow-ups:** market-data Coinbase `level2`/Kraken `book` depth adapters (one class
  each) + a normalized per-level book table; order-book WASM showcase core (needs emscripten); options SABR fit +
  arbitrage-free SVI; backtesting multi-asset dashboard analytics + borrow-fee model + `--sweep` CLI; portfolio
  cvxpy cardinality extra. No mandatory backlog items remain.

## 2026-06-03 — main thread (/improve-quant) — P3 competitive features: options + portfolio + backtesting
- **Theme:** the **P3 competitive-features** round, scoped by the user to 3 packages (options-pricing,
  portfolio-optimization, backtesting), **gap-analysis-first**. Ran a fresh read-only `feature-architect`
  gap analysis per package, reported back + confirmed picks with the user, then implemented 2 additive,
  contract-safe picks per package via 3 parallel specialist subagents (disjoint file sets; subagents did NOT
  commit or edit this ledger — the main thread owns commits + the ledger to avoid races). Main thread
  **re-ran all 3 suites independently** before committing. All on `feature/agent-improvements` (**NOT pushed**).
  Three conventional commits (one per package). NO new dependencies; NO existing public signature changed.
- **Gap analysis corrected the backlog in two places:** (1) portfolio's **CVaR was already done** in P2 (the
  backlog conflated it with CDaR) → implemented **CDaR** instead; **cvxpy deferred** (its convex wins —
  sector caps/turnover/shorting — are already reachable in scipy; only cardinality/MIQP is genuinely
  cvxpy-only; heavy dep not worth it). (2) backtesting's **parameter-sweep grid already existed**
  (`param_search.py`) → implemented the long/short strategy + cost-model library instead.
- **options-pricing (vs QuantLib): 238 tests (was 209), coverage 99.35% (gate 95%).**
  - NEW `src/monte_carlo.py`: `monte_carlo_price(...) -> MCResult(price, std_error)` — GBM terminal-price MC
    for European calls/puts with **antithetic variates + a Black-Scholes control variate** (variance-minimizing
    beta; control-variate mean `S·e^(-qT)` so dividends stay correct; SE over antithetic pair-means). Reuses
    the existing BS for degenerate cases. Self-validates: seeded MC converges to closed-form BS within ~3 SE.
  - NEW `src/vol_surface.py`: Gatheral **raw-SVI** per-expiry fit (`fit_svi_slice`, `fit_svi_surface`) on the
    EXISTING `solve_iv_surface()` tidy DataFrame via `scipy.optimize.least_squares` on total variance w²=θ·T
    vs log-moneyness; `svi_total_variance`/`svi_implied_vol`/`svi_smile` evaluators (`SVIParams` NamedTuple).
  - Reachability: `main.py` MC demo line (MC ± SE vs BS); Streamlit IV-surface tab now overlays the fitted SVI
    smile on the solved-IV points (offline-safe). Both new modules **100% covered**.
  - Domain accuracy: documented MC as GBM-only (NOT stochastic-vol) and SVI as a smile fit/interpolation (NOT
    arbitrage-free); README moved MC + vol-surface fit into "does", LEFT exotics/barriers/finite-difference/
    Heston in the not-done column. Test badge 209→238.
- **portfolio-optimization (vs PyPortfolioOpt/cvxpy): 285 tests (was 260), coverage 96.43% (gate 90%).**
  - NEW `optimize_min_cdar(confidence=0.95, **cons)` + `_min_cdar_lp` (Chekhlov–Uryasev–Zabarankin LP via
    `scipy.optimize.linprog` HiGHS, mirrors `_min_cvar_lp`; decision vec `[w, alpha, s(T), u(T)]`, `u_t` tracks
    the running peak of the **uncompounded** `cumsum` return path). Inherits bounds + sector caps (`groups=`)
    for free; **zero-arg-callable**, returns a standard `PortfolioResult`. Wired into `analysis`
    (`_OBJECTIVE_METHODS` + `_selected_objectives`), `config.OBJECTIVE_CHOICES`, FastAPI `_OBJECTIVES`, and the
    Streamlit `OBJECTIVES` registry. Added `portfolio_cdar` helper.
  - NEW opt-in **transaction-cost-aware rebalancing**: `current_weights`/`transaction_cost` kwargs threaded
    through the SLSQP objectives (sharpe/min_vol/sortino/max_return_target_vol/min_vol_target_return) as an L1
    turnover penalty `sum(cost·|w−w_prev|)`. **Default `current_weights=None`/`cost=0` is byte-identical**
    (verified `np.allclose atol=1e-12`), preserving the zero-arg contract + `PortfolioResult` shape. Optional
    `current_weights`/`transaction_cost` fields on the FastAPI `/optimize` body (unknown tickers → 422).
  - **Cross-package contract re-verified:** backtesting suite re-run green (228) — its package was NOT edited.
    Documented CDaR's arithmetic (`cumsum`) drawdown as related-but-distinct from `metrics.py`'s geometric
    `max_drawdown` (no metrics-parity claim for the CDaR value). Test badge bumped to 285.
- **backtesting (vs backtrader/zipline): 228 tests (was 207), coverage 89.55% (gate 80%).**
  - NEW `LongShortMomentum` (`src/strategy.py`): ranks the universe each rebalance, LONGs top-K at `+w` /
    SHORTs bottom-K at `−w` via signed `SignalEvent(target_weight=±w)` (modeled on `CrossSectionalMomentum`;
    top_k capped to len//2 so baskets don't overlap; does NOT route through the optimizer). Finally exercises
    the fully-built-but-dormant `allow_short` path end-to-end. `--strategy long_short` (forces
    `Portfolio(allow_short=True)`). Documented as a MECHANICS demo — NO borrow-fee/locate model, so short P&L
    is idealized.
  - NEW `src/costs.py` model library: `CommissionModel` ABC + Percent(default)/PerShare(+minimum)/Fixed;
    `SlippageModel` ABC + Percent(default)/FixedBps. Injectable into `SimulatedExecution` via optional
    `commission_model=`/`slippage_model=`; the original `commission_pct`/`slippage_pct` floats stay and build
    the Percent models when omitted ⇒ **fills byte-identical** to before (regression-locked). `costs.py` 100%.
- **Verification (REAL, main thread re-ran):** options **238** / portfolio **285** / backtesting **228** — all
  green, gates met (99.35 / 96.43 / 89.55%). ruff + ruff-format + mypy clean across all three (incl.
  `mypy api/app.py` standalone). Cross-package contract intact (backtester ↔ optimizer, shared `metrics`).
  Total Python now ~751 across these 3 + market-data 222 + order-book 41 = ~1014 + 53 C++.
- **User actions:** still **NOTHING PUSHED** — push `feature/agent-improvements` when ready, then connect
  Render Blueprint + Netlify. **Next pass (remaining P3):** market-data L2 order-book depth stream (headline
  gap vs cryptofeed) + multi-symbol fan-out; cpp/order-book ABIDES-lite (discrete-event clock + agents) / WASM
  showcase core. Optional smaller items: options SABR fit + arbitrage-free SVI constraints; backtesting
  multi-asset dashboard analytics + borrow-fee model + `--sweep` CLI flag; portfolio cvxpy cardinality extra.

## 2026-06-03 — main thread (/improve-quant) — team-usability pass: one-command DX + UI/UX + architecture docs
- **Theme:** make the monorepo *team-usable* — clone-and-run in one command, every app a polished product,
  and a single "how it fits together" doc. Driven by the user's pick: runnability (DX) + UI/UX + docs/arch;
  competitive features explicitly **deferred to next round** (see new P3 backlog). All on
  `feature/agent-improvements` (NOT pushed). Delivered via 5 parallel specialist subagents (disjoint file
  sets, no commits/README/ledger edits by subagents to avoid races) + 1 serial docs agent; the main thread
  owned all commits, this ledger, and the final squash. **Subagents verified each suite; the main thread
  re-ran all four Python suites independently before committing.**
- **One-command DX (root):** `Makefile` (self-documenting `make help`; targets `setup`, `test`/`test-py`/
  `test-cpp`, `lint`/`format`/`format-check`/`typecheck`, `build-orderbook`, run targets `run-options`:8501 /
  `run-backtest`:8050 / `run-optimizer-api`:8000 / `run-optimizer-ui`:8502 / `run-market-data` (daemon) /
  `run-market-monitor`:8503 / `run-showcase`, `docker-up`/`docker-down`, `clean`). `make setup` builds ONE
  shared `.venv` installing **portfolio-optimization first** (respects the `-e ../portfolio-optimization`
  editable cross-package contract). Root `docker-compose.yml` (redis + market-data worker on
  `STORAGE_BACKEND=duckdb` + options + backtesting + portfolio-api; `docker compose config` validates clean;
  per-service Dockerfiles under `docker/`, market-data reuses its existing Dockerfile). `.devcontainer/`
  (Python 3.11 + C++ toolchain + Node, `postCreate: make setup`). `scripts/bootstrap.sh`.
- **UI/UX (each app now feels like a product):**
  - **options-pricing** Streamlit `app.py` polished — page config + cohesive finance theme
    (`.streamlit/config.toml`), `st.metric` cards for price + Greeks with help tooltips, validation, spinners,
    graceful empty/error states, offline banner. **209 tests** (gate 95% → 99.24%); +3 AppTest smokes.
  - **backtesting** Dash `dashboard.py` polished — header/status bar, grouped control panel, 4 KPI metric cards
    (return/Sharpe/Sortino/max-DD), shared Plotly `quantlab` template, `dcc.Loading`, in-UI `MarketDataError`
    alert (no stack trace), new `assets/dashboard.css` (no new dep). **205 tests** (gate 80% → 89.20%); +8.
  - **portfolio-optimization** — **NEW** Streamlit front-end `streamlit_app.py`: input source (bundled offline
    sample / CSV upload / live tickers w/ fallback), 8 objectives, weights chart + metric cards, **solved
    efficient frontier**, "all objectives" comparison, Black-Litterman prior→posterior mini-form. Reuses the
    existing optimizer API only (contract intact). **259 tests** (gate 90% → 96.53%); +8 AppTest smokes;
    `streamlit`+`plotly` pinned; app file kept outside the coverage source.
  - **market-data** — **NEW** read-only Streamlit `monitor.py`: recent trades + 1-min OHLCV + price/volume
    chart for a symbol, reading the same `StorageBackend` via `Pipeline.replay()` (no websocket). Deterministic
    seeded sample on an empty/fresh store with a clear "sample data" banner so it always renders. **222 tests**
    (gate 85% → 98.68%); +8; `streamlit`+`plotly` pinned; monitor outside the coverage source.
- **Docs & architecture:** NEW root `ARCHITECTURE.md` (7 Mermaid diagrams — system context + the one
  backtesting→portfolio editable dep, per-app data flows, deployment topology; responsibility/entry-point
  table; design-decisions section). NEW `docs/getting-started.md` (clone→run in 5 min: make path with
  URLs/ports, docker-compose path, devcontainer path, what each of the 4 UIs does). Root `README.md` "Run it
  (one command)" section + Layout updated (all 4 packages now have UIs) + links to the new docs. All 5 package
  READMEs: stale test badges bumped to REAL counts (options 201→**209**, backtesting 175→**205**, portfolio
  234→**259**, market-data 173→**222**, order-book Python 27→**41**; C++ 53 unchanged) + a "Web UI" section
  each. Honored the AGENTS.md domain caveats (no fabricated competitor capabilities).
- **Verification (REAL, main thread):** options **209** / backtesting **207** / portfolio **260** /
  market-data **222** Python suites all green with coverage gates met (99.24 / 89.20 / 96.53 / 98.68%);
  order-book unchanged (53 C++ ctest + 41 py). `make help` renders; `docker compose config -q` clean.
  ruff/format/mypy clean. **Total Python now ~898 + 41 = 939; +53 C++.**
- **Hands-on runtime verification (no-pytest) + 2 offline fixes:** drove every entry point for real —
  CLIs run, FastAPI endpoints + Dash run-callback driven over HTTP, C++ demo binary, showcase build/serve,
  all servers boot (health 200). Found & FIXED two offline-path defects: (1) `portfolio main.py --offline`
  crashed because the default universe included JPM/GS, absent from the fixture → added JPM + GS
  (real tickers, SPY-correlated synthetic 2023 series) to `sample_prices.csv`; (2) the Dash dashboard's
  optimizer half ignored `BACKTESTING_OFFLINE` → wired the flag through `AnalysisConfig.offline` +
  `YFinanceDataHandler(offline=...)` so a single `BACKTESTING_OFFLINE=1` offlines the whole page and the
  default tickers/dates run offline out of the box. Both locked with regression tests (backtesting 205→207,
  portfolio 259→260). With network, all apps already worked; these only affected the no-egress demo path.
- **Commits squashed** at the user's request into one conventional commit on `feature/agent-improvements`.
- **User actions:** still **NOTHING PUSHED** — push `feature/agent-improvements` when ready, then connect
  Render Blueprint + Netlify (unchanged from prior passes). New: `make setup && make test` is the fastest way
  for a teammate to validate locally; `docker compose up` for a no-local-Python demo.
- **Next pass = competitive features (P3 below).**

## 2026-06-02 — feature-architect — packages/market-data (Kraken + Bitstamp adapters, --exchange flag, dotenv parity)
- Branch `feature/agent-improvements` (NOT pushed). Optional-polish pass: two more keyless-public exchange
  adapters, a `--exchange` CLI flag, and `_load_dotenv_once()` parity with options-pricing. Purely ADDITIVE —
  the Binance default path is byte-identical, every new flag/adapter is opt-in. Scoped `git add` to
  `packages/market-data/...` + this file only (three other agents on this branch in parallel).
- **`KrakenAdapter`** (`EXCHANGE=kraken`): Kraken WebSocket **v1** public `trade` feed at the fixed
  `wss://ws.kraken.com`; subscribe `{"event":"subscribe","subscription":{"name":"trade"},"pair":[...]}`.
  Trade updates arrive as JSON **arrays** (not objects): `[channelID, [[price, volume, time, side,
  orderType, misc], ...], "trade", "XBT/USD"]`. Side `b`/`s` is **already the taker/aggressor side** (NO
  flip, unlike Coinbase). Symbols round-trip `btcusd` <-> `XBT/USD` (Kraken uses `XBT` for bitcoin).
  ASSUMPTION (documented in code + README): a batched update returns its **first** fill (the pipeline's
  one-message->one-Trade contract; expanding batches would widen the adapter contract — out of scope).
- **`BitstampAdapter`** (`EXCHANGE=bitstamp`): Bitstamp `live_trades_<pair>` channel at the fixed
  `wss://ws.bitstamp.net`; subscribe `{"event":"bts:subscribe","data":{"channel":"live_trades_btcusd"}}`.
  Trade envelope: `{"event":"trade","channel":"live_trades_btcusd","data":{"price","amount","type",
  "timestamp","microtimestamp",...}}`. ASSUMPTION (documented): `type` 0=buy/1=sell is the taker/aggressor
  side (no flip); `microtimestamp` (us) preferred, falls back to `timestamp` (s). Channel suffix == the
  pipeline's symbol (`btcusd`) directly. NOTE: Bitstamp subscribes one channel per frame; the single-payload
  client subscribes the first symbol's channel (documented).
- **Protocol widened to `dict | list`:** Kraken sends arrays, so `ExchangeAdapter.normalize_trade`,
  `TickNormalizer.normalize_trade`, `Pipeline._on_message`, and the WS-client callback type now accept
  `dict | list`; Binance/Coinbase gained a defensive `isinstance(raw, dict)` guard. `build_adapter` +
  `_ADAPTERS` + `build_exchange_adapter` route kraken/bitstamp to their default-URL adapters.
- **`--exchange` CLI flag** (`main.py`): overrides `EXCHANGE`/`.env`, defaults to the prior behavior when
  unset. Refactored the un-testable inline `main()` into importable `build_parser()` + `build_config(args)`
  + `main(argv=None)` (mirrors the backtesting CLI pattern) so the wiring is unit-testable without a loop.
- **dotenv parity:** replaced config.py's bare `load_dotenv()` with an idempotent `_load_dotenv_once()`
  (mirrors options-pricing: `find_dotenv(usecwd=True)`, latched, ImportError-safe). Real env vars override
  `.env`. `python-dotenv==1.0.0` was already pinned; `.env` already gitignored; `.env.example` EXCHANGE
  comment expanded to list all four venues.
- **Tests:** `test_adapters.py` — protocol conformance for all four, `build_adapter` kraken/bitstamp +
  case-insensitive + unknown-raises (the stale `"kraken"`-is-unknown test updated to `"bogus"`);
  Kraken/Bitstamp exact normalization, side mapping, batch-first-fill, event-object/non-trade-channel/
  empty/short/malformed -> None, fixed URL, subscribe payload + symbol round-trips, non-XBT pair, and a
  Binance/Coinbase list-payload-ignored guard; `build_exchange_adapter` kraken/bitstamp; an END-TO-END
  Kraken pipeline run driving `MarketDataClient` with a FakeWebSocket replaying canned array messages (+ a
  subscription-status object) -> asserts cache/publish/batch-flush, aggressor sides, and the subscribe JSON.
  NEW `test_cli.py` — `--exchange` selects each venue + lowercases + overrides a real `EXCHANGE` env var +
  default-unset preserves binance; dotenv loads a tmp `.env`, real env wins over `.env`, fills unset vars,
  idempotency latch, with an autouse env-snapshot fixture so `load_dotenv`'s os.environ mutations never leak.
- **Gate (REAL numbers):** `python -m pytest` -> **214 passed**, coverage **98.68%** (gate
  `--cov-fail-under=85`; `adapters.py` 98% — the only 3 uncovered lines are the Protocol `...` stubs;
  `config.py` 100%, `pipeline.py`/`normalizer.py` >=95%). `ruff check .` clean, `ruff format --check .` clean
  (26 files), `mypy` clean (10 source files). NOTE: a `# type:`-prefixed comment in the Bitstamp parser was
  mis-read by mypy 2.1.0 as a type comment (syntax error) — reworded to `# data["type"]:`.
- **User actions:** none beyond eventual push. **Follow-ups:** all market-data picks done; Kraken batch
  updates could be expanded to multiple Trades (needs a list-returning adapter contract); Bitstamp
  multi-symbol needs one subscribe frame per channel (client currently sends one).

## 2026-06-02 — feature-architect — packages/backtesting (dashboard: allow_short toggle + hrp objective)
- Branch `feature/agent-improvements` (NOT pushed). Polish pass closing the optional follow-up flagged by
  prior backtesting passes: surfaced native short selling + the HRP objective in the Dash dashboard
  (`dashboard.py`), which prior passes deliberately left untouched because the objective dropdown is driven
  by the shared `OBJECTIVE_TO_KEY` map. Purely ADDITIVE: both defaults preserve current behavior (long-only,
  existing objectives). Shared `metrics` math untouched; optimizer used only via the existing zero-arg public
  API (`optimize_hrp`, already wired into `OptimizationRebalanceStrategy`); portfolio-optimization package NOT
  modified. Scoped `git add` to `packages/backtesting/...` + this file only (parallel agents on other packages).
- **Verified the shared path before editing `OBJECTIVE_TO_KEY`:** the map's values are the keys
  `run_analysis` uses in its `results` dict (`analysis._OBJECTIVE_METHODS`), and the dashboard calls
  `run_analysis(objective="all")` then indexes `analysis["results"][OBJECTIVE_TO_KEY[obj]]`. `hrp` is ALREADY
  a valid `run_analysis` objective (added to `_OBJECTIVE_METHODS` + `_selected_objectives` by the portfolio
  pass), so `objective="all"` computes it and `analysis["results"]["hrp"]` resolves. Therefore adding
  `"hrp": "hrp"` to the dashboard map is consistent with the analysis path — no analysis/marker code touched
  beyond an additive `_MARKER["hrp"]` entry (frontier_figure already `.get`-defaults unknown keys).
- **Changes (all additive, defaults unchanged):**
  - `OBJECTIVE_TO_KEY` gains `"hrp": "hrp"` (+ a `_MARKER["hrp"]` style + an HRP-uppercase dropdown label).
    The dropdown is generated from the map, so `hrp` now appears as a selectable objective and reaches
    `OptimizationRebalanceStrategy(objective="hrp")` (which already has an `optimize_hrp()` branch).
  - `backtest_optimized(...)` gains `allow_short=False` (default = unchanged long-only), threaded into
    `Portfolio(initial_capital=100_000, allow_short=allow_short)`.
  - Layout: a `dcc.Checklist` (id `allow-short`, default `[]` = off) added under the Objective dropdown; the
    callback gains a `State("allow-short", "value")`, maps `"short" in value` → `allow_short`, and the status
    line notes "(short selling enabled)" when on.
- **Tests (`tests/test_dashboard.py`, NEW: +8, 191 → 199), no server/network:** module imports + `build_app()`
  builds; `OBJECTIVE_TO_KEY["hrp"] == "hrp"`; EVERY map value is a valid `run_analysis` objective (asserted
  against the optimizer's own `_OBJECTIVE_METHODS` so the dashboard can't drift from the analysis path); prior
  objectives still present; `_MARKER` covers every objective; and `backtest_optimized` (with `Backtest`
  monkeypatched to a capturing stub — no event loop, no fetch) builds a long-only `Portfolio` by default, a
  short-enabled one when `allow_short=True`, and routes `objective="hrp"` into `OptimizationRebalanceStrategy`.
- **Gate (REAL numbers):** `python -m pytest` → **199 passed** (was 191), coverage **89.20%** (gate
  `--cov-fail-under=80` met). `dashboard.py` is OUTSIDE the `--cov=src` source (it lives at the package root,
  not `src/`), so the new tests validate behavior without diluting the gate — consistent with how `main.py`
  is treated. `ruff check .` clean, `ruff format --check .` clean (24 files), `mypy dashboard.py main.py src`
  clean (15 files). README "Web dashboard" section updated (hrp option + Allow-short checkbox).
- **Safety note / limitation:** the optimizer emits long-only target weights, so the `allow_short` toggle only
  has executable effect when a rebalance reduces a holding below a prior allocation (no negative target weights
  are generated by these objectives); it's wired correctly and proven to reach the Portfolio, but a dedicated
  long/short objective would be needed to exercise shorting heavily — out of scope for this polish pass.
- **User actions:** none beyond eventual push. **Follow-ups:** all backtesting P2 picks + the dashboard
  reachability follow-up are now DONE; a short-friendly demo strategy remains the only optional item.

## 2026-06-02 — feature-architect — packages/options-pricing (surfaced solved IV surface + batch pricing in Streamlit)
- Branch `feature/agent-improvements` (NOT pushed). Polish-only UI pass: surfaced the library-level solved IV
  surface + vectorized batch pricing in the Streamlit app (they existed in `src/` but weren't reachable from
  the UI — the "do next #1" follow-up). Purely ADDITIVE / UI-LAYER ONLY: NO library signature changed, the
  existing Calculator + Live market tabs are untouched, and `python main.py` (CLI) still works. Scoped
  `git add` to `packages/options-pricing/...` + this file (three other agents on this branch in parallel).
- **New "IV surface" tab in `app.py`** (third tab alongside Calculator + Live market): for a chosen symbol it
  fetches chains across MULTIPLE expiries, solves OUR IV per (strike, expiry) via the vectorized solver, and
  renders the REAL solved IV surface (`plot_solved_iv_surface`) + a per-expiry IV smile (`st.line_chart` of a
  strike×expiry IV pivot) + the tidy solved-IV table. Reuses `solve_iv_surface`/`plot_solved_iv_surface`
  verbatim (no reimplementation). A new `build_surface_chains(symbol, expiries, type, offline)` helper
  assembles `(chains_by_expiry, expiry_years, spot)` from `get_option_chain`+`_years_to_expiry`+`get_spot`,
  skipping sparse/failed expiries (never raises). **Offline degradation:** live mode takes the nearest N real
  expiries (`list_expirations`), and on ANY `MarketDataError` (or the "Offline sample" checkbox) falls back to
  `_offline_surface_expiries()` — a spread of synthetic future dates (20/45/90/160d) over the bundled fixture
  (offline `get_option_chain` ignores the requested expiry, so only T varies) → a genuine multi-expiry surface
  with no network. Empty-surface and no-usable-data cases show an info message instead of crashing.
- **Vectorized batch pricing** also surfaced in the same tab (low-risk): a strike-grid section prices 25
  strikes in ONE broadcasted `black_scholes_price_vec` call and line-charts price vs strike.
- **Tests (`tests/test_iv_surface_ui.py`, NEW: +5, 201 → 206):** headless (`Agg`, no network) smoke tests that
  call the SAME underlying functions the tab calls — `_offline_multi_expiry` builds a 4-expiry offline chain
  (distinct increasing T), `solve_iv_surface` solves a non-empty tidy surface (≥2 expiries, sane IV fractions),
  `plot_solved_iv_surface` renders+saves for call and renders headless for put, and a Streamlit `AppTest`
  builds `app.py` offline with no exception and asserts all 3 tabs are present.
- **Gate (REAL numbers):** `python -m pytest` → **206 passed** (was 201), coverage **99.24%** (gate
  `--cov-fail-under=95` met; `greeks_visualizer.py` 98%, `market_data.py` 100%, `black_scholes.py` 99% —
  `app.py` is outside `--cov=src` so the UI is AppTest-exercised but not coverage-counted). `ruff check .`
  clean, `ruff format --check .` clean (14 files), `mypy src` clean (5 files).
- **User actions:** none beyond eventual push. **Follow-ups:** could add a date-axis (instead of T-axis) toggle
  on the surface, or cache live chains across reruns (`st.cache_data`); no library work remains.

## 2026-06-02 — feature-architect — cpp/order-book (native C++ micro-benchmark, isolates matching path)
- Branch `feature/agent-improvements` (NOT pushed). Implemented the optional-polish item "a native C++
  micro-benchmark to isolate the matching path from pybind11 overhead." C++/CMake/docs only — no engine
  logic, bindings, or Python touched. Scoped `git add` to `cpp/order-book/...` + this file (three other
  agents editing other packages on this branch in parallel — did NOT `git add -A`).
- **`benchmarks/bench.cpp` (NEW):** pre-generates a synthetic order flow IN C++ (excluded from timing),
  then times `OrderBook::add_order` in a tight loop with `std::chrono::steady_clock`. Workload mirrors the
  Python harness `benchmarks/bench.py` exactly (mid=150.0, tick=0.01, `gauss(0,0.02)` drift per order,
  ~80% LIMIT / 20% MARKET, both sides, qty in {10,25,50,100,200}, LIMIT price offset -2..+2 ticks) so the
  numbers are directly comparable to the binding numbers. CLI args `[orders] [repeat] [seed]` (default
  500k / 3 / 7, runs in ~1s). Reports throughput (orders/sec) + per-order latency p50/p90/p99/max in BOTH
  ns and µs, plus a one-line summary. Uses `std::mt19937` (vs CPython's Mersenne in bench.py) so trade
  counts differ by ~400 on the same distribution — workload shape identical.
- **CMake wiring:** new `order_book_bench` executable target, guarded by `option(BUILD_BENCHMARK ON)`,
  links `orderbook_core`, forced `-O2` even under a Debug/no-build-type configure. It is a plain
  executable, deliberately NOT registered with ctest (no `add_test`/`gtest_discover_tests` references it),
  so `ctest` stays a pure test run. Independent of the demo / GoogleTest / pybind11 targets.
- **Build/gates (REAL numbers, clean Release build):** `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release &&
  cmake --build build` → all targets green (orderbook_core, order_book_demo, **order_book_bench**,
  orderbook_tests, _orderbook). `ctest --test-dir build` → **53/53 passed** (0.24s); `ctest -N | grep
  bench` → empty (bench correctly NOT a test). clang-format not installed on this machine (skipped; code
  follows the existing .clang-format 4-space style). Python suite untouched (not re-run; no Python edited).
- **Measured numbers — Apple M2 Pro (arm64, macOS 26.5), Release/-O2, 500,000 orders/run, seed 7:**
  - **Native C++:** **~7,790,000 orders/sec** (best of 3); latency **p50 84 ns / p90 250 ns / p99 500 ns**,
    mean 130 ns, max 22.7 µs; 466,116 trades.
  - **vs Python binding** (existing, same machine/workload): ~186,000 orders/sec; p50 4.8 µs / p90 10.6 µs /
    p99 18.1 µs; 466,518 trades.
  - **Native is ~42× faster** (7.79M vs 186k orders/sec; p50 84 ns vs 4.8 µs) — confirms the pybind11
    call/marshalling cost dominates the binding numbers; the pure matching loop is far cheaper.
- **Docs:** README "Benchmark the matching engine" section split into Native-C++ + Python-binding
  subsections (build+run commands, both result tables clearly labeled with arch/build-type/workload, the
  ~42× comparison + the RNG-difference caveat); project-structure tree gains `bench.cpp`.
- **User actions:** none beyond eventual push. **Follow-ups:** none required; could add a CMake preset or a
  Makefile shortcut, or a CI job that runs the bench non-gating for trend tracking (optional).

## 2026-06-02 — main thread (/improve-quant) — MILESTONE: entire P2 feature backlog DONE + wired end-to-end
- **All P2 feature-comprehensiveness picks across all 5 packages are now implemented, tested, and reachable
  end-to-end** (CLI/API/UI/sim), on `feature/agent-improvements` (43 commits past the squashed base
  `9d77a3b`; NOT pushed). Delivered this session across 7 parallel batches of specialist subagents:
  - **options-pricing** (201 tests): higher-order Greeks (vanna/volga/charm), Black-76 pricer, vectorized
    batch price/greeks/IV API, true solved IV surface; live chains + Finnhub spot w/ 401 warning + offline.
  - **portfolio-optimization** (251 tests): solved efficient frontier, Ledoit-Wolf shrinkage (opt-in), HRP,
    Black-Litterman — all four exposed in CLI/FastAPI (`/optimize/black-litterman` + example); resilient yfinance.
  - **backtesting** (191 tests): CSV/DataFrame data handlers, 7 wired optimizer objectives, native short
    selling (opt-in `allow_short`, long-only parity proven), resilient yfinance; CLI `--allow-short/--data-csv/
    --offline/--objective hrp`; HRP wired into `OptimizationRebalanceStrategy`.
  - **market-data** (173 tests): pluggable StorageBackend (Timescale + DuckDB), `replay()` feeder, OHLCV
    final-bar fix, backpressure cap, pluggable ExchangeAdapter (Binance + Coinbase), fail-fast on dead infra.
  - **cpp/order-book** (53 C++ ctest + 41 Python): IOC/FOK/post-only (TimeInForce), pybind11 bindings,
    throughput/latency benchmark (~186k orders/s, p50 4.8µs/p99 18µs on M2 Pro), and `simulator.py` now drives
    the REAL C++ engine through the binding (visualizer renders real engine state).
- **Deploy posture:** root `render.yaml` now defaults the market-data worker to `STORAGE_BACKEND=duckdb`
  (Redis + disk only — NO external Timescale for the demo; Timescale stays an opt-in `sync:false` path).
  Offline flags documented per service. Docs reconciled across all 5 READMEs + showcase (fixed a false
  Monte-Carlo claim + a portfolio self-contradiction). Showcase builds green.
- **Verification (all green):** options 201 / portfolio 251 / backtesting 191 / market-data 173 / order-book
  53 C++ + 41 py = **910 tests**. Cross-package contract (backtester ↔ optimizer, shared `metrics`) intact.
  ruff + ruff-format + mypy clean across all packages.
- **Branch hygiene:** the stale `feature/agent-improvements-stale-orphan` was deleted; only
  `feature/agent-improvements` + `main` remain.
- **User action:** push `feature/agent-improvements` when ready (nothing pushed, per standing rules); then
  connect Render Blueprint + Netlify. **Next pass:** optional polish only — dashboard checkboxes for
  allow_short/hrp, more exchange adapters (Kraken/Bitstamp ~1 class each), a native C++ micro-benchmark, and
  surfacing the vec IV-surface in the Streamlit app. No formal backlog items remain.

## 2026-06-02 — feature-architect — packages/portfolio-optimization (HRP + Black-Litterman reachable end-to-end)
- Branch `feature/agent-improvements` (NOT pushed). Wired the library-only `optimize_hrp` /
  `optimize_black_litterman` into the CLI/analysis, FastAPI demo, and a runnable example — closing the
  "library-API-only" follow-up the prior BL/HRP passes flagged. Purely ADDITIVE / API-SAFE: NO optimizer
  math touched, NO existing public signature changed, the zero-arg `optimize_*` contract +
  `PortfolioResult.weights` + `metrics` parity + the backtester injected-returns path all intact. Scoped
  `git add` to `packages/portfolio-optimization/...` + this file (two other agents editing backtesting +
  order-book on this branch in parallel).
- **HRP in the CLI/analysis (`analysis.py`, `config.py`):** added `"hrp" -> "optimize_hrp"` to
  `_OBJECTIVE_METHODS` and the `_selected_objectives` single-objective map, and `"hrp"` to
  `config.OBJECTIVE_CHOICES`. HRP is zero-arg (uses the injected cov), so it slots into `run_analysis`'s
  uniform `method()` loop exactly like `risk_parity` — `--objective hrp` and inclusion in `all` both work,
  flow through metrics + Monte Carlo, and `print_report` prints it (its `_make_result` populates
  sortino/cvar so the existing print path needs no change).
- **HRP in FastAPI (`api/app.py`):** added `"hrp" -> "optimize_hrp"` to `_OBJECTIVES`, so `POST /optimize`
  with `objective:"hrp"` returns 200 + weights (the existing `getattr(optimizer, method)()` zero-arg call
  path is unchanged — no logic duplicated). `/objectives` now lists `hrp`.
- **Black-Litterman — chose the FastAPI endpoint AS WELL AS an example (the cleaner option both ways):**
  BL needs views (P/Q) which don't fit the flat argparse `--objective` surface, so it is deliberately NOT a
  CLI objective (documented in the README). Instead: (1) a NEW typed endpoint `POST /optimize/black-litterman`
  accepts `views: [{assets:{ticker:loading}, q, confidence?}]` (absolute or relative), optional
  `market_weights`, `tau`, `risk_aversion`; it translates the payload to `P`/`Q`/`Omega` and calls the
  EXISTING `optimize_black_litterman` + `black_litterman_returns` (no math duplicated), returning weights
  plus the `prior_returns` and view-adjusted `posterior_returns`. With no views the posterior == prior
  (equilibrium-prior max-Sharpe). `confidence` (in (0,1]) scales the default `diag(tau P Sigma P^T)` Omega
  by `1/confidence` so higher confidence -> tighter uncertainty -> stronger tilt. Refactored the shared
  inject-returns construction into `_build_optimizer` (DRY, `/optimize` behavior unchanged). (2) NEW
  runnable `examples/black_litterman_demo.py` shows prior -> bullish AAPL view -> posterior shift ->
  optimized weights tilting toward AAPL (offline; verified AAPL weight 30.04% -> 44.68%).
- **Tests (+17, 234 -> 251):** `test_analysis.py` — `--objective hrp` end-to-end through `run_analysis`
  (weights sum to 1, long-only, objective tag, metrics+MC+primary all keyed on hrp), `all` includes hrp,
  `print_report` emits "HRP"; `_selected_objectives("hrp")` + `all`-covers-hrp + method-map. `test_config.py`
  — `hrp` is a valid objective choice. NEW `test_api.py` (FastAPI `TestClient`, no network) — `/objectives`
  lists hrp+black_litterman; `/optimize` hrp -> 200 + weights sum to 1 + long-only; unknown objective 422;
  BL no-views posterior==prior + weights sum to 1; BL bullish view raises AAPL posterior AND weight; higher
  confidence pulls posterior nearer q; relative view accepted; market_weights accepted; unknown view/market
  ticker -> 422; bad returns shape -> 422.
- **Gate (REAL numbers):** `python -m pytest` -> **251 passed** (was 234), coverage **96.53%** (gate
  `--cov-fail-under=90` met; `analysis.py` 98%, `config.py` 100%, `black_litterman.py` 100%, `optimizer.py`
  98% — unchanged). `api/` is outside the `--cov` source (`[tool.coverage.run] source =
  ["portfolio_optimization_engine"]`), so `test_api.py` validates behavior without diluting the gate.
  `ruff check .` clean, `ruff format --check` clean (32 files), `mypy` clean (11 source files; also ran
  `mypy api/app.py` standalone — clean).
- **Docs:** README CLI `--objective` choices (+hrp), a BL-is-not-a-CLI-objective callout, the API table
  (+`/optimize/black-litterman` row, supported-objectives line +hrp, view payload shape), the Quick Start
  examples block + project tree (+`black_litterman_demo.py`).
- **User actions:** none beyond eventual push. **Follow-ups:** could add a `"black_litterman"` branch to the
  backtester's `OptimizationRebalanceStrategy` (works today via the zero-arg contract; needs views to differ
  from equilibrium); a Streamlit/CLI views form is possible but low value. All four portfolio P2 picks remain
  DONE — this pass only surfaced them.

## 2026-06-02 — feature-architect — packages/backtesting (CLI reachability: short/CSV/offline + HRP objective)
- Branch `feature/agent-improvements` (NOT pushed). Made recently-landed backtesting capabilities reachable
  end-to-end from the CLI (they existed in the library but weren't wired into `main.py`). Purely ADDITIVE:
  every new flag defaults to the prior online, long-only behavior, and `python main.py` with no args still
  runs the original four-strategy DuckDB demo (`run_demo`) byte-for-byte. Cross-package contract intact —
  shared `metrics` math untouched, the optimizer used only via its existing zero-arg public API
  (`optimize_hrp()`), and the portfolio-optimization package NOT modified. Scoped `git add` to
  `packages/backtesting/...` + this file only (two other agents editing portfolio-optimization / order-book
  in parallel on this branch).
- **`main.py` refactor (testable, behavior-preserving):** the original `main()` body became `run_demo()`;
  `main(argv=None)` now parses args via a new `build_parser()` and dispatches to `run_demo()` when no
  `--strategy` is given (unchanged path) or to `run_single(args)` for one configurable backtest. Arg handling
  is factored into importable pure functions — `build_data_handler`, `build_portfolio`, `build_strategy`,
  `run_single` — so the CLI is unit-testable without a live network.
- **New CLI flags (all default to prior behavior):**
  - `--allow-short` → threads into `Portfolio(allow_short=True)` for a long/short backtest (default off).
  - `--data-source {yfinance,csv}` + `--data-csv PATH` (+ `--csv-combined`) → loads OHLCV via the existing
    `CSVDataHandler` (per-ticker `<dir>/<TICKER>.csv` by default, or one combined file with a `symbol`
    column under `--csv-combined`) instead of always hitting yfinance. `--data-csv` implies the csv source;
    `--data-source csv` without a path raises a clear error.
  - `--offline` → drives a deterministic run off the bundled fixture (sets `BACKTESTING_OFFLINE` for the
    `run_single` scope so BOTH the no-store handler path AND the DuckDB-store fetch path serve the fixture;
    the env var is restored in a `finally`, verified non-leaking by test). Also threaded into the
    `YFinanceDataHandler(offline=...)` arg.
  - `--strategy {sma,mean_reversion,momentum,optimize}` selects a single strategy; `--objective` (incl.
    `hrp`), `--lookback`, `--rebalance-freq`, `--target`, `--tickers`, `--start`, `--end`, `--capital` are
    the universe/window knobs.
- **HRP wired into `OptimizationRebalanceStrategy`:** added an `elif self.objective == "hrp":
  result = opt.optimize_hrp()` branch to `_compute_targets` — uses ONLY the optimizer's existing injected-cov
  zero-arg API (no portfolio-optimization edits). HRP is solver-free, long-only, fully-invested, so emitted
  weights are always backtester-executable. **Black-Litterman deliberately SKIPPED** in the switch and
  documented why: it needs investor views (P/Q) to differ from the equilibrium prior, and this walk-forward
  strategy has no view-generation mechanism — with no views BL collapses to the prior, so it would add weight
  for no behavioral difference.
- **Dashboard:** left UNCHANGED (noted, not done). The Dash objective dropdown is driven by `OBJECTIVE_TO_KEY`
  which ALSO feeds the optimization-analysis path (`run_analysis` + `_MARKER`), so adding `hrp` there would
  require touching the analysis/marker code — outside backtesting-only scope and higher-risk with parallel
  agents. The CLI is the clean in-scope reachability path; an `--allow-short` checkbox would likewise require
  re-wiring the tightly-coupled callback signature, so it's deferred.
- **Tests (`tests/test_cli.py` NEW: +14; +2 HRP tests in `test_data_handlers.py`) → 175 → 191.** No live
  network (CSV on `tmp_path`, the offline fixture, importable builders): defaults preserve long-only/yfinance/
  online; `--allow-short` builds a short-enabled `Portfolio` + capital threaded; `--data-csv` builds a
  `CSVDataHandler` and runs a full backtest off disk (per-ticker AND combined); `--data-source csv` without a
  path raises; `--offline` sets the handler flag, two runs are byte-identical (deterministic equity curve),
  and the env var doesn't leak; `--objective hrp` parses + an HRP optimize backtest runs end-to-end > 60 bars;
  and HRP at the strategy level returns a valid long-only simplex (sums to 1, all >= 0) + a full HRP backtest
  keeps positions non-negative.
- **Gate (REAL numbers):** `python -m pytest` → **191 passed** (was 175), coverage **89.20%** (gate
  `--cov-fail-under=80` met; `data_handler.py` 96%, `portfolio.py` 97%, `strategy.py` 88%; `main.py` is
  outside the `--cov=src` source so the CLI is test-exercised but not coverage-counted). `ruff check .` clean,
  `ruff format --check .` clean (23 files), `mypy main.py src` clean (14 files).
- **Docs:** README Quick Start gained a "Configurable single backtest (CLI flags)" subsection (examples +
  flag table + the CSV convention) and `hrp` added to the MPT objective list.
- **User actions:** none beyond eventual push. **Follow-ups:** optional dashboard `--allow-short` checkbox +
  `hrp` dropdown option (needs the shared `OBJECTIVE_TO_KEY`/`run_analysis` path extended first); a
  short-friendly demo strategy; expose `--exchange`-style parity knobs if more sources are added.

## 2026-06-02 — feature-architect — cpp/order-book (simulator now drives the LIVE C++ engine end-to-end)
- Branch `feature/agent-improvements` (NOT pushed). Closed the last open order-book glue item: the Python
  simulation layer now drives the REAL C++ matching engine through the pybind11 binding, and the
  visualizer renders that real engine state. NO C++/binding/CMake changes — Python + tests + README only.
  Scoped `git add` to `cpp/order-book/...` + this file.
- **`python/simulator.py` rewritten:** the old pure-Python toy (generated order dicts → `orders.json`, no
  matching) is replaced by two clear pieces with ONE source of truth (the C++ engine):
  - `MarketSimulator.generate_random_orders(n, use_tif=False)` — a deterministic flow *generator* only
    (no matching). ~80% LIMIT / 20% MARKET, both sides, prices around a `random.gauss` drifting mid;
    `use_tif` sprinkles IOC/FOK/POST_ONLY onto a minority of LIMIT orders. Dropped the numpy dependency
    (now `random`-only, still reproducible via `random.seed`). `save_orders` JSON dump kept for convenience.
  - `EngineSimulator(symbol, depth_levels).run(flow)` — submits each order via `orderbook.OrderBook.add_order`
    (the compiled binding), accumulates every fill the ENGINE returns, samples the live spread
    (`get_best_bid`/`get_best_ask`) each step, and reads the final depth ladders + best bid/ask straight off
    the engine. Returns a `SimulationResult` dataclass (trades/spreads/bids/asks/best_bid/best_ask as plain
    dicts so the visualizer/JSON need no binding types). `simulate(...)` one-shot helper + a `main()` CLI
    (`--orders/--symbol/--seed/--tif/--plot DIR`). The matching is now done ENTIRELY by C++ — no Python book.
- **`python/visualizer.py`:** existing dict-based `plot_depth_chart`/`plot_trade_tape`/`plot_spread_over_time`
  unchanged (their dict schema already matches `SimulationResult` output). Added `plot_simulation(result,
  out_dir=None)` that renders all three charts directly from a real-engine `SimulationResult` (depth always;
  tape/spread when data present), headless-safe (Agg). `--plot` wires the CLI to it.
- **Tests:** `tests/test_simulator_engine.py` NEW (+14, 27→41) — drives the binding end-to-end and asserts:
  real non-empty trades + the `best_bid <= best_ask` invariant; depth ladders sorted (bids desc / asks asc),
  best bid/ask agree with ladder tops, every level qty>0 / order_count>=1, spread series non-negative; a
  hand-built crossing matched against the C++ output (price/qty/buyer/seller ids + 40 resting remainder);
  TIF flow runs through the engine; all four TIFs map; empty flow; flow-generator invariants (market=0
  price, reproducible, `use_tif` emits non-GTC, market stays GTC); visualizer renders real output headless
  (all charts written; show-branch no-outdir; empty book renders depth only); and the CLI `main()` summary +
  `--plot` paths. `test_python_viz.py` updated (TestSimulator kept for the generator; added an
  `importorskip("orderbook")` since `simulator` now imports the binding at module load).
- **Build/gates (REAL numbers, clean build):** `cmake -S . -B build && cmake --build build` green (demo +
  GoogleTest + `_orderbook` module). `ctest --test-dir build` → **53/53 passed** (0.45s). `python -m pytest`
  → **41 passed**, coverage **99.03%** (gate `--cov-fail-under=80`; `simulator.py` 98%, `visualizer.py` 100%).
  `ruff check`/`ruff format --check` clean on changed Python. Live demo: 500 orders (seed 42) → 297 real
  fills (vol 21,645), best_bid 150.51 ≤ best_ask 150.67, 10 resting levels each side — all from the C++ engine.
- **Retired:** the pure-Python order-flow-to-JSON-only path as the "simulation" — there is no longer any
  Python-side matching; the generator remains but matching is delegated to the C++ engine via the binding.
- **User actions:** none beyond eventual push. **Follow-ups:** all P2 order-book picks DONE. Optional: a WASM
  core for an in-browser showcase demo; stop/stop-limit/iceberg remain intentionally out of scope.

## 2026-06-02 — docs-writer — monorepo-wide README + showcase reconciliation (docs-only)
- Branch `feature/agent-improvements` (NOT pushed). Documentation-only pass after the P2 feature
  backlog landed: reconciled every package README + the showcase so feature lists, badges (test
  counts), and the "vs <equivalent>" sections match what now EXISTS. NO source/tests/render.yaml
  touched. Scoped `git add` to README/showcase/this-file paths only (a deploy-engineer is editing
  render.yaml/DEPLOY on this branch in parallel). Re-confirmed test counts by `pytest --co` (NOT
  trusting the prompt): options **201**, portfolio **234**, backtesting **175**, market-data **173**,
  order-book **53 C++ (ctest) + 27 Python** — all match.
- **packages/options-pricing/README.md:** Tests badge 138→**201** (+ 3 prose "138 tests" mentions);
  removed a stray `</content></invoke>` corrupting the file tail; added a note that a set-but-rejected
  Finnhub key (401/403) logs one actionable warning then falls back (matches the implemented behavior).
  Higher-order Greeks / Black-76 / `*_vec` batch API / solved IV surface / live chains were already
  documented by the per-feature pass — verified each signature against `src/black_scholes.py` +
  `src/greeks_visualizer.py`.
- **packages/portfolio-optimization/README.md:** added Tests (**234**) + Coverage badges (had none);
  fixed the value-prop line (now: solved frontier, 7 objectives incl. HRP, Black-Litterman, Ledoit-Wolf).
  **Key accuracy fix:** the "vs" prose paragraphs CONTRADICTED the table/features — they still said the
  engine does NOT do Black-Litterman/HRP/clustering and that the frontier is "not a swept convex
  frontier." Rewrote to state both the Dirichlet cloud AND the true `solved_efficient_frontier` exist,
  and moved HRP/BL/Ledoit-Wolf into "does well." Stale "147 tests / ~95%" → **234 / ~96%** (tree +
  Testing section). Confirmed CLI `--objective` choices + FastAPI `/objectives` are library-only for
  HRP/BL (correctly NOT claimed in the CLI/API doc sections — those stay sharpe/min_vol/risk_parity/
  sortino/min_cvar).
- **packages/backtesting/README.md:** added Tests (**175**) + Coverage badges; expanded the MPT
  Integration bullet + the walk-forward snippet's `objective` comment from 4 → the **7** objectives the
  strategy actually accepts (sharpe/min_vol/min_cvar/risk_parity/sortino/max_return_target_vol/
  min_vol_target_return). Short selling, CSV/DataFrame handlers, resilient yfinance/offline were already
  documented by the per-feature pass.
- **packages/market-data/README.md:** added Tests (**173**) + Coverage badges; Dev section test count
  76→**173**; updated the value-prop blurb (Binance+Coinbase, pluggable storage, replay). **Accuracy
  fix:** the "vs cryptofeed" table + "what it doesn't do" prose still said "one Binance-style stream" /
  "Redis + TimescaleDB" only — updated to Binance+Coinbase adapters, pluggable Timescale/DuckDB, and a
  new Replay row. (Left the default-path mermaid as the illustrative single-sink view; DuckDB/replay are
  fully covered in prose below it.)
- **cpp/order-book/README.md:** removed a stray `</content></invoke>` tail; added a combined
  "53 C++ | 27 Python" Tests badge; trimmed the Roadmap — the throughput/latency benchmark item was DONE
  (`benchmarks/bench.py`, documented in Quick Start step 4) so it's removed; the "wire simulator through
  the binding" item is GENUINELY still open (verified `python/simulator.py` does NOT import `orderbook`)
  so it stayed, reframed as remaining visualizer glue, + a WASM line. TIF/pybind11/benchmark numbers
  already documented — reused the existing real M2 Pro numbers, fabricated nothing.
- **apps/showcase-site/src/projects.js:** rewrote all 5 project descriptions + "vs" notes to the
  expanded feature sets. **Biggest accuracy fix:** the options entry claimed **"Monte-Carlo option
  pricing"** — the package has NO Monte Carlo (BS + binomial + Greeks + IV only); removed it and
  replaced with the real capabilities (American binomial, higher-order Greeks, Black-76, vectorized IV
  surface, live chains). Order-book "**35** GoogleTest" → 53 C++ + 27 Python + pybind11 + TIF + bench
  numbers. Portfolio: added HRP/BL/Ledoit-Wolf/solved-frontier. Backtesting: short selling + CSV/DataFrame
  handlers + events/sec. Market-data: Coinbase + DuckDB + replay. Kept all `demoUrl` placeholders
  unchanged (deployment is separate). `npm run build` → **green** (vite, 5 modules, ~78ms).
- **Honored AGENTS domain caveats:** no exotics/Heston/SVI/MC claimed for options; vectorbt event-driven
  flagged PRO; mbt-gym described as model-based (not a matching engine); py_vollib/mibian European-only.
- **Nothing left contradicting code.** Non-doc follow-ups (future feature pass, not drift): HRP/Black-
  Litterman are library-API only (not yet in the portfolio CLI/FastAPI or the backtester objective
  switch) — correctly reflected as such; the order-book simulator→binding→visualizer glue is unwired
  (kept on Roadmap). README CI/badge GitHub slug is `quant-lab` (pre-monorepo name) across all repos —
  cosmetic, left as-is to avoid scope creep.

## 2026-06-02 — deploy-engineer — root render.yaml + deploy docs (no-external-DB cloud demos)
- Branch `feature/agent-improvements` (NOT pushed). Config + DOCS ONLY — no package source/tests touched.
  Wired the recently-added "runs-without-external-infra" knobs into the root `render.yaml` so every cloud
  demo is runnable and degrades gracefully. Verified each env var name/default against the real
  `config.py`/source before writing it. Scoped `git add` to `render.yaml`, `README.md` (Deploy section),
  `packages/backtesting/DEPLOY.md`, `packages/market-data/README.md` (deploy sections), and this file only
  (a docs-writer agent is editing package READMEs/showcase in parallel on the same branch — did NOT
  `git add -A`).
- **market-data worker — NO external DB by default (the headline change):** set `STORAGE_BACKEND=duckdb`
  + `DUCKDB_PATH=data/marketdata.duckdb` on the `market-data-pipeline` worker, so the Blueprint deploys a
  worker that runs on the managed Redis (already wired via `fromService`) + the container's writable disk
  alone — no external TimescaleDB. KEPT `DATABASE_URL` as a documented `sync:false` option for anyone who
  switches to `STORAGE_BACKEND=timescale`. Also surfaced `EXCHANGE=binance` and `MAX_BUFFER_SIZE=1000` as
  explicit (editable) env vars. Documented that the free-plan disk is EPHEMERAL (fine for a live-streaming
  demo; data lost on redeploy — use a persistent disk or Timescale for durability). Verified names against
  `packages/market-data/src/config.py` (`STORAGE_BACKEND`/`DUCKDB_PATH`/`EXCHANGE`/`MAX_BUFFER_SIZE`).
- **OFFLINE-flag decisions (per service, documented tradeoff mirroring the existing options choice):**
  - `options-pricing`: UNCHANGED — left `OPTIONS_PRICING_OFFLINE` OFF (commented stub), the app already
    falls back to the bundled chain per-request, so live works when egress allows.
  - `backtesting`: added a commented-out `BACKTESTING_OFFLINE` stub, default OFF — BUT documented the key
    difference: the backtesting data layer RAISES `MarketDataError` on a failed yfinance fetch (no
    per-request fixture fallback like options has); the dashboard surfaces that as an in-UI error (doesn't
    500) rather than results. So the docs recommend flipping `BACKTESTING_OFFLINE=1` ON if a guaranteed-
    deterministic showcase is wanted. The env flag overrides the dashboard's `offline=False` arg at the
    data layer (verified in `src/market_data.py::_offline_enabled`).
  - `portfolio-optimization`: NO data flag — the FastAPI demo optimizes a returns matrix POSTed by the
    caller (verified `api/app.py` does no network fetch), so it is deterministic by construction.
    `PORTFOLIO_OFFLINE` only affects the library/CLI's own yfinance fetches, not the deployed service —
    documented as such in render.yaml.
- **Docs updated:** root `README.md` Deploy section (no-external-DB posture for all four services + the
  exact dashboard secrets: `FINNHUB_API_KEY` optional, `DATABASE_URL` only for Timescale);
  `packages/backtesting/DEPLOY.md` (new "Live market data & the OFFLINE flag" section, mirrors options);
  `packages/market-data/README.md` deploy sections (DuckDB-default docker run + Render steps, ephemeral-
  disk caveat, optional-Timescale path, and the "without live infra" / one-Redis-demo callouts rewritten —
  they previously claimed Timescale was a hard dependency).
- **Validation:** `python3 -c "import yaml; yaml.safe_load(open('render.yaml'))"` parses clean; all four
  services present; `market-data` envVars include STORAGE_BACKEND=duckdb. `import api.app` (portfolio)
  imports clean. Docker build NOT run (not exercised this pass; Dockerfile unchanged from the verified
  prior pass). NO deploy/remote resources created.
- **User must set in the Render dashboard:** (1) `FINNHUB_API_KEY` — OPTIONAL, options live spot (falls
  back to yfinance if unset); (2) `DATABASE_URL` — ONLY if switching market-data to
  `STORAGE_BACKEND=timescale` (default DuckDB needs nothing). Plus the usual: push the repo to GitHub,
  Render → New → Blueprint → select repo (the `market-data-pipeline` + `portfolio-optimization-api`
  services have `autoDeploy:false`, so trigger their first deploy manually), and Netlify → connect repo
  (builds `apps/showcase-site` via root `netlify.toml`). Then wire the showcase "Live demo" buttons to the
  resulting `*.onrender.com` URLs.
- **Follow-ups:** none required for runnable demos. Optional: expose `--offline`/`EXCHANGE` through more
  CLI entry points; attach a Render persistent disk to make the DuckDB market-data store durable.

## 2026-06-02 — feature-architect — packages/backtesting (native short selling, opt-in)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 backtesting pick #3 — native short
  selling, the biggest real gap vs backtrader/zipline. Fully ADDITIVE + OPT-IN behind
  `Portfolio(allow_short=True)` (default `False`). The cross-package contract is intact: the shared
  `metrics` math is untouched (analytics still delegates Sharpe/Sortino/drawdown to
  `portfolio_optimization_engine.metrics`), the optimizer API is not touched (this change lives entirely
  in the execution/portfolio/analytics layer), and a representative LONG-ONLY backtest is proven
  byte-identical (all 148 prior tests still pass unchanged). Scoped `git add` to `packages/backtesting/...`
  + this file only (two other agents committing in parallel).
- **portfolio.py:** new `allow_short` ctor flag. When off, `process_fill` runs the EXACT original
  long-only branch (byte-identical). When on, it dispatches to a new `_apply_signed_fill` that handles all
  signed transitions: open/extend from flat (set entry), same-side magnitude increase (magnitude-weighted
  average entry), reduce toward flat (keep open-side entry), close-to-flat (drop tracking), and flip
  through zero (a SELL larger than the open long closes it and opens a short for the remainder, entry reset
  to the flip price). Cash accounting is direction-mechanical and already correct for signed qty: a SELL
  always credits proceeds (short sale credits cash), a BUY always debits (cover debits cash); equity
  `cash + Σ signed_qty*price` marks a short to market inversely with no special-case. `check_exits` now
  also handles shorts (qty<0) with INVERTED triggers — stop-loss when price rises above entry, take-profit
  when it falls below, trailing off the lowest price seen — emitting a buy-to-cover; the long branch (qty>0)
  is unchanged and qty<0 is still skipped when `allow_short` is off.
- **sizing.py:** new `_directional_order` (long/short) + an `_order_for` dispatcher; the three quantity
  sizers (`FixedFractional`/`PercentOfEquity`/`RiskBased`) now call `_order_for`, which routes to the
  existing `_long_only_order` when `allow_short` is off (byte-identical) and to the signed builder when on
  (SELL opens/extends a short, BUY covers; opening/extending is capped to buying power, covering is not).
  `TargetWeightSizer` now honors a negative `target_weight` only when `allow_short` is on, else clamps it
  to 0 (flat) — preserving the long-only behavior (no repo strategy emits negative weights, so this is a
  no-op for existing runs).
- **analytics.py — signed FIFO (the critical correctness point):** `PerformanceAnalytics` gained an
  `allow_short` flag (passed through from `Backtest.run` via `portfolio.allow_short`). When off,
  `_compute_round_trip_pnl` runs the ORIGINAL long-only matching verbatim (BUYs open lots, SELLs close FIFO
  with `(sell-entry)*qty`, a naked SELL is dropped). When on, it runs `_round_trip_pnl_signed`: a per-symbol
  FIFO of signed lots where a same-direction trade opens/extends a lot and an opposite trade closes lots
  FIFO — closing a LONG lot earns `(exit-entry)*qty`, closing a SHORT lot earns `(entry-cover)*qty` (sold
  high/covered low = profit) — with any remainder after the book empties opening a fresh lot on the other
  side (handles long->flat->short flips). A pure-long sequence yields the identical result with the flag on
  (test-asserted).
- **Tests (`tests/test_short_selling.py`, NEW): +27 (148 -> 175).** Short cash/position accounting
  (credit on open, debit on cover, partial cover keeps entry, weighted-avg on extend); inverse
  mark-to-market while short + gross exposure abs-value; long->flat->short flip cash+entry; short-side
  stop-loss/take-profit/no-trigger + the defensive allow_short-off skip; signed FIFO (clean short profit,
  short loss, multiple short lots, long-then-flip-to-short, long-only unchanged with flag on); sizer-level
  signed behavior (PercentOfEquity opens/blocks short, cover uncapped, LIMIT passthrough, zero-buying-power
  cap) + TargetWeight negative-weight open/clamp; end-to-end short backtest profits in a falling market +
  default-off clips the naked sell; and the PARITY block (default `allow_short` False, metrics echo the
  shared module exactly, identical-inputs -> byte-identical equity curve + trade frame + stats).
- **Gate (REAL numbers):** `python -m pytest` -> **175 passed** (was 148), coverage **89.17%** (gate
  `--cov-fail-under=80` met; `portfolio.py` 97%, `analytics.py` 77%, `sizing.py` 82%). `ruff check .` clean,
  `ruff format --check` clean (22 files), `mypy src` clean (13 files). README updated (Features bullet,
  Technical Highlights, a "Short selling (opt-in)" usage subsection, and a short-selling row in the vs.
  table).
- **Follow-ups:** all three P2 backtesting picks now DONE. Optional: expose `allow_short` through
  `main.py`/the Dash dashboard; add a short-friendly demo strategy (e.g. a long/short MA crossover that
  flips on the cross-down); margin interest already accrues on negative cash, so a leveraged long/short
  book is supported but untested end-to-end at the dashboard layer.

## 2026-06-02 — feature-architect — packages/market-data (pluggable ExchangeAdapter + Coinbase)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 market-data pick #1 — the headline
  gap vs cryptofeed/ccxt-pro: a pluggable `ExchangeAdapter` protocol + a 2nd exchange. ADDITIVE: the
  Binance path is byte-identical by default (URL + parsed Trade unchanged). Left the prior passes'
  `StorageBackend` / `replay()` / backpressure cap intact. Scoped `git add` to `packages/market-data/...`
  + this file only.
- **Protocol surface (`src/adapters.py`)** — new `runtime_checkable` `ExchangeAdapter` Protocol capturing
  the ONLY three things that vary per venue: `name: str`; `ws_url(symbols) -> str` (full connect URL);
  `subscribe_payload(symbols) -> dict | None` (JSON to send after connect, or `None` when streams are
  URL-embedded); `normalize_trade(raw) -> Trade | None` (parse one raw WS msg into the EXISTING normalized
  `Trade` — lowercased symbol, float price/qty, `buy`/`sell` aggressor side, UTC-aware timestamp,
  `exchange` tag; non-trade/malformed → None, logged not raised). `build_adapter(name)` factory (raises on
  unknown).
- **`BinanceAdapter`** — factored the existing Binance logic out of `normalizer.py` unchanged: combined
  `@trade` streams embedded in the path, no subscribe, `m` ("buyer is maker") → aggressor side
  (`m=true`→"sell"). Built from `WS_URL` so the URL is byte-identical to before. Now also ignores a
  non-`trade` `e` event type if present.
- **2nd exchange = `CoinbaseAdapter`** (Coinbase Exchange `matches` channel, keyless public).
  URL = fixed `wss://ws-feed.exchange.coinbase.com`; subscribe = `{"type":"subscribe","channels":
  [{"name":"matches","product_ids":[...]}]}`. Parsed message shape (verified against Coinbase docs):
  `{"type":"match"|"last_match","trade_id","sequence","maker_order_id","taker_order_id","time"(ISO-8601 Z),
  "product_id":"BTC-USD","size","price","side"}`. Two normalization decisions, documented in code:
  (1) Coinbase `side` is the **maker** side → flipped to the **taker/aggressor** side to stay consistent
  with Binance's "who crossed the spread" (`side="sell"` maker → normalized `"buy"`); (2) symbols
  round-trip `btcusd`/`btc-usd` ⇄ `BTC-USD` (dash-insert on subscribe, strip+lower on parse). Subscription
  acks / heartbeats parse to None.
- **Wiring** — `normalizer.TickNormalizer(adapter=None)` now delegates trade parsing to the adapter
  (defaults to `BinanceAdapter()` so a no-arg normalizer is unchanged); the OHLCV roll-up is unchanged and
  adapter-agnostic. `websocket_client.MarketDataClient(ws_url, max_retries, adapter=None)` defaults to
  `BinanceAdapter(ws_url)` (existing tests/URL unchanged), uses `adapter.ws_url(symbols)` and sends
  `adapter.subscribe_payload(symbols)` as JSON after connect when not None. `config.Config.exchange`
  (env `EXCHANGE`, default `"binance"`, lowercased). `pipeline.build_exchange_adapter(config)` returns a
  `BinanceAdapter(config.ws_url)` for binance (byte-identical) else `build_adapter(config.exchange)`;
  `Pipeline.__init__` builds the adapter once and shares it across client + normalizer.
- **Tests (`tests/test_adapters.py`, NEW): +39 (134 → 173), no live network.** Protocol conformance
  (both adapters `isinstance ExchangeAdapter`; `build_adapter` select/case/unknown-raise); Binance exact
  Trade, m→sell, no-`e`-key still parses, non-trade event ignored, malformed→None, URL embed + trailing-
  slash strip, no subscribe; Coinbase exact Trade (maker-sell→taker-buy), maker-buy→taker-sell, last_match,
  ack/heartbeat ignored, malformed→None, naive-time→UTC, fixed feed URL, subscribe product mapping +
  already-dashed + short-symbol passthrough + product_id round-trip; normalizer delegation (default Binance,
  injected Coinbase, OHLCV roll-up adapter-agnostic); config default=binance + lowercasing + `build_exchange_
  adapter` binance-uses-WS_URL/coinbase/unknown-raise + Pipeline shares one adapter; client drives adapter
  (default Binance URL, Coinbase feed URL + subscribe JSON sent, Binance sends nothing); and an END-TO-END
  Coinbase run driving `MarketDataClient` with a FakeWebSocket replaying canned match messages (+ ack) →
  asserts cache/publish/batch-flush and taker-normalized sides. Added `send()` to the conftest FakeWebSocket.
- **Gate (REAL numbers):** `python -m pytest` → **173 passed** (was 134), coverage **98.46%** (gate
  `--cov-fail-under=85`; `adapters.py` 97% — the 3 uncovered lines are the Protocol `...` method stubs,
  `normalizer.py` 95%, `pipeline.py` 99%, `websocket_client.py` 100%). `ruff check .` clean, `ruff format
  --check` clean (25 files), `mypy src` clean (10 files). README (Features bullet, data-flow steps 1–2,
  `EXCHANGE` config row, new "Exchange adapters" subsection, Project Structure), `.env.example`, and this
  ledger updated.
- **User actions:** none beyond eventual push. **Follow-ups:** ALL P2 market-data picks (#1 adapters, #2
  StorageBackend/DuckDB, #3 replay/backpressure) are now DONE. Optional next venues (Kraken/Bitstamp) are
  each ~one adapter class; could surface `--exchange` through `main.py` for parity with `--symbols`.

## 2026-06-02 — feature-architect — packages/portfolio-optimization (Black-Litterman expected-returns model)
- Branch `feature/agent-improvements` (NOT pushed). Implemented the P2 portfolio runner-up — Black-Litterman
  (Black & Litterman, 1992) — closing out ALL FOUR portfolio P2 picks (solved frontier, HRP, Ledoit-Wolf,
  BL). Purely ADDITIVE / API-SAFE: no existing public signature changed, no `metrics` math touched, the
  backtester's injected-returns + zero-arg `optimize_*` + `PortfolioResult.weights` contract intact. numpy
  only (no PyPortfolioOpt/cvxpy/sklearn). Mirrors `covariance.py`: a standalone helper module + a thin
  optimizer entry point. Builds on the just-added Ledoit-Wolf `covariance.py` / HRP `optimize_hrp` (read both
  first).
- **New `black_litterman.py`** (numpy-only helper module): `market_implied_prior(cov, w_mkt, risk_aversion)`
  returns the equilibrium (prior) excess returns via reverse optimization `Pi = delta * Sigma @ w_mkt`.
  `black_litterman(cov, w_mkt=None, P=None, Q=None, *, omega=None, tau=0.05, risk_aversion=2.5, pi=None)`
  returns the posterior expected-returns vector via the BL master formula:
  `E[R] = [ (tau*Sigma)^-1 + P^T Omega^-1 P ]^-1 [ (tau*Sigma)^-1 Pi + P^T Omega^-1 Q ]`
  (solved with `np.linalg.solve`). Neutral prior defaults to **equal-weight** when `w_mkt` is omitted;
  the view-uncertainty `Omega` defaults to the standard `diag(tau * P Sigma P^T)` (each diagonal entry
  floored at 1e-12 so a zero-variance view can't make Omega singular); `pi=` lets a caller inject the prior
  directly and skip reverse optimization. With **no views** (`P`/`Q` omitted, empty, or zero-confidence /
  huge `Omega`) the posterior equals the prior `Pi` exactly — the documented default behavior. Full input
  validation (square cov, `w_mkt`/`pi` length, `P` columns == n, `Q` length == k views, `Omega` shape k×k).
- **Two optimizer entry points** in `optimizer.py` (after `optimize_hrp`):
  - `black_litterman_returns(P=None, Q=None, *, w_mkt=None, omega=None, tau=0.05, risk_aversion=2.5,
    pi=None)` → a pandas **Series indexed by `self.tickers`** of the posterior annualized excess returns
    (uses only `self.cov_matrix`; raises the usual "Call calculate_returns() first" when cov is unset).
  - `optimize_black_litterman(P=None, Q=None, *, w_mkt=..., omega=..., tau=..., risk_aversion=..., pi=...,
    **cons)` → computes the posterior, **temporarily** sets it as `self.mean_returns`, runs the EXISTING
    max-Sharpe SLSQP solve, builds the result through the shared `_make_result` (so return/vol/Sharpe/
    Sortino/CVaR all flow through the identical `portfolio_*` path every objective uses), then **restores
    `self.mean_returns`** in a `finally` so the call has no lingering side effect. Accepts the same
    constraint kwargs (`min_weights`/`max_weights`/`allow_short`/`groups`); long-only weights sum to 1 by
    default; `objective == "black_litterman"`. Zero-arg-callable, so the backtester could drive it like any
    other objective.
- **Contract preserved:** the reported `expected_return` reflects the BL POSTERIOR (correct by design — the
  optimization and its metrics are on the posterior); `volatility` comes from the unchanged covariance via
  the same `portfolio_volatility` method, so it matches the shared `metrics.annualized_volatility` of the
  realized series to ~1e-6. Nothing on the default path calls BL, so parity with the backtester is intact.
- **Hand-checked 2-asset case:** `Sigma=[[.04,.006],[.006,.09]]`, `w=[.5,.5]`, `delta=2.5` →
  `Pi=[0.0575, 0.12]`; a bullish absolute view `Q=Pi[0]+0.05` on asset 0 raises its posterior to ~0.0825;
  zero-confidence (`Omega=1e12`) collapses back to `Pi`.
- **Tests (`tests/test_black_litterman.py`, NEW): +28 (206 -> 234).** Helper: prior formula vs hand value,
  no-views==prior, default w_mkt==equal-weight, zero-confidence==prior, empty/missing views==prior, bullish
  view shifts posterior up / bearish down, default `Omega`==`diag(tau P Sigma P^T)` (custom-equals-default
  posterior match), tighter `Omega` pulls posterior nearer the view, `pi=` override, finite/sane 3-asset,
  DataFrame cov accepted, and all six validation raises. Entry point: before-calculate raises (both methods),
  valid long-only weights summing to 1 + `objective` tag, no-views posterior Series == reverse-opt prior,
  bullish view tilts optimized weight UP vs no-view and raises posterior return, `mean_returns` restored
  after the call, metrics parity (posterior return, cov-vol vs shared metrics, internal Sharpe consistency),
  `max_weights` constraint respected, the hand-checkable 2-asset prior + bearish tilt, and single-asset
  degenerate (-> 1.0).
- **Gate (REAL numbers):** `python -m pytest` -> **234 passed** (was 206), coverage **96.45%** (gate
  `--cov-fail-under=90` met; `black_litterman.py` **100%**, `optimizer.py` 98% — the 4 missed lines/branches
  are the pre-existing CVaR-LP-fail / `_solve`-fail / target-cap paths, not BL). `ruff check .` clean,
  `ruff format --check` clean (30 files), `mypy` clean (11 source files).
- **Docs:** README Features bullet, "vs. the popular tools" row (now "Black-Litterman + HRP"), a Usage
  snippet (posterior Series + `optimize_black_litterman` + no-views prior), and the project-structure tree
  updated.
- **User actions:** none beyond eventual push. **Follow-ups:** all four portfolio P2 picks are DONE; could
  surface `optimize_black_litterman` / a views form in the FastAPI demo + CLI, and add an `"black_litterman"`
  branch to the backtester's `OptimizationRebalanceStrategy` (works today via the zero-arg contract, though
  it needs views to differ from equilibrium). cvxpy backend remains deferred (heavy dep / scope creep).

## 2026-06-02 — feature-architect — packages/market-data (replay feeder + OHLCV final-bar fix + backpressure cap)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 market-data pick #3 — all three sub-items.
  Built on the existing `StorageBackend` Protocol / DuckDB sink / fail-fast pipeline (read first). Scoped
  `git add` to `packages/market-data/...` + this file only (two other agents committing in parallel).
- **(1) `Pipeline.replay(symbol, start, end, *, source="trades"|"ohlcv", interval="1m", page_size=10000)`** —
  async generator that streams stored records back out in **timestamp order (oldest-first)**, turning the
  ingest daemon into a research feeder. Depends ONLY on the Protocol read API (`query_trades`/`query_ohlcv`),
  so it works against BOTH backends unchanged. `source="ohlcv"` just forwards the already-ascending
  `query_ohlcv`. `source="trades"` had to handle that `query_trades` returns **most-recent-first capped at
  `limit`** (the newest slice, not the oldest) — naive forward-paging returned the tail. Fix: page BACKWARD
  (newest chunk first, narrow the upper bound to just past the oldest ts seen), accumulate, then yield sorted
  ascending + de-duplicated on `(time, price, quantity, side)` so a boundary-tie row read in two pages isn't
  doubled. Documented limitation: with no offset API, `page_size` must exceed the worst-case same-millisecond
  burst (default 10000 is well clear); a no-downward-progress guard prevents an infinite loop.
- **(2) OHLCV final-bar / single-trade-bar fix (`normalizer.py`)** — the roll-up only ever emitted a bar when
  a LATER-minute trade arrived, so the **final in-progress minute was silently dropped at end-of-stream**
  (the backlog "drops final bar" bug). Added `TickNormalizer.flush(symbol)` + `flush_all()` to emit the last
  bucket's bar and clear it; `Pipeline.stop()` now calls `flush_all()` and persists those bars (best-effort,
  logged-not-raised on sink failure) BEFORE tearing down storage. Also removed the misleading `len(bucket) < 2`
  early-return and documented that a single-trade minute still produces a valid bar (it always did on
  rollover; now explicit and also covered by flush). Regression tests prove the old drop and the new emit.
- **(3) Bounded buffer / backpressure cap** — new `MAX_BUFFER_SIZE` config (env `MAX_BUFFER_SIZE`, default
  1000). `_on_message` calls `_apply_backpressure()` after buffering: **primary policy = BLOCK** (await an
  inline `_flush_trades`, back-pressuring the WS consumer — lossless when the sink is healthy, logs a
  `WARNING` when the cap is hit). **Last resort only:** if the sink is STILL unreachable after that flush
  (a failed flush re-adds its batch so the buffer can't drain), drop the OLDEST trades down to the cap to
  guarantee a hard memory bound (no OOM), incrementing `self._dropped_trades` and logging a running count —
  never a silent drop. Chose block-first as the safer option per the task.
- **Tests:** +27 across three new files, all no-live-infra (107 -> 134): `test_replay.py` (replay yields
  oldest-first across a FakeReadBackend AND a real `tmp_path` DuckDB store — trades + OHLCV, half-open window,
  symbol isolation, empty window, multi-page paging on both backends, boundary-tie dedup, the documented
  page_size limit, bad-source raise); `test_normalizer_flush.py` (final bar buffered-then-flush, single-trade
  bar emits on rollover AND flush, carried-minute flush, `flush_all` across symbols, empty cases); and
  `test_backpressure.py` (cap never exceeded with a healthy sink + inline-flush warning, drop-oldest +
  running-count log when the sink stays down, dropped-count accumulation, below-cap no-warn/no-drop, and
  `stop()` persisting the final OHLCV bar incl. the logged-not-raised sink-failure path).
- **Gate (REAL numbers):** `python -m pytest` -> **134 passed** (was 107), coverage **99.07%** (gate
  `--cov-fail-under=85`; `pipeline.py` 99%, `normalizer.py` 97%). `ruff check .` clean, `ruff format --check`
  clean (23 files), `mypy` clean (9 source files). README (Features, config table, Usage replay example,
  flush note), `.env.example`, and this ledger updated.
- **Follow-ups:** P2 market-data pick #1 (pluggable `ExchangeAdapter` + 2nd exchange) is now the only open
  market-data feature item — the headline gap vs cryptofeed/ccxt-pro. Optional: a CLI subcommand wrapping
  `replay` (e.g. `python main.py replay --symbol btcusdt --start ... --end ...`) to dump to stdout/CSV.

## 2026-06-02 — feature-architect — packages/options-pricing (vectorized batch API + real IV surface)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 options pick #3 — a vectorized/batch
  pricing API enabling true IV chains + a real IV surface. Purely ADDITIVE: every scalar function and
  signature in `black_scholes.py` is unchanged; `market_data.price_chain` / the existing `plot_market_iv_*`
  helpers are untouched. No exotics/Heston/American/SVI added or claimed (stayed within AGENTS caveats).
- **Vectorized BS in `src/black_scholes.py`** (NumPy broadcasting, NO per-contract python loop):
  `black_scholes_price_vec(S,K,T,r,sigma,option_type,q)`, `greeks_vec(...) -> {delta,gamma,theta,vega,rho}`,
  and `implied_volatility_vec(market_price,S,K,T,r,option_type,q,tol,max_iter)`. All accept array-likes
  (numpy / pandas Series / scalars) for ANY arg and broadcast to a common shape via a `_broadcast` helper;
  degenerate `T<=0` (intrinsic) / `sigma<=0` (discounted forward) entries are handled ELEMENTWISE with
  `np.where` masks (denominators neutralized so no nan/inf leaks before masking, divide-by-zero warnings
  silenced via safe denominators). The vec IV solver runs ONE broadcasted Newton iteration over the whole
  array — same 0.3 seed, same step, same `sigma>=1e-6` floor and vega<1e-12 break as the scalar — and maps
  the scalar's `None` (expired / sub-intrinsic / vega-collapse / non-convergence) to per-element `nan` so one
  bad contract never sinks a chain.
- **Vec == scalar consistency approach:** the vec functions reuse the exact scalar formulae (same `_d1`,
  same cdf/pdf terms, same /365 theta and /100 vega·rho scaling), so a one-element call equals the scalar
  call to machine precision; tests assert this elementwise (price + every Greek to 1e-12, IV round-trip and
  IV-vs-scalar to 1e-6).
- **Real IV surface in `src/greeks_visualizer.py`:** `solve_iv_surface(chains_by_expiry, spot, expiry_years,
  r, option_type, q)` solves OUR IV per (strike, expiry) from market `mid` via `implied_volatility_vec` and
  returns a tidy DataFrame (`expiry, T, strike, iv`), dropping the nan (non-solved) contracts.
  `plot_solved_iv_surface(...)` plots IV as the z-axis over strike × time-to-expiry (years) — a GENUINE IV
  surface, distinct from the constant-σ `plot_price_surface` — and returns the tidy frame. Headless-safe
  (tests force Agg). IV-surface SOURCE for the smoke test = the bundled offline sample chain
  (`market_data.get_option_chain(..., offline=True)`), synthesized into two expiries; NO live network.
- **Tests (`tests/test_vectorized.py`, NEW): +31 (170 -> 201).** Vec-vs-scalar price across strikes/sigma/T
  (incl. 3×3 broadcast, pandas Series, dividend q, mixed-degenerate column, intrinsic/zero-vol limits);
  vec-vs-scalar all-Greeks (call+put, parametrized) + degenerate-expiry delta steps + shape/keys; vec IV
  round-trip to 1e-6, vec==scalar IV to 1e-6, sub-intrinsic/expired -> nan, mixed valid+invalid no crash,
  absurd-price -> nan; bad-option_type raises on all three vec fns; and the offline IV-surface block —
  tidy-frame schema, surface IV == scalar solver per contract, empty-input frame, headless save + no-save.
- **Gate (REAL numbers):** `python -m pytest` -> **201 passed** (was 170), coverage **99.24%** (gate
  `--cov-fail-under=95` met; `black_scholes.py` 99%, `greeks_visualizer.py` 98%, `market_data.py` 100%).
  `ruff check .` clean, `ruff format --check` clean (13 files), `mypy src` clean (5 files). The two remaining
  partial branches are the scalar IV loop exit (pre-existing) + the vec IV early-`any()` guard + plot
  save-path branches — all benign.
- **User actions:** none beyond eventual push. **Follow-ups:** could surface the vec API in the Streamlit
  app / CLI (e.g. a "solved IV surface" tab fed by multi-expiry `price_chain` calls). Heston/exotics/SVI
  remain intentionally out of scope.

## 2026-06-02 — feature-architect — cpp/order-book (throughput/latency benchmark harness)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 order-book pick #3 — a runnable
  throughput + latency benchmark harness driving the live C++ matching engine through the `orderbook`
  pybind11 binding. Purely ADDITIVE: NO C++ engine code, bindings, CMake, or existing tests touched —
  one new file (`benchmarks/bench.py`) + README/IMPROVEMENTS docs. ctest stayed 53/53, pytest 27/27.
- **What it measures (`benchmarks/bench.py`):**
  - **Throughput** — pre-generates a deterministic synthetic order flow (~80% LIMIT / 20% MARKET, both
    sides, prices placed -2..+2 ticks around a slow random-walk mid so the flow both crosses the book
    AND leaves resting depth), then feeds it through `OrderBook.add_order` in a tight timed loop
    (`add_order` bound out of the hot loop) and reports orders/sec over `--repeat` runs (best reported).
    Generation cost is excluded from timing.
  - **Latency** — a separate pass times each individual `add_order` with `time.perf_counter_ns` and
    reports the distribution (mean / p50 / p90 / p99 / max) in microseconds via nearest-rank percentiles.
  - Workload size is CLI-configurable (`--orders`, default 500k ≈ a few seconds; `--repeat`, `--seed`);
    prints a clean machine/arch header + summary table. Heavy work is guarded under `if __name__ ==
    "__main__"` so pytest collection never runs it (it also lives in `benchmarks/`, outside `testpaths
    = tests` and outside the `--cov` source `python`, so it neither runs nor dilutes coverage).
- **REAL measured numbers (Apple M2 Pro, arm64, macOS 26.5, Python 3.12.10; 500,000 orders/run, seed 7;
  driven via the pybind11 binding so figures INCLUDE the Python→C++ call overhead — the native matching
  path is faster):**
  - **Throughput: ~186,000 orders/sec** (best of 3: runs were 185,368 / 185,710 / 183,717 orders/sec).
  - **Latency: p50 4.833 µs, p90 10.625 µs, p99 18.084 µs, max 100.916 µs, mean 5.360 µs.**
  - 466,518 trades produced from the 500,000-order flow (matching path well-exercised).
- **Build/gates (real numbers):** `cmake -S . -B build && cmake --build build` green; `ctest --test-dir
  build` → **53/53 passed** (0.48s). `python -m pytest` → **27 passed**, coverage **97.12%** (gate
  `--cov-fail-under=80` met; benchmark excluded from coverage source as intended). `ruff check` +
  `ruff format --check` clean on `benchmarks/bench.py`.
- **Docs:** README gained a "4. Benchmark the matching engine" Quick-Start subsection (run commands +
  the measured M2 Pro results table, clearly labeled with machine/arch + workload size, NOT fabricated)
  and `benchmarks/bench.py` added to the Project Structure tree.
- **Follow-ups:** all three P2 order-book picks (pybind11, IOC/FOK/post-only, benchmark) now DONE. Next
  natural step toward "ABIDES-lite" is a native C++ micro-benchmark (isolates the matching path from the
  binding overhead) and the strategic discrete-event latency clock → agent-based participants. WASM core
  for the showcase remains a separate demo win. Stop/stop-limit/iceberg still intentionally out of scope.

## 2026-06-02 — feature-architect — cpp/order-book (pybind11 bindings — engine now programmable from Python)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 order-book pick #1 (highest-leverage
  foundational item): pybind11 bindings making the C++ matching engine drivable directly from Python. The
  C++ engine code (`include/`, `src/order_book.cpp`, `src/matching_engine.cpp`) was NOT modified — purely
  additive bindings + build wiring + test replacement. ctest stayed 53/53.
- **Bindings (`src/bindings.cpp`, module `_orderbook`):** binds the `Side` / `OrderType` / `TimeInForce`
  enums; `Order` (custom `__init__` that defaults `remaining_quantity = quantity` and `tif = GTC`, with
  read/write fields + `is_filled()` + a `__repr__`); read-only `Trade` and `DepthLevel` (+ `__repr__`);
  `OrderBook` (ctor, `add_order` -> list[Trade], `cancel_order`, `modify_order`, `get_best_bid/ask`
  returning Optional[float] via `std::optional`, `get_spread`, `get_bid_depth`/`get_ask_depth`,
  `get_volume_at_price`, `bid_count`/`ask_count`, `symbol` property); and `MatchingEngine` (ctor,
  `submit_order`, `cancel_order`, `get_order_book` with `reference_internal` policy, `get_symbols`).
  Uses `pybind11/stl.h` for vector/optional and `pybind11/chrono.h` (timestamps not exposed but header pulled
  for completeness). C++17, matched existing 4-space LLVM/col-100 style by hand (clang-format not installed).
- **Build wiring (`CMakeLists.txt`):** pybind11 v2.13.6 via FetchContent (mirrors the existing GoogleTest
  fetch — NO pip/system pybind11 dep). New `option(BUILD_PYTHON_BINDINGS ON)` guards a
  `pybind11_add_module(_orderbook ...)` so the C++ demo + GoogleTest targets still build with
  `-DBUILD_PYTHON_BINDINGS=OFF`. The compiled `.so` is emitted (via `LIBRARY_OUTPUT_DIRECTORY`) straight into
  `python/orderbook/`, where a thin `__init__.py` re-exports it — so `import orderbook` works from `python/`
  with no install step. The `.so` is gitignored (`*.so`); it is rebuilt by cmake.
- **Tests (`tests/test_orderbook.py` REWRITTEN + `tests/conftest.py` NEW):** dropped the old 7 brittle
  subprocess-stdout-parsing tests against `order_book_demo` (and their build-then-rmtree fixture that would
  have wiped the compiled module). New suite drives the engine IN-PROCESS through the binding: basic
  crossing/resting/partial-fill, market sweep across levels, FIFO price-time priority, cancel/modify, depth +
  best bid/ask + spread queries, multi-symbol `MatchingEngine` routing/cancel, and the TIF paths — IOC
  partial-fill-then-cancel + IOC no-liquidity, FOK kill-vs-fill, POST_ONLY rejected-when-crossing +
  rests-when-not — proving the TimeInForce path is reachable from Python. `conftest.py` puts `python/` on
  `sys.path`; the module `pytest.importorskip`s `orderbook` so an unbuilt extension skips cleanly rather than
  erroring. The viz/sim tests (`test_python_viz.py`) are unchanged.
- **Build/gates (REAL numbers, from a clean `rm -rf build`):** `cmake -S . -B build && cmake --build build`
  builds the demo + GoogleTest + `_orderbook` module all green. `ctest --test-dir build` -> **53/53 passed**.
  `python -m pytest` -> **27 passed**, coverage **97.12%** (gate `--cov-fail-under=80` met;
  `python/orderbook/__init__.py` 100%). `ruff check` + `ruff format --check` clean on the new Python files.
  Verified `import orderbook` + a submit/fill/depth round-trip works from `python/`.
- **Follow-ups:** P2 pick #3 throughput/latency benchmark harness still OPEN (now trivially Python-driveable
  via the binding). Wire `simulator.py`'s generated order flow through the `orderbook` binding into the live
  C++ book and feed resulting depth/trades into `visualizer.py` for an end-to-end Python-driven sim (noted in
  README Roadmap). Stop/stop-limit/iceberg remain intentionally out of scope.

## 2026-06-02 — feature-architect — packages/market-data (pluggable StorageBackend + DuckDB sink)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 market-data pick #2 — a pluggable
  `StorageBackend` protocol + a local DuckDB/Parquet sink. ADDITIVE: the TimescaleDB path is unchanged and
  stays the default, so existing deploys behave identically. Decouples the demo from TimescaleDB (which
  Render's managed Postgres can't host) → the pipeline is now RUNNABLE with zero external DB.
- **`src/storage_backend.py`** — new `runtime_checkable` `StorageBackend` Protocol capturing the EXACT async
  surface `pipeline.py` already uses: `connect`/`disconnect`/`init_schema`/`insert_trades`/`insert_ohlcv` +
  the read API `query_trades`/`query_ohlcv`. The existing `TimeSeriesStorage` already conforms verbatim (no
  behavior change — asserted via `isinstance(..., StorageBackend)` in tests); `pipeline.storage` is now typed
  `StorageBackend`.
- **`src/duckdb_storage.py`** — new `DuckDBStorage(database_path=...)` writing the SAME normalized
  `trades`/`ohlcv` schema (identical column order/types to the Timescale DDL) to a local DuckDB file (or
  `:memory:`). DuckDB's API is sync, so every call runs on a worker thread via `asyncio.to_thread` (keeps the
  async surface, doesn't block the loop). Read methods return dicts keyed exactly like Timescale's
  (`time, symbol, price, …` / `time, symbol, open, …, trade_count`). Bonus `export_parquet(dir)` dumps both
  tables to Parquet for downstream research tooling. No network, no external DB.
- **Config selection (`src/config.py`):** added `storage_backend` (env `STORAGE_BACKEND`, default
  `"timescale"` → unchanged) and `duckdb_path` (env `DUCKDB_PATH`, default `data/marketdata.duckdb`). New
  `build_storage(config)` factory in `pipeline.py` returns Timescale by default, DuckDB when selected (lazy
  import so duckdb is only needed when chosen), and raises on unknown backends. Fail-fast preserved AND made
  backend-aware: Timescale failure still logs the `DATABASE_URL` line (now also hints `STORAGE_BACKEND=duckdb`);
  DuckDB failure logs a `DUCKDB_PATH`-fix line. Redis fail-fast untouched.
- **Tests:** +30 (76 → 106) across two new files, all no-live-infra: `test_duckdb_storage.py` round-trips
  trades+OHLCV against a real `tmp_path` DuckDB file (column order == `_TRADE_COLUMNS`/`_OHLCV_COLUMNS` ==
  Timescale schema, float/str/aware-datetime types, time-bound/symbol/interval/limit filters, ascending order,
  persistence across reconnect, Parquet export, connect-required guard, both backends satisfy the Protocol);
  `test_storage_backend_selection.py` covers config default=timescale + lowercasing + DUCKDB_PATH override,
  `build_storage` picks/raises, and drives the Pipeline END-TO-END with FakeCache+FakeClient but a REAL DuckDB
  store (batch flush + OHLCV roll persisted and queried back; `start()` wires it with no Postgres; bad
  DUCKDB_PATH → actionable fail-fast). Existing Timescale/fake-pool tests unchanged.
- **Gate (real numbers):** `python -m pytest` → **106 passed** (was 76), coverage **98.88%** (gate
  `--cov-fail-under=85`; `duckdb_storage.py` 99%, `storage_backend.py` 100%, `config.py` 100%). `ruff check`
  clean, `ruff format --check` clean (20 files), `mypy` clean (9 source files). `duckdb==1.5.3` pinned in
  requirements.txt; `.gitignore` ignores `*.duckdb`/`*.parquet`/`data/`; `.env.example` + README (Features,
  config table, new "Storage backends" subsection, Project Structure) updated.
- **Unblocks a no-external-DB Render deploy:** set `STORAGE_BACKEND=duckdb` and the worker needs only Redis +
  a writable disk — no external TimescaleDB. (Render free disks are ephemeral, so this is best for a runnable
  demo / local dev rather than durable storage; for persistence either use Timescale or a Render persistent
  disk + periodic `export_parquet`.)
- **Follow-ups:** P2 market-data pick #1 (pluggable `ExchangeAdapter` + 2nd exchange) and pick #3
  (`replay(symbol,start,end)` from store + OHLCV final-bar fix + backpressure cap) still OPEN. Could wire
  `STORAGE_BACKEND=duckdb` into `render.yaml` for a guaranteed-runnable cloud demo (deploy-engineer).

## 2026-06-02 — feature-architect — packages/portfolio-optimization (Hierarchical Risk Parity)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 portfolio pick #2 — `optimize_hrp()`
  (López de Prado, 2016). Purely ADDITIVE / API-SAFE: no existing public signature changed, no metrics math
  touched. Builds on top of the just-added `solved_efficient_frontier` + Ledoit-Wolf `covariance.py` (read
  both first). Solver-free — fits the scipy/numpy-only ethos (uses `scipy.cluster.hierarchy.linkage` /
  `to_tree` + `scipy.spatial.distance.squareform`; NO sklearn, NO cvxpy).
- **New `PortfolioOptimizer.optimize_hrp(linkage_method="single")`** in `optimizer.py`. Three classic stages:
  (1) **Tree clustering** — correlation distance `d = sqrt(0.5*(1-corr))` (corr derived from `self.cov_matrix`,
  clipped to [-1,1] for FP safety), condensed via `squareform`, then `linkage(method=...)`. (2)
  **Quasi-diagonalization** — `_hrp_quasi_diag` reads the linkage tree's leaves left-to-right (`to_tree(...).
  pre_order`) so correlated assets are adjacent. (3) **Recursive bisection** — `_hrp_recursive_bisection`
  starts all weights at 1, repeatedly splits each contiguous cluster and scales the halves by
  `1 - var_left/(var_left+var_right)`, where `_hrp_cluster_var` is the inverse-variance ("naive risk parity")
  cluster variance `w'Cov w`. Final weights renormalized to sum to 1 (long-only by construction).
- **Contract preserved:** uses ONLY `self.cov_matrix` (the injected-returns path); returns the SAME
  `PortfolioResult` via the shared `_make_result` (return/vol/Sharpe/Sortino/CVaR through the identical
  `portfolio_*` stat methods every other objective uses), so the backtester can call it zero-arg. Raises the
  same `"Call calculate_returns() first"` ValueError when cov is unset. Single asset -> weight 1.0. HRP is
  intrinsically long-only/fully-invested so it deliberately takes NO bounds/short/group kwargs (narrow sig).
- **Tests:** +15 new in `tests/test_hrp.py` (191 -> 206), all injected-returns / no network: weights sum to 1
  and non-negative; result exposes all backtester fields + `objective=="hrp"`; reported vol/return/Sharpe
  match recomputing from weights AND match the shared `metrics.annualized_return`; on a 2-block synthetic cov
  (low-vol A/B/C vs ~5x-vol D/E/F, ~0 cross-corr) risk is split across BOTH blocks with the low-vol block
  carrying the clear majority (sanity, not exact); within-block near-identical assets get comparable weight;
  higher-variance asset down-weighted vs low-vol peer; runs on the same injected path as the others +
  before-calculate raises; degenerate single-asset (->1.0), 2-asset (lower-vol gets more), perfectly-
  correlated (dist 0, no NaN), duplicate-column singular cov — none crash; unit tests for the quasi-diag
  permutation/block-contiguity and `_hrp_cluster_var` == inverse-variance portfolio.
- **Gate (real numbers):** `python -m pytest` -> **206 passed** (was 191), coverage **96.17%** (gate
  `--cov-fail-under=90` met; `optimizer.py` 98% — the 2 missed lines/branches are pre-existing CVaR-LP-fail
  / target-return-cap paths, not HRP). `ruff check .` clean, `ruff format --check` clean (28 files), `mypy`
  clean (10 source files).
- **Follow-ups:** P2 portfolio runner-up Black-Litterman (M) still open; cvxpy backend deferred (scope creep).
  Optional: expose `optimize_hrp` as an objective in the FastAPI demo / CLI and wire an `"hrp"` branch into the
  backtester's `OptimizationRebalanceStrategy` (it would work today via the zero-arg contract).

## 2026-06-02 — feature-architect — cpp/order-book (IOC / FOK / post-only time-in-force)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 order-book pick #2 — IOC/FOK/post-only as
  pure match-loop variants, NO new book data structures. All existing MARKET/LIMIT/GTC behavior preserved
  exactly (53/53 ctest pass; the demo still runs unchanged).
- **Modeled as a `TimeInForce` flag, NOT extra `OrderType` values** (`include/order.h`): new
  `enum class TimeInForce { GTC, IOC, FOK, POST_ONLY }` plus an `Order::tif = TimeInForce::GTC` field placed
  after `remaining_quantity` with a default member initializer. Rationale: IOC/FOK/post-only are
  time-in-force / execution semantics orthogonal to the price type (an IOC order is still a LIMIT or MARKET),
  mirroring the FIX TimeInForce + ExecInst split. Keeping `OrderType` as `{MARKET, LIMIT}` means all existing
  price-comparison logic in `match_order` is untouched, and the 7-field aggregate-init call sites in
  `main.cpp` + tests still compile (tif defaults to GTC).
- **Match-loop changes (`src/order_book.cpp`, all in `add_order` + two const helpers — `match_order` itself
  unchanged):**
  - POST_ONLY: pre-check `would_cross(order)` (best-ask/bid comparison, MARKET always crosses); if it would
    take liquidity, return `{}` (reject — nothing rests, no trades). Otherwise falls through and rests as a
    maker via the normal path.
  - FOK: pre-check `available_fill_quantity(order)` (walks reachable levels capped at the order's price,
    short-circuits once it reaches the needed qty); if `< remaining_quantity`, return `{}` (kill — book
    untouched). Otherwise the normal `match_order` fills it completely.
  - IOC: no pre-check needed — runs the normal `match_order` (which already stops at the limit price / sweeps
    for MARKET), and the new resting predicate excludes IOC so any unfilled remainder is simply dropped
    (never rests).
  - The resting rule is now `type == LIMIT && (tif == GTC || tif == POST_ONLY)` — MARKET/IOC/FOK never rest.
- **Tests:** +18 GoogleTests in `tests/test_order_book.cpp` (`with_tif` helper) -> **ctest 35/35 -> 53/53**:
  IOC partial-fill-then-cancel-remainder, IOC full-fill (resting maker keeps remainder), IOC no-liquidity full
  cancel, IOC market-on-empty-book, IOC multi-level sweep + cancel; FOK fills-completely across levels, FOK
  exact-liquidity, FOK kill (book unchanged), FOK price-capped kill, FOK sell-side kill+fill; post-only rests
  when not crossing (buy + sell), post-only rejected when it would cross (buy at/above ask, sell at/below bid),
  post-only rests on empty book; GTC-default-unchanged + engine-routes-IOC/FOK. Each asserts BOTH reported
  fills and resulting book state (counts + volume-at-price).
- **Build/gate (real numbers):** `cmake -S . -B build && cmake --build build && ctest` -> **53/53 passed**
  (was 35). Demo `order_book_demo` rebuilt + ran clean. `clang-format` NOT installed in this env (matched the
  existing 4-space LLVM/col-100 style by hand); pybind11 deliberately NOT added (separate future pass). Python
  side untouched, so no pytest run needed.
- **Docs:** README Features + scope-note updated (added a Time-in-force bullet + semantics table; clarified
  the FIX-style TIF-vs-OrderType split), Roadmap line "Time-in-force (IOC/FOK)..." removed (now done),
  file-tree comment notes the new `TimeInForce` enum.
- **Follow-ups:** P2 order-book pick #1 **pybind11 bindings** still OPEN (highest-leverage; would let Python
  tests drive these TIF orders directly instead of via `main.cpp`). Pick #3 throughput/latency benchmark
  harness also open. Stop/stop-limit/iceberg remain intentionally out of scope.

## 2026-06-02 — feature-architect — packages/backtesting (CSV/DataFrame handlers + 3 wired optimizer objectives)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 backtesting picks #1 and #2 — both ADDITIVE.
  No existing public behavior changed; the cross-package contract (shared `metrics` parity, optimizer used ONLY
  via its existing injected-returns + zero-arg `optimize_*` + `PortfolioResult.weights` API) is intact. The
  portfolio-optimization package was NOT modified.
- **(1) Offline data handlers** in `src/data_handler.py`. Extracted all bar/event read methods
  (`get_latest_bars`, `iter_bars`, `get_current_price`, `get_resampled_bars`, `get_current_bar`,
  `get_next_open`) into a new private `_InMemoryDataHandler` base so ALL handlers share byte-identical
  windowing / no-look-ahead resampling / next-open semantics. `YFinanceDataHandler` now subclasses it
  (behavior unchanged — `fetch()` still goes DuckDB-cache-then-`download_ohlcv`, ctor signature kept).
  New `DataFrameDataHandler({ticker: df}, start=None, end=None)`: takes pre-built in-memory frames, normalizes
  them eagerly in `__init__` (case-insensitive OHLC cols, optional Volume defaulted to 0, Date/Datetime/
  Timestamp/Time column promoted to a sorted DatetimeIndex), infers start/end from the data when omitted,
  `fetch()` is a no-op. New `CSVDataHandler(tickers, path, start=None, end=None, *, per_ticker=True,
  filename_template="{ticker}.csv", symbol_column="symbol")`: loads either one CSV per ticker from a directory
  (default) OR a single combined file with a `symbol`/`ticker` column; normalizes via the same helper and
  date-slices to `[start, end]`. Shared `_normalize_ohlcv()` guarantees the canonical
  `Open/High/Low/Close/Volume` shape so the rest of the engine works unchanged. Kills the hard yfinance
  dependency for offline/custom/intraday data.
- **(2) Three wired optimizer objectives** in `OptimizationRebalanceStrategy`. `objective` now also accepts
  `sortino`, `max_return_target_vol`, `min_vol_target_return` (was sharpe/min_vol/min_cvar/risk_parity).
  The two constrained objectives take a new `target` ctor param (annual vol cap for max_return_target_vol;
  min annual return for min_vol_target_return), validated at construction (raises if missing). For
  `min_vol_target_return` the target is clamped to the window's max-achievable long-only return so a too-high
  ask degrades to the max-return corner instead of raising and skipping the rebalance. Unknown objective still
  falls back to sharpe. Uses ONLY the optimizer's existing public methods on injected returns.
- **Tests:** +22 in new `tests/test_data_handlers.py` (126 -> 148). A `_assert_parity` harness drives a
  `YFinanceDataHandler` (frames pre-injected, no network) and each new handler through identical pointer
  positions and asserts `iter_bars` count/order, `get_latest_bars` windowing, `get_resampled_bars` no-look-ahead,
  `get_current_bar`/`get_current_price`/`get_next_open` all match (CSV scalars via `pytest.approx` — a CSV
  round-trip loses ~1 ulp; frames via `assert_frame_equal(check_freq=False)` since freq is metadata, and real
  yfinance data also has `freq=None`). Plus per-ticker + combined-CSV loads, date slicing, lowercase/missing-
  Volume/date-column-promotion/sort normalization, missing-file & missing-symbol-column & unknown-ticker errors,
  a full backtest through `CSVDataHandler`, and each of the 3 new objectives producing long-only weights summing
  to 1 (unit `_compute_targets` + end-to-end `Backtest.run()`), the clamp path, and the missing-`target` raise.
- **Gate (real numbers):** `python -m pytest` -> **148 passed** (was 126), coverage **88.74%** (gate
  `--cov-fail-under=80` met; `data_handler.py` 96%, `strategy.py` 87%). `ruff check .` clean, `ruff format
  --check` clean (21 files), `mypy` clean (13 source files). No live network in any test.
- **Follow-ups:** P2 backtesting pick #3 (native short selling) still OPEN. Optionally expose `--data-csv` /
  `--data-source` through `main.py`/dashboard so the new handlers are reachable from the CLI/UI end-to-end.

## 2026-06-02 — feature-architect — packages/portfolio-optimization (solved frontier + Ledoit-Wolf shrinkage)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 portfolio picks #1 and #3 — both ADDITIVE /
  API-SAFE. The backtester's injected-returns + zero-arg `optimize_*` + `PortfolioResult.weights` contract and
  the shared `metrics` parity are untouched (no existing signature changed).
- **(1) True solved efficient frontier** — new `PortfolioOptimizer.solved_efficient_frontier(n_points=50, **cons)`.
  Sweeps the EXISTING `optimize_min_vol_target_return` across a return grid from the global min-vol portfolio's
  return up to `mean_returns.max()`, recording the solved min-vol portfolio per target. Returns a DataFrame
  (`return`, `volatility`, `sharpe`, `w_<ticker>...`) sorted by return. Infeasible/solver-failed targets are
  caught and skipped (so e.g. a tight `max_weights` cap just yields fewer rows, never an exception); single-asset
  degenerate grid is widened so it doesn't crash. The OLD random-Dirichlet `efficient_frontier` is KEPT
  unchanged (analysis.py + main.py + plotter still use it as the scatter cloud) — the solved frontier is purely
  additive.
- **(3) Ledoit-Wolf covariance shrinkage** — new `covariance.py` (numpy-only, NO sklearn): `ledoit_wolf_shrinkage(
  returns, target=...)` returns `(shrunk_cov, intensity)` with intensity clipped to [0,1] and the result forced
  symmetric/PSD; targets `constant_correlation` (default) and `identity` via `constant_correlation_target` /
  `identity_target`. Wired OPT-IN through `calculate_returns(shrinkage=None|"constant_correlation"|"identity")`
  via a private `_estimate_cov_matrix`; the chosen intensity is exposed on `self.shrinkage_intensity` (None when
  off). **Parity preserved:** with `shrinkage=None` (the default, and what the backtester uses) the cov is
  `returns.cov() * 252` — byte-identical to before (test asserts `.equals`).
- **Tests:** +24 new in `tests/test_frontier_shrinkage.py` (167 -> 191): frontier return/vol monotonicity, vols
  are the solved per-target minimum (spot-checked vs re-solve) and never below global min-vol, weights sum to 1
  and respect bounds incl. a `max_weights` cap, infeasible-target skipping, before-calculate / n_points<2 guards,
  single-asset degenerate; shrinkage intensity in [0,1], symmetric PSD, shrinks-toward-target (Frobenius), exact
  convex-combination identity, single-asset zero intensity, <2-obs raise, unknown-target raise, ndarray/1-D/3-D
  input handling; and a PARITY block proving default cov == sample cov and default weights unchanged.
- **Gate (real numbers):** `python -m pytest` -> **191 passed** (was 167), coverage **95.93%** (gate
  `--cov-fail-under=90` met; `covariance.py` 100%, `optimizer.py` 98%). `ruff check .` clean, `ruff format --check`
  clean (27 files), `mypy` clean (10 source files).
- **Follow-ups:** P2 pick #2 `optimize_hrp()` (Hierarchical Risk Parity) still OPEN. Optional: switch the
  `plot_efficient_frontier` overlay / analysis pipeline to draw the solved boundary on top of the cloud; expose a
  `shrinkage` knob in `AnalysisConfig` + CLI + FastAPI demo (left off so the default stays parity-safe).

## 2026-06-02 — feature-architect — packages/options-pricing (higher-order Greeks + Black-76)
- Branch `feature/agent-improvements` (NOT pushed). Implemented P2 options picks #1 and #2 — both additive,
  closed-form, low-risk. NO existing public signatures or behavior changed.
- **Higher-order Greeks** in `src/black_scholes.py` (same style as the first-order Greeks — `S, K, T, r,
  sigma, [option_type], q`): `vanna` (`d(delta)/dσ = d(vega)/dspot`, type-independent), `volga`/vomma
  (`d(vega)/dσ`, type-independent), `charm` (delta decay, standard convention `charm = -d(delta)/dT`,
  type-dependent via the dividend-carry term). All return 0 at `T<=0` / `σ<=0` like the existing Greeks.
- **Black-76 futures-options pricer** in `src/black_scholes.py`: `black_76_price(F, K, T, r, sigma,
  option_type)` (discounts the forward, no spot carry) plus per-Greek helpers `black_76_delta` /
  `black_76_gamma` / `black_76_vega`. Mirrors the existing degenerate-case handling (`T<=0` -> intrinsic,
  `σ<=0` -> discounted intrinsic). Completes the py_vollib "core three" (BS / BSM-with-dividend / Black-76).
- **Verification approach (reference-value + finite-difference):** vanna checked against BOTH `d(vega)/dspot`
  and `d(delta)/dσ`; volga against `d(vega)/dσ`; charm against `-d(delta)/dT` (call, put, and with dividend).
  Black-76 checked against an independently-computed ATM-forward reference (0.787645), futures put-call parity
  `C - P = e^{-rT}(F-K)` across an F×T grid, equivalence to Black-Scholes when `F = S·e^{(r-q)T}`, and its
  Greeks against central differences of `black_76_price`. Type-independence and zero-vol/expiry limits asserted.
- **Tests:** +29 (141 -> 170), all in `tests/test_accuracy.py` (`TestHigherOrderGreeks`, `TestBlack76`).
  **Coverage 99.27% -> 99.37%** (gate `--cov-fail-under=95` met; the one partial branch 307->324 is the
  pre-existing IV-solver loop exit, unchanged). `ruff check` clean, `ruff format --check` clean (12 files),
  `mypy src` clean (5 files). `python main.py` textbook demo still works and now also prints vanna/volga/charm
  and a Black-76 call/put line.
- **Stayed within AGENTS domain caveats:** vanilla European closed-form only — NO exotics/American-extension/
  Heston/MC/FD added or claimed.
- **Follow-ups (P2 options pick #3, deferred):** vectorized/batch pricing API for true IV chains/surface;
  note `plot_volatility_surface` was already renamed to `plot_price_surface` in a prior pass.

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
