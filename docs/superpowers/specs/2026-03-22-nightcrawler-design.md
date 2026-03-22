# Nightcrawler — Design Specification

## Overview

Software for planning and executing imaging sequences with a remote-controlled telescope (INDI/EKOS). The user draws a path on a star map, the telescope slews point-by-point along the path capturing images at each position. The captured frames are then assembled into a video.

**Two separate applications:**

1. **Planner & Capture App** — runs on the telescope control machine (RPi/StellarMate or stronger)
2. **Rendering App** — runs on a powerful desktop/workstation

Both applications use **NiceGUI** as the web framework. Single-user, no authentication.

---

## 1. Planner & Capture App

### 1.1 Star Map

**Engine:** Stellarium Web Engine (C compiled to WebAssembly via Emscripten, rendered with WebGL).

- Embedded in NiceGUI via Custom Element or iframe
- Uses the engine's own catalogs (Gaia/Hipparcos via HiPS tiles), not KStars catalogs
- Stars displayed up to magnitude 10 for offline use; deeper catalogs available with internet
- **Offline-capable:** `skydata/` directory hosted locally on the same server
- **License:** AGPL-3.0 — installed separately via post-installation script, not bundled

**Integration with NiceGUI:**
- The WASM engine renders into an HTML `<canvas>` element
- Python backend communicates with the engine via NiceGUI's JavaScript interop (`run_javascript()`, event handlers)
- Coordinate display overlay showing current cursor position (RA/Dec)

**Required JS bridge functions:**
- `screenToWorld(x, y)` → `{ra, dec}` — convert canvas click to celestial coordinates
- `worldToScreen(ra, dec)` → `{x, y}` — convert celestial coordinates to canvas position (for overlay positioning)
- `getFieldOfView()` → current FOV in degrees
- `setObserver(lat, lon, time)` — set observer location and time
- `lookAt(ra, dec, fov, duration)` — animate view to position
- Mouse event registration on canvas (click, drag, scroll)

### 1.2 Path Editor

**Rendering approach:** The Stellarium Web Engine's GeoJSON module includes a custom `Path` geometry type that supports SVG cubic Bézier curves (`M` and `C` commands) in celestial coordinates. This is used to **render** the spline on the star map.

**Editing approach:** The interactive editing (dragging control points, handles) is handled by a **custom JavaScript overlay layer** on top of the engine's canvas. This layer:
- Uses the engine's coordinate projection API (`core.screenToWorld`, `core.worldToScreen`) to convert between pixel and celestial coordinates
- Renders control point handles and interaction affordances as HTML/SVG elements positioned over the canvas
- Updates the GeoJSON Path data in the engine whenever the user modifies the spline
- Falls back to densely-sampled `LineString` segments if the `Path` type is unavailable in the engine build

**Drawing modes:**
- **Point-by-point:** Click on the star map to place control points, connected by smooth Bézier splines
- **Freehand:** Draw a line, simplified via Ramer-Douglas-Peucker algorithm, then fit a cubic Bézier spline through the resulting points

**Editing:**
- Drag control points to reshape the path
- Drag Bézier handles to adjust curvature
- Add/remove control points
- Split path at a point
- Undo/Redo

**Capture points:**
- Distributed along the spline at a configurable spacing (in degrees)
- Displayed as small markers (blue dots) on the path
- Spacing is adjustable — points update in real-time as spacing changes
- Even distribution for v1; variable speed (slow at objects of interest, fast between) as a future enhancement

### 1.3 Capture Settings (Global per Path)

| Setting | Type | Description |
|---------|------|-------------|
| Point spacing | float (degrees) | Distance between capture points along the spline |
| Exposure time | float (seconds) | Duration of each exposure |
| Exposures per point | integer | Number of frames captured at each point |
| Gain | integer | Camera gain (sensor-dependent) |
| Offset | integer | Camera offset/brightness |
| Binning | integer (1-4) | Pixel binning mode (1×1, 2×2, etc.) |

These settings apply globally to the entire path.

**Out of scope for v1:** Filter wheel support, per-point settings override.

### 1.4 Telescope Control

**Direct INDI connection (primary mode):**
- Configurable INDI server host and port (default: `localhost:7624`)
- Uses PyINDI or direct INDI XML protocol over TCP
- Telescope: Goto commands to slew to each capture point's RA/Dec
- Camera: Trigger exposure, receive FITS data
- Connection status displayed in the UI

**EKOS sequence export (alternative mode):**
- Generate an EKOS-compatible sequence from the planned path
- Exact export format to be determined during implementation (`.esq` XML format is undocumented and version-dependent; may use EKOS D-Bus API or Scheduler files instead)
- User imports the sequence into KStars/EKOS for execution
- No live connection needed — useful when the planner runs on a different machine

