"""FastAPI + NiceGUI entry point for Sequence Planner."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from nicegui import ui

from src.config import settings
from src.ui.layout import create_layout

app = FastAPI(title="Sequence Planner")

# Serve Stellarium WASM + skydata as static files
_static_dir = Path(__file__).parent.parent / "static"
_skydata_dir = Path(__file__).parent.parent / "skydata"

if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
if _skydata_dir.exists():
    app.mount("/skydata", StaticFiles(directory=str(_skydata_dir)), name="skydata")


@ui.page("/")
def index() -> None:
    """Render the main application page."""
    create_layout()


ui.run_with(app, title="Sequence Planner", dark=True)


def main() -> None:
    """Start the application server."""
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
