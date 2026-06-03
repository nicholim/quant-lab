# Makefile — one-command developer experience for the quant monorepo.
#
# Clone the repo and run any package with a single command, no per-package
# incantations to memorize. Created by the DX tooling pass.
#
#   make            # show this help
#   make setup      # create .venv and install all 5 packages + dev deps
#   make test       # run all Python pytest suites + the C++ ctest
#   make run-options# launch the options Streamlit app  (etc.)
#
# Robust on macOS (the dev machine) and Linux/CI. Uses python3 and a single
# shared .venv at the repo root. Run targets cd into the right package and
# launch its existing entry point.

# Run all recipe lines of a target in one shell so `cd` + `&&` chains hold.
.ONESHELL:
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

# Repo root = directory of this Makefile (works regardless of caller CWD).
ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
VENV := $(ROOT)/.venv
VPY  := $(VENV)/bin/python
VBIN := $(VENV)/bin
PYTHON ?= python3

# Python packages, in install/test order. portfolio FIRST (backtesting installs
# it editable via -e ../portfolio-optimization).
PY_PACKAGES := packages/portfolio-optimization packages/backtesting packages/market-data packages/options-pricing
# Order-book has a separate Python test suite under cpp/order-book (testpaths=tests).
CPP_BUILD := $(ROOT)/cpp/order-book/build

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help — self-documenting: any target with a `## comment` is listed.
# ---------------------------------------------------------------------------
.PHONY: help
help: ## Show this help (default target)
	@echo "Quant monorepo — developer commands"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Guard: most targets need the venv. Echo a helpful error if it's missing.
.PHONY: _check-venv
_check-venv:
	@if [ ! -x "$(VPY)" ]; then \
		echo "ERROR: no virtualenv at $(VENV). Run 'make setup' first." >&2; \
		exit 1; \
	fi

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
.PHONY: setup
setup: ## Create shared .venv and install all 5 packages + dev deps (editable)
	@if ! command -v $(PYTHON) >/dev/null 2>&1; then \
		echo "ERROR: '$(PYTHON)' not found. Install Python 3.11+." >&2; exit 1; \
	fi
	PYTHON=$(PYTHON) bash "$(ROOT)/scripts/bootstrap.sh"

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
.PHONY: test
test: test-py test-cpp ## Run all Python pytest suites + the C++ ctest

.PHONY: test-py
test-py: _check-venv ## Run every Python package's pytest suite
	@for pkg in $(PY_PACKAGES); do \
		echo "==> pytest $$pkg"; \
		( cd "$(ROOT)/$$pkg" && "$(VPY)" -m pytest ) || exit $$?; \
	done
	@echo "==> pytest cpp/order-book (Python viz/simulator; fixture rebuilds C++ demo)"
	@( cd "$(ROOT)/cpp/order-book" && "$(VPY)" -m pytest ) || exit $$?

.PHONY: test-cpp
test-cpp: build-orderbook ## Build the C++ order book and run its ctest suite
	@echo "==> ctest cpp/order-book"
	ctest --test-dir "$(CPP_BUILD)" --output-on-failure

# ---------------------------------------------------------------------------
# Lint / format / typecheck — ruff + mypy, per-package (each has its own config).
# ---------------------------------------------------------------------------
.PHONY: lint
lint: _check-venv ## Ruff lint across all Python packages
	@for pkg in $(PY_PACKAGES) cpp/order-book; do \
		echo "==> ruff check $$pkg"; \
		( cd "$(ROOT)/$$pkg" && "$(VBIN)/ruff" check . ) || exit $$?; \
	done

.PHONY: format
format: _check-venv ## Auto-format all Python packages with ruff format
	@for pkg in $(PY_PACKAGES) cpp/order-book; do \
		echo "==> ruff format $$pkg"; \
		( cd "$(ROOT)/$$pkg" && "$(VBIN)/ruff" format . ) || exit $$?; \
	done

.PHONY: format-check
format-check: _check-venv ## Check formatting without writing (CI parity)
	@for pkg in $(PY_PACKAGES) cpp/order-book; do \
		echo "==> ruff format --check $$pkg"; \
		( cd "$(ROOT)/$$pkg" && "$(VBIN)/ruff" format --check . ) || exit $$?; \
	done

.PHONY: typecheck
typecheck: _check-venv ## Run mypy across all Python packages
	@for pkg in $(PY_PACKAGES); do \
		echo "==> mypy $$pkg"; \
		( cd "$(ROOT)/$$pkg" && "$(VBIN)/mypy" ) || exit $$?; \
	done
	@echo "==> mypy cpp/order-book (python)"
	@( cd "$(ROOT)/cpp/order-book" && "$(VBIN)/mypy" python ) || exit $$?

