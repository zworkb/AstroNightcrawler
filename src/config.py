"""Application configuration via environment variables."""

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
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR

    model_config = {"env_prefix": "NC_", "env_file": ".env"}


settings = Settings()
