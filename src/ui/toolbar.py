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
            self._render_project_label()
            self._render_view_toggles()
            ui.space()
            self._render_action_tools()

    def _project_caption(self) -> str:
        """Return the caption for the persistent project header."""
        p = self.state.project
        if self.state.project_dir is None:
            return f"Projekt: {p.project} (ungespeichert)"
        return f"Projekt: {p.project} — {self.state.project_dir}"

    def _render_project_label(self) -> None:
        """Render the always-visible project name + path header."""
        ui.separator().props("vertical").classes("mx-1")
        self._proj_label = (
            ui.label(self._project_caption())
            .classes("text-sm text-grey-4 truncate")
            .style("max-width:32rem")
        )
        self._proj_label.tooltip(
            "Speicherort des aktuellen Projekts",
        )

    def refresh_project_label(self) -> None:
        """Update the persistent project header to reflect current state."""
        label = getattr(self, "_proj_label", None)
        if label is not None:
            label.text = self._project_caption()

    def refresh_project_state(self) -> None:
        """Sync all project-state-dependent UI: header + tool gating.

        Single hook the ``project_loaded`` layout callback calls so we
        don't have to thread two separate method names through the
        layout wiring.
        """
        self.refresh_project_label()
        self._update_tool_gating()

    def _render_drawing_tools(self) -> None:
        """Render drawing tool buttons.

        Pan stays always enabled (pure navigation, harmless without a
        project). The six path-mutating tools (Draw, Freehand, Move,
        Add Point, Remove Point, Split) are disabled until a project
        is located on disk — see :meth:`_update_tool_gating`.
        """
        tools = [
            ("pan_tool", "Pan"),
            ("draw", "Draw"),
            ("gesture", "Freehand"),
            ("open_with", "Move"),
            ("add_circle_outline", "Add Point"),
            ("remove_circle_outline", "Remove Point"),
            ("call_split", "Split"),
        ]
        self._path_tool_btns: list[ui.button] = []
        for icon, tooltip in tools:
            name = tooltip.lower().replace(" ", "_")
            btn = ui.button(
                icon=icon,
                on_click=self._mode_action(name),
            ).props("flat dense")
            btn.tooltip(tooltip)
            if name != "pan":
                self._path_tool_btns.append(btn)
        self._update_tool_gating()

    def _update_tool_gating(self) -> None:
        """Enable project-state-dependent controls iff a project_dir is bound.

        Path-mutating drawing tools AND the Start Capture button all
        require a located project, so they share the same gate. Called
        initially after rendering and again whenever the
        ``project_loaded`` callback fires so the gating tracks New /
        Open / Load-JSON transitions (Load-JSON drops ``project_dir``
        and re-disables everything, which is the intended behaviour
        from #142).
        """
        enabled = self.state.project_dir is not None
        for btn in getattr(self, "_path_tool_btns", []):
            btn.set_enabled(enabled)
        start_btn = getattr(self, "_start_capture_btn", None)
        if start_btn is not None:
            start_btn.set_enabled(enabled)

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

        new_btn = ui.button(
            icon="create_new_folder",
            on_click=self._on_new_project,
        ).props("flat dense")
        new_btn.tooltip("Neues Projekt (Name + Ort)")

        open_btn = ui.button(
            icon="drive_folder_upload",
            on_click=self._on_open_project,
        ).props("flat dense")
        open_btn.tooltip("Open Project (capture directory)")

        ekos_btn = ui.button(
            icon="file_download",
            on_click=self._on_ekos_export,
        ).props("flat dense")
        ekos_btn.tooltip("Export EKOS Sequence")

    def _render_view_toggles(self) -> None:
        """Render view toggle buttons for constellations, atmosphere, and DSOs."""
        ui.separator().props("vertical").classes("mx-1")

        # Constellation lines toggle (default: on)
        self._const_lines = True
        self._lines_btn = ui.button(
            icon="polyline",
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

        # DSO toggle (default: on)
        self._dso = True
        self._dso_btn = ui.button(
            icon="blur_on",
            on_click=lambda: self._toggle_dso(),
        ).props("flat dense").classes("text-blue")
        self._dso_btn.tooltip("Toggle deep sky objects")

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

    def _toggle_dso(self) -> None:
        """Toggle deep sky objects visibility on the starmap."""
        self._dso = not self._dso
        val = "true" if self._dso else "false"
        ui.run_javascript(f"window.stelBridge?.setDSOVisible({val})")
        self._dso_btn.classes(
            replace="text-blue" if self._dso else "text-grey",
        )

    def _render_action_tools(self) -> None:
        """Render the start-capture and render buttons."""
        self._start_capture_btn = ui.button(
            "Start Capture",
            icon="play_arrow",
            on_click=self._action("start_capture"),
            color="green",
        )
        self._start_capture_btn.tooltip("Start Capture Sequence")
        # Gating tracks project_dir: no located project -> no capture.
        # Re-evaluated in _update_tool_gating once the project_loaded
        # callback fires.
        self._update_tool_gating()

        render_btn = ui.button(
            "Render",
            icon="movie",
            on_click=self._action("open_render"),
            color="orange",
        )
        render_btn.tooltip("Open Renderer")

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

        self._trigger("project_loaded")
        self._sync_overlay_from_camera()

    async def _on_new_project(self) -> None:
        """Open the New-Project dialog: name field + parent-folder picker.

        Two stacked dialogs: an outer name dialog stays open while the
        nested :class:`FolderBrowserDialog` is used to pick the parent
        location. The folder browser closes itself on Select
        (folder_browser.py:171), then the user confirms with "Create".
        """
        from src.config import settings
        from src.ui.folder_browser import FolderBrowserDialog

        # Default parent: configured output_dir (same default as Open).
        parent_holder: dict[str, Path] = {"parent": Path(settings.output_dir)}

        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Neues Projekt").classes("text-lg font-bold")
            name_input = ui.input(
                label="Projektname",
                placeholder="z.B. cygnus_2026",
            ).classes("w-full")
            parent_label = ui.label(
                f"Ort: {parent_holder['parent']}",
            ).classes("text-xs text-grey break-all")

            def _pick_parent() -> None:
                def _on_parent_selected(chosen: Path) -> None:
                    parent_holder["parent"] = chosen
                    parent_label.text = f"Ort: {chosen}"

                browser = FolderBrowserDialog(on_select=_on_parent_selected)
                browser.open(parent_holder["parent"])

            ui.button(
                "Ort wählen…",
                icon="folder",
                on_click=_pick_parent,
            ).props("flat")

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Abbrechen", on_click=dialog.close).props("flat")
                ui.button(
                    "Anlegen",
                    icon="create_new_folder",
                    color="primary",
                    on_click=lambda: self._handle_new_project(
                        parent_holder["parent"],
                        name_input.value or "",
                        dialog,
                    ),
                )
        dialog.open()

    def _handle_new_project(
        self,
        parent: Path,
        name: str,
        dialog: ui.dialog,
    ) -> None:
        """Create the new project, refresh UI, and close the dialog.

        Args:
            parent: Parent directory chosen via the folder browser.
            name: Project name entered by the user.
            dialog: The outer name dialog (closed on success).
        """
        try:
            self.state.new_project(parent, name)
        except FileExistsError:
            ui.notify(
                "Verzeichnis existiert bereits — bitte anderen Namen wählen",
                type="negative",
            )
            return
        except ValueError as exc:
            ui.notify(f"Ungültiger Name: {exc}", type="warning")
            return
        except OSError as exc:
            ui.notify(
                f"Konnte Projekt nicht anlegen: {exc}",
                type="negative",
            )
            return

        dialog.close()
        ui.notify(
            f"Projekt '{self.state.project.project}' angelegt",
            type="positive",
        )
        # Same refresh hook Open uses: panel + header + gating + auto-save.
        self._trigger("project_loaded")
        self._sync_overlay_from_camera()

    async def _on_open_project(self) -> None:
        """Open the folder browser to pick an existing project directory."""
        from src.config import settings
        from src.ui.folder_browser import FolderBrowserDialog

        dialog = FolderBrowserDialog(on_select=self._handle_open_project)
        dialog.open(Path(settings.output_dir))

    def _handle_open_project(self, project_dir: Path) -> None:
        """Load the chosen project directory and refresh the star map.

        Args:
            project_dir: The directory selected in the folder browser.
        """
        try:
            report = self.state.open_project(project_dir)
        except (FileNotFoundError, ValueError) as exc:
            ui.notify(f"Could not open project: {exc}", type="negative")
            return

        project = self.state.project
        points = project.capture_points
        complete = sum(1 for p in points if p.is_complete)
        msg = f"Opened '{project.project}': {complete}/{len(points)} points complete"
        if report.removed_count:
            msg += (
                f" — {report.removed_count} frame(s) missing, "
                "will be re-captured"
            )
        ui.notify(msg, type="positive")

        # Repopulate the Capture Points table (refreshable) and redraw the
        # star-map overlay. The table refresh is separate from the overlay —
        # without it the path shows on the map but the table stays empty.
        self._trigger("project_loaded")
        self._sync_overlay_from_camera()

    def _sync_overlay_from_camera(self) -> None:
        """Pull live camera state from JS to trigger an overlay redraw.

        The JS callback emits ``camera_state_update``, whose handler
        updates ``last_camera`` and calls ``refresh_overlay`` so the
        loaded path/capture points render on the star map.
        """
        ui.run_javascript("""
            (() => {
                const cam = window.stelBridge?.getCameraState();
                if (cam) emitEvent('camera_state_update', cam);
            })();
        """)

    def _trigger(self, name: str) -> None:
        """Synchronously fire the callback for *name* from a sync handler.

        Used by ``_handle_open_project`` / ``_handle_upload`` /
        ``_handle_new_project`` to invoke the ``project_loaded`` callback
        (panel refresh + header refresh + auto-save). Resolves the dict
        at call time so layout's late callback overrides are honored.

        If the callback is a coroutine function, schedules it on the
        running event loop via ``asyncio.ensure_future`` (fire-and-forget)
        so the sync caller is not blocked.
        """
        import asyncio
        import inspect
        cb = self.callbacks.get(name, lambda: None)
        result = cb()
        if inspect.iscoroutine(result):
            try:
                asyncio.ensure_future(result)
            except RuntimeError:
                # No running loop — coroutine is silently dropped.
                pass

    def _action(self, name: str) -> Callable[[], Any]:
        """Return a stable async callable that resolves *name* at CLICK time.

        Earlier this returned ``self.callbacks.get(name, ...)`` directly,
        which snapshotted whatever was in the dict when the button was
        rendered. ``layout.py`` patches some entries (``start_capture``,
        ``project_loaded``) AFTER the toolbar renders to inject the
        panel reference for live refresh — those overrides never reached
        buttons that were already bound. Returning a late-binding closure
        re-reads the dict on every click so the override path works.

        The closure is ``async`` because some callbacks (notably
        ``start_capture``) are coroutine functions: NiceGUI inspects the
        handler with ``iscoroutinefunction``, and a sync wrapper that
        merely returns a coroutine would silently swallow it. Making
        ``_call`` itself async means NiceGUI awaits us, and we then
        await the underlying callback if it returned a coroutine.
        """
        import inspect
        async def _call() -> None:
            cb = self.callbacks.get(name, lambda: None)
            result = cb()
            if inspect.iscoroutine(result):
                await result
        return _call

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


