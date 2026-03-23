# Async INDI Client

A pure-Python async INDI client that speaks the INDI XML protocol directly over TCP. Built as a replacement for PyIndi, which cannot receive BLOBs (image data) from remote INDI servers.

## Why Not PyIndi?

PyIndi is a SWIG wrapper around the C++ INDI client library. While it works for device discovery and telescope control, it fails to deliver BLOB data when the INDI server runs on a remote machine (e.g., a Raspberry Pi with StellarMate). The `newBLOB` callback never fires, even though the server sends the data correctly.

We verified this by connecting via raw TCP (`netcat`) — the server sends 48MB BLOB data (Canon 600D full-resolution FITS). PyIndi just doesn't pass it through.

Our async client receives BLOBs reliably over the network.

## Architecture

```
src/indi/asynclient/
├── __init__.py       # Exports AsyncINDIClient
├── client.py         # Main client class (TCP connection, receive loop, event handling)
└── protocol.py       # XML stream parser, command builders, data models
```

### Integration with Nightcrawler

```
src/indi/
├── client.py          # Abstract INDIClient interface (ABC)
├── mock.py            # MockINDIClient for unit tests
├── async_adapter.py   # AsyncINDIAdapter — wraps AsyncINDIClient, implements INDIClient ABC
└── asynclient/        # The pure-Python async INDI client (this module)
```

The `AsyncINDIAdapter` is the bridge between Nightcrawler's `INDIClient` interface and the low-level `AsyncINDIClient`. The UI creates an adapter instance when the user clicks "Connect".

## How It Works

### INDI Protocol Basics

INDI uses XML over TCP (default port 7624). The protocol is a continuous stream of XML elements with no root element:

```xml
<defNumberVector device="Telescope" name="EQUATORIAL_EOD_COORD" state="Ok">
  <oneNumber name="RA">12.5</oneNumber>
  <oneNumber name="DEC">45.0</oneNumber>
</defNumberVector>
<setNumberVector device="Telescope" name="EQUATORIAL_EOD_COORD" state="Busy">
  <oneNumber name="RA">13.0</oneNumber>
</setNumberVector>
<setBLOBVector device="Camera" name="CCD1">
  <oneBLOB name="CCD1" format=".fits" size="35838720">
    ...base64 encoded FITS data...
  </oneBLOB>
</setBLOBVector>
```

### XML Stream Parser

Since INDI has no root element, standard XML parsers don't work directly. `INDIXMLParser` buffers incoming bytes and extracts complete top-level elements by matching opening and closing tags. It handles:

- Complete elements in a single chunk
- Elements split across multiple chunks (partial reads)
- Self-closing elements (`<message ... />`)
- Non-ASCII characters (UTF-8 with `errors='replace'`)

### Connection Lifecycle

```python
import asyncio
from src.indi.asynclient import AsyncINDIClient

async def main():
    client = AsyncINDIClient()

    # 1. Connect — opens TCP, starts background receive loop, requests properties
    await client.connect("192.168.2.12", 7624)

    # 2. Devices are auto-discovered from defXxxVector elements
    print(client.devices)  # {'LX200 OnStep': ..., 'Canon DSLR EOS 600D': ...}

    # 3. Enable BLOBs for a device
    await client.enable_blob("Canon DSLR EOS 600D")

    # 4. Send commands
    await client.send_switch("Canon DSLR EOS 600D", "UPLOAD_MODE",
                             {"UPLOAD_CLIENT": "On"})
    await client.send_number("Canon DSLR EOS 600D", "CCD_EXPOSURE",
                             {"CCD_EXPOSURE_VALUE": 5.0})

    # 5. Wait for BLOB (image data)
    data = await client.wait_for_blob(timeout=60.0)
    if data:
        print(f"Received {len(data)} bytes")
        with open("image.fits", "wb") as f:
            f.write(data)

    # 6. Disconnect
    await client.disconnect()

asyncio.run(main())
```

### Key Methods

| Method | Description |
|--------|-------------|
| `connect(host, port)` | Connect to INDI server, start receive loop, discover devices |
| `disconnect()` | Close connection and stop receive loop |
| `enable_blob(device, mode)` | Enable BLOB reception (`"Also"` or `"Only"`) |
| `send_number(device, vector, members)` | Send numeric values (e.g., coordinates, exposure time) |
| `send_switch(device, vector, members)` | Send switch states (e.g., upload mode) |
| `wait_for_blob(timeout)` | Wait for next BLOB, return raw bytes or `None` on timeout |
| `find_device_with_property(name)` | Find first device that has a given property vector |
| `get_vector(device, vector)` | Get current state of a property vector |
| `get_number(device, vector, member)` | Get a specific numeric value |

### Data Models

```python
@dataclass
class INDIProperty:
    name: str          # e.g., "RA", "CCD_EXPOSURE_VALUE"
    label: str         # Human-readable label
    value: str | float | bytes
    format: str        # For BLOBs: ".fits", ".jpg", etc.

@dataclass
class INDIVector:
    device: str        # e.g., "Canon DSLR EOS 600D"
    name: str          # e.g., "CCD_EXPOSURE"
    state: str         # "Idle", "Ok", "Busy", "Alert"
    members: dict[str, INDIProperty]
    vector_type: str   # "number", "switch", "text", "blob", "light"

@dataclass
class INDIDevice:
    name: str
    vectors: dict[str, INDIVector]
```

## Tested Hardware

- **Telescope:** LX200 OnStep (German Equatorial Mount)
- **Camera:** Canon DSLR EOS 600D via gphoto2
- **Server:** INDI server on Raspberry Pi (StellarMate), remote at 192.168.2.12:7624
- **Result:** 5184x3456 uint16 FITS images (35 MB) received successfully

## Limitations

- Single BLOB at a time (no concurrent captures)
- No INDI server implementation (client only)
- No BLOB compression support (assumes base64 encoding)
- XML parser is simple tag-matching, not a full SAX/DOM parser — works for well-formed INDI streams
