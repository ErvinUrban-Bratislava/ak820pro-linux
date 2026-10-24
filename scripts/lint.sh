#!/usr/bin/env bash
# Static Analysis Gate (v0.22.0, REVIEW.md §14.2 / ITERATIONS v0.22).
#
# Single source of truth for style + types: both this script and the CI `lint`
# job read [tool.ruff] / [tool.mypy] from pyproject.toml, so local and CI can
# never drift. Run from anywhere; exits non-zero on the first failing gate.
#
# Install once:  pip install ruff mypy "pillow>=9.0"
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

RUFF="${RUFF:-ruff}"
MYPY="${MYPY:-mypy}"
# Fall back to `python -m ...` when the console scripts are not on PATH.
command -v "$RUFF" >/dev/null 2>&1 || RUFF="python3 -m ruff"
command -v "$MYPY" >/dev/null 2>&1 || MYPY="python3 -m mypy"

echo "==> ruff check (ak820/ + ak820_ctrl.py)"
$RUFF check ak820/ ak820_ctrl.py

echo "==> ruff format --check (behavior-neutral formatting gate)"
$RUFF format --check ak820/ ak820_ctrl.py

echo "==> mypy (ak820/ package, config from pyproject.toml)"
$MYPY

echo "Static Analysis Gate: GREEN ✔"
