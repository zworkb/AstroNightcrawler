# Planner & Capture App — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a browser-based app for planning telescope imaging sequences on an interactive star map and executing them via INDI protocol.

**Architecture:** NiceGUI Python server with Stellarium Web Engine (WASM/WebGL) embedded as a custom element for the star map. A JavaScript overlay layer handles spline path editing. Python backend manages project state, INDI communication, and the capture sequence. All data persists as JSON project files.

**Tech Stack:** Python 3.11+, NiceGUI, Stellarium Web Engine (WASM), PyINDI-client, pydantic (data models), pytest

**Spec:** `docs/superpowers/specs/2026-03-22-sequence-planner-design.md`

---

## File Structure

```
sequence-planner/
├── .gitignore
├── pyproject.toml                    # Project config, dependencies
├── src/
│   ├── __init__.py
│   ├── main.py                       # NiceGUI app entry point
│   ├── app_state.py                  # Application state: project, INDI client, actions
│   ├── models/
│   │   ├── __init__.py
│   │   ├── project.py                # Pydantic models: Project, Path, CaptureSettings, CapturePoint, INDIConfig
│   │   ├── spline.py                 # Cubic Bézier spline math: evaluate, sample points at spacing
│   │   └── undo.py                   # Undo/Redo command stack
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── layout.py                 # Main page layout: toolbar, map container, bottom panel
│   │   ├── toolbar.py                # Toolbar component: drawing tools, file ops, capture start
│   │   ├── bottom_panel.py           # Bottom panel: settings, point list, INDI status
│   │   └── capture_view.py           # Capture mode UI: progress, pause/resume/cancel
│   ├── starmap/
│   │   ├── __init__.py
│   │   ├── engine.py                 # NiceGUI custom element wrapping Stellarium Web Engine
│   │   ├── bridge.js                 # JS bridge: init engine, coordinate conversion, event forwarding
│   │   └── path_overlay.js           # JS overlay: control points, handles, spline rendering, hit testing
│   ├── indi/
│   │   ├── __init__.py
│   │   ├── client.py                 # INDI client: connect, slew, capture, property monitoring, reconnect
│   │   └── mock.py                   # Mock INDI client for testing without hardware
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── controller.py             # Capture sequence controller: state machine, pause/resume, error handling
│   │   └── fits_writer.py            # FITS file writing with proper naming convention
│   └── export/
│       ├── __init__.py
│       └── ekos.py                   # EKOS sequence export (format TBD)
├── static/
│   └── stellarium/                   # Stellarium Web Engine WASM + JS (installed via setup script)
├── skydata/                          # Star catalog HiPS tiles (installed via setup script)
├── scripts/
│   └── install_stellarium.sh         # Download and install Stellarium Web Engine + skydata
├── tests/
│   ├── __init__.py
│   ├── test_models.py                # Project model serialization, validation
│   ├── test_spline.py                # Spline math: evaluation, sampling, arc length, RDP, freehand
│   ├── test_undo.py                  # Undo/Redo command stack
│   ├── test_capture_controller.py    # Capture state machine with mock INDI (incl. retry, reconnect)
│   ├── test_fits_writer.py           # FITS file naming, manifest updates
│   └── test_indi_client.py           # INDI protocol handling
└── docs/
    └── superpowers/
        ├── specs/
        │   └── 2026-03-22-sequence-planner-design.md
        └── plans/
            └── 2026-03-22-planner-capture-app.md  (this file)
```

**Known limitations documented in code:**
- Spline math uses flat Euclidean coordinates (no cos(dec) correction). Acceptable for paths <15° but inaccurate for large angular distances. Noted for future improvement.
- NiceGUI binds to `0.0.0.0` by default — intentional for access from other devices on the network (e.g., tablet in the observatory). Configurable via CLI arg.

---

## Task 1: Project Setup & Data Models

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/main.py`
- Create: `src/models/__init__.py`
- Create: `src/models/project.py`
- Create: `tests/__init__.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "sequence-planner"
version = "0.1.0"
description = "Telescope imaging sequence planner and capture controller"
requires-python = ">=3.11"
dependencies = [
    "nicegui>=2.0",
    "pydantic>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
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
```

- [ ] **Step 2: Create src/__init__.py and tests/__init__.py**

Empty files.

- [ ] **Step 3: Write failing tests for data models**

```python
# tests/test_models.py
import json
import pytest
from pathlib import Path
from src.models.project import (
    Coordinate,
    ControlPoint,
    SplinePath,
    CaptureSettings,
    CapturePoint,
    INDIConfig,
    Project,
)


class TestCoordinate:
    def test_create_coordinate(self):
        c = Coordinate(ra=10.684, dec=41.269)
        assert c.ra == 10.684
        assert c.dec == 41.269

    def test_coordinate_ra_range(self):
        with pytest.raises(ValueError):
            Coordinate(ra=400.0, dec=0.0)

    def test_coordinate_dec_range(self):
        with pytest.raises(ValueError):
            Coordinate(ra=0.0, dec=100.0)


class TestControlPoint:
    def test_control_point_with_handles(self):
        cp = ControlPoint(
            ra=10.684,
            dec=41.269,
            label="M31",
            handle_in=Coordinate(ra=9.0, dec=42.0),
            handle_out=Coordinate(ra=11.5, dec=41.0),
        )
        assert cp.label == "M31"
        assert cp.handle_out.ra == 11.5

    def test_control_point_without_handles(self):
        cp = ControlPoint(ra=14.053, dec=38.683)
        assert cp.handle_in is None
        assert cp.handle_out is None


class TestSplinePath:
    def test_create_path(self):
        path = SplinePath(
            control_points=[
                ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                ControlPoint(ra=20.0, dec=30.0, handle_in=Coordinate(ra=19.0, dec=31.0)),
            ],
        )
        assert path.spline_type == "cubic_bezier"
        assert path.coordinate_frame == "J2000"
        assert len(path.control_points) == 2

    def test_path_needs_at_least_two_points(self):
        with pytest.raises(ValueError):
            SplinePath(control_points=[ControlPoint(ra=10.0, dec=40.0)])


class TestCaptureSettings:
    def test_defaults(self):
        cs = CaptureSettings()
        assert cs.point_spacing_deg == 0.5
        assert cs.exposure_seconds == 30.0
        assert cs.exposures_per_point == 1
        assert cs.gain == 0
        assert cs.offset == 0
        assert cs.binning == 1

    def test_binning_range(self):
        with pytest.raises(ValueError):
            CaptureSettings(binning=5)


class TestCapturePoint:
    def test_pending_point(self):
        cp = CapturePoint(index=0, ra=10.684, dec=41.269)
        assert cp.status == "pending"
        assert cp.files == []
        assert cp.captured_at is None

    def test_captured_point(self):
        cp = CapturePoint(
            index=0,
            ra=10.684,
            dec=41.269,
            status="captured",
            files=["seq_0001_001.fits"],
            captured_at="2026-03-22T02:16:30Z",
        )
        assert cp.status == "captured"
        assert len(cp.files) == 1


class TestProject:
    def test_create_project(self):
        project = Project(
            project="test-sweep",
            path=SplinePath(
                control_points=[
                    ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                    ControlPoint(ra=20.0, dec=30.0, handle_in=Coordinate(ra=19.0, dec=31.0)),
                ],
            ),
        )
        assert project.version == "1.0"
        assert project.project == "test-sweep"

    def test_roundtrip_json(self, tmp_path):
        project = Project(
            project="test-sweep",
            path=SplinePath(
                control_points=[
                    ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                    ControlPoint(ra=20.0, dec=30.0, handle_in=Coordinate(ra=19.0, dec=31.0)),
                ],
            ),
            capture_settings=CaptureSettings(exposure_seconds=60.0, gain=120),
            indi=INDIConfig(telescope="EQMod Mount", camera="ZWO ASI294MC Pro"),
        )
        filepath = tmp_path / "project.json"
        filepath.write_text(project.model_dump_json(indent=2))
        loaded = Project.model_validate_json(filepath.read_text())
        assert loaded.project == "test-sweep"
        assert loaded.capture_settings.exposure_seconds == 60.0
        assert loaded.indi.telescope == "EQMod Mount"
        assert len(loaded.path.control_points) == 2

    def test_filename_for_point(self):
        """Index 0 -> seq_0001_001.fits"""
        cp = CapturePoint(index=0, ra=10.0, dec=40.0)
        expected = "seq_0001_001.fits"
        assert cp.filename(exposure=1) == expected

    def test_filename_for_multi_exposure(self):
        """Index 2, exposure 3 -> seq_0003_003.fits"""
        cp = CapturePoint(index=2, ra=10.0, dec=40.0)
        assert cp.filename(exposure=3) == "seq_0003_003.fits"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_models.py -v`
Expected: ModuleNotFoundError for `src.models.project`

- [ ] **Step 5: Implement data models**

```python
# src/models/__init__.py
```

```python
# src/models/project.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Coordinate(BaseModel):
    ra: float = Field(ge=0.0, lt=360.0, description="Right ascension in degrees")
    dec: float = Field(ge=-90.0, le=90.0, description="Declination in degrees")


class ControlPoint(BaseModel):
    ra: float = Field(ge=0.0, lt=360.0)
    dec: float = Field(ge=-90.0, le=90.0)
    label: Optional[str] = None
    handle_in: Optional[Coordinate] = None
    handle_out: Optional[Coordinate] = None


class SplinePath(BaseModel):
    control_points: list[ControlPoint] = Field(min_length=2)
    spline_type: str = "cubic_bezier"
    coordinate_frame: str = "J2000"


class CaptureSettings(BaseModel):
    point_spacing_deg: float = Field(default=0.5, gt=0.0)
    exposure_seconds: float = Field(default=30.0, gt=0.0)
    exposures_per_point: int = Field(default=1, ge=1)
    gain: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)
    binning: int = Field(default=1, ge=1, le=4)


