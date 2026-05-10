# Annotations and Overlays Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistent labels (manual + catalog-derived) drawn into rendered video frames whose underlying sky position is in view, defined and edited via the renderer UI.

**Architecture:** Labels live in the project's `manifest.json` as a `list[Label]` keyed to a reference frame's pixel space. The render pipeline draws them per frame via PIL, using the existing alignment chain to track positions. The UI hosts a list panel with click-to-add on the preview.

**Tech Stack:** Pydantic 2 (data model), PIL.ImageDraw (rendering), NiceGUI (UI), pytest (tests). Existing alignment infrastructure (`src/renderer/alignment.py`, `transitions.py`).

**Spec:** `docs/superpowers/specs/2026-05-10-annotations-overlays-design.md`

**Epic:** [#127 Annotations and Overlays](https://github.com/zworkb/AstroNightcrawler/issues/127)

**Test invocation note:** all tests must run with `PYTHON_GIL=0` because the project is on free-threaded Python 3.13t (some C-extensions request GIL re-enable on import — `PYTHON_GIL=0` overrides).

---

## File Structure

| File | Responsibility | Status |
|------|---------------|--------|
| `src/models/project.py` | `Label` model + `Project.labels` + `Project.north_angle_deg` | modify |
| `src/config.py` | `pixel_scale_arcsec` setting | modify |
| `src/renderer/labels.py` | Pure helpers: cumulative offset, label projection, catalog→pixel, PIL `_draw_labels` | create |
| `src/renderer/pipeline.py` | `RenderConfig.render_labels` + invoke `_draw_labels` in `_render_to_dir` | modify |
| `src/renderer/cli.py` | `--no-labels` flag | modify |
| `src/renderer/ui/render_layout.py` | Labels panel + click-to-add + catalog-add | modify |
| `tests/test_models_label.py` | Label round-trip + Project.labels backward-compat | create |
| `tests/test_labels_coordinates.py` | Pure-math helpers | create |
| `tests/test_labels_render.py` | `_draw_labels` PIL output | create |

---

## Task 1: Data model

Add the `Label` Pydantic model and the new fields on `Project`. Make sure round-trip serialization works and old manifests without the `labels` key still load.

**Files:**
- Modify: `src/models/project.py`
- Create: `tests/test_models_label.py`

- [ ] **Step 1.1: Read the existing `Project` model**

Run: `grep -n "class Project\|class CapturePoint\|class Coordinate" src/models/project.py`

Note where to insert `Label` (alphabetically near other small classes is fine; placement before `Project` is required so `Project` can reference it).

- [ ] **Step 1.2: Write the failing test**

Create `tests/test_models_label.py`:

```python
"""Tests for the Label model and Project.labels field."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    Label,
    Project,
    SplinePath,
)


def test_label_minimal_construction():
    """A Label needs id, text, ref_frame_index, x, y; the rest have defaults."""
    label = Label(id="abc-123", text="M27", ref_frame_index=0, x=100.5, y=200.0)
    assert label.color == "#ffff00"
    assert label.font_size == 24
    assert label.marker == "dot"
    assert label.source == "manual"
    assert label.catalog_ra is None


def test_label_validation_rejects_negative_ref_frame_index():
    with pytest.raises(ValidationError):
        Label(id="x", text="t", ref_frame_index=-1, x=0.0, y=0.0)


def test_label_round_trip_through_json():
    """Pydantic JSON round-trip preserves every field."""
    original = Label(
        id="uuid-1",
        text="catalog hit",
        ref_frame_index=3,
        x=512.0,
        y=384.5,
        color="#00ffff",
        font_size=32,
        marker="circle",
        text_offset_x=20,
        text_offset_y=-5,
        source="catalog",
        catalog_ra=299.901,
        catalog_dec=22.721,
        catalog_id="M27",
    )
    rebuilt = Label.model_validate_json(original.model_dump_json())
    assert rebuilt == original


def test_project_labels_defaults_to_empty_list():
    project = Project(
        project="p",
        path=SplinePath(control_points=[]),
    )
    assert project.labels == []
    assert project.north_angle_deg == 0.0


def test_project_loads_old_manifest_without_labels_key():
    """Backward compat: manifests written before this feature must still load."""
    old_manifest = {
        "version": "1.0",
        "created": "2026-01-01T00:00:00",
        "project": "old",
        "path": {"control_points": [], "spline_type": "cubic_bezier", "coordinate_frame": "J2000"},
        "capture_settings": {},
        "capture_points": [],
        "indi": None,
    }
    project = Project.model_validate(old_manifest)
    assert project.labels == []
    assert project.north_angle_deg == 0.0


def test_project_round_trip_with_labels():
    label = Label(id="u1", text="t", ref_frame_index=0, x=10.0, y=20.0)
    project = Project(
        project="p",
        path=SplinePath(control_points=[]),
        labels=[label],
        north_angle_deg=1.5,
    )
    rebuilt = Project.model_validate_json(project.model_dump_json())
    assert rebuilt.labels == [label]
    assert rebuilt.north_angle_deg == 1.5
```

- [ ] **Step 1.3: Run the tests; expect failure**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_models_label.py -q`
Expected: FAIL, ImportError on `Label` (not yet defined).

- [ ] **Step 1.4: Add the `Label` model**

In `src/models/project.py`, find the existing imports and ensure `Literal` is available. Add (above the `Project` class):

```python
from typing import Literal


class Label(BaseModel):
    """A single annotation drawn into rendered frames.

    Position is stored in the pixel space of one chosen reference frame.
    Tracking across other frames uses the renderer's alignment chain.
    """

    id: str = Field(description="UUID4 — stable across edits")
    text: str = Field(description="Display text")

    ref_frame_index: int = Field(
        ge=0,
        description="Which capture frame's pixel space holds (x, y)",
    )
    x: float = Field(description="Pixel-x in reference frame; sub-pixel allowed")
    y: float = Field(description="Pixel-y in reference frame")

    color: str = Field(default="#ffff00", description="CSS hex; text + marker share")
    font_size: int = Field(default=24, ge=6, le=200)
    marker: Literal["none", "dot", "cross", "circle"] = Field(default="dot")
    text_offset_x: int = Field(default=12, description="Text offset from marker in px")
    text_offset_y: int = Field(default=0)

    source: Literal["manual", "catalog"] = Field(default="manual")
    catalog_ra: float | None = Field(default=None, description="Original RA in degrees (catalog only)")
    catalog_dec: float | None = Field(default=None, description="Original Dec in degrees (catalog only)")
    catalog_id: str | None = Field(default=None, description="Source identifier, e.g. 'M27'")
```

- [ ] **Step 1.5: Add the new `Project` fields**

In the existing `Project` class (in `src/models/project.py`), add:

```python
    labels: list[Label] = Field(default_factory=list)
    north_angle_deg: float = Field(
        default=0.0,
        description="Sky orientation correction in degrees; 0° = north up. "
                    "Per-project because mount alignment quirks vary by session.",
    )
```

- [ ] **Step 1.6: Run the tests; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_models_label.py -q`
Expected: PASS, all 6 tests green.

- [ ] **Step 1.7: Run the full test suite to confirm no regressions**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: previous count + 6 = at least 155 passed.

- [ ] **Step 1.8: Commit**

```bash
git add src/models/project.py tests/test_models_label.py
git commit -m "feat(models): add Label model and Project.labels / north_angle_deg

Persisted in manifest.json. Backward-compatible: old manifests load
with an empty labels list. Refs #127."
```

---

## Task 2: Coordinate helpers

Pure functions for cumulative alignment offset, projecting a label into a frame's pixel space, and converting catalog RA/Dec to reference-frame pixel coordinates. No UI, no rendering. Full TDD.

**Files:**
- Create: `src/renderer/labels.py`
- Create: `tests/test_labels_coordinates.py`

- [ ] **Step 2.1: Write the failing test for `cumulative_offset`**

Create `tests/test_labels_coordinates.py`:

```python
"""Tests for label coordinate helpers (pure functions)."""

from __future__ import annotations

import math

from src.renderer.alignment import AlignmentResult
from src.renderer.labels import (
    catalog_to_ref_pixel,
    cumulative_offset,
    project_label_to_frame,
)


def _aln(dx: float, dy: float) -> AlignmentResult:
    return AlignmentResult(dx=dx, dy=dy, rotation=0.0, success=True)


def test_cumulative_offset_zero_when_same_index():
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    assert cumulative_offset(alignments, 1, 1) == (0.0, 0.0)


def test_cumulative_offset_forward():
    """alignments[i] is the shift from frame i to frame i+1."""
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0), _aln(5.0, 7.0)]
    # ref=0, target=2: sum of alignments[0] + alignments[1] = (30, 2)
    assert cumulative_offset(alignments, 0, 2) == (30.0, 2.0)


def test_cumulative_offset_backward():
    """Going backward negates each contributing offset."""
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    # ref=2, target=0: -(alignments[0] + alignments[1]) = (-30, -2)
    assert cumulative_offset(alignments, 2, 0) == (-30.0, -2.0)
```

- [ ] **Step 2.2: Run the test; expect failure**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py -q`
Expected: FAIL, ImportError on `src.renderer.labels`.

- [ ] **Step 2.3: Create `src/renderer/labels.py` with `cumulative_offset`**

```python
"""Label coordinate helpers and rendering primitives.

All position math lives here so it can be unit-tested without the
NiceGUI runtime, and so the pipeline / UI / preview share one source
of truth.
"""

from __future__ import annotations

from src.renderer.alignment import AlignmentResult


def cumulative_offset(
    alignments: list[AlignmentResult],
    from_index: int,
    to_index: int,
) -> tuple[float, float]:
    """Sum (dx, dy) contributions to walk from one frame index to another.

    ``alignments[i]`` is the shift from frame ``i`` to frame ``i+1`` —
    the same convention used by ``align_pair`` and ``linear_pan`` in
    the existing pipeline.

    Args:
        alignments: Pair-wise alignment results, length = N - 1 for N frames.
        from_index: Source frame index.
        to_index: Destination frame index.

    Returns:
        ``(dx, dy)`` to add to a pixel position in frame ``from_index``
        to get the equivalent position in frame ``to_index``.
    """
    if from_index == to_index:
        return (0.0, 0.0)
    if from_index < to_index:
        sign = 1.0
        lo, hi = from_index, to_index
    else:
        sign = -1.0
        lo, hi = to_index, from_index
    dx = sum(alignments[i].dx for i in range(lo, hi))
    dy = sum(alignments[i].dy for i in range(lo, hi))
    return (sign * dx, sign * dy)
```

- [ ] **Step 2.4: Run the test; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py::test_cumulative_offset_zero_when_same_index tests/test_labels_coordinates.py::test_cumulative_offset_forward tests/test_labels_coordinates.py::test_cumulative_offset_backward -q`
Expected: PASS, 3 tests.

- [ ] **Step 2.5: Add the failing test for `project_label_to_frame`**

Append to `tests/test_labels_coordinates.py`:

```python
from src.models.project import Label


def _label(ref_idx: int, x: float, y: float) -> Label:
    return Label(id="test", text="t", ref_frame_index=ref_idx, x=x, y=y)


def test_project_label_to_frame_identity():
    """Label on its own ref frame projects to its own (x, y)."""
    label = _label(ref_idx=0, x=100.0, y=200.0)
    alignments = [_aln(10.0, 5.0), _aln(20.0, -3.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 0, alignments, frame_dims)
    assert (px, py) == (100.0, 200.0)
    assert in_view is True


def test_project_label_to_frame_subtracts_cumulative_offset():
    """If the image shifted by (dx, dy) from ref to current frame, the
    static sky-point label appears at (x - dx, y - dy) in the current."""
    label = _label(ref_idx=0, x=100.0, y=200.0)
    alignments = [_aln(10.0, 5.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 1, alignments, frame_dims)
    assert (px, py) == (90.0, 195.0)
    assert in_view is True


def test_project_label_to_frame_out_of_bounds():
    label = _label(ref_idx=0, x=10.0, y=10.0)
    alignments = [_aln(2000.0, 0.0)]
    frame_dims = (1280, 720)
    px, py, in_view = project_label_to_frame(label, 1, alignments, frame_dims)
    assert in_view is False  # px = 10 - 2000 = -1990
```

- [ ] **Step 2.6: Run; expect failure**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py -q`
Expected: FAIL, ImportError on `project_label_to_frame`.

- [ ] **Step 2.7: Implement `project_label_to_frame`**

Append to `src/renderer/labels.py`:

```python
from src.models.project import Label


def project_label_to_frame(
    label: Label,
    frame_index: int,
    alignments: list[AlignmentResult],
    frame_dims: tuple[int, int],
) -> tuple[float, float, bool]:
    """Compute the pixel position of a label in an arbitrary frame.

    Args:
        label: The label whose position is anchored in ``label.ref_frame_index``.
        frame_index: Which frame to project into.
        alignments: Pair-wise alignment chain (see ``cumulative_offset``).
        frame_dims: ``(width, height)`` of the current frame in pixels.

    Returns:
        ``(px, py, in_view)`` — the projected pixel position and
        whether the position lies within the frame's bounds.
    """
    dx, dy = cumulative_offset(alignments, label.ref_frame_index, frame_index)
    px = label.x - dx
    py = label.y - dy
    width, height = frame_dims
    in_view = 0.0 <= px < width and 0.0 <= py < height
    return (px, py, in_view)
```

- [ ] **Step 2.8: Run; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py -q`
Expected: PASS, 6 tests.

- [ ] **Step 2.9: Add the failing test for `catalog_to_ref_pixel`**

Append:

```python
def test_catalog_to_ref_pixel_center_maps_to_frame_center():
    """A catalog point at the same RA/Dec as the frame center lands at the
    frame's center pixel."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=45.0,
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(1280, 720),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    assert math.isclose(x, 640.0)
    assert math.isclose(y, 360.0)


def test_catalog_to_ref_pixel_eastward_offset_moves_left_in_pixels():
    """East = +RA on sky; in image space (north up), east is to the left
    (mirror of standard plate convention since the image already has
    pixel x increasing rightward = west)."""
    # 1° = 3600 arcsec; at 1 arcsec/px that's 3600 px shift
    x, y = catalog_to_ref_pixel(
        ra_deg=181.0,  # 1° east of center
        dec_deg=45.0,
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    # cos(45°) compensation on RA: 1° on sky = cos(45°) ≈ 0.707° in projected angle
    expected_offset = -3600.0 * math.cos(math.radians(45.0))
    assert math.isclose(x, 4000.0 + expected_offset, abs_tol=0.5)
    assert math.isclose(y, 3000.0, abs_tol=0.5)


def test_catalog_to_ref_pixel_northward_offset_moves_up_in_pixels():
    """+Dec = north; in image space (north up), north is upward = pixel y decreasing."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=46.0,  # 1° north
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=0.0,
    )
    assert math.isclose(x, 4000.0, abs_tol=0.5)
    assert math.isclose(y, 3000.0 - 3600.0, abs_tol=0.5)


def test_catalog_to_ref_pixel_orientation_rotation():
    """A 90° north_angle_deg rotates so that 'north on sky' lands rightward in pixels."""
    x, y = catalog_to_ref_pixel(
        ra_deg=180.0,
        dec_deg=46.0,  # 1° north
        frame_center_ra_deg=180.0,
        frame_center_dec_deg=45.0,
        frame_dims=(8000, 6000),
        pixel_scale_arcsec=1.0,
        north_angle_deg=90.0,
    )
    # 90° rotation: north → +x in pixels
    assert math.isclose(x, 4000.0 + 3600.0, abs_tol=0.5)
    assert math.isclose(y, 3000.0, abs_tol=0.5)
```

- [ ] **Step 2.10: Run; expect failure**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py -q`
Expected: FAIL, ImportError on `catalog_to_ref_pixel`.

- [ ] **Step 2.11: Implement `catalog_to_ref_pixel`**

Append to `src/renderer/labels.py`:

```python
import math


def catalog_to_ref_pixel(
    ra_deg: float,
    dec_deg: float,
    frame_center_ra_deg: float,
    frame_center_dec_deg: float,
    frame_dims: tuple[int, int],
    pixel_scale_arcsec: float,
    north_angle_deg: float = 0.0,
) -> tuple[float, float]:
    """Approximate sky → reference-frame pixel projection.

    Uses a flat tangent-plane approximation valid for small fields
    (capture areas of a few degrees). RA differences are scaled by
    cos(dec) per standard celestial convention. The result is rotated
    by ``north_angle_deg`` to account for mount alignment offsets;
    0° means north points up in pixel space.

    Pixel convention: x increases rightward (= west on a north-up
    plate), y increases downward. North up means +Dec maps to
    decreasing y.

    Args:
        ra_deg: Catalog object's RA in degrees.
        dec_deg: Catalog object's Dec in degrees.
        frame_center_ra_deg: Reference frame's center RA from the manifest.
        frame_center_dec_deg: Reference frame's center Dec from the manifest.
        frame_dims: ``(width, height)`` of the reference frame in pixels.
        pixel_scale_arcsec: Arcseconds per pixel from the optical setup.
        north_angle_deg: Image-plane rotation; 0° = north up.

    Returns:
        ``(x, y)`` in reference-frame pixel coordinates.
    """
    width, height = frame_dims
    cx = width / 2.0
    cy = height / 2.0

    # Sky-plane offsets in arcseconds.
    cos_dec = math.cos(math.radians(frame_center_dec_deg))
    delta_ra_arcsec = (ra_deg - frame_center_ra_deg) * 3600.0 * cos_dec
    delta_dec_arcsec = (dec_deg - frame_center_dec_deg) * 3600.0

    # In the image plane (pixel space), north up means:
    #   +Dec → -y, +RA (east) → -x.
    # The catalog deltas above are sky-east-positive, sky-north-positive.
    sky_east = -delta_ra_arcsec / pixel_scale_arcsec   # east → -x → flipped
    sky_north = -delta_dec_arcsec / pixel_scale_arcsec  # north → -y → flipped

    # Apply rotation if mount is not aligned to celestial north.
    theta = math.radians(north_angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    # Rotation acts on (sky_east, sky_north) components in pixel space.
    rotated_x = cos_t * sky_east - sin_t * sky_north
    rotated_y = sin_t * sky_east + cos_t * sky_north

    return (cx + rotated_x, cy + rotated_y)
```

- [ ] **Step 2.12: Run; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_coordinates.py -q`
Expected: PASS, 10 tests.

- [ ] **Step 2.13: Run full suite to confirm no regressions**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: at least 165 passed (previous 155 + 10 new).

- [ ] **Step 2.14: Commit**

```bash
git add src/renderer/labels.py tests/test_labels_coordinates.py
git commit -m "feat(renderer): label coordinate helpers (offset, projection, catalog)

Pure functions, no I/O. Provide the math underpinning every label
draw call in the pipeline and UI. Refs #127."
```

---

## Task 3: Render integration

PIL-based `_draw_labels` plus pipeline / config wiring + CLI flag. After this, a hand-edited `manifest.json` with labels produces a video that includes them.

**Files:**
- Modify: `src/renderer/labels.py` (add `_draw_labels`)
- Modify: `src/config.py` (add `pixel_scale_arcsec`)
- Modify: `src/renderer/pipeline.py` (config field + invoke draw)
- Modify: `src/renderer/cli.py` (`--no-labels`)
- Create: `tests/test_labels_render.py`

- [ ] **Step 3.1: Add `pixel_scale_arcsec` to `Settings`**

In `src/config.py`, after `render_workers`:

```python
    pixel_scale_arcsec: float = Field(
        default=1.0,
        description="Arcseconds per pixel of the optical setup. "
                    "Used to convert catalog RA/Dec to reference-frame pixels. "
                    "Override via NC_PIXEL_SCALE_ARCSEC.",
    )
```

(`Field` is already imported in the file per the recent change; verify with the current source.)

- [ ] **Step 3.2: Write the failing test for `_draw_labels`**

Create `tests/test_labels_render.py`:

```python
"""Tests for the PIL-based _draw_labels helper."""

from __future__ import annotations

import numpy as np
import pytest

from src.models.project import Label
from src.renderer.labels import _draw_labels


def _blank(width: int = 200, height: int = 100) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_draw_labels_no_labels_returns_unchanged():
    img = _blank()
    out = _draw_labels(img, labels=[], offsets=[], frame_dims=(200, 100))
    assert np.array_equal(out, img)


def test_draw_labels_in_view_changes_pixels():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=100.0, y=50.0,
                  color="#ffffff", marker="dot")
    out = _draw_labels(img, labels=[label], offsets=[(0.0, 0.0)],
                       frame_dims=(200, 100))
    assert not np.array_equal(out, img)
    # the dot at the label position should be non-zero
    assert out[50, 100].sum() > 0


def test_draw_labels_out_of_view_leaves_image_unchanged():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=10.0, y=10.0)
    # cumulative offset of 1000 px shoves the label out of frame
    out = _draw_labels(img, labels=[label], offsets=[(1000.0, 0.0)],
                       frame_dims=(200, 100))
    assert np.array_equal(out, img)


def test_draw_labels_marker_none_still_draws_text():
    img = _blank()
    label = Label(id="a", text="HELLO", ref_frame_index=0, x=20.0, y=50.0,
                  marker="none", color="#ffffff")
    out = _draw_labels(img, labels=[label], offsets=[(0.0, 0.0)],
                       frame_dims=(200, 100))
    # text should leave SOME non-zero pixels in the area to the right of (20, 50)
    region = out[30:70, 20:200]
    assert region.sum() > 0


def test_draw_labels_offsets_length_must_match_labels():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=0.0, y=0.0)
    with pytest.raises(ValueError):
        _draw_labels(img, labels=[label], offsets=[],
                     frame_dims=(200, 100))
```

- [ ] **Step 3.3: Run; expect failure**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_render.py -q`
Expected: FAIL, ImportError on `_draw_labels`.

- [ ] **Step 3.4: Implement `_draw_labels`**

Append to `src/renderer/labels.py`:

```python
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from src.models.project import Label


def _resolve_font(size: int) -> ImageFont.ImageFont:
    """Load a sensible default font; fall back to the bitmap default.

    PIL's default font is a tiny bitmap; for legible labels we want
    a TrueType font. The DejaVu set ships with most Linux distros and
    is what NiceGUI's filmstrip thumbs already implicitly rely on.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_marker(
    draw: ImageDraw.ImageDraw,
    px: float,
    py: float,
    marker: str,
    color: str,
    size: int = 6,
) -> None:
    """Draw the marker glyph in-place on the given draw context."""
    if marker == "none":
        return
    s = size
    if marker == "dot":
        draw.ellipse([(px - s / 2, py - s / 2), (px + s / 2, py + s / 2)],
                     fill=color)
    elif marker == "cross":
        draw.line([(px - s, py), (px + s, py)], fill=color, width=2)
        draw.line([(px, py - s), (px, py + s)], fill=color, width=2)
    elif marker == "circle":
        draw.ellipse([(px - s, py - s), (px + s, py + s)],
                     outline=color, width=2)


def _draw_labels(
    frame: np.ndarray,
    labels: list[Label],
    offsets: list[tuple[float, float]],
    frame_dims: tuple[int, int],
) -> np.ndarray:
    """Draw labels in-place via PIL. Returns the same array (possibly
    converted dtype if PIL forced a copy).

    Args:
        frame: 8-bit RGB numpy array (H, W, 3).
        labels: One Label per element of ``offsets``.
        offsets: ``(dx, dy)`` cumulative offset from each label's
            ``ref_frame_index`` to the current frame. Length must
            equal ``len(labels)``.
        frame_dims: ``(width, height)`` matching the array's shape.

    Returns:
        The annotated frame (uint8 RGB).
    """
    if len(labels) != len(offsets):
        msg = f"labels ({len(labels)}) and offsets ({len(offsets)}) length mismatch"
        raise ValueError(msg)
    if not labels:
        return frame

    pil = Image.fromarray(frame)
    draw = ImageDraw.Draw(pil)

    width, height = frame_dims
    for label, (dx, dy) in zip(labels, offsets, strict=True):
        px = label.x - dx
        py = label.y - dy
        if not (0.0 <= px < width and 0.0 <= py < height):
            continue
        _draw_marker(draw, px, py, label.marker, label.color)
        if label.text:
            font = _resolve_font(label.font_size)
            tx = px + label.text_offset_x
            ty = py + label.text_offset_y
            draw.text((tx, ty), label.text, fill=label.color, font=font)

    return np.array(pil)
```

- [ ] **Step 3.5: Run; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/test_labels_render.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 3.6: Add `RenderConfig.render_labels`**

In `src/renderer/pipeline.py`, in the `RenderConfig` dataclass (after `auto_stretch_params`):

```python
    render_labels: bool = True
```

- [ ] **Step 3.7: Inspect the current pipeline structure**

Before wiring, read these regions of `src/renderer/pipeline.py` to identify exact insertion points and confirm signatures:
- The `RenderPipeline` class header (`__init__`, `load`)
- `_render_to_dir`: find the `write_frame_png(current_stretched, ...)` call (keyframe write when no transitions) and the "Write last key frame" block near the end
- `_process_transition_pair`: the worker that streams transition frames to disk one by one

The wiring lives in three places: keyframe-no-transitions, final-keyframe, and inside the per-transition-frame stream. Note their line numbers — you'll need them in Steps 3.8–3.10.

- [ ] **Step 3.8: Expose the project on `RenderPipeline` and add the offset helper**

Add to `RenderPipeline.__init__`:

```python
        self.project: Project | None = None
```

Replace `RenderPipeline.load()` with:

```python
    def load(self) -> None:
        """Load manifest and frame metadata."""
        manifest_path = self.capture_dir / "manifest.json"
        self.project = Project.model_validate_json(manifest_path.read_text())
        self.frames = load_manifest(self.capture_dir)
        logger.info("Loaded %d frames", len(self.frames))
```

Add at the top of `pipeline.py`:

```python
from src.models.project import Label, Project
from src.renderer.labels import _draw_labels, cumulative_offset
```

Add a small helper as a method on `RenderPipeline` (alongside `effective_crossfade_frames`):

```python
    def _label_offsets(
        self,
        labels: list[Label],
        frame_index: int,
    ) -> list[tuple[float, float]]:
        """Cumulative offsets per label from its ref frame to the given frame."""
        return [
            cumulative_offset(self._alignments, label.ref_frame_index, frame_index)
            for label in labels
        ]
```

- [ ] **Step 3.9: Wire keyframe + final-keyframe writes**

In `_render_to_dir`, near the top of the streaming section (just before the per-frame `for i in range(len(active)):` loop), fetch the labels list once:

```python
        labels = (
            self.project.labels if (self.project and self.config.render_labels)
            else []
        )
```

Find the existing keyframe-no-transitions write:

```python
                if not has_transitions:
                    write_frame_png(current_stretched, temp, frame_counter)
```

Wrap the array immediately before it:

```python
                if not has_transitions:
                    if labels:
                        current_stretched = _draw_labels(
                            current_stretched, labels,
                            self._label_offsets(labels, i),
                            (current_stretched.shape[1], current_stretched.shape[0]),
                        )
                    write_frame_png(current_stretched, temp, frame_counter)
```

Find the "Write last key frame" block and wrap `last` the same way (frame index is `len(active) - 1`):

```python
        if has_transitions and prev_stretched is not None:
            if is_pan:
                h, w = prev_stretched.shape[:2]
                crop_h = h - 2 * my if my else h
                crop_w = w - 2 * mx if mx else w
                last = prev_stretched[my:my + crop_h, mx:mx + crop_w]
            else:
                last = prev_stretched
            if labels:
                last = _draw_labels(
                    last, labels,
                    self._label_offsets(labels, len(active) - 1),
                    (last.shape[1], last.shape[0]),
                )
            final_idx = (len(active) - 1) * self.effective_crossfade_frames
            write_frame_png(last, temp, final_idx)
            frame_counter = max(frame_counter, final_idx + 1)
            render_progress.increment()
```

- [ ] **Step 3.10: Wire labels into `_process_transition_pair`**

Transitions run in worker threads. The cleanest spot is inside `_process_transition_pair`, just before each `write_frame_png(tf, ...)` call. The transition for `pair_idx` interpolates between keyframes `pair_idx` and `pair_idx + 1`; the per-frame parameter is `t = (offset + 1) / N` where `N = self.effective_crossfade_frames`.

Replace the body of `_process_transition_pair`:

```python
    def _process_transition_pair(
        self,
        prev_stretched: np.ndarray,
        current_stretched: np.ndarray,
        pair_idx: int,
        margins: tuple[int, int],
        temp: Path,
        start_frame_number: int,
        progress: _PhaseProgress,
    ) -> int:
        """Worker: generate transition frames for one pair and write them."""
        labels = (
            self.project.labels if (self.project and self.config.render_labels)
            else []
        )
        n = self.effective_crossfade_frames
        # Pre-compute the two endpoint offsets for each label so the
        # per-frame loop only does a small interpolation.
        if labels:
            offs_a = self._label_offsets(labels, pair_idx)
            offs_b = self._label_offsets(labels, pair_idx + 1)
        count = 0
        for offset, tf in enumerate(self._make_transition_pair(
            prev_stretched, current_stretched, pair_idx, margins,
        )):
            if labels:
                t = (offset + 1) / n
                interp = [
                    (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                    for a, b in zip(offs_a, offs_b, strict=True)
                ]
                tf = _draw_labels(
                    tf, labels, interp,
                    (tf.shape[1], tf.shape[0]),
                )
            write_frame_png(tf, temp, start_frame_number + offset)
            progress.increment()
            count += 1
        return count
```

- [ ] **Step 3.11: Add the CLI flag**

In `src/renderer/cli.py`, in `parse_args`, add:

```python
    p.add_argument(
        "--no-labels", action="store_true",
        help="Render without labels (overrides manifest's labels list)",
    )
```

In `_build_config`, propagate:

```python
        render_labels=not args.no_labels,
```

- [ ] **Step 3.12: Run all tests; expect pass**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: at least 170 passed (previous 165 + 5 new).

- [ ] **Step 3.13: Smoke test — synthetic render with one label**

Create `/tmp/label-smoke.py`:

```python
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from astropy.io import fits

# Put a hand-built manifest with one label into a temp dir,
# create two trivial FITS frames with a single bright star,
# then run the pipeline and check the output.
sys.path.insert(0, "/home/phil/dev/astro/nicegui/sequence-planner")
from src.renderer.pipeline import RenderConfig, RenderPipeline

tmp = Path(tempfile.mkdtemp(prefix="label-smoke-"))
print("smoke dir:", tmp)

# Two synthetic FITS files with a 4168x6224 black field + a bright pixel
for i in range(2):
    data = np.zeros((4168, 6224), dtype=np.uint16)
    data[2000 + i * 5, 3000 + i * 5] = 60000
    hdu = fits.PrimaryHDU(data)
    hdu.header["BAYERPAT"] = "RGGB"
    hdu.writeto(tmp / f"seq_{i:04d}_001.fits")

manifest = {
    "version": "1.0",
    "created": "2026-05-10T00:00:00",
    "project": "smoke",
    "path": {"control_points": [], "spline_type": "cubic_bezier", "coordinate_frame": "J2000"},
    "capture_settings": {
        "point_spacing_deg": 0.5,
        "exposure_seconds": 1.0,
        "exposures_per_point": 1,
        "sequence_name": "smoke",
    },
    "capture_points": [
        {"index": 0, "ra": 180.0, "dec": 45.0, "status": "captured", "files": ["seq_0000_001.fits"]},
        {"index": 1, "ra": 180.1, "dec": 45.0, "status": "captured", "files": ["seq_0001_001.fits"]},
    ],
    "labels": [
        {
            "id": "test",
            "text": "STAR",
            "ref_frame_index": 0,
            "x": 3000.0,
            "y": 2000.0,
            "color": "#ff00ff",
            "marker": "circle",
        }
    ],
}
(tmp / "manifest.json").write_text(json.dumps(manifest))

cfg = RenderConfig(
    fps=24, crf=23, transition="none", resolution="720p",
    render_labels=True,
)
pipe = RenderPipeline(tmp, cfg)
pipe.load()
out = tmp / "out.mp4"
pipe.render(out)
print("output:", out, "size:", out.stat().st_size)

# cleanup
shutil.rmtree(tmp)
```

Run: `PYTHON_GIL=0 .venv/bin/python /tmp/label-smoke.py`
Expected: prints `output: /tmp/label-smoke-...../out.mp4 size: <some bytes>`. No exceptions.

- [ ] **Step 3.14: Commit**

```bash
git add src/config.py src/renderer/labels.py src/renderer/pipeline.py src/renderer/cli.py tests/test_labels_render.py
git commit -m "feat(renderer): draw labels per frame; add render_labels toggle and --no-labels

Labels travel via the alignment chain; transitions interpolate the
offset. Refs #127."
```

---

## Task 4: UI — Labels panel (list, edit, delete; no click-to-add yet)

Adds a collapsible "Labels" section to the renderer UI. Lists existing labels, lets the user edit text/color/marker via a popover, and delete with one click. Persists changes to `manifest.json` immediately.

**Files:**
- Modify: `src/renderer/ui/render_layout.py`

This task is largely UI work; tests are smoke checks (NiceGUI integration tests are awkward and the project hasn't established a pattern for them).

- [ ] **Step 4.1: Add a `_persist_project` helper**

Near the top of `render_layout.py`, after the `_save_render_state` helper:

```python
def _persist_project(state: _RenderState) -> None:
    """Write the (possibly modified) project back to manifest.json.

    Called after any in-UI mutation of the labels list so the change
    survives a tab close even before the next render.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    manifest_path = state.pipeline.capture_dir / "manifest.json"
    manifest_path.write_text(state.pipeline.project.model_dump_json(indent=2))
```

- [ ] **Step 4.2: Build the Labels panel**

Add a new function `_build_labels_panel(state)` that creates a `ui.expansion("Labels", icon="label")` containing a placeholder for the list and an "Add" toggle button. Then call it from `create_render_layout` after `_build_stretch_controls(state)`.

```python
def _build_labels_panel(state: _RenderState) -> None:
    """Collapsible Labels list + edit/delete + click-to-add toggle."""
    with ui.expansion("Labels", icon="label").classes("w-full") as exp:
        state.labels_panel = exp
        with ui.column().classes("w-full gap-1"):
            state.labels_list_container = ui.column().classes("w-full gap-1")
            with ui.row().classes("w-full justify-end"):
                ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _toggle_click_to_add(state),
                ).props("dense flat")
        _refresh_labels_list(state)
```

(Add `labels_panel`, `labels_list_container`, `click_to_add_active: bool = False` to `_RenderState.__init__`.)

- [ ] **Step 4.3: Implement `_refresh_labels_list`**

```python
def _refresh_labels_list(state: _RenderState) -> None:
    """Re-render the labels list from the current project."""
    if not state.labels_list_container:
        return
    state.labels_list_container.clear()
    if not state.pipeline or not state.pipeline.project:
        with state.labels_list_container:
            ui.label("(load a capture first)").classes("text-grey text-xs")
        return
    labels = state.pipeline.project.labels
    if not labels:
        with state.labels_list_container:
            ui.label("(no labels yet)").classes("text-grey text-xs")
        return
    with state.labels_list_container:
        for label in labels:
            _render_label_row(state, label)


def _render_label_row(state: _RenderState, label: "Label") -> None:
    """One row in the labels list."""
    with ui.row().classes("w-full items-center gap-2"):
        ui.html(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:{label.color};border-radius:50%"></span>',
        )
        ui.label(label.text or "(empty)").classes("flex-grow text-sm")
        ui.label(f"({int(label.x)},{int(label.y)})").classes("text-xs text-grey")
        ui.button(icon="edit", on_click=lambda l=label: _open_edit_popover(state, l)).props("flat dense")
        ui.button(icon="delete", color="red",
                  on_click=lambda l=label: _delete_label(state, l)).props("flat dense")
```

(Add `from src.models.project import Label` at the top of the file.)

- [ ] **Step 4.4: Implement `_delete_label`**

```python
def _delete_label(state: _RenderState, label: Label) -> None:
    if not state.pipeline or not state.pipeline.project:
        return
    state.pipeline.project.labels = [
        x for x in state.pipeline.project.labels if x.id != label.id
    ]
    _persist_project(state)
    _refresh_labels_list(state)
```

- [ ] **Step 4.5: Implement `_open_edit_popover`**

```python
def _open_edit_popover(state: _RenderState, label: Label) -> None:
    """Open an inline dialog to edit a label's properties."""
    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(f"Edit label").classes("text-md font-bold")
        text_in = ui.input("Text", value=label.text)
        color_in = ui.input("Color (hex)", value=label.color)
        font_size_in = ui.number("Font size", value=label.font_size, min=6, max=200)
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value=label.marker, label="Marker",
        )
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            def _save() -> None:
                label.text = text_in.value or ""
                label.color = color_in.value or "#ffff00"
                label.font_size = int(font_size_in.value or 24)
                label.marker = marker_in.value or "dot"
                _persist_project(state)
                _refresh_labels_list(state)
                dialog.close()
            ui.button("Save", color="primary", on_click=_save)
    dialog.open()
