#!/bin/bash
# Scoretopia lint script — runs linters across the Python codebase.
# Run locally before pushing; also invoked by the TDD MCP server.
# Exit code 0 = all clean. Non-zero = failure.

set -e  # Exit immediately on any error

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/.venv/bin/ruff" ]; then
  RUFF="$ROOT_DIR/.venv/bin/ruff"
else
  RUFF=ruff
fi

echo "================================================"
echo "  Scoretopia Lint"
echo "================================================"

echo ""
echo ">>> [1/1] ruff check"
"$RUFF" check .
echo "    ruff check: PASSED"

echo ""
echo "================================================"
echo "  All lint checks PASSED"
echo "================================================"
