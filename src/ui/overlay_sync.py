"""Shared helper to serialize and push path data to the JS overlay."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from nicegui import ui

if TYPE_CHECKING:
    from src.app_state import AppState


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


def _serialize_control_points(
    state: AppState,
) -> list[dict[str, Any]]:
    """Serialize control points for the JS overlay."""
    return [
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


def _serialize_capture_points(
    state: AppState,
) -> list[dict[str, Any]]:
    """Serialize capture points for the JS overlay."""
    return [
        {"ra": p.ra, "dec": p.dec, "index": p.index}
        for p in state.project.capture_points
    ]
