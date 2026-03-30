# Dependencies

## Runtime — Planner & Capture

| Library | Version | Purpose | License |
|---|---|---|---|
| **NiceGUI** | >=2.0 | Web framework (Python -> Browser UI) | MIT |
| **FastAPI** | >=0.110 | ASGI web framework (underneath NiceGUI) | MIT |
| **uvicorn** | >=0.29 | ASGI server for running the app | BSD-3 |
| **Pydantic** | >=2.0 | Data models, validation, JSON serialization | MIT |
| **pydantic-settings** | >=2.0 | Config from .env / environment variables | MIT |
| **astropy** | >=6.0 | FITS file read/write, coordinate transforms | BSD-3 |

## Runtime — Renderer

| Library | Version | Purpose | License |
|---|---|---|---|
| **Pillow** | >=10.0 | Image resizing, PNG I/O for frame export | HPND |
| **numpy** | >=1.26 | Array operations for image processing | BSD-3 |
| **scipy** | >=1.12 | Sub-pixel image shifting (ndimage.shift) for linear-pan transitions | BSD-3 |
| **colour-demosaicing** | >=0.2 | Bayer CFA demosaicing (bilinear interpolation) | BSD-3 |
| **astroalign** | >=2.5 | Star-triangle alignment between adjacent frames | MIT |
| **astropy** | >=6.0 | FITS loading, ZScale/Asinh stretch for tone mapping | BSD-3 |

Note: `astropy` is shared between planner and renderer. The renderer also uses NiceGUI/FastAPI/uvicorn when started with `--ui`.

## Optional (Hardware)

| Library | Version | Purpose | License |
|---|---|---|---|
| **pyindi-client** | >=2.0 | INDI protocol (legacy, replaced by asynclient) | LGPL-2.1 |

Install with: `pip install ".[indi]"`

The pure-Python `AsyncINDIClient` (`src/indi/asynclient/`) is now the default and requires no extra dependencies.

## External (AGPL, installed separately)

| Library | Purpose | License |
|---|---|---|
| **Stellarium Web Engine** | Star map (C->WASM/WebGL), catalogs, coordinate systems | AGPL-3.0 |

Installed via `scripts/install_stellarium.sh`. Not bundled with the application due to AGPL license. Requires Emscripten for building from source.

## System Tools (not Python)

| Tool | Purpose | When needed |
|---|---|---|
| **ffmpeg** | H.264 video encoding from rendered PNG frames | Rendering App (`nightcrawler-render`) |
| **Emscripten (emcc)** | Build Stellarium WASM from source | Only during `install_stellarium.sh` |

## Development

| Tool | Version | Purpose | License |
|---|---|---|---|
| **pytest** | >=8.0 | Test framework | MIT |
| **pytest-asyncio** | >=0.23 | Async test support | MIT |
| **ruff** | >=0.4 | Linting + formatting (PEP 8) | MIT |
| **mypy** | >=1.10 | Static type checking | MIT |

Install with: `pip install ".[dev]"`
