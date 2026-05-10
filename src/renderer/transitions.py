"""Frame transition generation (crossfade and linear pan)."""

from __future__ import annotations

import logging
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


def _frame_b_reverse_pan(
    frame_b: np.ndarray,
    alignment: AlignmentResult,
    base_y: float,
    base_x: float,
    t: float,
    crop_h: int,
    crop_w: int,
    use_rotation: bool,
    log: logging.Logger,
) -> np.ndarray:
    """Sample frame_b at the position corresponding to frame_a's pan at t.

    Frame_a's pan at parameter t shows a window of the sky that lives
    at frame_a coordinate ``base + t * (dy, dx)`` (with rotation when
    applicable). For the tail-blend (#126) to be geometrically clean
    we must sample frame_b at the position that shows the SAME sky
    region — otherwise the blend mixes two different views and we get
    a ghost image / displacement.

    Astroalign's transform: ``a_coord = R @ b_coord + (dy, dx)``, so
    the inverse mapping is ``b_coord = R^-1 @ (a_coord - (dy, dx))``.
    Applied to the pan trajectory, frame_b's per-frame crop position
    parametrised by ``t`` is:

      matrix_b(t) = R((t-1) * theta_sign)
      offset_b(t) = matrix_b(t) @ base + (t-1) * R_inv_full @ (dy, dx)

    At ``t=1`` this reduces to identity / ``base``: frame_b is sampled
    straight at ``(margin, margin)``. At ``t=0`` it reduces to
    ``R^-1 @ (base - T)``: the b-coord of frame_a's start position,
    so the blend visually slides frame_b backwards as it fades in.

    Args:
        frame_b: Source frame (uint8 RGB or mono).
        alignment: Alignment result (dx, dy in pixels, rotation in deg).
        base_y, base_x: Crop start position in frame_a coords (typically
            ``(margin_y, margin_x)`` unless the caller overrode via
            ``start_x`` / ``start_y``).
        t: Pan parameter in ``[0, 1]``.
        crop_h, crop_w: Output dimensions.
        use_rotation: True when frame_a's pan uses the affine_transform
            path; False for the legacy ndimage.shift fast path.
        log: Logger for warnings on undersized crops.

    Returns:
        Float32 array of shape ``(crop_h, crop_w[, channels])`` ready
        to be blended with the corresponding frame_a pan output.
    """
    bh, bw = frame_b.shape[:2]
    source_b = frame_b.astype(np.float32)

    if not use_rotation:
        # Pure translation: frame_b crop slides backwards from base
        # at t=1 to (base - dy, base - dx) at t=0.
        b_pos_y = base_y + (t - 1.0) * alignment.dy
        b_pos_x = base_x + (t - 1.0) * alignment.dx
        b_iy = max(0, min(int(b_pos_y), max(0, bh - crop_h)))
        b_ix = max(0, min(int(b_pos_x), max(0, bw - crop_w)))
        b_fy = b_pos_y - int(b_pos_y)
        b_fx = b_pos_x - int(b_pos_x)
        b_cropped = source_b[b_iy:b_iy + crop_h, b_ix:b_ix + crop_w]
        if b_cropped.shape[0] != crop_h or b_cropped.shape[1] != crop_w:
            log.warning(  # type: ignore[attr-defined]
                "frame_b reverse-pan crop too small (got %dx%d, need %dx%d) "
                "at t=%.2f — padding with zeros",
                b_cropped.shape[1], b_cropped.shape[0], crop_w, crop_h, t,
            )
            padded = np.zeros(
                (crop_h, crop_w) + source_b.shape[2:], dtype=np.float32,
            )
            ph = min(crop_h, b_cropped.shape[0])
            pw = min(crop_w, b_cropped.shape[1])
            padded[:ph, :pw] = b_cropped[:ph, :pw]
            b_cropped = padded
        if abs(b_fx) > 0.01 or abs(b_fy) > 0.01:
            shift_vec = [b_fy, b_fx]
            if b_cropped.ndim == 3:
                shift_vec.append(0)
            b_cropped = ndimage_shift(b_cropped, shift_vec, order=1)
        return b_cropped

    # Rotation path. theta_sign matches frame_a's interpolation sign
    # (-alignment.rotation, in radians); see the rotation block in
    # ``linear_pan`` for the derivation of why this is the correct
    # inverse-mapping rotation for the affine_transform call.
    theta_sign_rad = np.deg2rad(-alignment.rotation)
    theta_b = (t - 1.0) * theta_sign_rad
    cos_b = float(np.cos(theta_b))
    sin_b = float(np.sin(theta_b))
    rot_b = np.array([[cos_b, -sin_b], [sin_b, cos_b]])
    # R_inv_full = R(-theta_sign_rad), the inverse of frame_a's full
    # rotation at t=1, used on the translation term so the formula
    # reduces correctly at t=0 (offset = R^-1 @ (base - T)).
    cos_inv = float(np.cos(-theta_sign_rad))
    sin_inv = float(np.sin(-theta_sign_rad))
    r_inv_full = np.array([[cos_inv, -sin_inv], [sin_inv, cos_inv]])
    base_yx = np.array([base_y, base_x])
    txy = np.array([alignment.dy, alignment.dx])
    offset_b = rot_b @ base_yx + (t - 1.0) * r_inv_full @ txy

    if source_b.ndim == 3:
        matrix_3d_b = np.eye(3)
        matrix_3d_b[:2, :2] = rot_b
        offset_3d_b = np.array([offset_b[0], offset_b[1], 0.0])
        warped_b = affine_transform(
            source_b, matrix_3d_b, offset=offset_3d_b,
            output_shape=(crop_h, crop_w, source_b.shape[2]),
            order=1, mode="constant", cval=0.0,
        )
    else:
        warped_b = affine_transform(
            source_b, rot_b, offset=offset_b,
            output_shape=(crop_h, crop_w),
            order=1, mode="constant", cval=0.0,
        )
    return warped_b


