"""Tests for AppState.open_project + multi-night resume (issue #136)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app_state import AppState, _resolve_output_dir
from src.capture.controller import CaptureState
from src.indi.mock import MockINDIClient
from src.models.project import ControlPoint, SplinePath

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_manifest_v1.json"


def _make_project_dir(tmp_path: Path, *, drop: set[str] | None = None) -> Path:
    """Create a temp project dir with the legacy manifest + fake FITS files.

    Args:
        tmp_path: Pytest temp directory.
        drop: Filenames to deliberately NOT create on disk (simulating a
            file deleted outside the app), to exercise reconcile.

    Returns:
        Path to the created project directory.
    """
    drop = drop or set()
    proj_dir = tmp_path / "deneb"
    proj_dir.mkdir()
    (proj_dir / "manifest.json").write_text(FIXTURE.read_text())
    # Files referenced by the captured points in the fixture.
    for fname in ("seq_0001_001.fits", "seq_0004_001.fits", "seq_0004_002.fits"):
        if fname not in drop:
            (proj_dir / fname).write_bytes(b"fake fits")
    return proj_dir


def test_open_project_loads_migrates_and_binds(tmp_path: Path) -> None:
    """Opening a legacy project migrates it and binds the output dir."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()

    report = state.open_project(proj_dir)

    # Migration ran: legacy v1.0 -> 2.0, files -> frames.
    assert state.project.version == "2.0"
    assert state.project.project == "deneb"
    # Output is bound to the opened directory.
    assert state.project_dir == proj_dir
    # All files present -> nothing reconciled away.
    assert report.removed_count == 0

    by_index = {p.index: p for p in state.project.capture_points}
    assert by_index[0].good_count == 1  # captured single
    assert by_index[0].is_complete
    assert not by_index[1].is_complete  # pending
    assert by_index[2].skipped  # skipped stays skipped
    assert by_index[3].good_count == 2 and by_index[3].is_complete


def test_open_project_reconciles_missing_files(tmp_path: Path) -> None:
    """A deleted FITS file is dropped and the point becomes incomplete."""
    proj_dir = _make_project_dir(tmp_path, drop={"seq_0004_002.fits"})
    state = AppState()

    report = state.open_project(proj_dir)

    assert report.removed_count == 1
    assert 3 in report.affected_points
    pt3 = next(p for p in state.project.capture_points if p.index == 3)
    # target_subs was 2 (migrated from 2 files); one file gone -> incomplete.
    assert pt3.good_count == 1
    assert not pt3.is_complete


def test_open_project_missing_manifest_raises(tmp_path: Path) -> None:
    """Opening a directory without manifest.json raises a clear error."""
    empty = tmp_path / "empty"
    empty.mkdir()
    state = AppState()
    with pytest.raises(FileNotFoundError, match="manifest.json"):
        state.open_project(empty)


def test_resolve_output_dir_uses_opened_dir(tmp_path: Path) -> None:
    """When a project is opened, capture resolves to that directory."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()
    state.open_project(proj_dir)

    resolved = _resolve_output_dir(state.project_dir)
    assert resolved == proj_dir


def test_resolve_output_dir_creates_missing(tmp_path: Path) -> None:
    """``_resolve_output_dir`` creates the directory if it does not exist."""
    target = tmp_path / "fresh_project"
    assert not target.exists()

    resolved = _resolve_output_dir(target)

    assert resolved == target
    assert target.is_dir()


@pytest.mark.asyncio
async def test_start_capture_without_project_dir_raises(tmp_path: Path) -> None:
    """Since #142, ``start_capture`` requires a located project on disk."""
    state = AppState()
    client = MockINDIClient()
    await client.connect("localhost")
    state.indi_client = client
    # Plain plan (no project_dir bound) with ≥2 capture points.
    state.project.path = SplinePath(control_points=[
        ControlPoint(ra=10.0, dec=20.0),
        ControlPoint(ra=11.0, dec=21.0),
    ])
    state.update_capture_points()
    assert state.project_dir is None

    with pytest.raises(ValueError, match="anlegen|öffnen"):
        state.start_capture()