class CapturePoint(BaseModel):
    index: int = Field(ge=0)
    ra: float
    dec: float
    files: list[str] = Field(default_factory=list)
    status: str = Field(default="pending")
    captured_at: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"pending", "capturing", "captured", "failed", "skipped"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v

    def filename(self, exposure: int = 1) -> str:
        """Generate FITS filename. Index is 0-based internally, 1-based in filename."""
        return f"seq_{self.index + 1:04d}_{exposure:03d}.fits"


class INDIConfig(BaseModel):
    telescope: str = ""
    camera: str = ""
    host: str = "localhost"
    port: int = 7624


class Project(BaseModel):
    version: str = "1.0"
    created: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    project: str = ""
    path: SplinePath = Field(
        default_factory=lambda: SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0),
                ControlPoint(ra=1.0, dec=0.0),
            ]
        )
    )
    capture_settings: CaptureSettings = Field(default_factory=CaptureSettings)
    capture_points: list[CapturePoint] = Field(default_factory=list)
    indi: INDIConfig = Field(default_factory=INDIConfig)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_models.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/__init__.py src/models/__init__.py src/models/project.py tests/__init__.py tests/test_models.py
git commit -m "feat: add project data models with pydantic validation"
```

---

## Task 2: Spline Math

**Files:**
- Create: `src/models/spline.py`
- Create: `tests/test_spline.py`

- [ ] **Step 1: Write failing tests for spline math**

```python
# tests/test_spline.py
import math
import pytest
from src.models.spline import (
    bezier_point,
    bezier_curve_length,
    sample_points_along_spline,
)
from src.models.project import ControlPoint, Coordinate, SplinePath


class TestBezierPoint:
    def test_start_point(self):
        """t=0 returns the first control point."""
        p = bezier_point(
            p0=(0.0, 0.0), p1=(1.0, 2.0), p2=(3.0, 2.0), p3=(4.0, 0.0), t=0.0
        )
        assert p == pytest.approx((0.0, 0.0))

    def test_end_point(self):
        """t=1 returns the last control point."""
        p = bezier_point(
            p0=(0.0, 0.0), p1=(1.0, 2.0), p2=(3.0, 2.0), p3=(4.0, 0.0), t=1.0
        )
        assert p == pytest.approx((4.0, 0.0))

    def test_midpoint_straight_line(self):
        """For a straight line, t=0.5 is the midpoint."""
        p = bezier_point(
            p0=(0.0, 0.0), p1=(1.0, 0.0), p2=(2.0, 0.0), p3=(3.0, 0.0), t=0.5
        )
        assert p == pytest.approx((1.5, 0.0))


class TestBezierCurveLength:
    def test_straight_line_length(self):
        length = bezier_curve_length(
            p0=(0.0, 0.0), p1=(1.0, 0.0), p2=(2.0, 0.0), p3=(3.0, 0.0)
        )
        assert length == pytest.approx(3.0, abs=0.01)

    def test_curved_line_longer_than_straight(self):
        straight = bezier_curve_length(
            p0=(0.0, 0.0), p1=(1.0, 0.0), p2=(2.0, 0.0), p3=(3.0, 0.0)
        )
        curved = bezier_curve_length(
            p0=(0.0, 0.0), p1=(1.0, 5.0), p2=(2.0, 5.0), p3=(3.0, 0.0)
        )
        assert curved > straight


class TestSamplePointsAlongSpline:
    def test_sample_straight_path(self):
        path = SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0, handle_out=Coordinate(ra=1.0, dec=0.0)),
                ControlPoint(ra=3.0, dec=0.0, handle_in=Coordinate(ra=2.0, dec=0.0)),
            ],
        )
        points = sample_points_along_spline(path, spacing_deg=1.0)
        # A 3-degree straight line with 1-degree spacing should give ~4 points (including endpoints)
        assert len(points) >= 3
        # First and last point should match control points
        assert points[0] == pytest.approx((0.0, 0.0), abs=0.05)
        assert points[-1] == pytest.approx((3.0, 0.0), abs=0.05)

    def test_spacing_affects_count(self):
        path = SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0, handle_out=Coordinate(ra=2.0, dec=0.0)),
                ControlPoint(ra=6.0, dec=0.0, handle_in=Coordinate(ra=4.0, dec=0.0)),
            ],
        )
        coarse = sample_points_along_spline(path, spacing_deg=2.0)
        fine = sample_points_along_spline(path, spacing_deg=0.5)
        assert len(fine) > len(coarse)

    def test_multi_segment_path(self):
        path = SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0, handle_out=Coordinate(ra=1.0, dec=0.0)),
                ControlPoint(
                    ra=3.0, dec=0.0,
                    handle_in=Coordinate(ra=2.0, dec=0.0),
                    handle_out=Coordinate(ra=4.0, dec=0.0),
                ),
                ControlPoint(ra=6.0, dec=0.0, handle_in=Coordinate(ra=5.0, dec=0.0)),
            ],
        )
        points = sample_points_along_spline(path, spacing_deg=1.0)
        # Two segments of ~3 degrees each, 1-degree spacing -> ~7 points
        assert len(points) >= 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_spline.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Implement spline math**

```python
# src/models/spline.py
"""Cubic Bézier spline math for path evaluation and point sampling."""

from __future__ import annotations

import math

from src.models.project import ControlPoint, Coordinate, SplinePath


def bezier_point(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    """Evaluate cubic Bézier curve at parameter t (0..1).

    p0, p3 are endpoints; p1, p2 are control handles.
    """
    u = 1.0 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return (x, y)


def bezier_curve_length(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    segments: int = 100,
) -> float:
    """Approximate arc length of a cubic Bézier by summing small segments."""
    length = 0.0
    prev = p0
    for i in range(1, segments + 1):
        t = i / segments
        curr = bezier_point(p0, p1, p2, p3, t)
        dx = curr[0] - prev[0]
        dy = curr[1] - prev[1]
        length += math.sqrt(dx * dx + dy * dy)
        prev = curr
    return length


def _segment_handles(
    cp_start: ControlPoint, cp_end: ControlPoint
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]:
    """Extract the four Bézier points for a segment between two control points."""
    p0 = (cp_start.ra, cp_start.dec)
    p3 = (cp_end.ra, cp_end.dec)

    if cp_start.handle_out:
        p1 = (cp_start.handle_out.ra, cp_start.handle_out.dec)
    else:
        # Default: 1/3 of the way toward p3
        p1 = (p0[0] + (p3[0] - p0[0]) / 3, p0[1] + (p3[1] - p0[1]) / 3)

    if cp_end.handle_in:
        p2 = (cp_end.handle_in.ra, cp_end.handle_in.dec)
    else:
        # Default: 2/3 of the way from p0 toward p3
        p2 = (p0[0] + 2 * (p3[0] - p0[0]) / 3, p0[1] + 2 * (p3[1] - p0[1]) / 3)

    return p0, p1, p2, p3


def sample_points_along_spline(
    path: SplinePath, spacing_deg: float
) -> list[tuple[float, float]]:
    """Sample evenly-spaced points along a multi-segment cubic Bézier spline.

    Returns list of (ra, dec) tuples in degrees.
    """
    if len(path.control_points) < 2:
        return [(path.control_points[0].ra, path.control_points[0].dec)]

    # Build a densely-sampled polyline from all segments
    dense_points: list[tuple[float, float]] = []
    resolution = 200  # samples per segment

    for i in range(len(path.control_points) - 1):
        p0, p1, p2, p3 = _segment_handles(path.control_points[i], path.control_points[i + 1])
        start = 0 if i == 0 else 1  # avoid duplicate points at segment joins
        for j in range(start, resolution + 1):
            t = j / resolution
            dense_points.append(bezier_point(p0, p1, p2, p3, t))

    # Walk along the polyline and pick points at the requested spacing
    if not dense_points:
        return []

    result = [dense_points[0]]
    accumulated = 0.0

    for i in range(1, len(dense_points)):
        dx = dense_points[i][0] - dense_points[i - 1][0]
        dy = dense_points[i][1] - dense_points[i - 1][1]
        step = math.sqrt(dx * dx + dy * dy)
        accumulated += step

        if accumulated >= spacing_deg:
            result.append(dense_points[i])
            accumulated = 0.0

    # Always include the last point
    last = dense_points[-1]
    if result[-1] != last:
        result.append(last)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_spline.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/models/spline.py tests/test_spline.py
git commit -m "feat: add cubic Bézier spline math with point sampling"
```

