#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if (( $# > 1 )) || [[ ! "${1:-all}" =~ ^(all|stdlib|selector)$ ]]; then
  printf 'Usage: tests/run.sh [stdlib|selector]\n' >&2
  exit 2
fi

if [[ "${1:-all}" != selector ]]; then
  PYTHONPATH=tests python3 -m unittest -v test_fixture test_catalog test_reconcile
fi

if [[ "${1:-all}" != stdlib ]]; then
  selector=scripts/select-skills.py
  uv sync --locked --script "$selector"
  "$(uv python find --script "$selector")" -m unittest discover -s tests -p 'test_selector*.py' -v
fi