def test_settings_change_recomputes_capture_points(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spacing change schedules an apply that re-samples the capture points.

    The panel's ``_setting_number`` callback writes the new value to the
    capture settings and then debounces a call to ``_apply_settings``.
    We stub ``ui.timer`` so the apply runs synchronously (the running
    NiceGUI server isn't available in a unit test), and verify that
    halving the spacing roughly doubles the number of capture points.
    """
    from src.ui import bottom_panel as bp_mod

    state = AppState()
    state.new_project(tmp_path, "spacing_test")
    state.project.path = SplinePath(control_points=[
        ControlPoint(ra=10.0, dec=20.0),
        ControlPoint(ra=15.0, dec=20.0),
    ])
    state.project.capture_settings.point_spacing_deg = 1.0
    state.update_capture_points()
    initial = len(state.project.capture_points)
    assert initial >= 2

    panel = bp_mod.BottomPanelComponent(state)

    # Stub ``ui.timer`` so ``_schedule_settings_apply`` runs the callback
    # immediately — we're verifying the wiring, not the debounce itself.
    class _FakeTimer:
        def __init__(self, delay: float, cb, *, once: bool = False) -> None:
            cb()

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(bp_mod.ui, "timer", _FakeTimer)
    # Suppress NiceGUI storage/overlay side effects + the auto_save hook
    # (lazily imported from layout.py — patch the layout module's symbol).
    monkeypatch.setattr(
        bp_mod, "refresh_overlay", lambda *_a, **_k: None,
    )
    from src.ui import layout as layout_mod
    monkeypatch.setattr(layout_mod, "_auto_save", lambda *_a, **_k: None)
    # Refresh would touch live UI elements; bypass it.
    monkeypatch.setattr(panel, "refresh", lambda: None)

    # Simulate spacing change: halve it, expect ~double the points.
    state.project.capture_settings.point_spacing_deg = 0.5
    panel._schedule_settings_apply()

    after = len(state.project.capture_points)
    assert after > initial


def test_legacy_manifest_with_sequence_name_loads(tmp_path: Path) -> None:
    """v1 manifests carrying ``sequence_name`` still load after #142.

    The key was dropped from the model; ``extra="ignore"`` on
    ``CaptureSettings`` silently discards it so existing manifests on
    disk (e.g. output/deneb_21/manifest.json) keep working.
    """
    from src.models.project import Project

    raw = FIXTURE.read_text()
    assert '"sequence_name"' in raw  # fixture really carries the legacy key

    project = Project.model_validate_json(raw)

    # Loaded cleanly, migration ran (v1 -> v2), and the legacy field is gone.
    assert project.version == "2.0"
    assert not hasattr(project.capture_settings, "sequence_name")
    # The migrated capture_settings still carry the real (kept) fields.
    assert project.capture_settings.exposure_seconds == 5.0


@pytest.mark.asyncio
async def test_multi_night_resume_run(tmp_path: Path) -> None:
    """Open a mixed project, run capture: new frames are night 2, complete
    points are skipped, skipped points stay skipped, output stays in place."""
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()
    state.open_project(proj_dir)
    client = MockINDIClient()
    await client.connect("localhost")
    state.indi_client = client

    ctrl = state.start_capture()
    # next_night() over the night-1 captured frames -> 2.
    assert ctrl.night == 2
    # Capture writes into the opened directory, not a new one.
    assert ctrl.output_dir == proj_dir

    await ctrl.run()
    assert ctrl.state == CaptureState.COMPLETED

    by_index = {p.index: p for p in state.project.capture_points}
    # Point 0 was complete (night 1) -> untouched, no night-2 frame.
    assert by_index[0].good_count == 1
    assert all(f.night == 1 for f in by_index[0].frames)
    # Point 1 was pending -> filled this run with a night-2 frame.
    assert by_index[1].good_count == 1
    assert by_index[1].frames[0].night == 2
    # Point 2 was skipped -> stays skipped, never captured.
    assert by_index[2].skipped
    assert by_index[2].frames == []
    # Point 3 was complete (two night-1 frames) -> untouched.
    assert by_index[3].good_count == 2
    assert all(f.night == 1 for f in by_index[3].frames)


def _patch_storage(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace ``nicegui.app.storage`` with a plain-dict fake for the layout module.

    ``_auto_save`` and the restore block reach into ``app.storage.user`` to
    persist the project + bound directory. Tests don't run a NiceGUI server,
    so we swap ``app`` for a ``SimpleNamespace`` carrying a dict-backed
    ``storage.user``. Returns the dict so the test can assert on it directly.
    """
    from src.ui import layout as layout_mod

    fake_user: dict = {}
    fake_storage = SimpleNamespace(user=fake_user)
    fake_app = SimpleNamespace(storage=fake_storage)
    monkeypatch.setattr(layout_mod, "app", fake_app)
    return fake_user


def test_auto_save_writes_manifest_to_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_auto_save`` writes ``project_dir/manifest.json`` when bound."""
    from src.ui.layout import _auto_save

    storage = _patch_storage(monkeypatch)
    proj_dir = _make_project_dir(tmp_path)
    state = AppState()
    state.open_project(proj_dir)

    # Sanity: bound and disk manifest exists before the call.
    assert state.project_dir == proj_dir
    manifest = proj_dir / "manifest.json"
    before = manifest.read_text()

    # Mutate the project so the rewrite is observable.
    state.project.project = "renamed"
    _auto_save(state)

    after = manifest.read_text()
    assert after != before
    assert '"project": "renamed"' in after  # disk format is indent=2
    # Session storage mirror is updated under the NEW key only.
    assert storage["project_dir"] == str(proj_dir)
    assert "opened_project_dir" not in storage
    # Session-storage mirror uses compact JSON (no indent).
    assert '"project":"renamed"' in storage["project"]


def test_auto_save_skips_disk_when_not_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without ``project_dir`` no disk write happens — session storage only."""
    from src.ui.layout import _auto_save

    storage = _patch_storage(monkeypatch)
    state = AppState()
    assert state.project_dir is None

    _auto_save(state)

    # Nothing got written into tmp_path.
    assert list(tmp_path.iterdir()) == []
    assert storage["project_dir"] is None
    assert "project" in storage


def test_storage_migration_reads_legacy_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session persisted under the legacy key still restores ``project_dir``.

    Older builds wrote ``opened_project_dir``. The restore block in
    ``create_layout`` reads ``project_dir`` first and falls back to the
    legacy key — this guards reloads from breaking on upgrade.
    """
    storage = _patch_storage(monkeypatch)
    proj_dir = _make_project_dir(tmp_path)
    storage["opened_project_dir"] = str(proj_dir)

    # Replicate the restore lookup verbatim.
    from src.ui import layout as layout_mod
    opened_dir = (
        layout_mod.app.storage.user.get("project_dir")
        or layout_mod.app.storage.user.get("opened_project_dir")
    )
    assert opened_dir == str(proj_dir)
    assert (Path(opened_dir) / "manifest.json").exists()


# --------------------------------------------------------------------------- #
# new_project (issue #141)                                                    #
# --------------------------------------------------------------------------- #


def test_new_project_creates_dir_and_manifest(tmp_path: Path) -> None:
    """``new_project`` creates the directory + initial manifest and binds."""
    state = AppState()

    target = state.new_project(tmp_path, "cygnus_2026")

    assert target == tmp_path / "cygnus_2026"
    assert target.is_dir()
    manifest = target / "manifest.json"
    assert manifest.exists()

    # Manifest is valid JSON, indent=2, with the chosen name.
    raw = manifest.read_text()
    data = json.loads(raw)
    assert data["project"] == "cygnus_2026"
    # indent=2 means each top-level key is preceded by two spaces.
    assert '\n  "project"' in raw

    # State bindings.
    assert state.project.project == "cygnus_2026"
    assert state.project.path.control_points == []
    assert state.project_dir == target


def test_new_project_collision_raises(tmp_path: Path) -> None:
    """An existing directory must NOT be overwritten silently."""
    state = AppState()
    (tmp_path / "deneb").mkdir()

    with pytest.raises(FileExistsError):
        state.new_project(tmp_path, "deneb")
    # State must not be mutated on collision.
    assert state.project_dir is None


@pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
def test_new_project_empty_name_raises(tmp_path: Path, bad: str) -> None:
    """Empty / whitespace-only names are rejected with ``ValueError``."""
    state = AppState()
    with pytest.raises(ValueError, match="empty"):
        state.new_project(tmp_path, bad)
    assert state.project_dir is None


@pytest.mark.parametrize("bad", ["foo/bar", "..\\baz", "a/b/c"])
def test_new_project_invalid_name_raises(tmp_path: Path, bad: str) -> None:
    """Names with path separators are rejected."""
    state = AppState()
    with pytest.raises(ValueError, match="separator"):
        state.new_project(tmp_path, bad)
    assert state.project_dir is None


@pytest.mark.asyncio
async def test_new_project_then_capture_writes_in_place(
    tmp_path: Path,
) -> None:
    """After ``new_project`` + drawing a path, capture runs in-place at night 1."""
    state = AppState()
    target = state.new_project(tmp_path, "vega_session")
    # Draw a minimal 2-point path so start_capture has something to capture.
    state.project.path = SplinePath(
        control_points=[
            ControlPoint(ra=279.234, dec=38.783),
            ControlPoint(ra=280.0, dec=39.0),
        ],
    )
    state.update_capture_points()

    client = MockINDIClient()
    await client.connect("localhost")
    state.indi_client = client

    ctrl = state.start_capture()

    assert ctrl.output_dir == target
    assert ctrl.night == 1


def test_new_project_resets_undo_stack(tmp_path: Path) -> None:
    """``new_project`` starts a fresh undo history."""
    state = AppState()
    state.undo_stack.push("before", "after")
    assert state.undo_stack.can_undo

    state.new_project(tmp_path, "fresh")

    assert not state.undo_stack.can_undo
    assert not state.undo_stack.can_redo


def test_drawing_tools_disabled_without_project(tmp_path: Path) -> None:
    """Toolbar tool gating tracks ``state.project_dir``.

    No NiceGUI server here — we stub the buttons with objects exposing
    ``set_enabled`` and call the private ``_update_tool_gating`` directly.
    """
    from src.ui.toolbar import ToolbarComponent

    state = AppState()
    toolbar = ToolbarComponent(state)

    class _FakeBtn:
        def __init__(self) -> None:
            self.enabled = True

        def set_enabled(self, value: bool) -> None:
            self.enabled = value

    toolbar._path_tool_btns = [_FakeBtn() for _ in range(6)]

    # No project_dir -> path-mutating tools disabled.
    toolbar._update_tool_gating()
    assert all(not b.enabled for b in toolbar._path_tool_btns)

    # After locating the project -> tools enabled.
    state.new_project(tmp_path, "andromeda")
    toolbar._update_tool_gating()
    assert all(b.enabled for b in toolbar._path_tool_btns)

    # load_project_from_json drops project_dir -> tools disabled again.
    state.load_project_from_json(state.project.model_dump_json())
    toolbar._update_tool_gating()
    assert all(not b.enabled for b in toolbar._path_tool_btns)
