"""Pydantic data models for telescope imaging sequence projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ``AutoStretchParams`` lives in ``src.renderer.stretch`` and is itself a
# pydantic ``BaseModel`` (see #114). We import it eagerly here — ``stretch``
# is a leaf module (numpy + astropy only) and does not import from
# ``models.project``, so there is no circular-import risk.
from src.renderer.stretch import AutoStretchParams, StretchParams


class Coordinate(BaseModel):
    """A sky coordinate in RA/Dec (degrees, J2000)."""

    ra: float = Field(description="Right ascension in degrees (0-360)")
    dec: float = Field(description="Declination in degrees (-90 to 90)")

    @field_validator("ra")
    @classmethod
    def validate_ra(cls, v: float) -> float:
        """Validate RA is within 0-360 degrees."""
        if not 0.0 <= v <= 360.0:
            msg = f"RA must be between 0 and 360 degrees, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("dec")
    @classmethod
    def validate_dec(cls, v: float) -> float:
        """Validate Dec is within -90 to 90 degrees."""
        if not -90.0 <= v <= 90.0:
            msg = f"Dec must be between -90 and 90 degrees, got {v}"
            raise ValueError(msg)
        return v


class ControlPoint(Coordinate):
    """A point on the spline path with optional Bezier handles."""

    label: str | None = Field(default=None, description="Optional label for this point")
    handle_in: Coordinate | None = Field(default=None, description="Incoming Bezier handle")
    handle_out: Coordinate | None = Field(default=None, description="Outgoing Bezier handle")


class SplinePath(BaseModel):
    """A drawn path defined by a list of control points."""

    control_points: list[ControlPoint] = Field(
        description="Ordered list of control points defining the path"
    )
    spline_type: str = Field(default="cubic_bezier", description="Type of spline interpolation")
    coordinate_frame: str = Field(default="J2000", description="Coordinate reference frame")

    # No minimum point count enforced at model level.
    # Paths with 0-1 points are valid during editing.
    # The capture controller validates ≥2 points before starting.


class CaptureSettings(BaseModel):
    """Global capture parameters for a sequence."""

    # Silently drop legacy keys (e.g. ``sequence_name`` from pre-#142
    # manifests) so old projects still load. The project directory is now
    # the sole identity of a sequence — see #140/#141/#142.
    model_config = ConfigDict(extra="ignore")

    point_spacing_deg: float = Field(
        default=0.5, description="Spacing between capture points in degrees"
    )
    exposure_seconds: float = Field(default=30.0, description="Exposure time per frame in seconds")
    gain: int = Field(default=0, ge=0, description="Camera gain setting")
    binning: int = Field(default=1, description="Camera binning (1, 2, 3, or 4)")
    exposures_per_point: int = Field(default=1, ge=1, description="Exposures per capture point")
    offset: int = Field(default=0, ge=0, description="Camera offset setting")

    @field_validator("binning")
    @classmethod
    def validate_binning(cls, v: int) -> int:
        """Validate binning is 1, 2, 3, or 4."""
        if v not in (1, 2, 3, 4):
            msg = f"Binning must be 1, 2, 3, or 4, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("exposure_seconds")
    @classmethod
    def validate_exposure(cls, v: float) -> float:
        """Validate exposure is positive."""
        if v <= 0:
            msg = f"Exposure must be positive, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("point_spacing_deg")
    @classmethod
    def validate_spacing(cls, v: float) -> float:
        """Validate spacing is positive."""
        if v <= 0:
            msg = f"Point spacing must be positive, got {v}"
            raise ValueError(msg)
        return v


class RenderSettings(BaseModel):
    """Per-project render output + look parameters (issue #151).

    Holds the "look" decisions for *this* project's video render —
    stretch mode, manual black/white/midtone handles, frozen
    auto-stretch ZScale params, output format (fps/crf/resolution/
    speed), transition look, and alignment tuning.

    Prior to #151 these lived in ``app.storage.general["render"]``
    (machine-global), which leaked tuning between projects whenever
    the user switched the renderer's input directory. Moving them
    into the manifest pins them to the project that actually carries
    the look — and lets the tuning travel between machines.

    Backward compat: ``extra="ignore"`` silently drops unknown keys
    (forward-compat smoke), and every field has a default so old
    manifests without ``render_settings`` validate cleanly.
    """

    model_config = ConfigDict(extra="ignore")

    # ----- Stretch / tonemap -----
    stretch_mode: str = "auto"
    black: float = 0.0
    white: float = 1.0
    midtone: float = 1.0
    auto_stretch_freeze: bool = True
    auto_stretch_params: AutoStretchParams | None = None
    linear_pan_blend_tail: int = 20

    # ----- Output format -----
    fps: int = 24
    crf: int = 18
    transition: str = "linear-pan"
    crossfade_frames: int = 20
    resolution: str = "720p"
    speed: float = 1.0

    # ----- Alignment -----
    align_max_dim: int = 0
    align_sigma: float = 2.0

    # ----- Audio -----
    music_track: str | None = Field(
        default=None,
        description=(
            "Absolute path to an audio file (mp3, wav, m4a, ogg, flac) "
            "muxed into the rendered video when ``include_music`` is True. "
            "``None`` means no track configured."
        ),
    )
    include_music: bool = Field(
        default=True,
        description=(
            "Toggle whether the configured ``music_track`` is attached "
            "to the rendered video. When False, the music path stays "
            "configured but the audio mux is skipped."
        ),
    )
    include_labels: bool = Field(
        default=True,
        description=(
            "Toggle whether project labels are burnt into the rendered "
            "frames. Flows into ``RenderConfig.render_labels`` at "
            "config-build time."
        ),
    )
    loop_music: bool = Field(
        default=True,
        description=(
            "When True the music track is looped via ffmpeg "
            "``-stream_loop -1`` so a short audio file covers the "
            "full video. Default True — typical use case."
        ),
    )


class CapturedFrame(BaseModel):
    """A single sub-exposure captured at a capture point."""

    filename: str = Field(description="Relative FITS filename, e.g. seq_0001_001.fits")
    status: Literal["good", "rejected", "pending"] = Field(
        default="good", description="Per-frame quality status"
    )
    night: int = Field(default=1, description="Which capture session produced this frame")
    captured_at: datetime | None = Field(
        default=None, description="UTC timestamp of capture (ISO 8601 in JSON)"
    )
    force_fresh_stretch: bool = Field(
        default=False,
        description=(
            "Per-frame override: ignore the project's frozen auto-stretch "
            "params for this specific frame and compute ZScale fresh. "
            "Set via the 'Reset' button in the histogram panel — for "
            "outlier exposures (e.g. one re-shot frame with different "
            "brightness) that don't fit the project-wide freeze."
        ),
    )
    stretch_override: StretchParams | None = Field(
        default=None,
        description=(
            "Per-frame manual stretch parameters (black/white/midtone), "
            "active only when ``force_fresh_stretch`` is True. The "
            "B/W/M sliders write here instead of the project-wide "
            "stretch_params when the user is viewing an overridden "
            "frame, so tuning an outlier exposure doesn't disturb the "
            "rest of the sequence."
        ),
    )
    # Quality metrics (hfr, star_count, snr) are added in a later issue (#139).


class CapturePoint(Coordinate):
    """A point where an image is captured, with per-frame status tracking."""

    index: int = Field(ge=0, description="Zero-based index along the path")
    status: Literal["pending", "capturing", "captured", "failed", "skipped"] = Field(
        default="pending", description="In-flight capture status (transient)"
    )
    target_subs: int = Field(default=1, description="Number of GOOD subs wanted here")
    frames: list[CapturedFrame] = Field(
        default_factory=list, description="Captured sub-exposures at this point"
    )
    skipped: bool = Field(default=False, description="Point deliberately skipped")
    captured_at: datetime | None = Field(
        default=None, description="UTC timestamp of last capture (ISO 8601 in JSON)"
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: Any) -> Any:
        """Migrate legacy v1.0 dicts (files: list[str]) to the frame schema.

        Runs before field validation so old JSON never collides with the new
        schema. New-schema dicts (already carrying ``frames``) pass through.
        """
        if not isinstance(data, dict):
            return data
        if "frames" in data:
            return data  # already new schema
        legacy_files = data.get("files", []) or []
        legacy_status = data.get("status", "pending")
        captured_at = data.get("captured_at")
        # Old points kept all their files; treat each as a good frame.
        data["frames"] = [
            {"filename": f, "status": "good", "night": 1, "captured_at": captured_at}
            for f in legacy_files
        ]
        # Preserve the old completion decision: a point that was "captured"
        # must stay complete after migration, even if it has fewer files than
        # exposures_per_point -- otherwise re-opening an old finished project
        # would falsely look incomplete and trigger unwanted re-capture.
        if legacy_status == "captured":
            data["target_subs"] = max(len(legacy_files), 1)
        else:
            data.setdefault("target_subs", 1)  # planner overrides w/ exposures_per_point
        data.setdefault("skipped", legacy_status == "skipped")
        data.pop("files", None)
        return data

    @property
    def good_count(self) -> int:
        """Number of frames with status 'good'."""
        return sum(1 for f in self.frames if f.status == "good")

    @property
    def is_complete(self) -> bool:
        """True if skipped or enough good subs have been captured."""
        return self.skipped or self.good_count >= self.target_subs

    def filename(self, exposure: int) -> str:
        """Generate a FITS filename for this capture point.

        Args:
            exposure: The 1-based exposure number within this point.

        Returns:
            Filename in the format seq_NNNN_MMM.fits where NNNN is the
            1-based point index and MMM is the exposure number.
        """
        return f"seq_{self.index + 1:04d}_{exposure:03d}.fits"


class INDIConfig(BaseModel):
    """INDI server and device configuration."""

    host: str = Field(default="localhost", description="INDI server hostname")
    port: int = Field(default=7624, description="INDI server port")
    telescope: str = Field(default="", description="INDI telescope device name")
    camera: str = Field(default="", description="INDI camera device name")


class Label(BaseModel):
    """A single annotation drawn into rendered frames.

    Position is stored in the pixel space of one chosen reference frame.
    Tracking across other frames uses the renderer's alignment chain.
    """

    id: str = Field(description="UUID4 — stable across edits")
    text: str = Field(description="Display text")

    ref_frame_index: int = Field(
        ge=0,
        description="Which capture frame's pixel space holds (x, y)",
    )
    x: float = Field(description="Pixel-x in reference frame; sub-pixel allowed")
    y: float = Field(description="Pixel-y in reference frame")

    color: str = Field(default="#ffff00", description="CSS hex; text + marker share")
    font_size: int = Field(default=24, ge=6, le=200)
    marker: Literal["none", "dot", "cross", "circle"] = Field(default="dot")
    text_offset_x: int = Field(default=12, description="Text offset from marker in px")
    text_offset_y: int = Field(default=0)

    leader: Literal["none", "line", "arrow"] = Field(
        default="none",
        description=(
            "Connect marker to text with a leader line. "
            "'arrow' adds a small arrowhead at the marker end."
        ),
    )
    offset_radius: int = Field(
        default=50,
        ge=0,
        le=500,
        description=(
            "For leader-line labels: pixel radius of empty space around "
            "both endpoints (target and text). The drawn line floats "
            "between the target and the text instead of touching either, "
            "so the leader-line doesn't obscure the object it's pointing "
            "at. Ignored when leader='none'."
        ),
    )

    source: Literal["manual", "catalog"] = Field(default="manual")
    catalog_ra: float | None = Field(default=None, description="Original RA in degrees (catalog only)")
    catalog_dec: float | None = Field(default=None, description="Original Dec in degrees (catalog only)")
    catalog_id: str | None = Field(default=None, description="Source identifier, e.g. 'M27'")


@dataclass
class ReconcileReport:
    """Result of reconciling a project's frames against the filesystem.

    Not persisted, so a plain dataclass is sufficient.
    """

    removed_count: int = 0
    affected_points: list[int] = field(default_factory=list)


class Project(BaseModel):
    """Top-level project container, serializable to JSON."""

    version: str = Field(default="2.0", description="Project file format version")
    created: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC creation timestamp (ISO 8601 in JSON)",
    )
    project: str = Field(description="Project name")
    path: SplinePath = Field(description="The spline path for the sequence")
    capture_settings: CaptureSettings = Field(
        default_factory=CaptureSettings, description="Capture parameters"
    )
    # Per-project render look/output settings (issue #151). Default
    # factory makes old manifests without this key validate cleanly.
    render_settings: RenderSettings = Field(
        default_factory=RenderSettings,
        description="Per-project render output and look parameters",
    )
    capture_points: list[CapturePoint] = Field(
        default_factory=list, description="Generated capture points along the path"
    )
    indi: INDIConfig | None = Field(default=None, description="INDI device configuration")
    labels: list[Label] = Field(default_factory=list)
    north_angle_deg: float = Field(
        default=0.0,
        description="Sky orientation correction in degrees; 0° = north up. "
                    "Per-project because mount alignment quirks vary by session.",
    )
    wcs_flip_180: bool = Field(
        default=False,
        description=(
            "Catalog-Overlay 180° um Frame-Mitte drehen — kompensiert "
            "Pierside-Bug in manchen Capture-Tools "
            "(siehe docs/wcs-pierside-analysis.md)."
        ),
    )

    @model_validator(mode="after")
    def _stamp_current_version(self) -> Project:
        """Stamp the current schema version after (migrating) load.

        Old manifests carry version "1.0"; once their points have been
        migrated to the frame schema they are effectively "2.0", so saving
        them again writes the new version and no migration is needed next.
        """
        self.version = "2.0"
        return self

    def next_night(self) -> int:
        """Return the night number for the next capture session.

        Each capture session tags its frames with a night number so a
        multi-night project can tell which session produced which frame.
        The next night is one more than the highest night seen across all
        existing frames; a brand-new project (no frames yet) starts at 1.

        Returns:
            ``max(existing nights) + 1``, or 1 when there are no frames.
        """
        return max(
            (f.night for pt in self.capture_points for f in pt.frames),
            default=0,
        ) + 1

    def reconcile_with_disk(self, capture_dir: Path) -> ReconcileReport:
        """Drop frames whose FITS file no longer exists on disk.

        The filesystem is authoritative: a frame whose file was deleted
        outside the app is removed from ``frames``, which lowers
        ``good_count`` and makes the point incomplete again, so the next
        capture run refills the gap up to ``target_subs``. A manual file
        delete thus behaves exactly like rejecting the frame.

        Args:
            capture_dir: Directory holding the FITS files.

        Returns:
            A report with the number of removed frames and the indices of
            affected capture points.
        """
        report = ReconcileReport()
        for point in self.capture_points:
            kept = [f for f in point.frames if (capture_dir / f.filename).exists()]
            removed = len(point.frames) - len(kept)
            if removed:
                point.frames = kept
                report.removed_count += removed
                report.affected_points.append(point.index)
        return report