```

- [ ] **Step 4.6: Stub out `_toggle_click_to_add`**

For Task 4, click-to-add is not yet implemented — leave the toggle as a stub that just notifies the user:

```python
def _toggle_click_to_add(state: _RenderState) -> None:
    """Stub for now; full click-to-add lands in Task 5."""
    ui.notify("Click-to-add: coming in next iteration", type="info")
```

- [ ] **Step 4.7: Hook into the load flow**

After `_load(state)` finishes successfully, call `_refresh_labels_list(state)` so the panel populates after a new capture is loaded. Find the `_load` function and add the call near where `_show_preview(state, 0)` is invoked:

```python
            await _show_preview(state, 0)
            _refresh_labels_list(state)
            ui.notify(f"Ready — {n} frames loaded")
```

- [ ] **Step 4.8: Wire `_build_labels_panel` into `create_render_layout`**

```python
def create_render_layout() -> None:
    state = _RenderState()
    with ui.column().classes("w-full p-4 gap-4"):
        _build_top_bar(state)
        state.preview = ui.image().classes("w-full max-h-96 object-contain")
        _build_stretch_controls(state)
        state.filmstrip = ui.row().classes("w-full overflow-x-auto gap-1 py-2")
        _build_labels_panel(state)   # ← new
        _build_output_settings(state)
        state.progress = ui.linear_progress(value=0).classes("w-full")
        state.status_label = ui.label("")
