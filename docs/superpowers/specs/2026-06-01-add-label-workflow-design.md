# Add-Label Workflow Unification — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Issue: [#154](https://github.com/zworkb/AstroNightcrawler/issues/154)
Status: design approved, ready for implementation plan

## 1. Goal

Replace the three exclusive label-placement modes ("Catalog mode",
"Leader label", "Add label") with **two modifier checkboxes** + a
**single one-shot action button**. Catalog and Leader become
orthogonal modifiers that shape what "Add Label" does on the next
click sequence, not separate state machines.

## 2. Why now

#152 + #153 left the renderer with three buttons that mutually
exclude each other and three near-duplicate click handlers
(`_handle_catalog_click`, `_handle_leader_click`,
`_handle_preview_click_to_add`). The user has to think about *which
mode* they're in instead of *what kind of label* they want. The
reciprocal-exclusivity code is boilerplate every time someone touches
those toggles. Treating Catalog and Leader as modifiers (the way
keyboard modifiers like Shift/Ctrl shape a single primary action)
collapses the surface area and removes the most common confusion.

## 3. Labels panel placement

Move the entire `_build_labels_panel(...)` call from its current
position (between filmstrip and output settings, around line 145)
to **directly under the preview image** — i.e., before the stretch
controls. Order becomes:

1. Top bar (Browse / Load / Render)
2. Preview image + overlay
3. **Labels panel (NEW position)** ← collapsed by default, costs one
   row of vertical space when not in use
4. Stretch controls
5. Filmstrip
6. Output settings
7. Progress / status

Rationale: placing the labels panel adjacent to the preview means
the user doesn't have to scroll past stretch controls and filmstrip
to reach the placement toolbar — they look at the preview, decide a
label is needed, click the panel right below. Collapsed the panel
is just a one-line `ui.expansion` header so it doesn't crowd the
preview area visually.

## 4. Toolbar layout

A single row in the Labels panel:

```
[☐ Catalog] [☐ Leader] [+ Add label]
```

- **Catalog** — checkbox. When checked, "Add Label" places the marker
  on the geometrically nearest catalog object in the current frame's
  FOV (snap, no distance threshold).
- **Leader** — checkbox. When checked, "Add Label" requires a second
  click for the text position and creates a label with
  `leader="line"` connecting marker to text.
- **Add label** — one-shot button. Clicking it arms the next click
  sequence on the preview. After the sequence completes (or is
  cancelled), the button disarms and the user must click it again
  for the next label.

Both checkboxes show their state via the standard NiceGUI checkbox
visuals; their values persist across page reloads via
`app.storage.general`. "Add Label" is ephemeral — never persisted.

A tooltip on the "Add Label" button describes what the current
modifier combination will do (see the matrix in §5) so the user can
sanity-check before clicking. Example: with both modifiers checked,
the tooltip reads "1 Klick → Text platzieren, Leader-Linie zum
nächsten Catalog-Objekt im FOV".

## 5. Click-behaviour matrix

Modifiers determine the click sequence and the prefilled dialog
defaults:

| Catalog | Leader | Click sequence | Marker placed at | Text-anchor at | Dialog defaults |
|:---:|:---:|---|---|---|---|
| ☐ | ☐ | 1 click | click position | marker + `(12, 0)` legacy offset | text=`"Label"`, marker=`"dot"`, leader=`"none"` |
| ☑ | ☐ | 1 click | nearest catalog object | object position (no offset) | text=object name, marker=`"circle"`, leader=`"none"` |
| ☐ | ☑ | 2 clicks: target then text | first click | second click | text=`"Label"`, marker=`"dot"`, leader=`"line"` |
| ☑ | ☑ | 1 click | nearest catalog object | click position | text=object name, marker=`"circle"`, leader=`"line"` |

For all four combos: after the click sequence the dialog opens with
the prefilled values; the user reviews/edits and presses Save (label
committed) or Cancel (no label). Either button closes the dialog
*and* disarms Add Label.

## 6. Dialog

Reuse the existing label-edit dialog (`_open_edit_popover` in
`src/renderer/ui/render_layout.py`) with the prefilled values from
the matrix. The dialog has fields for:

- Text (string input)
- Color (hex input)
- Font size (number, 6..200)
- Marker (none / dot / cross / circle select)
- **Leader (none / line / arrow select)** — already added in #153

