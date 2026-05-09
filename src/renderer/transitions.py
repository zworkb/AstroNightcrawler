"""Frame transition generation (crossfade and linear pan)."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
from scipy.ndimage import affine_transform
from scipy.ndimage import shift as ndimage_shift

from src.renderer.alignment import AlignmentResult

# Threshold below which rotation is treated as zero and the legacy
# ``ndimage.shift`` fast path is used. This keeps output byte-identical
# to pre-#125 renders for the (very common) case where astroalign
# reports a rotation that is numerically tiny — well-aligned mounts,
# short pairs, or downsampled-alignment noise. ``1e-6 deg`` is far
# below anything astroalign can resolve from real star data.
_ROTATION_EPS_DEG = 1e-6


def crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    num_frames: int = 6,
) -> Iterator[np.ndarray]:
    """Generate crossfade transition frames between two images.

    Yields frames one at a time (generator) so callers that stream them
    to disk don't need to hold all ``num_frames`` arrays in memory at
    once. For a 24-frame, 8K-RGB transition that's the difference
    between ~80 MB peak (one frame) and ~1.9 GB (full list). See
    issue #124.

    Args:
        frame_a: Starting frame (8-bit).
        frame_b: Ending frame (8-bit).
        num_frames: Number of intermediate frames.

    Yields:
        Blended frames, in order from mostly-A to mostly-B.
    """
    for i in range(num_frames):
        # alpha goes from 0.0 (=frame_a) to 1.0 (=frame_b), inclusive
        alpha = i / max(num_frames - 1, 1)
        blended = (
            (1 - alpha) * frame_a.astype(np.float32)
            + alpha * frame_b.astype(np.float32)
        )
        yield blended.astype(np.uint8)


def linear_pan(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alignment: AlignmentResult,
    num_frames: int = 6,
    margin_x: int = 0,
    margin_y: int = 0,
    start_x: float | None = None,
    start_y: float | None = None,
) -> Iterator[np.ndarray]:
    """Generate linear pan transition with sub-pixel shifting.

    The crop window slides from frame_a's position to frame_b's
    position over num_frames intermediate frames.

    Yields frames one at a time (generator) so streaming consumers can
    write each frame to disk and free it before the next is generated;
    see :func:`crossfade` for the memory rationale (issue #124).

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

    Yields:
        Cropped, shifted frames, in order from frame_a's position to
        frame_b's position.
    """
    import logging

    _log = logging.getLogger(__name__)
    h, w = frame_a.shape[:2]
    crop_h = max(2, h - 2 * margin_y)
    crop_w = max(2, w - 2 * margin_x)
    # H.264 requires even dimensions
    crop_h -= crop_h % 2
    crop_w -= crop_w % 2
    base_x = float(margin_x) if start_x is None else start_x
    base_y = float(margin_y) if start_y is None else start_y

    # Regression-safe fast path: when there's no rotation to interpolate,
    # use the legacy crop+shift code so output is byte-identical to
    # pre-#125 renders. Most production captures land here because well-
    # aligned mounts produce rotations far below this threshold.
    use_rotation = abs(alignment.rotation) >= _ROTATION_EPS_DEG

    for i in range(num_frames):
        # t goes from 0.0 (=frame_a) to 1.0 (=frame_b), inclusive
        t = i / max(num_frames - 1, 1)

        # Interpolated crop position
        pos_x = base_x + t * alignment.dx
        pos_y = base_y + t * alignment.dy

        if not use_rotation:
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

            yield cropped.astype(np.uint8)
            continue

        # Rotation path: use a single ``affine_transform`` call that
        # does rotation around the crop-window centre + sub-pixel
        # translation in one bilinear-interpolation step. This avoids
        # the visible "snap" at keyframe transitions when alignment
        # rotation is non-trivial (issue #125).
        #
        # Sign convention: ``alignment.rotation`` comes from
        # ``astroalign.find_transform(b, a)``, so it is the angle that
        # rotates frame_b's sky to match frame_a's orientation. We want
        # the crop window (which lives in frame_a) to rotate *toward*
        # frame_b's orientation as t goes 0->1, so we apply the same
        # sign here (``+t * alignment.rotation``). If a visual test
        # shows the rotation runs the wrong way, flip the sign of
        # ``theta`` below.
        #
        # Note on margins: rotation can introduce up to
        # ``crop_diag / 2 * sin(rotation)`` pixels of black fill at
        # the corners (from ``cval=0``). For typical sub-degree
        # rotations the existing translation-driven margin headroom
        # in pipeline.py already covers this; if very large rotations
        # ever appear we may need to extend the margin computation.
        theta = np.deg2rad(-t * alignment.rotation)
        cos_t = float(np.cos(theta))
        sin_t = float(np.sin(theta))
        rot = np.array([[cos_t, -sin_t], [sin_t, cos_t]])

        # ``affine_transform`` semantics: output[y,x] = input[matrix @
        # [y,x] + offset]. We must mirror astroalign's transform shape:
        # ``a_coord = R @ b_coord + (dy, dx)`` (rotation around the
        # *source* origin, then translation). Linear interpolation:
        #   matrix_t = R(t * theta)
        #   offset_t = R_t @ (base_y, base_x) + t * (dy, dx)
        # At t=0: matrix=I, offset=(base_y, base_x) → cropped at
        # (base_y, base_x), no rotation. At t=1: matrix=R, offset =
        # R @ (base_y, base_x) + (dy, dx) → exactly astroalign's full
        # affine transform applied to the crop window.
        # An earlier version rotated around the crop *centre* and added
        # a separately-interpolated translation; that produced ~|crop|
        # × sin(theta) px of spurious motion per frame which the user
        # saw as a few-pixel jitter on top of the pan. See #125.
        base_yx = np.array([base_y, base_x])
        offset_yx = rot @ base_yx + t * np.array(
            [alignment.dy, alignment.dx],
        )

        source = frame_a.astype(np.float32)

        if source.ndim == 3:
            matrix_3d = np.eye(3)
            matrix_3d[:2, :2] = rot
            offset_3d = np.array([offset_yx[0], offset_yx[1], 0.0])
            warped = affine_transform(
                source,
                matrix_3d,
                offset=offset_3d,
                output_shape=(crop_h, crop_w, source.shape[2]),
                order=1,
                mode="constant",
                cval=0.0,
            )
        else:
            warped = affine_transform(
                source,
                rot,
                offset=offset_yx,
                output_shape=(crop_h, crop_w),
                order=1,
                mode="constant",
                cval=0.0,
            )

        # Clip back to uint8 range — bilinear interpolation can produce
        # values slightly outside [0, 255] for high-contrast edges.
        np.clip(warped, 0.0, 255.0, out=warped)
        yield warped.astype(np.uint8)
