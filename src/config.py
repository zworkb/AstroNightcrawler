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
    settle_timeout: float = 30.0
    capture_timeout_extra: float = 60.0  # added to exposure time (DSLR needs more)

    model_config = {"env_prefix": "NC_", "env_file": ".env"}


settings = Settings()
