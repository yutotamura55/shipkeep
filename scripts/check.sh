#!/usr/bin/env bash
# CI と同じ検査 (書式、lint、型検査、テストとカバレッジ) をすべて実行する。
set -euo pipefail
cd "$(dirname "$0")/.."
ruff format --check .
ruff check .
mypy
pytest -q --cov
