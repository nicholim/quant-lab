# Real-Time Market Data Pipeline

[![CI](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholim/quant-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Tests](https://img.shields.io/badge/tests-173%20passing-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-~98%25-brightgreen.svg)](pyproject.toml)

> An asyncio daemon that streams exchange trades over WebSocket (Binance, Coinbase, Kraken, or
> Bitstamp via a pluggable adapter), normalizes them to typed records, caches the latest state in Redis, and
> batch-writes ticks + 1-minute OHLCV bars to a pluggable storage backend (TimescaleDB or DuckDB).
> A `replay()` feeder streams stored history back out for backtests.

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

1. **WebSocket client** (`websocket_client.py`) connects to the URL the configured
   `ExchangeAdapter` (`adapters.py`) builds for the symbols, sends the adapter's subscribe payload
   if any, and yields raw JSON messages. On drop it reconnects with exponential backoff
   (2s → 60s cap, `max_retries=10`); the retry budget resets only after a message is actually
   delivered, so a connection that flaps cannot reconnect forever.
2. **Normalizer** (`normalizer.py`) delegates per-exchange parsing to the adapter, turning each raw
   message into a typed `Trade` (lowercased symbol, float price/qty, `buy`/`sell` aggressor side,
   UTC-aware timestamp). Non-trade messages and malformed payloads are dropped, not raised.
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
- **Pluggable exchanges** — An `ExchangeAdapter` protocol (`src/adapters.py`) captures everything that
  varies per venue (WS URL, subscribe payload, raw-message → `Trade` parsing). Ships **Binance**
  (default), **Coinbase**, **Kraken**, and **Bitstamp**; select with `EXCHANGE=binance|coinbase|kraken|bitstamp`
  or the `--exchange` CLI flag. Adding a venue is one small class — the client, normalizer schema, and
  storage are untouched
- **Tick Normalization** — Standardize raw trade messages into typed `Trade` dataclass
- **OHLCV Aggregation** — Real-time candlestick bar construction from tick-level data; a single-trade
  minute still produces a valid bar and the final in-progress bar is flushed on shutdown (`flush_all()`)
  so no bar is dropped at end-of-stream
- **Replay / research feeder** — `Pipeline.replay(symbol, start, end, source="trades"|"ohlcv")` streams
  stored records back out of *either* backend in timestamp order (oldest-first) as an async generator,
  turning the ingest daemon into a historical feeder for backtests
- **Bounded buffer / backpressure** — the in-memory trade buffer is capped at `MAX_BUFFER_SIZE`; on a
  slow/stalled sink the pipeline blocks on an inline flush, and only if the sink stays unreachable does
  it drop the oldest trades (with a logged running count) so a dead sink can never OOM the worker
- **Redis Caching** — Latest prices, capped trade lists, and pub/sub for downstream consumers
- **Pluggable Storage** — A `StorageBackend` protocol with two interchangeable sinks: **TimescaleDB**
  (hypertables + batch inserts) or a local **DuckDB** file (`STORAGE_BACKEND=duckdb`) — the latter needs
  no external DB or network, so the pipeline runs on free/cloud infra and locally with zero setup
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
`--symbols`, `--exchange`, and `--log-level` override the corresponding env vars. Local config and
secrets can live in a `.env` file (loaded once at import via python-dotenv); real environment
variables always take precedence over `.env`.

```bash
# Pick the venue from the command line (overrides EXCHANGE / .env)
python main.py --exchange kraken --symbols btcusd,ethusd
```

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `STORAGE_BACKEND` | `timescale` | Storage sink: `timescale` (external TimescaleDB) or `duckdb` (local file) |
| `DATABASE_URL` | `postgresql://user:password@localhost:5432/marketdata` | TimescaleDB connection (used when `STORAGE_BACKEND=timescale`) |
| `DUCKDB_PATH` | `data/marketdata.duckdb` | Local DuckDB file path (used when `STORAGE_BACKEND=duckdb`) |
| `EXCHANGE` | `binance` | Exchange adapter: `binance` (URL-embedded `@trade` streams), `coinbase` (`matches` feed), `kraken` (v1 `trade` feed), or `bitstamp` (`live_trades` channel). Overridable per-run with `--exchange` |
| `WS_URL` | `wss://stream.binance.com:9443/ws` | WebSocket endpoint (used by the Binance adapter; Coinbase/Kraken/Bitstamp use their own fixed feeds) |
| `SYMBOLS` | `btcusdt,ethusdt` | Comma-separated trading pairs (Coinbase/Bitstamp: `btcusd`; Kraken auto-maps `btcusd` → `XBT/USD`) |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `BATCH_SIZE` | `100` | Trades buffered before a batch DB insert |
| `FLUSH_INTERVAL_SECONDS` | `5` | Max seconds between DB flushes |
| `MAX_BUFFER_SIZE` | `1000` | Backpressure cap on the in-memory trade buffer (block-then-drop-oldest when full) |

### Storage backends

Persistence goes through a small `StorageBackend` protocol (`src/storage_backend.py`) — the pipeline
calls `connect` / `init_schema` / `insert_trades` / `insert_ohlcv` / `query_*` and doesn't care which
sink is behind it. `STORAGE_BACKEND` (default `timescale`, unchanged behavior) selects the
implementation:

- **`timescale`** — `TimeSeriesStorage` (`asyncpg` → TimescaleDB hypertables). Needs an external
  TimescaleDB; this is what the Render worker uses.
- **`duckdb`** — `DuckDBStorage` writes the **same** normalized `trades` / `ohlcv` schema to a local
  DuckDB file (`DUCKDB_PATH`), with **no external DB and no network**. Run the whole pipeline with
  zero infra beyond Redis:

  ```bash
  STORAGE_BACKEND=duckdb DUCKDB_PATH=data/marketdata.duckdb python main.py --symbols btcusdt
  ```

  Captured tables can be exported to Parquet (`DuckDBStorage.export_parquet(dir)`) for downstream
  research/backtesting tooling.

### Exchange adapters

The ingest side is exchange-agnostic behind an `ExchangeAdapter` protocol (`src/adapters.py`). An
adapter supplies only the three things that differ per venue — the **WS URL** for a list of symbols,
the **subscribe payload** to send after connecting (or `None` if streams are URL-embedded), and how to
**parse one raw message into the same normalized `Trade`** the pipeline already consumes (lowercased
symbol, float price/qty, `buy`/`sell` aggressor side, UTC-aware timestamp, `exchange` tag). The
client, normalizer schema, and storage never change. `EXCHANGE` (default `binance`) selects it:

- **`binance`** — `BinanceAdapter`: combined `@trade` streams embedded in the URL path
  (`…/btcusdt@trade/ethusdt@trade`), no subscribe message; `m` ("buyer is maker") maps to the
  aggressor side. Built from `WS_URL`, so the default path is byte-identical to before.
- **`coinbase`** — `CoinbaseAdapter`: connects to the fixed `wss://ws-feed.exchange.coinbase.com`
  feed and subscribes to the `matches` channel for the configured products. A `match` /`last_match`
  message (`{"type":"match","product_id":"BTC-USD","price":"…","size":"…","side":"sell",…}`) is
  parsed to a `Trade`. Coinbase's `side` is the **maker** side, so it's flipped to the **taker/
  aggressor** side to stay consistent with Binance ("who crossed the spread"). Symbols round-trip
  `btcusd` ⇄ `BTC-USD`.
- **`kraken`** — `KrakenAdapter`: connects to the fixed `wss://ws.kraken.com` (v1) feed and
  subscribes to the `trade` channel. A trade update arrives as a JSON **array**
  (`[channelID, [[price, volume, time, side, orderType, misc], …], "trade", "XBT/USD"]`); the first
  fill is parsed. Kraken's `side` (`b`/`s`) is **already the taker/aggressor side**, so no flip is
  needed. Symbols round-trip `btcusd` ⇄ `XBT/USD` (Kraken uses `XBT` for bitcoin). A batched update
  yields its first fill (documented simplification).
- **`bitstamp`** — `BitstampAdapter`: connects to the fixed `wss://ws.bitstamp.net` feed and
  subscribes to the `live_trades_<pair>` channel. A trade arrives as
  `{"event":"trade","channel":"live_trades_btcusd","data":{"price":…,"amount":…,"type":0,"microtimestamp":…}}`;
  `type` (`0`=buy, `1`=sell) is the taker/aggressor side (no flip). `microtimestamp` is preferred for
  the timestamp, falling back to `timestamp`. Symbols are the channel suffix (`btcusd`) directly.

  All four venues are keyless public market data.

  ```bash
  EXCHANGE=coinbase SYMBOLS=btcusd,ethusd python main.py
  python main.py --exchange kraken --symbols btcusd
  python main.py --exchange bitstamp --symbols btcusd
  ```

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

# Emit the final in-progress bar(s) at end-of-stream (otherwise dropped)
for bar in normalizer.flush_all():
    ...
```

### Replay stored data as a research feeder

`Pipeline.replay()` streams previously-persisted records back out of whichever `StorageBackend` is
configured, in timestamp order (oldest-first), as an async generator — so a backtest can consume
captured history through the same shape as the live feed:

```python
from datetime import UTC, datetime
from src.config import Config
from src.pipeline import Pipeline

pipe = Pipeline(Config())          # STORAGE_BACKEND=duckdb or timescale
await pipe.storage.connect()
start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)

async for trade in pipe.replay("btcusdt", start, end):           # source="trades" (default)
    ...
async for bar in pipe.replay("btcusdt", start, end, source="ohlcv", interval="1m"):
    ...
```

## vs. cryptofeed / ccxt-pro / ArcticDB

These tools overlap with parts of this pipeline but solve different problems. This project is a
**runnable ingestion daemon with an opinionated storage layer**, not a multi-exchange client
library or a standalone database engine.

| | This pipeline | [cryptofeed](https://github.com/bmoscon/cryptofeed) | [ccxt-pro](https://docs.ccxt.com/#/ccxt.pro/README) | [ArcticDB](https://github.com/man-group/ArcticDB) |
|---|---|---|---|---|
| What it is | Streaming ingest + storage daemon | Crypto WS feed handler library | Unified exchange WS/REST client | DataFrame time-series store |
| Exchanges | Binance + Coinbase + Kraken + Bitstamp, pluggable adapter | 40+ exchanges, many channels | 100+ exchanges (unified API) | N/A (storage only) |
| Channels | Trades → 1m OHLCV | trades, L2/L3 book, ticker, funding, … | trades, book, ticker, OHLCV, orders | N/A |
| Persistence | Built-in, pluggable: Redis cache + TimescaleDB or local DuckDB | Pluggable backends (Redis, Mongo, Kafka, …) | None (you write your own) | Its core job (S3/LMDB, versioned) |
| Replay / research feeder | Yes (`Pipeline.replay()` over the store) | No | No | Read API (it is the store) |
| Ready to run? | Yes — `python main.py` (zero infra with `STORAGE_BACKEND=duckdb`) | Library; you write the handler | Library; you write the loop | Library; you write read/write code |
| License | MIT (this repo) | Open source | ccxt is open source; **ccxt-pro is paid** | Open source |

**What this project does well:** it is a complete, deployable example you can run as-is — resilient
reconnect, typed normalization, batched writes that survive a flush failure, and a TimescaleDB
schema with hypertables and per-symbol indexes already wired up.

**What it intentionally does *not* do:** no order books / ticker / funding channels, no order
placement, and only the trade-stream shape (one channel) on the four adapters it ships (Binance,
Coinbase, Kraken, Bitstamp) — not cryptofeed's 40+ venues or many channels. It is not a database engine — Timescale
or DuckDB does the storage, ArcticDB-style versioning/time-travel is out of scope.

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
    ├── adapters.py           # ExchangeAdapter protocol + Binance/Coinbase/Kraken/Bitstamp adapters
    ├── websocket_client.py   # Async WS client with auto-reconnect (driven by an adapter)
    ├── normalizer.py         # Trade/OHLCV normalization (parsing delegated to the adapter)
    ├── cache.py              # Redis caching layer (prices, trades, pub/sub)
    ├── storage_backend.py    # StorageBackend protocol (the persistence contract)
    ├── storage.py            # TimescaleDB backend (hypertables, batch inserts)
    ├── duckdb_storage.py     # Local DuckDB/Parquet backend (no external DB/network)
    └── pipeline.py           # Main orchestrator tying all components together
```

## Deployment (Docker + Render background worker)

This pipeline is a **daemon**, not a web service, so it deploys as a Render
**background worker** (`type: worker`) built from the included `Dockerfile`.

```bash
# Build and run locally — no external DB (DuckDB on local disk + Redis only)
docker build -t market-data-pipeline .
docker run --rm \
  -e REDIS_URL=redis://host:6379 \
  -e STORAGE_BACKEND=duckdb \
  -e DUCKDB_PATH=data/marketdata.duckdb \
  market-data-pipeline

# OR with durable TimescaleDB storage
docker run --rm \
  -e REDIS_URL=redis://host:6379 \
  -e STORAGE_BACKEND=timescale \
  -e DATABASE_URL="postgresql://user:pass@host:5432/marketdata?sslmode=require" \
  market-data-pipeline
```

### Render (via `render.yaml` blueprint)

The blueprint deploys this worker **with no external database by default**:
`STORAGE_BACKEND=duckdb` persists the normalized trades/ohlcv to a local DuckDB
file (`DUCKDB_PATH`), so the worker needs only Redis + a writable disk.

1. Push this repo to GitHub, then in Render: **New → Blueprint** and select it.
2. The blueprint creates the worker plus a managed **Key Value (Redis-compatible)**
   instance and auto-injects `REDIS_URL`. With the DuckDB default you set **no
   secrets** — leave `DATABASE_URL` unset.
3. **Ephemeral disk on the free plan.** The DuckDB file lives on the container's
   disk, which is wiped on every redeploy/restart. That is fine for a live-streaming
   demo (the worker keeps appending fresh ticks/bars), but data is not durable. For
   durability either attach a Render **persistent disk**, or switch to Timescale.
4. **Optional — durable Timescale storage.** Render's managed Postgres lacks the
   TimescaleDB extension/hypertables this pipeline expects, so provision an
   **external TimescaleDB** ([Timescale Cloud](https://www.timescale.com/cloud)
   free tier or Aiven). Then in the Render dashboard set `STORAGE_BACKEND=timescale`
   and paste the connection string into the `DATABASE_URL` secret (marked
   `sync: false`, so Render prompts for it on deploy).
5. Adjust `EXCHANGE` (`binance`/`coinbase`/`kraken`/`bitstamp`), `WS_URL`, `SYMBOLS`, and
   `MAX_BUFFER_SIZE` env vars as needed.

The service is defined in the **root `render.yaml`** (one blueprint for the whole monorepo)
under the `market-data-pipeline` worker, with `rootDir: packages/market-data` and
`dockerfilePath: ./Dockerfile`. `autoDeploy` is off, so deploys are manual. There is **no
per-package `render.yaml`** — edit the root file.

### Behavior without live infra (Redis / a store)

A store is a **hard dependency** for ingestion (there is nowhere to cache or persist trades
without one), but with the DuckDB default it is just a local file — only **Redis** has to be
external. The worker does **not** silently no-op or hang; it **fails fast with one actionable
log line** and exits non-zero, so the failure is obvious in the Render logs and the worker
restarts (and retries) on schedule. On startup `Pipeline.start()`:

- Connects to Redis first. If `REDIS_URL` points at nothing, you get a single line like:

  ```
  ERROR | src.pipeline | Could not connect to Redis at redis://… (ConnectionError: …).
          Set REDIS_URL to a reachable instance and restart.
  ```

  and the worker exits — instead of dumping a ~20-frame redis/asyncio traceback.
- Then connects to the selected store. With `STORAGE_BACKEND=duckdb` a bad/unwritable
  `DUCKDB_PATH` produces a `DUCKDB_PATH`-fix line. With `STORAGE_BACKEND=timescale` it runs
  `init_schema()` (which calls `create_hypertable`, so a plain Postgres without the Timescale
  extension fails here) and an unreachable `DATABASE_URL` produces the analogous
  `Could not connect to/initialize TimescaleDB at … Set DATABASE_URL …` line, then exits.

This is by design: a market-data ingester with no store is a no-op, so booting "successfully"
into a state that drops every trade would be worse than failing loudly. Once it is connected,
the WebSocket layer is the resilient part — it auto-reconnects with exponential backoff and a
retry budget (see `websocket_client.py`), so transient stream drops do not kill the worker.

> **One-Redis demo:** with the `STORAGE_BACKEND=duckdb` default the only external piece the
> Blueprint needs is the free managed Key Value (Redis) it provisions itself — no external
> database. The DuckDB file is ephemeral on the free plan, so this is a runnable live-streaming
> demo rather than durable storage; bring an external TimescaleDB (set `STORAGE_BACKEND=timescale`
> + `DATABASE_URL`) or a persistent disk when you want the data to survive restarts.

## Development

```bash
pip install -r requirements.txt
ruff check .      # lint
mypy              # type-check (gradual)
pytest            # 173 tests, ~98% coverage; gate --cov-fail-under=85
```

The test suite uses in-memory fakes (`FakeWebSocket` / `FakeRedis` / `FakePool` in
`tests/conftest.py`) — **no live WebSocket, Redis, or TimescaleDB is ever touched**, so the full
suite runs offline in well under a second. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev
workflow, branch/commit conventions, and the PR checklist.

## License

MIT — see [`LICENSE`](LICENSE).
