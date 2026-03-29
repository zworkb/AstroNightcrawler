"""Tests for FITS stretch / tonmapping."""

import numpy as np

from src.renderer.stretch import (
    StretchParams,
    apply_stretch,
    auto_stretch,
    manual_stretch,
)


class TestAutoStretch:
    def test_returns_8bit_array(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.dtype == np.uint8
        assert result.shape == (100, 100)

    def test_output_uses_full_range(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.max() > 200
        assert result.min() < 50

    def test_color_image(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100, 3), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.dtype == np.uint8
        assert result.shape == (100, 100, 3)


class TestManualStretch:
    def test_custom_black_white(self) -> None:
        data = np.linspace(0, 65535, 256, dtype=np.uint16).reshape(16, 16)
        params = StretchParams(black=0.1, white=0.9, midtone=0.5)
        result = manual_stretch(data, params)
        assert result.dtype == np.uint8

    def test_mono_to_rgb_flag(self) -> None:
        data = np.zeros((16, 16), dtype=np.uint16)
        params = StretchParams()
        result = manual_stretch(data, params, mono_to_rgb=True)
        assert result.ndim == 3
        assert result.shape[2] == 3


class TestApplyStretch:
    def test_auto_mode(self) -> None:
        data = np.random.randint(0, 65535, (50, 50), dtype=np.uint16)
        result = apply_stretch(data, mode="auto")
        assert result.dtype == np.uint8

    def test_histogram_mode(self) -> None:
        data = np.random.randint(0, 65535, (50, 50), dtype=np.uint16)
        result = apply_stretch(data, mode="histogram")
        assert result.dtype == np.uint8
