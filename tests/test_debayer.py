"""Tests for Bayer pattern detection and demosaicing."""

import numpy as np

from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer


class TestDetectBayer:
    def test_returns_pattern_from_header(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.AUTO) == "RGGB"

    def test_auto_mode_none_header_returns_none(self) -> None:
        assert detect_bayer(None, DebayerMode.AUTO) is None

    def test_override_ignores_header(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.GBRG) == "GBRG"

    def test_off_mode_returns_none(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.OFF) is None


class TestDebayerFrame:
    def test_mono_passthrough(self) -> None:
        mono = np.zeros((100, 100), dtype=np.uint16)
        result = debayer_frame(mono, None)
        assert result.ndim == 2
        assert result.shape == (100, 100)

    def test_rggb_produces_color(self) -> None:
        raw = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        result = debayer_frame(raw, "RGGB")
        assert result.ndim == 3
        assert result.shape[2] == 3  # RGB
        assert result.shape[0] == 100
        assert result.shape[1] == 100

    def test_already_color_passthrough(self) -> None:
        color = np.zeros((100, 100, 3), dtype=np.uint16)
        result = debayer_frame(color, None)
        assert result.ndim == 3