For new-label dialogs (vs editing existing) the dialog also needs to
know the `(target_x, target_y)` and `(text_x, text_y)` so it can
construct the `Label` with the right `x, y`, `text_offset_x/y`,
`source`, and (when catalog) `catalog_ra/dec/id`. This means a new
caller path: `_open_create_dialog(state, target_pixel, text_pixel,
catalog_obj_or_none, leader_default)`. The existing edit-dialog
function gets a parallel "create" variant that shares its UI but
constructs a new label on Save.

## 7. State model

**New `_RenderState` fields**:

- `catalog_modifier_active: bool` — checkbox, persisted
- `leader_modifier_active: bool` — checkbox, persisted
- `pending_placement: PendingPlacement | None` — runtime-only, holds
  the first click of a two-click sequence (replaces `leader_pending_target`)

**Refactored fields**:

- `click_to_add_active: bool` — the "Add Label armed" flag.
  Mutually exclusive with NOTHING else.

**Removed fields**:

- `catalog_mode_active: bool` → replaced by `catalog_modifier_active`
- `leader_mode_active: bool` → replaced by `leader_modifier_active`
- `leader_pending_target` (already removed in #153 follow-up, but the
  new `pending_placement` is its successor for the broader use case)

**Removed functions**:

- `_toggle_catalog_mode` and its exclusivity logic
- `_toggle_leader_mode` and its exclusivity logic
- The reciprocal exclusivity blocks added in #153 commit `7e365b1`
  inside `_toggle_click_to_add`

**New functions**:

- `_toggle_catalog_modifier(state)` — flip checkbox, persist, push
  JS overlay state, refresh FOV slice if newly enabled
- `_toggle_leader_modifier(state)` — flip checkbox, persist, push
  JS overlay state
- `_arm_add_label(state)` — set `click_to_add_active=True`, enable JS
  overlay click capture, update button visuals (highlight when armed)
- `_disarm_add_label(state)` — set False, clear overlay, update visuals

## 8. JS overlay dispatch

The JS overlay (in `_catalog_overlay_script`) currently has
`state.mode = 'catalog' | 'leader'`. Replace with three flags pushed
from Python:

```javascript
state.addMode = false;      // overall pointer-events ON/OFF
state.catalogModifier = false;
state.leaderModifier = false;
```

The Python helper `_push_overlay_state(state)` consolidates the old
`_push_catalog_overlay_state` and explicitly broadcasts all three
flags via a single `window.__catalogOverlaySetState(id, payload)` JS
function (renamed from the old `__catalogOverlaySetActive` and
`__catalogOverlaySetMode`).

Overlay click dispatch becomes:

```javascript
function onClick(ev) {
  if (!state.addMode) return;
  if (state.leaderModifier && !state.pendingTarget) {
    // First click of leader sequence
    if (state.catalogModifier) {
      // Catalog snap: one-click → target = nearest, text = click
      const target = snapNearest(ev);  // always snaps
      emit('label_placement', {
        kind: 'catalog_leader', target, text_at_click,
      });
    } else {
      // Manual leader: stash first click, wait for second
      state.pendingTarget = clickPos;
      // rubber-band starts on next mousemove
    }
    return;
  }
  if (state.leaderModifier && state.pendingTarget) {
    // Second click: text position
    emit('label_placement', {
      kind: 'manual_leader',
      target: state.pendingTarget, text: clickPos,
    });
    state.pendingTarget = null;
    return;
  }
  // No leader → one click
  if (state.catalogModifier) {
    const target = snapNearest(ev);
    emit('label_placement', {kind: 'catalog', target});
  } else {
    emit('label_placement', {kind: 'manual', target: clickPos});
  }
}
```

Single emitted event name (`label_placement`) replaces the three
separate ones. Payload carries `kind` for the Python handler to
branch on.

## 9. Python click handler

A single function `_handle_label_placement(state, event)` replaces
`_handle_catalog_click`, `_handle_leader_click`, and
`_handle_preview_click_to_add`. It:

1. Reads `kind` from the payload.
2. Extracts target/text pixels and (when catalog) the nearest object's
   metadata from the payload.
