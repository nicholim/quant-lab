# Contributing

Thanks for your interest in improving the Real-Time Market Data Pipeline. This is a small,
single-purpose asyncio daemon; contributions that keep it focused and readable are very welcome.

## Dev setup

Requires **Python 3.11**.

```bash
git clone https://github.com/nicholim/quant-lab.git
cd market-data-pipeline

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Optional: install the pre-commit hooks (ruff + ruff-format + hygiene checks)
pip install pre-commit && pre-commit install
```

You do **not** need Redis, TimescaleDB, or a live exchange connection to develop or run the tests —
the test suite stubs all of them out (see below).

## Quality gates

Run all three before opening a PR. CI (`.github/workflows/ci.yml`) runs the same on push/PR to
`main`.

```bash
ruff check .      # lint (E, F, I, UP, B); config in pyproject.toml
mypy              # type-check; gradual, ignore_missing_imports (non-blocking in CI)
pytest            # 71 tests; coverage gated at --cov-fail-under=85 (currently ~99%)
```

- **ruff** is the source of truth for style (line length 100). Format with `ruff format .`.
- **mypy** runs in gradual mode and is currently non-blocking in CI, but please don't add new
  type errors.
- **pytest** uses `asyncio_mode = auto`, so `async def test_*` functions run without a decorator.

## Testing approach — mocks, not live services

All tests are **fully in-memory and offline**. There is no live WebSocket, Redis, or TimescaleDB
in the test path. The shared fakes live in `tests/conftest.py`:

- `FakeWebSocket` — async-iterable stand-in for a `websockets` connection; yields canned raw
  messages and records `close()`.
- `FakeRedis` — implements only the commands `RedisCache` uses (`ping`, `hset`, `hgetall`,
  `lpush`, `ltrim`, `lrange`, `publish`, `aclose`).
- `FakePool` / `FakeConnection` — async stand-ins for an `asyncpg` pool that record executed
  queries and args.

When you add a feature:

- Drive new behavior through these fakes; **do not** add tests that require network or a running
  database.
- If you touch a component's external surface (e.g. a new Redis command or SQL statement), extend
  the matching fake so the test exercises the real code path.
- Prefer asserting on observable effects (what was published, what args were passed to a query)
  over internal state.

## Branching & commits

- Branch off `main`; do your work on a feature branch (e.g. `feature/<topic>` or `fix/<topic>`).
  Do not commit directly to `main`.
- Use **Conventional Commits**: `feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`,
  `refactor:`. Keep commits small and focused.

## Pull request checklist

- [ ] `ruff check .` passes and code is `ruff format`-ed.
- [ ] `mypy` introduces no new errors.
- [ ] `pytest` passes and coverage stays at or above the gate.
- [ ] New/changed behavior is covered by tests that use the in-memory fakes (no live services).
- [ ] Docs updated if behavior, env vars, or commands changed (README config table, Quick Start).
- [ ] Commits follow Conventional Commits; PR description explains the change and any trade-offs.
