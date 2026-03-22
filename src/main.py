"""FastAPI + NiceGUI entry point for Sequence Planner."""

from fastapi import FastAPI
from nicegui import ui

from src.config import settings
from src.ui.layout import create_layout

app = FastAPI(title="Sequence Planner")


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
