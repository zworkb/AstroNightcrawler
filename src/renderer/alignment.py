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

        from src.config import settings

        # Optional downsampling for speed
        scale = 1
        a, b = frame_a, frame_b
        target = settings.render_align_max_dim
        if target > 0:
            max_dim = max(a.shape[0], a.shape[1])
            if max_dim > target:
                scale = max_dim / target
                step = int(scale)
                a = a[::step, ::step]
                b = b[::step, ::step]
                logger.info("Alignment downsampled %dx (%s → %s)", step, frame_a.shape, a.shape)

        logger.info("Running find_transform on %s frames...", a.shape)
        import time as _time
        t0 = _time.monotonic()
        transform, _ = aa.find_transform(b, a, detection_sigma=settings.render_align_sigma)
        t1 = _time.monotonic()
        logger.info("find_transform completed in %.1fs", t1 - t0)
        dx = transform.translation[0] * scale
        dy = transform.translation[1] * scale
        rotation = math.degrees(math.atan2(transform.params[1][0], transform.params[0][0]))
        logger.info("Alignment: dx=%.1f dy=%.1f rot=%.2f°", dx, dy, rotation)
        return AlignmentResult(dx=dx, dy=dy, rotation=rotation, success=True)
    except Exception as exc:
        logger.warning("Alignment failed: %s", exc)
        return AlignmentResult(success=False)


def filter_outlier_alignments(
    results: list[AlignmentResult],
) -> list[AlignmentResult]:
    """Fix failed alignments by replacing with median of successful ones.

    Only failed alignments (success=False) are replaced.
    Successful alignments are kept as-is, even if they differ
    significantly from the median — this supports non-linear paths
    where dx/dy intentionally varies.

    Args:
        results: Alignment results for each adjacent pair.

    Returns:
        New list with failures replaced by median.
    """
    if len(results) < 2:
        return list(results)

    good = [r for r in results if r.success]
    if not good:
        logger.warning("No successful alignments — cannot filter")
        return list(results)

    good_dxs = np.array([r.dx for r in good])
    good_dys = np.array([r.dy for r in good])
    med_dx = float(np.median(good_dxs))
    med_dy = float(np.median(good_dys))
    n_failed = sum(1 for r in results if not r.success)
    logger.info(
        "Alignment stats: %d/%d successful, %d failed, median dx=%.1f dy=%.1f",
        len(good), len(results), n_failed, med_dx, med_dy,
    )

    if n_failed == 0:
        return list(results)

    fixed: list[AlignmentResult] = []
    for i, r in enumerate(results):
        if not r.success:
            logger.info(
                "Pair %d failed — replacing with median (dx=%.1f dy=%.1f)",
                i, med_dx, med_dy,
            )
            fixed.append(AlignmentResult(
                dx=med_dx, dy=med_dy, success=True,
            ))
        else:
            fixed.append(r)

    return fixed


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
