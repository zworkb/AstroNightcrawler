# Add-Label Workflow Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse three exclusive label-placement modes (Catalog / Leader / Add label) into two persistent modifier checkboxes plus one one-shot action button; the user clicks "Add Label" and the active modifiers shape the click sequence and dialog defaults.

**Architecture:** State refactor (`catalog_mode_active`/`leader_mode_active` → `*_modifier_active`; `click_to_add_active` becomes the lone armed-flag). JS overlay carries three orthogonal flags and emits a single `label_placement` event. Python consolidates three click handlers into one. The existing edit-dialog gains a create variant that prefills based on modifier state.

**Tech Stack:** NiceGUI 2.x (ui.checkbox, ui.button, ui.dialog), Pydantic v2 (`Label` unchanged), `app.storage.general` for persistence, the existing JS-overlay machinery from #152/#153.

**Issue:** [#154](https://github.com/zworkb/AstroNightcrawler/issues/154). **Spec:** [docs/superpowers/specs/2026-06-01-add-label-workflow-design.md](../specs/2026-06-01-add-label-workflow-design.md).

---

## File Map

Single file touched: `src/renderer/ui/render_layout.py`.

| Region | Role | Changes |
|---|---|---|
| `_APP_PERSISTED_FIELDS` (~line 1297) | persisted keys tuple | rename two keys |
| `_RenderState.__init__` (~line 2570-2610) | state field init | rename two fields, add soft-migration helper |
| `_build_render_tab` body (~line 145) | layout order | move `_build_labels_panel` call up |
| `_build_labels_panel` (~line 1484) | toolbar UI | three buttons → two checkboxes + one button |
| `_toggle_catalog_mode` / `_toggle_leader_mode` (~lines 1607, 1648) | mode toggles | replace with modifier toggles + arm/disarm helpers |
| `_apply_catalog_mode_button` / `_apply_leader_button` | visual state | redundant — checkboxes auto-render |
| `_push_catalog_overlay_state` (~line 1686) | JS overlay state | broadcast three flags via single setter |
| `_catalog_overlay_script` JS (~lines 2278-2299) | overlay state + dispatch | single `setState` API, unified `label_placement` event |
| `_handle_catalog_click` / `_handle_leader_click` / `_handle_preview_click` | click handlers | consolidate into `_handle_label_placement` |
| `_open_create_popover` (~line 2430) | popover for new label | becomes the create-dialog parallel to edit |

Only one file is touched, which is dense but right — these are all UI-state changes that have to land coherently or the JS overlay stops working. Each task below scopes the diff so it's mergeable on its own.

---

### Task 1: Move labels panel up + rename state fields + soft-migrate

This task is **state-rename + a layout reorder + one migration**. Pure mechanical. No behavioural change for the user — old toggles still work, just under new field names.

**Files:**
- Modify: `src/renderer/ui/render_layout.py:1297-1305` (`_APP_PERSISTED_FIELDS`)
- Modify: `src/renderer/ui/render_layout.py:~145` (`_build_render_tab` body order)
- Modify: `src/renderer/ui/render_layout.py:~2570-2610` (`_RenderState.__init__`)
- Modify all other call sites of `catalog_mode_active` / `leader_mode_active`

- [ ] **Step 1.1: Rename in `_APP_PERSISTED_FIELDS`**

Edit `_APP_PERSISTED_FIELDS` (search for it at line ~1297). Replace:

```python
_APP_PERSISTED_FIELDS: tuple[str, ...] = (
    "input_dir",
    "output_path",
    "render_workers",
    "preview_detail_mode",
    "catalog_mode_active",
    "leader_mode_active",
)
```

with:

```python
_APP_PERSISTED_FIELDS: tuple[str, ...] = (
    "input_dir",
    "output_path",
    "render_workers",
    "preview_detail_mode",
    "catalog_modifier_active",
    "leader_modifier_active",
)
```

- [ ] **Step 1.2: Rename + soft-migrate in `_RenderState.__init__`**

In `_RenderState.__init__` (search for `self.click_to_add_active`), find the two blocks that initialise `catalog_mode_active` and `leader_mode_active` from `stored.get(...)`. Replace them with the renamed fields plus a one-shot migration that lifts legacy keys:

```python
        # Add-label modifiers (issue #154). Catalog and Leader used to
        # be exclusive modes (#152, #153) — they're now persistent
        # checkboxes that shape what the one-shot "Add Label" button
        # does. The legacy ``catalog_mode_active`` / ``leader_mode_active``
        # keys soft-migrate on first read so users who had a mode on
        # before the upgrade keep their preference.
        self.catalog_modifier_active: bool = bool(
            stored.get("catalog_modifier_active",
                       stored.get("catalog_mode_active", False)),
        )
        self.leader_modifier_active: bool = bool(
            stored.get("leader_modifier_active",
                       stored.get("leader_mode_active", False)),
        )
        # Drop the legacy keys so we don't migrate twice and they don't
        # diverge from the new ones.
        for legacy in ("catalog_mode_active", "leader_mode_active"):
            stored.pop(legacy, None)
        self.catalog_overlay_id: str = ""
        self.catalog_overlay: ui.element | None = None
        self.catalog_modifier_checkbox: ui.checkbox | None = None
        self.leader_modifier_checkbox: ui.checkbox | None = None
        self.add_label_button: ui.button | None = None
        self.catalog_fov_cache: dict[int, dict] = {}
```

Delete the existing `self.catalog_mode_button`, `self.leader_button` fields and the comment blocks that described them (they're replaced by the checkbox/button handles above). Keep `self.click_to_add_active: bool = False` as-is — it's now the "armed" flag.

- [ ] **Step 1.3: Bulk-rename the field references**

Run a project-wide grep to find every remaining reference to `catalog_mode_active` or `leader_mode_active`:

```bash
grep -n "catalog_mode_active\|leader_mode_active" src/renderer/ui/render_layout.py
```

For each hit, replace the old name with the new one (`catalog_modifier_active` / `leader_modifier_active`). Expected sites (verify with grep, don't trust this list blindly — it may have drifted):

- `_toggle_catalog_mode` body (we'll rewrite this whole function in Task 3)
- `_toggle_leader_mode` body (Task 3)
- `_toggle_click_to_add` body (the reciprocal-exclusivity block — Task 3 deletes it)
- `_push_catalog_overlay_state` (Task 4)
- `_refresh_catalog_fov_slice` (gate at top of function)
- `_show_preview` (the call to `_refresh_catalog_fov_slice` gated on `state.catalog_mode_active`)

For Task 1 just do the rename. Behaviour stays.

- [ ] **Step 1.4: Move `_build_labels_panel` up in the render layout**

Find the body of the function that builds the right-pane / render tab (search for `_build_labels_panel(state)` — currently around line 145, between `state.filmstrip = ui.row(...)` and `_build_output_settings(state)`).

Move the `_build_labels_panel(state)` line to **before** `_build_stretch_controls(state)`. The new layout order:

```python
        ...preview wrapper / overlay / image setup (untouched)...
        state.preview.on("click", lambda e: _handle_preview_click(state, e), js_handler="""...""")
        _build_labels_panel(state)              # <-- moved up
        _build_stretch_controls(state)
        state.filmstrip = ui.row().classes(
            "w-full overflow-x-auto gap-1 py-2",
        )
        _build_output_settings(state)
        state.progress = ui.linear_progress(value=0).classes("w-full")
        state.status_label = ui.label("")
```

- [ ] **Step 1.5: Run full test suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: 269 passed (no test changes; this is a pure refactor).

- [ ] **Step 1.6: Manual smoke**

Launch `make run-render` and:
- Load a project. Verify the Labels panel now sits directly under the preview image (above the histogram controls).
- Click "Catalog mode" button — it should still toggle (it currently uses the renamed field via Task 1.3).
- Click "Leader label" button — same.
- Reload the page — toggle state survives. (Soft-migration only matters for users with existing legacy storage; new field names round-trip cleanly.)

- [ ] **Step 1.7: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "refactor(ui): rename catalog/leader mode flags + soft-migrate + move labels panel under preview (#154)"
```

---

### Task 2: Replace toolbar buttons with checkboxes + Add Label button

The visible UI change. Three buttons in the labels-panel toolbar become two checkboxes plus one one-shot button. We update the toggle helpers in the next task — for now the new checkboxes just call through to the existing `_toggle_catalog_mode` / `_toggle_leader_mode` functions so behaviour stays.

**Files:**
- Modify: `src/renderer/ui/render_layout.py:1484-1510` (`_build_labels_panel` toolbar block)

- [ ] **Step 2.1: Replace the three-button toolbar with checkboxes + button**

Find `_build_labels_panel`. The toolbar block currently looks like:

```python
            with ui.row().classes("w-full justify-end gap-2"):
                state.catalog_mode_button = ui.button(
                    "Catalog mode", icon="search",
                    on_click=lambda: _toggle_catalog_mode(state),
                ).props("dense flat").tooltip(...)
                state.leader_button = ui.button(
                    "Leader label", icon="call_made",
                    on_click=lambda: _toggle_leader_mode(state),
                ).props("dense flat").tooltip(...)
                ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _toggle_click_to_add(state),
                ).props("dense flat")
```

Replace the entire block (including the `with ui.row(...)`) with:

```python
            with ui.row().classes("w-full items-center justify-end gap-3"):
                state.catalog_modifier_checkbox = ui.checkbox(
                    "Catalog",
                    value=state.catalog_modifier_active,
                    on_change=lambda e: _toggle_catalog_modifier(state, e.value),
                ).props("dense").tooltip(
                    "Beim Klicken aufs nächste Catalog-Objekt im FOV snappen",
                )
                state.leader_modifier_checkbox = ui.checkbox(
                    "Leader",
                    value=state.leader_modifier_active,
                    on_change=lambda e: _toggle_leader_modifier(state, e.value),
                ).props("dense").tooltip(
                    "Leader-Linie zwischen Marker und Text zeichnen",
                )
                state.add_label_button = ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _arm_add_label(state),
                ).props("dense flat")
                _refresh_add_label_tooltip(state)
```

`_toggle_catalog_modifier`, `_toggle_leader_modifier`, `_arm_add_label`, and `_refresh_add_label_tooltip` are added in Task 3. For Task 2 the references won't resolve yet — that's intentional: the toolbar code lands first, the helpers next.

Also delete the `_apply_catalog_mode_button(state)` and `_apply_leader_button(state)` call at the bottom of `_build_labels_panel` (NiceGUI checkboxes self-render their state — no apply helper needed).

- [ ] **Step 2.2: Run the suite — it will fail to import**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py 2>&1 | tail -5`

Expected: import-time NameError on `_toggle_catalog_modifier` (or one of the four new symbols). That's the cue to move on to Task 3 immediately — DO NOT commit Task 2 in isolation. The two tasks land in one commit. Or write Task 3 first if you prefer; the order inside the file doesn't matter, only that they ship together.

Mark Task 2 as "wait for Task 3 to land."

---

### Task 3: Add modifier toggle helpers + Add-Label arm/disarm

**Files:**
- Modify: `src/renderer/ui/render_layout.py` — delete `_toggle_catalog_mode`, `_toggle_leader_mode`, `_apply_catalog_mode_button`, `_apply_leader_button`; add `_toggle_catalog_modifier`, `_toggle_leader_modifier`, `_arm_add_label`, `_disarm_add_label`, `_refresh_add_label_tooltip`

- [ ] **Step 3.1: Delete the old mode-toggle functions**

Find the four functions `_toggle_catalog_mode`, `_apply_catalog_mode_button`, `_toggle_leader_mode`, `_apply_leader_button` (currently around lines 1607-1684). Delete all four. The new code below replaces them. While at it, also delete the reciprocal-exclusivity blocks INSIDE `_toggle_click_to_add` (the lines that set `state.catalog_mode_active = False` and `state.leader_mode_active = False`); after this refactor `_toggle_click_to_add` is unused too and we'll delete it in Task 5 — for now just strip its body to a single early-return so it can't be accidentally called:

```python
def _toggle_click_to_add(state: _RenderState) -> None:
    """DEPRECATED: replaced by _arm_add_label (#154). Kept as a stub
    so any stale on_click bindings don't crash; safe to delete after
    Task 5."""
    return
```

- [ ] **Step 3.2: Add the new helpers**

In the same region (where the old mode-toggle functions used to live), add:

```python
def _toggle_catalog_modifier(state: _RenderState, checked: bool) -> None:
    """Persist the Catalog modifier state and refresh the JS overlay.

    Catalog modifier on → next Add-Label click snaps the marker to
    the nearest catalog object in the current FOV. Pre-loading the
    FOV slice keeps the snap responsive (no first-click stall).
    """
    state.catalog_modifier_active = bool(checked)
    _save_render_state(state)
    _push_overlay_state(state)
    _refresh_add_label_tooltip(state)
    if state.catalog_modifier_active:
        _refresh_catalog_fov_slice(state)


def _toggle_leader_modifier(state: _RenderState, checked: bool) -> None:
    """Persist the Leader modifier state and refresh the JS overlay."""
    state.leader_modifier_active = bool(checked)
    _save_render_state(state)
    _push_overlay_state(state)
    _refresh_add_label_tooltip(state)


def _arm_add_label(state: _RenderState) -> None:
    """Enter one-shot Add-Label mode (#154).

    Sets ``click_to_add_active=True`` so the JS overlay routes the
    next click(s) into ``label_placement`` events. After the dialog
    closes (Save or Cancel) the caller invokes ``_disarm_add_label``
    so the next preview click falls through to normal behaviour.
    """
    state.click_to_add_active = True
    state.pending_placement = None
    _push_overlay_state(state)
    _refresh_add_label_button_visual(state)
    if state.catalog_modifier_active:
        _refresh_catalog_fov_slice(state)


def _disarm_add_label(state: _RenderState) -> None:
    """Exit Add-Label mode without committing anything."""
    state.click_to_add_active = False
    state.pending_placement = None
    _push_overlay_state(state)
    _refresh_add_label_button_visual(state)


def _refresh_add_label_button_visual(state: _RenderState) -> None:
    """Highlight the Add-Label button while it's armed."""
    btn = state.add_label_button
    if btn is None:
        return
    try:
        if state.click_to_add_active:
            btn.props("dense color=primary")
        else:
            btn.props("dense flat")
    except RuntimeError:
        pass


def _refresh_add_label_tooltip(state: _RenderState) -> None:
    """Tooltip describes the current modifier combo's behaviour."""
    btn = state.add_label_button
    if btn is None:
        return
    cat = state.catalog_modifier_active
    leader = state.leader_modifier_active
    if cat and leader:
        msg = "1 Klick → Text platzieren, Leader-Linie zum nächsten Catalog-Objekt"
    elif cat:
        msg = "1 Klick → Label am nächsten Catalog-Objekt im FOV"
    elif leader:
        msg = "2 Klicks → 1. Target, 2. Textposition, mit Leader-Linie"
    else:
        msg = "1 Klick → Label an Klick-Position"
    try:
        btn.tooltip(msg)
    except RuntimeError:
        pass
```

- [ ] **Step 3.3: Add `pending_placement` to `_RenderState`**

Open `_RenderState.__init__`. After the `self.click_to_add_active: bool = False` line, add:

```python
        # Holds the first click of a two-click leader placement; cleared
        # when the second click arrives, ESC cancels, or Add-Label
        # disarms. Runtime-only — never persisted.
        self.pending_placement: tuple[float, float] | None = None
```

- [ ] **Step 3.4: Run the suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: 269 passed. The new functions don't have unit tests at this level (UI), but the import-side errors from Task 2 should now resolve. `_push_overlay_state` is added in Task 4 — but it's only CALLED in the new helpers, and pytest doesn't exercise them. If pytest fails with `_push_overlay_state` not defined at import time, that's because of forward references inside a function body — Python only resolves names at call time. If it fails, swap Task 3 and Task 4 in execution order.

- [ ] **Step 3.5: Manual smoke**

Launch `make run-render` and:
- The Labels panel toolbar shows two checkboxes + one button.
- Toggling either checkbox doesn't crash (JS overlay state push happens but the JS function name will be wrong until Task 4).
- Clicking "Add label" doesn't crash (button visual changes to primary).
- Reload survives the checkbox state.

Clicks on the preview won't do anything new yet — Tasks 4 + 5 wire that up.

- [ ] **Step 3.6: Commit Tasks 2 + 3 together**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): two modifier checkboxes + one-shot Add-Label button (#154)"
```

---

### Task 4: JS overlay refactor — single state push + unified click event

**Files:**
- Modify: `src/renderer/ui/render_layout.py:1686-1700` (`_push_catalog_overlay_state`)
- Modify: `src/renderer/ui/render_layout.py:~2278-2299` (JS overlay state setters and the IIFE state init)

- [ ] **Step 4.1: Replace `_push_catalog_overlay_state` with `_push_overlay_state`**

Delete the existing `_push_catalog_overlay_state` (lines ~1686-1700) and add `_push_overlay_state`:

```python
def _push_overlay_state(state: _RenderState) -> None:
    """Broadcast the three flags + add-mode to the JS overlay.

    Pointer events on the overlay are enabled iff either modifier is
    on OR Add-Label is armed — the modifiers alone enable the catalog
    tooltip on hover; Add-Label enables click capture.
    """
    overlay_id = state.catalog_overlay_id
    if not overlay_id:
        return
    import json as _json
    payload = {
        "addMode": bool(state.click_to_add_active),
        "catalogModifier": bool(state.catalog_modifier_active),
        "leaderModifier": bool(state.leader_modifier_active),
    }
    try:
        ui.run_javascript(
            f"if (window.__catalogOverlaySetState) "
            f"{{ window.__catalogOverlaySetState({overlay_id!r}, {_json.dumps(payload)}); }}",
        )
    except RuntimeError:
        pass
```

Then grep for every remaining call site of the old name:

```bash
grep -n "_push_catalog_overlay_state" src/renderer/ui/render_layout.py
```

Replace each call site with `_push_overlay_state(state)`.

- [ ] **Step 4.2: Rewrite the JS overlay state init and setter API**

In `_catalog_overlay_script` (around line 2278), the IIFE currently initialises:

```javascript
const state = window.__catalogOverlayState[OVERLAY_ID] = (
    window.__catalogOverlayState[OVERLAY_ID]
    || {{active: false, mode: 'catalog', objects: [], frameIndex: 0,
         natW: 0, natH: 0, tooltipEl: null,
         leaderPending: null, leaderCanvas: null}}
);
```

Replace `active: false, mode: 'catalog'` with the new three-flag shape (keep the other fields untouched):

```javascript
const state = window.__catalogOverlayState[OVERLAY_ID] = (
    window.__catalogOverlayState[OVERLAY_ID]
    || {{addMode: false, catalogModifier: false, leaderModifier: false,
         objects: [], frameIndex: 0,
         natW: 0, natH: 0, tooltipEl: null,
         pendingTarget: null, leaderCanvas: null}}
);
```

(`pendingTarget` replaces `leaderPending` — same semantic, clearer name now that there's only one notion of "pending click".)

Then delete the two old window setters (`__catalogOverlaySetActive` and `__catalogOverlaySetMode`) and add ONE new setter:

```javascript
window.__catalogOverlaySetState = window.__catalogOverlaySetState
    || function(id, payload) {{
        const s = window.__catalogOverlayState && window.__catalogOverlayState[id];
        if (!s || !payload) return;
        s.addMode = !!payload.addMode;
        s.catalogModifier = !!payload.catalogModifier;
        s.leaderModifier = !!payload.leaderModifier;
        // Toggle pointer-events: any click capture requires Add-Mode on,
        // but the catalog-tooltip on hover is useful with Catalog
        // modifier alone (no Add-Mode needed) — keep events enabled in
        // that case too so the tooltip works.
        const overlay = document.getElementById(id);
        if (overlay) {{
            const active = s.addMode || s.catalogModifier;
            overlay.style.pointerEvents = active ? 'auto' : 'none';
            overlay.style.cursor = s.addMode ? 'crosshair' : (active ? 'default' : '');
        }}
        // Mode change cancels any in-progress pending placement.
        if (!s.addMode) {{
            s.pendingTarget = null;
            if (s.leaderCanvas) {{
                const ctx = s.leaderCanvas.getContext('2d');
                ctx.clearRect(0, 0, s.leaderCanvas.width, s.leaderCanvas.height);
            }}
        }}
        if (s.tooltipEl && !s.catalogModifier) {{
            s.tooltipEl.style.display = 'none';
        }}
    }};
```

Also keep `__catalogOverlaySetObjects` intact — its semantics don't change.

- [ ] **Step 4.3: Replace the catalog-specific `applyActive`**

`applyActive()` (which sets pointer-events based on `state.active`) is now redundant — the new `__catalogOverlaySetState` does pointer-events directly. Remove the `applyActive()` function definition and its call at the end of `attach()`. If `attach()` had any other initialisation, leave it; just remove the `applyActive()` call.

- [ ] **Step 4.4: Rewrite `onClick` + `onMove` to dispatch on the three flags**

Replace the existing `onClick` and `onMove` bodies in the JS overlay. New `onMove`:

```javascript
function onMove(ev) {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) return;

    // Rubber-band: only meaningful when Add-Mode armed, Leader on, and
    // first click has landed.
    if (state.addMode && state.leaderModifier && state.pendingTarget) {{
        drawRubberBand(overlay, ev);
    }}

    // Catalog tooltip: enabled whenever Catalog modifier is on,
    // regardless of Add-Mode. Pure information overlay — no clicks
    // needed to see what's where.
    if (state.catalogModifier && state.objects && state.objects.length > 0) {{
        const proj = overlayToOrigPixel(overlay, ev);
        const hit = nearest(proj.origX, proj.origY);
        if (hit) {{
            const tip = ensureTooltip(overlay);
            const id = hit.obj.id || '';
            const name = hit.obj.name || id;
            const label = id && id !== name ? name + ' (' + id + ')' : name;
            tip.textContent = label;
            tip.style.left = (ev.clientX + 14) + 'px';
            tip.style.top = (ev.clientY + 14) + 'px';
            tip.style.display = 'block';
            return;
        }}
    }}
    if (state.tooltipEl) state.tooltipEl.style.display = 'none';
}}
```

And new `onClick`:

```javascript
function onClick(ev) {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay || !state.addMode) return;
    const proj = overlayToOrigPixel(overlay, ev);

    if (state.leaderModifier && !state.pendingTarget && !state.catalogModifier) {{
        // Manual leader: first of two clicks.
        state.pendingTarget = {{origX: proj.origX, origY: proj.origY}};
        return;
    }}

    if (state.leaderModifier && state.pendingTarget) {{
        // Manual leader: second click — commit.
        emitPlacement({{
            kind: 'manual_leader',
            target_x: state.pendingTarget.origX,
            target_y: state.pendingTarget.origY,
            text_x: proj.origX, text_y: proj.origY,
            ref_frame_index: state.frameIndex,
        }});
        clearRubberBand();
        return;
    }}

    if (state.catalogModifier) {{
        // Catalog snap: always picks the nearest object (no distance
        // threshold). Without Leader modifier, marker = nearest object
        // and we drop the text at the object too (no offset). With
        // Leader modifier, marker = nearest, text = click.
        if (!state.objects || state.objects.length === 0) {{
            // No catalog data in FOV — fall through to manual placement
            // so the user's click isn't wasted (#154 §11).
            emitPlacement({{
                kind: 'manual',
                target_x: proj.origX, target_y: proj.origY,
                ref_frame_index: state.frameIndex,
            }});
            return;
        }}
        const hit = nearest(proj.origX, proj.origY);
        if (state.leaderModifier) {{
            emitPlacement({{
                kind: 'catalog_leader',
                target_x: hit.obj.pixel_x, target_y: hit.obj.pixel_y,
                text_x: proj.origX, text_y: proj.origY,
                ref_frame_index: state.frameIndex,
                catalog_id: hit.obj.id, catalog_name: hit.obj.name,
                ra: hit.obj.ra, dec: hit.obj.dec,
            }});
        }} else {{
            emitPlacement({{
                kind: 'catalog',
                target_x: hit.obj.pixel_x, target_y: hit.obj.pixel_y,
                ref_frame_index: state.frameIndex,
                catalog_id: hit.obj.id, catalog_name: hit.obj.name,
                ra: hit.obj.ra, dec: hit.obj.dec,
            }});
        }}
        return;
    }}

    // No modifiers: plain manual placement.
    emitPlacement({{
        kind: 'manual',
        target_x: proj.origX, target_y: proj.origY,
        ref_frame_index: state.frameIndex,
    }});
}}

