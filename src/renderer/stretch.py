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


def derive_manual_params_from_auto(data: np.ndarray) -> StretchParams:
    """Compute manual params that approximate the auto_stretch result on this data.

    Black/white come from ZScale (averaged across channels for color data).
    Midtone approximates the default AsinhStretch curve.

    Args:
        data: Input array (uint16, float, any shape).

    Returns:
        StretchParams configured to roughly reproduce the auto stretch.
    """
    interval = ZScaleInterval()
    fdata = data.astype(np.float64)
    dmax = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0

    if fdata.ndim == 3:
        vmins = []
        vmaxs = []
        for ch in range(fdata.shape[2]):
            vmin_ch, vmax_ch = interval.get_limits(fdata[:, :, ch])
            vmins.append(vmin_ch)
            vmaxs.append(vmax_ch)
        vmin = float(np.mean(vmins))
        vmax = float(np.mean(vmaxs))
    else:
        vmin, vmax = interval.get_limits(fdata)
        vmin = float(vmin)
        vmax = float(vmax)

    black = float(np.clip(vmin / dmax, 0.0, 1.0))
    white = float(np.clip(vmax / dmax, 0.0, 1.0))
    # Default AsinhStretch (a=0.1) maps 0.5 -> ~0.771; solving 0.5^gamma=0.771
    # gives gamma ~ 0.375, i.e. midtone = 1/gamma - 0.01 ~ 2.65.
    midtone = 2.65
    return StretchParams(black=black, white=white, midtone=midtone)


def derive_manual_params_from_histogram(
    data: np.ndarray,
    low: float = 0.001,
    high: float = 0.999,
) -> StretchParams:
    """Compute manual params that approximate histogram_stretch on this data.

    Black/white come from percentile cutoffs (matching histogram_stretch).
    Midtone is linear (gamma ~ 1.0).

    Args:
        data: Input array (uint16, float, any shape).
        low: Lower percentile cutoff (0..1), matching histogram_stretch.
        high: Upper percentile cutoff (0..1), matching histogram_stretch.

    Returns:
        StretchParams configured to roughly reproduce the histogram stretch.
    """
    fdata = data.astype(np.float64)
    dmax = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0

    vmin = float(np.percentile(fdata, low * 100))
    vmax = float(np.percentile(fdata, high * 100))

    black = float(np.clip(vmin / dmax, 0.0, 1.0))
    white = float(np.clip(vmax / dmax, 0.0, 1.0))
    # Linear stretch: gamma = 1/(midtone+0.01) = 1.0 when midtone = 0.99.
    midtone = 0.99
    return StretchParams(black=black, white=white, midtone=midtone)


def compute_histogram(
    data: np.ndarray, bins: int = 256,
) -> dict[str, np.ndarray]:
    """Compute normalized histogram(s) for stretch widget display.

    Pixel values are normalized to ``[0, 1]`` by dividing by the maximum
    representable value of the input dtype (matching ``manual_stretch``),
    so the histogram x-axis aligns directly with the black/white handles.

    For 3-channel arrays a separate histogram per channel is returned
    (shape ``(bins, 3)``); for mono input shape is ``(bins,)``.

    Args:
        data: Input array (uint16, float, any shape).
        bins: Number of histogram bins.

    Returns:
        Dict with keys:
            - ``edges``: ``(bins+1,)`` float, bin edges in ``[0, 1]``.
            - ``counts``: raw counts, shape ``(bins,)`` or ``(bins, 3)``.
            - ``log_counts``: ``log10(counts + 1)``, same shape as counts.
    """
    fdata = data.astype(np.float64)
    if np.issubdtype(data.dtype, np.integer):
        dmax = float(np.iinfo(data.dtype).max)
    else:
        dmax = 1.0
    normed = fdata / dmax

    edges = np.linspace(0.0, 1.0, bins + 1, dtype=np.float64)

    if normed.ndim == 3 and normed.shape[2] == 3:
        counts = np.empty((bins, 3), dtype=np.int64)
        for ch in range(3):
            ch_counts, _ = np.histogram(normed[:, :, ch], bins=edges)
            counts[:, ch] = ch_counts
    else:
        flat = normed.ravel()
        counts_1d, _ = np.histogram(flat, bins=edges)
        counts = counts_1d.astype(np.int64)

    log_counts = np.log10(counts.astype(np.float64) + 1.0)
    return {"edges": edges, "counts": counts, "log_counts": log_counts}


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
