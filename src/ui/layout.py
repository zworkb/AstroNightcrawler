"""Main page layout composing toolbar, map area, and bottom panel."""

from nicegui import ui

from src.app_state import AppState
from src.ui.bottom_panel import BottomPanelComponent
from src.ui.toolbar import ToolbarComponent

_HEAD_CSS = (
    "<style>"
    "body{margin:0;overflow:hidden}"
    " .map-container{flex:1;position:relative;"
    "background:#0a0a19;min-height:0}"
    "</style>"
)


def create_layout() -> None:
    """Build the full-page layout with toolbar, map, and bottom panel."""
    state = AppState()
    ui.add_head_html(_HEAD_CSS)
    with ui.column().classes("w-full h-screen no-wrap"):
        ToolbarComponent(state).render()
        with ui.element("div").classes("map-container"):
            ui.label(
                "Star map — coming soon",
            ).classes("text-grey-6 absolute-center")
        BottomPanelComponent(state).render()
