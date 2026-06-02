"""Shared pytest setup: put the python/ package dir on sys.path.

This lets the test suite `import orderbook` (the thin re-export of the compiled
pybind11 `_orderbook` extension) as well as the pure-Python `simulator` /
`visualizer` modules, regardless of where pytest is invoked from.
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON_DIR = os.path.join(PROJECT_ROOT, "python")

if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)
