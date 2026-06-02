# Contributing

Thanks for your interest in improving the Order Book Simulator. This repo has two parts — a **C++17
matching-engine core** and a **Python visualization/simulation layer** — and each has its own
tooling. Please run both relevant pipelines before opening a PR.

## Prerequisites

- C++17 compiler (clang or gcc) and **CMake ≥ 3.16**
- **Python 3.10+**
- Optional but recommended: `clang-format`, `ruff`, `mypy`, and `pre-commit`

## Project layout

- `include/`, `src/` — C++ core (`OrderBook`, `MatchingEngine`) and the `main.cpp` demo
- `tests/test_order_book.cpp` — GoogleTest unit tests (run via `ctest`)
- `python/` — `visualizer.py` (matplotlib) and `simulator.py` (order-flow generator)
- `tests/test_orderbook.py`, `tests/test_python_viz.py` — pytest suite

## C++ workflow

### Build

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

GoogleTest is fetched automatically via CMake `FetchContent` on the first configure — no system
install required.

### Test (GoogleTest via ctest)

```bash
ctest --test-dir build --output-on-failure
```

All 35 tests must pass. New engine behavior **must** come with a test in
`tests/test_order_book.cpp` (registered automatically through `gtest_discover_tests`). Cover the
matching invariants you touch: price-time priority, partial fills, market vs. limit, cancel/modify,
crossing the spread, and empty-book edge cases.

### Format (clang-format)

Style is defined in `.clang-format` (LLVM-based, C++17, 4-space indent, 100-column limit).

```bash
clang-format -i include/*.h src/*.cpp tests/*.cpp   # apply
clang-format --dry-run --Werror include/*.h src/*.cpp tests/*.cpp  # check
```

## Python workflow

```bash
cd python && pip install -r requirements.txt && cd ..

ruff check python tests        # lint
ruff format python tests       # format
mypy python                    # type-check (gradual; ignore_missing_imports)
pytest                         # tests + coverage (--cov-fail-under=80)
```

Tool config lives in `pyproject.toml` (ruff: line-length 100, select `E,F,I,UP,B`; mypy targets
`python/`). The pytest suite builds the C++ demo in a fixture, so a working C++ toolchain is
required to run `pytest`.

## pre-commit (optional, recommended)

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

This runs trailing-whitespace/EOF/YAML checks plus ruff, ruff-format, and clang-format.

## Branching & commits

- Branch off `main`; do not commit directly to `main`. Use descriptive names, e.g.
  `feature/stop-orders`, `fix/cancel-index`.
- Use **Conventional Commits**: `feat:`, `fix:`, `test:`, `ci:`, `docs:`, `chore:`, `refactor:`.
  Keep commits small and focused.

## Pull request checklist

- [ ] `cmake --build build` succeeds and `ctest --test-dir build` is green (35/35+).
- [ ] New/changed engine behavior has a GoogleTest case.
- [ ] `ruff check python tests` and `mypy python` are clean; `pytest` passes (coverage ≥ 80%).
- [ ] C++ is clang-formatted; Python is ruff-formatted.
- [ ] Docs updated if behavior or the public API changed (README + this file).
- [ ] Commits follow Conventional Commits; PR description explains the change and rationale.

## CI

`.github/workflows/ci.yml` runs two jobs on push/PR to `main`: a **C++** job (CMake configure +
build + ctest) and a **Python** job (ruff + mypy + pytest on 3.11 and 3.12). PRs should be green on
both before merge.
</content>