---

## Task 3: INDI Client

**Files:**
- Create: `src/indi/__init__.py`
- Create: `src/indi/client.py`
- Create: `src/indi/mock.py`
- Create: `tests/test_indi_client.py`

- [ ] **Step 1: Write failing tests for INDI client**

```python
# tests/test_indi_client.py
import asyncio
import pytest
from src.indi.client import INDIClient, INDIError, SlewTimeout, SettleTimeout
from src.indi.mock import MockINDIClient


class TestMockINDIClient:
    @pytest.fixture
    def client(self):
        return MockINDIClient()

    @pytest.mark.asyncio
    async def test_connect(self, client):
        await client.connect("localhost", 7624)
        assert client.connected

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        await client.connect("localhost", 7624)
        await client.disconnect()
        assert not client.connected

    @pytest.mark.asyncio
    async def test_slew_to(self, client):
        await client.connect("localhost", 7624)
        await client.slew_to(ra=10.684, dec=41.269)
        assert client.current_ra == pytest.approx(10.684)
        assert client.current_dec == pytest.approx(41.269)

    @pytest.mark.asyncio
    async def test_wait_for_settle(self, client):
        await client.connect("localhost", 7624)
        await client.slew_to(ra=10.684, dec=41.269)
        settled = await client.wait_for_settle(timeout=5.0)
        assert settled is True

    @pytest.mark.asyncio
    async def test_capture(self, client):
        await client.connect("localhost", 7624)
        data = await client.capture(
            exposure_seconds=1.0, gain=120, offset=10, binning=1
        )
        assert data is not None
        assert len(data) > 0  # returns fake FITS data

    @pytest.mark.asyncio
    async def test_slew_without_connect_raises(self, client):
        with pytest.raises(INDIError, match="not connected"):
            await client.slew_to(ra=10.0, dec=40.0)

    @pytest.mark.asyncio
    async def test_get_devices(self, client):
        await client.connect("localhost", 7624)
        devices = await client.get_devices()
        assert "telescope" in devices
        assert "camera" in devices


class TestINDIClientInterface:
    """Verify the real client has the same interface as the mock."""

    def test_has_required_methods(self):
        for method in ["connect", "disconnect", "slew_to", "wait_for_settle", "capture", "get_devices", "abort"]:
            assert hasattr(INDIClient, method), f"INDIClient missing method: {method}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_indi_client.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Implement INDI client interface and mock**

```python
# src/indi/__init__.py
```

```python
# src/indi/client.py
"""INDI client for telescope and camera control.

The real implementation uses PyINDI or direct INDI XML protocol.
For now, this defines the interface; the actual INDI protocol handling
will be implemented when hardware integration begins.
"""

from __future__ import annotations


class INDIError(Exception):
    pass


class SlewTimeout(INDIError):
    pass


class SettleTimeout(INDIError):
    pass


class CaptureTimeout(INDIError):
    pass


class INDIClient:
    """INDI client interface for telescope and camera control."""

    def __init__(self) -> None:
        self.connected = False
        self.host = ""
        self.port = 7624

    async def connect(self, host: str, port: int = 7624) -> None:
        raise NotImplementedError("Real INDI client not yet implemented")

    async def disconnect(self) -> None:
        raise NotImplementedError

    async def slew_to(self, ra: float, dec: float, timeout: float = 120.0) -> None:
        raise NotImplementedError

    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        raise NotImplementedError

    async def capture(
        self,
        exposure_seconds: float,
        gain: int = 0,
        offset: int = 0,
        binning: int = 1,
        timeout: float | None = None,
    ) -> bytes:
        raise NotImplementedError

    async def get_devices(self) -> dict[str, str]:
        raise NotImplementedError

    async def reconnect(self, timeout: float = 60.0, interval: float = 10.0) -> bool:
        """Attempt to reconnect to the INDI server.

        Retries every `interval` seconds for up to `timeout` seconds.
        Returns True if reconnected, False if timed out.
        """
        raise NotImplementedError

    async def abort(self) -> None:
        raise NotImplementedError
```

```python
# src/indi/mock.py
"""Mock INDI client for testing without hardware."""

from __future__ import annotations

import asyncio
import struct

from src.indi.client import INDIClient, INDIError


class MockINDIClient(INDIClient):
    """Simulates telescope slew and camera capture for testing."""

    def __init__(
        self,
        slew_delay: float = 0.01,
        settle_delay: float = 0.01,
        fail_slew_count: int = 0,
        fail_capture_count: int = 0,
    ) -> None:
        super().__init__()
        self.current_ra = 0.0
        self.current_dec = 0.0
        self.slew_delay = slew_delay
        self.settle_delay = settle_delay
        self._fail_slew_count = fail_slew_count
        self._fail_capture_count = fail_capture_count
        self._slew_attempts = 0
        self._capture_attempts = 0

    async def connect(self, host: str, port: int = 7624) -> None:
        self.host = host
        self.port = port
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def slew_to(self, ra: float, dec: float, timeout: float = 120.0) -> None:
        if not self.connected:
            raise INDIError("not connected")
        await asyncio.sleep(self.slew_delay)
        self.current_ra = ra
        self.current_dec = dec

    async def wait_for_settle(self, timeout: float = 30.0) -> bool:
        if not self.connected:
            raise INDIError("not connected")
        await asyncio.sleep(self.settle_delay)
        return True

    async def capture(
        self,
        exposure_seconds: float,
        gain: int = 0,
        offset: int = 0,
        binning: int = 1,
        timeout: float | None = None,
    ) -> bytes:
        if not self.connected:
            raise INDIError("not connected")
        # Generate a valid minimal FITS file using proper 80-byte card format
        import io
        import numpy as np
        try:
            from astropy.io import fits
            hdu = fits.PrimaryHDU(data=np.zeros((100, 100), dtype=np.uint16))
            hdu.header["EXPTIME"] = exposure_seconds
            hdu.header["OBJCTRA"] = f"{self.current_ra:.6f}"
            hdu.header["OBJCTDEC"] = f"{self.current_dec:.6f}"
            hdu.header["INSTRUME"] = "Mock Camera"
            hdu.header["TELESCOP"] = "Mock Mount"
            buf = io.BytesIO()
            hdu.writeto(buf)
            return buf.getvalue()
        except ImportError:
            # Fallback: minimal valid FITS without astropy
            cards = [
                f"{'SIMPLE':<8}= {'T':>20} / Standard FITS",
                f"{'BITPIX':<8}= {'16':>20} / 16-bit data",
                f"{'NAXIS':<8}= {'2':>20} / 2D image",
                f"{'NAXIS1':<8}= {'100':>20} / width",
                f"{'NAXIS2':<8}= {'100':>20} / height",
                f"{'EXPTIME':<8}= {exposure_seconds:>20.1f} / Exposure time",
                f"{'END':<8}",
            ]
            header_str = "".join(f"{c:<80}" for c in cards)
            header_bytes = header_str.encode("ascii")
            padding = 2880 - (len(header_bytes) % 2880)
            if padding < 2880:
                header_bytes += b" " * padding
            pixel_data = b"\x00\x01" * (100 * 100)
            return header_bytes + pixel_data

    async def get_devices(self) -> dict[str, str]:
        if not self.connected:
            raise INDIError("not connected")
        return {"telescope": "Mock Mount", "camera": "Mock Camera"}

    async def abort(self) -> None:
        if not self.connected:
            raise INDIError("not connected")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_indi_client.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/indi/__init__.py src/indi/client.py src/indi/mock.py tests/test_indi_client.py
git commit -m "feat: add INDI client interface and mock for testing"
```

---

## Task 4: FITS Writer

**Files:**
- Create: `src/capture/__init__.py`
- Create: `src/capture/fits_writer.py`
- Create: `tests/test_fits_writer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fits_writer.py
import pytest
from pathlib import Path
from src.capture.fits_writer import FITSWriter
from src.models.project import CapturePoint


class TestFITSWriter:
    @pytest.fixture
    def output_dir(self, tmp_path):
        return tmp_path / "capture_output"

    def test_creates_output_directory(self, output_dir):
        writer = FITSWriter(output_dir)
        assert output_dir.exists()

    def test_write_fits(self, output_dir):
        writer = FITSWriter(output_dir)
        point = CapturePoint(index=0, ra=10.684, dec=41.269)
        fake_data = b"\x00" * 2880  # Minimal FITS block
        filepath = writer.write(point, exposure_num=1, data=fake_data)
        assert filepath.exists()
        assert filepath.name == "seq_0001_001.fits"

    def test_write_multi_exposure(self, output_dir):
        writer = FITSWriter(output_dir)
        point = CapturePoint(index=0, ra=10.684, dec=41.269)
        fake_data = b"\x00" * 2880
        f1 = writer.write(point, exposure_num=1, data=fake_data)
        f2 = writer.write(point, exposure_num=2, data=fake_data)
        assert f1.name == "seq_0001_001.fits"
        assert f2.name == "seq_0001_002.fits"

    def test_write_updates_point_files(self, output_dir):
        writer = FITSWriter(output_dir)
        point = CapturePoint(index=0, ra=10.684, dec=41.269)
        fake_data = b"\x00" * 2880
        writer.write(point, exposure_num=1, data=fake_data)
        assert "seq_0001_001.fits" in point.files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_fits_writer.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Implement FITS writer**

