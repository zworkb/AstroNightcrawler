"""Toolbar component with drawing, edit, file, and action buttons."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

from src.models.project import SplinePath

if TYPE_CHECKING:
    from src.app_state import AppState


_MODE_MAP: dict[str, str] = {
    "draw": "draw",
    "freehand": "freehand",
    "move": "move",
    "add_point": "add_point",
    "remove_point": "remove_point",
    "split": "split",
}


class ToolbarComponent:
    """Top toolbar with drawing tools, undo/redo, file, and capture.

    Attributes:
        state: Shared application state.
        callbacks: Mapping of action names to handler functions.
    """

    def __init__(
        self,
        state: AppState,
        callbacks: dict[str, Callable[[], None]] | None = None,
    ) -> None:
        """Initialise the toolbar.

        Args:
            state: Shared application state.
            callbacks: Optional mapping of action names to handlers.
        """
        self.state = state
        self.callbacks = callbacks or {}

    def render(self) -> None:
        """Render the toolbar row."""
        with ui.row().classes(
            "w-full items-center gap-1 px-2 py-1 bg-dark"
        ):
            self._render_drawing_tools()
            ui.separator().props("vertical")
            self._render_edit_tools()
            ui.separator().props("vertical")
            self._render_file_tools()
            ui.space()
            self._render_action_tools()

    def _render_drawing_tools(self) -> None:
        """Render drawing tool buttons."""
        tools = [
            ("draw", "Draw"),
            ("gesture", "Freehand"),
            ("open_with", "Move"),
            ("add_circle_outline", "Add Point"),
            ("remove_circle_outline", "Remove Point"),
            ("call_split", "Split"),
        ]
        for icon, tooltip in tools:
            name = tooltip.lower().replace(" ", "_")
            btn = ui.button(
                icon=icon,
                on_click=self._mode_action(name),
            ).props("flat dense")
            btn.tooltip(tooltip)

    def _render_edit_tools(self) -> None:
        """Render undo/redo buttons."""
        undo_btn = ui.button(
            icon="undo",
            on_click=self._on_undo,
        ).props("flat dense")
        undo_btn.tooltip("Undo")
        undo_btn.bind_enabled_from(
            self.state.undo_stack, "can_undo",
            backward=lambda v: v,
        )

        redo_btn = ui.button(
            icon="redo",
            on_click=self._on_redo,
        ).props("flat dense")
        redo_btn.tooltip("Redo")
        redo_btn.bind_enabled_from(
            self.state.undo_stack, "can_redo",
            backward=lambda v: v,
        )

    def _render_file_tools(self) -> None:
        """Render save/load buttons."""
        save_btn = ui.button(
            icon="save",
            on_click=self._action("save"),
        ).props("flat dense")
        save_btn.tooltip("Save")

        load_btn = ui.button(
            icon="folder_open",
            on_click=self._action("load"),
        ).props("flat dense")
        load_btn.tooltip("Load")

    def _render_action_tools(self) -> None:
        """Render the start-capture button."""
        btn = ui.button(
            "Start Capture",
            icon="play_arrow",
            on_click=self._action("start_capture"),
            color="green",
        )
        btn.tooltip("Start Capture Sequence")

    def _action(self, name: str) -> Callable[[], None]:
        """Return the callback for *name*, or a no-op."""
        return self.callbacks.get(name, lambda: None)

    def _mode_action(self, name: str) -> Callable[[], None]:
        """Return a callback that sets the JS overlay mode."""
        mode = _MODE_MAP.get(name)
        if mode is None:
            return lambda: None

        def _set_mode() -> None:
            ui.run_javascript(
                f"window.pathOverlayBridge?.setMode('{mode}')"
            )

        return _set_mode

    async def _on_undo(self) -> None:
        """Undo the last action and refresh the overlay."""
        snapshot = self.state.undo_stack.undo()
        if snapshot is None:
            return
        path = SplinePath.model_validate_json(snapshot)
        self.state.project.path = path
        self.state.update_capture_points()
        await _refresh_overlay(self.state)

    async def _on_redo(self) -> None:
        """Redo the last undone action and refresh the overlay."""
        snapshot = self.state.undo_stack.redo()
        if snapshot is None:
            return
        path = SplinePath.model_validate_json(snapshot)
        self.state.project.path = path
        self.state.update_capture_points()
        await _refresh_overlay(self.state)


async def _refresh_overlay(state: AppState) -> None:
    """Serialize path data and push to the JS overlay.

    Args:
        state: Application state with current path and capture points.
    """
    cp_data = _serialize_control_points(state)
    cap_data = _serialize_capture_points(state)
    js = (
        "window.pathOverlayBridge?.update("
        f"{json.dumps(cp_data)}, {json.dumps(cap_data)})"
    )
    await ui.run_javascript(js)


def _serialize_control_points(state: AppState) -> list[dict[str, object]]:
    """Serialize control points for the JS overlay.

    Args:
        state: Application state.

    Returns:
        List of serialized control point dicts.
    """
    return [
        {
            "ra": cp.ra,
            "dec": cp.dec,
            "label": cp.label,
            "handleIn": (
                {"ra": cp.handle_in.ra, "dec": cp.handle_in.dec}
                if cp.handle_in else None
            ),
            "handleOut": (
                {"ra": cp.handle_out.ra, "dec": cp.handle_out.dec}
                if cp.handle_out else None
            ),
        }
        for cp in state.project.path.control_points
    ]


def _serialize_capture_points(state: AppState) -> list[dict[str, object]]:
    """Serialize capture points for the JS overlay.

    Args:
        state: Application state.

    Returns:
        List of serialized capture point dicts.
    """
    return [
        {"ra": p.ra, "dec": p.dec, "index": p.index}
        for p in state.project.capture_points
    ]