def _maybe_blend_tail(
    pan_frame: np.ndarray,
    b_crop: np.ndarray | None,
    i: int,
    blend_start: int,
    blend_tail_frames: int,
) -> np.ndarray:
    """Apply the smoothstep tail-blend on the final K frames (#126).

    Both ``linear_pan`` code paths (zero-rotation fast path and rotation
    affine_transform path) hand off through this single helper so the
    blend logic exists in exactly one place. When no blend is active
    (``blend_tail_frames == 0`` or ``i < blend_start`` or ``b_crop is
    None``) this returns the un-blended uint8 frame, byte-identical to
    the pre-#126 output.

    Args:
        pan_frame: Float32 pan output for the current frame.
        b_crop: Pre-computed float32 frame_b reference crop, or ``None``
            when blending is disabled.
        i: Index of the current frame in the linear_pan loop.
        blend_start: First frame index (inclusive) at which blending kicks
            in (``num_frames - blend_tail_frames``).
        blend_tail_frames: Total number of trailing frames in the blend.

    Returns:
        uint8 frame ready to yield.
    """
    if blend_tail_frames > 0 and i >= blend_start and b_crop is not None:
        progress = (i - blend_start) / max(blend_tail_frames - 1, 1)
        # Smoothstep easing: 3t^2 - 2t^3 — gentle ramp-in/out, no kink.
        alpha = progress * progress * (3.0 - 2.0 * progress)
        blended = (1.0 - alpha) * pan_frame + alpha * b_crop
        return np.clip(blended, 0.0, 255.0).astype(np.uint8)
    return pan_frame.astype(np.uint8)


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
    blend_tail_frames: int = 0,
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
        blend_tail_frames: Number of trailing frames over which to
            crossfade from frame_a's pan output to frame_b's
            ``(margin, margin)``-crop using smoothstep easing. Smooths
            brightness/exposure jumps between keyframes (issue #126).
            ``0`` (default) = no blending, byte-identical to the
            pre-#126 path. Values >= ``num_frames`` are clamped to
            ``num_frames - 1`` with a warning.

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

    # Tail-blend setup (#126). Clamp pathological values, precompute
    # frame_b's reference crop once. Default blend_tail_frames=0 short-
    # circuits all this work and yields the un-blended pan output, so
    # existing renders stay byte-identical.
    if blend_tail_frames > 0 and blend_tail_frames >= num_frames:
        _log.warning(
            "blend_tail_frames=%d >= num_frames=%d, clamping to %d",
            blend_tail_frames, num_frames, num_frames - 1,
        )
        blend_tail_frames = max(0, num_frames - 1)

    # Note: Frame_b is NOT precomputed at a fixed (margin, margin)
    # position. The earlier version did that, but at any t < 1 the pan
    # in frame_a shows a different sky region than frame_b's static
    # (margin, margin) crop. Blending a static frame_b with a moving
    # pan produced a visible geometric ghost / displacement. The fix:
    # frame_b also pans, in mirror-symmetry to frame_a's pan, so at
    # each t both views show the SAME sky region — only the
    # brightness / exposure differs, which is exactly what we want
    # the blend to smooth. See #126 follow-up.
    use_blend = blend_tail_frames > 0
    blend_start = num_frames - blend_tail_frames if use_blend else num_frames

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

            # Funnel both code paths into ``pan_frame`` (float32) so the
            # tail-blend logic below runs once for either branch (#126).
            pan_frame: np.ndarray = cropped
            b_pan_frame = (
                _frame_b_reverse_pan(
                    frame_b, alignment, base_y, base_x, t,
                    crop_h, crop_w, use_rotation=False, log=_log,
                )
                if use_blend and i >= blend_start
                else None
            )
            yield _maybe_blend_tail(
                pan_frame, b_pan_frame, i, blend_start, blend_tail_frames,
            )
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
        b_pan_frame = (
            _frame_b_reverse_pan(
                frame_b, alignment, base_y, base_x, t,
                crop_h, crop_w, use_rotation=True, log=_log,
            )
            if use_blend and i >= blend_start
            else None
        )
        yield _maybe_blend_tail(
            warped, b_pan_frame, i, blend_start, blend_tail_frames,
        )
