#!/usr/bin/env bash
# =============================================================================
# Build Stellarium Web Engine from source (WASM + JS)
#
# This script is idempotent — safe to re-run. It will:
# 1. Install Emscripten SDK if not present
# 2. Clone/update stellarium-web-engine source
# 3. Build WASM via make js
# 4. Copy artifacts to static/stellarium/
#
# Requirements: git, python3, cmake (usually pre-installed)
# Time: ~10 min first run, ~2 min rebuild
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EMSDK_DIR="${EMSDK_DIR:-$HOME/emsdk}"
BUILD_DIR="${PROJECT_DIR}/build/stellarium-web-engine"
STATIC_DIR="${PROJECT_DIR}/static/stellarium"
REPO_URL="https://github.com/Stellarium/stellarium-web-engine.git"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
step() { echo -e "\n${GREEN}===${NC} $1 ${GREEN}===${NC}"; }

echo ""
echo "=== Stellarium Web Engine Build ==="

# ---- 1. Emscripten SDK -------------------------------------------------------
step "1/4 — Emscripten SDK"

if [ -f "${EMSDK_DIR}/emsdk" ]; then
    ok "emsdk found at ${EMSDK_DIR}"
else
    echo "  Cloning emsdk to ${EMSDK_DIR}..."
    git clone https://github.com/emscripten-core/emsdk.git "$EMSDK_DIR"
    ok "emsdk cloned"
fi

# Install latest if not already active
if command -v emcc &>/dev/null; then
    ok "emcc already available: $(emcc --version 2>&1 | head -1)"
else
    echo "  Installing latest Emscripten (this takes a few minutes)..."
    cd "$EMSDK_DIR"
    ./emsdk install latest
    ./emsdk activate latest
    ok "Emscripten installed"
fi

# Source emsdk environment
# shellcheck source=/dev/null
source "${EMSDK_DIR}/emsdk_env.sh" 2>/dev/null || true

if ! command -v emcc &>/dev/null; then
    fail "emcc not found after installation"
    echo "    Try: source ${EMSDK_DIR}/emsdk_env.sh"
    exit 1
fi
ok "emcc ready: $(emcc --version 2>&1 | head -1)"

# ---- 2. Stellarium Web Engine source -----------------------------------------
step "2/4 — Stellarium Web Engine source"

mkdir -p "$(dirname "$BUILD_DIR")"

if [ -d "${BUILD_DIR}/.git" ]; then
    echo "  Updating existing checkout..."
    git -C "$BUILD_DIR" fetch --all --quiet
    git -C "$BUILD_DIR" reset --hard origin/master --quiet
    ok "Source updated"
else
    echo "  Cloning stellarium-web-engine..."
    git clone --depth 1 "$REPO_URL" "$BUILD_DIR"
    ok "Source cloned"
fi

# ---- 3. Build WASM -----------------------------------------------------------
step "3/4 — Building WASM"

cd "$BUILD_DIR"

# Check if scons is available, install if not
if ! command -v scons &>/dev/null; then
    echo "  Installing scons..."
    pip3 install --quiet scons
fi

echo "  Building (this takes a few minutes on first run)..."
make js 2>&1 | tail -5

if [ $? -ne 0 ]; then
    fail "Build failed"
    exit 1
fi
ok "Build complete"

# ---- 4. Copy artifacts -------------------------------------------------------
step "4/4 — Installing artifacts"

mkdir -p "$STATIC_DIR"

# Find and copy the built files
FOUND=false
for dir in "build" "build/js" "build/js-es6" "html/static/js"; do
    if ls "${BUILD_DIR}/${dir}/"*stellarium-web-engine* 2>/dev/null; then
        cp -v "${BUILD_DIR}/${dir}/"*stellarium-web-engine*.js "$STATIC_DIR/" 2>/dev/null || true
        cp -v "${BUILD_DIR}/${dir}/"*stellarium-web-engine*.wasm "$STATIC_DIR/" 2>/dev/null || true
        FOUND=true
    fi
done

if [ "$FOUND" = false ]; then
    # Broader search
    echo "  Searching for build artifacts..."
    find "$BUILD_DIR" -name "*.wasm" -o -name "*stel*.js" 2>/dev/null | head -10
    warn "Could not find expected artifacts — check build output above"
    exit 1
fi

# Verify
JS_COUNT=$(ls "$STATIC_DIR"/*.js 2>/dev/null | wc -l)
WASM_COUNT=$(ls "$STATIC_DIR"/*.wasm 2>/dev/null | wc -l)

if [ "$JS_COUNT" -gt 0 ] && [ "$WASM_COUNT" -gt 0 ]; then
    ok "Artifacts installed to ${STATIC_DIR}/"
    echo ""
    echo "  Files:"
    ls -lh "$STATIC_DIR"/*.{js,wasm} 2>/dev/null | awk '{print "    " $9 " (" $5 ")"}'
else
    fail "Missing artifacts in ${STATIC_DIR}/"
    exit 1
fi

echo ""
echo -e "${GREEN}=== Build complete ===${NC}"
echo ""
echo "  Stellarium Web Engine is ready."
echo "  Restart the app to use the star map."
echo ""
