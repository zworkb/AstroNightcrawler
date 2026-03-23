#!/usr/bin/env bash
# Download Stellarium Web sky data for offline use.
#
# Source: stellarium-web.org (public CDN)
# Size: ~5 MB
#
# Usage:
#   ./scripts/download_skydata.sh
#
# The data is saved to skydata/ in the project root.
# This only needs to be run once per installation.

set -euo pipefail

BASE_URL="https://stellarium-web.org/skydata"
DEST="$(cd "$(dirname "$0")/.." && pwd)/skydata"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "  ${GREEN}✓${NC} $1"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $1"; }

if [ -d "$DEST/stars" ] && [ -d "$DEST/dso" ]; then
    echo "skydata/ already exists. Delete it first to re-download."
    exit 0
fi

echo "=== Downloading Stellarium Sky Data ==="
echo "Source: $BASE_URL"
echo "Destination: $DEST"
echo ""

mkdir -p "$DEST"

# Helper: download a file, creating parent dirs as needed
dl() {
    local path="$1"
    local dir="$DEST/$(dirname "$path")"
    mkdir -p "$dir"
    curl -sfL "$BASE_URL/$path" -o "$DEST/$path" 2>/dev/null || warn "Failed: $path"
}

# Stars catalogue (HiPS tiles, Norder 0 + 1)
echo "  Downloading star catalogues..."
dl "stars/properties"
for i in $(seq 0 11); do
    dl "stars/Norder0/Dir0/Npix${i}.eph"
done
for i in $(seq 0 47); do
    dl "stars/Norder1/Dir0/Npix${i}.eph"
done
info "Stars"

# Deep sky objects
echo "  Downloading DSO catalogue..."
dl "dso/properties"
for i in $(seq 0 11); do
    dl "dso/Norder0/Dir0/Npix${i}.eph"
done
info "DSO"

# Sky cultures (western)
echo "  Downloading sky cultures..."
dl "skycultures/western/index.json"
dl "skycultures/western/description.md"
dl "skycultures/western/description.en.utf8"
# Constellation illustrations
for f in $(curl -sfL "$BASE_URL/skycultures/western/index.json" 2>/dev/null | \
    python3 -c "import json,sys; d=json.load(sys.stdin); [print(f) for f in d.get('illustrations_files', [])]" 2>/dev/null); do
    dl "skycultures/western/$f"
done
info "Sky cultures"

# Milky way survey
echo "  Downloading milky way..."
dl "surveys/milkyway/properties"
dl "surveys/milkyway/Norder0/Allsky.webp"
for i in $(seq 0 11); do
    dl "surveys/milkyway/Norder0/Dir0/Npix${i}.webp"
done
info "Milky way"

# Landscape
echo "  Downloading landscape..."
dl "landscapes/guereins/properties"
dl "landscapes/guereins/Norder0/Allsky.webp"
for i in $(seq 0 11); do
    dl "landscapes/guereins/Norder0/Dir0/Npix${i}.webp"
done
info "Landscape"

# Solar system (moon, sun)
echo "  Downloading solar system..."
for body in moon sun; do
    dl "surveys/sso/${body}/properties" 2>/dev/null
    dl "surveys/sso/${body}/Norder0/Allsky.webp" 2>/dev/null
    for i in $(seq 0 11); do
        dl "surveys/sso/${body}/Norder0/Dir0/Npix${i}.webp" 2>/dev/null
    done
done
info "Solar system"

# Minor bodies (optional)
dl "CometEls.txt" 2>/dev/null
dl "mpcorb.dat" 2>/dev/null
dl "tle_satellite.jsonl.gz" 2>/dev/null

echo ""
echo "=== Sky Data Download Complete ==="
du -sh "$DEST"
