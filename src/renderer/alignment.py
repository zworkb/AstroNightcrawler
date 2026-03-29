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
