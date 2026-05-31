"""Label coordinate helpers and rendering primitives.

All position math lives here so it can be unit-tested without the
NiceGUI runtime, and so the pipeline / UI / preview share one source
of truth.
"""

from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.models.project import Label
from src.renderer.alignment import AlignmentResult


def cumulative_offset(
    alignments: list[AlignmentResult],
    from_index: int,
    to_index: int,
) -> tuple[float, float]:
    """Sum (dx, dy) contributions to walk from one frame index to another.

    ``alignments[i]`` is the shift from frame ``i`` to frame ``i+1`` —
    the same convention used by ``align_pair`` and ``linear_pan`` in
    the existing pipeline.

    Args:
        alignments: Pair-wise alignment results, length = N - 1 for N frames.
        from_index: Source frame index.
        to_index: Destination frame index.

    Returns:
        ``(dx, dy)`` to add to a pixel position in frame ``from_index``
        to get the equivalent position in frame ``to_index``.
    """
    if from_index == to_index:
        return (0.0, 0.0)
    if not alignments:
        # No alignment chain available (e.g. transition="none" path that
        # skips the alignment phase). Treat every frame as identical to
        # the reference — labels appear at their stored ``(x, y)`` on
        # every frame. This matches the intuition that a clip with no
        # tracking simply doesn't shift labels around.
        return (0.0, 0.0)
    if from_index < to_index:
        sign = 1.0
        lo, hi = from_index, to_index
    else:
        sign = -1.0
        lo, hi = to_index, from_index
    # Clamp to the available chain length. The chain is N-1 for N frames,
    # so walking past its end (e.g. when ``transition="none"`` is mixed
    # with a partial alignment list, or after outlier filtering) clips
    # the walk silently rather than crashing — labels stop tracking at
    # the last known offset, which is the least-surprising fallback.
    hi = min(hi, len(alignments))
    if hi <= lo:
        return (0.0, 0.0)
    dx = sum(alignments[i].dx for i in range(lo, hi))
    dy = sum(alignments[i].dy for i in range(lo, hi))
    return (sign * dx, sign * dy)


def project_label_to_frame(
    label: Label,
    frame_index: int,
    alignments: list[AlignmentResult],
    frame_dims: tuple[int, int],
) -> tuple[float, float, bool]:
    """Compute the pixel position of a label in an arbitrary frame.

    Args:
        label: The label whose position is anchored in ``label.ref_frame_index``.
        frame_index: Which frame to project into.
        alignments: Pair-wise alignment chain (see ``cumulative_offset``).
        frame_dims: ``(width, height)`` of the current frame in pixels.

    Returns:
        ``(px, py, in_view)`` — the projected pixel position and
        whether the position lies within the frame's bounds.
    """
    dx, dy = cumulative_offset(alignments, label.ref_frame_index, frame_index)
    px = label.x - dx
    py = label.y - dy
    width, height = frame_dims
    in_view = 0.0 <= px < width and 0.0 <= py < height
    return (px, py, in_view)


