"""Mock INDI client for testing without real hardware.

Provides a fully functional in-memory implementation of the INDI client
interface, with configurable delays and failure injection for testing
retry logic.
"""

from __future__ import annotations

import asyncio
import io

import numpy as np
from astropy.io import fits

from src.indi.client import (
    CaptureParams,
    CaptureTimeout,
    INDIClient,
    INDIError,
    SlewTimeout,
)


class MockINDIClient(INDIClient):
    """In-memory INDI client for testing.

    Attributes:
        slew_delay: Simulated slew duration in seconds.
        settle_delay: Simulated settle duration in seconds.
        fail_slew_count: Number of initial slew calls that should fail.
        fail_capture_count: Number of initial capture calls that should fail.
        current_ra: Current right ascension after last successful slew.
        current_dec: Current declination after last successful slew.
    """

    def __init__(
        self,
        *,
        slew_delay: float = 0.01,
        settle_delay: float = 0.01,
        fail_slew_count: int = 0,
        fail_capture_count: int = 0,
    ) -> None:
        """Initialise the mock client.

        Args:
            slew_delay: Simulated slew duration in seconds.
            settle_delay: Simulated settle duration in seconds.
            fail_slew_count: How many initial slew_to calls should raise SlewTimeout.
            fail_capture_count: How many initial capture calls should raise CaptureTimeout.
        """
        self.slew_delay = slew_delay
        self.settle_delay = settle_delay
        self.fail_slew_count = fail_slew_count
        self.fail_capture_count = fail_capture_count
        self.current_ra: float = 0.0
        self.current_dec: float = 0.0
        self._connected: bool = False
        self._slew_attempts: int = 0
        self._capture_attempts: int = 0

    def _require_connected(self) -> None:
        """Raise INDIError if the client is not connected."""
        if not self._connected:
            raise INDIError("not connected")

    @property
    def connected(self) -> bool:
        """Whether the mock client is currently connected."""
        return self._connected

    async def connect(self, host: str, port: int = 7624) -> None:
        """Simulate connecting to an INDI server.

        Args:
            host: Hostname (ignored in mock).
            port: Port number (ignored in mock).
        """
        self._connected = True

    async def disconnect(self) -> None:
        """Simulate disconnecting from the INDI server."""
        self._connected = False

    async def slew_to(self, ra: float, dec: float) -> None:
        """Simulate slewing to coordinates, with optional failure injection.

        Args:
            ra: Right ascension in decimal hours.
            dec: Declination in decimal degrees.

        Raises:
            INDIError: If the client is not connected.
            SlewTimeout: If within the configured failure count.
        """
        self._require_connected()
        self._slew_attempts += 1
        if self._slew_attempts <= self.fail_slew_count:
            raise SlewTimeout(f"slew failed (attempt {self._slew_attempts})")
        await asyncio.sleep(self.slew_delay)
        self.current_ra = ra
        self.current_dec = dec

    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        """Simulate waiting for mount settle.

        Args:
            timeout: Maximum seconds to wait (unused in mock).

        Returns:
            Always True after the configured settle delay.
        """
        self._require_connected()
        await asyncio.sleep(self.settle_delay)
        return True

    async def capture(self, params: CaptureParams) -> bytes:
        """Simulate capturing an image, returning valid FITS bytes.

        Args:
            params: Capture parameters.

        Returns:
            Raw FITS file bytes containing a 100x100 uint16 image.

        Raises:
            INDIError: If the client is not connected.
            CaptureTimeout: If within the configured failure count.
        """
        self._require_connected()
        self._capture_attempts += 1
        if self._capture_attempts <= self.fail_capture_count:
            raise CaptureTimeout(f"capture failed (attempt {self._capture_attempts})")
        return self._generate_fits(params)

    @staticmethod
    def _generate_fits(params: CaptureParams) -> bytes:
        """Build a minimal FITS file in memory.

        Args:
            params: Capture parameters to record in the FITS header.

        Returns:
            Raw FITS bytes.
        """
        hdu = fits.PrimaryHDU(data=np.zeros((100, 100), dtype=np.uint16))
        hdu.header["EXPTIME"] = params.exposure_seconds
        hdu.header["GAIN"] = params.gain
        hdu.header["OFFSET"] = params.offset
        hdu.header["XBINNING"] = params.binning
        buf = io.BytesIO()
        hdu.writeto(buf)
        return buf.getvalue()

    async def get_devices(self) -> dict[str, str]:
        """Return a mock device mapping.

        Returns:
            Dictionary with telescope and camera device names.
        """
        self._require_connected()
        return {"telescope": "Simulator Telescope", "camera": "Simulator CCD"}

    async def abort(self) -> None:
        """Simulate aborting any in-progress operation."""
        self._require_connected()

    async def reconnect(self, timeout: float = 60.0) -> bool:
        """Simulate a reconnection attempt.

        Args:
            timeout: Maximum seconds to retry (unused in mock).

        Returns:
            Always True after re-establishing the connection.
        """
        self._connected = True
        return True
