"""Main page layout composing toolbar, map area, and bottom panel."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nicegui import app, ui

from src.app_state import AppState
from src.models.freehand import compute_handles, fit_bezier_to_points, rdp_simplify
from src.models.project import ControlPoint, Coordinate, Project, SplinePath
from src.starmap.engine import StarMap
from src.starmap.projection import azalt_to_radec
from src.ui.bottom_panel import BottomPanelComponent
from src.ui.capture_view import CaptureViewComponent
from src.ui.overlay_sync import refresh_overlay
from src.ui.toolbar import ToolbarComponent

_HEAD_CSS = (
    "<style>"
    "body{margin:0;overflow:hidden}"
    " .main-layout{position:relative;width:100%;height:100vh;overflow:hidden}"
    " .map-container{position:absolute;top:48px;left:0;right:0;bottom:0;"
    "background:#0a0a19;overflow:hidden}"
    " .map-container>div{height:100%!important}"
    " .map-container>div>div{height:100%!important}"
    " .map-container canvas{width:100%!important;height:100%!important}"
    " .bottom-panel{position:absolute;left:0;right:0;bottom:0;"
    "z-index:10;max-height:60vh;overflow-y:auto;"
    "background:rgba(30,30,30,0.95);backdrop-filter:blur(4px)}"
    "</style>"
)


def _auto_save(state: AppState) -> None:
    """Save project to session storage and (if located) to disk manifest.

    Session storage stays as a fallback for the not-yet-located state.
    Once ``project_dir`` is set, the disk manifest is the source of truth
    and is rewritten on every edit; format mirrors
    ``CaptureController._save_manifest`` (``indent=2``) so opening the
    project mid-edit doesn't churn the file.
    """
    log = logging.getLogger("starmap")
    data = state.project.model_dump_json()
    app.storage.user["project"] = data
    app.storage.user["project_dir"] = (
        str(state.project_dir) if state.project_dir else None
    )
    if state.project_dir is not None:
        try:
            (state.project_dir / "manifest.json").write_text(
                state.project.model_dump_json(indent=2),
            )
        except OSError:
            log.warning(
                "Manifest write to %s failed",
                state.project_dir, exc_info=True,
            )
    log.info(
        "Auto-saved project (%d control points, %d bytes, project_dir=%s)",
        len(state.project.path.control_points), len(data),
        state.project_dir,
    )


def _restore_state_from_storage(state: AppState, storage: Any) -> None:
    """Restore ``state.project`` + ``state.project_dir`` from a storage dict.

    Order matters (see issue #147): when a project is located on disk
    (``opened_project_dir`` / ``project_dir`` in storage) AND its manifest
    exists, the disk manifest is the source of truth — because
    ``CaptureController._save_manifest`` writes ONLY to disk during a
    capture run, so the session-storage snapshot is stale after a run
    completes. Without a located project we fall back to the session-
    storage snapshot exactly like before.

    Args:
        state: The fresh ``AppState`` to populate.
        storage: A dict-like (e.g. ``app.storage.user``) holding the
            ``project`` JSON snapshot, the ``project_dir`` binding and
            (legacy) ``opened_project_dir``.
    """
    log = logging.getLogger("starmap")
    # Backward compat: older sessions persisted the binding under
    # ``opened_project_dir``; read it as fallback but only write the new
    # ``project_dir`` key going forward so the old one decays naturally.
    opened_dir = (
        storage.get("project_dir")
        or storage.get("opened_project_dir")
    )

    if opened_dir:
        p = Path(opened_dir)
        manifest_path = p / "manifest.json"
        if manifest_path.exists():
            # Disk manifest is authoritative for located projects — the
            # capture controller writes here directly, bypassing session
            # storage, so trusting the snapshot would resurrect a
            # pre-capture view of the world (#147).
            try:
                state.project = Project.model_validate_json(
                    manifest_path.read_text(),
                )
                state.project_dir = p
                log.info(
                    "Restored project from disk manifest %s "
                    "(%d capture points)",
                    manifest_path, len(state.project.capture_points),
                )
            except Exception:  # noqa: BLE001
                log.warning(
                    "Failed to load manifest from %s — continuing with "
                    "default state", manifest_path, exc_info=True,
                )
                state.project_dir = p
            else:
                try:
                    report = state.project.reconcile_with_disk(p)
                    if report.removed_count:
                        log.info(
                            "Reconciled %d missing frame(s) against %s",
                            report.removed_count, p,
                        )
                except Exception:  # noqa: BLE001
                    log.warning(
                        "Reconcile on restore failed for %s",
                        p, exc_info=True,
                    )
            return

        # Located binding exists but the directory/manifest is gone —
        # clear the stale binding and fall through to the session-storage
        # fallback so the user still gets a usable state.
        storage["project_dir"] = None
        log.info("Remembered project dir %s is gone — clearing binding", p)

    # Unlocated project: session-storage snapshot is the only thing we have.
    saved = storage.get("project")
    if saved:
        log.info(
            "Restoring project from session storage (%d bytes)", len(saved),
        )
        try:
            state.load_project_from_json(saved)
            log.info(
                "Restored %d control points",
                len(state.project.path.control_points),
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "Failed to restore project from storage", exc_info=True,
            )
    else:
        log.info("No saved project in storage")


def create_layout() -> None:
    """Build the full-page layout with toolbar, map, and panel."""
    state = AppState()
    _restore_state_from_storage(state, app.storage.user)

    # Welcome nudge: if neither a project nor a located directory was
    # restored, the user lands on an empty "Untitled" canvas with the
    # path-mutating tools greyed out (see toolbar gating). The greyed
    # tools are the primary visual cue; this notify is the backup hint
    # so the user knows what to do next. Non-modal, persistent until
    # dismissed.
    if state.project_dir is None and not state.project.path.control_points:
        ui.notify(
            "Neues Projekt anlegen oder Projekt öffnen",
            type="info",
            timeout=0,
            close_button=True,
        )

    capture_view = CaptureViewComponent()

    callbacks = _build_callbacks(state, capture_view)
    ui.add_head_html(_HEAD_CSS)

    with ui.element("div").classes("main-layout"):
        with ui.row().classes("w-full").style("position:absolute;top:0;left:0;right:0;z-index:20"):
            toolbar = ToolbarComponent(state, callbacks)
            toolbar.render()
        with ui.element("div").style(
            "position:absolute;top:48px;left:0;right:0;z-index:15"
        ):
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
            "object_selected",
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

    # Wire the panel refresh + persistence into the toolbar callbacks (same
    # dict the toolbar holds by reference) so loading/opening a project both
    # repopulates the Capture Points table AND persists the new state —
    # including the project_dir binding — so it survives a reload. The
    # persistent project header is updated through the same hook so the
    # caption tracks ``state.project_dir`` whenever a project is loaded
    # or opened.
    def _on_project_loaded() -> None:
        panel.refresh()
        # Single hook on the toolbar that refreshes the persistent header
        # AND the tool-gating state (Pan stays on; path-mutating tools
        # follow ``state.project_dir is not None``). Keeps the callback
        # wiring stable when we add more project-state-dependent UI.
        toolbar.refresh_project_state()
        _auto_save(state)

    callbacks["project_loaded"] = _on_project_loaded

    # Rewire the start_capture callback now that ``panel`` exists so the
    # capture view can refresh the bottom panel live during the run (#147
    # Fix B). ``_build_callbacks`` ran before ``panel`` was created.
    async def _on_start_capture_with_panel() -> None:
        await _start_capture(state, capture_view, panel)

    callbacks["start_capture"] = _on_start_capture_with_panel

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

    async def on_open_render() -> None:
        from src.config import settings as cfg

        port = cfg.port + 1
        js = f'window.open("http://"+window.location.hostname+":{port}","_blank")'
        ui.run_javascript(js)

    return {
        "start_capture": on_start_capture,
        "open_render": on_open_render,
    }


async def _start_capture(
    state: AppState,
    capture_view: CaptureViewComponent,
    panel: BottomPanelComponent | None = None,
) -> None:
    """Start the capture sequence.

    Args:
        state: Shared application state.
        capture_view: Capture view to show progress.
        panel: Optional bottom panel; threaded into the capture view so
            the capture-points table refreshes live during the run
            (issue #147 Fix B).
    """
    try:
        controller = state.start_capture()
        print(  # noqa: T201
            f"Starting capture: {len(controller.project.capture_points)} points,"
            f" client={type(state.indi_client).__name__}",
        )
        capture_view.start(controller, state, panel)
        await controller.run()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("capture").error(
            "Capture failed: %s: %s", type(exc).__name__, exc,
        )
        ui.notify(f"Capture error: {exc}", type="negative")
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
    ui.on("object_selected", lambda e: ui.notify(
        _extract_detail(e).get("name", ""),
        type="info", timeout=3000,
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
    state.project.path.control_points = compute_handles(
        state.project.path.control_points,
    )
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
        # Clear auto-computed handles on the moved point so they get
        # recomputed; manually-set handles on *other* points are preserved.
        cps[idx].handle_in = None
        cps[idx].handle_out = None
        state.project.path.control_points = compute_handles(cps)
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
    state.project.path.control_points = compute_handles(cps)
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


