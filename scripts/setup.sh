#!/usr/bin/env bash
# =============================================================================
# Nightcrawler — Post-install check (idempotent, safe to re-run)
#
# Python + dependencies are handled by: make install
# This script only handles non-Python setup and status checks.
# =============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
ENV_FILE="${PROJECT_DIR}/.env"
STELLARIUM_DIR="${PROJECT_DIR}/static/stellarium"
SKYDATA_DIR="${PROJECT_DIR}/skydata"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }

cd "$PROJECT_DIR"

echo ""
echo "=== Nightcrawler Status ==="
echo ""

# ---- .env file ---------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    ok ".env — exists"
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        ok ".env — created from .env.example (edit to configure)"
    else
        warn ".env.example not found"
    fi
fi

# ---- Stellarium Web Engine ---------------------------------------------------
if ls "${STELLARIUM_DIR}"/*.wasm 1>/dev/null 2>&1; then
    ok "Stellarium WASM — installed"
else
    warn "Stellarium WASM — not found (run: ./scripts/build_stellarium.sh)"
fi

# ---- Sky data ----------------------------------------------------------------
if [ -d "${SKYDATA_DIR}/stars" ] && [ -d "${SKYDATA_DIR}/dso" ]; then
    ok "Sky data — $(du -sh "$SKYDATA_DIR" | cut -f1)"
else
    warn "Sky data — not found (run: ./scripts/download_skydata.sh)"
fi

echo ""
echo "  To install:  make install"
echo "  To run:      make run"
echo ""
