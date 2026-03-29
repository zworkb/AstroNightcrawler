"""FITS to 8-bit sRGB stretch functions.

Stub — full implementation in Task 4.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class StretchParams:
    """Manual stretch parameters."""

    black: float = 0.0
    white: float = 1.0
    midtone: float = 0.5


def auto_stretch(data: np.ndarray) -> np.ndarray:
    """Apply ZScale + AsinhStretch and return 8-bit result.

    Args:
        data: Input array (uint16, float, any shape).

    Returns:
        8-bit numpy array.
    """
    raise NotImplementedError("Full auto_stretch in Task 4")


def manual_stretch(data: np.ndarray, params: StretchParams) -> np.ndarray:
    """Apply manual black/white/midtone stretch.

    Args:
        data: Input array.
        params: Stretch parameters.

    Returns:
        8-bit numpy array.
    """
    raise NotImplementedError("Full manual_stretch in Task 4")
