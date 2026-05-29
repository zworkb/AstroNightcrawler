"""Tests for AppState.open_project + multi-night resume (issue #136)."""

from pathlib import Path

import pytest

from src.app_state import AppState, _resolve_output_dir
from src.capture.controller import CaptureState
from src.indi.mock import MockINDIClient

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_manifest_v1.json"


def _make_project_dir(tmp_path: Path, *, drop: set[str] | None = None) -> Path:
    """Create a temp project dir with the legacy manifest + fake FITS files.

    Args:
        tmp_path: Pytest temp directory.
        drop: Filenames to deliberately NOT create on disk (simulating a
            file deleted outside the app), to exercise reconcile.

    Returns:
        Path to the created project directory.
    """
    drop = drop or set()
    proj_dir = tmp_path / "deneb"
    proj_dir.mkdir()
    (proj_dir / "manifest.json").write_text(FIXTURE.read_text())
    # Files referenced by the captured points in the fixture.
    for fname in ("seq_0001_001.fits", "seq_0004_001.fits", "seq_0004_002.fits"):
        if fname not in drop:
            (proj_dir / fname).write_bytes(b"fake fits")
    return proj_dir


def test_open_project_loads_migrates_and_binds(tmp_path: Path) -> None:
    """Opening a legacy project migrates it and binds the output dir."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()

    report = state.open_project(proj_dir)

    # Migration ran: legacy v1.0 -> 2.0, files -> frames.
    assert state.project.version == "2.0"
    assert state.project.project == "deneb"
    # Output is bound to the opened directory.
    assert state.opened_project_dir == proj_dir
    # All files present -> nothing reconciled away.
    assert report.removed_count == 0

    by_index = {p.index: p for p in state.project.capture_points}
    assert by_index[0].good_count == 1  # captured single
    assert by_index[0].is_complete
    assert not by_index[1].is_complete  # pending
    assert by_index[2].skipped  # skipped stays skipped
    assert by_index[3].good_count == 2 and by_index[3].is_complete


def test_open_project_reconciles_missing_files(tmp_path: Path) -> None:
    """A deleted FITS file is dropped and the point becomes incomplete."""
    proj_dir = _make_project_dir(tmp_path, drop={"seq_0004_002.fits"})
    state = AppState()

    report = state.open_project(proj_dir)

    assert report.removed_count == 1
    assert 3 in report.affected_points
    pt3 = next(p for p in state.project.capture_points if p.index == 3)
    # target_subs was 2 (migrated from 2 files); one file gone -> incomplete.
    assert pt3.good_count == 1
    assert not pt3.is_complete


def test_open_project_missing_manifest_raises(tmp_path: Path) -> None:
    """Opening a directory without manifest.json raises a clear error."""
    empty = tmp_path / "empty"
    empty.mkdir()
    state = AppState()
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        state.open_project(empty)


def test_resolve_output_dir_uses_opened_dir(tmp_path: Path) -> None:
    """When a project is opened, capture resolves to that directory."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()
    state.open_project(proj_dir)

    resolved = _resolve_output_dir(state.project, state.opened_project_dir)
    assert resolved == proj_dir


@pytest.mark.asyncio
async def test_multi_night_resume_run(tmp_path: Path) -> None:
    """Open a mixed project, run capture: new frames are night 2, complete
    points are skipped, skipped points stay skipped, output stays in place."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()
    state.open_project(proj_dir)
    client = MockINDIClient()
    await client.connect("localhost")
    state.indi_client = client

    ctrl = state.start_capture()
    # next_night() over the night-1 captured frames -> 2.
    assert ctrl.night == 2
    # Capture writes into the opened directory, not a new one.
    assert ctrl.output_dir == proj_dir

    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED

    by_index = {p.index: p for p in state.project.capture_points}
    # Point 0 was complete (night 1) -> untouched, no night-2 frame.
    assert by_index[0].good_count == 1
    assert all(f.night == 1 for f in by_index[0].frames)
    # Point 1 was pending -> filled this run with a night-2 frame.
    assert by_index[1].good_count == 1
    assert by_index[1].frames[0].night == 2
    # Point 2 was skipped -> stays skipped, never captured.
    assert by_index[2].skipped
    assert by_index[2].frames == []
    # Point 3 was complete (two night-1 frames) -> untouched.
    assert by_index[3].good_count == 2
    assert all(f.night == 1 for f in by_index[3].frames)
