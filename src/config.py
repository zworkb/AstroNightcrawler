"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    All settings can be overridden via environment variables prefixed
    with ``SEQ_``, or via a ``.env`` file in the project root.

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

    model_config = {"env_prefix": "SEQ_", "env_file": ".env"}


settings = Settings()
