"""Tests for the renderer FITS importer."""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from src.models.project import CapturePoint, CaptureSettings, ControlPoint, Project, SplinePath
from src.renderer.importer import load_frame, load_manifest


@pytest.fixture()
def capture_dir(tmp_path: Path) -> Path:
    """Create a minimal capture directory with manifest + FITS files."""
    for i in range(3):
        data = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        hdu = fits.PrimaryHDU(data)
        hdu.header["BAYERPAT"] = "RGGB"
        hdu.header["EXPTIME"] = 5.0
        hdu.writeto(tmp_path / f"seq_{i+1:04d}_001.fits")

    project = Project(
        project="test",
        path=SplinePath(
            control_points=[
                ControlPoint(ra=10.0, dec=40.0),
                ControlPoint(ra=11.0, dec=41.0),
            ]
        ),
        capture_settings=CaptureSettings(exposure_seconds=5.0),
        capture_points=[
            CapturePoint(
                ra=10.0, dec=40.0, index=0, status="captured", files=["seq_0001_001.fits"]
            ),
            CapturePoint(
                ra=10.5, dec=40.5, index=1, status="captured", files=["seq_0002_001.fits"]
            ),
            CapturePoint(ra=11.0, dec=41.0, index=2, status="skipped", files=[]),
        ],
    )
    (tmp_path / "manifest.json").write_text(project.model_dump_json(indent=2))
    return tmp_path


class TestLoadManifest:
    def test_loads_captured_points(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert len(frames) == 2  # skipped point excluded

    def test_frame_order(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].index == 0
        assert frames[1].index == 1

    def test_resolves_fits_path(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].fits_path == capture_dir / "seq_0001_001.fits"
        assert frames[0].fits_path.exists()

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path)


class TestLoadFrame:
    def test_returns_numpy_array(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        data = load_frame(frames[0])
        assert isinstance(data, np.ndarray)
        assert data.shape == (100, 100)
        assert data.dtype == np.uint16

    def test_reads_bayer_pattern(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].bayer_pattern == "RGGB"
