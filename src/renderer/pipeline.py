"""Rendering pipeline orchestration."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.renderer.alignment import AlignmentResult, align_pair, compute_crop_margins
from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer
from src.renderer.importer import FrameInfo, load_frame, load_manifest
from src.renderer.stretch import StretchParams, apply_stretch
from src.renderer.transitions import crossfade, linear_pan
from src.renderer.video import check_ffmpeg, encode_video, write_frame_png

logger = logging.getLogger(__name__)


@dataclass
class RenderConfig:
    """Configuration for a render job."""

    fps: int = 24
    crf: int = 18
    stretch_mode: str = "auto"
    stretch_params: StretchParams | None = None
    debayer_mode: DebayerMode = DebayerMode.AUTO
    transition: str = "crossfade"
    crossfade_frames: int = 6
    keep_frames: bool = False
    temp_dir: Path | None = None


class RenderPipeline:
    """Orchestrates the full rendering pipeline.

    Usage:
        pipeline = RenderPipeline(capture_dir, config)
        pipeline.load()
        pipeline.render(output_path)
    """

    def __init__(self, capture_dir: Path, config: RenderConfig) -> None:
        self.capture_dir = capture_dir
        self.config = config
        self.frames: list[FrameInfo] = []
        self._alignments: list[AlignmentResult] = []

    def load(self) -> None:
        """Load manifest and frame metadata."""
        self.frames = load_manifest(self.capture_dir)
        logger.info("Loaded %d frames", len(self.frames))

    def active_frames(self) -> list[FrameInfo]:
        """Return non-skipped frames."""
        return [f for f in self.frames if not f.skipped]

    def skip_frame(self, index: int) -> None:
        """Mark a frame as skipped."""
        for f in self.frames:
            if f.index == index:
                f.skipped = True
                return

    def stretch_frame(self, frame_idx: int) -> np.ndarray:
        """Load, debayer, and stretch a single frame.

        Args:
            frame_idx: Index into self.frames list.

        Returns:
            8-bit sRGB numpy array.
        """
        frame = self.frames[frame_idx]
        data = load_frame(frame)
        pattern = detect_bayer(frame.bayer_pattern, self.config.debayer_mode)
        debayered = debayer_frame(data, pattern)
        return apply_stretch(
            debayered,
            mode=self.config.stretch_mode,
            params=self.config.stretch_params,
            mono_to_rgb=True,
        )

    def render(self, output_path: Path) -> None:
        """Run the full pipeline and produce a video file.

        Args:
            output_path: Path for the output video file.
        """
        if not check_ffmpeg():
            msg = "ffmpeg not found"
            raise RuntimeError(msg)

        active = self.active_frames()
        if len(active) < 2:
            msg = "Need at least 2 frames to render"
            raise RuntimeError(msg)

        temp = self._get_temp_dir()
        try:
            self._render_to_dir(active, temp)
            encode_video(temp, output_path, self.config.fps, self.config.crf)
        finally:
            if not self.config.keep_frames:
                shutil.rmtree(temp, ignore_errors=True)

    def _render_to_dir(self, active: list[FrameInfo], temp: Path) -> None:
        """Process all frames and write PNGs to temp directory."""
        frame_counter = 0

        # Stretch all active frames
        stretched: list[np.ndarray] = []
        for i, frame in enumerate(active):
            idx = self.frames.index(frame)
            logger.info("Processing frame %d/%d", i + 1, len(active))
            stretched.append(self.stretch_frame(idx))

        # Align if needed for linear pan
        margins = (0, 0)
        if self.config.transition == "linear-pan" and len(stretched) > 1:
            self._alignments = []
            for i in range(len(stretched) - 1):
                mono_a = _to_mono(stretched[i])
                mono_b = _to_mono(stretched[i + 1])
                self._alignments.append(align_pair(mono_a, mono_b))
            margins = compute_crop_margins(self._alignments)

        # Generate output frames with transitions
        for i in range(len(stretched)):
            frame_img = stretched[i]
            if margins != (0, 0):
                mx, my = margins
                frame_img = frame_img[my:frame_img.shape[0] - my, mx:frame_img.shape[1] - mx]
            write_frame_png(frame_img, temp, frame_counter)
            frame_counter += 1

            # Add transition frames between this and next
            if i < len(stretched) - 1:
                trans = self._make_transition(stretched, i, margins)
                for tf in trans:
                    write_frame_png(tf, temp, frame_counter)
                    frame_counter += 1

        logger.info("Wrote %d total frames to %s", frame_counter, temp)

    def _make_transition(
        self,
        stretched: list[np.ndarray],
        i: int,
        margins: tuple[int, int],
    ) -> list[np.ndarray]:
        """Generate transition frames between stretched[i] and stretched[i+1]."""
        if self.config.transition == "crossfade":
            return crossfade(stretched[i], stretched[i + 1], self.config.crossfade_frames)
        if self.config.transition == "linear-pan" and self._alignments:
            return linear_pan(
                stretched[i], stretched[i + 1],
                self._alignments[i],
                self.config.crossfade_frames,
                margins[0], margins[1],
            )
        return []

    def _get_temp_dir(self) -> Path:
        """Get or create the temporary frame directory."""
        if self.config.temp_dir:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            return self.config.temp_dir
        return Path(tempfile.mkdtemp(prefix="nc-render-"))


def _to_mono(frame: np.ndarray) -> np.ndarray:
    """Convert RGB to mono for alignment."""
    if frame.ndim == 3:
        return np.mean(frame, axis=2).astype(np.uint8)
    return frame
