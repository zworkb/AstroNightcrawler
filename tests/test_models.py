"""Tests for project data models."""

from datetime import datetime
from pathlib import Path

import pytest

from src.models.project import (
    CapturedFrame,
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Coordinate,
    INDIConfig,
    Project,
    ReconcileReport,
    SplinePath,
)

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_manifest_v1.json"


class TestCoordinate:
    def test_valid_coordinate(self) -> None:
        c = Coordinate(ra=10.684, dec=41.269)
        assert c.ra == 10.684
        assert c.dec == 41.269

    def test_ra_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            Coordinate(ra=400.0, dec=0.0)

    def test_dec_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            Coordinate(ra=0.0, dec=100.0)


class TestControlPoint:
    def test_with_handles(self) -> None:
        cp = ControlPoint(
            ra=10.684, dec=41.269, label="M31",
            handle_out=Coordinate(ra=11.5, dec=41.0),
        )
        assert cp.label == "M31"
        assert cp.handle_out is not None
        assert cp.handle_out.ra == 11.5

    def test_without_handles(self) -> None:
        cp = ControlPoint(ra=14.053, dec=38.683)
        assert cp.handle_in is None
        assert cp.handle_out is None


class TestSplinePath:
    def test_allows_empty_and_single_point_for_editing(self) -> None:
        """Editing allows 0 or 1 points; capture validates separately."""
        empty = SplinePath(control_points=[])
        assert len(empty.control_points) == 0
        single = SplinePath(control_points=[ControlPoint(ra=10.0, dec=40.0)])
        assert len(single.control_points) == 1

    def test_defaults(self) -> None:
        path = SplinePath(control_points=[
            ControlPoint(ra=10.0, dec=40.0),
            ControlPoint(ra=20.0, dec=30.0),
        ])
        assert path.spline_type == "cubic_bezier"
        assert path.coordinate_frame == "J2000"


class TestCaptureSettings:
    def test_defaults(self) -> None:
        cs = CaptureSettings()
        assert cs.point_spacing_deg == 0.5
        assert cs.exposure_seconds == 30.0
        assert cs.binning == 1

    def test_exposures_per_point_default(self) -> None:
        cs = CaptureSettings()
        assert cs.exposures_per_point == 1

    def test_offset_default(self) -> None:
        cs = CaptureSettings()
        assert cs.offset == 0

    def test_gain_default(self) -> None:
        cs = CaptureSettings()
        assert cs.gain == 0

    def test_negative_gain_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(gain=-1)

    def test_invalid_binning_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(binning=5)

    def test_invalid_exposure_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(exposure_seconds=-1.0)

    def test_invalid_spacing_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(point_spacing_deg=0.0)


class TestCapturePoint:
    def test_pending_by_default(self) -> None:
        cp = CapturePoint(index=0, ra=10.684, dec=41.269)
        assert cp.status == "pending"
        assert cp.frames == []
        assert cp.target_subs == 1
        assert cp.skipped is False
        assert cp.captured_at is None

    def test_good_count_counts_only_good(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0,
            frames=[
                CapturedFrame(filename="a.fits", status="good"),
                CapturedFrame(filename="b.fits", status="rejected"),
                CapturedFrame(filename="c.fits", status="good"),
            ],
        )
        assert cp.good_count == 2

    def test_is_complete_when_good_count_reaches_target(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=2,
            frames=[
                CapturedFrame(filename="a.fits", status="good"),
                CapturedFrame(filename="b.fits", status="good"),
            ],
        )
        assert cp.is_complete

    def test_incomplete_when_rejected_frames_do_not_count(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=2,
            frames=[
                CapturedFrame(filename="a.fits", status="good"),
                CapturedFrame(filename="b.fits", status="rejected"),
            ],
        )
        assert cp.good_count == 1
        assert not cp.is_complete

    def test_skipped_is_always_complete(self) -> None:
        cp = CapturePoint(index=0, ra=0.0, dec=0.0, target_subs=5, skipped=True)
        assert cp.is_complete

    def test_filename_index_zero(self) -> None:
        cp = CapturePoint(index=0, ra=10.0, dec=40.0)
        assert cp.filename(exposure=1) == "seq_0001_001.fits"

    def test_filename_multi_exposure(self) -> None:
        cp = CapturePoint(index=2, ra=10.0, dec=40.0)
        assert cp.filename(exposure=3) == "seq_0003_003.fits"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError):
            CapturePoint(index=0, ra=0.0, dec=0.0, status="invalid")

    def test_negative_index_raises(self) -> None:
        with pytest.raises(ValueError):
            CapturePoint(index=-1, ra=0.0, dec=0.0)

    def test_status_skipped_valid(self) -> None:
        cp = CapturePoint(index=0, ra=0.0, dec=0.0, status="skipped")
        assert cp.status == "skipped"

    def test_status_captured_valid(self) -> None:
        cp = CapturePoint(index=0, ra=0.0, dec=0.0, status="captured")
        assert cp.status == "captured"


class TestProject:
    def test_roundtrip_json(self, tmp_path: Path) -> None:
        project = Project(
            project="test-sweep",
            path=SplinePath(control_points=[
                ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                ControlPoint(ra=20.0, dec=30.0, handle_in=Coordinate(ra=19.0, dec=31.0)),
            ]),
            capture_settings=CaptureSettings(exposure_seconds=60.0, gain=120),
            indi=INDIConfig(telescope="EQMod Mount", camera="ZWO ASI294MC Pro"),
        )
        filepath = tmp_path / "project.json"
        filepath.write_text(project.model_dump_json(indent=2))
        loaded = Project.model_validate_json(filepath.read_text())
        assert loaded.version == "2.0"
        assert isinstance(loaded.created, datetime)
        assert loaded.project == "test-sweep"
        assert loaded.capture_settings.exposure_seconds == 60.0
        assert loaded.path.control_points[0].handle_out is not None


