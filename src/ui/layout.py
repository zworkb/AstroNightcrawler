"""Main page layout composing toolbar, map area, and bottom panel."""

from __future__ import annotations

import logging
from typing import Any

from nicegui import app, ui

from src.app_state import AppState
from src.models.freehand import fit_bezier_to_points, rdp_simplify
from src.models.project import ControlPoint, Coordinate, SplinePath
from src.starmap.engine import StarMap
from src.starmap.projection import azalt_to_radec
from src.ui.bottom_panel import BottomPanelComponent
from src.ui.capture_view import CaptureViewComponent
from src.ui.overlay_sync import refresh_overlay
from src.ui.toolbar import ToolbarComponent

_HEAD_CSS = (
    "<style>"
    "body{margin:0;overflow:hidden}"
    " .map-container{flex:1;position:relative;"
    "background:#0a0a19;min-height:0;overflow:hidden}"
    "</style>"
)


def _auto_save(state: AppState) -> None:
    """Save project to server-side user storage for persistence."""
    data = state.project.model_dump_json()
    app.storage.user["project"] = data
    logging.getLogger("starmap").info(
        "Auto-saved project (%d control points, %d bytes)",
        len(state.project.path.control_points), len(data),
    )


def create_layout() -> None:
    """Build the full-page layout with toolbar, map, and panel."""
    state = AppState()

    # Restore project from server-side user storage if available
    saved = app.storage.user.get("project")
    if saved:
        logging.getLogger("starmap").info(
            "Restoring project from storage (%d bytes)", len(saved),
        )
        try:
            state.load_project_from_json(saved)
            logging.getLogger("starmap").info(
                "Restored %d control points",
                len(state.project.path.control_points),
            )
        except Exception:  # noqa: BLE001
            logging.getLogger("starmap").warning(
                "Failed to restore project from storage", exc_info=True,
            )
    else:
        logging.getLogger("starmap").info("No saved project in storage")

    capture_view = CaptureViewComponent()

    callbacks = _build_callbacks(state, capture_view)
    ui.add_head_html(_HEAD_CSS)

    with ui.column().classes("w-full h-screen no-wrap"):
        toolbar = ToolbarComponent(state, callbacks)
        toolbar.render()
        capture_view.render()
        with ui.element("div").classes("map-container"):
            star_map = StarMap(width="100%", height="100%")

        # Inject init script directly — no timer, no await, no roundtrip
        cid = star_map.container_id
        events = [
            "map_click",
            "path_add_point", "path_freehand_complete",
            "path_move_point", "path_point_moved",
            "path_remove_point", "path_split",
            "path_add_point_on_segment",
            "path_handle_moved",
        ]
        listeners = "\n".join(
            f"el.addEventListener('{evt}',"
            f" (e) => emitEvent('{evt}', e.detail));"
            for evt in events
        )
        from src.config import settings as cfg
        obs_lat = cfg.observer_lat
        obs_lon = cfg.observer_lon

        ui.add_body_html(f"""<script>
            (async () => {{
                // Wait for DOM to be ready
                await new Promise(r => setTimeout(r, 500));
                try {{
                    await window.stelBridge.initEngine(
                        '{cid}',
                        '/static/stellarium/stellarium-web-engine.js',
                        '/skydata/'
                    );
                    // Set observer location from server config
                    window.stelBridge.setObserver({obs_lat}, {obs_lon});
                }} catch(e) {{
                    console.warn('Stellarium init failed:', e);
                }}
                const el = document.getElementById('{cid}');
                if (el && window.pathOverlayBridge) {{
                    window.pathOverlayBridge.init('{cid}');
                    {listeners}
                    console.log('Overlay + events initialized');
                    // Trigger camera update so restored paths are displayed
                    const cam2 = window.stelBridge?.getCameraState();
                    if (cam2) emitEvent('camera_state_update', cam2);
                }}
            }})();
        </script>""")
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
            return dict(args.get("detail", args))
        if isinstance(args, list) and args:
            return args[0] if isinstance(args[0], dict) else {}
        return {}

    ui.on("map_click", lambda e: _on_map_click_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("path_point_moved", lambda e: _on_point_moved_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("path_freehand_complete", lambda e: _on_freehand_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("path_remove_point", lambda e: _on_remove_point_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("path_handle_moved", lambda e: _on_handle_moved_sync(
        state, _extract_detail(e), panel,
    ))
    ui.on("camera_state_update", lambda e: _on_camera_update(
        state, _extract_detail(e), panel,
    ))


def _on_camera_update(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle camera state update from JS (used after project load).

    Args:
        state: Shared application state.
        detail: Camera state dict with observer_* keys.
        panel: Bottom panel to refresh.
    """
    state.last_camera.update(detail)
    refresh_overlay(state)


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

    az = float(detail["ra"])   # toWorld() returns Az/Alt, not true RA/Dec
    alt = float(detail["dec"])
    ra, dec = _convert_azalt(az, alt, detail)
    _store_observer(state, detail)
    before = state.project.path.model_dump_json()
    cp = ControlPoint(ra=ra, dec=dec)
    state.project.path.control_points.append(cp)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    refresh_overlay(state)
    _auto_save(state)



def _on_point_moved_sync(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle a control point being dragged to a new position (sync).

    Args:
        state: Shared application state.
        detail: Event detail with index, ra, dec.
        panel: Bottom panel to refresh.
    """
    idx = detail.get("index", 0)
    cps = state.project.path.control_points
    if 0 <= idx < len(cps):
        az = float(detail["ra"])
        alt = float(detail["dec"])
        ra, dec = _convert_azalt(az, alt, detail)
        _store_observer(state, detail)
        before = state.project.path.model_dump_json()
        cps[idx].ra = ra
        cps[idx].dec = dec
        after = state.project.path.model_dump_json()
        state.undo_stack.push(before, after)
        state.update_capture_points()
        panel.refresh()
        refresh_overlay(state)
        _auto_save(state)


def _on_freehand_sync(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle freehand stroke completion by fitting a spline (sync).

    Args:
        state: Shared application state.
        detail: Event detail with points list of {ra, dec}.
        panel: Bottom panel to refresh.
    """
    raw_points = detail.get("points", [])
    if len(raw_points) < 2:
        return
    _store_observer(state, detail)
    before = state.project.path.model_dump_json()
    tuples = [
        _convert_azalt(p["ra"], p["dec"], detail) for p in raw_points
    ]
    simplified = rdp_simplify(tuples, epsilon=0.1)
    if len(simplified) < 2:
        return
    cps = fit_bezier_to_points(simplified)
    state.project.path = SplinePath(control_points=cps)
    after = state.project.path.model_dump_json()
    state.undo_stack.push(before, after)
    state.update_capture_points()
    panel.refresh()
    refresh_overlay(state)
    _auto_save(state)


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
    refresh_overlay(state)
    _auto_save(state)


def _on_handle_moved_sync(
    state: AppState,
    detail: dict[str, Any],
    panel: BottomPanelComponent,
) -> None:
    """Handle a Bezier handle being dragged to a new position (sync).

    Args:
        state: Shared application state.
        detail: Event detail with pointIndex, handleType, ra, dec.
        panel: Bottom panel to refresh.
    """
    idx = detail.get("pointIndex", 0)
    handle_type = detail.get("handleType", "out")
    cps = state.project.path.control_points
    if 0 <= idx < len(cps):
        az = float(detail["ra"])
        alt = float(detail["dec"])
        ra, dec = _convert_azalt(az, alt, detail)
        _store_observer(state, detail)
        before = state.project.path.model_dump_json()
        coord = Coordinate(ra=ra, dec=dec)
        if handle_type == "in":
            cps[idx].handle_in = coord
        else:
            cps[idx].handle_out = coord
        after = state.project.path.model_dump_json()
        state.undo_stack.push(before, after)
        state.update_capture_points()
        panel.refresh()
        refresh_overlay(state)
        _auto_save(state)


def _convert_azalt(
    az: float,
    alt: float,
    detail: dict[str, Any],
) -> tuple[float, float]:
    """Convert Az/Alt from JS toWorld() to true RA/Dec.

    Args:
        az: Azimuth in degrees from JS overlay.
        alt: Altitude in degrees from JS overlay.
        detail: Event detail dict with observer_* keys.

    Returns:
        Tuple of (ra, dec) in degrees (J2000/ICRS).
    """
    obs_lat = float(detail.get("observer_lat", 0))
    obs_lon = float(detail.get("observer_lon", 0))
    obs_utc = float(detail.get("observer_utc", 0))
    return azalt_to_radec(az, alt, obs_lat, obs_lon, obs_utc)


def _store_observer(
    state: AppState,
    detail: dict[str, Any],
) -> None:
    """Persist observer data in last_camera for overlay refresh.

    Args:
        state: Shared application state.
        detail: Event detail dict with observer_* keys.
    """
    state.last_camera["observer_lat"] = float(
        detail.get("observer_lat", 0),
    )
    state.last_camera["observer_lon"] = float(
        detail.get("observer_lon", 0),
    )
    state.last_camera["observer_utc"] = float(
        detail.get("observer_utc", 0),
    )