```python
# src/capture/__init__.py
```

```python
# src/capture/fits_writer.py
"""Write captured FITS data to disk with proper naming convention."""

from __future__ import annotations

from pathlib import Path

from src.models.project import CapturePoint


class FITSWriter:
    """Writes FITS data to an output directory with seq_NNNN_MMM.fits naming."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, point: CapturePoint, exposure_num: int, data: bytes) -> Path:
        """Write FITS data for a capture point.

        Updates point.files with the filename.
        Returns the full path of the written file.
        """
        filename = point.filename(exposure=exposure_num)
        filepath = self.output_dir / filename
        filepath.write_bytes(data)
        if filename not in point.files:
            point.files.append(filename)
        return filepath
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_fits_writer.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/capture/__init__.py src/capture/fits_writer.py tests/test_fits_writer.py
git commit -m "feat: add FITS writer with naming convention"
```

---

## Task 5: Capture Controller (State Machine)

**Files:**
- Create: `src/capture/controller.py`
- Create: `tests/test_capture_controller.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_capture_controller.py
import asyncio
import pytest
from pathlib import Path
from src.capture.controller import CaptureController, CaptureState
from src.indi.mock import MockINDIClient
from src.models.project import (
    Project, SplinePath, ControlPoint, Coordinate,
    CaptureSettings, CapturePoint, INDIConfig,
)


def make_test_project(tmp_path: Path, num_points: int = 3) -> Project:
    project = Project(
        project="test",
        path=SplinePath(
            control_points=[
                ControlPoint(ra=0.0, dec=0.0, handle_out=Coordinate(ra=1.0, dec=0.0)),
                ControlPoint(ra=6.0, dec=0.0, handle_in=Coordinate(ra=5.0, dec=0.0)),
            ],
        ),
        capture_settings=CaptureSettings(
            point_spacing_deg=2.0, exposure_seconds=1.0, exposures_per_point=1
        ),
        capture_points=[
            CapturePoint(index=i, ra=float(i * 2), dec=0.0)
            for i in range(num_points)
        ],
    )
    return project


class TestCaptureController:
    @pytest.fixture
    def setup(self, tmp_path):
        client = MockINDIClient()
        project = make_test_project(tmp_path)
        output_dir = tmp_path / "output"
        controller = CaptureController(
            project=project,
            indi_client=client,
            output_dir=output_dir,
        )
        return controller, client, project

    @pytest.mark.asyncio
    async def test_initial_state(self, setup):
        controller, _, _ = setup
        assert controller.state == CaptureState.IDLE

    @pytest.mark.asyncio
    async def test_run_full_sequence(self, setup):
        controller, client, project = setup
        await client.connect("localhost", 7624)
        await controller.run()
        assert controller.state == CaptureState.COMPLETED
        # All points should be captured
        for point in project.capture_points:
            assert point.status == "captured"
            assert len(point.files) == 1

    @pytest.mark.asyncio
    async def test_pause_and_resume(self, setup):
        controller, client, project = setup
        await client.connect("localhost", 7624)

        # Start capture in background
        task = asyncio.create_task(controller.run())
        # Give it a moment to start
        await asyncio.sleep(0.05)
        controller.pause()
        assert controller.state == CaptureState.PAUSED

        controller.resume()
        await task
        assert controller.state == CaptureState.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel(self, setup):
        controller, client, project = setup
        await client.connect("localhost", 7624)

        task = asyncio.create_task(controller.run())
        await asyncio.sleep(0.05)
        controller.cancel()
        await task
        assert controller.state == CaptureState.CANCELLED

    @pytest.mark.asyncio
    async def test_progress_tracking(self, setup):
        controller, client, project = setup
        await client.connect("localhost", 7624)
        await controller.run()
        assert controller.current_point_index == len(project.capture_points)

    @pytest.mark.asyncio
    async def test_multi_exposure(self, tmp_path):
        client = MockINDIClient()
        project = make_test_project(tmp_path, num_points=2)
        project.capture_settings.exposures_per_point = 3
        output_dir = tmp_path / "output"
        controller = CaptureController(
            project=project, indi_client=client, output_dir=output_dir
        )
        await client.connect("localhost", 7624)
        await controller.run()
        for point in project.capture_points:
            assert len(point.files) == 3

    @pytest.mark.asyncio
    async def test_resume_from_partial(self, tmp_path):
        """Resume a sequence where some points are already captured."""
        client = MockINDIClient()
        project = make_test_project(tmp_path, num_points=3)
        project.capture_points[0].status = "captured"
        project.capture_points[0].files = ["seq_0001_001.fits"]
        output_dir = tmp_path / "output"
        controller = CaptureController(
            project=project, indi_client=client, output_dir=output_dir
        )
        await client.connect("localhost", 7624)
        await controller.run()
        # Only points 1 and 2 should have been captured fresh
        assert project.capture_points[0].status == "captured"
        assert project.capture_points[1].status == "captured"
        assert project.capture_points[2].status == "captured"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_capture_controller.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: Implement capture controller**

```python
# src/capture/controller.py
"""Capture sequence controller: state machine with pause/resume/cancel."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from src.capture.fits_writer import FITSWriter
from src.indi.client import INDIClient, INDIError, SlewTimeout, SettleTimeout, CaptureTimeout
from src.models.project import Project


class CaptureState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ERROR = "error"


class CaptureController:
    """Runs the capture sequence: slew, settle, capture, write, repeat."""

    def __init__(
        self,
        project: Project,
        indi_client: INDIClient,
        output_dir: Path,
    ) -> None:
        self.project = project
        self.indi = indi_client
        self.writer = FITSWriter(output_dir)
        self.output_dir = output_dir

        self.state = CaptureState.IDLE
        self.current_point_index = 0
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
        self._cancel_requested = False
        self.last_error: str | None = None

    def pause(self) -> None:
        if self.state == CaptureState.RUNNING:
            self.state = CaptureState.PAUSED
            self._pause_event.clear()

    def resume(self) -> None:
        if self.state == CaptureState.PAUSED:
            self.state = CaptureState.RUNNING
            self._pause_event.set()

    def cancel(self) -> None:
        self._cancel_requested = True
        # Unblock if paused so the loop can exit
        self._pause_event.set()

    async def run(self) -> None:
        """Execute the capture sequence."""
        self.state = CaptureState.RUNNING
        self._cancel_requested = False
        settings = self.project.capture_settings

        for i, point in enumerate(self.project.capture_points):
            # Skip already captured points
            if point.status == "captured":
                self.current_point_index = i + 1
                continue

            # Check for cancel
            if self._cancel_requested:
                self.state = CaptureState.CANCELLED
                return

            # Wait if paused
            await self._pause_event.wait()
            if self._cancel_requested:
                self.state = CaptureState.CANCELLED
                return

            self.current_point_index = i
            point.status = "capturing"

            try:
                # Slew with retry
                await self._slew_with_retry(point.ra, point.dec)

                # Capture exposures
                for exp_num in range(1, settings.exposures_per_point + 1):
                    if self._cancel_requested:
                        self.state = CaptureState.CANCELLED
                        return

                    await self._pause_event.wait()

                    timeout = settings.exposure_seconds + 30.0
                    data = await self.indi.capture(
                        exposure_seconds=settings.exposure_seconds,
                        gain=settings.gain,
                        offset=settings.offset,
                        binning=settings.binning,
                        timeout=timeout,
                    )
                    self.writer.write(point, exposure_num=exp_num, data=data)

                point.status = "captured"
                point.captured_at = datetime.now(timezone.utc).isoformat()

            except INDIError as e:
                point.status = "failed"
                self.last_error = str(e)
                self.state = CaptureState.PAUSED
                self._pause_event.clear()
                # Wait for user to resume or cancel
                await self._pause_event.wait()
                if self._cancel_requested:
                    self.state = CaptureState.CANCELLED
                    return
                # User resumed — retry this point
                point.status = "pending"
                continue

            self.current_point_index = i + 1

        # Save manifest
        self._save_manifest()
        self.state = CaptureState.COMPLETED

    async def _slew_with_retry(self, ra: float, dec: float, retries: int = 1) -> None:
        """Slew to target and wait for settle, with retry on failure."""
        for attempt in range(retries + 1):
            try:
                await self.indi.slew_to(ra, dec, timeout=120.0)
                settled = await self.indi.wait_for_settle(timeout=30.0)
                if settled:
                    return
            except (SlewTimeout, SettleTimeout):
                if attempt < retries:
                    continue
                raise

    def _save_manifest(self) -> None:
        """Write the project as manifest.json to the output directory."""
        manifest_path = self.output_dir / "manifest.json"
        manifest_path.write_text(self.project.model_dump_json(indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_capture_controller.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/capture/controller.py tests/test_capture_controller.py
git commit -m "feat: add capture controller state machine with pause/resume/cancel"
```

---

## Task 6: NiceGUI App Shell & Layout

**Files:**
- Create: `src/main.py`
- Create: `src/ui/__init__.py`
- Create: `src/ui/layout.py`
- Create: `src/ui/toolbar.py`
- Create: `src/ui/bottom_panel.py`

- [ ] **Step 1: Create app entry point**

```python
# src/main.py
"""Sequence Planner — NiceGUI application entry point."""

from nicegui import ui

from src.ui.layout import create_layout


@ui.page("/")
def index():
    create_layout()


def main():
    ui.run(title="Sequence Planner", host="0.0.0.0", port=8080, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
```

- [ ] **Step 2: Create UI layout**

```python
# src/ui/__init__.py
```

```python
# src/ui/layout.py
"""Main page layout: toolbar, star map container, bottom panel."""

from nicegui import ui

from src.ui.toolbar import create_toolbar
from src.ui.bottom_panel import create_bottom_panel


def create_layout():
    """Build the main page with toolbar, map area, and bottom panel."""
    ui.add_head_html("""
    <style>
        body { margin: 0; overflow: hidden; }
        .map-container {
            flex: 1;
            position: relative;
            background: #0a0a19;
            min-height: 0;
        }
    </style>
    """)

    with ui.column().classes("w-full h-screen no-wrap"):
        create_toolbar()

        # Star map container — will hold the Stellarium engine canvas
        with ui.element("div").classes("map-container") as map_container:
            ui.label("Star map loading...").classes("text-grey-6 absolute-center")

        create_bottom_panel()
```

```python
# src/ui/toolbar.py
"""Toolbar component: drawing tools, file operations, capture start."""

from nicegui import ui


def create_toolbar():
    """Create the toolbar above the star map."""
    with ui.row().classes("w-full items-center gap-1 px-2 py-1 bg-dark"):
        # Drawing tools
        ui.button("Draw", icon="edit").props("flat dense color=primary size=sm")
        ui.button("Move", icon="pan_tool").props("flat dense color=grey size=sm")
        ui.button("Add Point", icon="add_circle_outline").props("flat dense color=grey size=sm")
        ui.button("Split", icon="content_cut").props("flat dense color=grey size=sm")

        ui.separator().props("vertical")

        # Edit
        ui.button(icon="undo").props("flat dense color=grey size=sm").tooltip("Undo")
        ui.button(icon="redo").props("flat dense color=grey size=sm").tooltip("Redo")

        ui.space()

        # File
        ui.button("Save", icon="save").props("flat dense color=grey size=sm")
        ui.button("Load", icon="folder_open").props("flat dense color=grey size=sm")

        ui.separator().props("vertical")

        # Capture
        ui.button("Start Capture", icon="play_arrow").props(
            "dense color=positive size=sm"
        )
```

```python
# src/ui/bottom_panel.py
"""Bottom panel: path settings, capture point list, INDI connection status."""

from nicegui import ui


def create_bottom_panel():
    """Create the collapsible bottom panel."""
    with ui.expansion("Path: 0 points | Exposure: 30s × 1 | Spacing: 0.5°").classes(
        "w-full bg-dark-page"
    ) as panel:
        with ui.row().classes("w-full gap-4 p-2"):
            # Path settings
            with ui.column().classes("gap-1"):
                ui.label("Path Settings").classes("text-primary text-weight-medium")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Spacing (°):").classes("text-grey-5")
                    ui.number(value=0.5, min=0.01, max=10.0, step=0.1).classes(
                        "w-20"
                    )
                with ui.row().classes("items-center gap-2"):
                    ui.label("Exposure (s):").classes("text-grey-5")
                    ui.number(value=30.0, min=0.1, max=3600.0, step=1.0).classes(
                        "w-20"
                    )
                with ui.row().classes("items-center gap-2"):
                    ui.label("Exposures/Point:").classes("text-grey-5")
                    ui.number(value=1, min=1, max=100, step=1).classes("w-20")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Gain:").classes("text-grey-5")
                    ui.number(value=0, min=0, max=1000, step=1).classes("w-20")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Offset:").classes("text-grey-5")
                    ui.number(value=0, min=0, max=1000, step=1).classes("w-20")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Binning:").classes("text-grey-5")
                    ui.number(value=1, min=1, max=4, step=1).classes("w-20")

            # Capture point table
            with ui.column().classes("gap-1 flex-grow"):
                ui.label("Capture Points").classes("text-orange text-weight-medium")
                columns = [
                    {"name": "index", "label": "#", "field": "index", "align": "left"},
                    {"name": "ra", "label": "RA", "field": "ra", "align": "left"},
                    {"name": "dec", "label": "Dec", "field": "dec", "align": "left"},
                    {"name": "status", "label": "Status", "field": "status", "align": "left"},
                ]
                ui.table(columns=columns, rows=[]).classes("w-full")

            # INDI connection
            with ui.column().classes("gap-1"):
                ui.label("INDI Connection").classes("text-positive text-weight-medium")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Host:").classes("text-grey-5")
                    ui.input(value="localhost").classes("w-32")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Port:").classes("text-grey-5")
                    ui.number(value=7624).classes("w-20")
                with ui.row().classes("items-center gap-2"):
                    ui.label("Status:").classes("text-grey-5")
                    ui.label("Disconnected").classes("text-negative")
                ui.button("Connect", icon="link").props("dense size=sm")
```

- [ ] **Step 3: Verify app starts**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && timeout 5 python -m src.main || true`
Expected: NiceGUI starts on port 8080 (then times out — that's fine, we just want no import errors)

- [ ] **Step 4: Commit**

```bash
git add src/main.py src/ui/__init__.py src/ui/layout.py src/ui/toolbar.py src/ui/bottom_panel.py
git commit -m "feat: add NiceGUI app shell with toolbar and bottom panel"
```

---

## Task 7: Stellarium Web Engine Integration

**Files:**
- Create: `scripts/install_stellarium.sh`
- Create: `src/starmap/__init__.py`
- Create: `src/starmap/engine.py`
- Create: `src/starmap/bridge.js`

- [ ] **Step 1: Create Stellarium install script**

```bash
#!/usr/bin/env bash
# scripts/install_stellarium.sh
# Download and build Stellarium Web Engine for local use.
# AGPL-3.0 — installed separately, not bundled with the application.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STATIC_DIR="$PROJECT_DIR/static/stellarium"
SKYDATA_DIR="$PROJECT_DIR/skydata"

echo "=== Stellarium Web Engine Installer ==="

# Check for Emscripten
if ! command -v emcc &>/dev/null; then
    echo "ERROR: Emscripten (emcc) not found."
    echo "Install from: https://emscripten.org/docs/getting_started/"
    exit 1
fi

# Clone or update
REPO_DIR="$PROJECT_DIR/.stellarium-build"
if [ -d "$REPO_DIR" ]; then
    echo "Updating existing checkout..."
    cd "$REPO_DIR" && git pull
else
    echo "Cloning stellarium-web-engine..."
    git clone https://github.com/Stellarium/stellarium-web-engine.git "$REPO_DIR"
fi

# Build WASM
cd "$REPO_DIR"
echo "Building WebAssembly..."
make js-es6

# Copy build artifacts
mkdir -p "$STATIC_DIR"
cp html/static/js/stellarium-web-engine.js "$STATIC_DIR/"
cp html/static/js/stellarium-web-engine.wasm "$STATIC_DIR/"

# Download offline star data (magnitude ≤ 10 subset)
mkdir -p "$SKYDATA_DIR"
echo "Downloading offline star data..."
# The actual data URLs will need to be determined from the Stellarium data server.
# For now, create a placeholder structure.
mkdir -p "$SKYDATA_DIR/stars" "$SKYDATA_DIR/dso" "$SKYDATA_DIR/skycultures"
echo "Skydata directory prepared at $SKYDATA_DIR"
echo ""
echo "=== Installation complete ==="
echo "Static files: $STATIC_DIR"
echo "Sky data: $SKYDATA_DIR"
```

- [ ] **Step 2: Make install script executable**

Run: `chmod +x /home/phil/dev/astro/nicegui/sequence-planner/scripts/install_stellarium.sh`

- [ ] **Step 3: Create JS bridge**

```javascript
// src/starmap/bridge.js
// JavaScript bridge between NiceGUI Python and Stellarium Web Engine.
// Handles engine initialization, coordinate conversion, and event forwarding.

let stelEngine = null;
let stelCanvas = null;

/**
 * Initialize the Stellarium Web Engine in the given container.
 * @param {string} containerId - DOM element ID to render into
 * @param {string} wasmUrl - URL to the .wasm file
 * @param {string} skydataUrl - Base URL for sky data tiles
 */
async function initEngine(containerId, wasmUrl, skydataUrl) {
    const container = document.getElementById(containerId);
    if (!container) throw new Error(`Container ${containerId} not found`);

    // Create canvas
    stelCanvas = document.createElement('canvas');
    stelCanvas.style.width = '100%';
    stelCanvas.style.height = '100%';
    container.appendChild(stelCanvas);

    // Load and init the WASM module
    const Module = await import(wasmUrl);
    stelEngine = await Module.default({
        canvas: stelCanvas,
        skydataUrl: skydataUrl,
    });

    // Resize handler
    const resizeObserver = new ResizeObserver(() => {
        stelCanvas.width = container.clientWidth;
        stelCanvas.height = container.clientHeight;
    });
    resizeObserver.observe(container);

    // Forward mouse events to NiceGUI
    stelCanvas.addEventListener('click', (e) => {
        const rect = stelCanvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const coords = screenToWorld(x, y);
        if (coords) {
            emitEvent('map_click', { x, y, ra: coords.ra, dec: coords.dec });
        }
    });

    stelCanvas.addEventListener('mousemove', (e) => {
        const rect = stelCanvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        const coords = screenToWorld(x, y);
        if (coords) {
            emitEvent('map_mousemove', { x, y, ra: coords.ra, dec: coords.dec });
        }
    });

    return true;
}

/**
 * Convert screen pixel coordinates to celestial RA/Dec.
 */
function screenToWorld(x, y) {
    if (!stelEngine) return null;
    // Stellarium Web Engine API — exact method depends on build
    try {
        const pos = stelEngine.core.screenToWorld([x, y]);
        if (pos) {
            return { ra: pos[0], dec: pos[1] };
        }
    } catch (e) {
        console.warn('screenToWorld failed:', e);
    }
    return null;
}

/**
 * Convert celestial coordinates to screen pixel position.
 */
function worldToScreen(ra, dec) {
    if (!stelEngine) return null;
    try {
        const pos = stelEngine.core.worldToScreen([ra, dec]);
        if (pos) {
            return { x: pos[0], y: pos[1] };
        }
    } catch (e) {
        console.warn('worldToScreen failed:', e);
    }
    return null;
}

/**
 * Get current field of view in degrees.
 */
function getFieldOfView() {
    if (!stelEngine) return null;
    return stelEngine.core.fov;
}

/**
 * Set observer location and time.
 */
function setObserver(lat, lon, utcTime) {
    if (!stelEngine) return;
    stelEngine.core.observer.latitude = lat * Math.PI / 180;
    stelEngine.core.observer.longitude = lon * Math.PI / 180;
    if (utcTime) {
        stelEngine.core.observer.utc = utcTime;
    }
}

/**
 * Animate view to a position.
 */
function lookAt(ra, dec, fov, duration) {
    if (!stelEngine) return;
    // Convert RA/Dec to the engine's internal coordinate format
    stelEngine.core.lookat([ra, dec], duration || 1.0);
    if (fov) {
        stelEngine.core.zoomTo(fov, duration || 1.0);
    }
}

/**
 * Emit an event to the NiceGUI Python backend.
 */
function emitEvent(name, data) {
    // NiceGUI's JavaScript interop sends events via this pattern
    if (window.emitNiceGUIEvent) {
        window.emitNiceGUIEvent(name, data);
    }
}

// Export for use by NiceGUI
window.stelBridge = {
    initEngine,
    screenToWorld,
    worldToScreen,
    getFieldOfView,
    setObserver,
    lookAt,
};
```

- [ ] **Step 4: Create NiceGUI custom element wrapper**

```python
# src/starmap/__init__.py
```

```python
# src/starmap/engine.py
"""NiceGUI custom element wrapping the Stellarium Web Engine."""

from __future__ import annotations

from pathlib import Path

from nicegui import ui
from nicegui.element import Element


class StarMap(Element, component="div"):
    """Star map widget embedding Stellarium Web Engine."""

    def __init__(self) -> None:
        super().__init__()
        self._classes = ["map-container"]
        self.map_id = f"starmap-{id(self)}"
        self._setup_callbacks: list = []

        # Set the container ID
        self._props["id"] = self.map_id

        # Load the bridge script
        bridge_path = Path(__file__).parent / "bridge.js"
        if bridge_path.exists():
            ui.add_body_html(f"<script>{bridge_path.read_text()}</script>")

    async def initialize(
        self,
        wasm_url: str = "/static/stellarium/stellarium-web-engine.js",
        skydata_url: str = "/skydata/",
    ) -> None:
        """Initialize the Stellarium engine in this container."""
        await ui.run_javascript(
            f"await window.stelBridge.initEngine('{self.map_id}', '{wasm_url}', '{skydata_url}')"
        )

    async def look_at(self, ra: float, dec: float, fov: float = 10.0, duration: float = 1.0) -> None:
        await ui.run_javascript(
            f"window.stelBridge.lookAt({ra}, {dec}, {fov}, {duration})"
        )

    async def set_observer(self, lat: float, lon: float) -> None:
        await ui.run_javascript(
            f"window.stelBridge.setObserver({lat}, {lon}, null)"
        )

    def on_click(self, callback) -> None:
        """Register a callback for map clicks. Receives {ra, dec, x, y}."""
        self._setup_callbacks.append(("map_click", callback))

    def on_mousemove(self, callback) -> None:
        """Register a callback for mouse movement. Receives {ra, dec, x, y}."""
        self._setup_callbacks.append(("map_mousemove", callback))
```

- [ ] **Step 5: Commit**

```bash
git add scripts/install_stellarium.sh src/starmap/__init__.py src/starmap/engine.py src/starmap/bridge.js
git commit -m "feat: add Stellarium Web Engine integration with JS bridge"
```

---

## Task 8: Path Overlay (JavaScript)

**Files:**
- Create: `src/starmap/path_overlay.js`

- [ ] **Step 1: Implement path overlay**

```javascript
// src/starmap/path_overlay.js
// SVG overlay for spline path editing on top of the Stellarium canvas.
// Handles control points, Bézier handles, capture point markers, and hit testing.

class PathOverlay {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        this.svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:10;';
        this.container.style.position = 'relative';
        this.container.appendChild(this.svg);

        this.controlPoints = [];   // {ra, dec, handleIn?, handleOut?}
        this.capturePoints = [];   // {ra, dec, index}
        this.selectedPoint = null;
        this.dragging = null;
        this.mode = 'move';        // 'draw', 'move', 'add_point', 'split'

        this._setupInteraction();
    }

    /**
     * Set the current editing mode.
     */
    setMode(mode) {
        this.mode = mode;
        this.svg.style.pointerEvents = (mode === 'move') ? 'none' : 'all';
    }

    /**
     * Update the overlay from project data.
     * Called whenever control points or capture points change.
     */
    update(controlPoints, capturePoints) {
        this.controlPoints = controlPoints;
        this.capturePoints = capturePoints;
        this._render();
    }

    /**
     * Re-render all overlay elements based on current screen positions.
     * Must be called when the map view changes (pan/zoom).
     */
    _render() {
        // Clear SVG
        while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);

        if (this.controlPoints.length < 2) {
            this._renderControlPointDots();
            return;
        }

        // Draw path segments
        this._renderPath();
        // Draw Bézier handles
        this._renderHandles();
        // Draw capture points
        this._renderCapturePoints();
        // Draw control points (on top)
        this._renderControlPointDots();
    }

    _renderPath() {
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        let d = '';

        for (let i = 0; i < this.controlPoints.length - 1; i++) {
            const cp0 = this.controlPoints[i];
            const cp1 = this.controlPoints[i + 1];

            const p0 = window.stelBridge.worldToScreen(cp0.ra, cp0.dec);
            const h0 = cp0.handleOut
                ? window.stelBridge.worldToScreen(cp0.handleOut.ra, cp0.handleOut.dec)
                : p0;
            const h1 = cp1.handleIn
                ? window.stelBridge.worldToScreen(cp1.handleIn.ra, cp1.handleIn.dec)
                : window.stelBridge.worldToScreen(cp1.ra, cp1.dec);
            const p1 = window.stelBridge.worldToScreen(cp1.ra, cp1.dec);

            if (!p0 || !h0 || !h1 || !p1) continue;

            if (i === 0) d += `M ${p0.x} ${p0.y} `;
            d += `C ${h0.x} ${h0.y}, ${h1.x} ${h1.y}, ${p1.x} ${p1.y} `;
        }

        path.setAttribute('d', d);
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', 'rgba(237,137,54,0.6)');
        path.setAttribute('stroke-width', '2');
        path.setAttribute('stroke-dasharray', '6,4');
        this.svg.appendChild(path);
    }

    _renderHandles() {
        for (const cp of this.controlPoints) {
            const pScreen = window.stelBridge.worldToScreen(cp.ra, cp.dec);
            if (!pScreen) continue;

            if (cp.handleIn) {
                const h = window.stelBridge.worldToScreen(cp.handleIn.ra, cp.handleIn.dec);
                if (h) this._drawHandle(pScreen, h);
            }
            if (cp.handleOut) {
                const h = window.stelBridge.worldToScreen(cp.handleOut.ra, cp.handleOut.dec);
                if (h) this._drawHandle(pScreen, h);
            }
        }
    }

    _drawHandle(from, to) {
        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', from.x); line.setAttribute('y1', from.y);
        line.setAttribute('x2', to.x); line.setAttribute('y2', to.y);
        line.setAttribute('stroke', 'rgba(237,137,54,0.3)');
        line.setAttribute('stroke-width', '1');
        this.svg.appendChild(line);

        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', to.x); circle.setAttribute('cy', to.y);
        circle.setAttribute('r', '3');
        circle.setAttribute('fill', 'rgba(237,137,54,0.5)');
        circle.style.pointerEvents = 'all';
        circle.style.cursor = 'grab';
        this.svg.appendChild(circle);
    }

    _renderCapturePoints() {
        for (const cp of this.capturePoints) {
            const pos = window.stelBridge.worldToScreen(cp.ra, cp.dec);
            if (!pos) continue;

            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', pos.x); circle.setAttribute('cy', pos.y);
            circle.setAttribute('r', '3');
            circle.setAttribute('fill', '#63b3ed');
            circle.setAttribute('opacity', '0.8');
            this.svg.appendChild(circle);
        }
    }

    _renderControlPointDots() {
        for (let i = 0; i < this.controlPoints.length; i++) {
            const cp = this.controlPoints[i];
            const pos = window.stelBridge.worldToScreen(cp.ra, cp.dec);
            if (!pos) continue;

            const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            circle.setAttribute('cx', pos.x); circle.setAttribute('cy', pos.y);
            circle.setAttribute('r', '6');
            circle.setAttribute('fill', '#ed8936');
            circle.setAttribute('stroke', '#fff');
            circle.setAttribute('stroke-width', '1.5');
            circle.style.pointerEvents = 'all';
            circle.style.cursor = 'grab';
            circle.dataset.pointIndex = i;
            this.svg.appendChild(circle);
        }
    }

    _setupInteraction() {
        this.svg.addEventListener('mousedown', (e) => {
            if (this.mode === 'draw') {
                const coords = window.stelBridge.screenToWorld(e.offsetX, e.offsetY);
                if (coords) {
                    emitEvent('path_add_point', coords);
                }
            }
            // Drag handling for control points
            if (e.target.dataset && e.target.dataset.pointIndex !== undefined) {
                this.dragging = {
                    type: 'control_point',
                    index: parseInt(e.target.dataset.pointIndex),
                    element: e.target,
                };
                e.target.style.cursor = 'grabbing';
            }
        });

        document.addEventListener('mousemove', (e) => {
            if (!this.dragging) return;
            const rect = this.svg.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const coords = window.stelBridge.screenToWorld(x, y);
            if (coords) {
                emitEvent('path_move_point', {
                    index: this.dragging.index,
                    ra: coords.ra,
                    dec: coords.dec,
                });
            }
        });

        document.addEventListener('mouseup', () => {
            if (this.dragging) {
                if (this.dragging.element) {
                    this.dragging.element.style.cursor = 'grab';
                }
                emitEvent('path_point_moved', { index: this.dragging.index });
                this.dragging = null;
            }
        });
    }
}

// Global instance
window.pathOverlay = null;

function initPathOverlay(containerId) {
    window.pathOverlay = new PathOverlay(containerId);
    return true;
}

window.pathOverlayBridge = {
    init: initPathOverlay,
    setMode: (mode) => window.pathOverlay?.setMode(mode),
    update: (controlPoints, capturePoints) => window.pathOverlay?.update(controlPoints, capturePoints),
};
```

- [ ] **Step 2: Register overlay script in engine.py**

Add to `src/starmap/engine.py` in the `__init__` method, after the bridge script loading:

```python
        overlay_path = Path(__file__).parent / "path_overlay.js"
        if overlay_path.exists():
            ui.add_body_html(f"<script>{overlay_path.read_text()}</script>")
```

- [ ] **Step 3: Commit**

```bash
git add src/starmap/path_overlay.js src/starmap/engine.py
git commit -m "feat: add SVG path overlay for spline editing"
```

---

## Task 9: Capture View UI

**Files:**
- Create: `src/ui/capture_view.py`

- [ ] **Step 1: Implement capture mode UI**

```python
# src/ui/capture_view.py
"""Capture mode UI: progress display, pause/resume/cancel controls."""

from nicegui import ui

from src.capture.controller import CaptureController, CaptureState


class CaptureView:
    """UI overlay for the capture process."""

    def __init__(self) -> None:
        self.controller: CaptureController | None = None
        self._timer = None

        # UI elements (created in build())
        self._status_label = None
        self._point_label = None
        self._exposure_label = None
        self._remaining_label = None
        self._progress_bar = None
        self._pause_btn = None
        self._cancel_btn = None
        self._container = None

    def build(self) -> None:
        """Create the capture mode toolbar and progress panel."""
        self._container = ui.row().classes(
            "w-full items-center gap-4 px-4 py-2 bg-dark"
        )
        self._container.set_visibility(False)

        with self._container:
            self._status_label = ui.label("CAPTURE RUNNING").classes(
                "text-positive text-weight-bold"
            )

            ui.space()

            self._point_label = ui.label("Point: 0 / 0").classes("text-grey-4")
            self._exposure_label = ui.label("Exposure: 0 / 0").classes("text-grey-4")
            self._remaining_label = ui.label("Remaining: --").classes("text-grey-4")

            ui.space()

            self._pause_btn = ui.button("Pause", icon="pause", on_click=self._on_pause).props(
                "dense color=warning size=sm"
            )
            self._cancel_btn = ui.button("Cancel", icon="stop", on_click=self._on_cancel).props(
                "dense color=negative size=sm"
            )

        # Progress bar in bottom panel area
        self._progress_bar = ui.linear_progress(value=0).classes("w-full")
        self._progress_bar.set_visibility(False)

    def start(self, controller: CaptureController) -> None:
        """Show capture UI and start updating."""
        self.controller = controller
        self._container.set_visibility(True)
        self._progress_bar.set_visibility(True)
        self._timer = ui.timer(0.5, self._update)

    def stop(self) -> None:
        """Hide capture UI."""
        self._container.set_visibility(False)
        self._progress_bar.set_visibility(False)
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _update(self) -> None:
        """Periodic update of progress display."""
        if not self.controller:
            return

        c = self.controller
        total = len(c.project.capture_points)
        current = min(c.current_point_index + 1, total)

        state_text = {
            CaptureState.RUNNING: "CAPTURE RUNNING",
            CaptureState.PAUSED: "PAUSED",
            CaptureState.COMPLETED: "COMPLETED",
            CaptureState.CANCELLED: "CANCELLED",
            CaptureState.ERROR: f"ERROR: {c.last_error}",
        }
        self._status_label.text = state_text.get(c.state, str(c.state))

        if c.state == CaptureState.PAUSED:
            self._status_label.classes(replace="text-warning text-weight-bold")
            self._pause_btn.text = "Resume"
            self._pause_btn.props(replace="dense color=positive size=sm")
        else:
            self._status_label.classes(replace="text-positive text-weight-bold")
            self._pause_btn.text = "Pause"
            self._pause_btn.props(replace="dense color=warning size=sm")

        self._point_label.text = f"Point: {current} / {total}"
        self._progress_bar.value = current / total if total > 0 else 0

        if c.state in (CaptureState.COMPLETED, CaptureState.CANCELLED):
            self.stop()

    def _on_pause(self) -> None:
        if not self.controller:
            return
        if self.controller.state == CaptureState.PAUSED:
            self.controller.resume()
        else:
            self.controller.pause()

    def _on_cancel(self) -> None:
        if self.controller:
            self.controller.cancel()
```

- [ ] **Step 2: Commit**

```bash
git add src/ui/capture_view.py
git commit -m "feat: add capture mode UI with progress and pause/resume"
```

---

## Task 10: Wire Everything Together

**Files:**
- Modify: `src/main.py`
- Modify: `src/ui/layout.py`

- [ ] **Step 1: Create app state manager**

```python
# src/app_state.py
"""Application state: holds the current project and provides actions."""

from __future__ import annotations

import json
from pathlib import Path

from src.models.project import Project, CapturePoint
from src.models.spline import sample_points_along_spline
from src.indi.mock import MockINDIClient
from src.capture.controller import CaptureController


class AppState:
    """Singleton-ish state holder for the current session."""

    def __init__(self) -> None:
        self.project = Project()
        self.indi_client = MockINDIClient()  # Switch to real client when hardware available
        self.capture_controller: CaptureController | None = None
        self.output_dir = Path("./output")

    def update_capture_points(self) -> None:
        """Recalculate capture points from the current path and settings."""
        points = sample_points_along_spline(
            self.project.path,
            self.project.capture_settings.point_spacing_deg,
        )
        self.project.capture_points = [
            CapturePoint(index=i, ra=ra, dec=dec)
            for i, (ra, dec) in enumerate(points)
        ]

    def save_project(self, filepath: Path) -> None:
        filepath.write_text(self.project.model_dump_json(indent=2))

    def load_project(self, filepath: Path) -> None:
        self.project = Project.model_validate_json(filepath.read_text())

    async def start_capture(self) -> CaptureController:
        self.update_capture_points()
        self.capture_controller = CaptureController(
            project=self.project,
            indi_client=self.indi_client,
            output_dir=self.output_dir,
        )
        return self.capture_controller
```

- [ ] **Step 2: Update layout to wire components**

Update `src/ui/layout.py` to use AppState and connect toolbar actions:

```python
# src/ui/layout.py
"""Main page layout: toolbar, star map container, bottom panel."""

from nicegui import ui

from src.app_state import AppState
from src.ui.toolbar import create_toolbar
from src.ui.bottom_panel import create_bottom_panel
from src.ui.capture_view import CaptureView


def create_layout():
    """Build the main page with toolbar, map area, and bottom panel."""
    state = AppState()
    capture_view = CaptureView()

    ui.add_head_html("""
    <style>
        body { margin: 0; overflow: hidden; }
        .map-container {
            flex: 1;
            position: relative;
            background: #0a0a19;
            min-height: 0;
        }
    </style>
    """)

    with ui.column().classes("w-full h-screen no-wrap"):
        create_toolbar(state, capture_view)
        capture_view.build()

        # Star map container
        with ui.element("div").classes("map-container"):
            ui.label("Star map — install Stellarium Web Engine to enable").classes(
                "text-grey-6 absolute-center"
            )

        create_bottom_panel(state)
```

- [ ] **Step 3: Update toolbar with state bindings**

Update `src/ui/toolbar.py` to accept state and capture_view parameters and bind button actions.

- [ ] **Step 4: Update bottom panel with state bindings**

Update `src/ui/bottom_panel.py` to accept state parameter and bind settings to project model.

- [ ] **Step 5: Verify app runs end-to-end**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && timeout 5 python -m src.main || true`
Expected: App starts without errors

- [ ] **Step 6: Commit**

```bash
git add src/app_state.py src/ui/layout.py src/ui/toolbar.py src/ui/bottom_panel.py src/main.py
git commit -m "feat: wire app state, UI components, and capture flow together"
```

---

## Task 11: Project Save/Load

**Files:**
- Modify: `src/ui/toolbar.py`

- [ ] **Step 1: Add save/load dialogs to toolbar**

Wire the Save and Load buttons to file dialogs using NiceGUI's `ui.upload` and `app.download`. Save writes the current project as JSON, Load reads a JSON file and restores the project state.

- [ ] **Step 2: Test save/load manually**

Start the app, draw a path (or set control points via the bottom panel), save, reload the page, load the file. Verify the path and settings are restored.

- [ ] **Step 3: Commit**

```bash
git add src/ui/toolbar.py
git commit -m "feat: add project save/load functionality"
```

---

## Task 12: EKOS Sequence Export (Stub)

**Files:**
- Create: `src/export/__init__.py`
- Create: `src/export/ekos.py`
- Create: `tests/test_ekos_export.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_ekos_export.py
import pytest
from src.export.ekos import export_sequence
from src.models.project import (
    Project, SplinePath, ControlPoint, Coordinate,
    CaptureSettings, CapturePoint,
)


class TestEKOSExport:
    def test_export_produces_output(self, tmp_path):
        project = Project(
            project="test",
            path=SplinePath(
                control_points=[
                    ControlPoint(ra=0.0, dec=0.0, handle_out=Coordinate(ra=1.0, dec=0.0)),
                    ControlPoint(ra=3.0, dec=0.0, handle_in=Coordinate(ra=2.0, dec=0.0)),
                ],
            ),
            capture_points=[
                CapturePoint(index=0, ra=0.0, dec=0.0),
                CapturePoint(index=1, ra=1.5, dec=0.0),
                CapturePoint(index=2, ra=3.0, dec=0.0),
            ],
        )
        output = tmp_path / "sequence.esq"
        export_sequence(project, output)
        assert output.exists()
        content = output.read_text()
        assert len(content) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_ekos_export.py -v`

- [ ] **Step 3: Implement minimal EKOS export**

```python
# src/export/__init__.py
```

```python
# src/export/ekos.py
"""Export capture sequence for EKOS/KStars.

