"""Stereographic projection for pixel ↔ sky coordinate conversion.

Uses astropy WCS to convert between canvas pixel coordinates and
equatorial (RA/Dec) sky coordinates, matching the Stellarium Web Engine's
stereographic projection.
"""

from __future__ import annotations

from astropy.wcs import WCS


def pixel_to_radec(
    x: float,
    y: float,
    canvas_width: int,
    canvas_height: int,
    center_yaw: float,
    center_pitch: float,
    fov: float,
) -> tuple[float, float]:
    """Convert pixel coordinates to RA/Dec.

    Stellarium uses azimuth/altitude internally. The yaw/pitch from the
    engine correspond to the center of the viewport. We build a WCS with
    stereographic (AZP) projection centered on (yaw, pitch) and invert
    the pixel to get sky coordinates.

    Args:
        x: Pixel x coordinate (0 = left).
        y: Pixel y coordinate (0 = top).
        canvas_width: Canvas width in pixels.
        canvas_height: Canvas height in pixels.
        center_yaw: Camera yaw (azimuth-like) in degrees.
        center_pitch: Camera pitch (altitude-like) in degrees.
        fov: Field of view in degrees.

    Returns:
        Tuple of (ra, dec) in degrees. Note: these are approximate
        azimuth/altitude values from the engine's frame, not true
        equatorial RA/Dec. True RA/Dec requires observer location
        and time for the conversion.
    """
    wcs = _build_wcs(
        canvas_width, canvas_height,
        center_yaw, center_pitch, fov,
    )
    world = wcs.pixel_to_world_values(x, y)
    ra = float(world[0]) % 360.0
    dec = float(world[1])
    return (ra, dec)


def radec_to_pixel(
    ra: float,
    dec: float,
    canvas_width: int,
    canvas_height: int,
    center_yaw: float,
    center_pitch: float,
    fov: float,
) -> tuple[float, float]:
    """Convert RA/Dec to pixel coordinates.

    Args:
        ra: Right ascension in degrees.
        dec: Declination in degrees.
        canvas_width: Canvas width in pixels.
        canvas_height: Canvas height in pixels.
        center_yaw: Camera yaw in degrees.
        center_pitch: Camera pitch in degrees.
        fov: Field of view in degrees.

    Returns:
        Tuple of (x, y) pixel coordinates.
    """
    wcs = _build_wcs(
        canvas_width, canvas_height,
        center_yaw, center_pitch, fov,
    )
    pixel = wcs.world_to_pixel_values(ra, dec)
    return (float(pixel[0]), float(pixel[1]))


def _build_wcs(
    width: int,
    height: int,
    center_lon: float,
    center_lat: float,
    fov: float,
) -> WCS:
    """Build an astropy WCS for stereographic projection.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        center_lon: Longitude of projection center in degrees.
        center_lat: Latitude of projection center in degrees.
        fov: Vertical field of view in degrees.

    Returns:
        Configured WCS object.
    """
    cdelt = fov / height
    w = WCS(naxis=2)
    w.wcs.crpix = [width / 2.0, height / 2.0]
    w.wcs.cdelt = [-cdelt, cdelt]
    w.wcs.crval = [center_lon, center_lat]
    w.wcs.ctype = ["RA---STG", "DEC--STG"]
    return w
