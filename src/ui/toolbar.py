"""Toolbar component with drawing, edit, file, and action buttons."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from src.app_state import AppState


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
            btn = ui.button(
                icon=icon,
                on_click=self._action(tooltip.lower().replace(" ", "_")),
            ).props("flat dense")
            btn.tooltip(tooltip)

    def _render_edit_tools(self) -> None:
        """Render undo/redo buttons."""
        undo_btn = ui.button(
            icon="undo",
            on_click=self._action("undo"),
        ).props("flat dense")
        undo_btn.tooltip("Undo")
        undo_btn.bind_enabled_from(
            self.state.undo_stack, "can_undo",
            backward=lambda v: v,
        )

        redo_btn = ui.button(
            icon="redo",
            on_click=self._action("redo"),
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
