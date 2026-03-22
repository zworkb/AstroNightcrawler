# Planner & Capture App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser-based app for planning telescope imaging sequences on an interactive star map and executing them via INDI protocol.

**Architecture:** NiceGUI Python server with Stellarium Web Engine (WASM/WebGL) embedded for the star map. A JavaScript SVG overlay layer handles spline path editing. Python backend manages project state, INDI communication, and the capture sequence. All data persists as JSON project files.

**Tech Stack:** Python 3.11+, NiceGUI, Stellarium Web Engine (WASM), PyINDI-client, pydantic (data models), astropy (FITS), pytest, ruff, mypy

**Spec:** `docs/superpowers/specs/2026-03-22-sequence-planner-design.md`

**Coding Standards:** `cleancode.md` + `cleancode-python.md` — all code must comply. Key rules:
- Type hints on all functions, modern syntax (`list[str]`, `str | None`)
- Functions ≤30 lines, ≤3 parameters, nesting ≤3 levels, files ≤500 lines
- Google-style docstrings on public functions
- NiceGUI patterns: state in dataclasses, component classes with `render()`, `@ui.refreshable`
- Tooling: ruff + mypy + pytest before every commit

---

## File Structure

```
sequence-planner/
├── .gitignore
├── pyproject.toml                    # Project config, dependencies, ruff/mypy config
├── src/
│   ├── __init__.py
│   ├── main.py                       # NiceGUI app entry point
│   ├── app_state.py                  # Application state dataclass + actions
│   ├── models/
│   │   ├── __init__.py
│   │   ├── project.py                # Pydantic: Project, Path, CaptureSettings, CapturePoint, INDIConfig
│   │   ├── spline.py                 # Bézier math: evaluate, arc length, sample points
│   │   ├── freehand.py               # Ramer-Douglas-Peucker + Bézier fitting
│   │   └── undo.py                   # Undo/Redo command stack
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── layout.py                 # Main page layout: toolbar, map, bottom panel
│   │   ├── toolbar.py                # Toolbar component
│   │   ├── bottom_panel.py           # Bottom panel component
│   │   └── capture_view.py           # Capture mode UI: progress, pause/resume
│   ├── starmap/
│   │   ├── __init__.py
│   │   ├── engine.py                 # NiceGUI element wrapping Stellarium Web Engine
│   │   ├── bridge.js                 # JS: engine init, coordinate conversion, events
│   │   └── path_overlay.js           # JS: SVG overlay for spline editing
│   ├── indi/
│   │   ├── __init__.py
│   │   ├── client.py                 # INDI client interface + exceptions
│   │   └── mock.py                   # Mock INDI client for testing
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── controller.py             # Capture state machine: run, pause, resume, cancel, skip
│   │   └── fits_writer.py            # FITS file writing
│   └── export/
│       ├── __init__.py
│       └── ekos.py                   # EKOS sequence export
├── static/
│   └── stellarium/                   # WASM + JS (installed via script, gitignored)
├── skydata/                          # HiPS tiles (installed via script, gitignored)
├── scripts/
│   └── install_stellarium.sh         # Download Stellarium Web Engine + skydata
├── tests/
│   ├── __init__.py
│   ├── test_models.py
│   ├── test_spline.py
│   ├── test_freehand.py
│   ├── test_undo.py
│   ├── test_indi_client.py
│   ├── test_fits_writer.py
│   ├── test_capture_controller.py
│   ├── test_ekos_export.py
│   └── test_integration.py
└── docs/
```

**Known limitations (documented in code):**
- Spline math uses flat Euclidean coordinates (no `cos(dec)` correction). Accurate for paths <15°, inaccurate for large angular distances.
- NiceGUI binds to `0.0.0.0:8080` by default — intentional for network access (tablet in observatory). Configurable via `--host`/`--port` args.

---

## Task 1: Project Setup & Data Models

**Files:**
- Create: `pyproject.toml`, `src/__init__.py`, `src/models/__init__.py`, `src/models/project.py`
- Create: `tests/__init__.py`, `tests/test_models.py`

- [ ] **Step 1: Create pyproject.toml with ruff/mypy config**

