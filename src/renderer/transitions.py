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
    start_x: float | None = None,
    start_y: float | None = None,
) -> list[np.ndarray]:
    """Generate linear pan transition with sub-pixel shifting.

    The crop window slides from frame_a's position to frame_b's
    position over num_frames intermediate frames.

    Args:
        frame_a: Starting frame (8-bit RGB).
        frame_b: Ending frame (8-bit RGB).
        alignment: Offset between the two frames.
        num_frames: Number of intermediate frames.
        margin_x: Horizontal crop margin in pixels (defines crop size).
        margin_y: Vertical crop margin in pixels (defines crop size).
        start_x: Horizontal starting offset of the crop window within
            frame_a. Defaults to margin_x when None.
        start_y: Vertical starting offset of the crop window within
            frame_a. Defaults to margin_y when None.

    Returns:
        List of cropped, shifted frames.
    """
    h, w = frame_a.shape[:2]
    crop_h = h - 2 * margin_y
    crop_w = w - 2 * margin_x
    base_x = float(margin_x) if start_x is None else start_x
    base_y = float(margin_y) if start_y is None else start_y
    frames: list[np.ndarray] = []

    for i in range(num_frames):
        t = (i + 1) / (num_frames + 1)

        # Interpolated crop position
        pos_x = base_x + t * alignment.dx
        pos_y = base_y + t * alignment.dy

        # Integer and fractional parts
        ix = int(pos_x)
        iy = int(pos_y)
        fx = pos_x - ix
        fy = pos_y - iy

        # Blend the two frames for smooth brightness transition
        blended = (
            (1 - t) * frame_a.astype(np.float32)
            + t * frame_b.astype(np.float32)
        )

        # Crop at the integer position
        cy = iy
        cx = ix
        cropped = blended[cy:cy + crop_h, cx:cx + crop_w]

        # Sub-pixel shift for the fractional part only
        if abs(fx) > 0.01 or abs(fy) > 0.01:
            shift_vec = [fy, fx]
            if cropped.ndim == 3:
                shift_vec.append(0)
            cropped = ndimage_shift(cropped, shift_vec, order=1)

        frames.append(cropped.astype(np.uint8))

    return frames