### 1.5 Capture Process

**Two modes:**

**Fully automatic:**
- Press start, the app works through all capture points sequentially
- At each point: slew telescope → wait for settle → capture exposure(s) → write FITS → next point
- Runs until all points are captured or manually stopped

**Monitored:**
- Live progress view showing current point, exposure progress, estimated time remaining
- **Pause/Resume:** Interrupt capture at any time, continue from where it left off
- **Cancel:** Stop the sequence entirely
- Progress bar and per-point status tracking

**No automatic weather monitoring.** The user decides manually whether conditions are acceptable.

### 1.5.1 Error Handling & Timeouts

| Operation | Timeout | On Failure |
|-----------|---------|------------|
| Slew to target | 120 seconds | Retry once, then pause sequence and alert user |
| Settle (mount reports tracking) | 30 seconds | Retry slew+settle once, then pause and alert |
| Exposure | exposure_time + 30 seconds | Retry once, then pause and alert |
| FITS write | 30 seconds | Pause and alert (possible disk full) |
| INDI connection loss | 10 seconds reconnect attempt | Pause sequence, attempt reconnect every 10s for 60s, then alert |

**General policy:** On any unrecoverable error, the sequence pauses (not aborts) so the user can assess the situation. The mount is commanded to stop slewing on application crash or disconnect (safety abort). The user can then resume, skip the current point, or cancel.

**Settle definition:** The mount is considered settled when the INDI `TELESCOPE_TRACK_STATE` property reports `TRACK_ON` after a slew completes. If plate-solving is available, an optional plate-solve verify step can be enabled (future enhancement).

### 1.6 UI Layout — Hybrid (Karte + Bottom Panel)

**Toolbar** (top of star map):
- Drawing tools: Draw, Move, Add Point, Split
- Edit: Undo, Redo
- File: Save, Load
- Action: Start Capture (switches to capture mode)

**Star Map** (main area):
- Stellarium Web Engine canvas, fills most of the viewport
- Spline path overlay: orange control points with Bézier handles, blue capture point markers
- Coordinate display (RA/Dec at cursor position)
- Legend showing point types

**Bottom Panel — collapsed** (summary bar):
- Path info: number of control points, number of capture points
- Current settings: exposure, spacing
- Estimated total duration
- Toggle to expand

**Bottom Panel — expanded:**
- Path settings: spacing, exposure time, exposures per point (editable)
- Capture point list: table with index, RA, Dec, status
- INDI connection: host, port, connection status

**Capture mode:**
- Toolbar changes: shows "CAPTURE RUNNING" indicator, Pause and Cancel buttons
- Bottom panel shows: current point / total, current exposure progress, estimated time remaining, progress bar
- Star map highlights the current capture point

### 1.7 Project & Manifest Lifecycle

The **project file** and the **manifest** share the same JSON schema. They represent different lifecycle stages of the same document:

1. **Planning stage** — saved as `project.json`: contains `path`, `capture_settings`, `indi` config. The `capture_points` array is present but all points have `"status": "pending"` and empty `files` arrays.
2. **During capture** — the project file is updated in-place: `capture_points` get status updates (`"capturing"`, `"captured"`) and filenames as images are written.
3. **After capture** — the file is copied into the output directory as `manifest.json`. This is the self-describing handoff to the Rendering App.

A manifest can be loaded back as a project file to review or extend the sequence (e.g., re-capture failed points).

---

## 2. Data Interface — FITS + JSON Manifest

The capture output is a **directory** containing FITS image files and a JSON manifest. This directory is the handoff between the Planner/Capture App and the Rendering App.

### 2.1 FITS Files

Captured by EKOS/INDI with standard FITS headers:
- `OBJCTRA`, `OBJCTDEC` — target coordinates
- `EXPTIME` — exposure duration
- `DATE-OBS` — timestamp (ISO 8601)
- `INSTRUME` — camera
- `TELESCOP` — telescope
- `FOCUSPOS`, `FOCUSTEM` — focus position and temperature

Naming convention: `seq_NNNN_MMM.fits` where NNNN is the capture point index (1-based) and MMM is the exposure number at that point (1-based). The manifest's `capture_points[].index` field is 0-based for programmatic access; the filename index is always `index + 1`.

### 2.2 JSON Manifest (`manifest.json`)

