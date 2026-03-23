"""Tests for project data models."""

from pathlib import Path

import pytest

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Coordinate,
    INDIConfig,
    Project,
    SplinePath,
)


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
        assert cp.files == []
        assert cp.captured_at is None

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
        assert loaded.version == "1.0"
        assert loaded.created != ""
        assert loaded.project == "test-sweep"
        assert loaded.capture_settings.exposure_seconds == 60.0
        assert loaded.path.control_points[0].handle_out is not None
