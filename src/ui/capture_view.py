"""Capture view UI component showing progress during an active sequence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nicegui import ui

from src.capture.controller import CaptureState

if TYPE_CHECKING:
    from src.capture.controller import CaptureController


class CaptureViewComponent:
    """Shows capture progress during an active sequence.

    Displays status, point/exposure counters, estimated remaining time,
    and pause/resume/skip/cancel controls.
    """

    def __init__(self) -> None:
        """Initialise capture view with empty UI references."""
        self._controller: CaptureController | None = None
        self._timer: Any = None
        self._container: Any = None
        self._progress: Any = None
        self._status_label: Any = None
        self._point_label: Any = None
        self._exposure_label: Any = None
        self._remaining_label: Any = None
        self._pause_btn: Any = None
        self._skip_btn: Any = None
        self._cancel_btn: Any = None

    def render(self) -> None:
        """Create the capture progress bar and control row (initially hidden)."""
        self._container = ui.row().classes(
            "w-full items-center gap-2 px-2 py-1 bg-dark"
        )
        self._container.set_visibility(False)

        with self._container:
            self._status_label = ui.label("CAPTURE RUNNING").classes(
                "text-green font-bold"
            )
            ui.space()
            self._point_label = ui.label("Point: 0 / 0")
            self._exposure_label = ui.label("Exposure: 0 / 0")
            self._remaining_label = ui.label("Remaining: --")
            ui.space()
            self._render_buttons()

        self._progress = ui.linear_progress(value=0, show_value=False).classes(
            "w-full"
        )
        self._progress.set_visibility(False)

    def _render_buttons(self) -> None:
        """Render pause, skip, and cancel buttons."""
        self._pause_btn = ui.button(
            "Pause", icon="pause", on_click=self._on_pause_resume
        ).props("dense")
        self._skip_btn = ui.button(
            "Skip", icon="skip_next", on_click=self._on_skip
        ).props("dense")
        self._cancel_btn = ui.button(
            "Cancel", icon="cancel", on_click=self._on_cancel, color="red"
        ).props("dense")

    def start(self, controller: CaptureController) -> None:
        """Begin showing capture progress for the given controller.

        Args:
            controller: Active capture controller to monitor.
        """
        self._controller = controller
        if self._container is not None:
            self._container.set_visibility(True)
        if self._progress is not None:
            self._progress.set_visibility(True)
        self._timer = ui.timer(0.5, self._update)

    def stop(self) -> None:
        """Hide the capture UI and cancel the refresh timer."""
        if self._container is not None:
            self._container.set_visibility(False)
        if self._progress is not None:
            self._progress.set_visibility(False)
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._controller = None

    def _update(self) -> None:
        """Periodic refresh of labels, progress bar, and button states."""
        if self._controller is None:
            return
        state = self._controller.state
        if state in (CaptureState.COMPLETED, CaptureState.CANCELLED):
            self.stop()
            return
        self._update_status(state)
        self._update_counters()
        self._update_progress()
        self._highlight_current_point()

    def _update_status(self, state: CaptureState) -> None:
        """Update the status label text and colour based on state.

        Args:
            state: Current capture state.
        """
        if self._status_label is None or self._pause_btn is None:
            return
        if state == CaptureState.PAUSED and self._controller is not None:
            error = self._controller.last_error
            if error:
                self._status_label.text = f"ERROR: {error}"
                self._status_label.classes(replace="text-red font-bold")
            else:
                self._status_label.text = "PAUSED"
                self._status_label.classes(replace="text-orange font-bold")
            self._pause_btn.text = "Resume"
            self._pause_btn._props["icon"] = "play_arrow"  # noqa: SLF001
        else:
            self._status_label.text = "CAPTURE RUNNING"
            self._status_label.classes(replace="text-green font-bold")
            self._pause_btn.text = "Pause"
            self._pause_btn._props["icon"] = "pause"  # noqa: SLF001
        self._pause_btn.update()

    def _update_counters(self) -> None:
        """Update point, exposure, and remaining-time labels."""
        if self._controller is None:
            return
        total = len(self._controller.project.capture_points)
        idx = self._controller.current_point_index
        if self._point_label is not None:
            self._point_label.text = f"Point: {idx + 1} / {total}"
        settings = self._controller.project.capture_settings
        if self._exposure_label is not None:
            self._exposure_label.text = (
                f"Exposure: 1 / {settings.exposures_per_point}"
            )
        remaining = self._controller.estimated_remaining_seconds
        if self._remaining_label is not None:
            self._remaining_label.text = (
                f"Remaining: {_format_time(remaining)}"
            )

    def _update_progress(self) -> None:
        """Update the linear progress bar value."""
        if self._controller is None or self._progress is None:
            return
        total = len(self._controller.project.capture_points)
        if total == 0:
            return
        idx = self._controller.current_point_index
        self._progress.value = idx / total

    def _highlight_current_point(self) -> None:
        """Highlight the current point on the map overlay."""
        if self._controller is None:
            return
        idx = self._controller.current_point_index
        ui.run_javascript(
            f"window.pathOverlayBridge?.highlightPoint({idx})"
        )

    def _on_pause_resume(self) -> None:
        """Toggle between pause and resume."""
        if self._controller is None:
            return
        if self._controller.state == CaptureState.PAUSED:
            self._controller.resume()
        else:
            self._controller.pause()

    def _on_skip(self) -> None:
        """Skip the current point and resume."""
        if self._controller is None:
            return
        self._controller.skip_point()
        self._controller.resume()

    def _on_cancel(self) -> None:
        """Cancel the capture sequence."""
        if self._controller is None:
            return
        self._controller.cancel()


def _format_time(seconds: float) -> str:
    """Format seconds as '~Xm Ys'.

    Args:
        seconds: Number of seconds to format.

    Returns:
        Human-readable time string.
    """
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"~{minutes}m {secs}s"
