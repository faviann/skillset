#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if (( $# > 1 )) || [[ "${1:-stdlib}" != stdlib ]]; then
  printf 'Usage: tests/run.sh [stdlib]\n' >&2
  exit 2
fi

# stdlib is the only test group currently present.
exec python3 -m unittest discover -s tests -p 'test_*.py' -v
