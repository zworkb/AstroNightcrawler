"""Lightweight WCS construction + pixel/world mapping helpers.

The renderer needs world↔pixel for two flows:
  1. Burning catalog labels into a rendered frame
     (``labels.catalog_to_ref_pixel`` — pre-existing, this module
     hosts its shared math now).
  2. Live preview: filter the catalog to the FOV around the current
     frame's mount-pointing center, then place each match on the
     preview image for client-side nearest-object queries (#152).

We never have plate-solved WCS from the capture pipeline. Instead we
build a *synthetic* tangent-plane WCS from:
  * the FITS-header mount center (``CRVAL1/2`` ≡ ``CapturePoint.ra/dec``),
  * the camera's pixel scale from :class:`Settings`, and
  * the per-project ``north_angle_deg`` rotation.

That's accurate to within a few pixels on the FOVs we care about
(a couple of degrees), which is plenty for label placement.
"""

from __future__ import annotations

from typing import Any

from astropy.wcs import WCS


def build_wcs(
    *,
    center_ra_deg: float,
    center_dec_deg: float,
    frame_dims: tuple[int, int],
    pixel_scale_arcsec: float,
    north_angle_deg: float = 0.0,
) -> WCS:
    """Construct a synthetic tangent-plane WCS for one frame.

    Convention matches :func:`src.renderer.labels.catalog_to_ref_pixel`:
    pixel-x increases rightward (=west on a north-up plate), pixel-y
    increases downward, and ``north_angle_deg`` rotates the celestial
    axes clockwise in pixel space (0° = north up).

    Args:
        center_ra_deg: Pointing center RA in degrees (J2000).
        center_dec_deg: Pointing center Dec in degrees (J2000).
        frame_dims: ``(width, height)`` of the frame in pixels.
        pixel_scale_arcsec: Arcseconds per pixel from the optical setup.
        north_angle_deg: Image-plane rotation; 0° = north up.

    Returns:
        Configured :class:`astropy.wcs.WCS` instance.
    """
    width, height = frame_dims
    scale_deg = pixel_scale_arcsec / 3600.0

    # Build the rotation matrix that matches ``catalog_to_ref_pixel``.
    # That function applies a +north_angle_deg rotation in the
    # (sky_east, sky_north) -> (pixel_x, pixel_y) mapping. To reproduce
    # it inside a standard FITS CD-matrix we encode the same rotation
    # plus the east-flip (RA increases east, but pixel-x increases west).
    import math

    theta = math.radians(north_angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    wcs = WCS(naxis=2)
    # CRPIX is 1-indexed per FITS convention; matching the existing
    # ``catalog_to_ref_pixel`` math means a sky point at the pointing
    # center lands at 0-indexed pixel ``(width/2, height/2)`` (i.e.
    # 1-indexed ``(width/2 + 1, height/2 + 1)``).
    wcs.wcs.crpix = [width / 2.0 + 1.0, height / 2.0 + 1.0]
    wcs.wcs.crval = [center_ra_deg, center_dec_deg]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    # CD matrix: encodes both scale and rotation. The east-flip lives in
    # the sign of CD1_1 (negative RA per +x pixel because pixel-x goes
    # west). The synthetic CD matrix below is equivalent to the
    # hand-rolled math in ``catalog_to_ref_pixel`` (verified by the
    # round-trip test in tests/test_wcs.py).
    # The matrix below maps (Δx, Δy)_px -> (ΔRA, ΔDec)_deg around the
    # CRPIX/CRVAL pivot. We choose it so that at north_angle_deg=0:
    #   * +Δx → -ΔRA (east is to the LEFT in image space), and
    #   * +Δy → -ΔDec (north is UP in image space).
    # At north_angle_deg=+90 the (sky_east, sky_north) frame rotates by
    # +90° in pixel space, which means +ΔDec must show up as +Δx.
    wcs.wcs.cd = [
        [-scale_deg * cos_t, -scale_deg * sin_t],
        [scale_deg * sin_t, -scale_deg * cos_t],
    ]
    return wcs


def pixel_to_world(wcs: WCS, x: float, y: float) -> tuple[float, float]:
    """Pixel ``(x, y)`` -> sky ``(ra_deg, dec_deg)`` via ``wcs``.

    Origin convention: 0-based pixel coordinates (top-left = (0, 0)),
    matching numpy / PIL / the renderer pipeline.
    """
    coord = wcs.pixel_to_world_values(x, y)
    return float(coord[0]), float(coord[1])


def world_to_pixel(wcs: WCS, ra_deg: float, dec_deg: float) -> tuple[float, float]:
    """Sky ``(ra_deg, dec_deg)`` -> pixel ``(x, y)`` via ``wcs``.

    Origin convention: 0-based pixel coordinates (top-left = (0, 0)).
    """
    px, py = wcs.world_to_pixel_values(ra_deg, dec_deg)
    return float(px), float(py)


def north_angle_from_fits_header(header: Any) -> float | None:
    """Extract field rotation (degrees) from a FITS header.

    Capture apps (Ekos, NINA) write ``CROTA1``/``CROTA2`` — the rotation
    from celestial north to the +y pixel axis. That's the same axis our
    :func:`build_wcs` expects in ``north_angle_deg`` (sign-flipped — see
    below), so plugging it straight in gives a per-frame rotation that
    handles arbitrary camera mounting *and* the 180° flip after a
    meridian crossing without the user touching anything.

    FITS CROTA2 convention: "angle from celestial north to +y, measured
    eastward (CCW on the sky)." Our ``build_wcs`` convention: positive
    ``north_angle_deg`` rotates the sky frame clockwise in pixel space.
    The two are opposite signs, hence the negation here.

    Returns ``None`` if no rotation field is present, so the caller can
    fall back to the project default (which is 0° = "north up").
    """
    if header is None:
        return None
    for key in ("CROTA2", "CROTA1"):
        val = header.get(key) if hasattr(header, "get") else None
        if val is not None:
            try:
                return -float(val)
            except (TypeError, ValueError):
                continue
    return None


def pixel_scale_from_fits_header(header: Any) -> float | None:
    """Extract pixel scale (arcsec/pixel) from a FITS header.

    Order of preference:
      1. ``CDELT1`` (degrees/pixel) — written by plate-solvers and most
         capture apps (Ekos, NINA). Most accurate when present.
      2. ``XPIXSZ`` (μm) + ``FOCALLEN`` (mm) — derived via
         ``arcsec = pixel_size_μm / focal_length_mm × 206.265``. Falls
         back here when CDELT is absent (raw frames from some cameras).

    Returns ``None`` if neither pair is present, so the caller can fall
    back to the optical-setup default in :class:`Settings`.

    The global ``settings.pixel_scale_arcsec`` is only a fallback because
    a single setting can't cover multiple OTA/camera combinations across
    projects — the per-frame header is the source of truth (#152).
    """
    if header is None:
        return None
    cdelt = header.get("CDELT1") if hasattr(header, "get") else None
    if cdelt is not None:
        try:
            return abs(float(cdelt)) * 3600.0
        except (TypeError, ValueError):
            pass
    xpixsz = header.get("XPIXSZ") if hasattr(header, "get") else None
    focallen = header.get("FOCALLEN") if hasattr(header, "get") else None
    if xpixsz is not None and focallen is not None:
        try:
            xpixsz_f = float(xpixsz)
            focallen_f = float(focallen)
            if focallen_f > 0:
                return xpixsz_f / focallen_f * 206.265
        except (TypeError, ValueError):
            pass
    return None


def project_catalog_to_pixels(
    objects: list[dict[str, Any]],
    wcs: WCS,
    frame_dims: tuple[int, int],
) -> list[dict[str, Any]]:
    """Annotate catalog rows with pixel coords; drop offscreen entries.

    Args:
        objects: Catalog dicts (must carry ``ra`` and ``dec``).
        wcs: Synthetic WCS for the frame (see :func:`build_wcs`).
        frame_dims: ``(width, height)`` in pixels — used to drop objects
            that project outside the visible frame.

    Returns:
        New list of dicts, each enriched with ``pixel_x``/``pixel_y``
        floats. Objects outside the frame are omitted.
    """
    width, height = frame_dims
    out: list[dict[str, Any]] = []
    for obj in objects:
        px, py = world_to_pixel(wcs, obj["ra"], obj["dec"])
        if not (0.0 <= px < width and 0.0 <= py < height):
            continue
        enriched = dict(obj)
        enriched["pixel_x"] = px
        enriched["pixel_y"] = py
        out.append(enriched)
    return out


def apply_wcs_flip(wcs: WCS) -> WCS:
    """Negate CD/PC matrix → 180° rotation around CRPIX.

    Workaround for capture-tool bugs where the meridian-flip pierside
    is reflected in PIERSIDE but not in CROTA2, leaving Catalog labels
    point-mirrored through the frame centre. User toggles this per
    project; see docs/wcs-pierside-analysis.md for the full picture.

    Mutates ``wcs`` in place and returns it for chaining.
    """
    if wcs.wcs.has_cd():
        wcs.wcs.cd = -wcs.wcs.cd
    else:
        wcs.wcs.pc = -wcs.wcs.get_pc()
    return wcs
