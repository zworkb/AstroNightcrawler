---
name: Brainstorm status - Sequence Planner
description: Current brainstorming and planning status for the telescope sequence planner project
type: project
---

## Status

- Brainstorming: **COMPLETE**
- Design Spec: **COMPLETE** — `docs/superpowers/specs/2026-03-22-sequence-planner-design.md`
- Planner/Capture Plan: **COMPLETE** — `docs/superpowers/plans/2026-03-22-planner-capture-app.md` (15 tasks, reviewed 2×)
- Rendering App Plan: **NOT STARTED** (separate plan, after planner/capture is implemented)
- Implementation: **NOT STARTED**

## Key Decisions

- Two apps: Planner/Capture (RPi/StellarMate) + Rendering (powerful desktop)
- NiceGUI web framework, Stellarium Web Engine (WASM/WebGL) for star map
- AGPL engine installed separately via script
- Offline: HiPS tiles locally, Gaia/Hipparcos up to mag 10
- Spline paths (cubic Bézier) with GeoJSON Path rendering + JS SVG overlay for editing
- INDI direct + EKOS export
- FITS + JSON manifest as data interface
- Hybrid UI layout: star map + toolbar + collapsible bottom panel
- Single-user, no auth
- Clean Code standards enforced (cleancode.md + cleancode-python.md)

## Rendering App (decisions made, plan not yet written)

- FITS → PNG (auto-stretch via astropy ZScale+Asinh, manually adjustable)
- 24fps video via ffmpeg, H.264/mp4
- No stacking in v1 (future: Siril integration)
- CLI + NiceGUI web UI