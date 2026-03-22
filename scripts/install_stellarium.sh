#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="${PROJECT_DIR}/build/stellarium-web-engine"
STATIC_DIR="${PROJECT_DIR}/static/stellarium"
SKYDATA_DIR="${PROJECT_DIR}/static/skydata"
REPO_URL="https://github.com/nicegui-highcharts/stellarium-web-engine.git"

# ---------------------------------------------------------------------------
# 1. Check for Emscripten compiler
# ---------------------------------------------------------------------------
if ! command -v emcc &>/dev/null; then
    echo "ERROR: emcc (Emscripten) not found on PATH."
    echo "Install it via: https://emscripten.org/docs/getting_started/downloads.html"
    exit 1
fi

echo "Found emcc: $(command -v emcc)"

# ---------------------------------------------------------------------------
# 2. Clone or update the stellarium-web-engine repository
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$BUILD_DIR")"

if [ -d "$BUILD_DIR/.git" ]; then
    echo "Updating existing stellarium-web-engine checkout..."
    git -C "$BUILD_DIR" fetch --all
    git -C "$BUILD_DIR" reset --hard origin/master
else
    echo "Cloning stellarium-web-engine..."
    git clone --depth 1 "$REPO_URL" "$BUILD_DIR"
fi

# ---------------------------------------------------------------------------
# 3. Build WASM via make js-es6
# ---------------------------------------------------------------------------
echo "Building WASM (make js-es6)..."
cd "$BUILD_DIR"
make js-es6

# ---------------------------------------------------------------------------
# 4. Copy build artifacts to static/stellarium/
# ---------------------------------------------------------------------------
echo "Copying artifacts to ${STATIC_DIR}..."
mkdir -p "$STATIC_DIR"

cp -v "$BUILD_DIR/build/stellarium-web-engine.js" "$STATIC_DIR/" 2>/dev/null || true
cp -v "$BUILD_DIR/build/stellarium-web-engine.wasm" "$STATIC_DIR/" 2>/dev/null || true

# Also try the es6 build output location
cp -v "$BUILD_DIR/build/js-es6/"*.js "$STATIC_DIR/" 2>/dev/null || true
cp -v "$BUILD_DIR/build/js-es6/"*.wasm "$STATIC_DIR/" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 5. Create skydata directory structure
# ---------------------------------------------------------------------------
echo "Creating skydata directory structure..."
mkdir -p "$SKYDATA_DIR"/{stars,skycultures,surveys,planets}

echo "Done. Stellarium WASM artifacts are in: ${STATIC_DIR}"
echo "Sky data directory is ready at: ${SKYDATA_DIR}"