class TestLegacyMigration:
    """Backward-compat: v1.0 manifests (files schema) migrate transparently."""

    def _load(self) -> Project:
        return Project.model_validate_json(FIXTURE.read_text())

    def test_captured_point_becomes_good_frames_and_complete(self) -> None:
        proj = self._load()
        pt = proj.capture_points[0]  # captured w/ one file
        assert [f.filename for f in pt.frames] == ["seq_0001_001.fits"]
        assert all(f.status == "good" for f in pt.frames)
        assert pt.good_count == 1
        assert pt.target_subs == 1
        assert pt.is_complete

    def test_captured_point_with_multiple_files(self) -> None:
        proj = self._load()
        pt = proj.capture_points[3]  # captured w/ two files
        assert pt.good_count == 2
        assert pt.target_subs == 2
        assert pt.is_complete

    def test_pending_point_is_empty_and_incomplete(self) -> None:
        proj = self._load()
        pt = proj.capture_points[1]
        assert pt.frames == []
        assert pt.good_count == 0
        assert not pt.is_complete
        assert pt.skipped is False

    def test_skipped_point_sets_skipped_flag(self) -> None:
        proj = self._load()
        pt = proj.capture_points[2]
        assert pt.skipped is True
        assert pt.is_complete  # skipped counts as done

    def test_no_false_incompleteness_for_captured_points(self) -> None:
        """The critical regression: captured legacy points stay complete."""
        proj = self._load()
        captured = [proj.capture_points[0], proj.capture_points[3]]
        assert all(pt.is_complete for pt in captured)

    def test_captured_at_is_datetime_after_load(self) -> None:
        proj = self._load()
        frame = proj.capture_points[0].frames[0]
        assert isinstance(frame.captured_at, datetime)
        assert isinstance(proj.created, datetime)

    def test_round_trip_preserves_state_and_bumps_version(self) -> None:
        proj = self._load()
        dumped = proj.model_dump_json()
        reloaded = Project.model_validate_json(dumped)
        # Identical state by index
        for a, b in zip(proj.capture_points, reloaded.capture_points, strict=True):
            assert a.good_count == b.good_count
            assert a.is_complete == b.is_complete
            assert a.skipped == b.skipped
            assert [f.filename for f in a.frames] == [f.filename for f in b.frames]
        # Re-dumped JSON carries the new version and ISO timestamp string
        import json as _json
        raw = _json.loads(dumped)
        assert raw["version"] == "2.0"
        cap0 = raw["capture_points"][0]
        assert "files" not in cap0
        assert isinstance(cap0["frames"][0]["captured_at"], str)


class TestReconcileWithDisk:
    """reconcile_with_disk drops frames whose FITS file is gone."""

    def _project_with_frames(self, tmp_path: Path) -> Project:
        names = ["seq_0001_001.fits", "seq_0001_002.fits", "seq_0001_003.fits"]
        for n in names:
            (tmp_path / n).write_bytes(b"fake")
        return Project(
            project="recon",
            path=SplinePath(control_points=[]),
            capture_points=[
                CapturePoint(
                    index=0, ra=0.0, dec=0.0, target_subs=3,
                    frames=[CapturedFrame(filename=n, status="good") for n in names],
                ),
            ],
        )

    def test_drops_missing_files_and_reports(self, tmp_path: Path) -> None:
        proj = self._project_with_frames(tmp_path)
        (tmp_path / "seq_0001_002.fits").unlink()
        report = proj.reconcile_with_disk(tmp_path)
        assert report.removed_count == 1
        assert report.affected_points == [0]
        assert proj.capture_points[0].good_count == 2

    def test_complete_point_becomes_incomplete(self, tmp_path: Path) -> None:
        proj = self._project_with_frames(tmp_path)
        assert proj.capture_points[0].is_complete
        (tmp_path / "seq_0001_001.fits").unlink()
        proj.reconcile_with_disk(tmp_path)
        assert not proj.capture_points[0].is_complete

    def test_no_missing_files_is_noop(self, tmp_path: Path) -> None:
        proj = self._project_with_frames(tmp_path)
        report = proj.reconcile_with_disk(tmp_path)
        assert report == ReconcileReport(removed_count=0, affected_points=[])
        assert proj.capture_points[0].good_count == 3


class TestNextNight:
    """next_night() returns the night number for the next capture session."""

    def _project(self, *frames_per_point: list[CapturedFrame]) -> Project:
        return Project(
            project="nights",
            path=SplinePath(control_points=[]),
            capture_points=[
                CapturePoint(index=i, ra=0.0, dec=0.0, frames=list(fr))
                for i, fr in enumerate(frames_per_point)
            ],
        )

    def test_empty_project_starts_at_one(self) -> None:
        proj = self._project()
        assert proj.next_night() == 1

    def test_project_with_no_frames_starts_at_one(self) -> None:
        proj = self._project([], [])
        assert proj.next_night() == 1

    def test_increments_past_max_night(self) -> None:
        # Frames spread across nights 1, 1 and 2 -> next night is 3.
        proj = self._project(
            [
                CapturedFrame(filename="a.fits", night=1),
                CapturedFrame(filename="b.fits", night=1),
            ],
            [CapturedFrame(filename="c.fits", night=2)],
        )
        assert proj.next_night() == 3
