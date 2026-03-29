"""Bayer pattern detection and demosaicing."""

from __future__ import annotations

import enum
import logging

import numpy as np

logger = logging.getLogger(__name__)


class DebayerMode(enum.Enum):
    """Debayer mode selection."""

    AUTO = "auto"
    OFF = "off"
    RGGB = "RGGB"
    GBRG = "GBRG"
    GRBG = "GRBG"
    BGGR = "BGGR"


def detect_bayer(
    header_pattern: str | None,
    mode: DebayerMode,
) -> str | None:
    """Determine the Bayer pattern to use.

    Args:
        header_pattern: Pattern from FITS BAYERPAT header.
        mode: User-selected debayer mode.

    Returns:
        Bayer pattern string, or None if no debayering needed.
    """
    if mode == DebayerMode.OFF:
        return None
    if mode == DebayerMode.AUTO:
        return header_pattern
    return mode.value


def debayer_frame(
    data: np.ndarray,
    pattern: str | None,
) -> np.ndarray:
    """Apply demosaicing to a raw Bayer frame.

    Args:
        data: 2D raw CFA array or 3D already-debayered array.
        pattern: Bayer pattern (RGGB, GBRG, etc.) or None for mono.

    Returns:
        2D mono array or 3D RGB array.
    """
    if data.ndim == 3:
        return data  # Already color

    if pattern is None:
        return data  # Mono, no debayering

    from colour_demosaicing import demosaicing_CFA_Bayer_bilinear

    logger.info("Debayering with pattern %s", pattern)
    rgb = demosaicing_CFA_Bayer_bilinear(data.astype(np.float64), pattern)
    return rgb.astype(data.dtype)
