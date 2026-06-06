# Orphan FITS Recovery — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Status: design approved, ready for implementation plan

## 1. Goal

Give the user a "Manifest prüfen" button that detects FITS files on
disk which aren't referenced by any `CapturePoint.frames[]` in
`manifest.json`. If orphans are found, show them in a modal with
their proposed capture-point assignment; user clicks **Reparieren**
to apply patches atomically (with a timestamped backup of the old
manifest). Nothing happens silently on load — repair is always
explicit and user-triggered.

## 2. Why now

A user hit this concretely (#154 follow-up smoke). A re-capture run
wrote `seq_0019_001.fits` to disk but the manifest's
`CapturePoint(index=18).frames` stayed empty. The renderer's importer
skipped point 18 → silent gap in the filmstrip → user spent time
wondering why "frame #18" was missing. We worked around it with a
hand-written python patch; the next user shouldn't need to.

Empty capture points are the third "manifest vs. disk divergence" the
project has surfaced (after #143's missing-thumb regen and #150's
self-heal venv). The first two were fixed transparently; the user has
expressed a clear preference that manifest mutations stay explicit so
the renderer doesn't surprise them by editing project state on load.

## 3. Concept summary — explicit Check + Repair flow

**No implicit work on load.** The user gets a "Manifest prüfen"
button in the render UI. Click runs a read-only scan and surfaces
the result:

1. Click "Manifest prüfen" → backend runs `find_orphans(capture_dir,
   project)`.
2. **No orphans** → `ui.notify` "Manifest ist konsistent — keine
   verwaisten Dateien gefunden" and we're done.
3. **Orphans found** → open a modal listing each orphan: its
   filename, the capture-point index it would be assigned to, and
   whether the target capture point currently has 0 or N existing
   frames. Two buttons: **Abbrechen** (close, no changes) and
   **Reparieren** (apply patches + atomic-write manifest +
   timestamped backup + notify "N Dateien repariert").

The reconciler splits into two pure functions:

- `find_orphans(capture_dir: Path, project: Project) -> list[OrphanInfo]`
  — read-only, returns a structured list (filename, target capture
  point index, current frames count at that point).
- `apply_orphan_fixes(capture_dir: Path, project: Project,
  orphans: list[OrphanInfo]) -> list[str]` — mutates the project,
  writes the manifest atomically, creates the backup, returns the
  list of filenames patched.

For each orphan, the writer parses the sequence number from the
filename and resolves to a `CapturePoint` via `index = seq_number -
1` (matching how the capture writer numbers files). Files whose
sequence number doesn't match any existing capture point are
reported in the dialog as "kann nicht zugeordnet werden" and the
Repair action skips them.

### Filename pattern

Today the capture writer hard-codes `seq_{seq:04d}_{exp:03d}.fits`.
The reconciler uses:

```python
ORPHAN_RE = re.compile(r"^seq_(\d{4})_(\d{3})\.fits$")
```

A match yields `seq_idx = int(group(1))`, `exposure_idx =
int(group(2))`. `seq_idx` is 1-based; `CapturePoint.index = seq_idx
- 1`.

## 4. Backup discipline

The patcher writes the pre-fix manifest to
`manifest.json.bak-orphan-<UTC-ISO-8601>` before overwriting. Multiple
recoveries over time produce multiple backups (one per run); we never
overwrite an existing backup. The backup is created with the same
permissions as the manifest itself.

This is symmetrical with the standalone python one-liner we ran by
hand for the first incident — that left `manifest.json.bak-pre-recovery`
behind. The new code's backup naming is timestamped so consecutive
runs don't clobber each other.

## 5. UI placement

The "Manifest prüfen" button lives in the top bar of the render
view, next to the existing **Browse / Load / Render** trio, with a
`build_repair`-ish icon. Disabled when no project is loaded.

Dialog layout when orphans are found:

```
┌──────────────────────────────────────────────────────────────┐
│ Verwaiste FITS-Dateien gefunden                              │
│                                                              │
│ Folgende Dateien liegen auf der Platte, sind aber nicht im   │
│ Manifest registriert. Mit "Reparieren" werden sie dem        │
│ passenden Capture-Point hinzugefügt. Das aktuelle Manifest   │
│ wird als manifest.json.bak-orphan-<timestamp> gesichert.     │
│                                                              │
│  ✓ seq_0019_001.fits  →  Capture-Point #18 (war leer)        │
│  ✓ seq_0042_002.fits  →  Capture-Point #41 (hatte 1 Frame)   │
│  ⚠ seq_0099_001.fits  →  kein Capture-Point #98 — wird       │
│                          übersprungen                         │
│                                                              │
│                              [Abbrechen]  [Reparieren]       │
└──────────────────────────────────────────────────────────────┘
```

The list is scrollable for >10 orphans. The `⚠` markers are visual
warnings (orange icon, not clickable); they remain in the report
but Reparieren skips them.

After Repair, a single `ui.notify(..., type="positive")` confirms
N repaired files plus the count of skipped-because-no-slot ones.

## 6. Out of scope (v1)

- Detecting frames *missing* from disk (manifest references a file
  that's not there). That's a separate failure mode; current behaviour
  is to skip and log, which is acceptable.
- Multi-exposure-per-point reconciliation (we currently only render
  the first good exposure per point). The reconciler does add multi-
  exposure orphans correctly to `frames[]`; we just don't render
  beyond the first one yet.
- A scan-only / dry-run mode. The atomic-write + backup pattern is
  cheap; a separate mode adds UI surface for little gain.
- Per-night attribution heuristics. New orphans get
  `night = max(existing_night) or 1` without trying to infer the
  actual capture session.

## 7. Testing

Pytest fixtures populate a temp capture dir with:

- Manifest A: all points have their frames registered (no orphans).
  Reconciler returns 0, manifest is untouched, no backup is created.
- Manifest B: point 18 has `frames=[]` but `seq_0019_001.fits` is on
  disk. Reconciler returns 1, manifest is patched, backup file
  exists.
- Manifest C: orphan `seq_0099_001.fits` exists but no capture point
  has index 98. Reconciler returns 0 + a warning log entry, manifest
  untouched.
- Manifest D: two orphans across two different points. Both patched,
  one backup file.

Atomic-write contract: writing to `.tmp` + rename means a crash
mid-write leaves either the old manifest or the new one, never a
truncated file. Test by mocking `Path.replace` to raise after the
write but before the rename.

## 8. Implementation order

1. `src/renderer/orphan_recovery.py` (new) — `OrphanInfo` dataclass,
   `find_orphans(...)` pure read, `apply_orphan_fixes(...)`
   mutating writer, `_atomic_write_manifest(...)` helper.
2. Tests under `tests/test_orphan_recovery.py` — fixtures from §7.
3. UI: "Manifest prüfen" button in the top bar + modal dialog +
   Repair handler.
4. Smoke test on the user's actual `cygnus20260522-1` directory.

Each step ships in one commit; the new module + tests land
together; UI + handler land together. No changes to
`RenderPipeline.load()` — the recovery is strictly UI-triggered.
