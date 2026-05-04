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


# Frames written by the pipeline are timed against this reference rate.
# `crossfade_frames=24` means "1 second of transition" at this rate, so
# the natural duration of any clip is `total_frames / REFERENCE_FPS`.
# This is fixed; user-facing `fps` controls the output framerate
# (smoothness) and is independent of duration / speed.
REFERENCE_FPS = 24


def encode_video(
    frames_dir: Path,
    output_path: Path,
    fps: int = 24,
    crf: int = 18,
    speed: float = 1.0,
) -> None:
    """Encode numbered PNGs to video via ffmpeg.

    The input framerate is hard-coded to ``REFERENCE_FPS`` (24) so that
    the natural duration of the clip depends only on the number of
    pipeline-generated frames, not on the output framerate. The
    user-facing ``fps`` parameter then sets the output framerate via
    ``-r``: higher values improve playback smoothness (ffmpeg duplicates
    frames as needed) without changing the duration.

    Args:
        frames_dir: Directory containing frame_NNNNNN.png files.
        output_path: Output video file path.
        fps: Output framerate (smoothness). Higher = smoother playback.
            Does not change duration.
        crf: Constant rate factor (quality, lower=better).
        speed: Playback speed multiplier (1.0 = natural duration,
            2.0 = 2x faster / half duration, 0.5 = half speed / double
            duration). Truly orthogonal to ``fps``.

    Raises:
        RuntimeError: If ffmpeg is not installed or encoding fails.
    """
    if not check_ffmpeg():
        msg = "ffmpeg not found. Install it to encode video."
        raise RuntimeError(msg)

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(REFERENCE_FPS),
        "-i", str(frames_dir / "frame_%06d.png"),
    ]
    if speed != 1.0:
        # setpts=PTS/N stretches/compresses timestamps. Combined with the
        # fixed input framerate above, this controls duration only.
        cmd.extend(["-vf", f"setpts=PTS/{speed}"])
    cmd.extend([
        "-r", str(fps),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        str(output_path),
    ])
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr[-500:])
        msg = f"ffmpeg encoding failed (exit {result.returncode})"
        raise RuntimeError(msg)
    logger.info("Video encoded: %s", output_path)
