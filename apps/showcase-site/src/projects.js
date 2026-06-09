// Single source of truth for the 5 quant projects.
//
// All five live in one monorepo (nicholim/quant-lab); `path` is each project's
// subdirectory, used to build a deep link into the repo tree. `liveDemo: false`
// = not a hosted web service (no demo button): the market-data pipeline is a
// background worker (no HTTP endpoint) and the order-book engine is a native
// library shown via a static visualisation.

export const GITHUB_OWNER = "nicholim";
export const GITHUB_REPO = "quant-lab";
export const REPO_URL = `https://github.com/${GITHUB_OWNER}/${GITHUB_REPO}`;

export const projects = [
  {
    repo: "backtesting-framework",
    path: "packages/backtesting",
    title: "Backtesting Framework",
    tagline: "Event-driven backtester with a web dashboard and CLI.",
    summary:
      "A multi-asset, event-driven engine that fills at the next bar's open, with built-in " +
      "analytics, execution-cost modelling, resting LIMIT/STOP + OCO orders, opt-in short selling, " +
      "and an interactive Dash dashboard. Every run is persisted to DuckDB for SQL comparison.",
    metrics: ["~4,980 events/sec", "Event-driven", "DuckDB-persisted"],
    stack: ["Python 3.10+", "Dash", "DuckDB", "pandas"],
    versus: {
      vs: "backtrader / vectorbt / backtesting.py / zipline",
      note:
        "Event-driven by default (vectorbt's event sim + expanded order types are PRO/commercial), " +
        "multi-instrument (vs backtesting.py's single instrument), with first-class execution costs, " +
        "opt-in signed-FIFO short selling, and a turnkey optimize-then-backtest workflow.",
    },
    liveDemo: true,
    demoUrl: "https://backtest.nicholaslimarsha.com",
    demoNote: "Dash web service on Render (gunicorn).",
  },
  {
    repo: "market-data-pipeline",
    path: "packages/market-data",
    title: "Market Data Pipeline",
    tagline: "Async streaming ingestion daemon for live market data.",
    summary:
      "An asyncio + websockets daemon that streams trades from Binance or Coinbase, normalises them " +
      "to typed records, caches in Redis, and persists ticks + 1m OHLCV to TimescaleDB or a local " +
      "DuckDB file. Resilient reconnect/backoff, bounded-buffer backpressure, and a replay feeder.",
    metrics: ["Async streaming", "Auto reconnect/backoff", "Replay feeder"],
    stack: ["Python 3.11", "asyncio", "websockets", "Redis", "TimescaleDB"],
    versus: {
      vs: "cryptofeed / ccxt-pro / ArcticDB",
      note:
        "A self-hostable streaming daemon with pluggable persistence (Redis cache + Timescale or " +
        "DuckDB), a tested reconnect/backoff path, and a replay feeder, rather than a client library " +
        "or a standalone storage engine.",
    },
    liveDemo: false,
    demoStatus: "Background worker, no public web UI",
    demoNote:
      "Background worker on Render (Docker). Runs locally via `make run-market-data`, with a " +
      "Streamlit monitor via `make run-market-monitor`.",
  },
  {
    repo: "options-pricing-calculator",
    path: "packages/options-pricing",
    title: "Options Pricing Calculator",
    tagline: "Pricing library + interactive Streamlit app and CLI.",
    summary:
      "Black-Scholes-Merton and binomial-tree (European + American) pricing with the five Greeks, " +
      "higher-order Greeks (vanna/volga/charm), a Black-76 futures pricer, a Newton-Raphson IV " +
      "solver, and a vectorized batch API that prices real live option chains from free data.",
    metrics: ["Five Greeks", "Live IV surface", "European + American"],
    stack: ["Python 3.10+", "NumPy/SciPy", "Streamlit", "Plotly"],
    versus: {
      vs: "QuantLib / py_vollib / mibian",
      note:
        "More than vanilla European BS (py_vollib/mibian are European-only): adds American binomial " +
        "trees, higher-order Greeks, Black-76, a vectorized IV surface, and a live-data layer that " +
        "prices real chains and shows per-contract mispricing. QuantLib is the institutional " +
        "reference (exotics/Heston/MC) we benchmark vanilla accuracy against.",
    },
    liveDemo: true,
    demoUrl: "https://options.nicholaslimarsha.com",
    demoNote: "Streamlit web service on Render (also deployable to Streamlit Community Cloud).",
  },
  {
    repo: "order-book-simulator",
    path: "cpp/order-book",
    title: "Order Book Simulator",
    tagline: "C++17 price-time-priority matching engine with Python viz.",
    summary:
      "A limit-order-book matching engine in C++17 (price-time priority, partial fills, market/limit " +
      "orders, IOC/FOK/post-only, cancel/modify) with pybind11 bindings so Python drives the live " +
      "engine in-process, plus a Python simulator and visualiser.",
    metrics: ["~186k orders/sec", "p50 ~4.8 µs", "53 + 27 tests"],
    stack: ["C++17", "CMake", "pybind11", "Python (viz)", "GoogleTest"],
    versus: {
      vs: "ABIDES / mbt-gym",
      note:
        "A real price-time-priority matching engine (like ABIDES) rather than a model-based stochastic " +
        "fill simulator (mbt-gym, which does not match orders). Native C++ core for throughput, " +
        "Python-drivable via pybind11.",
    },
    liveDemo: false,
    demoStatus: "Native library; static depth chart below",
    demoNote:
      "Not a hosted service: a native library and simulator. Shown here via a static depth-chart " +
      "visualisation. A browser-runnable WASM build is future work.",
    artifact: "orderbook-depth.svg",
  },
  {
    repo: "portfolio-optimization-engine",
    path: "packages/portfolio-optimization",
    title: "Portfolio Optimization Engine",
    tagline: "Modern Portfolio Theory optimizer (library + CLI + API).",
    summary:
      "Mean-variance / MPT optimization (max Sharpe, min volatility, risk parity, max Sortino, " +
      "min CVaR, target return/vol), a true solved efficient frontier, Hierarchical Risk Parity, " +
      "Black-Litterman views, and Ledoit-Wolf shrinkage, all on a numpy/scipy-only stack (no cvxpy).",
    metrics: ["Max Sharpe · HRP", "Solved frontier", "numpy/scipy only"],
    stack: ["Python 3.10+", "scipy", "pandas", "FastAPI"],
    versus: {
      vs: "PyPortfolioOpt / riskfolio-lib / skfolio / cvxpy",
      note:
        "A focused, well-tested MPT optimizer (HRP, Black-Litterman, Ledoit-Wolf, and a solved " +
        "frontier on a numpy/scipy-only stack, no cvxpy) reused by the backtester, plus a thin " +
        "FastAPI demo, rather than a broad convex-optimization toolkit.",
    },
    liveDemo: true,
    demoUrl: "https://portfolio-optimization-api-k31s.onrender.com/docs",
    demoNote: "FastAPI service on Render (uvicorn). Opens the interactive /docs (Swagger) UI.",
  },
];
