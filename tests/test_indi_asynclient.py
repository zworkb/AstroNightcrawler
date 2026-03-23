"""Tests for the async INDI client XML parser."""
import base64
import xml.etree.ElementTree as ET

from src.indi.asynclient.protocol import (
    INDIXMLParser,
    build_enable_blob,
    build_get_properties,
    build_new_number,
    build_new_switch,
    parse_blob_element,
)


class TestINDIXMLParser:
    def test_parse_complete_element(self) -> None:
        parser = INDIXMLParser()
        xml = (
            b'<defNumberVector device="Telescope" name="COORD">'
            b'<oneNumber name="RA">12.5</oneNumber>'
            b"</defNumberVector>"
        )
        elems = parser.feed(xml)
        assert len(elems) == 1
        assert elems[0].tag == "defNumberVector"
        assert elems[0].get("device") == "Telescope"

    def test_parse_partial_then_complete(self) -> None:
        parser = INDIXMLParser()
        part1 = (
            b'<defNumberVector device="Tel" name="X">'
            b"<oneNumber name=\"V\">1</one"
        )
        assert parser.feed(part1) == []
        part2 = b"Number></defNumberVector>"
        elems = parser.feed(part2)
        assert len(elems) == 1

    def test_parse_multiple_elements(self) -> None:
        parser = INDIXMLParser()
        xml = (
            b'<message device="T" message="hello"/>'
            b'<message device="T" message="world"/>'
        )
        elems = parser.feed(xml)
        assert len(elems) == 2

    def test_parse_self_closing(self) -> None:
        parser = INDIXMLParser()
        xml = b'<message device="T" message="hi"/>'
        elems = parser.feed(xml)
        assert len(elems) == 1
        assert elems[0].get("message") == "hi"


class TestBuildCommands:
    def test_get_properties(self) -> None:
        data = build_get_properties()
        assert b"getProperties" in data
        assert b'version="1.7"' in data

    def test_get_properties_device(self) -> None:
        data = build_get_properties("MyTelescope")
        assert b'device="MyTelescope"' in data

    def test_enable_blob(self) -> None:
        data = build_enable_blob("Camera", "Also")
        assert b"enableBLOB" in data
        assert b"Camera" in data
        assert b"Also" in data

    def test_new_number(self) -> None:
        data = build_new_number("Tel", "COORD", {"RA": 12.5, "DEC": 45.0})
        assert b"newNumberVector" in data
        assert b"12.5" in data

    def test_new_switch(self) -> None:
        data = build_new_switch(
            "Cam", "UPLOAD_MODE", {"UPLOAD_CLIENT": "On"}
        )
        assert b"newSwitchVector" in data
        assert b"UPLOAD_CLIENT" in data


class TestBlobParsing:
    def test_parse_blob_element(self) -> None:
        raw = b"Hello FITS data"
        b64 = base64.b64encode(raw).decode()
        xml = f'<oneBLOB name="CCD1" format=".fits">{b64}</oneBLOB>'
        elem = ET.fromstring(xml)
        name, data, fmt = parse_blob_element(elem)
        assert name == "CCD1"
        assert data == raw
        assert fmt == ".fits"