```

- [ ] **Step 4.9: Run the test suite (regression check)**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: 170+ still passing.

- [ ] **Step 4.10: Manual smoke test**

```bash
PYTHON_GIL=0 make run-render
```

In the browser:
1. Load a capture directory.
2. Edit `manifest.json` directly to add one label (Task 1's test data shape works).
3. Reload the renderer page → expand "Labels" → see the row.
4. Click edit → change text → Save → row updates.
5. Click delete → row disappears.
6. Reload page → deletion persists.

- [ ] **Step 4.11: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(renderer-ui): labels panel with edit and delete

Click-to-add and catalog-add follow in subsequent commits. Refs #127."
```

---

## Task 5: UI — Click-to-add mode

Replace the stub from Task 4 with a working click-to-add flow: user clicks "Add label", the preview enters click-to-add mode, a click on the preview image captures the pixel coordinates, an inline popover opens for text/color/marker, save appends to the labels list.

**Files:**
- Modify: `src/renderer/ui/render_layout.py`

- [ ] **Step 5.1: Add UUID generation to the imports**

Add to the top of `render_layout.py`:

```python
import uuid
```

- [ ] **Step 5.2: Implement the toggle**

Replace the stub:

```python
def _toggle_click_to_add(state: _RenderState) -> None:
    state.click_to_add_active = not state.click_to_add_active
    if state.click_to_add_active:
        ui.notify("Click anywhere on the preview to add a label",
                  type="info", timeout=3000)
        if state.preview:
            state.preview.classes(add="cursor-crosshair")
    else:
        if state.preview:
            state.preview.classes(remove="cursor-crosshair")
```

