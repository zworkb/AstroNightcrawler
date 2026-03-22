"""Tests for cubic Bezier spline math."""

import pytest

from src.models.project import ControlPoint, Coordinate, SplinePath
from src.models.spline import (
    bezier_point,
    bezier_segment_length,
    sample_points_along_spline,
)

# A straight horizontal line from (0,0) to (3,0) with evenly-spaced handles
STRAIGHT_P0 = (0.0, 0.0)
STRAIGHT_P1 = (1.0, 0.0)
STRAIGHT_P2 = (2.0, 0.0)
STRAIGHT_P3 = (3.0, 0.0)


class TestBezierPoint:
    def test_t0_returns_start(self) -> None:
        result = bezier_point(STRAIGHT_P0, STRAIGHT_P1, STRAIGHT_P2, STRAIGHT_P3, 0.0)
        assert result == pytest.approx(STRAIGHT_P0)

    def test_t1_returns_end(self) -> None:
        result = bezier_point(STRAIGHT_P0, STRAIGHT_P1, STRAIGHT_P2, STRAIGHT_P3, 1.0)
        assert result == pytest.approx(STRAIGHT_P3)

    def test_t05_on_straight_line_returns_midpoint(self) -> None:
        result = bezier_point(STRAIGHT_P0, STRAIGHT_P1, STRAIGHT_P2, STRAIGHT_P3, 0.5)
        assert result == pytest.approx((1.5, 0.0))

    def test_curved_segment(self) -> None:
        p0 = (0.0, 0.0)
        p1 = (0.0, 1.0)
        p2 = (1.0, 1.0)
        p3 = (1.0, 0.0)
        result = bezier_point(p0, p1, p2, p3, 0.5)
        assert result[0] == pytest.approx(0.5)
        assert result[1] == pytest.approx(0.75)


class TestBezierSegmentLength:
    def test_straight_line_length(self) -> None:
        length = bezier_segment_length(
            STRAIGHT_P0, STRAIGHT_P1, STRAIGHT_P2, STRAIGHT_P3
        )
        assert length == pytest.approx(3.0, abs=0.01)

    def test_curved_longer_than_chord(self) -> None:
        p0 = (0.0, 0.0)
        p1 = (0.0, 2.0)
        p2 = (3.0, 2.0)
        p3 = (3.0, 0.0)
        length = bezier_segment_length(p0, p1, p2, p3)
        chord = 3.0
        assert length > chord

    def test_zero_length_segment(self) -> None:
        p = (5.0, 5.0)
        length = bezier_segment_length(p, p, p, p)
        assert length == pytest.approx(0.0)


class TestSamplePointsAlongSpline:
    @staticmethod
    def _straight_path() -> SplinePath:
        """A straight path from (0,0) to (6,0)."""
        return SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0),
                ControlPoint(ra=6.0, dec=0.0),
            ]
        )

    def test_first_and_last_match_endpoints(self) -> None:
        path = self._straight_path()
        pts = sample_points_along_spline(path, 1.0)
        assert pts[0] == pytest.approx((0.0, 0.0), abs=0.05)
        assert pts[-1] == pytest.approx((6.0, 0.0), abs=0.05)

    def test_correct_count_for_spacing(self) -> None:
        path = self._straight_path()
        pts = sample_points_along_spline(path, 2.0)
        # 6 degrees / 2 deg spacing → expect 4 points (0, 2, 4, 6)
        assert len(pts) == 4

    def test_finer_spacing_yields_more_points(self) -> None:
        path = self._straight_path()
        coarse = sample_points_along_spline(path, 2.0)
        fine = sample_points_along_spline(path, 1.0)
        assert len(fine) > len(coarse)

    def test_multi_segment_path(self) -> None:
        path = SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0),
                ControlPoint(ra=3.0, dec=0.0),
                ControlPoint(ra=6.0, dec=0.0),
            ]
        )
        pts = sample_points_along_spline(path, 1.0)
        assert pts[0] == pytest.approx((0.0, 0.0), abs=0.05)
        assert pts[-1] == pytest.approx((6.0, 0.0), abs=0.05)
        assert len(pts) == 7  # 0,1,2,3,4,5,6

    def test_with_explicit_handles(self) -> None:
        path = SplinePath(
            control_points=[
                ControlPoint(
                    ra=0.0, dec=0.0,
                    handle_out=Coordinate(ra=1.0, dec=1.0),
                ),
                ControlPoint(
                    ra=3.0, dec=0.0,
                    handle_in=Coordinate(ra=2.0, dec=1.0),
                ),
            ]
        )
        pts = sample_points_along_spline(path, 0.5)
        # Curved path should be longer than 3 deg, so more points
        assert len(pts) > 6
        assert pts[0] == pytest.approx((0.0, 0.0), abs=0.05)
        assert pts[-1] == pytest.approx((3.0, 0.0), abs=0.05)
