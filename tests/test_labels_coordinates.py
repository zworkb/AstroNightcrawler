"""Tests for label coordinate helpers (pure functions)."""

from __future__ import annotations

import math

from src.renderer.alignment import AlignmentResult
from src.renderer.labels import (
    catalog_to_ref_pixel,
    cumulative_offset,
    project_label_to_frame,
)


def _aln(dx: float, dy: float) -> AlignmentResult:
    return AlignmentResult(dx=dx, dy=dy, rotation=0.0, success=True)


def test_cumulative_offset_zero_when_same_index():
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    assert cumulative_offset(alignments, 1, 1) == (0.0, 0.0)


def test_cumulative_offset_forward():
    """alignments[i] is the shift from frame i to frame i+1."""
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0), _aln(5.0, 7.0)]
    # ref=0, target=2: sum of alignments[0] + alignments[1] = (30, 2)
    assert cumulative_offset(alignments, 0, 2) == (30.0, 2.0)


def test_cumulative_offset_backward():
    """Going backward negates each contributing offset."""
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    # ref=2, target=0: -(alignments[0] + alignments[1]) = (-30, -2)
    assert cumulative_offset(alignments, 2, 0) == (-30.0, -2.0)


from src.models.project import Label


def _label(ref_idx: int, x: float, y: float) -> Label:
    return Label(id="test", text="t", ref_frame_index=ref_idx, x=x, y=y)


def test_project_label_to_frame_identity():
    """Label on its own ref frame projects to its own (x, y)."""
    label = _label(ref_idx=0, x=100.0, y=200.0)
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 0, alignments, frame_dims)
    assert (px, py) == (100.0, 200.0)
    assert in_view is True


def test_project_label_to_frame_subtracts_cumulative_offset():
    """If the image shifted by (dx, dy) from ref to current frame, the
    static sky-point label appears at (x - dx, y - dy) in the current."""
    label = _label(ref_idx=0, x=100.0, y=200.0)
    alignments = [_aln(10.0, 5.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 1, alignments, frame_dims)
    assert (px, py) == (90.0, 195.0)
    assert in_view is True


def test_project_label_to_frame_out_of_bounds():
    label = _label(ref_idx=0, x=10.0, y=10.0)
    alignments = [_aln(2000.0, 0.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 1, alignments, frame_dims)
    assert in_view is False  # px = 10 - 2000 = -1990


def test_catalog_to_ref_pixel_center_maps_to_frame_center():
    """A catalog point at the same RA/Dec as the frame center lands at the
    frame's center pixel."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=45.0,
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(1280, 720),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    assert math.isclose(x, 640.0)
    assert math.isclose(y, 360.0)


def test_catalog_to_ref_pixel_eastward_offset_moves_left_in_pixels():
    """East = +RA on sky; in image space (north up), east is to the left
    (mirror of standard plate convention since the image already has
    pixel x increasing rightward = west)."""
    # 1° = 3600 arcsec; at 1 arcsec/px that's 3600 px shift
    x, y = catalog_to_ref_pixel(
        ra_deg=181.0,  # 1° east of center
        dec_deg=45.0,
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    # cos(45°) compensation on RA: 1° on sky = cos(45°) ≈ 0.707° in projected angle
    expected_offset = -3600.0 * math.cos(math.radians(45.0))
    assert math.isclose(x, 4000.0 + expected_offset, abs_tol=0.5)
    assert math.isclose(y, 3000.0, abs_tol=0.5)


def test_catalog_to_ref_pixel_northward_offset_moves_up_in_pixels():
    """+Dec = north; in image space (north up), north is upward = pixel y decreasing."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=46.0,  # 1° north
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    assert math.isclose(x, 4000.0, abs_tol=0.5)
    assert math.isclose(y, 3000.0 - 3600.0, abs_tol=0.5)


def test_catalog_to_ref_pixel_orientation_rotation():
    """A 90° north_angle_deg rotates so that 'north on sky' lands rightward in pixels."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=46.0,  # 1° north
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=90.0,
    )
    # 90° rotation: north → +x in pixels
    assert math.isclose(x, 4000.0 + 3600.0, abs_tol=0.5)
    assert math.isclose(y, 3000.0, abs_tol=0.5)