- [ ] **Step 5.3: Hook a click handler on the preview image (with the dimension args we need)**

In `create_render_layout`, replace the preview-image setup with one that requests the click position **and** the image's natural / displayed dimensions in the same event payload. NiceGUI 3.x lets you list dotted property paths into the JS event in the `args=` parameter:

```python
        state.preview = ui.image().classes("w-full max-h-96 object-contain")
        state.preview.on(
            "click",
            lambda e: _handle_preview_click(state, e),
            args=[
                "offsetX", "offsetY",
                "target.naturalWidth", "target.naturalHeight",
                "target.offsetWidth", "target.offsetHeight",
            ],
        )
```

The handler receives them under `event.args` (key names follow the last segment of each path: `naturalWidth`, `offsetWidth`, etc.).

If the dotted-path form is not honoured by the installed NiceGUI version, the agent should fall back to the simpler `args=["offsetX", "offsetY"]` form and read `pipeline.debayered_frame(...)` shape together with the preview's `max-h-96` CSS height to estimate the scale (less accurate; document this in the commit message if used).

Add the handler near the other UI helpers:

```python
def _handle_preview_click(state: _RenderState, event) -> None:
    """If click-to-add is active, treat the click as a label position."""
    if not state.click_to_add_active:
        return
    if not state.pipeline or not state.pipeline.project:
        return
    args = event.args or {}
    css_x = float(args.get("offsetX", 0))
    css_y = float(args.get("offsetY", 0))
    nat_w = float(args.get("naturalWidth", 1)) or 1.0
    nat_h = float(args.get("naturalHeight", 1)) or 1.0
    disp_w = float(args.get("offsetWidth", nat_w)) or nat_w
    disp_h = float(args.get("offsetHeight", nat_h)) or nat_h
    # Map CSS click → natural-image pixel within the preview JPEG.
    nat_x = css_x * (nat_w / disp_w)
    nat_y = css_y * (nat_h / disp_h)
    # Map natural-pixel (preview is the JPEG downsampled to ≤1280 px in
    # _show_preview) → original frame pixel, using the original
    # frame's actual dimensions.
    frame_idx = state.selected_frame
    pipeline = state.pipeline
    debayered = pipeline.debayered_frame(frame_idx)
    orig_h, orig_w = debayered.shape[:2]
    preview_scale = min(orig_w / nat_w, orig_h / nat_h)
    label_x = nat_x * preview_scale
    label_y = nat_y * preview_scale
    _open_create_popover(state, ref_frame_index=frame_idx, x=label_x, y=label_y)
    # Exit click-to-add after one placement.
    _toggle_click_to_add(state)
```

