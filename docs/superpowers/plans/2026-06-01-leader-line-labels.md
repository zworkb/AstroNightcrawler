# Leader-Line Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add callout-style labels — text next to the target, leader line connecting them — so labels stop covering the objects they name.

**Architecture:** One new pydantic field on `Label` (`leader: "none"|"line"|"arrow"`, default `"none"`); renderer branches on it inside the existing `_draw_labels` PIL path; UI adds one new toolbar toggle that does rubber-band placement (manual) or catalog snap (when click is near a known catalog object).

**Tech Stack:** Pydantic v2, PIL (`ImageDraw.line` / `ImageDraw.polygon`), NiceGUI 2.x (`ui.button` + `ui.run_javascript` for the rubber-band JS overlay).

**Issue:** [#153](https://github.com/zworkb/AstroNightcrawler/issues/153). **Spec:** [docs/superpowers/specs/2026-06-01-leader-line-labels-design.md](../specs/2026-06-01-leader-line-labels-design.md).

---

## File Map

| Path | Role | Touch |
|---|---|---|
| `src/models/project.py` | `Label` pydantic model | Modify — add `leader` field |
| `src/renderer/labels.py` | PIL `_draw_labels` helper | Modify — branch on `leader` for line + arrow |
| `src/renderer/ui/render_layout.py` | Render-tab toolbar + label list + event handlers | Modify — add toggle, handlers, JS overlay piggyback on catalog-overlay infra |
| `tests/test_labels_render.py` | Renderer pixel asserts | Modify — add 4 cases |
| `tests/test_render_pipeline.py` | Project round-trip | Modify — add 2 cases for manifest with/without `leader` |

No new files. The JS rubber-band overlay reuses the same `catalog-overlay-*` div + `__catalogOverlayState` machinery from #152 (one extra `mode` field on the JS state).

---

### Task 1: Add `leader` field to the `Label` model

**Files:**
- Modify: `src/models/project.py:248-275`
- Test: `tests/test_render_pipeline.py`

- [ ] **Step 1.1: Write the failing test**

Append to `tests/test_render_pipeline.py`:

```python
def test_label_default_leader_is_none():
    """New labels default to leader='none' so old manifests are unchanged."""
    from src.models.project import Label
    label = Label(
        id="a", text="M27", ref_frame_index=0, x=100.0, y=50.0,
    )
    assert label.leader == "none"


def test_label_round_trip_with_leader(tmp_path):
    """A manifest with leader='arrow' loads and re-saves byte-identical."""
    from src.models.project import Label, Project, CaptureSettings, CapturePoint
    label = Label(
        id="a", text="M27", ref_frame_index=0, x=100.0, y=50.0,
        text_offset_x=80, text_offset_y=-40, leader="arrow",
    )
    project = Project(
        name="t",
        capture_settings=CaptureSettings(),
        capture_points=[CapturePoint(index=0, ra=100.0, dec=20.0)],
        labels=[label],
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(project.model_dump_json(indent=2))
    reloaded = Project.model_validate_json(manifest.read_text())
    assert reloaded.labels[0].leader == "arrow"
    assert reloaded.labels[0].text_offset_x == 80


def test_label_loads_old_manifest_without_leader_field():
    """An older manifest predating this field still loads (defaulted)."""
    from src.models.project import Label
    raw_json = (
        '{"id":"a","text":"M27","ref_frame_index":0,'
        '"x":100.0,"y":50.0,"text_offset_x":12,"text_offset_y":0}'
    )
    label = Label.model_validate_json(raw_json)
    assert label.leader == "none"
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_render_pipeline.py::test_label_default_leader_is_none -v`

Expected: `AttributeError: 'Label' object has no attribute 'leader'`.

- [ ] **Step 1.3: Add the `leader` field**

In `src/models/project.py`, inside `class Label(BaseModel):` (between `text_offset_y` and `source`):

```python
    leader: Literal["none", "line", "arrow"] = Field(
        default="none",
        description=(
            "Connect marker to text with a leader line. "
            "'arrow' adds a small arrowhead at the marker end."
        ),
    )
```

`Literal` is already imported in that file.

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_render_pipeline.py -v -k "leader"`

Expected: all 3 new tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add src/models/project.py tests/test_render_pipeline.py
git commit -m "feat(models): Label.leader field for leader-line callouts (#153)"
```

---

### Task 2: Render `leader="line"` between marker and text

**Files:**
- Modify: `src/renderer/labels.py:206-260` (`_draw_labels`)
- Test: `tests/test_labels_render.py`

- [ ] **Step 2.1: Write the failing test**

Append to `tests/test_labels_render.py`:

```python
def test_draw_labels_with_leader_line_draws_pixels_between_marker_and_text():
    """leader='line' colors at least one pixel along the marker→text segment."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="dot",
        text_offset_x=150, text_offset_y=-60,
        leader="line",
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # Pixel halfway between marker (100, 100) and text-anchor (250, 40)
    # should be lit by the leader line.
    midx, midy = 175, 70
    assert out[midy, midx].max() > 0, (
        f"expected leader pixels around ({midx},{midy}), got {out[midy, midx]}"
    )


def test_draw_labels_leader_none_draws_no_pixels_between():
    """Default leader='none' must NOT light the midpoint."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="dot",
        text_offset_x=150, text_offset_y=-60,
        # leader omitted → defaults to "none"
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    midx, midy = 175, 70
    assert out[midy, midx].max() == 0, (
        f"unexpected pixel at midpoint with leader='none': {out[midy, midx]}"
    )
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_labels_render.py -v -k "leader"`

Expected: the `leader_line_draws_pixels` test FAILS (midpoint stays black). The `leader_none` test PASSES already.

- [ ] **Step 2.3: Add the leader-line branch**

In `src/renderer/labels.py`, add this helper above `_draw_labels` (after `_draw_marker`):

```python
def _draw_leader(
    draw: ImageDraw.ImageDraw,
    marker_x: float,
    marker_y: float,
    text_x: float,
    text_y: float,
    text_w: int,
    text_h: int,
    style: str,
    color: str,
    font_size: int,
) -> None:
    """Draw a leader line/arrow from the marker to the text bbox edge.

    Endpoints:
      * Marker end is the marker centre; the line gets clipped visually
        by the marker's own fill in subsequent draw calls, so we don't
        need to compute the marker's true outer radius here.
      * Text end is the point on the text bbox edge closest to the
        marker — computed as the line/box intersection.

    Style:
      * "line" — plain segment.
      * "arrow" — segment plus a filled triangle at the marker end
        pointing AT the marker.

    Width scales with font size so a 12-px font gets a 1-px line and
    a 60-px font gets a 5-px line.
    """
    import math

    # Text bbox: (text_x, text_y) is the top-left from ``draw.text``.
    bx0, by0 = text_x, text_y
    bx1, by1 = text_x + text_w, text_y + text_h
    # Centre of the text bbox.
    cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0

    dx = cx - marker_x
    dy = cy - marker_y
    if dx == 0.0 and dy == 0.0:
        return  # degenerate — marker is inside the text; nothing to draw

    # Clip the marker→text-centre ray at the bbox edge so the line
    # ends at the text edge, not inside the text.
    t_candidates = []
    if dx != 0.0:
        t_candidates.append((bx0 - marker_x) / dx)
        t_candidates.append((bx1 - marker_x) / dx)
    if dy != 0.0:
        t_candidates.append((by0 - marker_y) / dy)
        t_candidates.append((by1 - marker_y) / dy)
    # Intersection must (a) be in front of the marker (t > 0) and (b)
    # actually hit the bbox (the other axis at that t lies within it).
    text_end_x, text_end_y = cx, cy  # fallback
    best_t = float("inf")
    for t in t_candidates:
        if t <= 0.0 or t > 1.0:
            continue
        ix = marker_x + t * dx
        iy = marker_y + t * dy
        if (bx0 - 0.5 <= ix <= bx1 + 0.5) and (by0 - 0.5 <= iy <= by1 + 0.5):
            if t < best_t:
                best_t = t
                text_end_x, text_end_y = ix, iy

    width = max(1, font_size // 12)
    draw.line(
        [(marker_x, marker_y), (text_end_x, text_end_y)],
        fill=color, width=width,
    )

    if style == "arrow":
        # Arrow at the marker end. Triangle of length ~ font/3 along
        # the line, half-width ~ font/4 perpendicular to it.
        length = max(4.0, font_size / 3.0)
        half_w = max(2.0, font_size / 4.0)
        norm = math.hypot(dx, dy)
        ux, uy = dx / norm, dy / norm   # unit toward text
        # Arrow tip = marker; base = length pixels toward text.
        base_x = marker_x + ux * length
        base_y = marker_y + uy * length
        # Perpendicular for the two base corners.
        px, py = -uy, ux
        corner1 = (base_x + px * half_w, base_y + py * half_w)
        corner2 = (base_x - px * half_w, base_y - py * half_w)
        draw.polygon(
            [(marker_x, marker_y), corner1, corner2],
            fill=color,
        )
```

Then modify the per-label loop in `_draw_labels` (existing block at lines ~241-251) so the leader is drawn BEFORE the text and marker (so the marker overpaints the line's far end cleanly):

```python
    for label, (dx, dy) in zip(labels, offsets, strict=True):
        px = label.x - dx
        py = label.y - dy
        if not (0.0 <= px < width and 0.0 <= py < height):
            continue
        if label.text:
            font = _resolve_font(label.font_size)
            tx = px + label.text_offset_x
            ty = py + label.text_offset_y
            text_w, text_h = draw.textbbox((0, 0), label.text, font=font)[2:4]
            if label.leader != "none":
                _draw_leader(
                    draw, px, py, tx, ty, text_w, text_h,
                    label.leader, label.color, label.font_size,
                )
            draw.text((tx, ty), label.text, fill=label.color, font=font)
        _draw_marker(draw, px, py, label.marker, label.color)
        drew_any = True
```

Note: the marker is now drawn LAST so it covers the line endpoint cleanly. That's a behaviour change for old labels — but only when the marker overlaps the text origin (a current artifact already). All `tests/test_labels_render.py` cases that don't use `leader` should still pass.

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_labels_render.py tests/test_labels_coordinates.py -v`

Expected: all existing tests still PASS, plus the new `leader_line_draws_pixels` test PASSES.

- [ ] **Step 2.5: Commit**

```bash
git add src/renderer/labels.py tests/test_labels_render.py
git commit -m "feat(renderer): draw leader='line' between marker and text bbox (#153)"
```

---

### Task 3: Render `leader="arrow"` with arrowhead at marker

**Files:**
- Modify: none (`_draw_leader` already implements the arrow branch in Task 2)
- Test: `tests/test_labels_render.py`

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_labels_render.py`:

```python
def test_draw_labels_with_leader_arrow_paints_arrowhead_at_marker():
    """leader='arrow' lights pixels in the arrowhead triangle near the marker."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="none",  # so the marker doesn't add pixels
        text_offset_x=150, text_offset_y=0,  # horizontal so we know where the arrow base is
        leader="arrow",
        font_size=24,
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # Arrow length = font/3 = 8 px, half-width = font/4 = 6 px.
    # Base centre lies at ~(108, 100); a pixel at (105, 100) sits
    # inside the triangle (closer to the tip).
    assert out[100, 105].max() > 0, (
        f"expected arrowhead pixel at (105, 100), got {out[100, 105]}"
    )
    # A point 30 px away from the marker (clearly outside the arrowhead)
    # but still on the leader line should be lit (line is drawn too).
    assert out[100, 130].max() > 0


def test_draw_labels_leader_line_has_no_arrowhead_extras():
    """leader='line' does NOT paint the arrowhead-only region."""
    img_arrow = _blank(width=400, height=200)
    img_line = _blank(width=400, height=200)
    base = dict(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0, color="#ffffff", marker="none",
        text_offset_x=150, text_offset_y=0, font_size=24,
    )
    arrow_label = Label(**base, leader="arrow")
    line_label = Label(**base, leader="line")
    out_arrow = _draw_labels(
        img_arrow, labels=[arrow_label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    out_line = _draw_labels(
        img_line, labels=[line_label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # (101, 105) is well off the 1-px line for the line-only label
    # but inside the arrow triangle's half-width.
    assert out_arrow[105, 101].max() > 0
    assert out_line[105, 101].max() == 0
```

- [ ] **Step 3.2: Run tests to verify they pass**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_labels_render.py -v -k "arrow"`

Expected: both new tests PASS (Task 2 already implemented the arrow branch).

- [ ] **Step 3.3: Commit**

```bash
git add tests/test_labels_render.py
git commit -m "test(renderer): leader='arrow' arrowhead + asymmetry with leader='line' (#153)"
```

---

### Task 4: Add "Leader Label" toggle button + state field

**Files:**
- Modify: `src/renderer/ui/render_layout.py:1485-1505` (toolbar in `_build_labels_panel`), `:1300-1310` (`_APP_PERSISTED_FIELDS`), `:2300-2305` (`_RenderState` init)

- [ ] **Step 4.1: Add `leader_mode_active` to `_RenderState`**

In `src/renderer/ui/render_layout.py`, locate the `_RenderState.__init__` block where `catalog_mode_active` is initialised (around line 2300). Add directly below it:

```python
        self.leader_mode_active: bool = bool(
            stored.get("leader_mode_active", False),
        )
        self.leader_pending_target: tuple[float, float] | None = None
        self.leader_button: Button | None = None
```

`Button` is already imported. `leader_pending_target` holds the first click's pixel during rubber-band; `None` means "no first click yet". It's runtime state — not persisted.

- [ ] **Step 4.2: Persist the toggle across reloads**

In the same file, `_APP_PERSISTED_FIELDS` (around line 1300) lists which `_RenderState` attrs survive a UI reload. Add `"leader_mode_active"` next to `"catalog_mode_active"`:

```python
_APP_PERSISTED_FIELDS = (
    # ...
    "catalog_mode_active",
    "leader_mode_active",
    # ...
)
```

- [ ] **Step 4.3: Add the toggle button**

In `_build_labels_panel` (around line 1485), insert a button next to the Catalog-mode + Add-label buttons:

```python
                state.catalog_mode_button = ui.button(
                    "Catalog mode", icon="search",
                    on_click=lambda: _toggle_catalog_mode(state),
                ).props("dense flat").tooltip(
                    "Catalog click mode — Hover zeigt nähstes Objekt, "
                    "Klick platziert Label",
                )
                state.leader_button = ui.button(
                    "Leader label", icon="call_made",
                    on_click=lambda: _toggle_leader_mode(state),
                ).props("dense flat").tooltip(
                    "Leader-Line label: 1. Klick = Target, 2. Klick = "
                    "Textposition; bei Klick nahe einem Catalog-Objekt "
                    "wird der Text platziert und die Linie zum Objekt gezogen.",
                )
                ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _toggle_click_to_add(state),
                ).props("dense flat")
```

Then add `_apply_leader_button(state)` to the end of `_build_labels_panel`:

```python
        _refresh_labels_list(state)
        _apply_catalog_mode_button(state)
        _apply_leader_button(state)
```

- [ ] **Step 4.4: Add the toggle + visual-feedback helpers**

Below `_apply_catalog_mode_button` (around line 1620), add:

```python
def _toggle_leader_mode(state: _RenderState) -> None:
    """Toggle leader-line placement mode.

    Mutually exclusive with Catalog and Add-Label modes — turning Leader
    on switches the others off so the JS overlay has one unambiguous
    target. Persisted to app.storage so the toggle survives a reload.
    """
    state.leader_mode_active = not state.leader_mode_active
    if state.leader_mode_active:
        # Exclusivity: kill the other two so the JS overlay knows
        # whose click to deliver.
        state.catalog_mode_active = False
        state.click_to_add_active = False
    state.leader_pending_target = None
    _save_render_state(state)
    _apply_leader_button(state)
    _apply_catalog_mode_button(state)
    _push_catalog_overlay_state(state)
    # Pre-load the FOV slice so the catalog-snap path has data
    # available without the user enabling Catalog Mode first.
    if state.leader_mode_active:
        _refresh_catalog_fov_slice(state)


def _apply_leader_button(state: _RenderState) -> None:
    """Highlight the Leader button when active; reset prompt label."""
    btn = state.leader_button
    if btn is None:
        return
    try:
        if state.leader_mode_active:
            btn.props("dense color=primary")
        else:
            btn.props("dense flat")
    except RuntimeError:
        pass
```

Note: `click_to_add_active` is the existing single-click manual-label state — confirm its name by grepping; substitute the actual attr name if it differs.

- [ ] **Step 4.5: Manually smoke-test the toggle**

Run: `make run-render` and verify in the browser that:
- The "Leader label" button appears between "Catalog mode" and "Add label".
- Clicking it highlights it in primary color.
- Clicking "Catalog mode" while Leader is active deactivates Leader, and vice versa.
- Reloading the page preserves the active toggle.

Expected: all four checks pass. No clicks on the preview do anything new yet — that's Task 5.

- [ ] **Step 4.6: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): Leader-label toggle button + mode state (#153)"
```

---

### Task 5: Manual rubber-band placement

**Files:**
- Modify: `src/renderer/ui/render_layout.py` (`_catalog_overlay_script`, `_handle_catalog_click`, possibly the JS overlay state)

- [ ] **Step 5.1: Extend the JS overlay with a `leader` mode**

In `_catalog_overlay_script` (around line 1830), the `state` object initialised inside the IIFE currently has `{active, objects, frameIndex, natW, natH, tooltipEl}`. Extend it with the new mode fields:

```javascript
  const state = window.__catalogOverlayState[OVERLAY_ID] = (
    window.__catalogOverlayState[OVERLAY_ID]
    || {active: false, mode: 'catalog', objects: [], frameIndex: 0,
         natW: 0, natH: 0, tooltipEl: null,
         leaderPending: null, leaderCanvas: null}
  );
```

`mode` is `'catalog'` (existing #152 behaviour) or `'leader'` (new). `leaderPending` is `{origX, origY}` after the first leader-mode click; `null` otherwise. `leaderCanvas` is a `<canvas>` we draw the rubber-band line on.

- [ ] **Step 5.2: Add a JS API to switch modes**

Currently `window.__catalogOverlaySetActive(id, active)` toggles the overlay on/off. Add a second exported function in the script:

```javascript
  window.__catalogOverlaySetMode = function(id, mode) {
    const s = window.__catalogOverlayState[id];
    if (!s) return;
    s.mode = mode;
    s.leaderPending = null;
    if (s.leaderCanvas) s.leaderCanvas.getContext('2d').clearRect(
      0, 0, s.leaderCanvas.width, s.leaderCanvas.height,
    );
  };
```

- [ ] **Step 5.3: Add rubber-band drawing on mousemove in leader mode**

Inside the same script, after the existing `onMove` handler that drives the catalog tooltip, branch on `state.mode`:

```javascript
  function ensureLeaderCanvas(overlay) {
    if (state.leaderCanvas && document.body.contains(state.leaderCanvas)) {
      return state.leaderCanvas;
    }
    const c = document.createElement('canvas');
    const rect = overlay.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
    c.style.cssText = (
      'position: absolute; top: 0; left: 0; pointer-events: none; '
      + 'width: 100%; height: 100%;'
    );
    overlay.appendChild(c);
    state.leaderCanvas = c;
    return c;
  }

  function drawRubberBand(overlay, ev) {
    if (!state.leaderPending) return;
    const c = ensureLeaderCanvas(overlay);
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, c.width, c.height);
    const rect = overlay.getBoundingClientRect();
    // overlay coords for the pending target
    const proj = projOrigToCss(overlay, state.leaderPending.origX, state.leaderPending.origY);
    ctx.strokeStyle = 'rgba(255, 255, 0, 0.9)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(proj.cssX, proj.cssY);
    ctx.lineTo(ev.clientX - rect.left, ev.clientY - rect.top);
    ctx.stroke();
  }

  function projOrigToCss(overlay, origX, origY) {
    const rect = overlay.getBoundingClientRect();
    const origW = (state.frameDims && state.frameDims[0]) || rect.width;
    const origH = (state.frameDims && state.frameDims[1]) || rect.height;
    return {
      cssX: origX * (rect.width / origW),
      cssY: origY * (rect.height / origH),
    };
  }
```

In the existing `onMove`, wrap the existing tooltip code under `if (state.mode === 'catalog') { ... }` and add `else if (state.mode === 'leader') { drawRubberBand(overlay, ev); }`.

- [ ] **Step 5.4: Handle clicks in leader mode**

In the existing click handler (also inside `_catalog_overlay_script`), branch on mode. For `state.mode === 'leader'`:

```javascript
  function onClickLeader(overlay, ev) {
    const proj = overlayToOrigPixel(overlay, ev);
    if (!state.leaderPending) {
      // First click: try to snap to nearest catalog object within 30 CSS px.
      let snap = null;
      if (state.objects && state.objects.length) {
        const hit = nearest(proj.origX, proj.origY);
        if (hit) {
          // Convert 30 CSS px to orig px for the snap test.
          const rect = overlay.getBoundingClientRect();
          const origW = (state.frameDims && state.frameDims[0]) || rect.width;
          const snapOrigPx = 30 * (origW / rect.width);
          if (hit.dist <= snapOrigPx) snap = hit.obj;
        }
      }
      if (snap) {
        // Catalog snap: single click commits, text = click, target = catalog object.
        emitLeaderClick({
          kind: 'catalog',
          target_x: snap.pixel_x, target_y: snap.pixel_y,
          text_x: proj.origX, text_y: proj.origY,
          catalog_id: snap.id, catalog_name: snap.name,
          ra: snap.ra, dec: snap.dec,
        });
        state.leaderPending = null;
        if (state.leaderCanvas) state.leaderCanvas.getContext('2d').clearRect(
          0, 0, state.leaderCanvas.width, state.leaderCanvas.height,
        );
      } else {
        // Manual: stash target, rubber-band kicks in on next mousemove.
        state.leaderPending = {origX: proj.origX, origY: proj.origY};
      }
    } else {
      // Second click: commit manual leader label.
      emitLeaderClick({
        kind: 'manual',
        target_x: state.leaderPending.origX,
        target_y: state.leaderPending.origY,
        text_x: proj.origX, text_y: proj.origY,
      });
      state.leaderPending = null;
      if (state.leaderCanvas) state.leaderCanvas.getContext('2d').clearRect(
        0, 0, state.leaderCanvas.width, state.leaderCanvas.height,
      );
    }
  }

  function emitLeaderClick(payload) {
    if (window.emitEvent) {
      window.emitEvent('leader_label_click', payload);
    }
  }
```

In the existing onClick handler, replace its body with:

```javascript
  function onClick(ev) {
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay || !state.active) return;
    if (state.mode === 'leader') {
      onClickLeader(overlay, ev);
    } else {
      // existing catalog-mode click handler unchanged
      ...
    }
  }
```

- [ ] **Step 5.5: Dispatch mode + handle the new event in Python**

In `_push_catalog_overlay_state` (around line 1625), also dispatch the current mode whenever the overlay is enabled:

```python
def _push_catalog_overlay_state(state: _RenderState) -> None:
    overlay_id = state.catalog_overlay_id
    if not overlay_id:
        return
    enabled = "true" if (state.catalog_mode_active or state.leader_mode_active) else "false"
    mode = "leader" if state.leader_mode_active else "catalog"
    try:
        ui.run_javascript(
            f"if (window.__catalogOverlaySetActive) "
            f"{{ window.__catalogOverlaySetActive({overlay_id!r}, {enabled}); }}\n"
            f"if (window.__catalogOverlaySetMode) "
            f"{{ window.__catalogOverlaySetMode({overlay_id!r}, {mode!r}); }}",
        )
    except RuntimeError:
        pass
```

Then register the new event handler. Find the existing `ui.on('catalog_label_click', ...)` registration (search for `catalog_label_click` in render_layout.py). Add a sibling:

```python
ui.on('leader_label_click', lambda e: _handle_leader_click(state, e))
```

- [ ] **Step 5.6: Implement `_handle_leader_click`**

Below the existing `_handle_catalog_click` (around line 1790), add:

```python
def _handle_leader_click(state: _RenderState, event) -> None:  # noqa: ANN001
    """Persist a leader-line label when the JS overlay reports a click.

    Two payload shapes:
      * ``kind='manual'``: target + text are both raw pixel positions.
      * ``kind='catalog'``: target is a catalog object's projected
        pixel, text is the click position; we also record the
        ra/dec/id for catalog-source labels.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    args = event.args or {}
    if isinstance(args, list) and args:
        args = args[0]
    if not isinstance(args, dict):
        return
    try:
        kind = args.get("kind") or "manual"
        target_x = float(args["target_x"])
        target_y = float(args["target_y"])
        text_x = float(args["text_x"])
        text_y = float(args["text_y"])
    except (KeyError, TypeError, ValueError):
        logger.warning("leader_label_click payload malformed: %r", args)
        return

    ref_frame_index = state.selected_frame
    base_kwargs = dict(
        id=str(uuid.uuid4()),
        ref_frame_index=ref_frame_index,
        x=target_x, y=target_y,
        text_offset_x=int(round(text_x - target_x)),
        text_offset_y=int(round(text_y - target_y)),
        color="#ffff00",
        marker="circle",
        leader="line",
    )
    if kind == "catalog":
        new_label = Label(
            **base_kwargs,
            text=args.get("catalog_name") or args.get("catalog_id") or "?",
            source="catalog",
            catalog_ra=float(args["ra"]),
            catalog_dec=float(args["dec"]),
            catalog_id=args.get("catalog_id") or None,
        )
    else:
        new_label = Label(
            **base_kwargs,
            text="Label",
            source="manual",
        )
    state.pipeline.project.labels.append(new_label)
    _persist_project(state)
    _refresh_labels_list(state)
    _schedule_preview_refresh(state)
```

- [ ] **Step 5.7: Manual smoke test**

Run: `make run-render`. Activate "Leader label", click somewhere on the preview (not near a catalog object), move the mouse — the rubber-band line should follow. Click again — a new label appears in the list with `leader="line"`, marker at the first click, text near the second click. Reload the page to confirm persistence.

- [ ] **Step 5.8: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): manual rubber-band placement for leader labels (#153)"
```

---

### Task 6: Catalog-snap detection (already wired in Task 5)

Task 5's `onClickLeader` already implements the snap. This task confirms the smoke test and ensures the catalog overlay's FOV slice is populated when Leader mode activates without Catalog mode.

- [ ] **Step 6.1: Confirm `_refresh_catalog_fov_slice` populates state.objects**

Verify the existing `_refresh_catalog_fov_slice` (around line 1645) emits to the JS overlay. If it gates on `state.catalog_mode_active`, widen the gate to include `state.leader_mode_active`:

```python
def _refresh_catalog_fov_slice(state: _RenderState) -> None:
    if not (state.catalog_mode_active or state.leader_mode_active):
        return
    # ... rest unchanged
```

- [ ] **Step 6.2: Manual smoke test — catalog snap**

Run: `make run-render`. Load a project whose ref frame contains a known catalog object (e.g. the Deneb test frame). Activate "Leader label", click ~10 px AWAY from Deneb (within the 30-px snap window). A label should appear immediately with `text="Deneb"`, `catalog_id="Deneb"`, `source="catalog"`; the marker sits at Deneb's projected pixel, the text at the click. No rubber-band — single click commits.

- [ ] **Step 6.3: Commit if step 6.1 changed anything**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "fix(ui): widen FOV-slice gate so leader mode sees catalog objects (#153)"
```

If step 6.1 found the gate already accepted leader mode, skip this commit.

---

### Task 7: Expose `leader` field in the label-edit row

**Files:**
- Modify: `src/renderer/ui/render_layout.py:1529` (`_render_label_row`)

- [ ] **Step 7.1: Find the label-row UI**

Read `_render_label_row` around line 1529. It currently renders text, color, marker, etc. for an existing label. Identify the row layout (likely a `ui.row` with several controls).

- [ ] **Step 7.2: Add a `leader` selector**

Inside `_render_label_row`, next to the existing `marker` select widget, add:

```python
        leader_select = ui.select(
            options={"none": "no leader", "line": "line", "arrow": "arrow"},
            value=label.leader,
            on_change=lambda e, lbl=label: _set_label_leader(state, lbl, e.value),
        ).props("dense").classes("w-28")
```

And below it (or wherever the row's other setters live):

```python
def _set_label_leader(state: _RenderState, label: Label, value: str) -> None:
    """Update a label's leader style and persist."""
    if value not in ("none", "line", "arrow"):
        return
    label.leader = value
    _persist_project(state)
    _schedule_preview_refresh(state)
```

- [ ] **Step 7.3: Manual smoke test**

Run: `make run-render`. Open the Labels panel. Each existing label row should show the leader dropdown. Changing a label from `none` to `arrow` re-renders the preview with a new arrowhead immediately.

- [ ] **Step 7.4: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): per-label leader-style selector in the label list (#153)"
```

---

### Task 8: Final integration smoke + push

- [ ] **Step 8.1: Run the full test suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: all tests PASS, including the 5 new ones (3 model + 2 renderer + arrow asymmetry).

- [ ] **Step 8.2: End-to-end smoke checklist (with the user)**

Run: `make run-render`. Verify with the user:

1. Existing labels (no `leader` field in manifest) load and render exactly as before.
2. Toggle "Leader label" → highlighted; toggling it again deactivates.
3. Activating "Leader label" deactivates "Catalog mode" and "Add label" (and vice versa).
4. Manual: click + move + click → label committed with leader line drawn between the two points.
5. Catalog snap: click within 30 px of a known catalog object → single-click commits with `text` from the catalog.
6. Per-label leader dropdown switches between none/line/arrow and the preview updates.
7. Reload the page → all toggles + labels survive.
8. Capture-points colors on the map still work (regression check for the JS overlay infrastructure).

- [ ] **Step 8.3: Close the issue + push**

```bash
gh issue close 153 --comment "Leader-line labels shipped — Label.leader field, renderer line/arrow branches, Leader-mode toggle with manual rubber-band + catalog snap, per-label leader-style selector. End-to-end smoke green. Spec: docs/superpowers/specs/2026-06-01-leader-line-labels-design.md"
git push origin master
```

---

## Self-Review

Spec coverage check:

| Spec section | Task |
|---|---|
| 4 Data model — `leader` field, backward-compat | Task 1 |
| 5 Rendering — line drawing | Task 2 |
| 5 Rendering — arrow drawing | Task 2 (implementation) + Task 3 (asserts) |
| 6 UX — toolbar toggle | Task 4 |
| 6 UX — manual rubber-band | Task 5 |
| 6 UX — catalog snap | Task 5 (impl) + Task 6 (smoke + gate fix) |
| 7 Tracking across frames | Inherits from existing `text_offset_x/y` semantics; no new code |
| 8 Edge cases — degenerate `text_offset = (0, 0)` | `_draw_leader` early-return on zero delta (Task 2 code) |
| 8 Edge cases — off-frame | Existing `0.0 <= px < width` guard already skips the label whole (Task 2 left it in place) |
| 10 Testing — round-trip | Task 1 |
| 10 Testing — line/arrow renderer | Tasks 2 + 3 |
| 11 Implementation order — model → renderer → UI | Followed exactly |

No placeholders; every code step shows actual code. Type names cross-check: `Label.leader` is `Literal["none","line","arrow"]` in Task 1 and used as a `str` for `style=` in `_draw_leader` (Task 2) — `Literal` is a `str` subtype in pydantic, so passing it directly works.

The label-row selector's `_set_label_leader` writes back to the same pydantic field defined in Task 1 — values match.
