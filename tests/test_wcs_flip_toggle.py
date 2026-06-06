"""UI-toggle wiring for the per-project WCS 180°-flip (#157).

The actual flip math + projection are covered by ``test_wcs.py`` and
``test_catalog_overlay_flip.py``. This module pins the *toggle handler*
contract: setting the project flag, persisting the manifest, and
invalidating the cached FOV slice so the next refresh recomputes with
the new orientation.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Project,
    SplinePath,
)
from src.renderer.ui import render_layout
from src.renderer.ui.render_layout import _toggle_wcs_flip


def _make_project(flip: bool = False) -> Project:
    return Project(
        project="toggle-test",
        path=SplinePath(control_points=[
            ControlPoint(ra=180.0, dec=45.0),
            ControlPoint(ra=180.01, dec=45.01),
        ]),
        capture_settings=CaptureSettings(exposure_seconds=1.0),
        capture_points=[
            CapturePoint(
                ra=180.0, dec=45.0, index=0,
                status="captured", files=["frame_0001_001.fits"],
            ),
        ],
        wcs_flip_180=flip,
    )


def _stub_state(tmp_path: Path, project: Project) -> SimpleNamespace:
    """Minimal state stub that satisfies _persist_project + cache access."""
    pipeline = SimpleNamespace(project=project, capture_dir=tmp_path)
    return SimpleNamespace(
        pipeline=pipeline,
        catalog_fov_cache={0: {"objects": [{"id": "stale"}]}},
    )


def test_toggle_on_persists_flag_and_clears_cache(tmp_path, monkeypatch):
    """Flip on → project flag flips, manifest is written, cache empties."""
    refreshed: list[object] = []
    monkeypatch.setattr(
        render_layout, "_refresh_catalog_fov_slice",
        lambda s: refreshed.append(s),
    )
    project = _make_project(flip=False)
    state = _stub_state(tmp_path, project)

    _toggle_wcs_flip(state, True)

    assert project.wcs_flip_180 is True
    assert state.catalog_fov_cache == {}
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["wcs_flip_180"] is True
    assert refreshed == [state], "expected exactly one overlay refresh call"


def test_toggle_off_persists_flag_and_clears_cache(tmp_path, monkeypatch):
    """Flip off → mirror behavior of the on-path, flag goes False."""
    monkeypatch.setattr(
        render_layout, "_refresh_catalog_fov_slice", lambda s: None,
    )
    project = _make_project(flip=True)
    state = _stub_state(tmp_path, project)

    _toggle_wcs_flip(state, False)

    assert project.wcs_flip_180 is False
    assert state.catalog_fov_cache == {}
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["wcs_flip_180"] is False


def test_toggle_no_pipeline_is_noop():
    """No pipeline → handler returns silently, no AttributeError."""
    state = SimpleNamespace(pipeline=None, catalog_fov_cache={0: {}})
    _toggle_wcs_flip(state, True)
    # Cache untouched, no exception.
    assert state.catalog_fov_cache == {0: {}}


def test_toggle_coerces_truthy_value(tmp_path, monkeypatch):
    """e.value may arrive as a non-bool — handler must coerce via bool()."""
    monkeypatch.setattr(
        render_layout, "_refresh_catalog_fov_slice", lambda s: None,
    )
    project = _make_project(flip=False)
    state = _stub_state(tmp_path, project)

    _toggle_wcs_flip(state, 1)  # type: ignore[arg-type]

    assert project.wcs_flip_180 is True
    assert isinstance(project.wcs_flip_180, bool)
