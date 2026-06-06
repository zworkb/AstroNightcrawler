# Orphan FITS Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Manifest prüfen" button in the render top bar runs a read-only scan for FITS files on disk that aren't registered in `manifest.json`. When orphans are found, a modal lists each orphan + its proposed capture-point and the user clicks **Reparieren** to apply patches atomically (with timestamped backup). Nothing happens silently — every manifest mutation is explicit.

**Architecture:** New leaf module `src/renderer/orphan_recovery.py` exposes `find_orphans()` (pure read) + `apply_orphan_fixes()` (mutating writer with atomic write + backup). `RenderPipeline` carries one thin pass-through. `_build_top_bar` in render UI grows a button + dialog wired to those two functions. No changes to `RenderPipeline.load()`.

**Tech Stack:** Pydantic v2 (`Project.model_dump_json`), stdlib `re`/`pathlib`/`tempfile`/`datetime`, pytest fixtures, existing NiceGUI `ui.dialog`/`ui.button`/`ui.notify`.

**Spec:** [docs/superpowers/specs/2026-06-06-orphan-fits-recovery-design.md](../specs/2026-06-06-orphan-fits-recovery-design.md).

---

## File Map

| Path | Role | Touch |
|---|---|---|
| `src/renderer/orphan_recovery.py` | `OrphanInfo` dataclass, `find_orphans`, `apply_orphan_fixes`, atomic-write helper | Create |
| `tests/test_orphan_recovery.py` | Pytest fixtures + ~6 unit tests | Create |
| `src/renderer/ui/render_layout.py` | Top-bar button + dialog + handler | Modify (small) |

---

### Task 1: Reconciler module + tests

**Files:**
- Create: `src/renderer/orphan_recovery.py`
- Create: `tests/test_orphan_recovery.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_orphan_recovery.py`:

```python
"""Tests for the orphan-FITS reconciler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.project import (
    CapturePoint,
    CapturedFrame,
    CaptureSettings,
    Project,
    SplinePath,
)
from src.renderer.orphan_recovery import (
    OrphanInfo,
    apply_orphan_fixes,
    find_orphans,
)


def _make_project(point_frame_filenames: list[list[str]]) -> Project:
    """Build a minimal Project with N capture points; inner list = filenames."""
    points = []
    for i, fnames in enumerate(point_frame_filenames):
        points.append(CapturePoint(
            index=i, ra=180.0 + i * 0.1, dec=45.0,
            frames=[
                CapturedFrame(filename=fn, status="good")
                for fn in fnames
            ],
        ))
    return Project(
        project="t",
        path=SplinePath(control_points=[]),
        capture_settings=CaptureSettings(),
        capture_points=points,
    )


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _write_manifest(capture_dir: Path, project: Project) -> None:
    (capture_dir / "manifest.json").write_text(project.model_dump_json())


def test_find_orphans_empty_when_disk_matches_manifest(tmp_path):
    """No orphans when every FITS on disk is registered."""
    proj = _make_project([["seq_0001_001.fits"], ["seq_0002_001.fits"]])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "seq_0002_001.fits")
    _write_manifest(tmp_path, proj)
    assert find_orphans(tmp_path, proj) == []


def test_find_orphans_reports_orphan_with_target_point(tmp_path):
    """Disk has a file the manifest doesn't reference → reported as orphan."""
    proj = _make_project([["seq_0001_001.fits"], [], ["seq_0003_001.fits"]])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "seq_0002_001.fits")  # orphan: belongs to point 1
    _touch(tmp_path / "seq_0003_001.fits")
    _write_manifest(tmp_path, proj)
    orphans = find_orphans(tmp_path, proj)
    assert len(orphans) == 1
    o = orphans[0]
    assert o.filename == "seq_0002_001.fits"
    assert o.target_point_index == 1
    assert o.existing_frames_at_target == 0


def test_find_orphans_reports_unassignable(tmp_path):
    """Orphan whose sequence number is beyond all capture points is reported with target=None."""
    proj = _make_project([["seq_0001_001.fits"]])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "seq_0099_001.fits")  # no capture point 98
    _write_manifest(tmp_path, proj)
    orphans = find_orphans(tmp_path, proj)
    assert len(orphans) == 1
    assert orphans[0].filename == "seq_0099_001.fits"
    assert orphans[0].target_point_index is None


def test_find_orphans_ignores_non_seq_files(tmp_path):
    """Files whose names don't match the seq pattern are ignored."""
    proj = _make_project([["seq_0001_001.fits"]])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "calibration.fits")
    _touch(tmp_path / "README.md")
    _write_manifest(tmp_path, proj)
    assert find_orphans(tmp_path, proj) == []


def test_apply_fixes_patches_manifest_and_backs_up(tmp_path):
    """Apply mutates project, writes manifest, creates timestamped backup."""
    proj = _make_project([["seq_0001_001.fits"], []])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "seq_0002_001.fits")
    _write_manifest(tmp_path, proj)
    orphans = find_orphans(tmp_path, proj)
    patched = apply_orphan_fixes(tmp_path, proj, orphans)
    assert patched == ["seq_0002_001.fits"]
    assert [f.filename for f in proj.capture_points[1].frames] == [
        "seq_0002_001.fits",
    ]
    backups = list(tmp_path.glob("manifest.json.bak-orphan-*"))
    assert len(backups) == 1
    # On-disk manifest reflects the patch.
    loaded = json.loads((tmp_path / "manifest.json").read_text())
    fnames_after = [
        f["filename"]
        for pt in loaded["capture_points"] if pt["index"] == 1
        for f in pt["frames"]
    ]
    assert fnames_after == ["seq_0002_001.fits"]


def test_apply_fixes_skips_unassignable(tmp_path):
    """Unassignable orphans are not appended; assignable ones still are."""
    proj = _make_project([[], []])
    _touch(tmp_path / "seq_0001_001.fits")
    _touch(tmp_path / "seq_0002_001.fits")
    _touch(tmp_path / "seq_0099_001.fits")
    _write_manifest(tmp_path, proj)
    orphans = find_orphans(tmp_path, proj)
    patched = apply_orphan_fixes(tmp_path, proj, orphans)
    assert sorted(patched) == ["seq_0001_001.fits", "seq_0002_001.fits"]
    # No phantom point added for seq_0099.
    assert len(proj.capture_points) == 2
```