- [ ] **Step 5.4: Implement `_open_create_popover`**

```python
def _open_create_popover(
    state: _RenderState,
    *,
    ref_frame_index: int,
    x: float,
    y: float,
) -> None:
    """Open the same popover used for editing, but for a brand-new label."""
    new_label = Label(
        id=str(uuid.uuid4()),
        text="",
        ref_frame_index=ref_frame_index,
        x=x,
        y=y,
    )
    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("New label").classes("text-md font-bold")
        ui.label(
            f"Position: ({int(x)}, {int(y)}) on frame {ref_frame_index}",
        ).classes("text-xs text-grey")
        text_in = ui.input("Text", value="")
        color_in = ui.input("Color (hex)", value="#ffff00")
        font_size_in = ui.number("Font size", value=24, min=6, max=200)
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value="dot", label="Marker",
        )
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            def _save() -> None:
                new_label.text = text_in.value or ""
                new_label.color = color_in.value or "#ffff00"
                new_label.font_size = int(font_size_in.value or 24)
                new_label.marker = marker_in.value or "dot"
                if state.pipeline and state.pipeline.project:
                    state.pipeline.project.labels.append(new_label)
                    _persist_project(state)
                    _refresh_labels_list(state)
                dialog.close()
            ui.button("Save", color="primary", on_click=_save)
    dialog.open()
```

