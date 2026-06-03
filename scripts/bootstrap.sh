#!/usr/bin/env bash
# bootstrap.sh — one-shot dev environment setup for the quant monorepo.
#
# Creates a single shared virtualenv at <repo-root>/.venv and installs all
# Python packages (editable) + their dev tooling into it. Invoked by `make setup`,
# but also runnable directly: `./scripts/bootstrap.sh`.
#
# Created by the DX tooling pass (Makefile / docker-compose / devcontainer).
# Safe to re-run (idempotent: reuses an existing .venv).
set -euo pipefail

# Resolve the repo root relative to this script so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

PYTHON="${PYTHON:-python3}"
VENV="${ROOT}/.venv"

if ! command -v "${PYTHON}" >/dev/null 2>&1; then
  echo "ERROR: '${PYTHON}' not found on PATH. Install Python 3.11+ first." >&2
  exit 1
fi

echo ">> Using $(${PYTHON} --version) at $(command -v ${PYTHON})"

if [ ! -d "${VENV}" ]; then
  echo ">> Creating shared venv at ${VENV}"
  "${PYTHON}" -m venv "${VENV}"
else
  echo ">> Reusing existing venv at ${VENV}"
fi

# Use the venv's interpreter directly (no need to 'activate' in a script).
VPY="${VENV}/bin/python"

echo ">> Upgrading pip / wheel"
"${VPY}" -m pip install --upgrade pip wheel >/dev/null

# --- Install order matters: portfolio FIRST, because backtesting's
#     requirements.txt installs it via `-e ../portfolio-optimization`. ---
echo ">> Installing portfolio-optimization (+ API extras, editable)"
"${VPY}" -m pip install -r "${ROOT}/packages/portfolio-optimization/requirements-api.txt"
"${VPY}" -m pip install -e "${ROOT}/packages/portfolio-optimization"

echo ">> Installing backtesting (pulls in -e ../portfolio-optimization)"
( cd "${ROOT}/packages/backtesting" && "${VPY}" -m pip install -r requirements.txt )

echo ">> Installing market-data"
"${VPY}" -m pip install -r "${ROOT}/packages/market-data/requirements.txt"

echo ">> Installing options-pricing"
"${VPY}" -m pip install -r "${ROOT}/packages/options-pricing/requirements.txt"

echo ">> Installing order-book Python (simulator/visualizer) deps"
"${VPY}" -m pip install -r "${ROOT}/cpp/order-book/python/requirements.txt"

echo ">> Installing shared dev tooling (ruff, mypy)"
"${VPY}" -m pip install ruff mypy

echo ""
echo "Setup complete. Activate with:  source .venv/bin/activate"
echo "Or just use the Make targets (they call .venv automatically)."
