// Single source of truth for the 5 quant projects.
//
// `demoUrl` values are PLACEHOLDERS. Once each Render service is deployed,
// replace the `<repo>.onrender.com` host with the real URL Render assigns.
// `liveDemo: false` means the project is not a hosted service (no button).

export const GITHUB_OWNER = "nicholim";

export const projects = [
  {
    repo: "backtesting-framework",
    title: "Backtesting Framework",
    tagline: "Event-driven backtester with a web dashboard and CLI.",
    description:
      "An event-driven backtesting engine for trading strategies with built-in analytics " +
      "(Sharpe, Sortino, drawdown), execution-cost modelling, and an interactive Dash dashboard. " +
      "One-way depends on the portfolio optimizer for rebalance strategies.",
    stack: ["Python 3.10+", "Dash", "DuckDB", "pandas"],
    versus: {
      vs: "backtrader / vectorbt / backtesting.py / zipline",
      note:
        "Event-driven by default (vectorbt's event sim + expanded order types are PRO/commercial), " +
        "multi-instrument (vs backtesting.py's single instrument), with first-class execution costs.",
    },
    liveDemo: true,
    demoUrl: "https://backtesting-framework.onrender.com",
    demoNote: "Dash web service on Render (gunicorn).",
  },
  {
    repo: "market-data-pipeline",
    title: "Market Data Pipeline",
    tagline: "Async streaming ingestion daemon for live market data.",
    description:
      "An asyncio + websockets ingestion daemon that normalises live market data, caches in Redis, " +
      "and persists OHLCV to TimescaleDB. Resilient reconnect/backoff and batched flushing.",
    stack: ["Python 3.11", "asyncio", "websockets", "Redis", "TimescaleDB"],
    versus: {
      vs: "cryptofeed / ccxt-pro / ArcticDB",
      note:
        "A self-hostable streaming daemon with explicit Redis + Timescale persistence and a tested " +
        "reconnect/backoff path, rather than a client library or a storage engine.",
    },
    liveDemo: true,
    demoUrl: "https://market-data-pipeline.onrender.com",
    demoNote:
      "Background worker on Render (Docker). Needs Render Redis + an external TimescaleDB/Postgres; " +
      "no public web UI — link points at the service dashboard.",
  },
  {
    repo: "options-pricing-calculator",
    title: "Options Pricing Calculator",
    tagline: "Pricing library + interactive Streamlit app and CLI.",
    description:
      "Black-Scholes, binomial-tree, and Monte-Carlo option pricing with Greeks and implied-volatility " +
      "solving, exposed as a library, a CLI, and an interactive Streamlit + Plotly app with Greek surfaces.",
    stack: ["Python 3.10+", "NumPy/SciPy", "Streamlit", "Plotly"],
    versus: {
      vs: "QuantLib / py_vollib / mibian",
      note:
        "More than vanilla European BS (py_vollib/mibian are European-only): adds binomial trees, " +
        "Monte-Carlo, Greek visualisers and an IV solver in an interactive app. QuantLib is the " +
        "institutional reference we benchmark accuracy against.",
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
      "orders, cancel/modify) with a Python simulator and visualiser. Validated by 35 GoogleTest cases.",
    stack: ["C++17", "CMake", "Python (viz)", "GoogleTest"],
    versus: {
      vs: "ABIDES / mbt-gym",
      note:
        "A real price-time-priority matching engine (like ABIDES) rather than a model-based stochastic " +
        "fill simulator (mbt-gym). Native C++ core for throughput.",
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
      "Mean-variance / MPT portfolio optimization (max Sharpe, min volatility, target return/vol, " +
      "efficient frontier) with Monte-Carlo simulation and analysis tooling. Consumed by the backtester; " +
      "a thin FastAPI wrapper exposes an /optimize endpoint for the demo.",
    stack: ["Python 3.10+", "scipy", "pandas", "FastAPI"],
    versus: {
      vs: "PyPortfolioOpt / riskfolio-lib / skfolio / cvxpy",
      note:
        "A focused, well-tested MPT optimizer with a clean library/CLI API (reused by the backtester) " +
        "plus a thin FastAPI demo, rather than a broad convex-optimization toolkit.",
    },
    liveDemo: true,
    demoUrl: "https://portfolio-optimization-engine.onrender.com",
    demoNote: "FastAPI service on Render (uvicorn). Try POST /optimize or open /docs.",
  },
];
