"""Orchestrates all rendering pipeline stages.

Stub — full implementation in Task 8.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.renderer.alignment import AlignmentResult
from src.renderer.debayer import DebayerMode
from src.renderer.importer import FrameInfo, load_manifest
from src.renderer.stretch import StretchParams

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
        """Initialize the pipeline.

        Args:
            capture_dir: Directory with manifest.json and FITS files.
            config: Render configuration.
        """
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
        """Mark a frame as skipped.

        Args:
            index: Capture point index to skip.
        """
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
        raise NotImplementedError("Full stretch_frame in Task 8")

    def render(self, output: Path) -> None:
        """Run the full render pipeline and write output video.

        Args:
            output: Path for the output video file.
        """
        raise NotImplementedError("Full render in Task 8")
