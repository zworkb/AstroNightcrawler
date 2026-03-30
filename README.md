# AstroNightcrawler

> **Status:** Planner, capture, and rendering are functional. Tested with real hardware (LX200 OnStep + Canon 600D) and rendered video output with star-aligned linear pan and crossfade transitions.

A browser-based application for planning and executing imaging sequences with a remote-controlled telescope. Draw a path on an interactive star map, and the telescope follows it point by point, capturing images at each position. A companion rendering app converts the captured FITS frames into video.

## What it does

- **Plan** imaging sequences by drawing spline paths on a live star map (Stellarium Web Engine)
- **Capture** images by controlling your telescope and camera via INDI protocol over the network
- **Export** sequences for EKOS/KStars as an alternative to direct INDI control
- **Render** captured frames into video with debayering, stretch, star alignment, and transitions

## Quick Start

### Planner & Capture

```bash
git clone git@github.com:zworkb/AstroNightcrawler.git
cd AstroNightcrawler
make install    # Python venv + all dependencies
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
- **Images:** 5184x3456 px, 16-bit FITS (35 MB per frame)
- **Rendered output:** H.264 video with linear-pan transitions (star-aligned sub-pixel panning)

## License

TBD
