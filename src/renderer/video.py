"""FFmpeg video encoding from frame PNGs."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available on PATH."""
    return shutil.which("ffmpeg") is not None


def write_frame_png(
    frame: np.ndarray,
    output_dir: Path,
    index: int,
) -> Path:
    """Write a frame as a numbered PNG file.

    Args:
        frame: 8-bit RGB numpy array.
        output_dir: Directory to write to.
        index: Frame index (for filename ordering).

    Returns:
        Path to the written PNG file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"frame_{index:06d}.png"
    img = Image.fromarray(frame)
    img.save(path)
    return path


def encode_video(
    frames_dir: Path,
    output_path: Path,
    fps: int = 24,
    crf: int = 18,
) -> None:
    """Encode numbered PNGs to video via ffmpeg.

    Args:
        frames_dir: Directory containing frame_NNNNNN.png files.
        output_path: Output video file path.
        fps: Frames per second.
        crf: Constant rate factor (quality, lower=better).

    Raises:
        RuntimeError: If ffmpeg is not installed or encoding fails.
    """
    if not check_ffmpeg():
        msg = "ffmpeg not found. Install it to encode video."
        raise RuntimeError(msg)

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr[-500:])
        msg = f"ffmpeg encoding failed (exit {result.returncode})"
        raise RuntimeError(msg)
    logger.info("Video encoded: %s", output_path)
