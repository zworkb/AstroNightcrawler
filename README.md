# AstroNightcrawler

> **⚠️ Work in Progress** — Early development. Star map, path editor, and telescope capture are functional and tested with real hardware (LX200 OnStep + Canon 600D). Video rendering is not yet implemented.

A browser-based application for planning and executing imaging sequences with a remote-controlled telescope. Draw a path on an interactive star map, and the telescope follows it point by point, capturing images at each position. The resulting frames can be assembled into a video.

## What it does

- **Plan** imaging sequences by drawing spline paths on a live star map (Stellarium Web Engine)
- **Capture** images by controlling your telescope and camera via INDI protocol over the network
- **Export** sequences for EKOS/KStars as an alternative to direct INDI control
- **Render** captured frames into video (planned — separate app)

## Quick Start

```bash
git clone git@github.com:zworkb/AstroNightcrawler.git
cd AstroNightcrawler
make install    # Python venv + all dependencies
make run        # Start the app (auto-downloads sky data on first run)
```

Open `http://localhost:8090` in your browser.

### Stellarium Web Engine (optional, for star map)

The star map requires a pre-built Stellarium WASM binary. Either:

- Copy `static/stellarium/` from an existing installation, or
- Build from source: `./scripts/build_stellarium.sh` (requires Emscripten, ~10 min)

Without it, the app works but shows no star map.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
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

## Architecture

Two separate applications connected by a self-describing data format:

**Planner & Capture App** (this repository) — runs on the telescope control machine or any machine with network access to the INDI server
- Interactive star map with offline star catalogues (Stellarium Web Engine)
- Cubic Bézier spline path editor with configurable capture point spacing
- Pure-Python async INDI client with BLOB support (replaces PyIndi for reliable network operation)
- Capture controller with pause/resume/cancel, automatic retry, and safety abort
- NiceGUI web interface served via FastAPI/uvicorn

**Rendering App** (planned) — runs on a powerful workstation
- FITS → stretched PNG conversion (auto/manual)
- Video assembly at 24fps via ffmpeg

The two apps communicate through a directory of FITS files plus a JSON manifest.

## Technology

| Component | Technology |
|-----------|-----------|
| Web framework | NiceGUI on FastAPI/uvicorn |
| Star map | Stellarium Web Engine (C → WASM/WebGL) |
| Path editor | SVG overlay with stereographic projection |
| Telescope control | Pure-Python async INDI client (TCP/XML) |
| Coordinate conversion | astropy (Az/Alt ↔ RA/Dec J2000) |
| Data models | Pydantic |
| Image format | FITS (via astropy) |
| Configuration | pydantic-settings (.env) |
| Build system | mxmake (Makefile) |

## Development

```bash
make install         # Install everything
make run             # Start the app
make test            # Run tests (103 tests)
make check           # Linting (ruff)
make mypy            # Type checking
```

## Documentation

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
- **Images:** 5184×3456 px, 16-bit FITS (35 MB per frame)

## License

TBD
