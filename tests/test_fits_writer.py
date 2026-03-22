"""Tests for the FITS writer module."""

from pathlib import Path

import pytest

from src.capture.fits_writer import FITSWriter
from src.models.project import CapturePoint


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    """Return a non-existent subdirectory inside tmp_path."""
    return tmp_path / "fits_output"


@pytest.fixture()
def point() -> CapturePoint:
    """Return a sample capture point with index 0."""
    return CapturePoint(ra=15.0, dec=42.0, index=0)


def test_creates_output_directory(output_dir: Path) -> None:
    """FITSWriter creates the output directory on init."""
    assert not output_dir.exists()
    FITSWriter(output_dir)
    assert output_dir.is_dir()


def test_write_fits(output_dir: Path, point: CapturePoint) -> None:
    """Writes file with correct name seq_0001_001.fits for index=0, exposure=1."""
    writer = FITSWriter(output_dir)
    path = writer.write(point, exposure_num=1, data=b"fake-fits")
    assert path == output_dir / "seq_0001_001.fits"
    assert path.exists()


def test_write_multi_exposure(output_dir: Path, point: CapturePoint) -> None:
    """Index=0 with exposures 1 and 2 produce seq_0001_001 and seq_0001_002."""
    writer = FITSWriter(output_dir)
    p1 = writer.write(point, exposure_num=1, data=b"exp1")
    p2 = writer.write(point, exposure_num=2, data=b"exp2")
    assert p1.name == "seq_0001_001.fits"
    assert p2.name == "seq_0001_002.fits"


def test_write_updates_point_files(output_dir: Path, point: CapturePoint) -> None:
    """After write, the filename is in point.files list."""
    writer = FITSWriter(output_dir)
    writer.write(point, exposure_num=1, data=b"data")
    assert "seq_0001_001.fits" in point.files


def test_written_file_contains_data(output_dir: Path, point: CapturePoint) -> None:
    """The written file has the same bytes as the input data."""
    writer = FITSWriter(output_dir)
    data = b"\x00\x01\x02\xff" * 100
    path = writer.write(point, exposure_num=1, data=data)
    assert path.read_bytes() == data
