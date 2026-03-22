# Nightcrawler — Implementation Status

Last updated: 2026-03-22

## Summary

| Area | Status |
|------|--------|
| Backend (models, spline, INDI, capture) | Implemented + tested |
| Star map (Stellarium integration) | Working |
| Path drawing (Draw mode) | Working |
| Path drawing (other modes) | Code exists, not wired |
| UI (toolbar, panel, capture view) | Partially working |
| Coordinate system | Az/Alt only, RA/Dec conversion missing |
| Hardware testing | Not started |
| Rendering App | Not started |

---

## Task-by-Task Status

### Fully Complete (implemented, tested, working)

| Task | What | Tests | Notes |
|------|------|-------|-------|
| 1. Data Models | Pydantic models for Project, Path, Settings, etc. | 23 tests | SplinePath allows 0+ points for editing |
| 2. Spline Math | Bézier evaluation, arc length, point sampling | 12 tests | Euclidean coords (no cos(dec) correction) |
| 3. Freehand (RDP) | Ramer-Douglas-Peucker + Bézier fitting | 11 tests | |
| 4. Undo/Redo | UndoStack with memento pattern | 9 tests | |
| 5. INDI Client | Abstract interface + MockINDIClient | 9 tests | reconnect() uses stored host/port |
| 6. FITS Writer | seq_NNNN_MMM.fits naming | 5 tests | |
| 7. Capture Controller | State machine: run/pause/resume/cancel/skip | 10 tests | Safety abort via atexit+signal |
| 14. EKOS Export | Best-effort XML export | 4 tests | |
| 15. Integration Tests | Full workflow, save/load, pause/resume, retry | 5 tests | |

**Total: 88 tests, all passing**

### Working in Browser (implemented + manually verified)

| Feature | Status | Notes |
|---------|--------|-------|
| Star map rendering | **Working** | Stellarium WASM + skydata (mag ≤7) |
| Draw mode (point-by-point) | **Working** | Click to place control points |
| Pan mode | **Working** | Navigate star map freely |
| Spline + capture point rendering | **Working** | Orange control points, blue capture dots, dashed path |
| Projection (pan tracking) | **Working** | Points follow stars when panning, matching engine's stereographic projection |
| Resize handling | **Working** | Overlay re-renders on window resize |
| Bottom panel (settings) | **Working** | Spacing, exposure, gain etc. Apply button triggers recalculation |
| Toolbar mode switching | **Working** | Pan/Draw toggle |
| Setup + build scripts | **Working** | `setup.sh`, `build_stellarium.sh` |

### Code Exists but NOT Wired / NOT Tested in Browser

| Feature | Backend | JS Overlay | Event Wiring | Notes |
|---------|---------|------------|--------------|-------|
| Move points (drag) | — | `move` mode in path_overlay.js | **Missing** | Handler removed from layout.py |
| Remove points | — | `remove_point` mode in path_overlay.js | **Partial** | Handler exists but untested |
| Freehand drawing | freehand.py | `freehand` mode in path_overlay.js | **Missing** | Handler removed from layout.py |
| Add point on segment | — | `add_point` mode in path_overlay.js | **Missing** | |
| Split path | — | `split` mode in path_overlay.js | **Missing** | |
| Undo/Redo | undo.py | — | **Exists in toolbar.py** | Untested in browser |
| Save project | app_state.py | — | **Exists in toolbar.py** | Untested in browser |
| Load project | app_state.py | — | **Exists in toolbar.py** | Untested in browser |
| Start Capture | controller.py | — | **Exists in layout.py** | Untested in browser |
| Capture progress view | capture_view.py | — | **Exists** | Untested in browser |
| INDI connect button | — | — | **Exists in bottom_panel.py** | Untested |
| EKOS export button | ekos.py | — | **Missing from toolbar** | |

### Not Implemented

| Feature | Priority | Notes |
|---------|----------|-------|
| Az/Alt → RA/Dec conversion | **High** | Points stored in Az/Alt frame, INDI needs RA/Dec. Needs observer location + time via astropy |
| Coordinate display (RA/Dec at cursor) | Medium | Was disabled (server roundtrip too chatty). Needs client-side solution |
| Real INDI client (PyINDI) | High | Only mock exists. Needs hardware for testing |
| Bézier handles editing | Medium | Handles render but can't be dragged yet |
| Path persistence across sessions | Medium | Drawn path lost on page reload |
| EKOS export button in UI | Low | Backend works, just needs a toolbar button |
| Rendering App (Plan 2) | Low | FITS → PNG → video pipeline, separate app |

---

## Known Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Projection slight drift on wide FOV | Minor | Stereographic math matches engine closely but not pixel-perfect at FOV >60° |
| `_setting_number` has 8 parameters | Minor | Code review finding, needs SettingConfig dataclass |
| `pixel_to_radec` has 7 parameters | Minor | Needs CameraState dataclass |
| `_props` access on NiceGUI internals | Minor | Button icon swap uses private API |
| Debug console.log in path_overlay.js | Minor | First 20 toScreen calls logged |
| Timer slot error on tab reconnect | Cosmetic | "parent slot deleted" warning in server log |

---

## Architecture Notes

The coordinate system is the biggest open issue. Currently:

1. **Click** → JS `toWorld()` converts pixel to Az/Alt (Stellarium's camera frame)
2. **Store** → Python saves control points as `ra`/`dec` fields (but values are actually Az/Alt)
3. **Render** → JS `toScreen()` converts Az/Alt back to pixels

For real telescope use, we need:
1. Click → Az/Alt (from Stellarium camera)
2. Az/Alt → RA/Dec (via astropy, needs observer lat/lon + time)
3. Store as true RA/Dec
4. For display: RA/Dec → Az/Alt → pixel (reverse path)

This requires the observer location and time, which Stellarium already has (`core.observer.latitude`, `.longitude`, `.utc`).
