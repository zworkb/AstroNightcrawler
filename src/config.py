"""Application configuration via environment variables."""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables prefixed
    with ``NC_``, or via a ``.env`` file in the project root.

    Attributes:
        host: Server bind address.
        port: Server listen port.
        output_dir: Directory for FITS output files.
        indi_host: INDI server hostname.
        indi_port: INDI server port.
        settle_delay: Seconds to pause after slew before capture.
    """

    host: str = "0.0.0.0"
    port: int = 8090
    output_dir: str = "./output"
    indi_host: str = "localhost"
    indi_port: int = 7624
    observer_lat: float = 48.2  # Vienna default
    observer_lon: float = 16.4
    observer_elevation: float = 200.0
    slew_timeout: float = 120.0
    settle_delay: float = 3.0
    settle_timeout: float = 30.0
    capture_timeout_extra: float = 60.0  # added to exposure time (DSLR needs more)
    unpark_delay: float = 3.0  # seconds to wait after unpark before first slew
    render_fps: int = 24
    render_crf: int = 18
    render_transition: str = "crossfade"
    render_crossfade_frames: int = 24  # frames per transition (24 = 1s at 24fps)
    render_align_max_dim: int = 0  # 0 = no downsampling, >0 = max pixel dimension
    render_align_sigma: float = 2.0  # star detection sigma for alignment
    render_resolution: str = "native"  # native, 4k, 1440p, 1080p, 720p
    render_speed: float = 1.0  # playback speed multiplier (1=normal, 2=2x faster)
    render_workers: int = Field(
        default=4,
        description=(
            "Number of parallel workers for alignment + stretch. "
            "-1 = all available CPU cores. Memory rule of thumb: "
            "~50 MB/worker for alignment, ~78 MB/worker for stretch."
        ),
    )
    pixel_scale_arcsec: float = Field(
        default=1.0,
        description=(
            "Arcseconds per pixel of the optical setup. "
            "Used to convert catalog RA/Dec to reference-frame pixels. "
            "Override via NC_PIXEL_SCALE_ARCSEC."
        ),
    )
    render_linear_pan_blend_tail: int = Field(
        default=0,
        description=(
            "Number of trailing frames in a linear-pan transition over "
            "which to crossfade from frame_a's pan to frame_b. Smooths "
            "brightness jumps between keyframes (different exposures of "
            "the same sky region). 0 = no blending (default, byte-"
            "identical to pre-#126 behavior). Recommended: 6-8 (~1/4 of "
            "the crossfade-frame count). Memory cost: one float32 frame_b "
            "crop per pair (~78 MB at 8K-RGB), one temporary float32 "
            "blend per blended frame."
        ),
    )
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    model_config = {"env_prefix": "NC_", "env_file": ".env"}


settings = Settings()
