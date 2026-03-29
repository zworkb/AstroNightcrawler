"""Tests for the rendering pipeline orchestration."""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Project,
    SplinePath,
)
from src.renderer.pipeline import RenderConfig, RenderPipeline


@pytest.fixture()
def capture_dir(tmp_path: Path) -> Path:
    """Create capture dir with 5 small FITS frames."""
    for i in range(5):
        data = np.random.randint(100, 60000, (64, 64), dtype=np.uint16)
        # Add bright "stars" for alignment
        for _ in range(10):
            x, y = np.random.randint(5, 59, 2)
            data[y - 1 : y + 2, x - 1 : x + 2] = 65000
        hdu = fits.PrimaryHDU(data)
        hdu.writeto(tmp_path / f"seq_{i + 1:04d}_001.fits")

    project = Project(
        project="test-render",
        path=SplinePath(control_points=[
            ControlPoint(ra=10.0, dec=40.0),
            ControlPoint(ra=15.0, dec=45.0),
        ]),
        capture_settings=CaptureSettings(exposure_seconds=5.0),
        capture_points=[
            CapturePoint(ra=10 + i, dec=40 + i, index=i, status="captured",
                         files=[f"seq_{i + 1:04d}_001.fits"])
            for i in range(5)
        ],
    )
    (tmp_path / "manifest.json").write_text(project.model_dump_json(indent=2))
    return tmp_path


class TestRenderPipeline:
    def test_import_loads_frames(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        assert len(pipeline.frames) == 5

    def test_stretch_produces_8bit(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        stretched = pipeline.stretch_frame(0)
        assert stretched.dtype == np.uint8

    def test_skip_frame(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        pipeline.skip_frame(2)
        assert pipeline.frames[2].skipped

    def test_active_frames_excludes_skipped(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        pipeline.skip_frame(2)
        active = pipeline.active_frames()
        assert len(active) == 4
