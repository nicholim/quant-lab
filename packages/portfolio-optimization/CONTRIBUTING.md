# Contributing

Thanks for your interest in the Portfolio Optimization Engine. This is a small, readable Modern
Portfolio Theory codebase — contributions that keep it that way (clear, well-tested, dependency-light)
are very welcome.

## Dev setup

Python 3.10+ is required.

```bash
git clone https://github.com/nicholim/quant-lab.git
cd portfolio-optimization-engine

python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate

pip install -r requirements.txt
pip install -e ".[test]"            # editable install + test deps
pip install -r requirements-api.txt # only if you touch the FastAPI demo (api/)
```

Optional: install [`pre-commit`](https://pre-commit.com) so lint/format run on every commit.

```bash
pip install pre-commit && pre-commit install
```

## Running tests and lint

```bash
pytest                              # 147 tests; branch coverage gated at 90% (--cov-fail-under=90)
ruff check .                        # lint
ruff format --check .               # formatting
mypy portfolio_optimization_engine  # gradual typing (non-blocking)
```

The offline example doubles as a smoke test:

```bash
python examples/quickstart_offline.py
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy + pytest on Python 3.10/3.11/3.12 for every push
and PR to `main`. New code should keep coverage at or above the 90% gate.

## The public API contract (please read)

The sibling [`backtesting-framework`](../backtesting-framework) depends **one-way** on this package
(`OptimizationRebalanceStrategy`). Two things must not drift:

1. **`PortfolioOptimizer` injected-returns usage.** Callers build the optimizer, then set
   `.returns` / `.mean_returns` / `.cov_matrix` directly and call an `optimize_*` method (instead of
   `fetch_data()`). Keep these attribute names and the `optimize_*` / `PortfolioResult` signatures
   stable. The FastAPI demo (`api/app.py`) uses the same pattern.
2. **`metrics.py` definitions.** Sharpe/Sortino/drawdown/CAGR are a shared source of truth with the
   backtester. Changing a formula here changes results there — add/adjust the parity tests in
   `tests/test_optimizer_edge.py` and call it out in your PR.

If your change is purely additive (a new objective, a new constraint shape), prefer extending over
modifying the existing surface.

## Branch & commit conventions

- Branch off `main`: `feature/<short-name>`, `fix/<short-name>`, or `docs/<short-name>`.
- **Conventional Commits**: `feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`, `refactor:`. Keep
  commits small and focused.

## Pull request checklist

- [ ] `pytest` passes and coverage stays at or above 90%.
- [ ] `ruff check .` and `ruff format --check .` are clean.
- [ ] New behavior has tests; the public API contract above is preserved (or parity tests updated).
- [ ] README / docstrings updated if behavior or the CLI/API surface changed.
- [ ] Commits follow Conventional Commits.

## License

By contributing you agree your contributions are licensed under the project's [MIT License](LICENSE).
