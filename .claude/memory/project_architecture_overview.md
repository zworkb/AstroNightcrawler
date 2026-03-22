---
name: Two-machine architecture
description: Planner/capture runs on Raspberry Pi (StellarMate), rendering on a separate powerful machine - two separate apps
type: project
---

The system consists of two separate applications:

1. **Planner + Capture App** — runs on the EKOS/INDI machine (typically Raspberry Pi with StellarMate). Handles star map planning, path editing, telescope control, and image capture.
2. **Rendering App** — runs on a powerful desktop/workstation. Takes the captured image sequence and produces stacked images and video. Has both a script CLI and a web UI (also NiceGUI).

The captured image sequence must be self-describing: it should contain all metadata needed (point coordinates, sequence order, capture settings, path info) so the rendering app requires minimal user input.

**Why:** Raspberry Pi lacks the power for heavy image processing. The user wants a clean handoff between capture and post-processing.

**How to apply:** Design a well-defined data/export format that travels between the two machines. Keep the planner app lightweight. The rendering app is a separate project/module.