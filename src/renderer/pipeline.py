"""Rendering pipeline orchestration."""

from __future__ import annotations

import logging
import math
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from src.config import settings
from src.models.project import Label, Project
from src.renderer.alignment import (
    AlignmentResult,
    align_pair,
    filter_outlier_alignments,
)
from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer
from src.renderer.gil_watchdog import check_gil_state, reset_run_state
from src.renderer.importer import FrameInfo, load_frame, load_manifest
from src.renderer.labels import _draw_labels, cumulative_offset
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
    # Tail-blend frames for ``linear_pan`` transitions (#126). The last
    # K frames of each pair crossfade from frame_a's pan output to
    # frame_b's reference crop with smoothstep easing, eliminating the
    # brightness/exposure jump between keyframes. Default ``0`` = no
    # blending, identical to pre-#126 renders.
    linear_pan_blend_tail: int = field(
        default_factory=lambda: settings.render_linear_pan_blend_tail,
    )
    # Burn project labels into each frame via PIL (#130). When False,
    # produces a clean clip even if the manifest has a non-empty labels
    # list — used by ``--no-labels`` and the UI's "render clean" toggle.
    render_labels: bool = True


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
        # Populated by :meth:`load`. Holds the parsed manifest including
        # labels (#130). ``None`` before ``load`` is called.
        self.project: Project | None = None
        # Populated by ``_render_to_dir`` once we know ``resize_scale``;
        # read by transition workers. Empty until then.
        self._scaled_labels: list[Label] = []

    def load(self) -> None:
        """Load manifest and frame metadata."""
        manifest_path = self.capture_dir / "manifest.json"
        self.project = Project.model_validate_json(manifest_path.read_text())
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

    def _label_offsets(
        self,
        labels: list[Label],
        frame_index: int,
    ) -> list[tuple[float, float]]:
        """Cumulative offsets per label from its ref frame to ``frame_index``.

        Used by the label-draw call sites to position each label on the
        currently-being-written frame. Returns one ``(dx, dy)`` per
        label in the same order, suitable to pass directly to
        :func:`_draw_labels`.
        """
        return [
            cumulative_offset(
                self._alignments, label.ref_frame_index, frame_index,
            )
            for label in labels
        ]

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
        # Per-frame override via "Reset" button: when
        # ``force_fresh_stretch`` is set on this frame, ignore the
        # project freeze and compute ZScale fresh on just this frame.
        # Useful for outlier exposures (#154 follow-up).
        frame = self.frames[frame_idx]
        auto_params = (
            self.config.auto_stretch_params
            if (
                self.config.auto_stretch_freeze
                and not getattr(frame, "force_fresh_stretch", False)
            )
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
        # GIL runtime watchdog (#119): reset dedup state at run start
        # and probe at "render-start". Phase boundaries below also
        # probe so we catch a mid-render regression (e.g. a freshly
        # imported C-extension that flips the GIL back on).
        reset_run_state()
        check_gil_state("render-start")

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
            check_gil_state("alignment-phase")
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

        # Labels to burn into every frame (#130). Empty when either the
        # config flag is off or the manifest has no labels — keeps the
        # per-frame fast path zero-cost in that common case.
        #
        # Labels are persisted in *original* frame-pixel space (so that
        # editing them is resolution-independent). The render output may
        # be resized via ``resize_scale``; we need labels scaled to match
        # the resized frames so the draw call's bounds-check actually
        # accepts them. ``self._alignments`` is already pre-scaled at
        # this point (see "Scaled alignment offsets" above), so offsets
        # are already in resized space.
        #
        # We stash the scaled list on ``self`` so the transition workers
        # (``_process_transition_pair`` runs in a thread pool) read the
        # same scaled labels without reaching into self.project.labels
        # directly.
        raw_labels: list[Label] = (
            self.project.labels
            if (self.project and self.config.render_labels)
            else []
        )
        if raw_labels and resize_scale != 1.0:
            # All pixel-sized fields must scale with the frame resize,
            # not just the marker position. text_offset and
            # offset_radius are stored in orig-pixel space; leaving
            # them at orig-pixel magnitude inside a resized frame
            # pushes the text far beyond where the user meant it —
            # leader-line labels at 200 px offsets ended up hundreds
            # of CSS-px outside the rendered frame after the user
            # had clearly placed them in-bounds in the preview
            # (#154 follow-up).
            self._scaled_labels = [
                lbl.model_copy(update={
                    "x": lbl.x * resize_scale,
                    "y": lbl.y * resize_scale,
                    "text_offset_x": int(round(
                        lbl.text_offset_x * resize_scale,
                    )),
                    "text_offset_y": int(round(
                        lbl.text_offset_y * resize_scale,
                    )),
                    "offset_radius": int(round(
                        lbl.offset_radius * resize_scale,
                    )),
                })
                for lbl in raw_labels
            ]
        else:
            self._scaled_labels = list(raw_labels)
        labels = self._scaled_labels

        prev_stretched: np.ndarray | None = None
        dims_logged = False

        # Parallel stretch with bounded prefetch (#118): the heavy CPU
        # work (load_frame -> debayer -> apply_stretch) runs in worker
        # threads, but the orchestrator main loop still consumes results
        # in input order so transitions and PNG writes stay sequential.
        #
        # Memory bound: at most ``PREFETCH + 1`` debayered frames live
        # at once, regardless of how long ``active`` is. With workers=4
        # and PREFETCH=8 that is ~9 * 78 MB = ~700 MB peak — predictable
        # and independent of frame count.
        #
        # The probe (frame 0, already stretched above for dimension
        # detection) is seeded into the queue as a pre-completed future
        # so we don't redo that work; subsequent frames are submitted to
        # the pool. Worker exceptions surface from ``.result()`` and
        # propagate cleanly out of the ``with`` block.
        stretch_workers = _resolve_workers(self.config.render_workers)
        stretch_workers = min(stretch_workers, max(1, len(active)))
        prefetch = stretch_workers * 2
        check_gil_state("stretch-phase")
        logger.info(
            "Stage: stretch (%d frames) workers=%d prefetch=%d",
            len(active), stretch_workers, prefetch,
        )

        in_flight: dict[int, Future[np.ndarray]] = {}

        # Seed slot 0 with the probe (already computed). Wrap in a
        # pre-completed Future so the consumer code below is uniform.
        probe_future: Future[np.ndarray] = Future()
        probe_future.set_result(probe)
        in_flight[0] = probe_future

        # Parallel transition pool (#124): the per-pair work
        # (transition generation + 24 PNG writes) used to run serially
        # in the consumer, dominating wall-clock time at high worker
        # counts. We submit each pair to its own worker now; filenames
        # are precomputed per pair so the order ffmpeg sees is stable
        # regardless of completion order.
        #
        # Memory: each worker streams one transition frame at a time
        # (see :meth:`_process_transition_pair`), so peak per-worker
        # is ~80 MB instead of ~1.9 GB for a full 24-frame list.
        trans_workers = min(
            _resolve_workers(self.config.render_workers),
            max(1, len(active) - 1),
        )
        trans_futures: list[Future[int]] = []

        with ThreadPoolExecutor(
            max_workers=stretch_workers,
            thread_name_prefix="nc-stretch",
        ) as pool, ThreadPoolExecutor(
            max_workers=trans_workers,
            thread_name_prefix="nc-trans",
        ) as trans_pool:
            # Prime the queue: submit slots 1..min(prefetch, len-1).
            # Slot 0 is already seeded above.
            next_to_submit = 1
            prime_limit = min(prefetch + 1, len(active))
            while next_to_submit < prime_limit:
                idx = self.frames.index(active[next_to_submit])
                in_flight[next_to_submit] = pool.submit(self.stretch_frame, idx)
                next_to_submit += 1

            for i in range(len(active)):
                logger.info("Processing frame %d/%d", i + 1, len(active))
                t0 = time.monotonic()

                # Block until this slot is ready. Worker exceptions
                # re-raise here and propagate out of ``with``.
                current_stretched = in_flight.pop(i).result()

                elapsed = time.monotonic() - t0
                logger.debug(
                    "Frame %d/%d stretched in %.2fs (wait time)",
                    i + 1, len(active), elapsed,
                )

                # Top up the queue: submit one new task to keep the pool
                # full as we consume results. Caps in-flight count at
                # ``prefetch + 1``.
                if next_to_submit < len(active):
                    idx = self.frames.index(active[next_to_submit])
                    in_flight[next_to_submit] = pool.submit(
                        self.stretch_frame, idx,
                    )
                    next_to_submit += 1

                # Resize if needed
                if resize_scale != 1.0 and target[0] and target[1]:
                    current_stretched = _resize_frame(current_stretched, target[0], target[1])

                # Log dimensions once
                if not dims_logged:
                    h, w = current_stretched.shape[:2]
                    crop_h = h - 2 * my if my else h
                    crop_w = w - 2 * mx if mx else w
                    dims_logged = True

                # Write key frame only if no transitions (transitions
                # include start/end frames). The no-transitions path
                # stays sequential — it's already a single PNG per
                # outer iteration and not the bottleneck.
                if not has_transitions:
                    if labels:
                        current_stretched = _draw_labels(
                            current_stretched, labels,
                            self._label_offsets(labels, i),
                            (current_stretched.shape[1],
                             current_stretched.shape[0]),
                        )
                    write_frame_png(current_stretched, temp, frame_counter)
                    frame_counter += 1
                    render_progress.increment()

                # Dispatch transition with previous frame to the
                # transition pool. Each pair is independent and the
                # filenames are deterministic so workers can complete
                # out of order without confusing ffmpeg.
                if has_transitions and prev_stretched is not None and i > 0:
                    pair_idx = i - 1
                    start_frame = pair_idx * self.effective_crossfade_frames
                    logger.info(
                        "Dispatching transition %d->%d to worker (start frame %d)",
                        pair_idx, i, start_frame,
                    )
                    trans_futures.append(trans_pool.submit(
                        self._process_transition_pair,
                        prev_stretched, current_stretched, pair_idx, margins,
                        temp, start_frame, render_progress,
                    ))

                prev_stretched = current_stretched

            # Drain transition workers before the stretch pool exits.
            # ``.result()`` re-raises any worker exception so the user
            # sees a real error instead of silently corrupted output.
            for fut in trans_futures:
                frame_counter += fut.result()

        # Write last key frame (transitions don't include the final frame).
        # When transitions exist, all pair writes have completed above,
        # so the final key frame's index is deterministic.
        if has_transitions and prev_stretched is not None:
            if is_pan:
                h, w = prev_stretched.shape[:2]
                crop_h = h - 2 * my if my else h
                crop_w = w - 2 * mx if mx else w
                last = prev_stretched[my:my + crop_h, mx:mx + crop_w]
            else:
                last = prev_stretched
            if labels:
                # When ``is_pan``, ``last`` was cropped by (mx, my) on
                # each side, so the cropped frame's origin sits at
                # (mx, my) in the uncropped image. Add (mx, my) to the
                # cumulative offset so labels land in the cropped frame
                # at the right spot.
                base_offsets = self._label_offsets(labels, len(active) - 1)
                if is_pan:
                    offsets = [(dx + mx, dy + my) for dx, dy in base_offsets]
                else:
                    offsets = base_offsets
                last = _draw_labels(
                    last, labels, offsets,
                    (last.shape[1], last.shape[0]),
                )
            # Position after all transition frames: pairs contribute
            # exactly ``effective_crossfade_frames`` each.
            final_idx = (len(active) - 1) * self.effective_crossfade_frames
            write_frame_png(last, temp, final_idx)
            frame_counter = max(frame_counter, final_idx + 1)
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
    ) -> Iterator[np.ndarray]:
        """Generate transition frames between two consecutive stretched frames.

        Yields frames one at a time so streaming consumers (see
        :meth:`_process_transition_pair`, issue #124) can write each
        frame to disk and free it before the next is generated. Holding
        an entire 24-frame transition in memory at 8K-RGB would peak at
        ~1.9 GB; streaming caps it at ~80 MB per worker.

        Args:
            frame_a: The previous stretched frame.
            frame_b: The current stretched frame.
            pair_index: Index of the pair in the alignment list.
            margins: (mx, my) pixel margins for linear-pan cropping.
        """
        if self.config.transition == "crossfade":
            yield from crossfade(frame_a, frame_b, self.effective_crossfade_frames)
        elif self.config.transition == "linear-pan" and self._alignments:
            yield from linear_pan(
                frame_a, frame_b,
                self._alignments[pair_index],
                self.effective_crossfade_frames,
                margins[0], margins[1],
                blend_tail_frames=self.config.linear_pan_blend_tail,
            )
        # else: no transitions — yield nothing

    def _process_transition_pair(
        self,
        prev_stretched: np.ndarray,
        current_stretched: np.ndarray,
        pair_idx: int,
        margins: tuple[int, int],
        temp: Path,
        start_frame_number: int,
        progress: _PhaseProgress,
    ) -> int:
        """Worker: generate transition frames for one pair and write them.

        Streams frames from :meth:`_make_transition_pair` directly to
        disk, so peak memory per worker is one transition frame, not
        the full list. Filenames are deterministic
        (``start_frame_number + offset``), so ffmpeg picks them up in
        the right order regardless of which worker finishes first.

        ``progress.increment()`` runs once per written frame; the
        counter is thread-safe (#116), so concurrent workers don't
        race. Worker exceptions propagate through ``.result()`` in the
        main loop and abort the render.

        Labels (#130) are interpolated between the two keyframe offsets
        and burned into each transition frame just before its PNG
        write.

        Returns:
            The number of frames written.
        """
        # Read scaled labels prepared by ``_render_to_dir`` (resize_scale
        # already applied so positions land inside the resized frame).
        labels: list[Label] = (
            list(self._scaled_labels)
            if self.config.render_labels
            else []
        )
        n = self.effective_crossfade_frames
        # Pre-compute the two endpoint offsets for each label so the
        # per-frame loop only does a small interpolation.
        offs_a: list[tuple[float, float]] = []
        offs_b: list[tuple[float, float]] = []
        if labels:
            offs_a = self._label_offsets(labels, pair_idx)
            offs_b = self._label_offsets(labels, pair_idx + 1)
        # Transition frames are produced cropped by (mx, my) on each
        # side (linear_pan reserves that room for the pan). To map a
        # label's reference-frame pixel into the cropped output we
        # need an extra (+mx, +my) on top of the alignment offset.
        # For crossfade (margins=(0, 0)) this adds nothing.
        crop_mx, crop_my = margins
        count = 0
        for offset, tf in enumerate(self._make_transition_pair(
            prev_stretched, current_stretched, pair_idx, margins,
        )):
            if labels:
                t = (offset + 1) / n
                interp = [
                    (
                        a[0] + t * (b[0] - a[0]) + crop_mx,
                        a[1] + t * (b[1] - a[1]) + crop_my,
                    )
                    for a, b in zip(offs_a, offs_b, strict=True)
                ]
                tf = _draw_labels(
                    tf, labels, interp,
                    (tf.shape[1], tf.shape[0]),
                )
            write_frame_png(tf, temp, start_frame_number + offset)
            progress.increment()
            count += 1
        return count

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
