"""Async INDI client using raw TCP/XML protocol."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from src.indi.asynclient.protocol import (
    INDIProperty,
    INDIVector,
    INDIXMLParser,
    build_enable_blob,
    build_get_properties,
    build_new_number,
    build_new_switch,
    parse_blob_element,
)

logger = logging.getLogger(__name__)


@dataclass
class INDIDevice:
    """Represents a discovered INDI device with its properties."""

    name: str
    vectors: dict[str, INDIVector] = field(default_factory=dict)


class AsyncINDIClient:
    """Pure-Python async INDI client.

    Connects to an INDI server via TCP and speaks the INDI XML protocol
    directly. Handles BLOBs natively (unlike PyIndi over network).
    """

    def __init__(self) -> None:
        self.devices: dict[str, INDIDevice] = {}
        self.connected: bool = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._parser = INDIXMLParser()
        self._rx_task: asyncio.Task[None] | None = None
        self._blob_event = asyncio.Event()
        self._blob_data: bytes | None = None
        self._host: str = ""
        self._port: int = 7624

    async def connect(self, host: str, port: int = 7624) -> None:
        """Connect to an INDI server.

        Args:
            host: Hostname or IP address of the INDI server.
            port: TCP port (default 7624).
        """
        self._host = host
        self._port = port
        self._reader, self._writer = await asyncio.open_connection(
            host, port
        )
        self.connected = True
        logger.info("Connected to INDI server %s:%d", host, port)

        self._rx_task = asyncio.create_task(self._receive_loop())
        await self._send(build_get_properties())

        # Allow time for device definitions to arrive
        await asyncio.sleep(2.0)
        logger.info("Devices: %s", list(self.devices.keys()))

    async def disconnect(self) -> None:
        """Disconnect from the INDI server."""
        if self._rx_task:
            self._rx_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._rx_task
        if self._writer:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
        self.connected = False
        logger.info("Disconnected")

    async def enable_blob(self, device: str, mode: str = "Also") -> None:
        """Enable BLOB reception for a device.

        Args:
            device: The device name.
            mode: BLOB mode — "Never", "Also", or "Only".
        """
        await self._send(build_enable_blob(device, mode))
        logger.info("BLOB mode '%s' for %s", mode, device)

    async def send_number(
        self, device: str, vector: str, members: dict[str, float]
    ) -> None:
        """Send a newNumberVector command.

        Args:
            device: The device name.
            vector: The property vector name.
            members: Mapping of member name to numeric value.
        """
        await self._send(build_new_number(device, vector, members))

    async def send_switch(
        self, device: str, vector: str, members: dict[str, str]
    ) -> None:
        """Send a newSwitchVector command.

        Args:
            device: The device name.
            vector: The property vector name.
            members: Mapping of member name to switch value.
        """
        await self._send(build_new_switch(device, vector, members))

    async def wait_for_blob(self, timeout: float = 60.0) -> bytes | None:
        """Wait for a BLOB to arrive.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            Raw BLOB bytes, or None on timeout.
        """
        self._blob_data = None
        self._blob_event.clear()
        try:
            await asyncio.wait_for(
                self._blob_event.wait(), timeout=timeout
            )
            return self._blob_data
        except TimeoutError:
            return None

    def get_device(self, name: str) -> INDIDevice | None:
        """Get a device by name, or None if not found."""
        return self.devices.get(name)

    def get_vector(
        self, device: str, vector: str
    ) -> INDIVector | None:
        """Get a property vector from a device."""
        dev = self.devices.get(device)
        if dev:
            return dev.vectors.get(vector)
        return None

    def get_number(
        self, device: str, vector: str, member: str
    ) -> float | None:
        """Get a numeric property value."""
        vec = self.get_vector(device, vector)
        if vec and member in vec.members:
            try:
                return float(vec.members[member].value)
            except (ValueError, TypeError):
                return None
        return None

    def get_switch_state(
        self, device: str, vector: str
    ) -> str | None:
        """Get the state of a switch vector (Idle, Ok, Busy, Alert)."""
        vec = self.get_vector(device, vector)
        if vec:
            return vec.state
        return None

    def find_device_with_property(self, vector_name: str) -> str | None:
        """Find the first device that has a given property vector."""
        for dev_name, dev in self.devices.items():
            if vector_name in dev.vectors:
                return dev_name
        return None

    async def _send(self, data: bytes) -> None:
        """Send raw bytes to the server."""
        if self._writer:
            self._writer.write(data)
            await self._writer.drain()

    async def _receive_loop(self) -> None:
        """Background task: read from server and parse XML elements."""
        assert self._reader is not None
        try:
            while True:
                data = await self._reader.read(1_048_576)  # 1MB chunks
                if not data:
                    logger.warning("INDI server closed connection")
                    self.connected = False
                    break
                for elem in self._parser.feed(data):
                    self._handle_element(elem)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Receive loop error")
            self.connected = False

    def _handle_element(self, elem: object) -> None:
        """Dispatch a parsed XML element to the appropriate handler."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        tag = elem.tag
        if tag.startswith("def"):
            self._handle_def(elem)
        elif tag.startswith("set"):
            self._handle_set(elem)
        elif tag == "message":
            self._handle_message(elem)
        elif tag == "delProperty":
            self._handle_del(elem)

    def _handle_def(self, elem: object) -> None:
        """Handle defXxxVector — new property definition."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        device_name = elem.get("device", "")
        vector_name = elem.get("name", "")

        if device_name not in self.devices:
            self.devices[device_name] = INDIDevice(name=device_name)
            logger.info("New device: %s", device_name)

        vec_type = elem.tag.replace("def", "").replace("Vector", "").lower()
        vec = INDIVector(
            device=device_name,
            name=vector_name,
            label=elem.get("label", ""),
            group=elem.get("group", ""),
            state=elem.get("state", "Idle"),
            perm=elem.get("perm", "ro"),
            vector_type=vec_type,
        )
        for child in elem:
            member_name = child.get("name", "")
            vec.members[member_name] = INDIProperty(
                name=member_name,
                label=child.get("label", ""),
                value=child.text.strip() if child.text else "",
            )
        self.devices[device_name].vectors[vector_name] = vec

    def _handle_set(self, elem: object) -> None:
        """Handle setXxxVector — property update."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        if elem.tag == "setBLOBVector":
            self._handle_blob(elem)
            return

        device_name = elem.get("device", "")
        vector_name = elem.get("name", "")
        vec = self.get_vector(device_name, vector_name)
        if not vec:
            return

        vec.state = elem.get("state", vec.state)
        vec.timestamp = elem.get("timestamp", "")
        for child in elem:
            member_name = child.get("name", "")
            text = child.text.strip() if child.text else ""
            if member_name in vec.members:
                vec.members[member_name].value = text
            else:
                vec.members[member_name] = INDIProperty(
                    name=member_name, value=text
                )

    def _handle_blob(self, elem: object) -> None:
        """Handle setBLOBVector — incoming image data."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        device = elem.get("device", "")
        vector = elem.get("name", "")
        for child in elem:
            if child.tag == "oneBLOB":
                name, data, fmt = parse_blob_element(child)
                logger.info(
                    "BLOB received: %s.%s.%s %d bytes format=%s",
                    device, vector, name, len(data), fmt,
                )
                self._blob_data = data
                self._blob_event.set()

    def _handle_message(self, elem: object) -> None:
        """Handle message element."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        device = elem.get("device", "")
        msg_text = elem.get("message", "")
        if msg_text:
            logger.info("INDI [%s]: %s", device, msg_text)

    def _handle_del(self, elem: object) -> None:
        """Handle delProperty."""
        import xml.etree.ElementTree as ET

        assert isinstance(elem, ET.Element)
        device = elem.get("device", "")
        prop = elem.get("name", "")
        dev = self.devices.get(device)
        if dev and prop and prop in dev.vectors:
            del dev.vectors[prop]
