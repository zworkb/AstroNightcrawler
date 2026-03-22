"""Cubic Bezier spline math for path evaluation and point sampling.

All coordinates use (ra, dec) tuples in degrees. Distance calculations
use flat Euclidean distance (no cos(dec) correction). This is accurate
for paths under ~15 degrees but increasingly inaccurate for larger
angular distances.
"""

from __future__ import annotations

import math

from src.models.project import ControlPoint, SplinePath

Point = tuple[float, float]


def bezier_point(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Evaluate a cubic Bezier curve at parameter t.

    Args:
        p0: Start point (ra, dec).
        p1: First control handle.
        p2: Second control handle.
        p3: End point (ra, dec).
        t: Parameter in [0, 1].

    Returns:
        The (ra, dec) point on the curve at parameter t.
    """
    u = 1.0 - t
    u2 = u * u
    t2 = t * t
    w0 = u2 * u
    w1 = 3.0 * u2 * t
    w2 = 3.0 * u * t2
    w3 = t2 * t
    x = w0 * p0[0] + w1 * p1[0] + w2 * p2[0] + w3 * p3[0]
    y = w0 * p0[1] + w1 * p1[1] + w2 * p2[1] + w3 * p3[1]
    return (x, y)


def bezier_segment_length(p0: Point, p1: Point, p2: Point, p3: Point) -> float:
    """Approximate the arc length of a cubic Bezier segment.

    Uses 100 linear subdivisions to approximate the curve length.
    Distances are flat Euclidean (no cos(dec) correction), accurate
    for paths under ~15 degrees.

    Args:
        p0: Start point (ra, dec).
        p1: First control handle.
        p2: Second control handle.
        p3: End point (ra, dec).

    Returns:
        Approximate arc length in degrees.
    """
    n = 100
    total = 0.0
    prev = p0
    for i in range(1, n + 1):
        t = i / n
        curr = bezier_point(p0, p1, p2, p3, t)
        dx = curr[0] - prev[0]
        dy = curr[1] - prev[1]
        total += math.sqrt(dx * dx + dy * dy)
        prev = curr
    return total


def _segment_handles(
    cp_start: ControlPoint, cp_end: ControlPoint,
) -> tuple[Point, Point, Point, Point]:
    """Extract Bezier control points from two ControlPoints.

    When handles are absent, defaults to 1/3 and 2/3 linear
    interpolation between the endpoints.

    Args:
        cp_start: The starting control point.
        cp_end: The ending control point.

    Returns:
        Tuple of (p0, p1, p2, p3) for the cubic Bezier.
    """
    p0: Point = (cp_start.ra, cp_start.dec)
    p3: Point = (cp_end.ra, cp_end.dec)

    if cp_start.handle_out is not None:
        p1 = (cp_start.handle_out.ra, cp_start.handle_out.dec)
    else:
        p1 = _lerp(p0, p3, 1.0 / 3.0)

    if cp_end.handle_in is not None:
        p2 = (cp_end.handle_in.ra, cp_end.handle_in.dec)
    else:
        p2 = _lerp(p0, p3, 2.0 / 3.0)

    return (p0, p1, p2, p3)


def _lerp(a: Point, b: Point, t: float) -> Point:
    """Linearly interpolate between two points."""
    return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))


def sample_points_along_spline(path: SplinePath, spacing_deg: float) -> list[Point]:
    """Walk a spline path and sample points at even spacing intervals.

    Walks the polyline defined by the path's Bezier segments and
    places sample points at the requested spacing. The first and last
    points always match the path endpoints.

    Distances are flat Euclidean (no cos(dec) correction), accurate
    for paths under ~15 degrees.

    Args:
        path: A SplinePath with at least 2 control points.
        spacing_deg: Distance between sample points in degrees.

    Returns:
        List of (ra, dec) sample points along the path.
    """
    polyline = _build_polyline(path)
    return _resample_polyline(polyline, spacing_deg)


def _build_polyline(path: SplinePath) -> list[Point]:
    """Convert a SplinePath into a dense polyline."""
    steps_per_segment = 100
    points: list[Point] = []
    cps = path.control_points
    for i in range(len(cps) - 1):
        p0, p1, p2, p3 = _segment_handles(cps[i], cps[i + 1])
        start = 0 if i == 0 else 1
        for j in range(start, steps_per_segment + 1):
            t = j / steps_per_segment
            points.append(bezier_point(p0, p1, p2, p3, t))
    return points


def _resample_polyline(polyline: list[Point], spacing: float) -> list[Point]:
    """Resample a polyline at even spacing, keeping endpoints."""
    if len(polyline) < 2:
        return list(polyline)

    result: list[Point] = [polyline[0]]
    remaining = spacing
    for i in range(1, len(polyline)):
        dx = polyline[i][0] - polyline[i - 1][0]
        dy = polyline[i][1] - polyline[i - 1][1]
        seg_len = math.sqrt(dx * dx + dy * dy)
        if seg_len == 0.0:
            continue
        traveled = 0.0
        while traveled + remaining <= seg_len:
            traveled += remaining
            frac = traveled / seg_len
            pt = _lerp(polyline[i - 1], polyline[i], frac)
            result.append(pt)
            remaining = spacing
        remaining -= seg_len - traveled

    # Always include the final endpoint
    last = polyline[-1]
    if result[-1] != last:
        result.append(last)

    return result