- [ ] **Step 5.5: Run tests; verify no regressions**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: 170+ still passing.

- [ ] **Step 5.6: Manual smoke test**

```bash
PYTHON_GIL=0 make run-render
```

In the browser:
1. Load a capture directory.
2. Click "Add label" in the Labels panel → notification appears, cursor turns to crosshair on the preview.
3. Click somewhere on the preview → popover opens with the click position pre-filled.
4. Type "M27", pick yellow, marker dot, Save → row appears in the Labels list.
5. Reload the page → the label persists.
6. Render the video → the label is drawn at the correct position in every frame where its underlying point is visible.

- [ ] **Step 5.7: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(renderer-ui): click-to-add labels on preview

Toggle 'Add label' to enter click-to-add mode; clicking on the preview
captures the natural-pixel coordinates and opens the new-label popover.
Refs #127."
```

---

## Task 6: UI — Catalog-style label entry

Add an alternative add path: instead of clicking, the user enters text + RA + Dec, and the system computes the reference-frame pixel via `catalog_to_ref_pixel` using the project's `north_angle_deg` and the global `pixel_scale_arcsec`.

**Files:**
- Modify: `src/renderer/ui/render_layout.py`

- [ ] **Step 6.1: Add a "Catalog" button next to "Add label"**

In `_build_labels_panel`:

```python
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(
                    "Catalog…", icon="search",
                    on_click=lambda: _open_catalog_popover(state),
                ).props("dense flat")
                ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _toggle_click_to_add(state),
                ).props("dense flat")
