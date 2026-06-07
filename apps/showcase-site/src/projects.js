// Single source of truth for the 5 quant projects.
//
// `demoUrl` hosts are set to each Render SERVICE NAME (from render.yaml), which
// is the URL Render assigns by default: https://<service-name>.onrender.com.
// VERIFY each against the Render dashboard after deploy — Render appends a
// random suffix (e.g. -a1b2) if that subdomain is already taken globally; if so,
// paste the real host here. `liveDemo: false` = not a hosted web service (no button).

export const GITHUB_OWNER = "nicholim";

export const projects = [
  {
    repo: "backtesting-framework",
    title: "Backtesting Framework",
    tagline: "Event-driven backtester with a web dashboard and CLI.",
    description:
      "A multi-asset, event-driven backtesting engine that fills at the next bar's open, with " +
      "built-in analytics (Sharpe, Sortino, drawdown, beta/alpha), execution-cost modelling, " +
      "resting LIMIT/STOP + OCO orders, protective/trailing exits, opt-in short selling, CSV/" +
      "DataFrame data handlers (no yfinance needed), and an interactive Dash dashboard. One-way " +
      "depends on the portfolio optimizer for walk-forward MPT rebalance strategies. Every run is " +
      "persisted to DuckDB for SQL comparison; ~4,980 events/sec on a synthetic benchmark.",
    stack: ["Python 3.10+", "Dash", "DuckDB", "pandas"],
    versus: {
      vs: "backtrader / vectorbt / backtesting.py / zipline",
      note:
        "Event-driven by default (vectorbt's event sim + expanded order types are PRO/commercial), " +
        "multi-instrument (vs backtesting.py's single instrument), with first-class execution costs, " +
        "opt-in signed-FIFO short selling, and a turnkey optimize-then-backtest workflow.",
    },
    liveDemo: true,
    demoUrl: "https://backtesting-dashboard-id3q.onrender.com",
    demoNote: "Dash web service on Render (gunicorn).",
  },
  {
    repo: "market-data-pipeline",
    title: "Market Data Pipeline",
    tagline: "Async streaming ingestion daemon for live market data.",
    description:
      "An asyncio + websockets ingestion daemon that streams trades from Binance or Coinbase " +
      "(pluggable adapter), normalises them to typed records, caches in Redis, and persists ticks + " +
      "1m OHLCV to a pluggable storage backend (TimescaleDB or a local DuckDB file — zero infra). " +
      "Resilient reconnect/backoff, bounded-buffer backpressure, fail-fast on unreachable infra, and " +
      "a replay() feeder that streams stored history back out for backtests.",
    stack: ["Python 3.11", "asyncio", "websockets", "Redis", "TimescaleDB"],
    versus: {
      vs: "cryptofeed / ccxt-pro / ArcticDB",
      note:
        "A self-hostable streaming daemon with pluggable persistence (Redis cache + Timescale or " +
        "DuckDB), a tested reconnect/backoff path, and a replay feeder, rather than a client library " +
        "or a standalone storage engine.",
    },
    liveDemo: false,
    demoNote:
      "Background worker on Render (Docker) with Render Key Value (Redis) + a local DuckDB sink — " +
      "no public web UI, so there's no live link (a worker has no HTTP endpoint). Runs locally via " +
      "`make run-market-data`, with a Streamlit monitor via `make run-market-monitor`.",
  },
  {
    repo: "options-pricing-calculator",
    title: "Options Pricing Calculator",
    tagline: "Pricing library + interactive Streamlit app and CLI.",
    description:
      "Black-Scholes-Merton and Cox-Ross-Rubinstein binomial-tree (European + American) option " +
      "pricing with the five Greeks, higher-order Greeks (vanna/volga/charm), a Black-76 futures " +
      "pricer, a Newton-Raphson IV solver, and a NumPy-vectorized batch API for whole-chain pricing " +
      "and a real solved IV surface. Prices live option chains from free data (yfinance chains + " +
      "Finnhub spot) — exposed as a library, a CLI, and an interactive Streamlit + Plotly app.",
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
    demoUrl: "https://options-pricing-calculator.onrender.com",
    demoNote: "Streamlit web service on Render (also deployable to Streamlit Community Cloud).",
  },
  {
    repo: "order-book-simulator",
    title: "Order Book Simulator",
    tagline: "C++17 price-time-priority matching engine with Python viz.",
    description:
      "A limit-order-book matching engine in C++17 (price-time priority, partial fills, market/limit " +
      "orders, IOC/FOK/post-only time-in-force, cancel/modify) with pybind11 bindings so Python drives " +
      "the live engine in-process, plus a Python simulator and visualiser. Validated by 53 GoogleTest " +
      "cases + 27 Python tests; ~186k orders/sec, p50 ~4.8 µs on an M2 Pro benchmark.",
    stack: ["C++17", "CMake", "pybind11", "Python (viz)", "GoogleTest"],
    versus: {
      vs: "ABIDES / mbt-gym",
      note:
        "A real price-time-priority matching engine (like ABIDES) rather than a model-based stochastic " +
        "fill simulator (mbt-gym, which does not match orders). Native C++ core for throughput, " +
        "Python-drivable via pybind11.",
    },
    liveDemo: false,
    demoNote:
      "Not a hosted service — it is a native library + simulator. Shown here via a static depth-chart " +
      "visualisation. Browser-runnable WASM build is future work.",
    artifact: "orderbook-depth.svg",
  },
  {
    repo: "portfolio-optimization-engine",
    title: "Portfolio Optimization Engine",
    tagline: "Modern Portfolio Theory optimizer (library + CLI + API).",
    description:
      "Mean-variance / MPT portfolio optimization — max Sharpe, min volatility, risk parity, max " +
      "Sortino, min CVaR (Rockafellar-Uryasev LP), target return/vol, a true solved efficient " +
      "frontier, Hierarchical Risk Parity, Black-Litterman views, and opt-in Ledoit-Wolf covariance " +
      "shrinkage — with Monte-Carlo VaR/CVaR and a standalone metrics module. numpy/pandas/scipy only " +
      "(no cvxpy). Consumed by the backtester; a thin FastAPI wrapper exposes an /optimize endpoint.",
    stack: ["Python 3.10+", "scipy", "pandas", "FastAPI"],
    versus: {
      vs: "PyPortfolioOpt / riskfolio-lib / skfolio / cvxpy",
      note:
        "A focused, well-tested MPT optimizer — HRP, Black-Litterman, Ledoit-Wolf, and a solved " +
        "frontier on a numpy/scipy-only stack (no cvxpy) — reused by the backtester, plus a thin " +
        "FastAPI demo, rather than a broad convex-optimization toolkit.",
    },
    liveDemo: true,
    demoUrl: "https://portfolio-optimization-api-k31s.onrender.com/docs",
    demoNote: "FastAPI service on Render (uvicorn). Opens the interactive /docs (Swagger) UI.",
  },
];
