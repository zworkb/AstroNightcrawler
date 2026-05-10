"""Label coordinate helpers and rendering primitives.

All position math lives here so it can be unit-tested without the
NiceGUI runtime, and so the pipeline / UI / preview share one source
of truth.
"""

from __future__ import annotations

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
    if from_index < to_index:
        sign = 1.0
        lo, hi = from_index, to_index
    else:
        sign = -1.0
        lo, hi = to_index, from_index
    dx = sum(alignments[i].dx for i in range(lo, hi))
    dy = sum(alignments[i].dy for i in range(lo, hi))
    return (sign * dx, sign * dy)


from src.models.project import Label


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


import math


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
