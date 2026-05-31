# AstroNightcrawler

> **Status:** Planner, capture, and rendering are functional. Tested with real hardware (LX200 OnStep + Canon 600D) and rendered video output with star-aligned linear pan and crossfade transitions.

A browser-based application for planning and executing imaging sequences with a remote-controlled telescope. Draw a path on an interactive star map, and the telescope follows it point by point, capturing images at each position. A companion rendering app converts the captured FITS frames into video.

## What it does

- **Plan** imaging sequences by drawing spline paths on a live star map (Stellarium Web Engine)
- **Capture** images by controlling your telescope and camera via INDI protocol over the network
- **Export** sequences for EKOS/KStars as an alternative to direct INDI control
- **Render** captured frames into video with debayering, stretch, star alignment, and transitions

## Installation

Three install profiles match the three deployment scenarios. The capture
machine (typically a Raspberry Pi) does not need the render-only packages
(`scipy`, `astroalign`, `colour-demosaicing`, `Pillow`), so it can skip them.

### Capture machine (Raspberry Pi)

The capture and planner UI runs on the machine that controls the
telescope. It needs the core dependencies only.

```bash
UV_PYTHON=3.13 make install-capture
```

The planner UI is then available at `http://<raspi-ip>:8090`.

### Rendering workstation

The renderer runs on a workstation. It needs the render extras
(scipy, astroalign, colour-demosaicing, Pillow). Free-threaded
Python 3.13t is recommended to escape the GIL during alignment.

```bash
make install-render
PYTHON_GIL=0 nightcrawler-render --ui
```

### Development

For development and CI, install everything (core + render extras + the
QA toolchain — ruff, mypy, pytest, etc.):

```bash
make install
```

## Python Build (Free-Threaded vs Regular)