# ---------------------------------------------------------------------------
# C++ order book
# ---------------------------------------------------------------------------
.PHONY: build-orderbook
build-orderbook: ## CMake configure + build the C++ order book (Release)
	@if ! command -v cmake >/dev/null 2>&1; then \
		echo "ERROR: cmake not found. Install it (macOS: brew install cmake)." >&2; exit 1; \
	fi
	cmake -S "$(ROOT)/cpp/order-book" -B "$(CPP_BUILD)" -DCMAKE_BUILD_TYPE=Release
	cmake --build "$(CPP_BUILD)"

# ---------------------------------------------------------------------------
# Run targets — each launches the package's existing entry point.
# (Entry points per AGENTS.md: options app.py, backtesting dashboard.py,
#  portfolio api/app.py, market-data main.py, showcase apps/showcase-site.)
# ---------------------------------------------------------------------------
OPTIONS_PORT ?= 8501
BACKTEST_PORT ?= 8050
OPTIMIZER_PORT ?= 8000
OPTIMIZER_UI_PORT ?= 8502
MARKET_MONITOR_PORT ?= 8503

.PHONY: run-options
run-options: _check-venv ## Launch the options-pricing Streamlit app (port 8501)
	cd "$(ROOT)/packages/options-pricing" && \
		"$(VBIN)/streamlit" run app.py --server.port $(OPTIONS_PORT)

.PHONY: run-backtest
run-backtest: _check-venv ## Launch the backtesting Dash dashboard (port 8050)
	cd "$(ROOT)/packages/backtesting" && "$(VPY)" dashboard.py

.PHONY: run-optimizer-api
run-optimizer-api: _check-venv ## Launch the portfolio FastAPI demo via uvicorn (port 8000)
	cd "$(ROOT)/packages/portfolio-optimization" && \
		"$(VBIN)/uvicorn" api.app:app --host 0.0.0.0 --port $(OPTIMIZER_PORT) --reload

.PHONY: run-optimizer-ui
run-optimizer-ui: _check-venv ## Launch the portfolio optimizer Streamlit UI (port 8502)
	cd "$(ROOT)/packages/portfolio-optimization" && \
		"$(VBIN)/streamlit" run streamlit_app.py --server.port $(OPTIMIZER_UI_PORT)

.PHONY: run-market-data
run-market-data: _check-venv ## Run the market-data ingestion daemon (main.py)
	cd "$(ROOT)/packages/market-data" && "$(VPY)" main.py

.PHONY: run-market-monitor
run-market-monitor: _check-venv ## Launch the market-data live-monitor Streamlit UI (port 8503)
	cd "$(ROOT)/packages/market-data" && \
		"$(VBIN)/streamlit" run monitor.py --server.port $(MARKET_MONITOR_PORT)

.PHONY: run-showcase
run-showcase: ## Run the showcase site (vite dev server)
	@if ! command -v npm >/dev/null 2>&1; then \
		echo "ERROR: npm not found. Install Node 18+ to run the showcase." >&2; exit 1; \
	fi
	cd "$(ROOT)/apps/showcase-site" && \
		( [ -d node_modules ] || npm install ) && npm run dev

# ---------------------------------------------------------------------------
# Docker (convenience wrappers around the root docker-compose.yml)
# ---------------------------------------------------------------------------
.PHONY: docker-up
docker-up: ## Bring up the runnable stack with docker compose
	docker compose -f "$(ROOT)/docker-compose.yml" up --build

.PHONY: docker-down
docker-down: ## Tear down the docker compose stack (and volumes)
	docker compose -f "$(ROOT)/docker-compose.yml" down -v

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------
.PHONY: clean
clean: ## Remove venv, C++ build dir, and Python/test caches
	@echo ">> Removing .venv"
	rm -rf "$(VENV)"
	@echo ">> Removing C++ build dir"
	rm -rf "$(CPP_BUILD)"
	@echo ">> Removing Python caches"
	find "$(ROOT)" -type d \( -name '__pycache__' -o -name '.pytest_cache' \
		-o -name '.mypy_cache' -o -name '.ruff_cache' -o -name '*.egg-info' \) \
		-not -path '*/node_modules/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@echo ">> Removing coverage artifacts"
	find "$(ROOT)" -name '.coverage' -o -name 'coverage.xml' 2>/dev/null | xargs rm -f 2>/dev/null || true
	@echo "Clean done."