```toml
[project]
name = "sequence-planner"
version = "0.1.0"
description = "Telescope imaging sequence planner and capture controller"
requires-python = ">=3.11"
dependencies = [
    "nicegui>=2.0",
    "pydantic>=2.0",
    "astropy>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.4",
    "mypy>=1.10",
]
indi = [
    "pyindi-client>=2.0",
]

[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
```

- [ ] **Step 2: Create empty `__init__.py` files**

`src/__init__.py`, `src/models/__init__.py`, `tests/__init__.py`

- [ ] **Step 3: Write failing tests for data models**

```python
# tests/test_models.py
"""Tests for project data models."""

from pathlib import Path

import pytest
from src.models.project import (
    Coordinate, ControlPoint, SplinePath,
    CaptureSettings, CapturePoint, INDIConfig, Project,
)


class TestCoordinate:
    def test_valid_coordinate(self) -> None:
        c = Coordinate(ra=10.684, dec=41.269)
        assert c.ra == 10.684
        assert c.dec == 41.269

    def test_ra_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            Coordinate(ra=400.0, dec=0.0)

    def test_dec_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError):
            Coordinate(ra=0.0, dec=100.0)


class TestControlPoint:
    def test_with_handles(self) -> None:
        cp = ControlPoint(
            ra=10.684, dec=41.269, label="M31",
            handle_out=Coordinate(ra=11.5, dec=41.0),
        )
        assert cp.label == "M31"
        assert cp.handle_out is not None
        assert cp.handle_out.ra == 11.5

    def test_without_handles(self) -> None:
        cp = ControlPoint(ra=14.053, dec=38.683)
        assert cp.handle_in is None
        assert cp.handle_out is None


class TestSplinePath:
    def test_needs_at_least_two_points(self) -> None:
        with pytest.raises(ValueError):
            SplinePath(control_points=[ControlPoint(ra=10.0, dec=40.0)])

    def test_defaults(self) -> None:
        path = SplinePath(control_points=[
            ControlPoint(ra=10.0, dec=40.0),
            ControlPoint(ra=20.0, dec=30.0),
        ])
        assert path.spline_type == "cubic_bezier"
        assert path.coordinate_frame == "J2000"


class TestCaptureSettings:
    def test_defaults(self) -> None:
        cs = CaptureSettings()
        assert cs.point_spacing_deg == 0.5
        assert cs.exposure_seconds == 30.0
        assert cs.binning == 1

    def test_invalid_binning_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(binning=5)

    def test_invalid_exposure_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(exposure_seconds=-1.0)

    def test_invalid_spacing_raises(self) -> None:
        with pytest.raises(ValueError):
            CaptureSettings(point_spacing_deg=0.0)


class TestCapturePoint:
    def test_pending_by_default(self) -> None:
        cp = CapturePoint(index=0, ra=10.684, dec=41.269)
        assert cp.status == "pending"
        assert cp.files == []
        assert cp.captured_at is None

    def test_filename_index_zero(self) -> None:
        cp = CapturePoint(index=0, ra=10.0, dec=40.0)
        assert cp.filename(exposure=1) == "seq_0001_001.fits"

    def test_filename_multi_exposure(self) -> None:
        cp = CapturePoint(index=2, ra=10.0, dec=40.0)
        assert cp.filename(exposure=3) == "seq_0003_003.fits"

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError):
            CapturePoint(index=0, ra=0.0, dec=0.0, status="invalid")


class TestProject:
    def test_roundtrip_json(self, tmp_path: Path) -> None:
        project = Project(
            project="test-sweep",
            path=SplinePath(control_points=[
                ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                ControlPoint(ra=20.0, dec=30.0, handle_in=Coordinate(ra=19.0, dec=31.0)),
            ]),
            capture_settings=CaptureSettings(exposure_seconds=60.0, gain=120),
            indi=INDIConfig(telescope="EQMod Mount", camera="ZWO ASI294MC Pro"),
        )
        filepath = tmp_path / "project.json"
        filepath.write_text(project.model_dump_json(indent=2))
        loaded = Project.model_validate_json(filepath.read_text())
        assert loaded.version == "1.0"
        assert loaded.created != ""
        assert loaded.project == "test-sweep"
        assert loaded.capture_settings.exposure_seconds == 60.0
        assert loaded.path.control_points[0].handle_out is not None
```

- [ ] **Step 4: Run tests — expect failure**

Run: `python -m pytest tests/test_models.py -v`

