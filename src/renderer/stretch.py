"""FITS image stretch / tonmapping to visible 8-bit sRGB."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from astropy.visualization import AsinhStretch, ZScaleInterval

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
    interval = ZScaleInterval()
    stretch = AsinhStretch()
    fdata = data.astype(np.float64)

    if fdata.ndim == 3:
        result = np.empty_like(fdata)
        for ch in range(fdata.shape[2]):
            vmin, vmax = interval.get_limits(fdata[:, :, ch])
            normed = np.clip((fdata[:, :, ch] - vmin) / (vmax - vmin + 1e-10), 0, 1)
            result[:, :, ch] = stretch(normed)
        return (result * 255).astype(np.uint8)

    vmin, vmax = interval.get_limits(fdata)
    normed = np.clip((fdata - vmin) / (vmax - vmin + 1e-10), 0, 1)
    stretched = stretch(normed)
    return (stretched * 255).astype(np.uint8)


def histogram_stretch(
    data: np.ndarray,
    low: float = 0.001,
    high: float = 0.999,
) -> np.ndarray:
    """Apply percentile-based histogram stretch.

    Args:
        data: Input array.
        low: Lower percentile cutoff.
        high: Upper percentile cutoff.

    Returns:
        8-bit numpy array.
    """
    fdata = data.astype(np.float64)
    vmin = np.percentile(fdata, low * 100)
    vmax = np.percentile(fdata, high * 100)
    normed = np.clip((fdata - vmin) / (vmax - vmin + 1e-10), 0, 1)
    return (normed * 255).astype(np.uint8)


def manual_stretch(
    data: np.ndarray,
    params: StretchParams,
    mono_to_rgb: bool = False,
) -> np.ndarray:
    """Apply manual stretch with user-defined parameters.

    Args:
        data: Input array.
        params: Black/white/midtone settings (0..1 range).
        mono_to_rgb: If True, replicate mono to 3-channel.

    Returns:
        8-bit numpy array.
    """
    fdata = data.astype(np.float64)
    dmax = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
    normed = fdata / dmax
    normed = np.clip((normed - params.black) / (params.white - params.black + 1e-10), 0, 1)
    # Midtone gamma correction
    gamma = 1.0 / (params.midtone + 0.01)
    normed = np.power(normed, gamma)
    result = (normed * 255).astype(np.uint8)

    if mono_to_rgb and result.ndim == 2:
        result = np.stack([result, result, result], axis=2)

    return result


def apply_stretch(
    data: np.ndarray,
    mode: str = "auto",
    params: StretchParams | None = None,
    mono_to_rgb: bool = False,
) -> np.ndarray:
    """Apply stretch based on mode selection.

    Args:
        data: Input FITS data.
        mode: "auto", "histogram", or "manual".
        params: Manual parameters (required if mode=="manual").
        mono_to_rgb: Convert mono to 3-channel.

    Returns:
        8-bit sRGB numpy array.
    """
    if mode == "auto":
        result = auto_stretch(data)
    elif mode == "histogram":
        result = histogram_stretch(data)
    elif mode == "manual" and params:
        result = manual_stretch(data, params)
    else:
        result = auto_stretch(data)

    if mono_to_rgb and result.ndim == 2:
        result = np.stack([result, result, result], axis=2)

    return result
