#!/usr/bin/env bash
# =============================================================================
# Nightcrawler — Setup Script (idempotent, safe to re-run)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="${PROJECT_DIR}/.venv"
ENV_EXAMPLE="${PROJECT_DIR}/.env.example"
ENV_FILE="${PROJECT_DIR}/.env"
OUTPUT_DIR="${PROJECT_DIR}/output"
STELLARIUM_DIR="${PROJECT_DIR}/static/stellarium"

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

# ---- Track overall status ---------------------------------------------------
ALL_OK=true

# ---- 1. Find Python ≥3.11 ---------------------------------------------------
PYTHON=""
for candidate in python3.12 python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        major=$("$candidate" -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
        minor=$("$candidate" -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo 0)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python ≥3.11 not found"
    echo "    Install Python 3.11+ and re-run this script."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON" --version 2>&1)
ok "Python: ${PYTHON_VERSION} ($(command -v "$PYTHON"))"

# ---- 2. Virtual environment --------------------------------------------------
if [ -f "${VENV_DIR}/bin/python" ]; then
    # Verify venv is functional
    if "${VENV_DIR}/bin/python" -c "import sys" 2>/dev/null; then
        ok "Virtual environment (.venv) — exists"
    else
        warn "Virtual environment (.venv) — broken, recreating"
        rm -rf "$VENV_DIR"
        "$PYTHON" -m venv "$VENV_DIR"
        ok "Virtual environment (.venv) — recreated"
    fi
else
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment (.venv) — created"
fi

PIP="${VENV_DIR}/bin/pip"
PY="${VENV_DIR}/bin/python"

# ---- 3. Check for uv (prefer uv over pip if available) ----------------------
USE_UV=false
if command -v uv &>/dev/null; then
    USE_UV=true
fi

install_deps() {
    local spec="$1"
    if [ "$USE_UV" = true ]; then
        uv pip install --python "$PY" $spec
    else
        "$PIP" install --quiet $spec
    fi
}

# ---- 4. Dependencies --------------------------------------------------------
DEPS_OK=true
"$PY" -c "import pydantic, fastapi, nicegui, astropy" 2>/dev/null || DEPS_OK=false

if [ "$DEPS_OK" = true ]; then
    ok "Dependencies — already installed"
else
    echo -n "  Installing dependencies"
    if [ "$USE_UV" = true ]; then
        echo " (via uv)..."
        install_deps '-e ".[dev]"'
    else
        echo " (via pip)..."
        install_deps '-e ".[dev]"'
    fi
    ok "Dependencies — installed"
fi

# Check dev tools separately
DEV_OK=true
"$PY" -c "import pytest, ruff, mypy" 2>/dev/null || DEV_OK=false
if [ "$DEV_OK" = false ]; then
    echo "  Installing dev tools..."
    install_deps '".[dev]"'
    ok "Dev tools — installed"
else
    ok "Dev tools (pytest, ruff, mypy) — available"
fi

# ---- 5. .env file ------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
    ok ".env configuration — exists (not overwritten)"
else
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        ok ".env configuration — created from .env.example"
    else
        warn ".env configuration — .env.example not found, skipped"
        ALL_OK=false
    fi
fi

# ---- 6. Output directory -----------------------------------------------------
if [ -d "$OUTPUT_DIR" ]; then
    ok "Output directory — exists"
else
    mkdir -p "$OUTPUT_DIR"
    ok "Output directory — created"
fi

# ---- 7. Stellarium Web Engine ------------------------------------------------
if ls "${STELLARIUM_DIR}"/*.wasm 1>/dev/null 2>&1; then
    ok "Stellarium Web Engine — installed"
else
    warn "Stellarium Web Engine — not installed (optional)"
    echo "    Run: ./scripts/install_stellarium.sh (requires Emscripten)"
    ALL_OK=false
fi

# ---- 8. INDI client (optional) -----------------------------------------------
INDI_OK=false
"$PY" -c "import PyIndi" 2>/dev/null && INDI_OK=true
if [ "$INDI_OK" = true ]; then
    ok "PyINDI client — installed"
else
    warn "PyINDI client — not installed (optional, using mock)"
    echo "    Install with: ${PIP} install pyindi-client"
fi

# ---- 9. Smoke test -----------------------------------------------------------
echo ""
echo "  Running tests..."
if "${VENV_DIR}/bin/python" -m pytest tests/ -q --tb=line 2>&1 | tail -1 | grep -q "passed"; then
    TEST_RESULT=$("${VENV_DIR}/bin/python" -m pytest tests/ -q --tb=line 2>&1 | tail -1)
    ok "Tests: ${TEST_RESULT}"
else
    TEST_RESULT=$("${VENV_DIR}/bin/python" -m pytest tests/ -q --tb=line 2>&1 | tail -3)
    fail "Tests: ${TEST_RESULT}"
    ALL_OK=false
fi

# ---- Summary -----------------------------------------------------------------
echo ""
echo "=== Setup Complete ==="
echo ""
if [ "$ALL_OK" = true ]; then
    echo -e "  ${GREEN}All good!${NC} Start the app with:"
else
    echo -e "  ${YELLOW}Setup done with warnings (see above).${NC} Start with:"
fi
echo ""
echo "    source .venv/bin/activate"
echo "    nightcrawler"
echo ""
echo "  Or without activation:"
echo ""
echo "    .venv/bin/python -m src.main"
echo ""
