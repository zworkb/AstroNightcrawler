"""Rendering pipeline orchestration."""

from __future__ import annotations

import logging
import math
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from src.config import settings
from src.renderer.alignment import (
    AlignmentResult,
    align_pair,
    filter_outlier_alignments,
)
from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer
from src.renderer.importer import FrameInfo, load_frame, load_manifest
from src.renderer.stretch import (
    AutoStretchParams,
    StretchParams,
    apply_stretch,
    auto_stretch,
)
from src.renderer.transitions import crossfade, linear_pan
from src.renderer.video import check_ffmpeg, encode_video, write_frame_png

logger = logging.getLogger(__name__)


class ProgressUpdate(BaseModel):
    """Structured payload for render progress callbacks.

    The progress callback receives one of these per increment. Phase
    boundaries are signalled by the ``phase`` field switching values
    (e.g. from ``"prepare"`` to ``"render"``); ``current`` resets to 1
    at each boundary so each phase reads as a fresh 0..100% bar.

    Pydantic ``BaseModel`` (project policy: pydantic over dataclass for
    structured payloads) — JSON-serialisable, forward-compatible.
    """

    phase: str  # "prepare" or "render"
    current: int  # frames done in this phase (1-based after first increment)
    total: int  # total frames in this phase
    label: str  # human-readable status, e.g. "Preparing: pair 5/12"


ProgressCallback = Callable[[ProgressUpdate], None]


