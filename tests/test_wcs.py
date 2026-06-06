"""Tests for the synthetic WCS helpers (#152)."""

from __future__ import annotations

import math

import pytest

from src.renderer.wcs import (
    apply_wcs_flip,
    build_wcs,
    north_angle_from_fits_header,
    pixel_scale_from_fits_header,
    pixel_to_world,
    project_catalog_to_pixels,
    world_to_pixel,
)


def test_world_to_pixel_center_lands_at_frame_center():
    """Pointing-center RA/Dec projects to the geometric pixel center."""
    wcs = build_wcs(
        center_ra_deg=180.0,
        center_dec_deg=45.0,
        frame_dims=(1280, 720),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    x, y = world_to_pixel(wcs, 180.0, 45.0)
    assert x == pytest.approx(640.0, abs=0.6)
    assert y == pytest.approx(360.0, abs=0.6)


def test_round_trip_pixel_world_pixel_within_tolerance():
    """A pixel goes through world and comes back to (almost) itself."""
    wcs = build_wcs(
        center_ra_deg=83.633,  # near M1 / Crab
        center_dec_deg=22.015,
        frame_dims=(4000, 3000),
        pixel_scale_arcsec=1.5,
        north_angle_deg=12.0,
    )
    px0, py0 = 1234.5, 678.25
    ra, dec = pixel_to_world(wcs, px0, py0)
    px1, py1 = world_to_pixel(wcs, ra, dec)
    assert px1 == pytest.approx(px0, abs=1e-3)
    assert py1 == pytest.approx(py0, abs=1e-3)


def test_north_up_dec_increase_moves_pixel_y_up():
    """+1° in declination at small offsets reduces pixel-y (north up)."""
    wcs = build_wcs(
        center_ra_deg=180.0,
        center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    x_center, y_center = world_to_pixel(wcs, 180.0, 45.0)
    x_north, y_north = world_to_pixel(wcs, 180.0, 45.1)
    assert y_north < y_center
    assert math.isclose(x_north, x_center, abs_tol=2.0)


def test_north_angle_rotates_dec_axis_into_pixel_x():
    """90° north rotation: a +Dec offset should appear as a +pixel-x offset."""
    wcs = build_wcs(
        center_ra_deg=180.0,
        center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=90.0,
    )
    x_center, y_center = world_to_pixel(wcs, 180.0, 45.0)
    x_north, y_north = world_to_pixel(wcs, 180.0, 45.1)
    # +Dec now goes to +pixel-x, y stays roughly put.
    assert x_north > x_center + 100.0
    assert math.isclose(y_north, y_center, abs_tol=2.0)


def test_project_catalog_to_pixels_drops_offscreen_objects():
    """An object outside the frame is excluded from the result."""
    wcs = build_wcs(
        center_ra_deg=180.0,
        center_dec_deg=45.0,
        frame_dims=(1000, 1000),
        pixel_scale_arcsec=1.0,  # 1000x1000 px ≈ 0.28° FOV
        north_angle_deg=0.0,
    )
    objects = [
        {"id": "center", "name": "c", "ra": 180.0, "dec": 45.0,
         "mag": 5.0, "type": "X", "catalog": "M"},
        {"id": "far_away", "name": "f", "ra": 180.0, "dec": 60.0,
         "mag": 5.0, "type": "X", "catalog": "M"},
    ]
    projected = project_catalog_to_pixels(objects, wcs, (1000, 1000))
    ids = {p["id"] for p in projected}
    assert "center" in ids
    assert "far_away" not in ids
    for p in projected:
        assert 0.0 <= p["pixel_x"] < 1000.0
        assert 0.0 <= p["pixel_y"] < 1000.0


def test_pixel_scale_from_cdelt():
    """CDELT1 in degrees/pixel converts to arcsec/pixel."""
    header = {"CDELT1": 0.00041596310491, "CDELT2": 0.00041596310491}
    scale = pixel_scale_from_fits_header(header)
    assert scale == pytest.approx(1.497, abs=0.005)


def test_pixel_scale_from_xpixsz_focallen():
    """Fallback: XPIXSZ (μm) / FOCALLEN (mm) × 206.265 = arcsec/px."""
    header = {"XPIXSZ": 3.76, "FOCALLEN": 518.0}
    scale = pixel_scale_from_fits_header(header)
    assert scale == pytest.approx(3.76 / 518.0 * 206.265, abs=1e-6)


def test_pixel_scale_cdelt_preferred_over_xpixsz():
    """When both are present, CDELT wins (it's plate-solver authoritative)."""
    header = {"CDELT1": 0.0005, "XPIXSZ": 3.76, "FOCALLEN": 518.0}
    scale = pixel_scale_from_fits_header(header)
    assert scale == pytest.approx(1.8, abs=0.001)


def test_pixel_scale_returns_none_for_empty_header():
    """Caller falls back to settings default when header is uninformative."""
    assert pixel_scale_from_fits_header({}) is None
    assert pixel_scale_from_fits_header(None) is None


def test_north_angle_from_crota2():
    """CROTA2 -> negated -> build_wcs north_angle_deg convention."""
    assert north_angle_from_fits_header({"CROTA2": 34.75}) == pytest.approx(-34.75)
    assert north_angle_from_fits_header({"CROTA2": -180.0}) == pytest.approx(180.0)


def test_north_angle_crota1_fallback():
    """If only CROTA1 is present, use it."""
    assert north_angle_from_fits_header({"CROTA1": 12.5}) == pytest.approx(-12.5)


def test_north_angle_crota2_preferred_over_crota1():
    """When both are present, CROTA2 wins (the standard FITS rotation key)."""
    assert north_angle_from_fits_header(
        {"CROTA1": 12.5, "CROTA2": 34.75}
    ) == pytest.approx(-34.75)


def test_north_angle_returns_none_for_empty_header():
    """Caller falls back to project default when header is uninformative."""
    assert north_angle_from_fits_header({}) is None
    assert north_angle_from_fits_header(None) is None


def test_apply_wcs_flip_returns_same_wcs_instance():
    """Helper mutates in place and returns the wcs for chaining."""
    wcs = build_wcs(
        center_ra_deg=180.0, center_dec_deg=45.0,
        frame_dims=(1000, 800), pixel_scale_arcsec=1.0,
        north_angle_deg=10.0,
    )
    assert apply_wcs_flip(wcs) is wcs


def test_apply_wcs_flip_is_180deg_rotation_around_crpix():
    """A pixel offset from CRPIX maps to its point-mirror after flip."""
    wcs = build_wcs(
        center_ra_deg=180.0, center_dec_deg=45.0,
        frame_dims=(1000, 800), pixel_scale_arcsec=1.0,
        north_angle_deg=10.0,
    )
    px0, py0 = 700.0, 200.0
    ra, dec = wcs.pixel_to_world_values(px0, py0)
    apply_wcs_flip(wcs)
    px1, py1 = wcs.world_to_pixel_values(ra, dec)
    cx, cy = wcs.wcs.crpix[0] - 1, wcs.wcs.crpix[1] - 1
    assert px1 == pytest.approx(2 * cx - px0, abs=1.0)
    assert py1 == pytest.approx(2 * cy - py0, abs=1.0)


def test_apply_wcs_flip_twice_restores_projection():
    """Two flips return to the original projection within float tolerance."""
    wcs = build_wcs(
        center_ra_deg=180.0, center_dec_deg=45.0,
        frame_dims=(1000, 800), pixel_scale_arcsec=1.0,
        north_angle_deg=10.0,
    )
    x0, y0 = wcs.world_to_pixel_values(180.05, 45.03)
    apply_wcs_flip(wcs)
    apply_wcs_flip(wcs)
    x1, y1 = wcs.world_to_pixel_values(180.05, 45.03)
    assert x1 == pytest.approx(x0, abs=1e-6)
    assert y1 == pytest.approx(y0, abs=1e-6)
