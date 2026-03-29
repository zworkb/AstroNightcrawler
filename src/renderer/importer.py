"""Import FITS frames from a capture directory with manifest."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from src.models.project import Project

logger = logging.getLogger(__name__)


@dataclass
class FrameInfo:
    """Metadata for a single frame in the render sequence."""

    index: int
    fits_path: Path
    ra: float
    dec: float
    bayer_pattern: str | None = None
    exposure: float = 0.0
    skipped: bool = False


def load_manifest(capture_dir: Path) -> list[FrameInfo]:
    """Read manifest.json and return FrameInfo for captured points.

    Args:
        capture_dir: Directory containing manifest.json and FITS files.

    Returns:
        List of FrameInfo sorted by index, only captured points.
    """
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"No manifest.json in {capture_dir}"
        raise FileNotFoundError(msg)

    project = Project.model_validate_json(manifest_path.read_text())
    frames: list[FrameInfo] = []

    for point in project.capture_points:
        if point.status != "captured" or not point.files:
            continue
        fits_path = capture_dir / point.files[0]  # v1: first exposure only
        bayer = _read_bayer_pattern(fits_path)
        frames.append(FrameInfo(
            index=point.index,
            fits_path=fits_path,
            ra=point.ra,
            dec=point.dec,
            bayer_pattern=bayer,
            exposure=project.capture_settings.exposure_seconds,
        ))

    frames.sort(key=lambda f: f.index)
    logger.info("Loaded %d frames from %s", len(frames), capture_dir)
    return frames


def load_frame(frame: FrameInfo) -> np.ndarray:
    """Load FITS image data as a numpy array.

    Args:
        frame: Frame metadata with fits_path.

    Returns:
        2D numpy array (mono) or 3D (color, already debayered).
    """
    with fits.open(frame.fits_path) as hdul:
        return np.array(hdul[0].data)


def _read_bayer_pattern(fits_path: Path) -> str | None:
    """Read BAYERPAT from FITS header, or None if absent."""
    try:
        with fits.open(fits_path, memmap=True) as hdul:
            return hdul[0].header.get("BAYERPAT")
    except Exception:
        return None
