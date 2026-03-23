"""Async INDI adapter wrapping AsyncINDIClient.

Implements the INDIClient ABC using the pure-Python async INDI client,
replacing the PyIndi-based RealINDIClient for native BLOB support
over network connections.
"""

from __future__ import annotations

import asyncio
import logging

from src.config import settings
from src.indi.asynclient.client import AsyncINDIClient
from src.indi.client import (
    CaptureParams,
    CaptureTimeout,
    INDIClient,
    INDIError,
    SlewTimeout,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5


class AsyncINDIAdapter(INDIClient):
    """INDI client adapter backed by AsyncINDIClient.

    Discovers telescope and camera devices automatically by checking
    for the presence of standard INDI properties (EQUATORIAL_EOD_COORD
    for telescopes, CCD_EXPOSURE for cameras).
    """

    def __init__(self) -> None:
        self._inner = AsyncINDIClient()

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected."""
        return self._inner.connected

    async def connect(self, host: str, port: int = 7624) -> None:
        """Connect to an INDI server and prepare camera for BLOBs.

        Args:
            host: Hostname or IP address.
            port: TCP port (default 7624).
        """
        self._last_host = host
        self._last_port = port
        try:
            await self._inner.connect(host, port)
        except OSError as exc:
            raise INDIError(f"Cannot connect to {host}:{port}") from exc
        await self._setup_camera_blob()

    async def disconnect(self) -> None:
        """Disconnect from the INDI server."""
        await self._inner.disconnect()
        logger.info("Disconnected from INDI server")

    async def unpark(self) -> None:
        """Unpark the telescope mount before capturing."""
        telescope = self._find_telescope()
        if not telescope:
            return
        # Check if already unparked (UNPARK switch is On)
        vec = self._inner.get_vector(telescope, "TELESCOPE_PARK")
        if vec:
            unpark_val = vec.members.get("UNPARK")
            if unpark_val and unpark_val.value == "On":
                logger.info("Mount already unparked (UNPARK=On)")
                return
        try:
            await self._inner.send_switch(
                telescope, "TELESCOPE_PARK",
                {"UNPARK": "On", "PARK": "Off"},
            )
            logger.info("Unpark sent to %s, waiting...", telescope)
            # Wait for unpark to complete (up to 15s)
            elapsed = 0.0
            while elapsed < 15.0:
                s = self._get_vector_state(telescope, "TELESCOPE_PARK")
                if s in ("Ok", "Idle"):
                    logger.info(
                        "Unpark complete (state=%s), waiting %.1fs",
                        s, settings.unpark_delay,
                    )
                    await asyncio.sleep(settings.unpark_delay)
                    return
                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL
            logger.warning("Unpark timeout after 15s, continuing")
        except Exception:  # noqa: BLE001
            logger.warning("Unpark failed (mount may not support it)")

    async def slew_to(self, ra: float, dec: float) -> None:
        """Slew the telescope to the given coordinates.

        Args:
            ra: Right ascension in degrees (0..360).
            dec: Declination in degrees (-90..+90).
        """
        telescope = self._require_telescope()
        ra_hours = ra / 15.0
        await self._inner.send_number(
            telescope, "EQUATORIAL_EOD_COORD",
            {"RA": ra_hours, "DEC": dec},
        )
        logger.info(
            "Slew commanded: RA=%.4f deg (%.4fh) Dec=%.4f deg",
            ra, ra_hours, dec,
        )
        await self._poll_vector_state(
            telescope, "EQUATORIAL_EOD_COORD",
            settings.slew_timeout, SlewTimeout,
        )

    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        """Wait for the mount to settle after slewing.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True when the mount has settled.
        """
        telescope = self._find_telescope()
        if not telescope:
            return True
        elapsed = 0.0
        while elapsed < timeout:
            state = self._get_vector_state(
                telescope, "EQUATORIAL_EOD_COORD",
            )
            if state in ("Ok", "Idle"):
                logger.info(
                    "Mount settled (state=%s), waiting %.1fs settle delay",
                    state, settings.settle_delay,
                )
                await asyncio.sleep(settings.settle_delay)
                return True
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        logger.warning("Settle timeout after %.0fs, continuing", timeout)
        return True

    async def capture(self, params: CaptureParams) -> bytes:
        """Capture a single image and return the raw bytes.

        Args:
            params: Exposure, gain, binning, etc.

        Returns:
            Raw image bytes (typically FITS).
        """
        camera = self._require_camera()
        await self._configure_camera(camera, params)
        return await self._start_exposure(camera, params)

    async def get_devices(self) -> dict[str, str]:
        """Return discovered telescope and camera device names.

        Returns:
            Mapping of role to device name.
        """
        result: dict[str, str] = {}
        telescope = self._find_telescope()
        camera = self._find_camera()
        if telescope:
            result["telescope"] = telescope
        if camera:
            result["camera"] = camera
        return result

    async def abort(self) -> None:
        """Abort any in-progress slew."""
        if not self.connected:
            return
        telescope = self._find_telescope()
        if not telescope:
            return
        await self._inner.send_switch(
            telescope, "TELESCOPE_ABORT_MOTION",
            {"ABORT": "On"},
        )
        logger.info("Abort sent")

    # -- private helpers --

    def _find_telescope(self) -> str | None:
        """Return device name with EQUATORIAL_EOD_COORD."""
        return self._inner.find_device_with_property(
            "EQUATORIAL_EOD_COORD",
        )

    def _find_camera(self) -> str | None:
        """Return device name with CCD_EXPOSURE."""
        return self._inner.find_device_with_property("CCD_EXPOSURE")

    def _require_telescope(self) -> str:
        """Return telescope device name or raise."""
        self._require_connected()
        name = self._find_telescope()
        if not name:
            raise INDIError("No telescope device found")
        return name

    def _require_camera(self) -> str:
        """Return camera device name or raise."""
        self._require_connected()
        name = self._find_camera()
        if not name:
            raise INDIError("No camera device found")
        return name

    def _require_connected(self) -> None:
        """Raise if not connected."""
        if not self.connected:
            raise INDIError("Not connected")

    def _get_vector_state(
        self, device: str, vector: str,
    ) -> str | None:
        """Get the state string of a property vector."""
        vec = self._inner.get_vector(device, vector)
        return vec.state if vec else None

    async def _setup_camera_blob(self) -> None:
        """Enable BLOB mode and set UPLOAD_MODE for the camera."""
        camera = self._find_camera()
        if not camera:
            return
        await self._inner.enable_blob(camera, "Also")
        await self._inner.send_switch(
            camera, "UPLOAD_MODE",
            {"UPLOAD_CLIENT": "On", "UPLOAD_LOCAL": "Off",
             "UPLOAD_BOTH": "Off"},
        )
        logger.info("Camera BLOB mode enabled for %s", camera)

    async def _configure_camera(
        self, camera: str, params: CaptureParams,
    ) -> None:
        """Apply gain and binning settings before exposure.

        Args:
            camera: Camera device name.
            params: Capture parameters.
        """
        await self._inner.send_number(
            camera, "CCD_GAIN", {"GAIN": float(params.gain)},
        )
        await self._inner.send_number(
            camera, "CCD_BINNING",
            {"HOR_BIN": float(params.binning),
             "VER_BIN": float(params.binning)},
        )

    async def _start_exposure(
        self, camera: str, params: CaptureParams,
    ) -> bytes:
        """Trigger exposure and wait for BLOB data.

        Args:
            camera: Camera device name.
            params: Capture parameters.

        Returns:
            Raw image bytes.
        """
        await self._inner.send_number(
            camera, "CCD_EXPOSURE",
            {"CCD_EXPOSURE_VALUE": params.exposure_seconds},
        )
        logger.info("Exposure started: %.1fs", params.exposure_seconds)
        timeout = params.exposure_seconds + settings.capture_timeout_extra
        blob = await self._inner.wait_for_blob(timeout=timeout)
        if blob is None:
            raise CaptureTimeout(
                f"Capture timed out after {timeout}s",
            )
        logger.info("Image received: %d bytes", len(blob))
        return blob

    async def _poll_vector_state(
        self,
        device: str,
        vector: str,
        timeout: float,
        exc_cls: type[INDIError],
    ) -> None:
        """Poll a vector state until Ok, or raise on timeout/alert.

        First waits for state to leave Ok/Idle (operation started),
        then waits for it to return to Ok (operation complete).

        Args:
            device: INDI device name.
            vector: Property vector name.
            timeout: Seconds before raising.
            exc_cls: Exception class to raise on failure.
        """
        elapsed = 0.0
        # Phase 1: wait for Busy (operation started)
        while elapsed < min(timeout, 5.0):
            state = self._get_vector_state(device, vector)
            if state == "Busy":
                break
            if state == "Alert":
                raise exc_cls("Operation failed (Alert state)")
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        # Phase 2: wait for Ok (operation complete)
        while elapsed < timeout:
            state = self._get_vector_state(device, vector)
            if state == "Ok":
                return
            if state == "Alert":
                raise exc_cls("Operation failed (Alert state)")
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        raise exc_cls(f"Timed out after {timeout}s")
