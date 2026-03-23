"""Real INDI client using pyindi-client.

Connects to a live INDI server for actual telescope and camera control,
implementing the abstract INDIClient interface.
"""

from __future__ import annotations

import asyncio
import logging
import time

import PyIndi

from src.indi.client import (
    CaptureParams,
    CaptureTimeout,
    INDIClient,
    INDIError,
    SlewTimeout,
)

logger = logging.getLogger(__name__)

_SLEW_TIMEOUT = 120.0
_STDERR_SUPPRESSED = False


def _suppress_indi_stderr() -> None:
    """Replace fd 2 with a pipe that filters out PyIndi C-library spam.

    Keeps real error messages, suppresses known noise like
    "No IText", "Dispatch command error".
    """
    global _STDERR_SUPPRESSED  # noqa: PLW0603
    if _STDERR_SUPPRESSED:
        return
    import os
    import threading

    _SUPPRESS_PATTERNS = (
        b"No IText",
        b"No INumber",
        b"No ISwitch",
        b"Dispatch command error",
        b"Could not find property",
    )

    read_fd, write_fd = os.pipe()
    old_fd = os.dup(2)
    os.dup2(write_fd, 2)
    os.close(write_fd)

    def _filter() -> None:
        with os.fdopen(read_fd, "rb") as reader:
            for line in reader:
                if any(p in line for p in _SUPPRESS_PATTERNS):
                    continue
                os.write(old_fd, line)

    t = threading.Thread(target=_filter, daemon=True)
    t.start()
    _STDERR_SUPPRESSED = True
    logger.info("PyIndi stderr filter active")
_PROPERTY_POLL = 0.5


class _INDIHandler(PyIndi.BaseClient):
    """Low-level PyIndi callback handler.

    Collects device references and BLOB data received from the server.
    """

    def __init__(self) -> None:
        super().__init__()
        self.devices: dict[str, PyIndi.BaseDevice] = {}
        self._blob_data: bytes | None = None
        self._blob_event = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- device / property callbacks --

    def newDevice(self, d: PyIndi.BaseDevice) -> None:  # noqa: N802
        """Register a newly discovered INDI device."""
        name = d.getDeviceName()
        self.devices[name] = d
        logger.info("INDI device discovered: %s", name)

    def newProperty(self, p: PyIndi.Property) -> None:  # noqa: N802
        """Handle a new property (no-op)."""

    def removeProperty(self, p: PyIndi.Property) -> None:  # noqa: N802
        """Handle property removal (no-op)."""

    def newBLOB(self, bp: PyIndi.PropertyBlob) -> None:  # noqa: N802
        """Receive BLOB (image data) from camera."""
        for i in range(bp.count()):
            blob = bp[i]
            self._blob_data = blob.getblobdata()
            size = len(self._blob_data) if self._blob_data else 0
            print(f"BLOB received: {size} bytes")  # noqa: T201
            # Thread-safe event set via event loop
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._blob_event.set)
            else:
                self._blob_event.set()

    def newSwitch(self, svp: PyIndi.PropertySwitch) -> None:  # noqa: N802
        """Handle switch update (no-op)."""

    def newNumber(self, nvp: PyIndi.PropertyNumber) -> None:  # noqa: N802
        """Handle number update (no-op)."""

    def newText(self, tvp: PyIndi.PropertyText) -> None:  # noqa: N802
        """Handle text update (no-op)."""

    def newLight(self, lvp: PyIndi.PropertyLight) -> None:  # noqa: N802
        """Handle light update (no-op)."""

    def newMessage(self, d: PyIndi.BaseDevice, m: int) -> None:  # noqa: N802
        """Log an INDI message from the server."""
        logger.debug("INDI message: %s", m)

    def serverConnected(self) -> None:  # noqa: N802
        """Log successful server connection."""
        logger.info("INDI server connected")

    def serverDisconnected(self, code: int) -> None:  # noqa: N802
        """Log server disconnection."""
        logger.warning("INDI server disconnected (code %s)", code)