3. Calls `_open_create_dialog(state, target, text, catalog_obj,
   leader_default)` to open the dialog with prefilled values.
4. Disarms Add Label (`click_to_add_active=False`, button visuals
   updated, JS overlay click-capture disabled).

The dialog's Save handler constructs the `Label` from the dialog
fields plus the position info passed in. The Cancel handler just
closes the dialog. Both also disarm Add Label.

## 10. ESC

Two ESC paths:

1. **During a 2-click leader sequence (after first click, before
   second)**: ESC clears `pending_placement`, the rubber-band canvas,
   and the JS overlay's `pendingTarget`. Add Label stays armed for a
   fresh first click. This is a "cancel the in-progress click
   sequence" not a "cancel Add Label".
2. **During the dialog**: ESC is the NiceGUI dialog's built-in close
   shortcut, which triggers Cancel (per §9 — disarms Add Label).

These are wired separately. The first lives in the JS overlay
(`document` keydown listener already added in #153 follow-up); the
second is NiceGUI's default.

## 11. Soft-migration

On the first session start after upgrade, read the legacy
`catalog_mode_active` and `leader_mode_active` keys from
`app.storage.general` and lift them into
`catalog_modifier_active`/`leader_modifier_active`. Then delete the
legacy keys so we don't migrate twice.

Same pattern as `_maybe_soft_migrate_render_settings` from #151.
Implementation lives in `_RenderState.__init__` (or a dedicated
`_maybe_soft_migrate_modifiers` helper) — runs once at app start
when `_RenderState` first reads from storage.

## 12. Edge cases

- **No catalog objects in FOV with Catalog modifier on**: `snapNearest`
  returns `null`. The click is treated as if Catalog modifier were
  off (manual placement). A toast notification informs the user
  "Catalog FOV is empty — placed manually". The label still gets
  created so the user's click isn't wasted.
- **Browser tab switch mid-2-click-sequence**: `pending_placement`
  is runtime-only (not persisted). After a page reload the partial
  state is gone; user starts fresh by clicking Add Label again.
- **Click outside the preview image with Add Label armed**: the
  overlay's click capture only fires on the overlay; clicks elsewhere
  are not seen. No action — user remains armed.
- **Modifier checked while Add Label is armed**: the next click
  immediately reflects the new modifier state. Useful for users who
  realize mid-arming "actually I want a catalog snap".

## 13. Out of scope (v1)

- Continuous "keep mode" for rapid-fire placement of many labels
  (covered by a separate future issue if needed).
- Keyboard shortcut for "Add Label" (e.g., L key arms placement).
- Visual diff in toolbar between armed and unarmed Add Label beyond
  current button-highlight conventions.
- Custom snap distance threshold for Catalog modifier.

## 14. Testing

The toolbar UI itself has no unit tests today; the existing renderer
and model tests stay green. New testing scope:

- **Renderer & model**: unchanged (the `Label` data structure is
  unchanged; only placement UX moves).
- **Migration**: a small unit test for the soft-migration helper —
  legacy storage values map to new keys, legacy keys deleted.
- **Smoke checklist**: a manual end-to-end pass exercising all four
  modifier combos, ESC handling, and post-Save/post-Cancel state.

## 15. Implementation order

1. **State refactor**: rename fields, add migration. Tests green.
2. **Toolbar UI**: replace three buttons with two checkboxes + one
   button. Wire the toggle helpers. Visual feedback only — no click
   handling change yet (the buttons still call into the old paths).
3. **JS overlay refactor**: combined-flag dispatch, single
   `label_placement` event. Old per-mode handlers still callable but
   unused.
4. **Python click handler consolidation**: `_handle_label_placement`
   that branches on `kind`. Calls into `_open_create_dialog`.
5. **Create-dialog variant**: parallel to existing edit-dialog, takes
   target/text pixels + catalog object + leader default, constructs
   and persists the label on Save.
6. **Delete old code**: `_handle_catalog_click`, `_handle_leader_click`,
   `_toggle_catalog_mode`, `_toggle_leader_mode`, the legacy click-to-add
   popover if subsumed, and the reciprocal exclusivity in
   `_toggle_click_to_add`.
7. **Smoke + commit**.

Each step is mergeable on its own once the prior shipped — old paths
keep working until step 6 deletes them.
