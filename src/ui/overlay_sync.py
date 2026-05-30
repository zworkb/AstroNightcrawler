"""Shared helper to serialize and push path data to the JS overlay."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nicegui import ui

from src.starmap.projection import radec_to_azalt

if TYPE_CHECKING:
    from src.app_state import AppState
    from src.models.project import CapturePoint


def refresh_overlay(state: AppState) -> None:
    """Serialize path data and send to the JS overlay (sync).

    Args:
        state: Application state with path and capture points.
    """
    cp_data = _serialize_control_points(state)
    cap_data = _serialize_capture_points(state)
    js = (
        "window.pathOverlayBridge?.update("
        f"{json.dumps(cp_data)}, {json.dumps(cap_data)})"
    )
    ui.run_javascript(js)


def _observer_from_camera(
    cam: dict[str, Any],
) -> tuple[float, float, float]:
    """Extract observer params from camera state.

    Args:
        cam: Camera state dict with observer_* keys.

    Returns:
        Tuple of (lat, lon, utc) as floats.
    """
    return (
        float(cam.get("observer_lat", 0)),
        float(cam.get("observer_lon", 0)),
        float(cam.get("observer_utc", 0)),
    )


def _to_azalt(
    ra: float,
    dec: float,
    obs: tuple[float, float, float],
) -> tuple[float, float]:
    """Convert RA/Dec to Az/Alt using observer params.

    Args:
        ra: Right ascension in degrees.
        dec: Declination in degrees.
        obs: Tuple of (lat, lon, utc).

    Returns:
        Tuple of (az, alt) in degrees.
    """
    return radec_to_azalt(ra, dec, obs[0], obs[1], obs[2])


def _serialize_control_points(
    state: AppState,
) -> list[dict[str, Any]]:
    """Serialize control points for the JS overlay.

    Converts stored RA/Dec to Az/Alt for JS toScreen().
    """
    obs = _observer_from_camera(state.last_camera)
    result: list[dict[str, Any]] = []
    for cp in state.project.path.control_points:
        az, alt = _to_azalt(cp.ra, cp.dec, obs)
        entry: dict[str, Any] = {
            "ra": az,
            "dec": alt,
            "label": cp.label,
            "handleIn": None,
            "handleOut": None,
        }
        if cp.handle_in:
            h_az, h_alt = _to_azalt(
                cp.handle_in.ra, cp.handle_in.dec, obs,
            )
            entry["handleIn"] = {"ra": h_az, "dec": h_alt}
        if cp.handle_out:
            h_az, h_alt = _to_azalt(
                cp.handle_out.ra, cp.handle_out.dec, obs,
            )
            entry["handleOut"] = {"ra": h_az, "dec": h_alt}
        result.append(entry)
    return result


def display_status(p: CapturePoint) -> str:
    """Derive the on-map color category for a capture point.

    Skipped takes precedence over completeness so a skipped point that
    happens to have frames still reads as grey on the map.

    Returns one of:
        - "skipped"  -> grey (point.skipped is True)
        - "complete" -> green (good_count >= target_subs)
        - "partial"  -> yellow (0 < good_count < target_subs)
        - "open"     -> blue (good_count == 0)
    """
    if p.skipped:
        return "skipped"
    if p.is_complete:
        return "complete"
    if p.good_count > 0:
        return "partial"
    return "open"


def _serialize_capture_points(
    state: AppState,
) -> list[dict[str, Any]]:
    """Serialize capture points for the JS overlay.

    Converts stored RA/Dec to Az/Alt for JS toScreen().  Emits both the
    transient ``status`` (Literal field on the model, kept for backwards
    compat) and a derived ``display_status`` used by ``path_overlay.js``
    to color the dot (see ``display_status``).
    """
    obs = _observer_from_camera(state.last_camera)
    result: list[dict[str, Any]] = []
    for p in state.project.capture_points:
        az, alt = _to_azalt(p.ra, p.dec, obs)
        result.append({
            "ra": az,
            "dec": alt,
            "index": p.index,
            "status": p.status,
            "display_status": display_status(p),
        })
    return result
