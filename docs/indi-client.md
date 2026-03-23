# Async INDI Client

A pure-Python async INDI client that speaks the INDI XML protocol directly over TCP. Built because PyIndi (C++ SWIG wrapper) cannot receive BLOBs (image data) from remote INDI servers.

## Why not PyIndi?

PyIndi wraps the C++ libindi client via SWIG. While it handles device discovery and command sending correctly, its `newBLOB` callback never fires when connected to a remote INDI server over the network. This is a known limitation of the C++ BLOB channel implementation.

We verified via raw TCP (`netcat`) that the INDI server sends BLOB data correctly — 48 MB from a Canon 600D. The problem is purely in PyIndi's network BLOB handling.

Our pure-Python client receives BLOBs reliably by parsing the XML stream directly.

## Architecture

```
src/indi/asynclient/
├── __init__.py          # Exports AsyncINDIClient
├── client.py            # Main client class (async TCP + XML parsing)
└── protocol.py          # XML parser, command builders, data models
```

Plus the adapter that integrates with Nightcrawler:
```
src/indi/async_adapter.py   # AsyncINDIAdapter(INDIClient) — drop-in replacement
```

## How it Works

### Connection & Protocol

The INDI protocol sends XML elements over a plain TCP connection (default port 7624). Unlike normal XML, there is no root element — it's a continuous stream of top-level elements:

```xml
<defNumberVector device="Telescope" name="EQUATORIAL_EOD_COORD" ...>
  <oneNumber name="RA">12.5</oneNumber>
  <oneNumber name="DEC">45.0</oneNumber>
</defNumberVector>
<setNumberVector device="Telescope" name="EQUATORIAL_EOD_COORD" state="Ok" ...>
  <oneNumber name="RA">12.5</oneNumber>
  <oneNumber name="DEC">45.0</oneNumber>
</setNumberVector>
<setBLOBVector device="Camera" name="CCD1" ...>
  <oneBLOB name="CCD1" format=".fits" size="35838720">
    ... base64-encoded FITS data ...
  </oneBLOB>
</setBLOBVector>
```

### XML Stream Parser

`INDIXMLParser` handles the rootless XML stream by:
1. Buffering incoming bytes
2. Finding complete top-level elements by matching opening/closing tags
3. Parsing each complete element with `xml.etree.ElementTree`
4. Handling partial elements at chunk boundaries (1 MB read chunks)
5. Using `errors='replace'` for non-ASCII characters (OnStep sends `°`)

### BLOB Handling

BLOBs (Binary Large Objects) carry image data from the camera. They arrive as base64-encoded text inside `<oneBLOB>` elements. The client:
1. Detects `<setBLOBVector>` elements
2. Extracts the base64 text from `<oneBLOB>`
3. Decodes to raw bytes
4. Signals via `asyncio.Event` — fully async, no threading issues

### Receive Loop

A background `asyncio.Task` continuously reads from the TCP socket:
```python
async def _receive_loop(self):
    while True:
        data = await self._reader.read(1048576)  # 1 MB chunks
        elements = self._parser.feed(data)
        for elem in elements:
            self._handle_element(elem)
```

This runs concurrently with the main application — no blocking, no threads.

## Usage

### Standalone

```python
import asyncio
from src.indi.asynclient import AsyncINDIClient

async def main():
    client = AsyncINDIClient()
    await client.connect("192.168.2.12", 7624)

    # Find camera
    cam = client.find_device_with_property("CCD_EXPOSURE")

    # Enable BLOBs + set upload mode
    await client.enable_blob(cam)
    await client.send_switch(cam, "UPLOAD_MODE", {"UPLOAD_CLIENT": "On"})

    # Take a 5s exposure
    await client.send_number(cam, "CCD_EXPOSURE", {"CCD_EXPOSURE_VALUE": 5.0})

    # Wait for the image
    data = await client.wait_for_blob(timeout=60.0)
    if data:
        with open("capture.fits", "wb") as f:
            f.write(data)
        print(f"Saved {len(data)} bytes")

    await client.disconnect()

asyncio.run(main())
```

### Slewing the Telescope

```python
# Find telescope
tel = client.find_device_with_property("EQUATORIAL_EOD_COORD")

# Slew to Deneb (RA=310.36°, Dec=+45.28°)
ra_hours = 310.36 / 15.0  # INDI uses hours for RA
await client.send_number(tel, "EQUATORIAL_EOD_COORD", {
    "RA": ra_hours,
    "DEC": 45.28,
})

# Poll until slew completes
while True:
    vec = client.get_vector(tel, "EQUATORIAL_EOD_COORD")
    if vec and vec.state == "Ok":
        break
    await asyncio.sleep(0.5)
```

### In Nightcrawler

The `AsyncINDIAdapter` wraps `AsyncINDIClient` to implement Nightcrawler's `INDIClient` interface:

```python
from src.indi.async_adapter import AsyncINDIAdapter

adapter = AsyncINDIAdapter()
await adapter.connect("192.168.2.12", 7624)
await adapter.slew_to(ra=310.36, dec=45.28)  # degrees, converted internally
data = await adapter.capture(CaptureParams(exposure_seconds=5.0))
```

## API Reference

### AsyncINDIClient

| Method | Description |
|--------|-------------|
| `connect(host, port)` | Connect to INDI server, discover devices |
| `disconnect()` | Close connection |
| `enable_blob(device, mode)` | Enable BLOB reception ("Also" or "Only") |
| `send_number(device, vector, members)` | Send number values |
| `send_switch(device, vector, members)` | Send switch values |
| `wait_for_blob(timeout)` | Wait for next BLOB, return bytes |
| `find_device_with_property(name)` | Find device by property name |
| `get_device(name)` | Get device object |
| `get_vector(device, vector)` | Get property vector |
| `get_number(device, vector, member)` | Get numeric value |
| `devices` | Dict of discovered devices |
| `connected` | Connection status |

### Data Models

| Class | Description |
|-------|-------------|
| `INDIDevice` | Device with name and property vectors |
| `INDIVector` | Property vector with state, members, type |
| `INDIProperty` | Single property value (name, label, value) |
| `INDIMessage` | Server message (device, timestamp, text) |

### Command Builders

| Function | Output |
|----------|--------|
| `build_get_properties(device?)` | `<getProperties>` |
| `build_enable_blob(device, mode)` | `<enableBLOB>` |
| `build_new_number(device, name, members)` | `<newNumberVector>` |
| `build_new_switch(device, name, members)` | `<newSwitchVector>` |

## Tested Hardware

| Device | Type | Status |
|--------|------|--------|
| LX200 OnStep | Telescope (German EQ) | Slew, settle, abort ✓ |
| Canon DSLR EOS 600D | Camera (gphoto2) | Exposure, BLOB reception ✓ |

## Comparison

| Feature | PyIndi | AsyncINDI |
|---------|--------|-----------|
| Language | C++ (SWIG) | Pure Python |
| Async | No (threads) | Yes (asyncio) |
| Network BLOBs | Broken | Working ✓ |
| Dependencies | libindi, SWIG | None (stdlib only) |
| Install | Complex (C++ build) | pip install |
| INDI Protocol | Full | Core subset |
