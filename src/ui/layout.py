"""Main page layout composing toolbar, map area, and bottom panel."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nicegui import ui

from src.app_state import AppState
from src.models.freehand import fit_bezier_to_points, rdp_simplify
from src.models.project import ControlPoint, SplinePath
from src.ui.bottom_panel import BottomPanelComponent
from src.ui.capture_view import CaptureViewComponent
from src.ui.toolbar import ToolbarComponent

_HEAD_CSS = (
    "<style>"
    "body{margin:0;overflow:hidden}"
    " .map-container{flex:1;position:relative;"
    "background:#0a0a19;min-height:0}"
    "</style>"
)


def create_layout() -> None:
    """Build the full-page layout with toolbar, map, and panel."""
    state = AppState()
    capture_view = CaptureViewComponent()

    callbacks = _build_callbacks(state, capture_view)
    ui.add_head_html(_HEAD_CSS)

    with ui.column().classes("w-full h-screen no-wrap"):
        toolbar = ToolbarComponent(state, callbacks)
        toolbar.render()
        capture_view.render()
        with ui.element("div").classes("map-container"):
            ui.label(
                "Star map \u2014 coming soon",
            ).classes("text-grey-6 absolute-center")
        panel = BottomPanelComponent(state)
        panel.render()

    _register_path_events(state, panel)


def _build_callbacks(
    state: AppState,
    capture_view: CaptureViewComponent,
) -> dict[str, Any]:
    """Build toolbar callback mapping.

    Args:
        state: Shared application state.
        capture_view: Capture view component for start_capture.

    Returns:
        Dict of action name to callback function.
    """
    async def on_start_capture() -> None:
        await _start_capture(state, capture_view)

    return {"start_capture": on_start_capture}


async def _start_capture(
    state: AppState,
    capture_view: CaptureViewComponent,
) -> None:
    """Start the capture sequence.

    Args:
        state: Shared application state.
        capture_view: Capture view to show progress.
    """
    controller = state.start_capture()
    capture_view.start(controller)
    try:
        await controller.run()
    finally:
        capture_view.stop()


def _register_path_events(
    state: AppState,
    panel: BottomPanelComponent,
) -> None:
    """Register JS event handlers for path manipulation.

    Args:
        state: Shared application state.
        panel: Bottom panel to refresh on changes.
    """
    ui.on("path_add_point", lambda e: asyncio.ensure_future(
        _on_add_point(state, e.args, panel),
    ))
    ui.on("path_freehand_complete", lambda e: asyncio.ensure_future(
        _on_freehand_complete(state, e.args, panel),
    ))
    ui.on("path_move_point", lambda e: asyncio.ensure_future(
        _on_move_point(state, e.args, panel),
    ))
    ui.on("path_point_moved", lambda e: asyncio.ensure_future(
        _on_move_point(state, e.args, panel),
    ))
    ui.on("path_remove_point", lambda e: asyncio.ensure_future(
        _on_remove_point(state, e.args, panel),
    ))


async def _on_add_point(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle adding a control point from the JS overlay.

    Args:
        state: Shared application state.
        detail: Event detail with ra and dec.
        panel: Bottom panel to refresh.
    """
    before = state.project.path.model_dump_json()
    cp = ControlPoint(ra=detail["ra"], dec=detail["dec"])
    state.project.path.control_points.append(cp)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    await _refresh_overlay(state)


async def _on_freehand_complete(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle freehand drawing completion from the JS overlay.

    Args:
        state: Shared application state.
        detail: Event detail with list of points.
        panel: Bottom panel to refresh.
    """
    before = state.project.path.model_dump_json()
    raw_points: list[dict[str, float]] = detail.get("points", [])
    tuples = [(p["ra"], p["dec"]) for p in raw_points]
    simplified = rdp_simplify(tuples, epsilon=0.1)
    cps = fit_bezier_to_points(simplified)
    state.project.path = SplinePath(control_points=cps)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    await _refresh_overlay(state)


async def _on_move_point(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle moving a control point from the JS overlay.

    Args:
        state: Shared application state.
        detail: Event detail with index, ra, and dec.
        panel: Bottom panel to refresh.
    """
    idx = detail.get("index", 0)
    cps = state.project.path.control_points
    if 0 <= idx < len(cps):
        cps[idx].ra = detail["ra"]
        cps[idx].dec = detail["dec"]
    state.update_capture_points()
    panel.refresh()
    await _refresh_overlay(state)


async def _on_remove_point(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle removing a control point from the JS overlay.

    Args:
        state: Shared application state.
        detail: Event detail with index.
        panel: Bottom panel to refresh.
    """
    idx = detail.get("index", 0)
    cps = state.project.path.control_points
    if len(cps) <= 2:
        return
    before = state.project.path.model_dump_json()
    if 0 <= idx < len(cps):
        cps.pop(idx)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    await _refresh_overlay(state)


async def _refresh_overlay(state: AppState) -> None:
    """Serialize path data and push to the JS overlay.

    Args:
        state: Application state with path and capture points.
    """
    cp_data = [
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
    cap_data = [
        {"ra": p.ra, "dec": p.dec, "index": p.index}
        for p in state.project.capture_points
    ]
    js = (
        "window.pathOverlayBridge?.update("
        f"{json.dumps(cp_data)}, {json.dumps(cap_data)})"
    )
    await ui.run_javascript(js)
