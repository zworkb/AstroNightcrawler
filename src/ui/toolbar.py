"""Toolbar component with drawing, edit, file, and action buttons."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from src.models.project import SplinePath
from src.ui.overlay_sync import refresh_overlay

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
            self._render_view_toggles()
            ui.space()
            self._render_action_tools()

    def _render_drawing_tools(self) -> None:
        """Render drawing tool buttons."""
        tools = [
            ("pan_tool", "Pan"),
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
            on_click=self._on_save,
        ).props("flat dense")
        save_btn.tooltip("Save")

        load_btn = ui.button(
            icon="folder_open",
            on_click=self._on_load,
        ).props("flat dense")
        load_btn.tooltip("Load")

        ekos_btn = ui.button(
            icon="file_download",
            on_click=self._on_ekos_export,
        ).props("flat dense")
        ekos_btn.tooltip("Export EKOS Sequence")

    def _render_view_toggles(self) -> None:
        """Render view toggle buttons for constellations and atmosphere."""
        ui.separator().props("vertical").classes("mx-1")

        # Constellation lines toggle (default: on)
        self._const_lines = True
        self._lines_btn = ui.button(
            icon="auto_awesome",
            on_click=lambda: self._toggle_const_lines(),
        ).props("flat dense").classes("text-blue")
        self._lines_btn.tooltip("Toggle constellation lines")

        # Constellation labels toggle (default: on)
        self._const_labels = True
        self._labels_btn = ui.button(
            icon="label",
            on_click=lambda: self._toggle_const_labels(),
        ).props("flat dense").classes("text-blue")
        self._labels_btn.tooltip("Toggle constellation labels")

        # Atmosphere toggle (default: on)
        self._atmo = True
        self._atmo_btn = ui.button(
            icon="cloud",
            on_click=lambda: self._toggle_atmosphere(),
        ).props("flat dense").classes("text-blue")
        self._atmo_btn.tooltip("Toggle atmosphere")

    def _toggle_const_lines(self) -> None:
        """Toggle constellation lines visibility on the starmap."""
        self._const_lines = not self._const_lines
        val = "true" if self._const_lines else "false"
        ui.run_javascript(f"window.stelBridge?.setConstellationLines({val})")
        self._lines_btn.classes(
            replace="text-blue" if self._const_lines else "text-grey",
        )

    def _toggle_const_labels(self) -> None:
        """Toggle constellation labels visibility on the starmap."""
        self._const_labels = not self._const_labels
        val = "true" if self._const_labels else "false"
        ui.run_javascript(f"window.stelBridge?.setConstellationLabels({val})")
        self._labels_btn.classes(
            replace="text-blue" if self._const_labels else "text-grey",
        )

    def _toggle_atmosphere(self) -> None:
        """Toggle atmosphere visibility on the starmap."""
        self._atmo = not self._atmo
        val = "true" if self._atmo else "false"
        ui.run_javascript(f"window.stelBridge?.setAtmosphere({val})")
        self._atmo_btn.classes(
            replace="text-blue" if self._atmo else "text-grey",
        )

    def _render_action_tools(self) -> None:
        """Render the start-capture button."""
        btn = ui.button(
            "Start Capture",
            icon="play_arrow",
            on_click=self._action("start_capture"),
            color="green",
        )
        btn.tooltip("Start Capture Sequence")

    async def _on_ekos_export(self) -> None:
        """Export capture sequence as EKOS XML and trigger download."""
        from src.export.ekos import export_sequence

        self.state.update_capture_points()

        if len(self.state.project.capture_points) < 2:
            ui.notify("Need at least 2 capture points", type="warning")
            return

        tmp = Path(tempfile.mktemp(suffix=".esq"))
        export_sequence(self.state.project, tmp)
        ui.download(tmp)
        ui.notify("EKOS sequence exported", type="positive")

    async def _on_save(self) -> None:
        """Save the project to a temp file and trigger download."""
        data = self.state.project.model_dump_json(indent=2)
        tmp = Path(tempfile.mktemp(suffix=".json"))
        tmp.write_text(data)
        ui.download(tmp)
        ui.notify("Project saved", type="positive")

    async def _on_load(self) -> None:
        """Open a dialog for uploading a project JSON file."""
        with ui.dialog() as dialog, ui.card():
            ui.label("Load Project")
            ui.upload(
                on_upload=lambda e: self._handle_upload(e, dialog),
            ).props('accept=".json"')
        dialog.open()

    async def _handle_upload(
        self,
        event: Any,
        dialog: ui.dialog,
    ) -> None:
        """Parse uploaded JSON and replace the current project.

        Args:
            event: Upload event with file content.
            dialog: The open dialog to close after loading.
        """
        content = (await event.file.read()).decode()
        self.state.load_project_from_json(content)
        dialog.close()
        ui.notify("Project loaded", type="positive")

        # Get camera state synchronously via JS, then refresh overlay.
        # The JS callback updates last_camera and triggers a Python refresh.
        ui.run_javascript("""
            (() => {
                const cam = window.stelBridge?.getCameraState();
                if (cam) emitEvent('camera_state_update', cam);
            })();
        """)

    def _action(self, name: str) -> Callable[[], None]:
        """Return the callback for *name*, or a no-op."""
        return self.callbacks.get(name, lambda: None)

    def _mode_action(self, name: str) -> Callable[[], None]:
        """Return a callback that sets the drawing mode."""
        mode = _MODE_MAP.get(name, name)

        def _set_mode() -> None:
            self.state.current_mode = mode
            is_draw = mode == "draw"
            ui.run_javascript(
                f"window.stelBridge?.setDrawMode({str(is_draw).lower()});"
                f"window.pathOverlayBridge?.setMode('{mode}');"
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
        refresh_overlay(self.state)

    async def _on_redo(self) -> None:
        """Redo the last undone action and refresh the overlay."""
        snapshot = self.state.undo_stack.redo()
        if snapshot is None:
            return
        path = SplinePath.model_validate_json(snapshot)
        self.state.project.path = path
        self.state.update_capture_points()
        refresh_overlay(self.state)


