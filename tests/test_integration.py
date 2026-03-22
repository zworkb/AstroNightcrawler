"""Integration tests for full capture workflows."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from src.app_state import AppState
from src.capture.controller import CaptureState
from src.indi.mock import MockINDIClient
from src.models.freehand import fit_bezier_to_points, rdp_simplify
from src.models.project import (
    CaptureSettings,
    ControlPoint,
    Coordinate,
    Project,
    SplinePath,
)


def _make_test_state(tmp_path: Path) -> AppState:
    """Create an AppState with a 2-point path and output_dir under tmp_path.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        A configured AppState for testing.
    """
    state = AppState()
    state.project = Project(
        project="integration-test",
        path=SplinePath(
            control_points=[
                ControlPoint(
                    ra=10.0,
                    dec=20.0,
                    handle_out=Coordinate(ra=11.0, dec=20.5),
                ),
                ControlPoint(
                    ra=12.0,
                    dec=21.0,
                    handle_in=Coordinate(ra=11.5, dec=21.0),
                ),
            ],
        ),
        capture_settings=CaptureSettings(
            exposure_seconds=1.0,
            point_spacing_deg=0.5,
        ),
    )
    return state


async def _connect_client(state: AppState) -> None:
    """Connect the mock INDI client on the state.

    Args:
        state: AppState whose indi_client to connect.
    """
    await state.indi_client.connect("localhost")


async def test_full_capture_workflow(tmp_path: Path) -> None:
    """Full workflow: create project, sample path, capture all, verify files."""
    state = _make_test_state(tmp_path)
    state.update_capture_points()
    assert len(state.project.capture_points) >= 2

    await _connect_client(state)

    output_dir = tmp_path / "output"
    with patch("src.app_state.settings") as mock_settings:
        mock_settings.output_dir = str(output_dir)
        controller = state.start_capture()

    await controller.run()

    assert controller.state == CaptureState.COMPLETED
    for pt in state.project.capture_points:
        assert pt.status == "captured"
        assert len(pt.files) >= 1

    fits_files = list(output_dir.glob("*.fits"))
    assert len(fits_files) == len(state.project.capture_points)

    manifest = output_dir / "manifest.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert data["project"] == "integration-test"


async def test_save_load_roundtrip(tmp_path: Path) -> None:
    """Save and load a project, verifying all fields are preserved."""
    state = AppState()
    state.project = Project(
        project="roundtrip-test",
        path=SplinePath(
            control_points=[
                ControlPoint(
                    ra=5.0,
                    dec=10.0,
                    handle_out=Coordinate(ra=6.0, dec=11.0),
                ),
                ControlPoint(
                    ra=15.0,
                    dec=20.0,
                    handle_in=Coordinate(ra=14.0, dec=19.0),
                    handle_out=Coordinate(ra=16.0, dec=21.0),
                ),
                ControlPoint(
                    ra=25.0,
                    dec=30.0,
                    handle_in=Coordinate(ra=24.0, dec=29.0),
                ),
            ],
        ),
        capture_settings=CaptureSettings(
            exposure_seconds=45.0,
            gain=200,
        ),
    )

    save_path = tmp_path / "project.json"
    state.save_project(save_path)

    loaded_state = AppState()
    loaded_state.load_project(save_path)

    assert loaded_state.project.project == "roundtrip-test"
    assert len(loaded_state.project.path.control_points) == 3
    assert loaded_state.project.capture_settings.exposure_seconds == 45.0
    assert loaded_state.project.capture_settings.gain == 200

    cp1 = loaded_state.project.path.control_points[1]
    assert cp1.handle_in is not None
    assert cp1.handle_in.ra == 14.0
    assert cp1.handle_in.dec == 19.0
    assert cp1.handle_out is not None
    assert cp1.handle_out.ra == 16.0
    assert cp1.handle_out.dec == 21.0


async def test_capture_pause_resume(tmp_path: Path) -> None:
    """Pause mid-capture, verify PAUSED state, resume, verify completion."""
    state = _make_test_state(tmp_path)
    state.project.capture_settings = CaptureSettings(
        exposure_seconds=1.0,
        point_spacing_deg=0.3,
    )
    state.update_capture_points()
    assert len(state.project.capture_points) >= 3

    await _connect_client(state)

    output_dir = tmp_path / "output"
    with patch("src.app_state.settings") as mock_settings:
        mock_settings.output_dir = str(output_dir)
        controller = state.start_capture()

    paused_observed = False

    async def pause_then_resume() -> None:
        nonlocal paused_observed
        await asyncio.sleep(0.05)
        controller.pause()
        await asyncio.sleep(0.01)
        paused_observed = controller.state == CaptureState.PAUSED
        await asyncio.sleep(0.05)
        controller.resume()

    await asyncio.gather(controller.run(), pause_then_resume())

    assert paused_observed
    assert controller.state == CaptureState.COMPLETED
    for pt in state.project.capture_points:
        assert pt.status == "captured"


async def test_capture_with_retry(tmp_path: Path) -> None:
    """Slew failure on first attempt is retried; all points complete."""
    state = _make_test_state(tmp_path)
    state.update_capture_points()
    state.indi_client = MockINDIClient(fail_slew_count=1)
    await _connect_client(state)

    output_dir = tmp_path / "output"
    with patch("src.app_state.settings") as mock_settings:
        mock_settings.output_dir = str(output_dir)
        controller = state.start_capture()

    await controller.run()

    assert controller.state == CaptureState.COMPLETED
    for pt in state.project.capture_points:
        assert pt.status == "captured"


async def test_freehand_to_capture(tmp_path: Path) -> None:
    """Freehand points simplified and fit to bezier, then captured."""
    raw_points: list[tuple[float, float]] = [
        (10.0, 20.0),
        (10.2, 20.1),
        (10.5, 20.3),
        (10.7, 20.2),
        (11.0, 20.5),
        (11.3, 20.4),
        (11.5, 20.6),
        (12.0, 21.0),
    ]

    simplified = rdp_simplify(raw_points, epsilon=0.1)
    assert len(simplified) >= 2

    control_points = fit_bezier_to_points(simplified)
    assert len(control_points) >= 2

    state = AppState()
    state.project = Project(
        project="freehand-test",
        path=SplinePath(control_points=control_points),
        capture_settings=CaptureSettings(
            exposure_seconds=1.0,
            point_spacing_deg=0.5,
        ),
    )
    state.update_capture_points()
    assert len(state.project.capture_points) >= 2

    await _connect_client(state)

    output_dir = tmp_path / "output"
    with patch("src.app_state.settings") as mock_settings:
        mock_settings.output_dir = str(output_dir)
        controller = state.start_capture()

    await controller.run()

    assert controller.state == CaptureState.COMPLETED
    for pt in state.project.capture_points:
        assert pt.status == "captured"

    fits_files = list(output_dir.glob("*.fits"))
    assert len(fits_files) == len(state.project.capture_points)
