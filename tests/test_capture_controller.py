"""Tests for the capture controller state machine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.capture.controller import CaptureController, CaptureState
from src.indi.mock import MockINDIClient
from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    INDIConfig,
    Project,
    SplinePath,
)


def _make_project(
    num_points: int = 3,
    exposure: float = 1.0,
    exposures_per_point: int = 1,
) -> Project:
    """Build a minimal test project with N capture points.

    Args:
        num_points: Number of capture points to generate.
        exposure: Exposure time in seconds.
        exposures_per_point: Number of exposures per capture point.

    Returns:
        A Project instance ready for testing.
    """
    control_pts = [
        ControlPoint(ra=0.0, dec=0.0),
        ControlPoint(ra=10.0, dec=10.0),
    ]
    path = SplinePath(control_points=control_pts)
    settings = CaptureSettings(
        exposure_seconds=exposure,
        exposures_per_point=exposures_per_point,
    )
    capture_points = [
        CapturePoint(ra=float(i), dec=float(i), index=i)
        for i in range(num_points)
    ]
    return Project(
        project="test",
        path=path,
        capture_settings=settings,
        capture_points=capture_points,
        indi=INDIConfig(),
    )


async def _make_controller(
    tmp_path: Path,
    num_points: int = 3,
    exposure: float = 1.0,
    exposures_per_point: int = 1,
    fail_slew_count: int = 0,
    fail_capture_count: int = 0,
) -> tuple[CaptureController, MockINDIClient, Project]:
    """Build a controller with a connected mock client.

    Returns:
        Tuple of (controller, mock_client, project).
    """
    project = _make_project(num_points, exposure, exposures_per_point)
    client = MockINDIClient(
        fail_slew_count=fail_slew_count,
        fail_capture_count=fail_capture_count,
    )
    await client.connect("localhost")
    ctrl = CaptureController(project, client, tmp_path)
    return ctrl, client, project


@pytest.mark.asyncio
async def test_initial_state_is_idle(tmp_path: Path) -> None:
    """Controller starts in IDLE state."""
    ctrl, _, _ = await _make_controller(tmp_path)
    assert ctrl.state == CaptureState.IDLE


@pytest.mark.asyncio
async def test_run_full_sequence(tmp_path: Path) -> None:
    """All points captured and files written on full run."""
    ctrl, _, project = await _make_controller(tmp_path, num_points=3)
    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED
    for pt in project.capture_points:
        assert pt.status == "captured"
        assert len(pt.files) == 1


@pytest.mark.asyncio
async def test_pause_and_resume(tmp_path: Path) -> None:
    """Pausing halts progress; resuming completes the sequence."""
    ctrl, _, project = await _make_controller(tmp_path, num_points=5)

    async def pause_then_resume() -> None:
        await asyncio.sleep(0.05)
        ctrl.pause()
        assert ctrl.state == CaptureState.PAUSED
        await asyncio.sleep(0.05)
        ctrl.resume()

    await asyncio.gather(ctrl.run(), pause_then_resume())
    assert ctrl.state == CaptureState.COMPLETED


@pytest.mark.asyncio
async def test_cancel(tmp_path: Path) -> None:
    """Cancelling stops the sequence with CANCELLED state."""
    ctrl, _, _ = await _make_controller(tmp_path, num_points=10)

    async def do_cancel() -> None:
        await asyncio.sleep(0.05)
        ctrl.cancel()

    await asyncio.gather(ctrl.run(), do_cancel())
    assert ctrl.state == CaptureState.CANCELLED


@pytest.mark.asyncio
async def test_multi_exposure(tmp_path: Path) -> None:
    """Multiple exposures per point produce multiple files."""
    ctrl, _, project = await _make_controller(
        tmp_path, num_points=2, exposures_per_point=3
    )
    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED
    for pt in project.capture_points:
        assert len(pt.files) == 3


@pytest.mark.asyncio
async def test_resume_skips_captured(tmp_path: Path) -> None:
    """Pre-captured points are skipped during the run."""
    ctrl, _, project = await _make_controller(tmp_path, num_points=3)
    project.capture_points[0].status = "captured"
    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED
    assert project.capture_points[0].files == []
    assert project.capture_points[1].status == "captured"
    assert project.capture_points[2].status == "captured"


@pytest.mark.asyncio
async def test_skip_point(tmp_path: Path) -> None:
    """Skipping a point after slew failure advances past it."""
    ctrl, client, project = await _make_controller(
        tmp_path, num_points=3, fail_slew_count=3
    )

    async def handle_pause() -> None:
        await asyncio.sleep(0.05)
        if ctrl.state == CaptureState.PAUSED:
            ctrl.skip_point()
            client.fail_slew_count = 0
            client._slew_attempts = 0
            ctrl.resume()

    await asyncio.gather(ctrl.run(), handle_pause())
    assert project.capture_points[0].status == "skipped"


@pytest.mark.asyncio
async def test_slew_retry_on_failure(tmp_path: Path) -> None:
    """A single slew failure is retried and the point is captured."""
    ctrl, _, project = await _make_controller(
        tmp_path, num_points=1, fail_slew_count=1
    )
    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED
    assert project.capture_points[0].status == "captured"


@pytest.mark.asyncio
async def test_manifest_written_on_completion(tmp_path: Path) -> None:
    """manifest.json is created in output_dir on completion."""
    ctrl, _, _ = await _make_controller(tmp_path, num_points=2)
    await ctrl.run()
    manifest = tmp_path / "manifest.json"
    assert manifest.exists()
    data = json.loads(manifest.read_text())
    assert data["project"] == "test"


@pytest.mark.asyncio
async def test_estimated_remaining(tmp_path: Path) -> None:
    """Estimated remaining seconds reflects uncaptured points."""
    ctrl, _, project = await _make_controller(
        tmp_path, num_points=4, exposure=10.0
    )
    expected = 4 * (5.0 + 3.0 + 10.0 * 1)
    assert ctrl.estimated_remaining_seconds == expected
    project.capture_points[0].status = "captured"
    expected_after = 3 * (5.0 + 3.0 + 10.0 * 1)
    assert ctrl.estimated_remaining_seconds == expected_after
