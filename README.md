# Sequence Planner

A browser-based application for planning and executing imaging sequences with a remote-controlled telescope. Draw a path on an interactive star map, and the telescope follows it point by point, capturing images at each position. The resulting frames can be assembled into a video.

## What it does

- **Plan** imaging sequences by drawing spline paths on a live star map (Stellarium Web Engine)
- **Control** your telescope and camera via the INDI protocol to execute the planned sequence
- **Export** sequences for EKOS/KStars as an alternative to direct INDI control
- **Render** captured frames into video (separate app, planned)

## Architecture

Two separate applications connected by a self-describing data format:

**Planner & Capture App** (this repository) — runs on the telescope control machine (Raspberry Pi / StellarMate or desktop)
- Interactive star map with offline star catalogs (Gaia/Hipparcos, mag ≤7)
- Cubic Bézier spline path editor with configurable capture point spacing
- Capture controller with pause/resume/cancel, automatic retry, and safety abort
- NiceGUI web interface served via FastAPI/uvicorn

**Rendering App** (planned) — runs on a powerful workstation
- FITS → stretched PNG conversion (auto/manual)
- Video assembly at 24fps via ffmpeg
- CLI and web interface

The two apps communicate through a directory of FITS files plus a JSON manifest describing the sequence, path, and capture metadata.

## Quick Start

```bash
git clone <repo-url>
cd sequence-planner
./scripts/setup.sh           # Python venv + dependencies + smoke tests
./scripts/build_stellarium.sh # Build Stellarium Web Engine (requires Emscripten, ~10 min)
sequence-planner              # Start the app (or: .venv/bin/python -m src.main)
```

Open `http://localhost:8090` in your browser.

## Requirements

- Python 3.11+
- Git (for Stellarium build)
- Emscripten SDK (installed automatically by `build_stellarium.sh`)
- Optional: INDI server + telescope/camera for actual capture

## Configuration

All settings via environment variables or `.env` file (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SEQ_HOST` | `0.0.0.0` | Server bind address |
| `SEQ_PORT` | `8090` | Server port |
| `SEQ_OUTPUT_DIR` | `./output` | Capture output directory |
| `SEQ_INDI_HOST` | `localhost` | INDI server hostname |
| `SEQ_INDI_PORT` | `7624` | INDI server port |

## Technology

| Component | Technology |
|-----------|-----------|
| Web framework | NiceGUI on FastAPI/uvicorn |
| Star map | Stellarium Web Engine (C → WASM/WebGL) |
| Path editor | SVG overlay with stereographic projection |
| Telescope control | INDI protocol (PyINDI / mock) |
| Data models | Pydantic |
| Image format | FITS |
| Configuration | pydantic-settings (.env) |

## Project Status

Early development. The planner UI with star map, path drawing, and capture point visualization is functional. Telescope capture and rendering are implemented but not yet tested with real hardware.

## Documentation

- [Design Specification](docs/superpowers/specs/2026-03-22-sequence-planner-design.md)
- [Implementation Plan](docs/superpowers/plans/2026-03-22-planner-capture-app.md)
- [Architecture (UML)](docs/architecture.md)
- [Dependencies](docs/dependencies.md)
- [Stellarium Build Guide](docs/stellarium-build.md)

## License

TBD