The EKOS .esq format is undocumented and version-dependent.
This generates a best-effort XML file based on observed .esq structure.
Format may need adjustment for specific EKOS versions.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from src.models.project import Project


def export_sequence(project: Project, output_path: Path) -> None:
    """Export the project's capture points as an EKOS-compatible sequence file."""
    root = ET.Element("SequenceQueue", version="2.0")

    # Global settings
    settings = ET.SubElement(root, "GuideDeviation", enabled="false")
    settings.text = "2"

    for point in project.capture_points:
        job = ET.SubElement(root, "Job")

        exposure = ET.SubElement(job, "Exposure")
        exposure.text = str(project.capture_settings.exposure_seconds)

        count = ET.SubElement(job, "Count")
        count.text = str(project.capture_settings.exposures_per_point)

        bin_elem = ET.SubElement(job, "Binning")
        bin_x = ET.SubElement(bin_elem, "X")
        bin_x.text = str(project.capture_settings.binning)
        bin_y = ET.SubElement(bin_elem, "Y")
        bin_y.text = str(project.capture_settings.binning)

        gain_elem = ET.SubElement(job, "Gain")
        gain_elem.text = str(project.capture_settings.gain)

        offset_elem = ET.SubElement(job, "Offset")
        offset_elem.text = str(project.capture_settings.offset)

        # Target coordinates
        coords = ET.SubElement(job, "Coordinates")
        ra = ET.SubElement(coords, "J2000RA")
        ra.text = f"{point.ra:.6f}"
        dec = ET.SubElement(coords, "J2000DE")
        dec.text = f"{point.dec:.6f}"

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(str(output_path), encoding="unicode", xml_declaration=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_ekos_export.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/export/__init__.py src/export/ekos.py tests/test_ekos_export.py
git commit -m "feat: add EKOS sequence export (best-effort XML format)"
```

---

## Task 13: Integration Test & End-to-End Smoke Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""End-to-end integration test: project → capture points → mock capture → manifest."""

import asyncio
import pytest
from pathlib import Path
from src.app_state import AppState
from src.models.project import ControlPoint, Coordinate, SplinePath, CaptureSettings


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_capture_workflow(self, tmp_path):
        """Complete workflow: create project, calculate points, run capture, verify manifest."""
        state = AppState()
        state.output_dir = tmp_path / "output"

        # Set up a path
        state.project.project = "integration-test"
        state.project.path = SplinePath(
            control_points=[
                ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=12.0, dec=40.5)),
                ControlPoint(ra=20.0, dec=35.0, handle_in=Coordinate(ra=18.0, dec=36.0)),
            ],
        )
        state.project.capture_settings = CaptureSettings(
            point_spacing_deg=2.0, exposure_seconds=1.0, exposures_per_point=1
        )

        # Calculate capture points
        state.update_capture_points()
        assert len(state.project.capture_points) >= 3

        # Connect mock INDI and run capture
        await state.indi_client.connect("localhost", 7624)
        controller = await state.start_capture()
        await controller.run()

        # Verify all points captured
        for point in state.project.capture_points:
            assert point.status == "captured"
            assert len(point.files) == 1

        # Verify FITS files exist
        output_files = list(state.output_dir.glob("*.fits"))
        assert len(output_files) == len(state.project.capture_points)

        # Verify manifest written
        manifest = state.output_dir / "manifest.json"
        assert manifest.exists()

    @pytest.mark.asyncio
    async def test_save_load_roundtrip(self, tmp_path):
        """Save a project, load it back, verify equality."""
        state = AppState()
        state.project.project = "roundtrip-test"
        state.project.path = SplinePath(
            control_points=[
                ControlPoint(ra=10.0, dec=40.0, handle_out=Coordinate(ra=11.0, dec=40.5)),
                ControlPoint(
                    ra=15.0, dec=38.0,
                    handle_in=Coordinate(ra=14.0, dec=39.0),
                    handle_out=Coordinate(ra=16.0, dec=37.0),
                ),
                ControlPoint(ra=20.0, dec=35.0, handle_in=Coordinate(ra=19.0, dec=36.0)),
            ],
        )
        state.project.capture_settings.exposure_seconds = 45.0
        state.project.capture_settings.gain = 200

        filepath = tmp_path / "project.json"
        state.save_project(filepath)

        state2 = AppState()
        state2.load_project(filepath)
        assert state2.project.project == "roundtrip-test"
        assert len(state2.project.path.control_points) == 3
        assert state2.project.capture_settings.exposure_seconds == 45.0
        assert state2.project.capture_settings.gain == 200
        assert state2.project.path.control_points[1].handle_in.ra == 14.0
```

- [ ] **Step 2: Run integration tests**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/test_integration.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /home/phil/dev/astro/nicegui/sequence-planner && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "feat: add integration tests for full capture workflow"
```

---

## Summary

| Task | Component | Tests |
|------|-----------|-------|
| 1 | Project data models (Pydantic) | test_models.py |
| 2 | Spline math (Bézier evaluation, sampling) | test_spline.py |
| 3 | INDI client interface + mock | test_indi_client.py |
| 4 | FITS writer | test_fits_writer.py |
| 5 | Capture controller state machine | test_capture_controller.py |
| 6 | NiceGUI app shell & layout | Manual verification |
| 7 | Stellarium Web Engine integration | Manual verification |
| 8 | Path overlay (JavaScript) | Manual verification |
| 9 | Capture view UI | Manual verification |
| 10 | Wire everything together | Manual verification |
| 11 | Project save/load | Manual verification |
| 12 | EKOS export | test_ekos_export.py |
| 13 | Integration test | test_integration.py |

**Total: 13 tasks, ~65 steps, 7 test files**
