#!/usr/bin/env bash
# =============================================================================
# Nightcrawler — Post-install setup (idempotent, safe to re-run)
#
# Python dependencies are managed via pyproject.toml.
# Install them first with: make install (or: uv pip install -e ".[dev]")
#
# This script handles everything else:
#   - .env configuration
#   - Output directory
#   - Stellarium / Skydata status check
#   - Smoke test
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${PROJECT_DIR}/.venv"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
ENV_FILE="${PROJECT_DIR}/.env"
OUTPUT_DIR="${PROJECT_DIR}/output"
STELLARIUM_DIR="${PROJECT_DIR}/static/stellarium"
SKYDATA_DIR="${PROJECT_DIR}/skydata"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }

cd "$PROJECT_DIR"

echo ""
echo "=== Nightcrawler Setup ==="
echo ""

ALL_OK=true

# ---- 1. Check that Python venv + deps are installed -------------------------
if [ ! -f "${VENV_DIR}/bin/python" ]; then
    fail "Virtual environment not found at .venv/"
    echo "    Run first: make install"
    exit 1
fi

PY="${VENV_DIR}/bin/python"

if "$PY" -c "import pydantic, fastapi, nicegui, astropy" 2>/dev/null; then
    ok "Python dependencies — installed"
else
    fail "Python dependencies missing"
    echo "    Run: make install"
    exit 1
fi

# ---- 2. .env file ------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    ok ".env configuration — exists"
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        ok ".env configuration — created from .env.example"
    else
        warn ".env.example not found, skipped"
        ALL_OK=false
    fi
fi

# ---- 3. Output directory -----------------------------------------------------
if [ -d "$OUTPUT_DIR" ]; then
    ok "Output directory — exists"
else
    mkdir -p "$OUTPUT_DIR"
    ok "Output directory — created"
fi

# ---- 4. Stellarium Web Engine ------------------------------------------------
if ls "${STELLARIUM_DIR}"/*.wasm 1>/dev/null 2>&1; then
    ok "Stellarium Web Engine — installed"
else
    warn "Stellarium Web Engine — not installed"
    echo "    Run: ./scripts/build_stellarium.sh"
    ALL_OK=false
fi

# ---- 5. Sky data (star catalogues) -------------------------------------------
if [ -d "${SKYDATA_DIR}/stars" ] && [ -d "${SKYDATA_DIR}/dso" ]; then
    ok "Sky data — installed ($(du -sh "$SKYDATA_DIR" | cut -f1))"
else
    warn "Sky data — not installed"
    echo "    Run: ./scripts/download_skydata.sh"
    ALL_OK=false
fi

# ---- 6. Smoke test -----------------------------------------------------------
echo ""
echo "  Running tests..."
TEST_OUTPUT=$("$PY" -m pytest tests/ -q --tb=line 2>&1 | grep -E "passed|failed" | tail -1)
if echo "$TEST_OUTPUT" | grep -q "passed"; then
    ok "Tests: ${TEST_OUTPUT}"
else
    fail "Tests: ${TEST_OUTPUT}"
    ALL_OK=false
fi

# ---- Summary -----------------------------------------------------------------
echo ""
echo "=== Setup Complete ==="
echo ""
if [ "$ALL_OK" = true ]; then
    echo -e "  ${GREEN}All good!${NC} Start with:"
else
    echo -e "  ${YELLOW}Setup done with warnings (see above).${NC} Start with:"
fi
echo ""
echo "    nightcrawler"
echo ""