- [ ] **Step 5: Implement data models**

`src/models/project.py` — Pydantic models: `Coordinate`, `ControlPoint`, `SplinePath`, `CaptureSettings`, `CapturePoint`, `INDIConfig`, `Project`. All with full type hints and validation. `CapturePoint.filename()` generates `seq_NNNN_MMM.fits` (1-based from 0-based index). `CapturePoint.captured_at: str | None` set on capture completion. `Project` includes `version: str = "1.0"` and `created: str` (ISO 8601 UTC, default to now) for manifest compatibility.

- [ ] **Step 6: Run tests — expect pass**

Run: `python -m pytest tests/test_models.py -v`

- [ ] **Step 7: Run ruff + mypy**

Run: `ruff check src/models/ && mypy src/models/`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/__init__.py src/models/ tests/__init__.py tests/test_models.py
git commit -m "feat: add project data models with pydantic validation"
```

---

## Task 2: Spline Math

**Files:**
- Create: `src/models/spline.py`, `tests/test_spline.py`

- [ ] **Step 1: Write failing tests for spline math**

Test `bezier_point()` (t=0 → start, t=1 → end, t=0.5 on straight line → midpoint), `bezier_segment_length()` (straight line = exact, curved > straight), `sample_points_along_spline()` (correct count for given spacing, first/last match endpoints, finer spacing = more points, multi-segment paths).

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement spline math**

`src/models/spline.py`:
- `bezier_point(p0, p1, p2, p3, t)` → `tuple[float, float]` — evaluate cubic Bézier at t
- `bezier_segment_length(p0, p1, p2, p3)` → `float` — approximate arc length via 100 subdivisions
- `sample_points_along_spline(path, spacing_deg)` → `list[tuple[float, float]]` — walk the polyline, pick points at spacing intervals. Document Euclidean limitation in docstring.

Helper `_segment_handles(cp_start, cp_end)` extracts the 4 Bézier points from two ControlPoints, defaulting to 1/3 and 2/3 interpolation when handles are absent.

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add cubic Bézier spline math with point sampling"
```

---

## Task 3: Freehand Drawing — RDP Simplification + Bézier Fitting

**Files:**
- Create: `src/models/freehand.py`, `tests/test_freehand.py`

- [ ] **Step 1: Write failing tests**

Test `rdp_simplify()` (straight line unchanged, zigzag reduced, epsilon controls point count), `fit_bezier_to_points()` (produces ControlPoints with handles, 2 input points → 2 control points, result spline passes near input points).

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement RDP and Bézier fitting**

`src/models/freehand.py`:
- `rdp_simplify(points, epsilon)` → `list[tuple[float, float]]` — Ramer-Douglas-Peucker algorithm. Recursive, removes points closer than epsilon to the line between neighbors.
- `fit_bezier_to_points(points)` → `list[ControlPoint]` — Takes simplified points, creates ControlPoints with Catmull-Rom-style handles (tangent at each point based on neighbors, scaled to 1/3 segment length).

Both functions ≤30 lines. Pure math, no side effects.

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add freehand drawing with RDP simplification and Bézier fitting"
```

---

## Task 4: Undo/Redo Command Stack

**Files:**
- Create: `src/models/undo.py`, `tests/test_undo.py`

- [ ] **Step 1: Write failing tests**

Test: push command and undo restores previous state, redo re-applies, undo on empty stack is no-op, new command after undo clears redo stack, max stack size respected.

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement undo/redo**

`src/models/undo.py`:
- `UndoStack` class with `push(before_state, after_state)`, `undo()` → state or None, `redo()` → state or None, `can_undo`/`can_redo` properties.
- State is a JSON string (serialized SplinePath). Simple memento pattern — stores snapshots, not commands.
- Max stack size = 50 (configurable).

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add undo/redo stack for path editing"
```

---

## Task 5: INDI Client Interface + Mock

**Files:**
- Create: `src/indi/__init__.py`, `src/indi/client.py`, `src/indi/mock.py`
- Create: `tests/test_indi_client.py`

- [ ] **Step 1: Write failing tests**

Test MockINDIClient: connect/disconnect, slew_to updates position, wait_for_settle returns True, capture returns valid FITS bytes (parseable by astropy), slew without connect raises, get_devices returns dict, configurable failure count (fail N times then succeed), reconnect logic.

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement INDI client**

