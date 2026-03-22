"""Main page layout composing toolbar, map area, and bottom panel."""

from __future__ import annotations

import contextlib
import json
import logging
from typing import Any

from nicegui import ui

from src.app_state import AppState
from src.models.project import ControlPoint
from src.starmap.engine import StarMap
from src.starmap.projection import pixel_to_radec
from src.ui.bottom_panel import BottomPanelComponent
from src.ui.capture_view import CaptureViewComponent
from src.ui.toolbar import ToolbarComponent

_HEAD_CSS = (
    "<style>"
    "body{margin:0;overflow:hidden}"
    " .map-container{flex:1;position:relative;"
    "background:#0a0a19;min-height:0;overflow:hidden}"
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
            star_map = StarMap(width="100%", height="100%")

        async def _init_starmap() -> None:
            with contextlib.suppress(Exception):
                await star_map.initialize(
                    wasm_url="/static/stellarium/stellarium-web-engine.js",
                    skydata_url="/skydata/",
                )
            # Initialize path overlay on top of the star map
            cid = star_map.container_id
            result = await ui.run_javascript(f"""
                const el = document.getElementById('{cid}');
                if (!el) return 'CONTAINER_NOT_FOUND: {cid}';
                if (!window.pathOverlayBridge) return 'OVERLAY_NOT_LOADED';
                if (!window.stelBridge) return 'BRIDGE_NOT_LOADED';
                try {{
                    window.pathOverlayBridge.init('{cid}');
                    return 'OK: overlay initialized on ' + el.tagName;
                }} catch(e) {{
                    return 'ERROR: ' + e.message;
                }}
            """)
            import logging
            logging.getLogger("starmap").warning("Overlay init: %s", result)
            # Bridge DOM CustomEvents to NiceGUI server events
            for evt in [
                "map_click",
                "path_add_point", "path_freehand_complete",
                "path_move_point", "path_point_moved",
                "path_remove_point", "path_split",
                "path_add_point_on_segment",
            ]:
                await ui.run_javascript(f"""
                    document.getElementById('{cid}')
                        ?.addEventListener('{evt}', (e) => {{
                            emitEvent('{evt}', e.detail);
                        }});
                """)

        ui.timer(0.5, _init_starmap, once=True)
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
    def _extract_detail(e: Any) -> dict[str, Any]:
        """Extract event detail from NiceGUI event args."""
        args = e.args
        if isinstance(args, dict):
            return args.get("detail", args)
        if isinstance(args, list) and args:
            return args[0] if isinstance(args[0], dict) else {}
        return {}

    ui.on("map_click", lambda e: _on_map_click_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("path_remove_point", lambda e: _on_remove_point_sync(
        state, _extract_detail(e), panel,
    ))


def _on_map_click_sync(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Route map clicks based on current drawing mode (sync).

    Args:
        state: Shared application state.
        detail: Event detail with pixel coords + camera state.
        panel: Bottom panel to refresh.
    """
    logging.getLogger("starmap").info(
        "map_click keys=%s mode=%s", list(detail.keys()), state.current_mode,
    )

    if state.current_mode != "draw":
        return
    if "ra" not in detail:
        return

    ra = float(detail["ra"])
    dec = float(detail["dec"])
    before = state.project.path.model_dump_json()
    cp = ControlPoint(ra=ra, dec=dec)
    state.project.path.control_points.append(cp)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    _refresh_overlay_sync(state)


def _detail_to_radec(detail: dict[str, Any]) -> tuple[float, float]:
    """Convert event detail with pixel+camera to RA/Dec.

    Args:
        detail: Dict with x, y, canvas_width, canvas_height,
                yaw, pitch, fov.

    Returns:
        Tuple of (ra, dec) in degrees.
    """
    return pixel_to_radec(
        x=detail["x"],
        y=detail["y"],
        canvas_width=int(detail.get("canvas_width", 800)),
        canvas_height=int(detail.get("canvas_height", 600)),
        center_yaw=detail.get("yaw", 0.0),
        center_pitch=detail.get("pitch", 0.0),
        fov=detail.get("fov", 60.0),
    )


    # NOTE: freehand and move handlers will be added when those modes
    # are wired up. For now, only draw and remove are active.


def _on_remove_point_sync(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle removing a control point (sync).

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
    _refresh_overlay_sync(state)


def _refresh_overlay_sync(state: AppState) -> None:
    """Send path data to JS overlay (projection done client-side).

    Args:
        state: Application state with path and capture points.
    """
    cp_data = [
        {
            "ra": cp.ra, "dec": cp.dec, "label": cp.label,
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
    ui.run_javascript(js)