def catalog_to_ref_pixel(
    ra_deg: float,
    dec_deg: float,
    frame_center_ra_deg: float,
    frame_center_dec_deg: float,
    frame_dims: tuple[int, int],
    pixel_scale_arcsec: float,
    north_angle_deg: float = 0.0,
) -> tuple[float, float]:
    """Approximate sky → reference-frame pixel projection.

    Uses a flat tangent-plane approximation valid for small fields
    (capture areas of a few degrees). RA differences are scaled by
    cos(dec) per standard celestial convention. The result is rotated
    by ``north_angle_deg`` to account for mount alignment offsets;
    0° means north points up in pixel space.

    Pixel convention: x increases rightward (= west on a north-up
    plate), y increases downward. North up means +Dec maps to
    decreasing y.

    Note: this remains a flat-tangent approximation rather than a
    gnomonic projection (#152). The render pipeline burns labels at
    well under 1-degree offsets from frame center, where the flat
    approximation is sub-pixel accurate. The live-preview overlay
    introduced in #152 instead routes through :mod:`src.renderer.wcs`,
    which is a proper TAN-WCS and stays accurate for full FOV-wide
    object distributions.

    Args:
        ra_deg: Catalog object's RA in degrees.
        dec_deg: Catalog object's Dec in degrees.
        frame_center_ra_deg: Reference frame's center RA from the manifest.
        frame_center_dec_deg: Reference frame's center Dec from the manifest.
        frame_dims: ``(width, height)`` of the reference frame in pixels.
        pixel_scale_arcsec: Arcseconds per pixel from the optical setup.
        north_angle_deg: Image-plane rotation; 0° = north up.

    Returns:
        ``(x, y)`` in reference-frame pixel coordinates.
    """
    width, height = frame_dims
    cx = width / 2.0
    cy = height / 2.0

    # Sky-plane offsets in arcseconds.
    cos_dec = math.cos(math.radians(frame_center_dec_deg))
    delta_ra_arcsec = (ra_deg - frame_center_ra_deg) * 3600.0 * cos_dec
    delta_dec_arcsec = (dec_deg - frame_center_dec_deg) * 3600.0

    # In the image plane (pixel space), north up means:
    #   +Dec → -y, +RA (east) → -x.
    # The catalog deltas above are sky-east-positive, sky-north-positive.
    sky_east = -delta_ra_arcsec / pixel_scale_arcsec   # east → -x → flipped
    sky_north = -delta_dec_arcsec / pixel_scale_arcsec  # north → -y → flipped

    # Apply rotation if mount is not aligned to celestial north.
    theta = math.radians(north_angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    # Rotation acts on (sky_east, sky_north) components in pixel space.
    rotated_x = cos_t * sky_east - sin_t * sky_north
    rotated_y = sin_t * sky_east + cos_t * sky_north

    return (cx + rotated_x, cy + rotated_y)


def _resolve_font(size: int) -> ImageFont.ImageFont:
    """Load a sensible default font; fall back to the bitmap default.

    PIL's default font is a tiny bitmap; for legible labels we want
    a TrueType font. The DejaVu set ships with most Linux distros and
    is what NiceGUI's filmstrip thumbs already implicitly rely on.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_marker(
    draw: ImageDraw.ImageDraw,
    px: float,
    py: float,
    marker: str,
    color: str,
    size: int = 6,
) -> None:
    """Draw the marker glyph in-place on the given draw context."""
    if marker == "none":
        return
    s = size
    if marker == "dot":
        draw.ellipse(
            [(px - s / 2, py - s / 2), (px + s / 2, py + s / 2)],
            fill=color,
        )
    elif marker == "cross":
        draw.line([(px - s, py), (px + s, py)], fill=color, width=2)
        draw.line([(px, py - s), (px, py + s)], fill=color, width=2)
    elif marker == "circle":
        draw.ellipse(
            [(px - s, py - s), (px + s, py + s)],
            outline=color, width=2,
        )


def _draw_labels(
    frame: np.ndarray,
    labels: list[Label],
    offsets: list[tuple[float, float]],
    frame_dims: tuple[int, int],
) -> np.ndarray:
    """Draw labels in-place via PIL.

    Args:
        frame: 8-bit RGB numpy array (H, W, 3).
        labels: One Label per element of ``offsets``.
        offsets: ``(dx, dy)`` cumulative offset from each label's
            ``ref_frame_index`` to the current frame. Length must
            equal ``len(labels)``.
        frame_dims: ``(width, height)`` matching the array's shape.

    Returns:
        The annotated frame (uint8 RGB). If ``labels`` is empty, the
        original array is returned unchanged; otherwise a fresh array
        is returned (PIL round-trip).
    """
    if len(labels) != len(offsets):
        msg = (
            f"labels ({len(labels)}) and offsets ({len(offsets)}) "
            f"length mismatch"
        )
        raise ValueError(msg)
    if not labels:
        return frame

    pil = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil)

    width, height = frame_dims
    drew_any = False
    for label, (dx, dy) in zip(labels, offsets, strict=True):
        px = label.x - dx
        py = label.y - dy
        if not (0.0 <= px < width and 0.0 <= py < height):
            continue
        _draw_marker(draw, px, py, label.marker, label.color)
        if label.text:
            font = _resolve_font(label.font_size)
            tx = px + label.text_offset_x
            ty = py + label.text_offset_y
            draw.text((tx, ty), label.text, fill=label.color, font=font)
        drew_any = True

    if not drew_any:
        # All labels were out of view; preserve original-array identity
        # so callers can detect "no-op" via ``np.array_equal`` cheaply.
        return frame
    return np.array(pil)
