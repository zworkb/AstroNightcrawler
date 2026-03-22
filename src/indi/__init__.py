"""INDI telescope/camera client interface and mock implementation."""

from src.indi.client import (
    CaptureParams,
    CaptureTimeout,
    ConnectionLostError,
    INDIClient,
    INDIError,
    SettleTimeout,
    SlewTimeout,
)
from src.indi.mock import MockINDIClient

__all__ = [
    "CaptureParams",
    "CaptureTimeout",
    "ConnectionLostError",
    "INDIClient",
    "INDIError",
    "MockINDIClient",
    "SettleTimeout",
    "SlewTimeout",
]
