"""Star alignment between adjacent frames using astroalign."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    """Result of aligning two frames."""

    dx: float = 0.0
    dy: float = 0.0
    rotation: float = 0.0
    success: bool = False


def align_pair(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
) -> AlignmentResult:
    """Compute the alignment offset between two frames.

    Uses astroalign to find matching star triangles and compute
    the affine transformation.

    Args:
        frame_a: Reference frame (2D uint8 or uint16).
        frame_b: Target frame to align to frame_a.

    Returns:
        AlignmentResult with translation and rotation.
    """
    try:
        import astroalign as aa

        transform, _ = aa.find_transform(frame_b, frame_a)
        dx = transform.translation[0]
        dy = transform.translation[1]
        rotation = math.degrees(math.atan2(transform.params[1][0], transform.params[0][0]))
        logger.info("Alignment: dx=%.1f dy=%.1f rot=%.2f°", dx, dy, rotation)
        return AlignmentResult(dx=dx, dy=dy, rotation=rotation, success=True)
    except Exception as exc:
        logger.warning("Alignment failed: %s", exc)
        return AlignmentResult(success=False)


def filter_outlier_alignments(
    results: list[AlignmentResult],
) -> list[AlignmentResult]:
    """Replace outlier alignment results with median values.

    Any result whose dx or dy deviates more than 2 standard deviations
    from the median is replaced with the median offset.

    Args:
        results: Alignment results for each adjacent pair.

    Returns:
        New list with outliers replaced by median values.
    """
    if len(results) < 3:
        return list(results)

    dxs = np.array([r.dx for r in results])
    dys = np.array([r.dy for r in results])

    med_dx = float(np.median(dxs))
    med_dy = float(np.median(dys))
    std_dx = float(np.std(dxs))
    std_dy = float(np.std(dys))

    filtered: list[AlignmentResult] = []
    for i, r in enumerate(results):
        is_outlier = False
        if std_dx > 0 and abs(r.dx - med_dx) > 2 * std_dx:
            is_outlier = True
        if std_dy > 0 and abs(r.dy - med_dy) > 2 * std_dy:
            is_outlier = True
        if is_outlier:
            logger.warning(
                "Alignment outlier at pair %d: dx=%.1f dy=%.1f "
                "(median dx=%.1f dy=%.1f) — replaced with median",
                i, r.dx, r.dy, med_dx, med_dy,
            )
            filtered.append(AlignmentResult(
                dx=med_dx, dy=med_dy, rotation=r.rotation, success=r.success,
            ))
        else:
            filtered.append(r)

    return filtered


def compute_crop_margins(
    results: list[AlignmentResult],
) -> tuple[int, int]:
    """Compute crop margins from alignment results.

    Returns margins large enough to prevent black edges
    during any pairwise linear pan transition.

    Args:
        results: Alignment results for each adjacent pair.

    Returns:
        Tuple of (margin_x, margin_y) in pixels.
    """
    margin_x = 0.0
    margin_y = 0.0
    for r in results:
        margin_x = max(margin_x, abs(r.dx))
        margin_y = max(margin_y, abs(r.dy))
    return math.ceil(margin_x), math.ceil(margin_y)