The default `UV_PYTHON` is `3.13t` (free-threaded), which the parallel
renderer relies on (`PYTHON_GIL=0`, see #117). On machines where
free-threading is problematic (e.g. Raspberry Pi without a manually
compiled libxml), override per machine in your shell:

```bash
# ~/.bashrc on the Pi:
export UV_PYTHON=3.13
```

`uv`'s resolution order is `UV_PYTHON` env var > `.python-version` file >
`pyproject.toml`, so this override wins over the committed
`.python-version=3.13t`.

The Makefile auto-detects venv/build mismatches and rebuilds when needed
— but **only** when the build differs. Repeated `make install-*` and
`make run-*` calls with a consistent `UV_PYTHON` do NOT trigger
rebuilds. `make run-capture` works with either build; `make run-render`
requires the free-threaded build.

### Repair when the venv has the wrong Python

The self-heal (#150) catches this automatically on the next
`make install-*` / `make run-*`. If you want to force it manually:

```bash
rm -rf .venv .mxmake/sentinels/mxenv.sentinel
make install-render   # or install-capture
```

## Quick Start

### Planner & Capture

```bash
git clone git@github.com:zworkb/AstroNightcrawler.git
cd AstroNightcrawler
make install    # Python venv + all dependencies (see Installation above)
make run        # Start the planner app (auto-downloads sky data on first run)
```

Open `http://localhost:8090` in your browser.

### Renderer

```bash
# CLI mode — render a capture directory to video
nightcrawler-render --input ./output --output video.mp4 --transition linear-pan

# Web UI mode — browser-based preview and rendering
nightcrawler-render --ui
```

The renderer reads the `manifest.json` written during capture. Each captured frame is debayered, stretched to 8-bit sRGB, optionally aligned via star triangles, and assembled into video with transitions.

### Stellarium Web Engine (optional, for star map)

The star map requires a pre-built Stellarium WASM binary. Either:

- Copy `static/stellarium/` from an existing installation, or
- Build from source: `./scripts/build_stellarium.sh` (requires Emscripten, ~10 min)

Without it, the planner works but shows no star map.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- ffmpeg (for video rendering)
- Optional: INDI server + telescope/camera for actual capture

## Configuration

All settings via environment variables or `.env` file (created automatically from `.env.example` on first `make run`):

| Variable | Default | Description |
|----------|---------|-------------|
| `NC_HOST` | `0.0.0.0` | Server bind address |
| `NC_PORT` | `8090` | Server port |
| `NC_OUTPUT_DIR` | `./output` | Capture output directory |
| `NC_INDI_HOST` | `localhost` | INDI server hostname |
| `NC_INDI_PORT` | `7624` | INDI server port |
| `NC_OBSERVER_LAT` | `48.2` | Observer latitude (degrees) |
| `NC_OBSERVER_LON` | `16.4` | Observer longitude (degrees) |
| `NC_SETTLE_DELAY` | `3.0` | Seconds to wait after slew before capture |
| `NC_SLEW_TIMEOUT` | `120.0` | Max seconds to wait for slew completion |
| `NC_UNPARK_DELAY` | `3.0` | Seconds to wait after unpark |
| `NC_RENDER_FPS` | `24` | Video frames per second |
| `NC_RENDER_CRF` | `18` | H.264 quality (lower = better) |
| `NC_RENDER_TRANSITION` | `crossfade` | Transition type: none, crossfade, linear-pan |
| `NC_RENDER_CROSSFADE_FRAMES` | `24` | Interpolated frames per transition |
| `NC_RENDER_RESOLUTION` | `native` | Output resolution: native, 4k, 1440p, 1080p, 720p |
| `NC_RENDER_WORKERS` | `4` | Parallel workers for alignment + stretch. `-1` = all CPU cores. Overridden by GUI / `--workers` CLI flag. |

### Render parallelism

Alignment (and, with #118, stretching) runs in a thread pool. The worker
count is configurable from four places, in priority order: the **Workers**
field in the render UI's *Advanced Settings* > the `--workers N` (alias
`--render-workers`) CLI flag > the `NC_RENDER_WORKERS` env var > the
default of `4`. Use `-1` to use every available CPU core. Memory rule of
thumb: ~50 MB/worker during alignment and ~78 MB/worker during stretch
(at 4168x6224 uint16); the default of 4 is a comfortable laptop baseline.

## Sky Path Cinematography

This project pursues a niche we call **Sky Path Cinematography** (project
nickname: *Nightcrawling*) — drawing a continuous spline on a real star map,
having a motorized telescope walk it point by point, and assembling the
captured frames into a star-aligned pan video. As of 2026 a web survey found
no other tool that implements this full chain: existing mosaic planners
(EKOS, N.I.N.A., SharpCap, ASIAIR) stitch tiles to a still image, and
existing pan-video tools (Aladin Tour Navigator, WorldWide Telescope,
Stellarium scripting) move through synthetic survey data, not your own
captures. See [docs/related-work.md](docs/related-work.md) for the full
comparison and prior art.

## Architecture

Two separate applications connected by a self-describing data format:

**Planner & Capture App** (`nightcrawler`) — runs on the telescope control machine or any machine with network access to the INDI server
- Interactive star map with offline star catalogues (Stellarium Web Engine)
- Cubic Bezier spline path editor with configurable capture point spacing
- Pure-Python async INDI client with BLOB support (replaces PyIndi for reliable network operation)
- Capture controller with pause/resume/cancel, automatic retry, and safety abort
- NiceGUI web interface served via FastAPI/uvicorn

**Rendering App** (`nightcrawler-render`) — runs on a powerful workstation
- FITS import via manifest.json (shared Project model)
- Bayer demosaicing via colour-demosaicing (auto-detects pattern from FITS header)
- Tone mapping: ZScale+Asinh (auto), histogram percentile, or manual black/white/midtone
- Star-based frame alignment via astroalign (with optional downsampling for speed)
- Transitions: crossfade blending or linear-pan with sub-pixel shifting (scipy)
- Resolution presets: native, 4K, 1440p, 1080p, 720p
- H.264 video encoding via ffmpeg
- Both CLI and web UI interfaces

The two apps communicate through a directory of FITS files plus a JSON manifest (`manifest.json`), which is the Project model serialized after capture.

## Technology

| Component | Technology |
|-----------|-----------|
| Web framework | NiceGUI on FastAPI/uvicorn |
| Star map | Stellarium Web Engine (C -> WASM/WebGL) |
| Path editor | SVG overlay with stereographic projection |
| Telescope control | Pure-Python async INDI client (TCP/XML) |
| Coordinate conversion | astropy (Az/Alt <-> RA/Dec J2000) |
| Data models | Pydantic |
| Image format | FITS (via astropy) |
| Demosaicing | colour-demosaicing (bilinear CFA interpolation) |
| Star alignment | astroalign (triangle matching) |
| Image processing | numpy, scipy, Pillow |
| Tone mapping | astropy (ZScale, AsinhStretch) |
| Video encoding | ffmpeg (H.264, libx264) |
| Configuration | pydantic-settings (.env) |
| Build system | mxmake (Makefile) |

## Development

```bash
make install         # Install everything
make run             # Start the planner app
make test            # Run tests
make check           # Linting (ruff)
make mypy            # Type checking
```

### Catalog Update

The sky catalog used by the renderer's Catalog-mode label tool
(`data/catalog.csv`, Messier + Caldwell + OpenNGC + IAU named stars)
is committed to the repo. End-users never need to regenerate it.
Maintainers can refresh it from upstream ~1-2x/year when OpenNGC or
the IAU named-star list publishes new entries:

```bash
make build-catalog   # Downloads sources + writes data/catalog.csv (~15k rows)
git diff data/catalog.csv  # review what changed
```

Requires internet (downloads from raw.githubusercontent.com/mattiaverga/OpenNGC
and pas.rochester.edu/~emamajek/WGSN/).

#### Optional add-on catalogs

For deeper coverage, three opt-in tiers can be fetched from VizieR.
The output files (`data/catalog_tier{1,2,3}.csv`) are **not** committed —
each user fetches the tier(s) they need. `load_catalog()` picks them up
automatically once present.

```bash
make tier-1   # Sharpless 2 + Barnard dark nebulae + Arp Peculiar Galaxies (~1.3k rows, ~70 KB)
make tier-2   # (placeholder — vdB / Collinder / HCG sources still to add)
make tier-3   # UGC galaxies (~13k rows, ~680 KB)
```

To uninstall a tier just delete its CSV: `rm data/catalog_tier3.csv`.

## Documentation

- [Sky Path Cinematography — Genre & Prior Art](docs/related-work.md)
- [Design Specification](docs/superpowers/specs/2026-03-22-nightcrawler-design.md)
- [Async INDI Client](docs/async-indi-client.md)
- [Architecture (UML)](docs/architecture.md)
- [Dependencies](docs/dependencies.md)
- [Stellarium Build Guide](docs/stellarium-build.md)
- [Project Board](https://github.com/users/zworkb/projects/2)

## Tested Hardware

- **Telescope:** LX200 OnStep (German Equatorial Mount)
- **Camera:** Canon DSLR EOS 600D via gphoto2/INDI
- **Server:** INDI on Raspberry Pi (StellarMate), remote network access
- **Images:** 5184x3456 px, 16-bit FITS (35 MB per frame)
- **Rendered output:** H.264 video with linear-pan transitions (star-aligned sub-pixel panning)

## License

TBD
