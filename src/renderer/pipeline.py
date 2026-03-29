"""Rendering pipeline orchestration."""

from __future__ import annotations

import logging
import math
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.config import settings
from src.renderer.alignment import (
    AlignmentResult,
    align_pair,
    filter_outlier_alignments,
)
from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer
from src.renderer.importer import FrameInfo, load_frame, load_manifest
from src.renderer.stretch import StretchParams, apply_stretch
from src.renderer.transitions import crossfade, linear_pan
from src.renderer.video import check_ffmpeg, encode_video, write_frame_png

logger = logging.getLogger(__name__)

RESOLUTION_PRESETS: dict[str, tuple[int, int]] = {
    "native": (0, 0),
    "4k": (3840, 2160),
    "1440p": (2560, 1440),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}


@dataclass
class RenderConfig:
    """Configuration for a render job."""

    fps: int = field(default_factory=lambda: settings.render_fps)
    crf: int = field(default_factory=lambda: settings.render_crf)
    stretch_mode: str = "auto"
    stretch_params: StretchParams | None = None
    debayer_mode: DebayerMode = DebayerMode.AUTO
    transition: str = field(default_factory=lambda: settings.render_transition)
    crossfade_frames: int = field(default_factory=lambda: settings.render_crossfade_frames)
    resolution: str = field(default_factory=lambda: settings.render_resolution)
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
        logger.debug("Raw frame %d shape=%s dtype=%s", frame_idx, data.shape, data.dtype)

        pattern = detect_bayer(frame.bayer_pattern, self.config.debayer_mode)
        debayered = debayer_frame(data, pattern)
        logger.debug(
            "Debayered frame %d: %dx%d (%d channels)",
            frame_idx, debayered.shape[1], debayered.shape[0],
            debayered.shape[2] if debayered.ndim == 3 else 1,
        )

        stretched = apply_stretch(
            debayered,
            mode=self.config.stretch_mode,
            params=self.config.stretch_params,
            mono_to_rgb=True,
        )
        logger.debug(
            "Stretched frame %d: min=%d max=%d",
            frame_idx, int(stretched.min()), int(stretched.max()),
        )
        return stretched

    def render(
        self,
        output_path: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Run the full pipeline and produce a video file.

        Args:
            output_path: Path for the output video file.
            on_progress: Optional callback ``(current_frame, total_frames)``.
        """
        if not check_ffmpeg():
            msg = "ffmpeg not found"
            raise RuntimeError(msg)

        active = self.active_frames()
        if len(active) < 2:
            msg = "Need at least 2 frames to render"
            raise RuntimeError(msg)

        logger.info(
            "Render started: %d active frames, transition=%s, fps=%d, crf=%d",
            len(active), self.config.transition, self.config.fps, self.config.crf,
        )
        render_t0 = time.monotonic()

        temp = self._get_temp_dir()
        try:
            self._render_to_dir(active, temp, on_progress=on_progress)

            logger.info("Encoding video to %s", output_path)
            encode_t0 = time.monotonic()
            encode_video(temp, output_path, self.config.fps, self.config.crf)
            encode_elapsed = time.monotonic() - encode_t0
            file_size = output_path.stat().st_size if output_path.exists() else 0
            logger.info(
                "Encoding complete in %.1fs, file size %.2f MB",
                encode_elapsed, file_size / (1024 * 1024),
            )
        finally:
            if not self.config.keep_frames:
                shutil.rmtree(temp, ignore_errors=True)

        total_elapsed = time.monotonic() - render_t0
        logger.info("Render finished in %.1fs", total_elapsed)

    def _render_to_dir(
        self,
        active: list[FrameInfo],
        temp: Path,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        """Process all frames and write PNGs to temp directory."""
        frame_counter = 0
        # key frames + transition frames between each pair
        total_estimated = len(active) + (len(active) - 1) * self.config.crossfade_frames

        # Stretch all active frames
        logger.info("Stage: debayer + stretch (%d frames)", len(active))
        stretch_t0 = time.monotonic()
        stretched: list[np.ndarray] = []
        for i, frame in enumerate(active):
            idx = self.frames.index(frame)
            logger.info("Processing frame %d/%d", i + 1, len(active))
            t0 = time.monotonic()
            stretched.append(self.stretch_frame(idx))
            elapsed = time.monotonic() - t0
            logger.debug("Frame %d/%d processed in %.2fs", i + 1, len(active), elapsed)
        logger.info("Debayer + stretch complete in %.1fs", time.monotonic() - stretch_t0)

        # Compute resize scale factor (needed to adjust alignment offsets)
        resize_scale = 1.0
        if self.config.resolution != "native":
            target = RESOLUTION_PRESETS.get(self.config.resolution)
            if target and stretched:
                orig_h, orig_w = stretched[0].shape[:2]
                target_w, target_h = target
                resize_scale = min(target_w / orig_w, target_h / orig_h)
                logger.info(
                    "Will resize frames to %dx%d (%s, scale=%.3f)",
                    target_w, target_h, self.config.resolution, resize_scale,
                )

        # Align if needed for linear pan (BEFORE resize, using raw frames)
        cum_offsets: list[tuple[float, float]] = [(0.0, 0.0)]
        margins = (0, 0)
        if self.config.transition == "linear-pan" and len(stretched) > 1:
            logger.info("Stage: alignment (%d pairs) using raw frames", len(active) - 1)
            align_t0 = time.monotonic()
            self._alignments = []
            for i in range(len(active) - 1):
                logger.info("Aligning pair %d-%d...", i, i + 1)
                # Use RAW data (uint16) for alignment, not stretched 8-bit
                raw_a = _load_mono_raw(active[i])
                raw_b = _load_mono_raw(active[i + 1])
                logger.info("  mono shapes: %s dtype=%s", raw_a.shape, raw_a.dtype)
                result = align_pair(raw_a, raw_b)
                logger.info(
                    "  Result: dx=%.1f dy=%.1f success=%s",
                    result.dx, result.dy, result.success,
                )
                self._alignments.append(result)
            self._alignments = filter_outlier_alignments(self._alignments)
            logger.info(
                "Alignment complete in %.1fs (%d pairs kept after outlier filter)",
                time.monotonic() - align_t0, len(self._alignments),
            )

            # Compute cumulative offsets
            for a in self._alignments:
                prev = cum_offsets[-1]
                cum_offsets.append((prev[0] + a.dx, prev[1] + a.dy))

            # Scale pairwise offsets if resizing
            if resize_scale != 1.0:
                self._alignments = [
                    AlignmentResult(
                        dx=a.dx * resize_scale,
                        dy=a.dy * resize_scale,
                        rotation=a.rotation,
                        success=a.success,
                    )
                    for a in self._alignments
                ]
                logger.info("Scaled alignment offsets by %.3f for resize", resize_scale)

            # Margins from max PAIRWISE offset (not cumulative!)
            # Each transition is independent — margin must accommodate
            # the largest single-pair shift in either direction
            max_pair_dx = max(abs(a.dx) for a in self._alignments)
            max_pair_dy = max(abs(a.dy) for a in self._alignments)
            margins = (math.ceil(max_pair_dx), math.ceil(max_pair_dy))
            logger.info("Pairwise margins: %dx%d", margins[0], margins[1])

        # Resize frames AFTER alignment offset computation
        if resize_scale != 1.0:
            target = RESOLUTION_PRESETS.get(self.config.resolution, (0, 0))
            resize_t0 = time.monotonic()
            stretched = [_resize_frame(f, target[0], target[1]) for f in stretched]
            logger.info(
                "Resized %d frames in %.1fs", len(stretched), time.monotonic() - resize_t0,
            )

        # Generate output frames with transitions
        logger.info("Stage: generate output frames with transitions")
        gen_t0 = time.monotonic()
        h, w = stretched[0].shape[:2]
        crop_h = h - 2 * margins[1] if margins[1] else h
        crop_w = w - 2 * margins[0] if margins[0] else w

        is_pan = self.config.transition == "linear-pan" and margins != (0, 0)
        mx, my = margins

        for i in range(len(stretched)):
            if not is_pan:
                # For crossfade/none: write key frame, then transition
                write_frame_png(stretched[i], temp, frame_counter)
                frame_counter += 1
                if on_progress:
                    on_progress(frame_counter, total_estimated)

            # Add transition frames between this and next
            if i < len(stretched) - 1:
                trans = self._make_transition(stretched, i, margins)
                logger.info(
                    "Transition %d->%d: %d frames", i, i + 1, len(trans),
                )
                for tf in trans:
                    write_frame_png(tf, temp, frame_counter)
                    frame_counter += 1
                    if on_progress:
                        on_progress(frame_counter, total_estimated)

        # For linear-pan: write last key frame (cropped at margin)
        if is_pan:
            last = stretched[-1][my:my + crop_h, mx:mx + crop_w]
            write_frame_png(last, temp, frame_counter)
            frame_counter += 1

        gen_elapsed = time.monotonic() - gen_t0
        logger.info(
            "Wrote %d total frames to %s in %.1fs", frame_counter, temp, gen_elapsed,
        )

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


def _resize_frame(frame: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Resize a frame to fit within target dimensions, preserving aspect ratio.

    Args:
        frame: 8-bit RGB numpy array.
        target_w: Maximum width.
        target_h: Maximum height.

    Returns:
        Resized 8-bit RGB numpy array.
    """
    from PIL import Image

    img = Image.fromarray(frame)
    img.thumbnail((target_w, target_h), Image.LANCZOS)
    return np.array(img)


def _to_mono(frame: np.ndarray) -> np.ndarray:
    """Convert RGB to mono for alignment."""
    if frame.ndim == 3:
        return np.mean(frame, axis=2).astype(np.uint8)
    return frame


def _load_mono_raw(frame: FrameInfo) -> np.ndarray:
    """Load a frame as raw mono uint16 for alignment.

    Skips debayering and stretching — just loads the raw FITS data.
    For Bayer images, this is the CFA mosaic which still has enough
    star signal for alignment.
    """
    data = load_frame(frame)
    if data.ndim == 3:
        return np.mean(data, axis=2).astype(data.dtype)
    return data
