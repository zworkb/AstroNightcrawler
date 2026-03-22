"""Tests for freehand drawing: RDP simplification and Bézier fitting."""

import math

import pytest

from src.models.freehand import fit_bezier_to_points, rdp_simplify


class TestRdpSimplify:
    """Tests for the Ramer-Douglas-Peucker simplification."""

    def test_straight_line_unchanged(self) -> None:
        """A perfectly straight line should keep only endpoints."""
        points = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        result = rdp_simplify(points, epsilon=0.1)
        assert result == [(0.0, 0.0), (3.0, 3.0)]

    def test_zigzag_reduced(self) -> None:
        """A zigzag pattern should be reduced with moderate epsilon."""
        points = [
            (0.0, 0.0), (1.0, 1.0), (2.0, 0.0),
            (3.0, 1.0), (4.0, 0.0),
        ]
        result = rdp_simplify(points, epsilon=1.5)
        assert len(result) < len(points)
        assert result[0] == points[0]
        assert result[-1] == points[-1]

    def test_epsilon_zero_keeps_all(self) -> None:
        """With epsilon=0 every point is kept."""
        points = [(0.0, 0.0), (1.0, 0.5), (2.0, 0.0)]
        result = rdp_simplify(points, epsilon=0.0)
        assert result == points

    def test_larger_epsilon_fewer_points(self) -> None:
        """Increasing epsilon should never increase the point count."""
        points = [
            (0.0, 0.0), (1.0, 0.8), (2.0, 0.1),
            (3.0, 0.9), (4.0, 0.0),
        ]
        result_small = rdp_simplify(points, epsilon=0.3)
        result_large = rdp_simplify(points, epsilon=1.5)
        assert len(result_large) <= len(result_small)

    def test_two_points_returned_as_is(self) -> None:
        """Two points should be returned unchanged."""
        points = [(0.0, 0.0), (5.0, 5.0)]
        assert rdp_simplify(points, epsilon=1.0) == points


class TestFitBezierToPoints:
    """Tests for Bézier handle fitting."""

    def test_two_points_produce_two_control_points(self) -> None:
        """Two input points should yield exactly two ControlPoints."""
        pts = [(10.0, 20.0), (12.0, 22.0)]
        result = fit_bezier_to_points(pts)
        assert len(result) == 2

    def test_first_has_only_handle_out(self) -> None:
        """The first control point should have handle_out but not handle_in."""
        pts = [(10.0, 20.0), (12.0, 22.0)]
        result = fit_bezier_to_points(pts)
        assert result[0].handle_out is not None
        assert result[0].handle_in is None

    def test_last_has_only_handle_in(self) -> None:
        """The last control point should have handle_in but not handle_out."""
        pts = [(10.0, 20.0), (12.0, 22.0)]
        result = fit_bezier_to_points(pts)
        assert result[-1].handle_in is not None
        assert result[-1].handle_out is None

    def test_middle_points_have_both_handles(self) -> None:
        """Interior points should have both handle_in and handle_out."""
        pts = [(10.0, 20.0), (12.0, 22.0), (14.0, 20.0)]
        result = fit_bezier_to_points(pts)
        mid = result[1]
        assert mid.handle_in is not None
        assert mid.handle_out is not None

    def test_handle_distance_is_one_third_segment(self) -> None:
        """Handles should be at 1/3 of the adjacent segment length."""
        pts = [(10.0, 20.0), (13.0, 20.0)]
        result = fit_bezier_to_points(pts)
        h_out = result[0].handle_out
        assert h_out is not None
        dist = math.hypot(h_out.ra - 10.0, h_out.dec - 20.0)
        assert dist == pytest.approx(1.0, abs=1e-9)

    def test_fewer_than_two_points_raises(self) -> None:
        """Fitting with fewer than 2 points should raise ValueError."""
        with pytest.raises(ValueError, match="at least 2"):
            fit_bezier_to_points([(5.0, 5.0)])
