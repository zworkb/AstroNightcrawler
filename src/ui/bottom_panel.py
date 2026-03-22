"""Bottom panel showing path settings, capture table, and INDI status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

if TYPE_CHECKING:
    from src.app_state import AppState


class BottomPanelComponent:
    """Collapsible bottom panel with settings, table, and INDI controls.

    Attributes:
        state: Shared application state.
    """

    def __init__(self, state: AppState) -> None:
        """Initialise the bottom panel.

        Args:
            state: Shared application state.
        """
        self.state = state

    def render(self) -> None:
        """Render the expansion panel with all sections."""
        with ui.expansion(
            text=self._summary_text(),
            icon="tune",
        ).classes("w-full bg-dark"), ui.row().classes("w-full gap-4 p-2"):
            self._render_path_settings()
            ui.separator().props("vertical")
            self._render_capture_table()
            ui.separator().props("vertical")
            self._render_indi_section()

    def _summary_text(self) -> str:
        """Build the collapsed summary string."""
        proj = self.state.project
        n_ctrl = len(proj.path.control_points)
        n_cap = len(proj.capture_points)
        cs = proj.capture_settings
        est_min = self._estimate_minutes()
        return (
            f"Path: {n_ctrl} ctrl / {n_cap} capture"
            f" | {cs.exposure_seconds:.0f}s"
            f" x {cs.exposures_per_point}"
            f" | {cs.point_spacing_deg}\u00b0"
            f" | ~{est_min:.0f} min"
        )

    def _estimate_minutes(self) -> float:
        """Estimate total capture time in minutes."""
        cs = self.state.project.capture_settings
        n = len(self.state.project.capture_points)
        per_point = (
            5.0 + 3.0
            + cs.exposure_seconds * cs.exposures_per_point
        )
        return (n * per_point) / 60.0

    def _render_path_settings(self) -> None:
        """Render path and capture setting inputs."""
        cs = self.state.project.capture_settings
        with ui.column().classes("gap-2"):
            ui.label("Path Settings").classes("text-weight-bold")
            ui.number(
                "Spacing (\u00b0)", value=cs.point_spacing_deg,
                min=0.01, max=10.0, step=0.1,
                on_change=lambda e: setattr(
                    cs, "point_spacing_deg", e.value,
                ),
            ).classes("w-32")
            ui.number(
                "Exposure (s)", value=cs.exposure_seconds,
                min=0.1, max=3600.0, step=1.0,
                on_change=lambda e: setattr(
                    cs, "exposure_seconds", e.value,
                ),
            ).classes("w-32")
            ui.number(
                "Exposures/pt", value=cs.exposures_per_point,
                min=1, max=100, step=1,
                on_change=lambda e: setattr(
                    cs, "exposures_per_point", int(e.value),
                ),
            ).classes("w-32")
            ui.number(
                "Gain", value=cs.gain,
                min=0, max=1000, step=1,
                on_change=lambda e: setattr(
                    cs, "gain", int(e.value),
                ),
            ).classes("w-32")
            ui.number(
                "Offset", value=cs.offset,
                min=0, max=1000, step=1,
                on_change=lambda e: setattr(
                    cs, "offset", int(e.value),
                ),
            ).classes("w-32")
            ui.number(
                "Binning", value=cs.binning,
                min=1, max=4, step=1,
                on_change=lambda e: setattr(
                    cs, "binning", int(e.value),
                ),
            ).classes("w-32")

    def _render_capture_table(self) -> None:
        """Render the capture points table."""
        columns = [
            {"name": "index", "label": "#", "field": "index"},
            {"name": "ra", "label": "RA", "field": "ra"},
            {"name": "dec", "label": "Dec", "field": "dec"},
            {"name": "status", "label": "Status", "field": "status"},
        ]
        rows = [
            {
                "index": cp.index,
                "ra": f"{cp.ra:.4f}",
                "dec": f"{cp.dec:.4f}",
                "status": cp.status,
            }
            for cp in self.state.project.capture_points
        ]
        with ui.column().classes("gap-2 flex-grow"):
            ui.label("Capture Points").classes("text-weight-bold")
            ui.table(
                columns=columns,
                rows=rows,
                row_key="index",
            ).classes("w-full")

    def _render_indi_section(self) -> None:
        """Render INDI connection controls."""
        with ui.column().classes("gap-2"):
            ui.label("INDI Connection").classes("text-weight-bold")
            host_input = ui.input(
                "Host", value="localhost",
            ).classes("w-40")
            port_input = ui.number(
                "Port", value=7624,
                min=1, max=65535, step=1,
            ).classes("w-32")
            status_label = ui.label("Disconnected").classes(
                "text-red",
            )

            async def on_connect() -> None:
                host = host_input.value or "localhost"
                port = int(port_input.value or 7624)
                await self.state.indi_client.connect(host, port)
                status_label.text = "Connected"
                status_label.classes(remove="text-red", add="text-green")

            ui.button(
                "Connect", icon="power",
                on_click=on_connect,
            )