```json
{
  "version": "1.0",
  "created": "2026-03-22T02:15:00Z",
  "project": "M31-to-M33-sweep",

  "path": {
    "control_points": [
      {
        "ra": 10.684, "dec": 41.269, "label": "M31",
        "handle_out": {"ra": 11.5, "dec": 41.0}
      },
      {
        "ra": 14.053, "dec": 38.683,
        "handle_in": {"ra": 13.0, "dec": 39.5},
        "handle_out": {"ra": 15.5, "dec": 37.0}
      },
      {
        "ra": 23.462, "dec": 30.660, "label": "M33",
        "handle_in": {"ra": 21.0, "dec": 32.0}
      }
    ],
    "spline_type": "cubic_bezier",
    "coordinate_frame": "J2000"
  },

  "capture_settings": {
    "point_spacing_deg": 0.5,
    "exposure_seconds": 30,
    "exposures_per_point": 1,
    "gain": 120,
    "offset": 10,
    "binning": 1
  },

  "capture_points": [
    {
      "index": 0,
      "ra": 10.684,
      "dec": 41.269,
      "files": ["seq_0001_001.fits"],
      "status": "captured",
      "captured_at": "2026-03-22T02:16:30Z"
    }
  ],

  "indi": {
    "telescope": "EQMod Mount",
    "camera": "ZWO ASI294MC Pro",
    "host": "localhost",
    "port": 7624
  }
}
```

**Key properties:**
- `path` — spline definition with control points (RA/Dec in degrees, J2000/ICRS frame), reproducible
- `capture_settings` — global parameters for the entire path
- `capture_points` — each point with coordinates, associated FITS filenames, capture status and timestamp
- `indi` — equipment info for documentation

The Rendering App reads the manifest to determine frame order and locate FITS files. The manifest is self-describing — minimal manual input needed on the rendering side.

---

## 3. Rendering App

Separate application, runs on a powerful machine. Takes the capture output directory and produces a video.

### 3.1 Interfaces

**CLI:** Headless rendering via command line.
```
render --input /path/to/capture/dir --output video.mp4 --fps 24
       [--stretch auto|manual] [--black 0.1] [--white 0.99] [--midtone 0.5]
       [--scale 1.0] [--crf 23] [--frame-select 1]
```

**Web UI:** NiceGUI-based interface for:
- Importing a capture directory
- Previewing individual frames
- Adjusting stretch/tonemap parameters
- Starting the render and monitoring progress

### 3.2 Pipeline (v1)

1. **Read manifest** — determine frame order from `capture_points`
2. **FITS → PNG/TIFF conversion** — convert linear FITS data to visible images using `astropy.io.fits` and `astropy.visualization`
   - **Auto-stretch** as default: `ZScaleInterval` for black/white point detection + `AsinhStretch` for midtone transfer (matches common astro-imaging practice)
   - **Manual adjustment:** black point, white point, midtone stretch factor — configurable in the UI with live preview on the current frame
   - **Debayering:** Auto-detect from FITS `BAYERPAT` header; pass through as-is for mono cameras
3. **Assemble video** — ffmpeg, default 24fps, H.264 codec in mp4 container, configurable quality (CRF 18-28). Resolution: native sensor resolution by default, optional downscale.

**No stacking in v1.** When multiple exposures per point exist, the first frame is used by default. A global setting allows selecting which exposure number to use (e.g., "always use frame 1 of N"). Per-point selection is a future enhancement. Stacking is out of scope, potentially delegated to external tools like Siril.

### 3.3 Rendering UI

- Import: select capture directory, manifest is read automatically
- Frame browser: scroll through frames, see coordinates and metadata
- Stretch controls: auto-stretch toggle, manual black/white/midtone sliders, live preview on current frame
- Render: select output format, fps, start render, progress bar

---

## 4. Technology Stack

| Component | Technology |
|-----------|-----------|
| Web framework (both apps) | NiceGUI (Python) on FastAPI/uvicorn |
| Star map engine | Stellarium Web Engine (C/WASM/WebGL) |
| Spline overlay | Stellarium Web Engine GeoJSON Path (cubic Bézier) |
| Telescope control | PyINDI / INDI XML protocol |
| EKOS export | .esq sequence file generation |
| Image format | FITS (capture), PNG/TIFF (intermediate) |
| Video encoding | ffmpeg |
| Data exchange | JSON manifest + FITS files in a directory |
| Offline star data | Stellarium HiPS tiles (local `skydata/` directory) |

---

## 5. Future Enhancements (Out of Scope for v1)

- **Variable speed** along the path (slower at interesting objects, faster between)
- **Stacking** multiple exposures per point (potentially via Siril integration)
- **Per-point frame selection** in the rendering UI
- **Filter wheel support** — filter selection per sequence or per point
- **Deeper catalogs** — full Gaia catalog when running on stronger hardware
- **Automatic weather monitoring** — pause on cloud cover, tracking loss
- **Plate-solve verify** — confirm pointing accuracy after slew via plate solving
- **KStars catalog import** — convert KStars SQLite catalogs to HiPS tiles or GeoJSON overlays
- **Disk space estimation** — warn before starting capture if storage may be insufficient
