"""Tests for the INDI client interface and mock implementation."""

from __future__ import annotations

import inspect

import pytest
from astropy.io import fits

from src.indi.client import CaptureParams, INDIClient, INDIError, SlewTimeout
from src.indi.mock import MockINDIClient

# ---------------------------------------------------------------------------
# MockINDIClient tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> MockINDIClient:
    """Return a connected MockINDIClient."""
    c = MockINDIClient()
    await c.connect("localhost")
    return c


async def test_connect_disconnect() -> None:
    """Connect and disconnect toggle the connected flag."""
    client = MockINDIClient()
    assert not client.connected
    await client.connect("localhost")
    assert client.connected
    await client.disconnect()
    assert not client.connected


async def test_slew_to_updates_position(client: MockINDIClient) -> None:
    """slew_to updates current_ra and current_dec."""
    await client.slew_to(12.5, -45.0)
    assert client.current_ra == 12.5
    assert client.current_dec == -45.0


async def test_wait_for_settle_returns_true(client: MockINDIClient) -> None:
    """wait_for_settle returns True."""
    result = await client.wait_for_settle()
    assert result is True


async def test_capture_returns_valid_fits(client: MockINDIClient) -> None:
    """capture returns bytes parseable by astropy as a valid FITS file."""
    params = CaptureParams(exposure_seconds=5.0, gain=100, offset=10, binning=2)
    data = await client.capture(params)

    assert isinstance(data, bytes)
    import io

    hdu_list = fits.open(io.BytesIO(data))
    assert hdu_list[0].data.shape == (100, 100)
    assert hdu_list[0].header["EXPTIME"] == 5.0
    assert hdu_list[0].header["GAIN"] == 100
    hdu_list.close()


async def test_slew_without_connect_raises() -> None:
    """Calling slew_to on a disconnected client raises INDIError."""
    client = MockINDIClient()
    with pytest.raises(INDIError, match="not connected"):
        await client.slew_to(0.0, 0.0)


async def test_get_devices_returns_dict(client: MockINDIClient) -> None:
    """get_devices returns a dict with telescope and camera keys."""
    devices = await client.get_devices()
    assert isinstance(devices, dict)
    assert "telescope" in devices
    assert "camera" in devices


async def test_configurable_slew_failure() -> None:
    """First N slew calls raise SlewTimeout, then succeed."""
    client = MockINDIClient(fail_slew_count=2)
    await client.connect("localhost")

    with pytest.raises(SlewTimeout):
        await client.slew_to(1.0, 1.0)
    with pytest.raises(SlewTimeout):
        await client.slew_to(1.0, 1.0)

    # Third attempt succeeds
    await client.slew_to(1.0, 1.0)
    assert client.current_ra == 1.0


async def test_reconnect_returns_true(client: MockINDIClient) -> None:
    """reconnect returns True."""
    result = await client.reconnect()
    assert result is True


# ---------------------------------------------------------------------------
# INDIClient interface tests
# ---------------------------------------------------------------------------


def test_indi_client_has_required_methods() -> None:
    """INDIClient defines all required abstract methods."""
    expected = {
        "connected",
        "connect",
        "disconnect",
        "slew_to",
        "wait_for_settle",
        "capture",
        "get_devices",
        "abort",
        "reconnect",
    }
    members = {name for name, _ in inspect.getmembers(INDIClient) if not name.startswith("_")}
    assert expected.issubset(members)
