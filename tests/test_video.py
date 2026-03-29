"""Tests for ffmpeg video encoding."""

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.renderer.video import check_ffmpeg, encode_video, write_frame_png


class TestCheckFfmpeg:
    def test_returns_true_if_installed(self) -> None:
        # ffmpeg should be available in most dev environments
        result = check_ffmpeg()
        if shutil.which("ffmpeg"):
            assert result is True
        else:
            assert result is False


class TestWriteFramePng:
    def test_writes_png(self, tmp_path: Path) -> None:
        frame = np.full((50, 50, 3), 128, dtype=np.uint8)
        path = write_frame_png(frame, tmp_path, index=0)
        assert path.exists()
        assert path.suffix == ".png"
        img = Image.open(path)
        assert img.size == (50, 50)


class TestEncodeVideo:
    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
    def test_produces_mp4(self, tmp_path: Path) -> None:
        # Write some test frames
        for i in range(10):
            frame = np.full((50, 50, 3), i * 25, dtype=np.uint8)
            write_frame_png(frame, tmp_path, index=i)
        output = tmp_path / "test.mp4"
        encode_video(tmp_path, output, fps=10, crf=23)
        assert output.exists()
        assert output.stat().st_size > 100