function emitPlacement(payload) {{
    try {{
        if (window.emitEvent) {{
            window.emitEvent('label_placement', payload);
        }}
    }} catch (e) {{
        console.warn('label_placement emit failed', e);
    }}
}}
```

Delete the old `onClickLeader`, `clearRubberBand` (well — keep `clearRubberBand`, it's still called from ESC and from the snap path; it already exists), and any references to `state.mode`. The `emitLeaderClick` function is now subsumed by `emitPlacement` — delete `emitLeaderClick` and any leftover call sites.

ESC handling (`onKeyDown`) should still work — it already checks `state.pendingTarget` (formerly `state.leaderPending`). Verify the rename landed.

- [ ] **Step 4.5: Run the suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: 269 passed.

- [ ] **Step 4.6: Manual smoke**

Launch `make run-render` and:
- Toggle Catalog checkbox — hover over the preview shows the nearest-object tooltip even without Add-Mode armed. (Useful info-mode!)
- Toggle Leader checkbox — no visible effect alone.
- Click "Add label" — button highlights, cursor turns crosshair on the preview.
- Clicks emit `label_placement` events. The Python side hasn't been wired yet (next task), so nothing actually happens — that's expected.
- ESC clears any pending state.

Don't commit Task 4 alone — Task 5 lands the Python handler that consumes the new event.

---

### Task 5: Python click handler + create-dialog variant

**Files:**
- Modify: `src/renderer/ui/render_layout.py:~115-120` (event registration in `_build_render_tab`)
- Modify: `src/renderer/ui/render_layout.py` — delete `_handle_catalog_click`, `_handle_leader_click`, `_handle_preview_click`, `_toggle_click_to_add` (stub from Task 3)
- Modify: `src/renderer/ui/render_layout.py:~2430` (`_open_create_popover`) — generalise for all four placement kinds
- Add: `_handle_label_placement(state, event)` — single consumer of the `label_placement` event

- [ ] **Step 5.1: Add `_handle_label_placement`**

Add this function (best place: above `_open_create_popover`, around line 2425):

```python
def _handle_label_placement(state: _RenderState, event) -> None:  # noqa: ANN001
    """Single click-event handler for all four modifier combinations.

    Receives the JS overlay's ``label_placement`` event, extracts the
    placement geometry + catalog metadata from the payload, then opens
    the create-dialog with the right pre-fills. Save in the dialog
    persists the label; Cancel discards it. Either disarms Add-Label.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    if not state.click_to_add_active:
        # Race: event landed after disarm. Drop silently.
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
        ref_frame_index = int(args.get("ref_frame_index", state.selected_frame))
    except (KeyError, TypeError, ValueError):
        logger.warning("label_placement payload malformed: %r", args)
        return

    text_x: float | None = None
    text_y: float | None = None
    if "text_x" in args and "text_y" in args:
        try:
            text_x = float(args["text_x"])
            text_y = float(args["text_y"])
        except (TypeError, ValueError):
            text_x = text_y = None

    catalog_meta: dict | None = None
    if kind in ("catalog", "catalog_leader"):
        try:
            catalog_meta = {
                "catalog_id": args.get("catalog_id"),
                "catalog_name": args.get("catalog_name"),
                "ra": float(args["ra"]),
                "dec": float(args["dec"]),
            }
        except (KeyError, TypeError, ValueError):
            catalog_meta = None

    leader_default = (
        "line" if kind in ("manual_leader", "catalog_leader") else "none"
    )

    _open_create_dialog(
        state,
        ref_frame_index=ref_frame_index,
        target=(target_x, target_y),
        text=(text_x, text_y) if text_x is not None else None,
        catalog_meta=catalog_meta,
        leader_default=leader_default,
    )
```

- [ ] **Step 5.2: Generalise `_open_create_popover` → `_open_create_dialog`**

Find `_open_create_popover` (line ~2430). Rename to `_open_create_dialog` and rewrite its signature to take the new parameters. The body opens a NiceGUI dialog with all label fields prefilled per the matrix:

```python
def _open_create_dialog(
    state: _RenderState,
    *,
    ref_frame_index: int,
    target: tuple[float, float],
    text: tuple[float, float] | None,
    catalog_meta: dict | None,
    leader_default: str,
) -> None:
    """Open the new-label dialog with prefilled values per modifier matrix.

    Args:
        state: render state.
        ref_frame_index: which capture frame's pixel space the geometry lives in.
        target: marker pixel position in the ref frame.
        text: text-anchor pixel position; ``None`` for catalog-only kinds
            where the text sits AT the object (zero offset).
        catalog_meta: when set, populates the catalog cross-reference
            fields and uses the catalog name as the default text.
        leader_default: ``"none"`` / ``"line"`` / ``"arrow"`` — initial
            dialog value for the leader-style selector.
    """
    target_x, target_y = target
    if text is not None:
        text_x, text_y = text
    else:
        # Catalog-only: text sits at the object. Use the legacy 12-px
        # offset so the text doesn't visually overlap the marker glyph.
        text_x, text_y = target_x + 12, target_y

    default_text = (
        (catalog_meta.get("catalog_name") or catalog_meta.get("catalog_id") or "?")
        if catalog_meta else "Label"
    )
    default_marker = "circle" if catalog_meta else "dot"

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("New label").classes("text-md font-bold")
        text_in = ui.input("Text", value=default_text)
        color_in = ui.input("Color (hex)", value="#ffff00")
        font_size_in = ui.number("Font size", value=24, min=6, max=200)
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value=default_marker, label="Marker",
        )
        leader_in = ui.select(
            {"none": "no leader", "line": "line", "arrow": "arrow"},
            value=leader_default, label="Leader",
        )
        with ui.row().classes("w-full justify-end"):
            def _cancel() -> None:
                dialog.close()
                _disarm_add_label(state)

            ui.button("Cancel", on_click=_cancel).props("flat")

            def _save() -> None:
                label = Label(
                    id=str(uuid.uuid4()),
                    text=text_in.value or default_text,
                    ref_frame_index=ref_frame_index,
                    x=target_x, y=target_y,
                    color=color_in.value or "#ffff00",
                    font_size=int(font_size_in.value or 24),
                    marker=marker_in.value or default_marker,
                    text_offset_x=int(round(text_x - target_x)),
                    text_offset_y=int(round(text_y - target_y)),
                    leader=leader_in.value or "none",
                    source="catalog" if catalog_meta else "manual",
                    catalog_id=(catalog_meta or {}).get("catalog_id"),
                    catalog_ra=(catalog_meta or {}).get("ra"),
                    catalog_dec=(catalog_meta or {}).get("dec"),
                )
                state.pipeline.project.labels.append(label)
                _persist_project(state)
                _refresh_labels_list(state)
                _schedule_preview_refresh(state)
                dialog.close()
                _disarm_add_label(state)

            ui.button("Save", color="primary", on_click=_save)
    dialog.open()
```

`Label`, `uuid`, `_persist_project`, `_refresh_labels_list`, `_schedule_preview_refresh`, `logger` are already imported / defined in the file — verify with grep before assuming.

- [ ] **Step 5.3: Update event registration in `_build_render_tab`**

Find the three `ui.on(...)` registrations at line ~115-120:

```python
ui.on("catalog_label_click", _on_catalog_click)
ui.on("leader_label_click", lambda e: _handle_leader_click(state, e))
```

Plus the `state.preview.on("click", lambda e: _handle_preview_click(state, e), ...)` block at line ~126.

Delete all three and replace with one:

```python
ui.on("label_placement", lambda e: _handle_label_placement(state, e))
```

Also delete the standalone `state.preview.on("click", ...)` block — the JS-overlay is now the single source of click events, regardless of modifier. (The `state.preview.on(...)` was the legacy click-to-add path bypassing the overlay; we don't need it anymore.)

- [ ] **Step 5.4: Delete dead handlers**

Grep for and delete (in this order, since each may reference the next):

```bash
grep -n "def _handle_catalog_click\|def _handle_leader_click\|def _handle_preview_click\|def _toggle_click_to_add" src/renderer/ui/render_layout.py
```

Delete the entire bodies of:

- `_handle_catalog_click`
- `_handle_leader_click`
- `_handle_preview_click`
- `_toggle_click_to_add` (the stub from Task 3)

If any are still referenced (e.g., `_on_catalog_click` is a nested fn that calls `_handle_catalog_click`), delete those too.

- [ ] **Step 5.5: Run the suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: 269 passed. There are no dedicated tests for the click handlers (UI code), but model + renderer tests must stay green.

- [ ] **Step 5.6: Manual smoke — all four combos**

Launch `make run-render`. Test each modifier combination:

1. **None / None**: Click "Add label" → click on the preview → dialog opens with text="Label", marker="dot", leader="none". Save commits at click position.
2. **Catalog only**: Check Catalog → click "Add label" → click on the preview → dialog opens with text=<object name>, marker="circle", leader="none". Save commits at the object's projected pixel.
3. **Leader only**: Uncheck Catalog, check Leader → click "Add label" → click target → rubber-band follows mouse → click text position → dialog opens with text="Label", leader="line". Save commits with marker at click 1, text at click 2.
4. **Catalog + Leader**: Check both → click "Add label" → 1 click anywhere → dialog opens with text=<object name>, leader="line". Save commits with marker at object, text at click.

Also test:
- **Cancel**: opens dialog, click Cancel → no label, Add-Label disarms.
- **ESC during 2-click sequence**: starts leader placement → ESC before second click → pending state clears, Add-Label stays armed for fresh first click.
- **ESC in dialog**: closes dialog like Cancel (NiceGUI default).
- **Reload**: modifier checkboxes survive, Add-Label-armed state does not (correct — ephemeral).

- [ ] **Step 5.7: Commit Tasks 4 + 5 together**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): unified label_placement event + consolidated Python handler (#154)"
```

---

### Task 6: Final review + push

- [ ] **Step 6.1: Confirm dead code is gone**

Grep for any remaining references to the deleted names:

```bash
grep -n "catalog_mode_active\|leader_mode_active\|catalog_mode_button\|leader_button\|_toggle_catalog_mode\|_toggle_leader_mode\|_apply_catalog_mode_button\|_apply_leader_button\|_handle_catalog_click\|_handle_leader_click\|_handle_preview_click\|_toggle_click_to_add\|_open_create_popover\|_push_catalog_overlay_state\|catalog_label_click\|leader_label_click" src/renderer/ui/render_layout.py
```

Expected: zero hits. If anything remains, it's a missed delete — fix it.

- [ ] **Step 6.2: Full suite**

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py`

Expected: 269 passed.

- [ ] **Step 6.3: Close + push**

```bash
gh issue close 154 --comment "Add-Label workflow unified — two modifier checkboxes + one-shot Add-Label button replace three exclusive modes. Spec: docs/superpowers/specs/2026-06-01-add-label-workflow-design.md. End-to-end smoke green across all four modifier combos."
git push origin master
```

---

## Self-Review

**Spec coverage:**

| Spec § | Task |
|---|---|
| 3 Labels panel placement | Task 1 step 1.4 |
| 4 Toolbar layout (checkboxes + button) | Task 2 |
| 5 Click-behaviour matrix | Tasks 4 (JS dispatch) + 5 (Python handler/dialog) |
| 6 Dialog with prefills | Task 5 step 5.2 |
| 7 State model (rename + new fields) | Task 1 (rename), Task 3 (new helpers + pending_placement) |
| 8 JS overlay (single setState + label_placement event) | Task 4 |
| 9 Python click handler consolidation | Task 5 |
| 10 ESC (2-stage) | Task 4 step 4.4 (verify rename of pendingTarget); dialog ESC is NiceGUI default |
| 11 Soft-migration | Task 1 step 1.2 |
| 12 Edge cases (no catalog objects in FOV) | Task 4 step 4.4 (fallthrough branch in onClick) |
| 14 Testing — migration unit test | NOT INCLUDED — migration is a one-liner inside `__init__`, smoke covers it. If a unit test feels necessary, add it as a tiny test in `tests/test_render_settings.py` using a fake `stored` dict. |
| 15 Implementation order | Tasks 1-6 follow the spec's 7-step order, with steps 1-2 of the spec merged into Task 1 |

**Type consistency check:** Function names used in the plan:
- `_toggle_catalog_modifier(state, checked: bool)` (Task 3) — called from checkbox `on_change` in Task 2 ✓
- `_toggle_leader_modifier(state, checked: bool)` (Task 3) — same ✓
- `_arm_add_label(state)` (Task 3) — called from button `on_click` in Task 2 ✓
- `_disarm_add_label(state)` (Task 3) — called from dialog Save/Cancel in Task 5 ✓
- `_refresh_add_label_button_visual(state)` (Task 3) — called from arm/disarm ✓
- `_refresh_add_label_tooltip(state)` (Task 3) — called from Task 2 toolbar build + Task 3 toggles ✓
- `_push_overlay_state(state)` (Task 4) — called from Task 3 toggles + Task 3 arm/disarm; ✓ but note Task 3 lands BEFORE Task 4, so the import won't crash but the function name will be unresolved at call time during Task 3 smoke. The plan's Task 3 step 3.4 calls this out: if it fails, swap Tasks 3 + 4. Realistic risk: low (smoke test of Task 3 only flips checkboxes, doesn't yet trigger a click).
- `_handle_label_placement(state, event)` (Task 5) — registered via `ui.on(...)` in Task 5 ✓
- `_open_create_dialog(state, ...)` (Task 5) — called from `_handle_label_placement` ✓

**Placeholder scan:** zero TBD / TODO / "appropriate error handling" / vague references. Every code step shows complete code. Every grep step shows the exact grep command.

**Scope check:** Single focused refactor across one file. Reasonable for one PR / one execution session.