- [ ] **Step 1.2: Run tests — fail with ImportError**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_orphan_recovery.py -v
```

Expected: `ImportError: cannot import name 'OrphanInfo' / 'find_orphans' / 'apply_orphan_fixes' from 'src.renderer.orphan_recovery'`.

- [ ] **Step 1.3: Write the reconciler**

Create `src/renderer/orphan_recovery.py`:

```python
"""Reconcile FITS files on disk against the manifest.

When a capture run drops a FITS file on disk but the manifest update
crashes / is interrupted, the file becomes an "orphan" — present on
disk, missing from ``CapturePoint.frames``. The renderer's importer
skips capture points with no good frames, so the orphan silently
leaves a gap in the filmstrip and the final render. This module
finds those orphans and lets the user apply fixes explicitly via
a UI button.

Pattern: ``seq_NNNN_MMM.fits`` (1-based sequence number, exposure
index). The 1-based sequence number maps to
``CapturePoint.index = seq_number - 1``.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.models.project import CapturedFrame, Project

logger = logging.getLogger(__name__)

ORPHAN_RE = re.compile(r"^seq_(\d{4})_(\d{3})\.fits$")


@dataclass(frozen=True)
class OrphanInfo:
    """Describes one orphan FITS file the scanner found.

    ``target_point_index`` is ``None`` when the file's sequence
    number doesn't correspond to any existing capture point — the
    UI surfaces those entries as "kann nicht zugeordnet werden" and
    ``apply_orphan_fixes`` skips them.
    """

    filename: str
    target_point_index: int | None
    existing_frames_at_target: int


def find_orphans(capture_dir: Path, project: Project) -> list[OrphanInfo]:
    """Scan ``capture_dir`` for FITS files not referenced by ``project``.

    Returns a sorted list (by filename) of :class:`OrphanInfo` records.
    Pure read — does not mutate ``project`` or touch the manifest.
    """
    known: set[str] = {
        cf.filename
        for point in project.capture_points
        for cf in point.frames
    }
    by_idx: dict[int, int] = {
        p.index: len(p.frames) for p in project.capture_points
    }

    orphans: list[OrphanInfo] = []
    for fits_path in sorted(capture_dir.glob("seq_*.fits")):
        m = ORPHAN_RE.match(fits_path.name)
        if m is None or fits_path.name in known:
            continue
        seq_idx = int(m.group(1))
        target = seq_idx - 1
        if target in by_idx:
            orphans.append(OrphanInfo(
                filename=fits_path.name,
                target_point_index=target,
                existing_frames_at_target=by_idx[target],
            ))
        else:
            orphans.append(OrphanInfo(
                filename=fits_path.name,
                target_point_index=None,
                existing_frames_at_target=0,
            ))
    return orphans


def apply_orphan_fixes(
    capture_dir: Path, project: Project, orphans: list[OrphanInfo],
) -> list[str]:
    """Append assignable orphans to ``project`` and persist atomically.

    Skips orphans whose ``target_point_index`` is ``None``. Mutates
    ``project`` in place. When at least one orphan was patched the
    updated manifest is written back atomically (tmp + rename) and a
    timestamped backup of the pre-patch manifest is left in
    ``capture_dir/manifest.json.bak-orphan-<UTC>``.

    Returns the list of filenames that were actually patched in.
    """
    assignable = [o for o in orphans if o.target_point_index is not None]
    if not assignable:
        return []

    by_idx = {p.index: p for p in project.capture_points}
    patched: list[str] = []
    for orphan in assignable:
        point = by_idx.get(orphan.target_point_index)
        if point is None:
            continue
        point.frames.append(CapturedFrame(
            filename=orphan.filename,
            status="good",
            night=1,
            captured_at=None,
        ))
        patched.append(orphan.filename)

    if not patched:
        return []

    manifest_path = capture_dir / "manifest.json"
    if manifest_path.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = capture_dir / f"manifest.json.bak-orphan-{stamp}"
        backup_path.write_bytes(manifest_path.read_bytes())

    _atomic_write_manifest(manifest_path, project)
    logger.info(
        "Recovered %d orphan FITS file(s) into manifest: %s",
        len(patched), patched,
    )
    return patched


def _atomic_write_manifest(path: Path, project: Project) -> None:
    """Write ``project`` JSON via tmp + rename so a crash leaves either
    the old file or the new one — never a truncated one.
    """
    payload = project.model_dump_json(indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix="manifest-", suffix=".json", dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
```

- [ ] **Step 1.4: Run tests — should pass**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_orphan_recovery.py -v
```

Expected: 6/6 PASS.

- [ ] **Step 1.5: Full suite — no regressions**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Expected: 276 passed (270 existing + 6 new).

- [ ] **Step 1.6: Commit**

```bash
git add src/renderer/orphan_recovery.py tests/test_orphan_recovery.py
git commit -m "feat(renderer): orphan-FITS reconciler with atomic write + timestamped backup"
```

---

### Task 2: "Manifest prüfen" button + Repair dialog

**Files:**
- Modify: `src/renderer/ui/render_layout.py` — `_build_top_bar` adds the button; new top-level `_check_orphans` handler + helpers.

- [ ] **Step 2.1: Find the top-bar build site**

Inside `src/renderer/ui/render_layout.py`, locate `_build_top_bar` (search for `def _build_top_bar`). The body builds a `ui.row` with Browse / Load / Render buttons.

- [ ] **Step 2.2: Add the button**

Insert immediately after the `ui.button("Render", ...)` line:

```python
        ui.button(
            "Manifest prüfen", icon="manage_search",
            on_click=lambda: _check_orphans(state),
        ).bind_enabled_from(
            state, "pipeline",
            backward=lambda p: p is not None,
        )
```

`bind_enabled_from` only fires `backward` when the bound attr changes, which happens at load. That keeps the button disabled until a project is loaded. If `bind_enabled_from` doesn't accept a `backward` mapper in the installed NiceGUI version, fall back to a plain `ui.button(..., on_click=...)` and rely on the handler's own `if not state.pipeline: ui.notify("...")` guard.

- [ ] **Step 2.3: Add the handler + dialog**

Add this top-level function near `_load` (the other top-bar action handler) in the same file:

```python
def _check_orphans(state: _RenderState) -> None:
    """Run the orphan-FITS scan and surface a Repair dialog when needed.

    User-triggered (button click). Read-only scan first; mutating
    Repair only happens after the user clicks the corresponding
    dialog button.
    """
    from src.renderer.orphan_recovery import (
        apply_orphan_fixes, find_orphans,
    )

    if not state.pipeline or not state.pipeline.project:
        ui.notify(
            "Bitte zuerst ein Projekt laden.",
            type="warning", timeout=2500,
        )
        return
    orphans = find_orphans(
        state.pipeline.capture_dir, state.pipeline.project,
    )
    if not orphans:
        ui.notify(
            "Manifest ist konsistent — keine verwaisten Dateien "
            "gefunden",
            type="positive", timeout=3000,
        )
        return

    assignable = [o for o in orphans if o.target_point_index is not None]
    unassignable = [o for o in orphans if o.target_point_index is None]

    with ui.dialog() as dialog, ui.card().classes("w-[640px]"):
        ui.label("Verwaiste FITS-Dateien gefunden").classes(
            "text-lg font-bold",
        )
        ui.label(
            f"{len(orphans)} Datei{'' if len(orphans) == 1 else 'en'} "
            "liegen auf der Platte, sind aber nicht im Manifest "
            "registriert. Mit „Reparieren" werden die zuordenbaren "
            "dem passenden Capture-Point hinzugefügt. Das aktuelle "
            "Manifest wird vorher als "
            "manifest.json.bak-orphan-<timestamp> gesichert.",
        ).classes("text-sm")
        with ui.column().classes(
            "w-full max-h-72 overflow-y-auto gap-1 my-2",
        ):
            for o in assignable:
                existing = (
                    f"({o.existing_frames_at_target} Frames)"
                    if o.existing_frames_at_target
                    else "(war leer)"
                )
                ui.label(
                    f"✓  {o.filename}  →  Capture-Point "
                    f"#{o.target_point_index}  {existing}"
                ).classes("text-sm font-mono")
            for o in unassignable:
                ui.label(
                    f"⚠  {o.filename}  →  kein passender "
                    f"Capture-Point — wird übersprungen"
                ).classes("text-sm font-mono text-orange-400")

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button(
                "Abbrechen", on_click=dialog.close,
            ).props("flat")

            def _repair() -> None:
                patched = apply_orphan_fixes(
                    state.pipeline.capture_dir,
                    state.pipeline.project,
                    orphans,
                )
                skipped = len(unassignable)
                ui.notify(
                    f"{len(patched)} Datei{'' if len(patched) == 1 else 'en'} "
                    "ins Manifest aufgenommen"
                    + (
                        f", {skipped} übersprungen"
                        if skipped else ""
                    ),
                    type="positive", timeout=4000,
                )
                dialog.close()
                # Re-run the filmstrip + preview so the newly-known
                # frame appears immediately.
                import asyncio
                asyncio.create_task(_load(state))

            ui.button(
                "Reparieren",
                color="primary",
                on_click=_repair,
            )
    dialog.open()
```

- [ ] **Step 2.4: Run the suite — no regressions**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Expected: 276 passed.

- [ ] **Step 2.5: Import smoke**

```bash
PYTHON_GIL=0 .venv/bin/python -c "from src.renderer.ui import render_layout; print('OK')"
```

Expected: `OK`.

- [ ] **Step 2.6: Commit**

```bash
git add src/renderer/ui/render_layout.py
git commit -m "feat(ui): Manifest prüfen button + Repair dialog for orphan FITS"
```

---

### Task 3: Smoke + push + close

- [ ] **Step 3.1: Smoke on user's data**

`make run-render`. Load `~/astro/astroberry/nightcrawler/cygnus20260522-1`. Click "Manifest prüfen":

1. After the user's earlier hand-patch the manifest should be clean → notification "Manifest ist konsistent".
2. To verify the Repair path: hand-edit the manifest to set `capture_points[18].frames = []` again (or delete the entry the user manually added), reload, click "Manifest prüfen" → dialog appears with `seq_0019_001.fits → Capture-Point #18`. Click Reparieren → notify success, filmstrip rebuilds, the previously-missing slot is back.

- [ ] **Step 3.2: Push + close**

```bash
git push origin master
gh issue close <NUMBER> --comment "Shipped orphan-FITS recovery — explicit 'Manifest prüfen' button + Repair dialog, atomic write + timestamped backup. Spec: docs/superpowers/specs/2026-06-06-orphan-fits-recovery-design.md"
```

---

## Self-Review

**Spec coverage:**

| Spec § | Task |
|---|---|
| 3 Explicit Check + Repair (no implicit work on load) | Task 2 |
| 3 `find_orphans` / `apply_orphan_fixes` split | Task 1 |
| 4 Timestamped backup, atomic write | Task 1 |
| 5 UI placement (top bar) + dialog layout | Task 2 |
| 7 Testing (4+ fixtures, find / apply / unassignable / non-seq) | Task 1 (6 tests) |

**Placeholder scan:** every code block is complete production code. The `bind_enabled_from` fallback note in Task 2.2 names a concrete alternative; the implementer picks one based on the installed NiceGUI version.

**Type consistency:** `OrphanInfo.target_point_index: int | None` matches the `find_orphans` return type and the `apply_orphan_fixes` filtering check. The reconciler returns `list[str]` of filenames; the dialog's `_repair` reads its length only.