`src/indi/client.py`:
- Exceptions: `INDIError`, `SlewTimeout(INDIError)`, `SettleTimeout(INDIError)`, `CaptureTimeout(INDIError)`, `ConnectionLostError(INDIError)`
- `INDIClient` abstract interface with methods: `connect`, `disconnect`, `slew_to`, `wait_for_settle`, `capture`, `get_devices`, `abort`, `reconnect`
- `capture` uses a dataclass `CaptureParams(exposure_seconds, gain, offset, binning)` to stay within ≤3 parameters

`src/indi/mock.py`:
- `MockINDIClient(INDIClient)` — simulates all operations with configurable delays
- `fail_count` parameter: slew/capture fails N times before succeeding (for retry testing)
- `capture()` returns valid FITS using astropy (with fallback to manual card construction)
- `reconnect()` simulates reconnection with configurable success/failure

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add INDI client interface and mock with retry support"
```

---

## Task 6: FITS Writer

**Files:**
- Create: `src/capture/__init__.py`, `src/capture/fits_writer.py`
- Create: `tests/test_fits_writer.py`

- [ ] **Step 1: Write failing tests**

Test: creates output directory, writes file with correct name, multi-exposure naming, updates point.files list, written file is valid FITS (astropy can open it).

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement FITS writer**

`src/capture/fits_writer.py`:
- `FITSWriter(output_dir)` — creates dir on init
- `write(point, exposure_num, data)` → `Path` — writes bytes, appends filename to `point.files`

Simple, ≤30 lines total.

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add FITS writer with naming convention"
```

---

## Task 7: Capture Controller (State Machine)

**Files:**
- Create: `src/capture/controller.py`, `tests/test_capture_controller.py`

- [ ] **Step 1: Write failing tests**

Test: initial state is IDLE, full sequence completes all points, pause/resume works, cancel stops sequence, multi-exposure captures correct count, resume skips already-captured points, `skip_point()` advances to next, slew retry on failure (mock fails once then succeeds), connection loss triggers pause + reconnect attempt, manifest is written on completion, estimated time remaining is calculated.

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement capture controller**

`src/capture/controller.py`:
- `CaptureState` enum: IDLE, RUNNING, PAUSED, COMPLETED, CANCELLED, ERROR
- `CaptureController` with:
  - `run()` — async main loop: iterate points, slew+settle+capture, handle errors per spec timeout table
  - `pause()` / `resume()` / `cancel()` / `skip_point()` — state transitions via asyncio.Event
  - `estimated_remaining_seconds` property — `(remaining_points) * (slew_est + settle_est + exposure * count)`
  - `_slew_with_retry()` — retry once on SlewTimeout/SettleTimeout
  - `_capture_exposures()` — capture loop with FITS write timeout (30s, pause and alert on disk full)
  - `_handle_connection_loss()` — call `indi.reconnect()` every 10s for 60s, pause if fails
  - `_save_manifest()` — write project JSON to output dir
  - Safety: `atexit` handler + signal handler calls `indi.abort()` on unexpected termination

Keep methods focused: `run()` delegates to `_capture_point()`, which delegates to `_slew_with_retry()` and `_capture_exposures()`. Each ≤30 lines.

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Run ruff + mypy, commit**

```bash
git commit -m "feat: add capture controller with pause/resume, retry, and safety abort"
```

---

## Task 8: NiceGUI App Shell & Layout

**Files:**
- Create: `src/main.py`, `src/app_state.py`
- Create: `src/ui/__init__.py`, `src/ui/layout.py`, `src/ui/toolbar.py`, `src/ui/bottom_panel.py`

- [ ] **Step 1: Create app state dataclass**

`src/app_state.py`:
- `AppState` dataclass holding: `project: Project`, `indi_client: INDIClient`, `undo_stack: UndoStack`, `output_dir: Path`
- Methods: `update_capture_points()`, `save_project(path)`, `load_project(path)`, `start_capture()` (preserves existing captured points when resuming)
- Uses dependency injection for INDI client (mock by default, real when available)

- [ ] **Step 2: Create main entry point**

`src/main.py`: NiceGUI app with `@ui.page("/")`, calls `create_layout()`. Host/port configurable via argparse.

- [ ] **Step 3: Create toolbar component**