```

- [ ] **Step 6.2: Implement `_open_catalog_popover`**

```python
def _open_catalog_popover(state: _RenderState) -> None:
    """Add a label by entering its sky coordinates instead of clicking."""
    if not state.pipeline or not state.pipeline.project:
        ui.notify("Load a capture first", type="warning")
        return
    project = state.pipeline.project
    if not project.capture_points:
        ui.notify("No capture points in manifest", type="warning")
        return
    frame_idx = state.selected_frame
    ref_point = next(
        (p for p in project.capture_points if p.index == frame_idx),
        project.capture_points[0],
    )

    with ui.dialog() as dialog, ui.card().classes("w-96"):
        ui.label("Add catalog label").classes("text-md font-bold")
        ui.label(
            f"Reference frame {ref_point.index}: "
            f"RA={ref_point.ra:.4f}°  Dec={ref_point.dec:.4f}°",
        ).classes("text-xs text-grey")
        text_in = ui.input("Text", value="")
        ra_in = ui.number("RA (deg)", value=ref_point.ra,
                          format="%.6f", step=0.0001)
        dec_in = ui.number("Dec (deg)", value=ref_point.dec,
                           format="%.6f", step=0.0001)
        catalog_id_in = ui.input("Catalog ID (optional)", value="")
        color_in = ui.input("Color (hex)", value="#ffff00")
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value="circle", label="Marker",
        )
        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")
            def _save() -> None:
                from src.config import settings
                from src.renderer.labels import catalog_to_ref_pixel
                pipeline = state.pipeline
                if not pipeline or not pipeline.project:
                    return
                debayered = pipeline.debayered_frame(ref_point.index)
                orig_h, orig_w = debayered.shape[:2]
                px, py = catalog_to_ref_pixel(
                    ra_deg=float(ra_in.value or ref_point.ra),
                    dec_deg=float(dec_in.value or ref_point.dec),
                    frame_center_ra_deg=ref_point.ra,
                    frame_center_dec_deg=ref_point.dec,
                    frame_dims=(orig_w, orig_h),
                    pixel_scale_arcsec=settings.pixel_scale_arcsec,
                    north_angle_deg=pipeline.project.north_angle_deg,
                )
                new_label = Label(
                    id=str(uuid.uuid4()),
                    text=text_in.value or (catalog_id_in.value or ""),
                    ref_frame_index=ref_point.index,
                    x=px, y=py,
                    color=color_in.value or "#ffff00",
                    marker=marker_in.value or "circle",
                    source="catalog",
                    catalog_ra=float(ra_in.value),
                    catalog_dec=float(dec_in.value),
                    catalog_id=(catalog_id_in.value or None),
                )
                pipeline.project.labels.append(new_label)
                _persist_project(state)
                _refresh_labels_list(state)
                dialog.close()
            ui.button("Add", color="primary", on_click=_save)
    dialog.open()
