"""Tests for EKOS sequence export."""

from pathlib import Path
from xml.etree.ElementTree import parse as parse_xml

from src.export.ekos import export_sequence
from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Project,
    SplinePath,
)


def _make_test_project() -> Project:
    """Build a test project with 3 capture points.

    Returns:
        A Project with known coordinates and capture settings.
    """
    settings = CaptureSettings(
        exposure_seconds=60.0,
        gain=100,
        binning=2,
        exposures_per_point=5,
        offset=10,
    )
    points = [
        CapturePoint(ra=10.0, dec=20.0, index=0),
        CapturePoint(ra=30.0, dec=40.0, index=1),
        CapturePoint(ra=50.0, dec=60.0, index=2),
    ]
    path = SplinePath(
        control_points=[
            ControlPoint(ra=10.0, dec=20.0),
            ControlPoint(ra=50.0, dec=60.0),
        ]
    )
    return Project(
        project="Test Sequence",
        path=path,
        capture_settings=settings,
        capture_points=points,
    )


class TestEKOSExport:
    """Tests for the EKOS .esq export function."""

    def test_export_produces_file(self, tmp_path: Path) -> None:
        """Export creates a non-empty XML file."""
        out = tmp_path / "test.esq"
        export_sequence(_make_test_project(), out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_export_correct_job_count(self, tmp_path: Path) -> None:
        """Number of Job elements matches capture points."""
        out = tmp_path / "test.esq"
        project = _make_test_project()
        export_sequence(project, out)
        tree = parse_xml(str(out))
        jobs = tree.findall("Job")
        assert len(jobs) == len(project.capture_points)

    def test_export_correct_coordinates(self, tmp_path: Path) -> None:
        """RA/Dec values in XML match capture point coordinates."""
        out = tmp_path / "test.esq"
        project = _make_test_project()
        export_sequence(project, out)
        tree = parse_xml(str(out))
        jobs = tree.findall("Job")
        for job, point in zip(jobs, project.capture_points, strict=True):
            coords = job.find("Coordinates")
            assert coords is not None
            ra_elem = coords.find("J2000RA")
            de_elem = coords.find("J2000DE")
            assert ra_elem is not None and ra_elem.text == str(point.ra)
            assert de_elem is not None and de_elem.text == str(point.dec)

    def test_export_correct_settings(self, tmp_path: Path) -> None:
        """Exposure, gain, binning match capture settings."""
        out = tmp_path / "test.esq"
        project = _make_test_project()
        export_sequence(project, out)
        tree = parse_xml(str(out))
        jobs = tree.findall("Job")
        settings = project.capture_settings
        for job in jobs:
            assert job.findtext("Exposure") == str(settings.exposure_seconds)
            assert job.findtext("Count") == str(settings.exposures_per_point)
            assert job.findtext("Gain") == str(settings.gain)
            assert job.findtext("Offset") == str(settings.offset)
            binning = job.find("Binning")
            assert binning is not None
            assert binning.findtext("X") == str(settings.binning)
            assert binning.findtext("Y") == str(settings.binning)
