"""Reusable folder browser dialog for NiceGUI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nicegui import ui


@dataclass
class DirectoryEntry:
    """A single entry in a directory listing."""

    name: str
    path: Path
    is_dir: bool
    has_manifest: bool = False
    size: int = 0


def list_directory(path: Path) -> list[DirectoryEntry]:
    """List entries in a directory.

    Directories first (sorted), then files (sorted).
    Directories containing manifest.json are flagged.

    Args:
        path: Directory to list.

    Returns:
        List of DirectoryEntry, with '..' first if not root.
    """
    entries: list[DirectoryEntry] = []

    # Parent navigation (not at filesystem root)
    if path.parent != path:
        entries.append(DirectoryEntry(
            name="..", path=path.parent, is_dir=True,
        ))

    dirs: list[DirectoryEntry] = []
    files: list[DirectoryEntry] = []

    for item in sorted(path.iterdir()):
        if item.name.startswith("."):
            continue
        try:
            if item.is_dir():
                has_manifest = (item / "manifest.json").exists()
                dirs.append(DirectoryEntry(
                    name=item.name, path=item, is_dir=True,
                    has_manifest=has_manifest,
                ))
            else:
                files.append(DirectoryEntry(
                    name=item.name, path=item, is_dir=False,
                    size=item.stat().st_size,
                ))
        except PermissionError:
            continue

    return entries + dirs + files


class FolderBrowserDialog:
    """A dialog for navigating and selecting a directory.

    Usage:
        dialog = FolderBrowserDialog(on_select=my_callback)
        dialog.open(start_path)
    """

    def __init__(self, on_select: Callable[[Path], None]) -> None:
        """Init with selection callback.

        Args:
            on_select: Called with the selected directory Path.
        """
        self._on_select = on_select
        self._current: Path = Path.cwd()
        self._dialog: ui.dialog | None = None

    def open(self, start_path: Path | None = None) -> None:
        """Open the dialog at the given path.

        Args:
            start_path: Initial directory to display.
        """
        self._current = start_path or Path.cwd()
        self._show()

    def _show(self) -> None:
        """Build and show the dialog."""
        if self._dialog:
            self._dialog.close()

        with ui.dialog() as self._dialog, ui.card().classes("w-96"):
            ui.label("Select Capture Directory").classes("text-lg font-bold")
            ui.label(str(self._current)).classes(
                "text-xs text-grey break-all",
            )
            ui.separator()

            entries = list_directory(self._current)
            with ui.column().classes("w-full max-h-80 overflow-y-auto gap-0"):
                for entry in entries:
                    self._render_entry(entry)

            self._render_buttons()

        self._dialog.open()

    def _render_buttons(self) -> None:
        """Render Cancel and Select buttons."""
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("Cancel", on_click=self._dialog.close).props("flat")
            has_manifest = (self._current / "manifest.json").exists()
            select_btn = ui.button(
                "Select",
                on_click=lambda: self._select(),
                color="green" if has_manifest else "primary",
            )
            if has_manifest:
                select_btn.tooltip("Contains manifest.json")

    def _render_entry(self, entry: DirectoryEntry) -> None:
        """Render a single directory entry row.

        Args:
            entry: The directory entry to display.
        """
        icon = "folder" if entry.is_dir else "description"
        color = "text-green" if entry.has_manifest else ""

        with ui.row().classes(
            f"w-full items-center gap-2 px-2 py-1 "
            f"cursor-pointer hover:bg-gray-800 {color}",
        ).on("click", lambda _, e=entry: self._on_click(e)):
            ui.icon(icon).classes("text-lg")
            ui.label(entry.name).classes("flex-grow")
            if entry.has_manifest:
                ui.badge("manifest", color="green").props("dense")
            elif not entry.is_dir:
                size_kb = entry.size // 1024
                ui.label(f"{size_kb} KB").classes("text-xs text-grey")

    def _on_click(self, entry: DirectoryEntry) -> None:
        """Handle click on an entry.

        Args:
            entry: The clicked directory entry.
        """
        if entry.is_dir:
            self._current = entry.path
            if self._dialog:
                self._dialog.close()
            self._show()

    def _select(self) -> None:
        """Confirm selection of current directory."""
        if self._dialog:
            self._dialog.close()
        self._on_select(self._current)
