# Contributing

Thanks for your interest in improving the Quantitative Backtesting Framework. This guide
covers local setup, the checks CI runs, and the conventions we follow.

## Development setup

This framework has a **one-way dependency** on the sibling
[portfolio-optimization-engine](../portfolio-optimization-engine) (installed editable via
`requirements.txt`). Clone both repos side by side:

```bash
git clone https://github.com/nicholim/quant-lab.git
git clone https://github.com/nicholim/quant-lab.git
cd backtesting-framework

python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt     # pulls in ../portfolio-optimization-engine (editable)
pip install ruff mypy               # dev tools (not pinned in requirements.txt)
```

Verify the install:

```bash
python main.py                      # runs the example CLI strategies
pytest -q                           # should report 103 passed
```

## Running the checks

CI (`.github/workflows/ci.yml`) runs lint, format check, type check, and tests on Python
3.10 / 3.11 / 3.12. Reproduce locally:

```bash
ruff check .            # lint (blocking in CI)
ruff format --check .   # formatting (non-blocking in CI)
mypy                    # type check (gradual; non-blocking in CI)
pytest -q               # tests with coverage gate (--cov-fail-under=80)
```

Configuration lives in `pyproject.toml`:

- **ruff**: line length 100, rule sets `E, F, I, UP, B` (`E501` deferred to the formatter).
- **mypy**: Python 3.10 target, `ignore_missing_imports`, checks `src/` only (gradual typing —
  there is known pre-existing type debt; don't introduce new errors).
- **pytest**: branch coverage of `src/` with an 80% gate (`src/interactive.py` is omitted; the
  UI layer is exercised by hand).

### Pre-commit

Hooks are configured in `.pre-commit-config.yaml` (ruff, ruff-format, whitespace/EOF/YAML/TOML
checks; mypy at the `manual` stage):

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Benchmarks

`benchmarks/throughput.py` measures event-loop throughput on synthetic data (no network):

```bash
python benchmarks/throughput.py --bars 2520 --symbols 5 --repeat 3
```

If you change the event loop, execution, or sizing, run it before and after and note the
delta in your PR.

## Conventions

- **Branches**: feature work goes on a branch off `main` (e.g. `feature/<topic>`,
  `fix/<topic>`). Don't commit directly to `main`.
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`, `refactor:`. Keep them small and focused.
- **Cross-repo contract**: Sharpe/Sortino/drawdown must come from the engine's `metrics`
  module so this backtester and the optimizer report identical numbers. Don't fork those
  formulas, and don't make the engine depend on this repo (the dependency is one-way).
- **Realism invariants**: orders fill at the *next* bar's open (never the signal bar), and the
  portfolio is long-only by default. Preserve these unless a change is explicitly opt-in.

## Pull request checklist

- [ ] Branched off `main`; commits follow Conventional Commits.
- [ ] `ruff check .` is clean; `ruff format` applied.
- [ ] No *new* mypy errors introduced.
- [ ] `pytest -q` passes and coverage stays at/above the gate.
- [ ] New behavior has tests (and a benchmark note if it touches the hot loop).
- [ ] README/docstrings updated if public behavior changed.
- [ ] The cross-repo metrics contract and next-open fill semantics are preserved.
