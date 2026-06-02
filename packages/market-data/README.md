# Real-Time Market Data Pipeline

[![CI](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)

> An asyncio daemon that streams exchange trades over WebSocket, normalizes them to typed
> records, caches the latest state in Redis, and batch-writes ticks + 1-minute OHLCV bars to
> TimescaleDB.

## Why this exists

Quantitative research and live trading both need a clean, durable record of raw market data.
Pulling that data ad-hoc from an exchange REST API is rate-limited and lossy. This project is a
small, single-purpose **ingestion daemon**: one process that holds a resilient WebSocket
connection open, turns each exchange message into a stable schema, and persists it — so your
strategies, backtests, and dashboards read from your own time-series store instead of hammering
the exchange. It is intentionally narrow: not an exchange-API SDK, not an order-management
system, just the ingest-and-store half of a market-data stack.

## Architecture

```mermaid
graph LR
    A[Exchange<br/>WebSocket] --> B[Async Client<br/>Auto-Reconnect]
    B --> C[Tick<br/>Normalizer]
    C --> D[Redis Cache<br/>Pub/Sub]
    C --> E[TimescaleDB<br/>Hypertable]
    C --> F[OHLCV<br/>Aggregator]
    F --> E

    style A fill:#3b82f6,color:#fff
    style B fill:#8b5cf6,color:#fff
    style C fill:#06b6d4,color:#fff
    style D fill:#ef4444,color:#fff
    style E fill:#10b981,color:#fff
    style F fill:#f59e0b,color:#fff
```

**Data flow** (all `asyncio`, single process — see `src/pipeline.py`):

1. **WebSocket client** (`websocket_client.py`) opens a combined `@trade` stream for the
   configured symbols and yields raw JSON messages. On drop it reconnects with exponential
   backoff (2s → 60s cap, `max_retries=10`); the retry budget resets only after a message is
   actually delivered, so a connection that flaps cannot reconnect forever.
2. **Normalizer** (`normalizer.py`) parses each raw message into a typed `Trade` (lowercased
   symbol, float price/qty, `buy`/`sell` side, UTC-aware timestamp). Malformed messages are
   logged and dropped, not raised.
3. **Cache** (`cache.py`) writes the latest price (`price:<symbol>` hash), pushes onto a capped
   recent-trades list (`trades:<symbol>`, max 1000), and publishes to a `trades:<symbol>`
   pub/sub channel for downstream consumers.
4. **Buffer / batch** — the pipeline appends each trade to an in-memory buffer and flushes to
   storage when it reaches `BATCH_SIZE`, or every `FLUSH_INTERVAL_SECONDS`, whichever comes
   first. A failed flush re-adds the batch instead of dropping it.
5. **Storage** (`storage.py`) writes batched trades and completed OHLCV bars to TimescaleDB
   hypertables (`trades`, `ohlcv`) via `asyncpg`. The OHLCV aggregator emits a 1-minute bar
   when the minute rolls over.

## Features

- **WebSocket Ingestion** — Async client with auto-reconnect and exponential backoff (up to 60s)
- **Tick Normalization** — Standardize raw trade messages into typed `Trade` dataclass
- **OHLCV Aggregation** — Real-time candlestick bar construction from tick-level data
- **Redis Caching** — Latest prices, capped trade lists, and pub/sub for downstream consumers
- **TimescaleDB Storage** — Hypertable-based time-series storage with batch inserts for throughput
- **Async Pipeline** — Fully asynchronous architecture using `asyncio` with graceful shutdown

## Technical Highlights

- **Fully async architecture** — End-to-end `asyncio` from WebSocket ingestion through Redis caching to TimescaleDB storage, zero blocking I/O
- **Fault-tolerant ingestion** — Auto-reconnect with exponential backoff (2s → 60s cap), isolated callback error handling prevents single message failures from crashing the consumer loop
- **UTC-normalized timestamps** — All trade timestamps converted to timezone-aware UTC at normalization layer, consistent across storage and cache
- **Batch-optimized writes** — Trade buffer with configurable flush threshold (count-based) and periodic timer (time-based) for throughput without data loss
- **Typed data models** — `Trade` and `OHLCVBar` dataclasses enforce schema at the normalization boundary, not at storage

## Tech Stack

- **Python 3.11+** (asyncio)
- **websockets** — Async WebSocket client
- **redis[hiredis]** — In-memory caching and pub/sub
- **asyncpg** — Async PostgreSQL driver
- **TimescaleDB** — Time-series database (PostgreSQL extension)

## Prerequisites

- Python 3.11+
- Redis server running locally or remotely
- PostgreSQL with TimescaleDB extension

## Quick Start

```bash
git clone https://github.com/nicholim/quant-lab.git
cd market-data-pipeline

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Redis and PostgreSQL connection strings

# Run the pipeline
python main.py --symbols btcusdt,ethusdt --log-level INFO
```

## Configuration

All settings are read from environment variables (or a `.env` file) by `src/config.py`. CLI flags
`--symbols` and `--log-level` override the corresponding env vars.

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `DATABASE_URL` | `postgresql://user:password@localhost:5432/marketdata` | TimescaleDB connection |
| `WS_URL` | `wss://stream.binance.com:9443/ws` | WebSocket endpoint (Binance-style `@trade` streams) |
| `SYMBOLS` | `btcusdt,ethusdt` | Comma-separated trading pairs |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `BATCH_SIZE` | `100` | Trades buffered before a batch DB insert |
| `FLUSH_INTERVAL_SECONDS` | `5` | Max seconds between DB flushes |

## Usage

```python
from src.config import Config
from src.normalizer import TickNormalizer

# Normalize a raw Binance trade message
normalizer = TickNormalizer()
trade = normalizer.normalize_trade({
    "s": "BTCUSDT", "p": "67500.50", "q": "0.15", "m": False, "T": 1712400000000
})
# Trade(symbol='btcusdt', price=67500.5, quantity=0.15, side='buy', ...)

# Accumulate trades into OHLCV bars
bar = normalizer.accumulate_trade(trade)
if bar:
    print(f"1m bar: O={bar.open} H={bar.high} L={bar.low} C={bar.close} V={bar.volume}")
```

## vs. cryptofeed / ccxt-pro / ArcticDB

These tools overlap with parts of this pipeline but solve different problems. This project is a
**runnable ingestion daemon with an opinionated storage layer**, not a multi-exchange client
library or a standalone database engine.

| | This pipeline | [cryptofeed](https://github.com/bmoscon/cryptofeed) | [ccxt-pro](https://docs.ccxt.com/#/ccxt.pro/README) | [ArcticDB](https://github.com/man-group/ArcticDB) |
|---|---|---|---|---|
| What it is | Streaming ingest + storage daemon | Crypto WS feed handler library | Unified exchange WS/REST client | DataFrame time-series store |
| Exchanges | One Binance-style `@trade` stream | 40+ exchanges, many channels | 100+ exchanges (unified API) | N/A (storage only) |
| Channels | Trades → 1m OHLCV | trades, L2/L3 book, ticker, funding, … | trades, book, ticker, OHLCV, orders | N/A |
| Persistence | Built-in: Redis cache + TimescaleDB | Pluggable backends (Redis, Mongo, Kafka, …) | None (you write your own) | Its core job (S3/LMDB, versioned) |
| Ready to run? | Yes — `python main.py` | Library; you write the handler | Library; you write the loop | Library; you write read/write code |
| License | MIT (this repo) | Open source | ccxt is open source; **ccxt-pro is paid** | Open source |

**What this project does well:** it is a complete, deployable example you can run as-is — resilient
reconnect, typed normalization, batched writes that survive a flush failure, and a TimescaleDB
schema with hypertables and per-symbol indexes already wired up.

**What it intentionally does *not* do:** no multi-exchange abstraction, no order books / ticker /
funding channels, no order placement, and only a single Binance-style `@trade` stream shape. It is
not a database engine — TimescaleDB does the storage, ArcticDB-style versioning/time-travel is out
of scope.

**Who it's for:** someone who wants their own durable trade + OHLCV store from a crypto stream and
prefers a small, readable codebase they can extend over adopting a large feed-handler framework. If
you need many exchanges and channels, reach for cryptofeed (and point one of its Redis/SQL backends
at a similar schema); if you need a serverless DataFrame store, use ArcticDB.

See [`awesome-quant`](https://github.com/wilsonfreitas/awesome-quant) for the wider ecosystem.

## Project Structure

```
market-data-pipeline/
├── main.py                  # Entry point with argparse and signal handling
├── .env.example             # Environment variable template
├── requirements.txt
└── src/
    ├── config.py             # Dataclass-based config from env vars
    ├── websocket_client.py   # Async WS client with auto-reconnect
    ├── normalizer.py         # Trade/OHLCV normalization and aggregation
    ├── cache.py              # Redis caching layer (prices, trades, pub/sub)
    ├── storage.py            # TimescaleDB storage (hypertables, batch inserts)
    └── pipeline.py           # Main orchestrator tying all components together
```

## Deployment (Docker + Render background worker)

This pipeline is a **daemon**, not a web service, so it deploys as a Render
**background worker** (`type: worker`) built from the included `Dockerfile`.

```bash
# Build and run locally
docker build -t market-data-pipeline .
docker run --rm \
  -e REDIS_URL=redis://host:6379 \
  -e DATABASE_URL="postgresql://user:pass@host:5432/marketdata?sslmode=require" \
  market-data-pipeline
```

### Render (via `render.yaml` blueprint)

1. Push this repo to GitHub, then in Render: **New → Blueprint** and select it.
2. The blueprint creates the worker plus a managed **Key Value (Redis-compatible)**
   instance and auto-injects `REDIS_URL`.
3. **TimescaleDB is not Render Postgres.** Render's managed Postgres lacks the
   TimescaleDB extension/hypertables this pipeline expects. Provision an **external
   TimescaleDB** (e.g. [Timescale Cloud](https://www.timescale.com/cloud) free tier or
   Aiven) and paste its connection string into the `DATABASE_URL` secret env var in the
   Render dashboard (it is marked `sync: false`, so Render prompts for it on deploy).
4. Adjust `WS_URL` / `SYMBOLS` env vars as needed.

The service is defined in the **root `render.yaml`** (one blueprint for the whole monorepo)
under the `market-data-pipeline` worker, with `rootDir: packages/market-data` and
`dockerfilePath: ./Dockerfile`. `autoDeploy` is off, so deploys are manual. There is **no
per-package `render.yaml`** — edit the root file.

### Behavior without live infra (Redis / TimescaleDB)

Redis and TimescaleDB are **hard dependencies** for ingestion — there is nowhere to cache or
persist trades without them. The worker does **not** silently no-op or hang; it **fails fast
with one actionable log line** and exits non-zero, so the failure is obvious in the Render logs
and the worker restarts (and retries) on schedule. On startup `Pipeline.start()`:

- Connects to Redis first. If `REDIS_URL` points at nothing, you get a single line like:

  ```
  ERROR | src.pipeline | Could not connect to Redis at redis://… (ConnectionError: …).
          Set REDIS_URL to a reachable instance and restart.
  ```

  and the worker exits — instead of dumping a ~20-frame redis/asyncio traceback.
- Then connects to TimescaleDB and runs `init_schema()` (which calls `create_hypertable`, so a
  plain Postgres without the Timescale extension fails here). A bad/unreachable `DATABASE_URL`
  produces the analogous `Could not connect to/initialize TimescaleDB at … Set DATABASE_URL …`
  line, then exit.

This is by design: a market-data ingester with no store is a no-op, so booting "successfully"
into a state that drops every trade would be worse than failing loudly. Once it is connected,
the WebSocket layer is the resilient part — it auto-reconnects with exponential backoff and a
retry budget (see `websocket_client.py`), so transient stream drops do not kill the worker.

> **No managed-infra demo:** because Render cannot host TimescaleDB and the free Key Value
> add-on is the only piece it provisions, this worker is the one project in the monorepo that
> needs the user to bring an external database before it does anything useful. It is included as
> a deployable, honest example rather than a one-click live demo.

## Development

```bash
pip install -r requirements.txt
ruff check .      # lint
mypy              # type-check (gradual)
pytest            # 76 tests, ~99% coverage; gate --cov-fail-under=85
```

The test suite uses in-memory fakes (`FakeWebSocket` / `FakeRedis` / `FakePool` in
`tests/conftest.py`) — **no live WebSocket, Redis, or TimescaleDB is ever touched**, so the full
suite runs offline in well under a second. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev
workflow, branch/commit conventions, and the PR checklist.

## License

MIT — see [`LICENSE`](LICENSE).
