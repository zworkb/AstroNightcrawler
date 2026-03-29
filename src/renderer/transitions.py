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
    import logging

    _log = logging.getLogger(__name__)
    h, w = frame_a.shape[:2]
    crop_h = max(1, h - 2 * margin_y)
    crop_w = max(1, w - 2 * margin_x)
    base_x = float(margin_x) if start_x is None else start_x
    base_y = float(margin_y) if start_y is None else start_y
    frames: list[np.ndarray] = []

    for i in range(num_frames):
        # t goes from 0.0 (=frame_a) to 1.0 (=frame_b), inclusive
        t = i / max(num_frames - 1, 1)

        # Interpolated crop position
        pos_x = base_x + t * alignment.dx
        pos_y = base_y + t * alignment.dy

        # Integer and fractional parts — clamp to valid range
        ix = max(0, min(int(pos_x), w - crop_w))
        iy = max(0, min(int(pos_y), h - crop_h))
        fx = pos_x - int(pos_x)
        fy = pos_y - int(pos_y)

        # Pure pan on frame_a — NO blending with frame_b.
        # The pan moves the crop window across frame_a. At t=1.0,
        # the window shows the same sky as frame_b at (margin, margin),
        # so the cut to the next transition is seamless.
        source = frame_a.astype(np.float32)

        # Crop at the integer position (with bounds safety)
        cropped = source[iy:iy + crop_h, ix:ix + crop_w]

        if cropped.shape[0] != crop_h or cropped.shape[1] != crop_w:
            _log.warning(
                "Crop mismatch at t=%.2f: expected %dx%d got %dx%d "
                "(frame %dx%d, pos %d,%d, margin %d,%d)",
                t, crop_w, crop_h, cropped.shape[1], cropped.shape[0],
                w, h, ix, iy, margin_x, margin_y,
            )
            # Pad to expected size if too small
            padded = np.zeros(
                (crop_h, crop_w) + source.shape[2:], dtype=source.dtype,
            )
            ph = min(crop_h, cropped.shape[0])
            pw = min(crop_w, cropped.shape[1])
            padded[:ph, :pw] = cropped[:ph, :pw]
            cropped = padded

        # Sub-pixel shift for the fractional part only
        if abs(fx) > 0.01 or abs(fy) > 0.01:
            shift_vec = [fy, fx]
            if cropped.ndim == 3:
                shift_vec.append(0)
            cropped = ndimage_shift(cropped, shift_vec, order=1)

        frames.append(cropped.astype(np.uint8))

    return frames
