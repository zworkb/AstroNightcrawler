"""Abstract INDI client interface and related exceptions.

Defines the protocol that all INDI client implementations must follow,
along with typed exceptions for common failure modes and a dataclass
for bundling capture parameters.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass


class INDIError(Exception):
    """Base exception for all INDI client errors."""


class SlewTimeout(INDIError):
    """Raised when a slew operation exceeds its timeout."""


class SettleTimeout(INDIError):
    """Raised when waiting for the mount to settle exceeds its timeout."""


class CaptureTimeout(INDIError):
    """Raised when an image capture exceeds its timeout."""


class ConnectionLostError(INDIError):
    """Raised when the connection to the INDI server is lost."""


@dataclass
class CaptureParams:
    """Bundle of parameters for a single image capture.

    Attributes:
        exposure_seconds: Exposure duration in seconds.
        gain: Sensor gain value.
        offset: Sensor offset value.
        binning: Pixel binning factor (1 = no binning).
    """

    exposure_seconds: float
    gain: int = 0
    offset: int = 0
    binning: int = 1


class INDIClient(ABC):
    """Abstract base class for INDI client implementations.

    Subclasses must override every abstract method to provide a
    concrete connection to an INDI server (or a mock/test double).
    """

    @property
    @abstractmethod
    def connected(self) -> bool:
        """Whether the client is currently connected to an INDI server."""
        raise NotImplementedError

    @abstractmethod
    async def connect(self, host: str, port: int = 7624) -> None:
        """Connect to an INDI server.

        Args:
            host: Hostname or IP address of the INDI server.
            port: TCP port number (default 7624).
        """
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the INDI server."""
        raise NotImplementedError

    @abstractmethod
    async def slew_to(self, ra: float, dec: float) -> None:
        """Slew the telescope to the given equatorial coordinates.

        Args:
            ra: Right ascension in decimal hours (0..24).
            dec: Declination in decimal degrees (-90..+90).
        """
        raise NotImplementedError

    @abstractmethod
    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        """Wait for the mount to finish settling after a slew.

        Args:
            timeout: Maximum seconds to wait before raising SettleTimeout.

        Returns:
            True when the mount has settled.
        """
        raise NotImplementedError

    @abstractmethod
    async def capture(self, params: CaptureParams) -> bytes:
        """Capture a single image and return it as FITS bytes.

        Args:
            params: Capture parameters (exposure, gain, etc.).

        Returns:
            Raw FITS file bytes.
        """
        raise NotImplementedError

    @abstractmethod
    async def get_devices(self) -> dict[str, str]:
        """Return a mapping of device roles to device names.

        Returns:
            Dictionary like ``{"telescope": "...", "camera": "..."}``.
        """
        raise NotImplementedError

    @abstractmethod
    async def abort(self) -> None:
        """Abort any in-progress slew or capture."""
        raise NotImplementedError

    async def reconnect(self, timeout: float = 60.0) -> bool:
        """Retry connecting every 10 seconds for up to *timeout* seconds.

        Args:
            timeout: Maximum total seconds to keep retrying.

        Returns:
            True if reconnection succeeded, False if timed out.
        """
        elapsed = 0.0
        interval = 10.0
        while elapsed < timeout:
            try:
                await self.connect("localhost")
                return True
            except INDIError:
                await asyncio.sleep(interval)
                elapsed += interval
        return False
