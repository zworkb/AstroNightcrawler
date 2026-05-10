# Annotations and Overlays — Design

Epic: [#127](https://github.com/zworkb/AstroNightcrawler/issues/127)
Status: design approved, ready for implementation plan

## 1. Goal

Allow the user to place text labels (and small markers) at specific positions in the captured material. The labels appear in the rendered video whenever their position is in the visible frame, follow the image correctly across linear-pan transitions, and persist with the project so the same labels reappear on every re-render.

## 2. Scope

In:

- **A — User-placed annotations.** Click on a preview frame, type text, pick color/marker, save. Stored as pixel coordinates in a chosen reference frame.
- **B — Catalog-style labels.** User supplies a label text plus an RA/Dec (entered manually for v1; real catalog lookup deferred). Internally converted to pixel coordinates in the reference frame at insert time. After conversion, behaves identically to (A).

Out:

- Capture-path indicators (rejected — would clutter rendering with metadata that is already visible during planning).
- Per-frame manual repositioning (the alignment chain handles this for free).
- Time-varying / animated labels (text fades, motion paths).
- Real online catalog lookup (separate follow-up; the data model already accommodates `catalog_*` fields).

## 3. Coordinate model

**Pixel-in-reference-frame.** Each label stores its position as `(x, y)` in the pixel space of one chosen reference frame (default: frame 0).

**Why this and not RA/Dec everywhere:**

- Manual click → save is direct (pixel coordinates fall out of the click event).
- No FITS WCS dependency; capture FITS files don't have WCS today.
- The renderer pipeline already produces per-pair `AlignmentResult(dx, dy, rotation)`. Cumulative pixel offsets from any frame to any other are derived by summing the chain.

**Catalog → pixel conversion** (one-shot, at insert time):

- Reference frame's center RA/Dec is the target sky position from the manifest's `CapturePoint`.
- Pixel scale derived from sensor + focal length. Lives in `Settings` (env vars `NC_PIXEL_SCALE_ARCSEC` etc.) since it's a property of the optics, reused across projects.
- Orientation: 0° (north up) by default; user can override **per project**, so it lives on the `Project` model as `north_angle_deg: float = 0.0`. Mount-alignment quirks vary per capture session.
- For v1, the conversion is approximate — good enough for "show M27 here", not for sub-arcsec positioning. Plate-solving is a follow-up.

**Field rotation across the path** is ignored in v1: only translation (`dx, dy`) is used to track positions across frames. For typical short-exposure DSLR captures with paths of a few degrees, the cumulative rotation between adjacent frames is well below 1°. If a future capture pattern needs it, the alignment data already includes `rotation`; we extend the transform.

## 4. Data model

In `src/models/project.py`, alongside `CapturePoint` and `SplinePath`:

```python
from pydantic import BaseModel, Field
from typing import Literal


class Label(BaseModel):
    """A single annotation drawn into rendered frames."""

    id: str = Field(description="UUID4 — stable across edits")
    text: str = Field(description="Display text")

    # Position in the reference frame's pixel space.
    ref_frame_index: int = Field(
        ge=0,
        description="Which capture frame's pixel space holds (x, y)",
    )
    x: float = Field(description="Pixel-x in reference frame; sub-pixel allowed")
    y: float = Field(description="Pixel-y in reference frame")

    # Appearance
    color: str = Field(default="#ffff00", description="CSS hex; text + marker share this color")
    font_size: int = Field(default=24, ge=6, le=200)
    marker: Literal["none", "dot", "cross", "circle"] = Field(default="dot")
    text_offset_x: int = Field(default=12, description="Text offset from marker (px)")
    text_offset_y: int = Field(default=0)

    # Provenance — empty for manual labels, filled for catalog labels.
    source: Literal["manual", "catalog"] = Field(default="manual")
    catalog_ra: float | None = Field(default=None, description="Original RA in degrees (catalog labels only)")
    catalog_dec: float | None = Field(default=None, description="Original Dec in degrees (catalog labels only)")
    catalog_id: str | None = Field(default=None, description="Source identifier, e.g. 'M27'")
```

Add to the existing `Project` model:

```python
class Project(BaseModel):
    # ...existing fields...
    labels: list[Label] = Field(default_factory=list)
```

**Backward compatibility:** old `manifest.json` files without a `labels` key load with an empty list (default factory). No migration script needed.

## 5. Storage

Persisted in the existing `manifest.json` under the `Project.labels` key. No new file format, no extra I/O.

The manifest is the established Capture↔Render hand-off boundary. Adding render-side metadata to it is consistent with the existing precedent (the renderer already reads `CaptureSettings.exposure_seconds` and `CapturePoint.ra/dec` from the same file).

When labels change (add / edit / delete in the renderer UI), the modified `Project` is re-serialized to `manifest.json` immediately, so changes survive a tab close even before the next render.

## 6. UI workflow

**Hybrid: panel-with-list + click-to-add (Option C from brainstorming).**

### 6.1 Panel placement

A new collapsible section "Labels" in the renderer UI, between the stretch controls and the output settings. Initially collapsed; expanding it reveals the list and the toggle for click-to-add mode.

### 6.2 List panel

For each `Label`:
- One row showing: marker color swatch, text (truncated), `(x, y)` in ref frame, edit + delete buttons.
- "Add" button toggles click-to-add mode.

### 6.3 Click-to-add mode

When active:
- Cursor over the preview shows a crosshair.
- A click on the preview opens an inline popover at the click position with: text input, color picker, marker dropdown, font size, ref-frame index (defaults to currently displayed frame), Save / Cancel.
- Save: append to `state.pipeline.config.labels`, persist to manifest, refresh preview, exit click-to-add mode.

### 6.4 Color picker

For v1: a preset palette of 6–8 well-known annotation colors (yellow, white, cyan, magenta, red, lime, orange) plus a hex input field for custom. Quasar's `ui.color_input` covers this.

### 6.5 Edit / delete

- Edit: clicking a label row in the list opens the same popover, pre-filled.
- Delete: button on the row, single click deletes immediately (no confirmation dialog — annotations are cheap to recreate; if needed, undo can be added later).

### 6.6 Visualisation in the preview

Every label whose pixel position lies within the currently displayed frame's bounds is drawn on the preview, using the same render path as the final video (see §7). The preview is a small thumbnail / max-h-96 area, so the label scales down with the preview but stays legible.

## 7. Render integration

**In Python pipeline, before each PNG write.** A new helper:

```python
def _draw_labels(
    frame: np.ndarray,
    labels: list[Label],
    cumulative_offset: tuple[float, float],
    frame_dims: tuple[int, int],
) -> np.ndarray:
    """Draw labels in-place via PIL.ImageDraw.

    cumulative_offset is the (dx, dy) from each label's ref_frame_index
    to the current frame. Labels whose projected (x - dx, y - dy)
    falls outside frame_dims are silently skipped.
    """
```

Called from `_render_to_dir`:
- For each key frame, compute `cumulative_offset = sum_alignments(label.ref_frame_index → current_frame_index)` and call the helper before `write_frame_png(...)`.
- For each transition frame, compute the interpolated offset (linear-pan: between the bracketing keyframes' offsets; crossfade / none: use the relevant keyframe's offset).
- The same helper is used for the live preview, which is a single-frame call.

**Why this position in the pipeline:**
- Labels appear after stretch + resize but before encoding — they're in the final output pixel space.
- The pipeline already has alignment data computed (for linear-pan) and a streaming per-frame loop. Inserting a per-frame draw is a one-line change.
- ffmpeg drawtext was rejected: dynamic per-frame text positions would require a complex filter chain or a sidecar text file per frame — both fragile and harder to debug than PIL.

**Performance:** PIL text + small marker drawing is microseconds per frame. Negligible vs the existing stretch / debayer cost.

**Render toggle:** new field on `RenderConfig`:

```python
render_labels: bool = True
```

UI: a checkbox in the Labels section, "Include labels in render". Default on. CLI: `--no-labels` flag to disable.

## 8. Reference-frame skip handling

**v1:** if the user sets the chosen reference frame to "skipped", the labels render anyway — the alignment chain still works through the (physically-still-present) reference frame even if it's not in the output.

**v2 (deferred):** when the renderer detects ref-frame skipping, show an inline warning in the Labels panel: "Reference frame is skipped. Labels may not appear correctly. Re-anchor labels?" plus a one-click button that converts each label's `(x, y)` to the next non-skipped frame's pixel space using the alignment chain.

## 9. Out-of-scope details, deferred

- **Real catalog lookup (B+):** v1 only accepts manually entered RA/Dec for catalog-source labels. Online catalog (SIMBAD, NGC) is a separate issue, plumbing in the data model is already in place via `catalog_*` fields.
- **Plate-solving:** orientation defaults to 0° (north up); user can override. Real plate-solving via astrometry.net or astropy's matching is a follow-up.
- **Animated labels:** time-varying text or motion paths are out.
- **Label groups / layers:** all labels are independent. No grouping.
- **Export label list as CSV / TSV:** not needed for the rendered output; useful only as a data-export feature.

## 10. Acceptance criteria

For the implementation plan to consider this design "done":

1. The user can click on the preview, type text, pick a color, save → label appears in the preview at the clicked position and survives a tab reload (because it's in `manifest.json`).
2. The user can list, edit, and delete existing labels via the side panel.
3. A render with labels enabled produces a video where each label appears at the correct screen position whenever its underlying sky point is in frame, with linear-pan transitions tracking smoothly.
4. Disabling the render-labels toggle produces the same video without any labels visible.
5. A capture directory's `manifest.json` without a `labels` key continues to load and render normally.
6. Adding a manually-entered catalog label (text + RA + Dec) places it correctly in the reference frame using the configured pixel scale and orientation.

## 11. Implementation ordering (for the plan)

Suggested sequence so that early sub-issues are user-visible quickly:

1. Data model: extend `Project` with `labels` field; add `Label` pydantic model. Round-trip test.
2. Coordinate helpers: pure functions for ref-frame-pixel ↔ frame-N-pixel via cumulative alignment, plus the catalog-RA/Dec → ref-frame-pixel conversion (with unit tests, no UI yet).
3. Render integration: `_draw_labels` helper + call sites in `_render_to_dir`, plus `RenderConfig.render_labels` toggle and CLI flag.
4. UI: Labels panel with the list, edit/delete affordances, persist to manifest. No click-to-add yet — labels can be added by typing pixel coords.
5. UI: click-to-add mode in the preview, with the inline popover.
6. UI: catalog-style add (manual RA/Dec entry).

Steps 1–3 alone deliver value to anyone scripting the manifest by hand. Steps 4–6 progressively make the feature ergonomic.