`src/ui/toolbar.py`:
- `ToolbarComponent` class following NiceGUI component pattern
- Constructor receives `AppState` and callbacks
- `render()` creates tool buttons, wires click handlers
- Drawing mode buttons: Draw, Freehand, Move, Add Point, Remove Point, Split
- Edit: Undo, Redo (disabled state based on `undo_stack.can_undo`/`can_redo`)
- File: Save, Load (with NiceGUI file dialogs)
- Action: Start Capture

- [ ] **Step 4: Create bottom panel component**

`src/ui/bottom_panel.py`:
- `BottomPanelComponent` class with `@ui.refreshable` sections
- Collapsed: dynamic summary ("Path: N ctrl / M capture points | 30s × 1 | 0.5° | ~4 min")
- Expanded: editable settings bound to `AppState.project.capture_settings`, capture point table, INDI connection (host/port/status/connect button)
- Estimated total duration computed from capture points × (slew + settle + exposure × count)

- [ ] **Step 5: Create layout**

`src/ui/layout.py`:
- `create_layout()` assembles toolbar, star map container, bottom panel
- CSS for dark theme, full-height layout

- [ ] **Step 6: Verify app starts without errors**

Run: `timeout 5 python -m src.main || true`

- [ ] **Step 7: Commit**

```bash
git commit -m "feat: add NiceGUI app shell with toolbar and bottom panel"
```

---

## Task 9: Stellarium Web Engine Integration

**Files:**
- Create: `scripts/install_stellarium.sh`
- Create: `src/starmap/__init__.py`, `src/starmap/engine.py`, `src/starmap/bridge.js`

- [ ] **Step 1: Create install script**

`scripts/install_stellarium.sh`: Clone repo, build WASM via Emscripten (`make js-es6`), copy artifacts to `static/stellarium/`, prepare `skydata/` directory. Check for `emcc` first.

- [ ] **Step 2: Create JS bridge**

`src/starmap/bridge.js`:
- `initEngine(containerId, wasmUrl, skydataUrl)` — create canvas, load WASM, setup resize observer
- `screenToWorld(x, y)` → `{ra, dec}` via engine API
- `worldToScreen(ra, dec)` → `{x, y}` via engine API
- `getFieldOfView()`, `setObserver(lat, lon, utcTime)`, `lookAt(ra, dec, fov, duration)`
- Mouse events: click and mousemove emit custom DOM events with RA/Dec data
- Coordinate display: updates a DOM element with current cursor RA/Dec on mousemove
- Exported as `window.stelBridge`

- [ ] **Step 3: Create NiceGUI star map element**

`src/starmap/engine.py`:
- Uses `ui.html()` to create a container div with unique ID
- Loads bridge.js and path_overlay.js via `ui.add_body_html()`
- `initialize()` calls `stelBridge.initEngine()` via `ui.run_javascript()`
- `look_at()`, `set_observer()` delegate to JS bridge
- Click/mousemove events forwarded to Python via `ui.on()` with custom JavaScript event listeners using `getElement()` pattern (correct NiceGUI interop, not the incorrect `window.emitNiceGUIEvent`)

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: add Stellarium Web Engine integration with JS bridge"
```

---

## Task 10: Path Overlay (JavaScript SVG)

**Files:**
- Create: `src/starmap/path_overlay.js`

- [ ] **Step 1: Implement PathOverlay class**

`src/starmap/path_overlay.js`:
- `PathOverlay(containerId)` — creates SVG element over the engine canvas
- Modes: `draw`, `freehand`, `move`, `add_point`, `remove_point`, `split`
- `update(controlPoints, capturePoints)` — re-renders all overlay elements
- `_renderPath()` — SVG `<path>` with cubic Bézier commands, using `stelBridge.worldToScreen()` for coordinate conversion
- `_renderHandles()` — handle lines and drag dots for each control point
- `_renderCapturePoints()` — blue dots along the path
- `_renderControlPointDots()` — orange draggable dots
- `highlightPoint(index)` — visually distinguish the active capture point (for capture mode)
- `_setupInteraction()`:
  - `draw` mode: click adds control point, emits `path_add_point` event
  - `freehand` mode: mousedown starts collecting points, mouseup emits `path_freehand_complete` with the point list
  - `move` mode: drag control points/handles, emit `path_move_point` / `path_point_moved`
  - `add_point` mode: click on path segment adds a new control point
  - `remove_point` mode: click on a control point removes it (path reconnects through neighbors)
  - `split` mode: click on path splits at that position
- All events dispatched as CustomEvents on the container element (consumed by NiceGUI via `ui.on()`)
- Exposed as `window.pathOverlayBridge` with `init`, `setMode`, `update`, `highlightPoint`

- [ ] **Step 2: Register overlay in engine.py**

Add path_overlay.js loading in `StarMap.__init__()`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat: add SVG path overlay for spline editing with all drawing modes"
```

