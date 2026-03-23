"""INDI XML protocol parser and builder."""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class INDIProperty:
    """A single INDI property value."""

    name: str
    label: str = ""
    value: str | float | bytes = ""
    format: str = ""


@dataclass
class INDIVector:
    """An INDI property vector (group of related properties)."""

    device: str
    name: str
    label: str = ""
    group: str = ""
    state: str = "Idle"  # Idle, Ok, Busy, Alert
    perm: str = "ro"
    timeout: float = 0
    timestamp: str = ""
    members: dict[str, INDIProperty] = field(default_factory=dict)
    vector_type: str = ""  # number, switch, text, blob, light


@dataclass
class INDIMessage:
    """An INDI message from the server."""

    device: str
    timestamp: str
    message: str


class INDIXMLParser:
    """Incremental parser for the INDI XML stream.

    INDI sends XML elements without a root element, so we cannot use a
    standard DOM parser. Instead we buffer incoming bytes and extract
    complete top-level elements by matching open/close tags.
    """

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[ET.Element]:
        """Feed raw bytes and return any complete XML elements.

        Args:
            data: Raw bytes received from the INDI server.

        Returns:
            List of parsed XML elements that were complete in the buffer.
        """
        self._buffer += data
        elements: list[ET.Element] = []

        while self._buffer:
            start = self._buffer.find(b"<")
            if start == -1:
                break

            # Skip processing instructions
            if self._buffer[start : start + 2] == b"<?":
                end = self._buffer.find(b"?>", start)
                if end == -1:
                    break
                self._buffer = self._buffer[end + 2 :]
                continue

            # Strip any leading junk before the tag
            if start > 0:
                self._buffer = self._buffer[start:]
                start = 0

            elem = self._try_extract_element()
            if elem is None:
                break
            elements.append(elem)

        return elements

    def _try_extract_element(self) -> ET.Element | None:
        """Try to extract one complete element from the front of _buffer.

        Returns:
            The parsed element, or None if the buffer is incomplete.
        """
        tag_name = self._read_tag_name()
        if tag_name is None:
            return None

        close_bracket = self._buffer.find(b">")
        if close_bracket == -1:
            return None

        # Self-closing tag: <tag ... />
        if self._buffer[close_bracket - 1 : close_bracket] == b"/":
            return self._consume_bytes(close_bracket + 1)

        # Find matching close tag
        close_tag = f"</{tag_name}>".encode()
        close_pos = self._buffer.find(close_tag, close_bracket)
        if close_pos == -1:
            return None

        end = close_pos + len(close_tag)
        return self._consume_bytes(end)

    def _read_tag_name(self) -> str | None:
        """Read the tag name from the start of _buffer (assumes '<' at 0)."""
        for i in range(1, len(self._buffer)):
            ch = self._buffer[i : i + 1]
            if ch in (b" ", b">", b"/", b"\t", b"\n", b"\r"):
                return self._buffer[1:i].decode("utf-8", errors="replace")
        return None

    def _consume_bytes(self, end: int) -> ET.Element | None:
        """Parse buffer[:end] as XML and advance the buffer."""
        raw = self._buffer[:end]
        self._buffer = self._buffer[end:]
        try:
            return ET.fromstring(raw.decode("utf-8", errors="replace"))
        except ET.ParseError:
            return None


def build_get_properties(device: str | None = None) -> bytes:
    """Build a getProperties command.

    Args:
        device: Optional device name to query. None queries all.

    Returns:
        Encoded XML bytes.
    """
    if device:
        return f'<getProperties version="1.7" device="{device}"/>\n'.encode()
    return b'<getProperties version="1.7"/>\n'


def build_enable_blob(device: str, mode: str = "Also") -> bytes:
    """Build an enableBLOB command.

    Args:
        device: The device name.
        mode: BLOB mode — "Never", "Also", or "Only".

    Returns:
        Encoded XML bytes.
    """
    return f'<enableBLOB device="{device}">{mode}</enableBLOB>\n'.encode()


def build_new_number(
    device: str, name: str, members: dict[str, float]
) -> bytes:
    """Build a newNumberVector command.

    Args:
        device: The device name.
        name: The vector property name.
        members: Mapping of member name to numeric value.

    Returns:
        Encoded XML bytes.
    """
    parts = [f'<newNumberVector device="{device}" name="{name}">']
    for mname, mval in members.items():
        parts.append(f'<oneNumber name="{mname}">{mval}</oneNumber>')
    parts.append("</newNumberVector>\n")
    return "".join(parts).encode()


def build_new_switch(
    device: str, name: str, members: dict[str, str]
) -> bytes:
    """Build a newSwitchVector command.

    Args:
        device: The device name.
        name: The vector property name.
        members: Mapping of member name to switch value ("On"/"Off").

    Returns:
        Encoded XML bytes.
    """
    parts = [f'<newSwitchVector device="{device}" name="{name}">']
    for mname, mval in members.items():
        parts.append(f'<oneSwitch name="{mname}">{mval}</oneSwitch>')
    parts.append("</newSwitchVector>\n")
    return "".join(parts).encode()


def parse_blob_element(elem: ET.Element) -> tuple[str, bytes, str]:
    """Parse a oneBLOB element.

    Args:
        elem: An XML element with tag ``oneBLOB``.

    Returns:
        Tuple of (name, decoded_data, format_string).
    """
    name = elem.get("name", "")
    fmt = elem.get("format", "")
    b64_text = elem.text or ""
    data = base64.b64decode(b64_text)
    return (name, data, fmt)
