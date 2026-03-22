# Dependencies

## Runtime

| Library | Version | Purpose | License |
|---|---|---|---|
| **NiceGUI** | ≥2.0 | Web framework (Python → Browser UI) | MIT |
| **FastAPI** | ≥0.110 | ASGI web framework (underneath NiceGUI) | MIT |
| **uvicorn** | ≥0.29 | ASGI server for running the app | BSD-3 |
| **Pydantic** | ≥2.0 | Data models, validation, JSON serialization | MIT |
| **pydantic-settings** | ≥2.0 | Config from .env / environment variables | MIT |
| **astropy** | ≥6.0 | FITS file read/write, image stretch | BSD-3 |

## Optional (Hardware)

| Library | Version | Purpose | License |
|---|---|---|---|
| **pyindi-client** | ≥2.0 | INDI protocol for telescope/camera control | LGPL-2.1 |

Install with: `pip install ".[indi]"`

## External (AGPL, installed separately)

| Library | Purpose | License |
|---|---|---|
| **Stellarium Web Engine** | Star map (C→WASM/WebGL), catalogs, coordinate systems | AGPL-3.0 |

Installed via `scripts/install_stellarium.sh`. Not bundled with the application due to AGPL license. Requires Emscripten for building from source.

## Development

| Tool | Version | Purpose | License |
|---|---|---|---|
| **pytest** | ≥8.0 | Test framework | MIT |
| **pytest-asyncio** | ≥0.23 | Async test support | MIT |
| **ruff** | ≥0.4 | Linting + formatting (PEP 8) | MIT |
| **mypy** | ≥1.10 | Static type checking | MIT |

Install with: `pip install ".[dev]"`

## System Tools (not Python)

| Tool | Purpose | When needed |
|---|---|---|
| **ffmpeg** | Video encoding | Rendering App only (separate plan) |
| **Emscripten (emcc)** | Build Stellarium WASM from source | Only during `install_stellarium.sh` |