---

## Task 11: Capture View UI

**Files:**
- Create: `src/ui/capture_view.py`

- [ ] **Step 1: Implement capture mode UI**

`src/ui/capture_view.py`:
- `CaptureViewComponent` class with `render()` and `@ui.refreshable` progress section
- Shows: status label (RUNNING/PAUSED/ERROR), point N/M, exposure progress, estimated time remaining (from `controller.estimated_remaining_seconds`), progress bar
- Pause button toggles to Resume when paused
- Skip button (advances past current point on error)
- Cancel button
- `start(controller)` — show UI, start 0.5s update timer
- `stop()` — hide UI, cancel timer
- Highlights current capture point on star map via `pathOverlayBridge.highlightPoint(index)`

- [ ] **Step 2: Commit**

```bash
git commit -m "feat: add capture mode UI with progress and pause/resume/skip"
```

---

## Task 12: Wire Everything Together

**Files:**
- Modify: `src/ui/layout.py`, `src/ui/toolbar.py`, `src/ui/bottom_panel.py`
- Modify: `src/starmap/engine.py`

- [ ] **Step 1: Wire toolbar actions to app state**

In `src/ui/toolbar.py`:
- Draw button: sets overlay mode to `draw`, registers `path_add_point` handler that adds ControlPoint to project, pushes undo state, recalculates capture points, refreshes bottom panel
- Freehand button: sets overlay mode to `freehand`, handles `path_freehand_complete` by running RDP + Bézier fitting, replacing path, pushing undo
- Move button: sets overlay mode to `move`, handles drag events by updating control point coordinates
- Add Point / Remove Point / Split: delegate to overlay modes with corresponding event handlers. Remove point emits `path_remove_point` with index, handler removes from project path, pushes undo, recalculates capture points
- Undo/Redo: call `undo_stack.undo()`/`redo()`, deserialize the returned SplinePath snapshot, update project
- Save: serialize project to JSON, offer download via `app.download()`
- Load: `ui.upload()` dialog, deserialize JSON, update app state
- Start Capture: switch to capture view

- [ ] **Step 2: Wire bottom panel to app state**

In `src/ui/bottom_panel.py`:
- Bind number inputs to `capture_settings` fields with `on_change` handlers
- On settings change: recalculate capture points, refresh summary and table
- Connect button: call `indi_client.connect()`, update status label
- Table rows populated from `project.capture_points`

- [ ] **Step 3: Wire star map events**

In `src/starmap/engine.py`:
- Register mousemove → update coordinate display element
- Register click → forward to toolbar's current mode handler
- On view change (pan/zoom) → call `pathOverlayBridge.update()` to reposition overlay elements

- [ ] **Step 4: Wire capture flow**

In `src/ui/layout.py`:
- Start capture: create CaptureController with app state, start capture_view, run controller
- On completion: switch back to planning mode, refresh bottom panel

- [ ] **Step 5: Verify app runs end-to-end**

Run: `timeout 5 python -m src.main || true`

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: wire app state, UI components, and capture flow"
```

---

## Task 13: Project Save/Load

**Files:**
- Modify: `src/ui/toolbar.py`, `src/app_state.py`

- [ ] **Step 1: Implement save dialog**

Save button triggers `app_state.save_project()` → `app.download()` to send JSON to browser.

- [ ] **Step 2: Implement load dialog**

Load button opens `ui.upload()`, on upload: parse JSON, call `app_state.load_project()`, refresh all UI components (star map overlay, bottom panel, toolbar state).

- [ ] **Step 3: Test manually**

Create path, save, reload page, load file, verify path and settings restored.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: add project save/load"
```

---

## Task 14: EKOS Sequence Export