class RealINDIClient(INDIClient):
    """INDI client using pyindi-client for real hardware control.

    Discovers telescope and camera devices automatically by checking
    for the presence of standard INDI properties.
    """

    def __init__(self) -> None:
        self._handler = _INDIHandler()
        self._connected = False

    @property
    def connected(self) -> bool:
        """Whether the client is currently connected."""
        return self._connected

    async def connect(self, host: str, port: int = 7624) -> None:
        """Connect to an INDI server.

        Args:
            host: Hostname or IP address.
            port: TCP port (default 7624).
        """
        self._last_host = host
        self._last_port = port
        self._handler.setServer(host, port)
        _suppress_indi_stderr()
        ok = self._handler.connectServer()
        if not ok:
            raise INDIError(f"Cannot connect to {host}:{port}")
        self._connected = True
        await asyncio.sleep(2.0)  # allow device enumeration
        logger.info("Devices after connect: %s", list(self._handler.devices.keys()))

    async def disconnect(self) -> None:
        """Disconnect from the INDI server."""
        self._handler.disconnectServer()
        self._connected = False
        logger.info("Disconnected from INDI server")

    async def slew_to(self, ra: float, dec: float) -> None:
        """Slew the telescope to the given coordinates.

        Args:
            ra: Right ascension in degrees (0..360).
            dec: Declination in degrees (-90..+90).
        """
        self._require_connected()
        telescope = self._find_telescope()
        if not telescope:
            raise INDIError("No telescope device found")

        coord = await self._await_number(telescope, "EQUATORIAL_EOD_COORD")
        ra_hours = ra / 15.0  # INDI uses hours for RA
        coord[0].value = ra_hours
        coord[1].value = dec
        self._handler.sendNewNumber(coord)
        logger.info("Slew commanded: RA=%.4f° (%.4fh)  Dec=%.4f°", ra, ra_hours, dec)

        await self._poll_until_ok(coord, _SLEW_TIMEOUT, SlewTimeout)

    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        """Wait for the mount to settle after slewing.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            True when the mount has settled.
        """
        self._require_connected()
        telescope = self._find_telescope()
        if not telescope:
            return True

        # Wait for EQUATORIAL_EOD_COORD to reach IPS_OK or IPS_IDLE
        coord = telescope.getNumber("EQUATORIAL_EOD_COORD")
        if coord:
            start = time.monotonic()
            while time.monotonic() - start < timeout:
                state = coord.getState()
                if state in (PyIndi.IPS_OK, PyIndi.IPS_IDLE):
                    logger.info("Mount settled (state=%s)", state)
                    return True
                await asyncio.sleep(_PROPERTY_POLL)
            # Timeout — log but don't fail, mount may still be OK
            logger.warning("Settle timeout after %.0fs, continuing", timeout)
            return True
        # No coordinate property — assume settled
        return True

    async def capture(self, params: CaptureParams) -> bytes:
        """Capture a single image and return the raw bytes.

        Args:
            params: Exposure, gain, binning, etc.

        Returns:
            Raw image bytes (typically FITS).
        """
        self._require_connected()
        camera = self._find_camera()
        if not camera:
            raise INDIError("No camera device found")

        self._configure_camera(camera, params)
        return await self._start_exposure(camera, params)

    async def get_devices(self) -> dict[str, str]:
        """Return discovered telescope and camera device names.

        Returns:
            Mapping of role to device name.
        """
        self._require_connected()
        result: dict[str, str] = {}
        telescope = self._find_telescope()
        camera = self._find_camera()
        if telescope:
            result["telescope"] = telescope.getDeviceName()
        if camera:
            result["camera"] = camera.getDeviceName()
        return result

    async def abort(self) -> None:
        """Abort any in-progress slew."""
        if not self._connected:
            return
        telescope = self._find_telescope()
        if not telescope:
            return
        abort_prop = telescope.getSwitch("TELESCOPE_ABORT_MOTION")
        if abort_prop:
            abort_prop[0].setState(PyIndi.ISS_ON)
            self._handler.sendNewSwitch(abort_prop)
            logger.info("Abort sent")

    # -- private helpers --

    def _require_connected(self) -> None:
        """Raise if not connected."""
        if not self._connected:
            raise INDIError("not connected")

    def _find_telescope(self) -> PyIndi.BaseDevice | None:
        """Return the first device with EQUATORIAL_EOD_COORD."""
        for dev in self._handler.devices.values():
            if dev.getNumber("EQUATORIAL_EOD_COORD"):
                return dev
        return None

    def _find_camera(self) -> PyIndi.BaseDevice | None:
        """Return the first device with CCD_EXPOSURE."""
        for dev in self._handler.devices.values():
            if dev.getNumber("CCD_EXPOSURE"):
                return dev
        return None

    async def _await_number(
        self,
        device: PyIndi.BaseDevice,
        name: str,
        timeout: float = 10.0,
    ) -> PyIndi.PropertyNumber:
        """Poll until a number property is available.

        Args:
            device: INDI device to query.
            name: Property name.
            timeout: Seconds before giving up.

        Returns:
            The number property vector.
        """
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            prop = device.getNumber(name)
            if prop:
                return prop
            await asyncio.sleep(_PROPERTY_POLL)
        raise INDIError(f"Property {name} not available after {timeout}s")

    @staticmethod
    async def _poll_until_ok(
        prop: PyIndi.PropertyNumber,
        timeout: float,
        exc_cls: type[INDIError],
    ) -> None:
        """Poll a property until its state is IPS_OK.

        Args:
            prop: Property to monitor.
            timeout: Seconds before raising.
            exc_cls: Exception class to raise on timeout or alert.
        """
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            state = prop.getState()
            if state == PyIndi.IPS_OK:
                return
            if state == PyIndi.IPS_ALERT:
                raise exc_cls("Operation failed (ALERT state)")
            await asyncio.sleep(_PROPERTY_POLL)
        raise exc_cls(f"Timed out after {timeout}s")

    def _configure_camera(
        self,
        camera: PyIndi.BaseDevice,
        params: CaptureParams,
    ) -> None:
        """Apply gain and binning settings before exposure.

        Args:
            camera: Camera device.
            params: Capture parameters.
        """
        self._handler.setBLOBMode(
            PyIndi.B_ALSO, camera.getDeviceName(), None,
        )
        gain_prop = camera.getNumber("CCD_GAIN")
        if gain_prop:
            gain_prop[0].value = params.gain
            self._handler.sendNewNumber(gain_prop)

        bin_prop = camera.getNumber("CCD_BINNING")
        if bin_prop:
            bin_prop[0].value = params.binning
            bin_prop[1].value = params.binning
            self._handler.sendNewNumber(bin_prop)

    async def _start_exposure(
        self,
        camera: PyIndi.BaseDevice,
        params: CaptureParams,
    ) -> bytes:
        """Trigger an exposure and wait for BLOB data.

        Args:
            camera: Camera device.
            params: Capture parameters.

        Returns:
            Raw image bytes.
        """
        self._handler._blob_data = None
        self._handler._blob_event.clear()
        self._handler._loop = asyncio.get_running_loop()

        exp_prop = await self._await_number(camera, "CCD_EXPOSURE")
        exp_prop[0].value = params.exposure_seconds
        self._handler.sendNewNumber(exp_prop)
        logger.info("Exposure started: %.1fs", params.exposure_seconds)

        from src.config import settings
        timeout = params.exposure_seconds + settings.capture_timeout_extra
        try:
            await asyncio.wait_for(
                self._handler._blob_event.wait(), timeout=timeout,
            )
        except TimeoutError as err:
            raise CaptureTimeout(f"Capture timed out after {timeout}s") from err

        if self._handler._blob_data is None:
            raise CaptureTimeout("No image data received")

        logger.info("Image received: %d bytes", len(self._handler._blob_data))
        return self._handler._blob_data
