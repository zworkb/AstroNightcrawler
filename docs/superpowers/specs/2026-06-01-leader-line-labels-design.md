# Leader-Line Labels — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Status: design approved, ready for implementation plan

## 1. Goal

Add a new label rendering mode where the label text sits next to its
target (not covering it) and a leader line connects the two. Useful
for annotating small or visually-crowded targets — planetary nebulae,
double stars, faint galaxies in a cluster — where the current overlap
between marker and text obscures the object itself.

## 2. Why now

Issue [#152](https://github.com/zworkb/AstroNightcrawler/issues/152)
just shipped catalog-click label placement. The catalog mode reveals
how often the labelled object is exactly the thing the text covers
(see the smoke-test screenshot: "NGC 6881" written over the nebula's
core). A callout-style label with a leader line solves this without
forcing the user to manually tweak `text_offset_x/y` after each
placement.

## 3. Concept summary

A label gains a `leader` field describing whether and how the marker
and text are connected:

- `none` (default) — current behavior: marker at target, text at
  `(target + text_offset)`, nothing drawn between them.
- `line` — same geometry, plus a thin line from the marker edge to
  the nearest edge of the text bounding box.
- `arrow` — like `line`, but with a small filled triangle at the
  marker end. The arrow points AT the target.

The pixel geometry is unchanged: target lives at `(x, y)`, text at
`(x + text_offset_x, y + text_offset_y)`. The leader line is purely
derived at render time from those two anchors and the rendered
text-box size. Old labels with small offsets and `leader="none"`
remain byte-identical.

## 4. Data model

`src/models/project.py` `Label` gains one field:

```python
leader: Literal["none", "line", "arrow"] = Field(
    default="none",
    description="Connect marker to text with a leader line. "
                "'arrow' adds a small arrowhead at the marker end."
)
```

Compatibility:

- pydantic `ConfigDict(extra="ignore")` is already on the parent
  `Project` model — old manifests load with `leader="none"` defaulted.
- New manifests with `leader != "none"` loaded by an old build of the
  app simply lose the leader (the field gets ignored). No crash, no
  data loss.

`text_offset_x/y` semantics widen: they remain the text-anchor offset
from the marker, but the UI sliders' upper bounds open up so the user
can place text well outside the marker's immediate neighborhood. The
data-format range is already unbounded (`int`); only the editor UI
needs adjustment.

## 5. Rendering

`src/renderer/labels.py` `_draw_labels` gets one new branch when
`label.leader != "none"`. The line endpoints are:

- **Marker end**: the marker's outer edge in the direction of the
  text anchor. Computed from marker geometry (circle radius / dot
  size / cross arms) so the line doesn't cut into the marker.
- **Text end**: the point on the text bounding-box edge closest to
  the marker. We measure the rendered text size via the same
  `_resolve_font` path the existing draw code uses, build the bbox,
  then clip the marker→text-center line at the bbox boundary.

For `leader="arrow"`:

- Draw the same line.
- At the marker end, draw a small filled triangle pointing AT the
  marker. Triangle half-width ≈ `font_size / 4`, length ≈
  `font_size / 3`. PIL's `ImageDraw.polygon` handles it cleanly.

Color = `label.color` for both line and arrowhead — matches the
marker + text so the callout reads as one unit. Line width = 2 px
unless the font is very large, in which case scale to `font_size //
12` (a 48 px font gets a 4 px line).

## 6. Placement UX

A new toolbar toggle, **"Leader Label"**, sits next to "Add Label"
and "Catalog Mode" in the render UI. The existing buttons keep their
current behavior unchanged — this is purely additive.

The leader button's click handler auto-detects whether it's a manual
or catalog placement based on what the user clicks on:

### Manual sub-mode (no catalog object nearby)

Rubber-band placement:

1. **First click** records the target pixel.
2. **Mouse move** draws a temporary preview overlay: a line from the
   recorded target to the current cursor, plus a placeholder text
   anchor at the cursor.
3. **Second click** records the text pixel. The label is committed
   with `text_offset = click2 - click1` and `leader` set per the
   current selection in the label-style panel (default `"line"`).

ESC cancels the partial state. The mode stays active for further
placements (consistent with the existing modes).

### Catalog sub-mode (click near a catalog object)

The user has the catalog overlay loaded (which #152 already does
when Catalog Mode is active, but for leader mode we load it
silently). If the click lands within a small threshold (≈ 30 CSS
pixels) of a known catalog object's projected pixel:

1. **Single click** records the text pixel.
2. The nearest catalog object becomes the target, its true catalog
   pixel position the marker.
3. The label is committed with `source="catalog"`, `catalog_ra/dec`
   from the catalog row, and `leader` per the panel.

The 30-px threshold is roughly "the user could have intended to
click the object." Beyond that, the manual sub-mode kicks in.

### Tooltip during tracking

While tracking the rubber-band line, show a small tooltip near the
cursor: distance from target in pixels and arcminutes. Same widget
as #152's catalog tooltip; just driven from the in-progress label
rather than catalog data.

## 7. Tracking across frames

A leader-line label's marker (target) lives at `(x, y)` in the ref
frame and tracks via the existing alignment chain — same as today's
labels. The text position is `marker + text_offset`, so the entire
callout (marker + line + text) translates with the target across
frames. The leader line's length and angle stay constant.

This is the "Relativ (Offset)" option from the brainstorm. The
alternative — fixed text-position with rotating leader — was
rejected because it breaks legibility (text would slide off-screen
during a pan).

## 8. Edge cases

- **Marker at frame edge**: text can land off-frame; the existing
  clip behavior in `_draw_labels` (skip off-frame labels) extends to
  the leader line. Skip-rule: if either endpoint is off-frame by
  more than a font-size's worth, skip the entire label.
- **Zero-length leader**: `text_offset = (0, 0)` is the degenerate
  case where text sits on the marker. Render no line in that case —
  it would be a single-pixel artifact.
- **Very long leader**: no cap. Users may want labels well outside
  the immediate object (e.g., labeling a target in the corner with
  text in open space at the center). The renderer just draws the
  full line.
- **Overlapping leader lines**: not addressed in v1. If two leader
  labels cross, they cross. Future enhancement: collision routing.

## 9. Out of scope (v1)

- Multiple leader lines per label (one target → multiple text
  fragments).
- Curved / right-angle / bracket leader styles.
- Auto-placement (algorithm that picks text position to avoid the
  underlying image content). Manual is fine for now.
- Drag-to-reposition after placement. Edit via the label panel's
  numeric inputs only. (Drag handling for labels doesn't exist yet
  generally; that's a separate issue.)
- Different colors / widths for line vs marker. They inherit.

## 10. Testing

- `tests/test_labels.py` (existing): add a case for `leader="line"`
  drawing a 2-px line between marker and text bbox edges.
- Add a case for `leader="arrow"` rendering a triangle within the
  expected bounding region (assert pixel color at a known
  arrow-interior coord matches `label.color`).
- `tests/test_project_model.py` (existing): add a case loading a
  manifest with `leader="arrow"` and one missing the field
  (default → `"none"`).
- UI behavior (click+track, catalog snap) is not unit-tested at the
  renderer level — those flows live in `render_layout.py` and need
  manual smoke testing per the existing pattern. The plan includes
  a smoke checklist for the user.

## 11. Implementation order

1. Model: add `leader` field + default + tests.
2. Renderer: leader-line drawing branch + arrow + tests.
3. UI: "Leader Label" toggle button.
4. UI: manual rubber-band placement.
5. UI: catalog-snap detection.
6. UI: label panel exposes the `leader` field for editing
   post-creation.
7. Smoke test + commit.

Each step is mergeable on its own — the model + renderer ship a
working renderer-side feature even before the UI exists (manifest-
hand-edit testing).
