"""FFmpeg wrapper for video encoding.

Stub — full implementation in Task 7.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available on PATH.

    Returns:
        True if ffmpeg is found.
    """
    raise NotImplementedError("Full check_ffmpeg in Task 7")


def write_frame_png(frame: np.ndarray, path: Path) -> None:
    """Write a single frame as PNG.

    Args:
        frame: 8-bit image array.
        path: Output file path.
    """
    raise NotImplementedError("Full write_frame_png in Task 7")


def encode_video(
    frame_dir: Path,
    output: Path,
    fps: int = 24,
    crf: int = 18,
) -> None:
    """Encode PNGs to H.264 video via ffmpeg.

    Args:
        frame_dir: Directory containing numbered PNGs.
        output: Output video path.
        fps: Frames per second.
        crf: Constant rate factor (quality).
    """
    raise NotImplementedError("Full encode_video in Task 7")
