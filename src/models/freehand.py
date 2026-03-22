"""Freehand drawing utilities: RDP simplification and Bézier fitting."""

import math

from src.models.project import ControlPoint, Coordinate


def _perpendicular_distance(
    point: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    """Return perpendicular distance from *point* to the line segment."""
    dx = line_end[0] - line_start[0]
    dy = line_end[1] - line_start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.hypot(point[0] - line_start[0], point[1] - line_start[1])
    t = ((point[0] - line_start[0]) * dx + (point[1] - line_start[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    proj_x = line_start[0] + t * dx
    proj_y = line_start[1] + t * dy
    return math.hypot(point[0] - proj_x, point[1] - proj_y)


def rdp_simplify(
    points: list[tuple[float, float]],
    epsilon: float,
) -> list[tuple[float, float]]:
    """Simplify a polyline using the Ramer-Douglas-Peucker algorithm.

    Args:
        points: Ordered polyline vertices as (x, y) tuples.
        epsilon: Maximum allowed perpendicular distance; larger values
            produce fewer output points.

    Returns:
        A simplified list of (x, y) tuples retaining the first and last
        points.
    """
    if len(points) <= 2:
        return list(points)

    max_dist = 0.0
    max_idx = 0
    for i in range(1, len(points) - 1):
        dist = _perpendicular_distance(points[i], points[0], points[-1])
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist > epsilon:
        left = rdp_simplify(points[: max_idx + 1], epsilon)
        right = rdp_simplify(points[max_idx:], epsilon)
        return left[:-1] + right

    return [points[0], points[-1]]


def _tangent_vector(
    prev: tuple[float, float],
    nxt: tuple[float, float],
) -> tuple[float, float]:
    """Return the unit tangent direction from *prev* to *nxt*."""
    dx = nxt[0] - prev[0]
    dy = nxt[1] - prev[1]
    length = math.hypot(dx, dy)
    if length == 0.0:
        return (0.0, 0.0)
    return (dx / length, dy / length)


def fit_bezier_to_points(
    points: list[tuple[float, float]],
) -> list[ControlPoint]:
    """Convert a simplified polyline into ControlPoints with Bézier handles.

    Tangent at each interior point is derived from its neighbours
    (Catmull-Rom style) and scaled to one-third of the adjacent segment
    length.

    Args:
        points: Ordered polyline vertices as (ra, dec) tuples.

    Returns:
        A list of ``ControlPoint`` instances with appropriate handles.
    """
    n = len(points)
    if n < 2:
        msg = "Need at least 2 points to fit Bézier curves"
        raise ValueError(msg)

    result: list[ControlPoint] = []
    for i, pt in enumerate(points):
        handle_in: Coordinate | None = None
        handle_out: Coordinate | None = None

        if i == 0:
            tang = _tangent_vector(points[0], points[1])
            seg_len = math.hypot(points[1][0] - pt[0], points[1][1] - pt[1])
            scale = seg_len / 3.0
            handle_out = Coordinate(
                ra=pt[0] + tang[0] * scale,
                dec=pt[1] + tang[1] * scale,
            )
        elif i == n - 1:
            tang = _tangent_vector(points[-2], points[-1])
            seg_len = math.hypot(pt[0] - points[-2][0], pt[1] - points[-2][1])
            scale = seg_len / 3.0
            handle_in = Coordinate(
                ra=pt[0] - tang[0] * scale,
                dec=pt[1] - tang[1] * scale,
            )
        else:
            tang = _tangent_vector(points[i - 1], points[i + 1])
            seg_in = math.hypot(pt[0] - points[i - 1][0], pt[1] - points[i - 1][1])
            seg_out = math.hypot(points[i + 1][0] - pt[0], points[i + 1][1] - pt[1])
            handle_in = Coordinate(
                ra=pt[0] - tang[0] * seg_in / 3.0,
                dec=pt[1] - tang[1] * seg_in / 3.0,
            )
            handle_out = Coordinate(
                ra=pt[0] + tang[0] * seg_out / 3.0,
                dec=pt[1] + tang[1] * seg_out / 3.0,
            )

        result.append(ControlPoint(
            ra=pt[0], dec=pt[1],
            handle_in=handle_in, handle_out=handle_out,
        ))

    return result
