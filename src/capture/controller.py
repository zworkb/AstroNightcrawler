"""Capture controller state machine for telescope imaging sequences.

Manages the lifecycle of a capture sequence including slew, settle,
capture, and FITS write operations with pause/resume, retry logic,
and safety abort handlers.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import logging
import signal
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from src.capture.fits_writer import FITSWriter
from src.indi.client import (
    CaptureParams,
    CaptureTimeout,
    ConnectionLostError,
    SettleTimeout,
    SlewTimeout,
)

if TYPE_CHECKING:
    from src.indi.client import INDIClient
    from src.models.project import CapturePoint, Project

logger = logging.getLogger(__name__)

SLEW_TIMEOUT = 120.0
SETTLE_TIMEOUT = 30.0
EST_SLEW_SECONDS = 5.0
EST_SETTLE_SECONDS = 3.0


class CaptureState(Enum):
    """Possible states for the capture controller."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class CaptureController:
    """State machine controlling a full capture sequence.

    Iterates through capture points, performing slew, settle, capture,
    and FITS write for each. Supports pause/resume, cancel, skip, and
    automatic retry on transient failures.
    """

    def __init__(
        self,
        project: Project,
        indi_client: INDIClient,
        output_dir: Path,
    ) -> None:
        """Initialise the controller.

        Args:
            project: The project containing capture points and settings.
            indi_client: Connected INDI client implementation.
            output_dir: Directory for FITS output files.
        """
        self.project = project
        self.indi = indi_client
        self.output_dir = output_dir
        self.writer = FITSWriter(output_dir)
        self.state = CaptureState.IDLE
        self.current_point_index: int = 0
        self.last_error: str | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._cancel_flag = False
        self._register_safety_handlers()

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Execute the full capture sequence.

        Iterates all capture points, skipping those already captured.
        On completion writes a manifest. Respects pause and cancel.
        """
        self.state = CaptureState.RUNNING
        points = self.project.capture_points
        while self.current_point_index < len(points):
            if self._cancel_flag:
                self.state = CaptureState.CANCELLED
                return
            await self._pause_event.wait()
            if self._cancel_flag:
                self.state = CaptureState.CANCELLED
                return
            point = points[self.current_point_index]
            if point.status == "captured":
                self.current_point_index += 1
                continue
            await self._capture_point(point)
            if self.state == CaptureState.PAUSED:
                continue
            self.current_point_index += 1
        if not self._cancel_flag:
            self._save_manifest()
            self.state = CaptureState.COMPLETED

    def pause(self) -> None:
        """Pause the capture sequence."""
        self.state = CaptureState.PAUSED
        self._pause_event.clear()

    def resume(self) -> None:
        """Resume the capture sequence after a pause."""
        self.state = CaptureState.RUNNING
        self.last_error = None
        self._pause_event.set()

    def cancel(self) -> None:
        """Cancel the capture sequence."""
        self._cancel_flag = True
        self._pause_event.set()

    def skip_point(self) -> None:
        """Mark the current point as skipped and advance."""
        points = self.project.capture_points
        if self.current_point_index < len(points):
            points[self.current_point_index].status = "skipped"
            self.current_point_index += 1

    @property
    def estimated_remaining_seconds(self) -> float:
        """Estimate seconds remaining for uncaptured points.

        Returns:
            Estimated seconds based on remaining points and settings.
        """
        settings = self.project.capture_settings
        remaining = sum(
            1
            for p in self.project.capture_points[self.current_point_index:]
            if p.status not in ("captured", "skipped")
        )
        per_point = (
            EST_SLEW_SECONDS
            + EST_SETTLE_SECONDS
            + settings.exposure_seconds * settings.exposures_per_point
        )
        return remaining * per_point

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    async def _capture_point(self, point: CapturePoint) -> None:
        """Slew, settle, capture exposures, and write FITS for one point.

        Args:
            point: The capture point to process.
        """
        point.status = "capturing"
        try:
            await self._slew_with_retry(point.ra, point.dec)
            await self._capture_exposures(point)
            point.status = "captured"
            point.captured_at = datetime.now(UTC).isoformat()
        except (OSError, SlewTimeout, SettleTimeout, CaptureTimeout) as exc:
            self._handle_error(point, str(exc))
        except ConnectionLostError:
            await self._handle_connection_loss(point)

    async def _slew_with_retry(self, ra: float, dec: float) -> None:
        """Slew and settle with one automatic retry.

        Args:
            ra: Right ascension in degrees.
            dec: Declination in degrees.
        """
        try:
            await asyncio.wait_for(
                self.indi.slew_to(ra, dec), timeout=SLEW_TIMEOUT
            )
            await asyncio.wait_for(
                self.indi.wait_for_settle(SETTLE_TIMEOUT),
                timeout=SETTLE_TIMEOUT,
            )
        except (TimeoutError, SlewTimeout, SettleTimeout):
            await self._retry_slew(ra, dec)

    async def _retry_slew(self, ra: float, dec: float) -> None:
        """Single retry of slew+settle. Raises on second failure.

        Args:
            ra: Right ascension in degrees.
            dec: Declination in degrees.
        """
        await asyncio.wait_for(
            self.indi.slew_to(ra, dec), timeout=SLEW_TIMEOUT
        )
        await asyncio.wait_for(
            self.indi.wait_for_settle(SETTLE_TIMEOUT),
            timeout=SETTLE_TIMEOUT,
        )

    async def _capture_exposures(self, point: CapturePoint) -> None:
        """Capture all exposures for a point and write FITS files.

        Args:
            point: The capture point to capture exposures for.
        """
        settings = self.project.capture_settings
        params = CaptureParams(
            exposure_seconds=settings.exposure_seconds,
            gain=settings.gain,
            offset=settings.offset,
            binning=settings.binning,
        )
        timeout = settings.exposure_seconds + 30.0
        for i in range(1, settings.exposures_per_point + 1):
            data = await self._capture_single(params, timeout)
            self.writer.write(point, i, data)

    async def _capture_single(
        self, params: CaptureParams, timeout: float
    ) -> bytes:
        """Capture one exposure with retry on timeout.

        Args:
            params: Capture parameters.
            timeout: Timeout in seconds for the capture.

        Returns:
            Raw FITS bytes from the camera.
        """
        try:
            return await asyncio.wait_for(
                self.indi.capture(params), timeout=timeout
            )
        except (TimeoutError, CaptureTimeout):
            return await asyncio.wait_for(
                self.indi.capture(params), timeout=timeout
            )

    def _handle_error(self, point: CapturePoint, error_msg: str) -> None:
        """Set error state and pause on failure.

        Args:
            point: The capture point that failed.
            error_msg: Description of the error.
        """
        point.status = "failed"
        self.last_error = error_msg
        self.state = CaptureState.PAUSED
        self._pause_event.clear()
        logger.error("Capture paused: %s", error_msg)

    async def _handle_connection_loss(
        self, point: CapturePoint
    ) -> None:
        """Try to reconnect; pause if reconnection fails.

        Args:
            point: The capture point that was in progress.
        """
        success = await self.indi.reconnect(timeout=60)
        if not success:
            self._handle_error(point, "Connection lost, reconnect failed")

    def _save_manifest(self) -> None:
        """Write the project JSON to output_dir/manifest.json."""
        manifest_path = self.output_dir / "manifest.json"
        manifest_path.write_text(
            self.project.model_dump_json(indent=2)
        )

    def _register_safety_handlers(self) -> None:
        """Register atexit and signal handlers to abort on shutdown."""
        atexit.register(self._safety_abort)
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, self._signal_handler)

    def _safety_abort(self) -> None:
        """Synchronous abort called at interpreter exit."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.indi.abort())
            else:
                loop.run_until_complete(self.indi.abort())
        except Exception:  # noqa: BLE001
            pass

    def _signal_handler(self, signum: int, frame: object) -> None:
        """Handle SIGTERM/SIGINT by aborting the INDI client.

        Args:
            signum: Signal number received.
            frame: Current stack frame (unused).
        """
        self._safety_abort()
