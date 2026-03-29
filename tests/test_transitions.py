"""Tests for frame transition generation."""

import numpy as np

from src.renderer.alignment import AlignmentResult
from src.renderer.transitions import crossfade, linear_pan


class TestCrossfade:
    def test_produces_correct_count(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert len(frames) == 4

    def test_first_is_mostly_a(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert frames[0].mean() < 100

    def test_last_is_mostly_b(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert frames[-1].mean() > 150


class TestLinearPan:
    def test_produces_correct_count(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5)
        assert len(frames) == 4

    def test_output_size_is_cropped(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5)
        # Crop: 100 - 2*5 = 90 wide, 100 - 2*5 = 90 tall
        assert frames[0].shape == (90, 90, 3)
