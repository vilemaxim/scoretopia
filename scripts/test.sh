#!/bin/bash
# Scoretopia test script — runs the Python test suite.
# Run locally before pushing; also invoked by the TDD MCP server.
# Exit code 0 = all pass. Non-zero = failure.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x "$ROOT_DIR/.venv/bin/pytest" ]; then
  PYTEST="$ROOT_DIR/.venv/bin/pytest"
else
  PYTEST=pytest
fi

echo "================================================"
echo "  Scoretopia Tests"
echo "================================================"

echo ""
echo ">>> pytest"
"$PYTEST"
echo "    pytest: PASSED"

echo ""
echo "================================================"
echo "  All tests PASSED"
echo "================================================"
