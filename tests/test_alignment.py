"""Tests for star alignment between frames."""

import numpy as np

from src.renderer.alignment import AlignmentResult, align_pair, compute_crop_margins


class TestAlignPair:
    def test_identical_frames_zero_offset(self) -> None:
        np.random.seed(42)
        frame = np.zeros((200, 200), dtype=np.uint8)
        # Add well-separated bright "stars" on dark background
        for _ in range(30):
            x, y = np.random.randint(10, 190, 2)
            frame[y - 2 : y + 3, x - 2 : x + 3] = 255
        result = align_pair(frame, frame)
        assert abs(result.dx) < 1.0
        assert abs(result.dy) < 1.0
        assert result.success

    def test_shifted_frame(self) -> None:
        np.random.seed(42)
        frame = np.zeros((200, 200), dtype=np.uint8)
        for _ in range(30):
            x, y = np.random.randint(20, 180, 2)
            frame[y - 2 : y + 3, x - 2 : x + 3] = 255
        shifted = np.roll(frame, 5, axis=1)  # shift 5px right
        result = align_pair(frame, shifted)
        assert result.success
        # astroalign find_transform(B, A) returns transform mapping B->A
        assert abs(abs(result.dx) - 5.0) < 2.0

    def test_failure_returns_identity(self) -> None:
        blank = np.zeros((50, 50), dtype=np.uint8)
        result = align_pair(blank, blank)
        assert not result.success
        assert result.dx == 0.0
        assert result.dy == 0.0


class TestComputeCropMargins:
    def test_no_shifts(self) -> None:
        results = [AlignmentResult(dx=0, dy=0, success=True)]
        mx, my = compute_crop_margins(results)
        assert mx == 0
        assert my == 0

    def test_bidirectional_shifts(self) -> None:
        results = [
            AlignmentResult(dx=5.0, dy=-3.0, success=True),
            AlignmentResult(dx=-2.0, dy=4.0, success=True),
        ]
        mx, my = compute_crop_margins(results)
        assert mx == 5  # max(abs(5), abs(-2))
        assert my == 4  # max(abs(-3), abs(4))
