#!/usr/bin/env bash
# Run every check that CI runs: formatting, lint, type checking, and tests.
set -euo pipefail
cd "$(dirname "$0")/.."
ruff format --check .
ruff check .
mypy
pytest -q
