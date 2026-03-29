"""Frame transition generation (crossfade and linear pan)."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import shift as ndimage_shift

from src.renderer.alignment import AlignmentResult


def crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    num_frames: int = 6,
) -> list[np.ndarray]:
    """Generate crossfade transition frames between two images.

    Args:
        frame_a: Starting frame (8-bit).
        frame_b: Ending frame (8-bit).
        num_frames: Number of intermediate frames.

    Returns:
        List of blended frames.
    """
    frames: list[np.ndarray] = []
    for i in range(num_frames):
        alpha = (i + 1) / (num_frames + 1)
        blended = (
            (1 - alpha) * frame_a.astype(np.float32)
            + alpha * frame_b.astype(np.float32)
        )
        frames.append(blended.astype(np.uint8))
    return frames


def linear_pan(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alignment: AlignmentResult,
    num_frames: int = 6,
    margin_x: int = 0,
    margin_y: int = 0,
) -> list[np.ndarray]:
    """Generate linear pan transition with sub-pixel shifting.

    The crop window slides from frame_a's position to frame_b's
    position over num_frames intermediate frames.

    Args:
        frame_a: Starting frame (8-bit RGB).
        frame_b: Ending frame (8-bit RGB).
        alignment: Offset between the two frames.
        num_frames: Number of intermediate frames.
        margin_x: Horizontal crop margin in pixels.
        margin_y: Vertical crop margin in pixels.

    Returns:
        List of cropped, shifted frames.
    """
    h, w = frame_a.shape[:2]
    crop_h = h - 2 * margin_y
    crop_w = w - 2 * margin_x
    frames: list[np.ndarray] = []

    for i in range(num_frames):
        t = (i + 1) / (num_frames + 1)

        # Interpolate crop window position
        ox = margin_x + t * alignment.dx
        oy = margin_y + t * alignment.dy

        # Blend the two frames
        blended = (
            (1 - t) * frame_a.astype(np.float32)
            + t * frame_b.astype(np.float32)
        )

        # Sub-pixel shift
        shift_vec = [oy - margin_y, ox - margin_x]
        if blended.ndim == 3:
            shift_vec.append(0)
        shifted = ndimage_shift(blended, shift_vec, order=1)

        # Crop to safe area
        cropped = shifted[margin_y:margin_y + crop_h, margin_x:margin_x + crop_w]
        frames.append(cropped.astype(np.uint8))

    return frames