class _PhaseProgress:
    """Thread-safe progress counter for a single phase.

    Workers call :meth:`increment` after each unit of work completes.
    The lock protects the counter so the order of completion is
    irrelevant; under sequential use this degenerates to a plain
    counter with no contention. Designed for the upcoming
    ``ThreadPoolExecutor`` parallelisation in #117 / #118.
    """

    def __init__(
        self,
        phase: str,
        total: int,
        callback: ProgressCallback | None,
        label_fmt: Callable[[int, int], str],
    ) -> None:
        self._lock = threading.Lock()
        self._phase = phase
        self._total = total
        self._current = 0
        self._callback = callback
        self._label_fmt = label_fmt

    def increment(self, n: int = 1) -> None:
        """Atomically advance the counter and emit a progress update."""
        with self._lock:
            self._current += n
            current = self._current
        if self._callback is not None:
            self._callback(ProgressUpdate(
                phase=self._phase,
                current=current,
                total=self._total,
                label=self._label_fmt(current, self._total),
            ))

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
    speed: float = field(default_factory=lambda: settings.render_speed)
    keep_frames: bool = False
    temp_dir: Path | None = None
    # Auto-stretch freeze: if True and ``auto_stretch_params`` is set,
    # the frozen ZScale limits are reused for every frame instead of
    # being recomputed per-frame. Eliminates brightness flicker between
    # frames and gives WYSIWYG (preview matches render). See issue #114.
    auto_stretch_freeze: bool = True
    auto_stretch_params: AutoStretchParams | None = None
    # Parallel worker count for alignment (and, in #118, stretch). The
    # default comes from ``settings.render_workers`` so the env var
    # ``NC_RENDER_WORKERS`` continues to work transparently. The UI,
    # CLI flags, and direct ``RenderConfig`` overrides all win over
    # the settings value at config-build time. ``-1`` means "use all
    # available CPU cores"; positive ints are clamped to >= 1 by
    # ``_resolve_workers``. See issue #120.
    render_workers: int = field(default_factory=lambda: settings.render_workers)


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

    @property
    def effective_crossfade_frames(self) -> int:
        """Crossfade frame count adjusted for playback speed.

        We scale by ``1/speed`` so slow-motion playback gets proportionally
        more *real* interpolated frames, not just duplicated ones. Without
        this, slow-mo stutters because motion-fps drops to
        ``REFERENCE_FPS * speed`` (e.g. 6 fps at speed=0.25).

        For speed > 1.0 the effective count goes down, which keeps the
        transition smooth at the (shorter) target duration without
        wasting CPU on frames that would just be dropped at encode time.
        """
        return max(1, round(self.config.crossfade_frames / self.config.speed))

    def skip_frame(self, index: int) -> None:
        """Mark a frame as skipped."""
        for f in self.frames:
            if f.index == index:
                f.skipped = True
                return

    def debayered_frame(self, frame_idx: int) -> np.ndarray:
        """Load and debayer a frame without stretching — for analysis.

        Args:
            frame_idx: Index into self.frames list.

        Returns:
            Debayered numpy array (raw dtype, e.g. uint16).
        """
        frame = self.frames[frame_idx]
        data = load_frame(frame)
        pattern = detect_bayer(frame.bayer_pattern, self.config.debayer_mode)
        return debayer_frame(data, pattern)

    def auto_stretched_frame(
        self,
        frame_idx: int,
        params: AutoStretchParams | None = None,
    ) -> np.ndarray:
        """Load, debayer, and auto-stretch a frame to uint8.

        Used by the ``auto+manual`` UI mode: the histogram widget
        operates on this 8-bit, well-distributed result so the
        black/white/midtone handles cover a meaningful range.

        Args:
            frame_idx: Index into self.frames list.
            params: Optional pre-computed ZScale limits to apply
                (frozen auto-stretch — see issue #114). If ``None``,
                ZScale is computed fresh from this frame's data.

        Returns:
            8-bit numpy array (uint8), pre-stretched via ZScale + Asinh.
        """
        return auto_stretch(self.debayered_frame(frame_idx), params=params)

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

        # Pass frozen auto-stretch params through when freeze is active —
        # apply_stretch only consults them in auto / auto+manual modes.
        auto_params = (
            self.config.auto_stretch_params
            if self.config.auto_stretch_freeze
            else None
        )
        stretched = apply_stretch(
            debayered,
            mode=self.config.stretch_mode,
            params=self.config.stretch_params,
            mono_to_rgb=True,
            auto_params=auto_params,
        )
        logger.debug(
            "Stretched frame %d: min=%d max=%d",
            frame_idx, int(stretched.min()), int(stretched.max()),
        )
        return stretched

    def render(
        self,
        output_path: Path,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Run the full pipeline and produce a video file.

        Args:
            output_path: Path for the output video file.
            on_progress: Optional callback receiving :class:`ProgressUpdate`
                instances. The callback may be invoked from worker threads
                (when parallelisation lands in #117/#118) and is expected
                to be safe to call concurrently — the UI typically just
                stashes the latest payload into a shared dict polled by a
                ``ui.timer``.
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
            encode_video(
                temp, output_path,
                self.config.fps, self.config.crf, self.config.speed,
            )
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
        on_progress: ProgressCallback | None = None,
    ) -> None:
        """Process all frames and write PNGs to temp directory."""
        # Free-threading probe: log whether the GIL is actually disabled.
        # Doesn't bail out — the alignment threadpool still works either
        # way, just without the speedup. Issue #119 will add a stricter
        # watchdog. ``_is_gil_enabled`` exists on 3.13+; older builds
        # silently skip the check.
        is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
        if is_gil_enabled is not None:
            if is_gil_enabled():
                logger.warning(
                    "GIL is enabled — alignment parallelisation will not "
                    "actually run in parallel. Check PYTHON_GIL=0 and "
                    "that you are running a free-threaded Python build "
                    "(python3.13t).",
                )
            else:
                logger.info("Free-threading active (GIL disabled)")

        frame_counter = 0
        # Render-phase total mirrors what we will actually write to disk:
        # - With transitions (linear-pan / crossfade): N-1 transitions of
        #   ``effective_crossfade_frames`` each, plus the final key frame.
        # - Without transitions (``transition == "none"``): one key frame
        #   per active frame, no in-between frames.
        # The previous formula double-counted in the transitions case;
        # this one keeps the bar honest at 100% on completion.
        has_transitions_for_total = self.config.transition in ("linear-pan", "crossfade")
        if has_transitions_for_total:
            total_estimated = (
                (len(active) - 1) * self.effective_crossfade_frames + 1
            )
        else:
            total_estimated = len(active)

        # Probe first frame to determine dimensions for resize scale
        first_idx = self.frames.index(active[0])
        probe = self.stretch_frame(first_idx)
        orig_h, orig_w = probe.shape[:2]

        # Compute resize scale factor (needed to adjust alignment offsets)
        resize_scale = 1.0
        target = (0, 0)
        if self.config.resolution != "native":
            target = RESOLUTION_PRESETS.get(self.config.resolution, (0, 0))
            if target[0] and target[1]:
                target_w, target_h = target
                resize_scale = min(target_w / orig_w, target_h / orig_h)
                logger.info(
                    "Will resize frames to %dx%d (%s, scale=%.3f)",
                    target_w, target_h, self.config.resolution, resize_scale,
                )

        # Align if needed for linear pan (BEFORE resize, using raw frames)
        # Phase boundary: a "prepare" phase only exists when we actually
        # have alignment work to do. For crossfade / single-frame inputs
        # we skip straight to the render phase — emitting a fake 0-total
        # prepare phase would just confuse the UI.
        margins = (0, 0)
        has_prepare_phase = (
            self.config.transition == "linear-pan" and len(active) > 1
        )
        if has_prepare_phase:
            num_pairs = len(active) - 1
            workers = min(_resolve_workers(self.config.render_workers), num_pairs)
            logger.info(
                "Stage: alignment (%d pairs) using raw frames, workers=%d",
                num_pairs, workers,
            )
            align_t0 = time.monotonic()
            prepare_progress = _PhaseProgress(
                phase="prepare",
                total=num_pairs,
                callback=on_progress,
                label_fmt=lambda c, t: f"Preparing: pair {c}/{t}",
            )

            # Pattern A: submit all pairs, gather by index via the
            # futures->idx map. This preserves pair order in
            # ``self._alignments`` regardless of completion order, which
            # is required because ``filter_outlier_alignments`` reasons
            # about adjacency. Worker exceptions surface from
            # ``fut.result()`` and propagate cleanly out of the ``with``
            # block; the bar still advances because ``_align_one_pair``
            # increments the counter in ``finally``.
            #
            # Memory note: each worker holds two raw frames at once
            # (~50 MB at 4168x6224 uint16). With the default cap of 4
            # workers that is ~400 MB peak just for alignment input —
            # see :func:`_alignment_workers` for the override.
            results: list[AlignmentResult | None] = [None] * num_pairs
            with ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="nc-align",
            ) as pool:
                futures = {
                    pool.submit(
                        _align_one_pair,
                        active[i], active[i + 1], i, prepare_progress,
                    ): i
                    for i in range(num_pairs)
                }
                for fut in as_completed(futures):
                    idx = futures[fut]
                    results[idx] = fut.result()

            # All slots are populated unless a worker raised, in which
            # case ``fut.result()`` already re-raised above.
            self._alignments = [r for r in results if r is not None]
            self._alignments = filter_outlier_alignments(self._alignments)
            logger.info(
                "Alignment complete in %.1fs (%d pairs kept after outlier filter)",
                time.monotonic() - align_t0, len(self._alignments),
            )

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

        # Stream frames: stretch on-demand, keep at most 2 in memory
        logger.info("Stage: stream debayer + stretch + transitions (%d frames)", len(active))
        gen_t0 = time.monotonic()

        # Phase boundary: switch from "prepare" to "render". The counter
        # resets to 0 here so the UI's bar reads 0..total in this phase
        # alone — weighting prepare vs render into a single bar would be
        # fragile because alignment timing varies wildly with frame size.
        render_progress = _PhaseProgress(
            phase="render",
            total=total_estimated,
            callback=on_progress,
            label_fmt=lambda c, t: f"Rendering: {c}/{t}",
        )

        has_transitions = self.config.transition in ("linear-pan", "crossfade")
        is_pan = self.config.transition == "linear-pan" and margins != (0, 0)
        mx, my = margins

        prev_stretched: np.ndarray | None = None
        dims_logged = False

        for i in range(len(active)):
            idx = self.frames.index(active[i])
            logger.info("Processing frame %d/%d", i + 1, len(active))
            t0 = time.monotonic()

            # Re-use probed first frame instead of stretching again
            current_stretched = probe if i == 0 else self.stretch_frame(idx)

            elapsed = time.monotonic() - t0
            logger.debug("Frame %d/%d stretched in %.2fs", i + 1, len(active), elapsed)

            # Resize if needed
            if resize_scale != 1.0 and target[0] and target[1]:
                current_stretched = _resize_frame(current_stretched, target[0], target[1])

            # Log dimensions once
            if not dims_logged:
                h, w = current_stretched.shape[:2]
                crop_h = h - 2 * my if my else h
                crop_w = w - 2 * mx if mx else w
                dims_logged = True

            # Write key frame only if no transitions (transitions include start/end)
            if not has_transitions:
                write_frame_png(current_stretched, temp, frame_counter)
                frame_counter += 1
                render_progress.increment()

            # Generate transition with previous frame
            if prev_stretched is not None and i > 0:
                trans = self._make_transition_pair(
                    prev_stretched, current_stretched, i - 1, margins,
                )
                logger.info(
                    "Transition %d->%d: %d frames", i - 1, i, len(trans),
                )
                for tf in trans:
                    write_frame_png(tf, temp, frame_counter)
                    frame_counter += 1
                    render_progress.increment()

            prev_stretched = current_stretched

        # Write last key frame (transitions don't include the final frame)
        if has_transitions and prev_stretched is not None:
            if is_pan:
                h, w = prev_stretched.shape[:2]
                crop_h = h - 2 * my if my else h
                crop_w = w - 2 * mx if mx else w
                last = prev_stretched[my:my + crop_h, mx:mx + crop_w]
            else:
                last = prev_stretched
            write_frame_png(last, temp, frame_counter)
            frame_counter += 1
            render_progress.increment()

        # Release reference
        del probe
        del prev_stretched

        gen_elapsed = time.monotonic() - gen_t0
        logger.info(
            "Wrote %d total frames to %s in %.1fs", frame_counter, temp, gen_elapsed,
        )

    def _make_transition_pair(
        self,
        frame_a: np.ndarray,
        frame_b: np.ndarray,
        pair_index: int,
        margins: tuple[int, int],
    ) -> list[np.ndarray]:
        """Generate transition frames between two consecutive stretched frames.

        Args:
            frame_a: The previous stretched frame.
            frame_b: The current stretched frame.
            pair_index: Index of the pair in the alignment list.
            margins: (mx, my) pixel margins for linear-pan cropping.
        """
        if self.config.transition == "crossfade":
            return crossfade(frame_a, frame_b, self.effective_crossfade_frames)
        if self.config.transition == "linear-pan" and self._alignments:
            return linear_pan(
                frame_a, frame_b,
                self._alignments[pair_index],
                self.effective_crossfade_frames,
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
    # H.264 requires even dimensions
    w, h = img.size
    if w % 2 or h % 2:
        w = w - w % 2
        h = h - h % 2
        img = img.crop((0, 0, w, h))
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


def _resolve_workers(value: int) -> int:
    """Resolve a worker-count config value into an actual pool size.

    The UI / CLI / env / settings layers all feed an integer into
    ``RenderConfig.render_workers``. This helper interprets that value:

    - ``-1`` means "use all available CPU cores" (``os.cpu_count()``).
    - Positive ints are clamped to ``>= 1``.
    - ``0`` and other non-positive values fall back to ``1`` (a single
      worker — never disable parallelism silently in a way that
      degrades into "no work happens").

    The env-var passthrough that the old ``_alignment_workers`` helper
    implemented now lives in ``Settings.render_workers``: pydantic_settings
    reads ``NC_RENDER_WORKERS`` from the environment / ``.env`` and
    populates the field automatically. So the env var still works,
    but the resolution logic is centralised here. See issue #120.
    """
    if value == -1:
        return os.cpu_count() or 1
    return max(1, value)


def _align_one_pair(
    frame_a: FrameInfo,
    frame_b: FrameInfo,
    pair_idx: int,
    progress: _PhaseProgress,
) -> AlignmentResult:
    """Worker: align a single frame pair.

    Loads both raw frames, runs ``align_pair`` and returns the result.
    The progress counter is incremented in ``finally`` so the bar still
    advances if the worker raises — important for UX (the bar would
    otherwise freeze at N-1/N while the exception propagates back to
    the main thread).
    """
    try:
        raw_a = _load_mono_raw(frame_a)
        raw_b = _load_mono_raw(frame_b)
        logger.info(
            "Pair %d: mono shapes=%s dtype=%s",
            pair_idx, raw_a.shape, raw_a.dtype,
        )
        result = align_pair(raw_a, raw_b)
        logger.info(
            "Pair %d: dx=%.1f dy=%.1f success=%s",
            pair_idx, result.dx, result.dy, result.success,
        )
        return result
    finally:
        progress.increment()
