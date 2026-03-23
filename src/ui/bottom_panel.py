"""Bottom panel showing path settings, capture table, and INDI status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from src.ui.overlay_sync import refresh_overlay

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
        self._expansion: ui.expansion | None = None

    def render(self) -> None:
        """Render the expansion panel with all sections."""
        self._expansion = ui.expansion(
            text=self._summary_text(),
            icon="tune",
        ).classes("w-full bg-dark")
        with self._expansion, ui.row().classes("w-full gap-4 p-2"):
            self._render_path_settings()
            ui.separator().props("vertical")
            self._render_capture_table()  # type: ignore[call-arg]
            ui.separator().props("vertical")
            self._render_indi_section()

    def refresh(self) -> None:
        """Refresh the summary text and capture table."""
        if self._expansion is not None:
            self._expansion._props["label"] = self._summary_text()
            self._expansion.update()
        self._refresh_table()

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

    def _on_apply_settings(self) -> None:
        """Recalculate capture points and refresh overlay."""
        self.state.update_capture_points()
        self.refresh()
        refresh_overlay(self.state)

    def _render_path_settings(self) -> None:
        """Render path and capture setting inputs."""
        cs = self.state.project.capture_settings
        with ui.column().classes("gap-2"):
            ui.label("Path Settings").classes("text-weight-bold")
            self._setting_number(
                "Spacing (\u00b0)", cs.point_spacing_deg,
                0.01, 10.0, 0.1, "point_spacing_deg",
            )
            self._setting_number(
                "Exposure (s)", cs.exposure_seconds,
                0.1, 3600.0, 1.0, "exposure_seconds",
            )
            self._setting_number(
                "Exposures/pt", cs.exposures_per_point,
                1, 100, 1, "exposures_per_point", as_int=True,
            )
            self._setting_number(
                "Gain", cs.gain,
                0, 1000, 1, "gain", as_int=True,
            )
            self._setting_number(
                "Offset", cs.offset,
                0, 1000, 1, "offset", as_int=True,
            )
            self._setting_number(
                "Binning", cs.binning,
                1, 4, 1, "binning", as_int=True,
            )
            ui.button(
                "Apply", icon="check",
                on_click=self._on_apply_settings,
            ).props("dense color=primary size=sm")

    def _setting_number(
        self,
        label: str,
        value: float | int,
        min_val: float,
        max_val: float,
        step: float,
        attr: str,
        *,
        as_int: bool = False,
    ) -> None:
        """Render a number input wired to a capture setting.

        Args:
            label: Display label.
            value: Initial value.
            min_val: Minimum allowed value.
            max_val: Maximum allowed value.
            step: Increment step.
            attr: Attribute name on capture_settings.
            as_int: Whether to cast the value to int.
        """
        cs = self.state.project.capture_settings

        def _on_change(e: object, a: str = attr) -> None:
            val = getattr(e, "value", None)
            if val is None:
                return
            setattr(cs, a, int(val) if as_int else val)

        ui.number(
            label, value=value,
            min=min_val, max=max_val, step=step,
            on_change=_on_change,
        ).classes("w-32")

    @ui.refreshable
    def _render_capture_table(self) -> None:
        """Render the capture points table."""
        columns = [
            {"name": "index", "label": "#", "field": "index"},
            {"name": "ra", "label": "RA", "field": "ra"},
            {"name": "dec", "label": "Dec", "field": "dec"},
            {"name": "status", "label": "Status", "field": "status"},
        ]
        rows = self._build_table_rows()
        with ui.column().classes("gap-2 flex-grow"):
            ui.label("Capture Points").classes("text-weight-bold")
            ui.table(
                columns=columns,
                rows=rows,
                row_key="index",
            ).classes("w-full")

    def _build_table_rows(self) -> list[dict[str, object]]:
        """Build row data for the capture points table.

        Returns:
            List of row dicts with index, ra, dec, and status.
        """
        return [
            {
                "index": cp.index,
                "ra": f"{cp.ra:.4f}",
                "dec": f"{cp.dec:.4f}",
                "status": cp.status,
            }
            for cp in self.state.project.capture_points
        ]

    def _refresh_table(self) -> None:
        """Refresh the capture table content."""
        self._render_capture_table.refresh()

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
                try:
                    from src.indi.async_adapter import AsyncINDIAdapter
                    self.state.indi_client = AsyncINDIAdapter()
                    await self.state.indi_client.connect(host, port)
                    status_label.text = f"Connected to {host}:{port}"
                    status_label.classes(
                        remove="text-red", add="text-green",
                    )
                except Exception as exc:  # noqa: BLE001
                    status_label.text = f"Failed: {exc}"
                    status_label.classes(
                        remove="text-green", add="text-red",
                    )

            ui.button(
                "Connect", icon="power",
                on_click=on_connect,
            )
