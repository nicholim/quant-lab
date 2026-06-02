# Contributing

Thanks for your interest in improving the Options Pricing Calculator. This is a small, focused
vanilla-options pricer — contributions that keep it readable and well-tested are very welcome.

## Dev setup

```bash
git clone https://github.com/nicholim/quant-lab.git
cd options-pricing-calculator

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt          # includes pytest + pytest-cov

# Optional dev tools (also run in CI)
pip install ruff mypy pre-commit
pre-commit install                        # auto-runs ruff/format on commit
```

Python 3.10+ is required (CI runs 3.10, 3.11, and 3.12).

## Running tests

```bash
pytest                                    # full suite + coverage gate (--cov-fail-under=95)
pytest tests/test_accuracy.py -v          # accuracy / correctness checks only
pytest -k parity                          # run a subset by keyword
```

The suite currently has **106 tests** at **~99% branch coverage**. New code should keep coverage
above the 95% gate. When adding a pricing feature, add a correctness check the way the existing
tests do — a textbook reference value, a parity identity, a convergence bound, or a round-trip.

## Linting and type-checking

```bash
ruff check .                              # lint (blocking in CI)
ruff format .                             # auto-format
mypy                                      # gradual typing (non-blocking in CI)
```

Config lives in `pyproject.toml`: ruff line-length 100, rule set `E,F,I,UP,B`; mypy with
`ignore_missing_imports`, targeting `src/`.

## Conventions

- **Pricing core stays pure.** Functions in `src/` take explicit parameters and return values — no
  global state, no I/O. UI/CLI concerns belong in `app.py` / `main.py`.
- **Match the existing signatures.** `(S, K, T, r, sigma, option_type="call", q=0.0)` ordering;
  theta is per calendar day, vega/rho are per 1% move. Keep these consistent.
- **Don't fabricate scope.** This library is vanilla European/American only. Do not advertise
  exotics, Heston, Monte Carlo, or curve bootstrapping it does not implement.

## Branches and commits

- Branch off `main`: `feature/<short-description>` or `fix/<short-description>`.
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`,
  `docs:`, `chore:`, `ci:`. Keep commits small and focused.

## Pull request checklist

- [ ] `pytest` passes and coverage stays ≥ 95%.
- [ ] `ruff check .` is clean and code is `ruff format`-ed.
- [ ] New pricing/Greek behavior has a correctness check (reference value, parity, convergence, or round-trip).
- [ ] README/docs updated if behavior or the public API changed.
- [ ] Commits follow Conventional Commits; no unrelated changes bundled in.
</content>