```

- [ ] **Step 6.3: Run tests; verify no regressions**

Run: `PYTHON_GIL=0 .venv/bin/pytest tests/ -q --tb=no`
Expected: 170+ still passing.

- [ ] **Step 6.4: Manual smoke test**

```bash
PYTHON_GIL=0 make run-render
```

In the browser:
1. Load a capture.
2. Click "Catalog…" → dialog opens with the current frame's RA/Dec pre-filled.
3. Enter "M27", RA=299.901, Dec=22.721, leave the rest as defaults, Add.
4. New label appears in the list. Inspect the `(x, y)` — should be near frame center because the entered RA/Dec ≈ the frame's center; small offset reflects the difference.
5. Render: the label appears with a circle at roughly the frame center.

- [ ] **Step 6.5: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(renderer-ui): catalog-style label entry via RA/Dec

Uses pixel_scale_arcsec from Settings and Project.north_angle_deg
to project the entered sky coordinates into the reference frame's
pixel space. Closes the manual portion of #127."
```

---

## Done. Loose ends explicitly out of scope

These were named in the spec as deferred and remain so:

- Real online catalog lookup (e.g., SIMBAD client, NGC database). The data model already supports `catalog_id`/`catalog_ra`/`catalog_dec`; integrating a network resolver is a follow-up issue.
- Plate-solving for accurate `north_angle_deg`. Today the user supplies this manually; orientation defaults to 0°.
- Reference-frame skip remap (warn + auto-rebase labels onto a non-skipped frame).
- Label undo/redo.
- Animated / time-varying labels.
- Label groups, layers, or per-label visibility per frame range.
