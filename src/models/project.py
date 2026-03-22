"""Pydantic data models for telescope imaging sequence projects."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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


class CapturePoint(Coordinate):
    """A point where an image is captured, with status tracking."""

    index: int = Field(ge=0, description="Zero-based index along the path")
    status: Literal["pending", "capturing", "captured", "failed", "skipped"] = Field(
        default="pending", description="Capture status"
    )
    files: list[str] = Field(default_factory=list, description="List of captured file paths")
    captured_at: str | None = Field(
        default=None, description="ISO 8601 UTC timestamp of capture completion"
    )

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


class Project(BaseModel):
    """Top-level project container, serializable to JSON."""

    version: str = Field(default="1.0", description="Project file format version")
    created: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO 8601 UTC creation timestamp",
    )
    project: str = Field(description="Project name")
    path: SplinePath = Field(description="The spline path for the sequence")
    capture_settings: CaptureSettings = Field(
        default_factory=CaptureSettings, description="Capture parameters"
    )
    capture_points: list[CapturePoint] = Field(
        default_factory=list, description="Generated capture points along the path"
    )
    indi: INDIConfig | None = Field(default=None, description="INDI device configuration")