**Files:**
- Create: `src/export/__init__.py`, `src/export/ekos.py`, `tests/test_ekos_export.py`

- [ ] **Step 1: Write failing test**

Test: export produces non-empty XML file, XML contains correct number of Job elements, each Job has correct RA/Dec and exposure settings.

- [ ] **Step 2: Run tests — expect failure**

- [ ] **Step 3: Implement EKOS export**

`src/export/ekos.py`:
- `export_sequence(project, output_path)` → None — writes best-effort EKOS XML
- Uses `xml.etree.ElementTree`
- Each capture point becomes a `<Job>` with Exposure, Count, Binning, Gain, Offset, Coordinates
- Documented caveat: format is undocumented and version-dependent

- [ ] **Step 4: Run tests — expect pass**

- [ ] **Step 5: Add export button to toolbar, commit**

```bash
git commit -m "feat: add EKOS sequence export"
```

---

## Task 15: Integration Tests

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

Test complete workflows:
- `test_full_capture_workflow`: create project → calculate points → run capture with mock INDI → verify all points captured, FITS files exist, manifest written
- `test_save_load_roundtrip`: create project with multi-segment path and handles → save → load → verify all data preserved including handles
- `test_capture_pause_resume`: start capture → pause after first point → resume → verify completion
- `test_capture_with_retry`: mock INDI fails first slew → verify retry succeeds and point is captured
- `test_freehand_to_capture`: generate points via RDP+fitting → calculate capture points → verify valid sequence

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`

- [ ] **Step 3: Run ruff + mypy on entire project**

Run: `ruff check src/ tests/ && mypy src/`

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: add integration tests for full capture workflow"
```

---

## Summary

| Task | Component | Tests | New vs Review Fix |
|------|-----------|-------|-------------------|
| 1 | Project data models (Pydantic) | test_models.py | — |
| 2 | Spline math (Bézier) | test_spline.py | — |
| 3 | Freehand drawing (RDP + fitting) | test_freehand.py | **C1: was missing** |
| 4 | Undo/Redo command stack | test_undo.py | **C2: was missing** |
| 5 | INDI client + mock (with retry/reconnect) | test_indi_client.py | **C3: reconnect added** |
| 6 | FITS writer | test_fits_writer.py | — |
| 7 | Capture controller (state machine) | test_capture_controller.py | **I8: safety abort, skip_point** |
| 8 | NiceGUI app shell & layout | Manual | **I2,I3,I4: coord display, time est, dynamic panel** |
| 9 | Stellarium Web Engine integration | Manual | **M1,M2: fixed NiceGUI patterns** |
| 10 | Path overlay (JavaScript SVG) | Manual | **I1,I5: add/split/highlight** |
| 11 | Capture view UI | Manual | **I3,I5: time remaining, highlight point** |
| 12 | Wire everything together | Manual | **M4: detailed wiring steps** |
| 13 | Project save/load | Manual | — |
| 14 | EKOS export | test_ekos_export.py | — |
| 15 | Integration tests | test_integration.py | — |

**Total: 15 tasks, 7 test files, all review issues addressed**

**Review issues addressed:**
- C1: Freehand drawing → Task 3
- C2: Undo/Redo → Task 4
- C3: INDI reconnect → Task 5 (reconnect method + configurable failures in mock)
- I1: Add/remove/split point handlers → Task 10 (all modes incl. remove_point) + Task 12
- I2: Coordinate display → Task 9 (bridge.js mousemove + DOM element)
- I3: Estimated time remaining → Task 7 (controller property) + Task 11 (display)
- I4: Dynamic bottom panel → Task 8 (`@ui.refreshable` sections)
- I5: Highlight current capture point → Task 10 (`highlightPoint`) + Task 11
- I6: Valid mock FITS → Task 5 (astropy-generated FITS)
- I7: pyproject.toml build backend → Task 1 (fixed)
- I8: Safety abort → Task 7 (atexit + signal handler)
- M1: NiceGUI event bridge → Task 9 (correct `getElement()` pattern)
- M2: StarMap class → Task 9 (correct NiceGUI element pattern)
- M3: Legend → Task 10 (included in overlay)
- M4: Wiring code → Task 12 (detailed steps)
- M5: Preserve captured state on resume → Task 8 (`start_capture` preserves state)
- M7: .gitignore → already created
