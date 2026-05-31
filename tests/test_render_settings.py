"""Tests for per-project ``RenderSettings`` and soft-migration (#151).

These cover the backward-compatibility matrix demanded by issue #151:

1. v1 fixture loads → ``render_settings`` is default.
2. Real ``output/deneb_21/manifest.json`` loads → no crash.
3. Round-trip with a non-trivial ``auto_stretch_params`` payload is
   bit-stable through JSON.
4. Forward-compat: unknown ``render_settings.future_xyz`` is dropped
   silently by ``extra="ignore"``.
5. Soft-migration: legacy app-storage values get lifted onto the
   project and removed from the (mutable) source dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models.project import (
    Project,
    RenderSettings,
)
from src.renderer.stretch import AutoStretchParams

FIXTURE_V1 = Path(__file__).parent / "fixtures" / "legacy_manifest_v1.json"
DENEB_21_MANIFEST = (
    Path(__file__).parent.parent / "output" / "deneb_21" / "manifest.json"
)


class TestRenderSettingsDefaults:
    """Defaults must match the documented baseline (issue #151 spec table)."""

    def test_defaults(self) -> None:
        rs = RenderSettings()
        assert rs.stretch_mode == "auto"
        assert rs.black == 0.0
        assert rs.white == 1.0
        assert rs.midtone == 1.0
        assert rs.auto_stretch_freeze is True
        assert rs.auto_stretch_params is None
        assert rs.linear_pan_blend_tail == 0
        assert rs.fps == 24
        assert rs.crf == 18
        assert rs.transition == "crossfade"
        assert rs.crossfade_frames == 24
        assert rs.resolution == "native"
        assert rs.speed == 1.0
        assert rs.align_max_dim == 0
        assert rs.align_sigma == 2.0


class TestBackwardCompatLegacyV1:
    """Test 1 — legacy v1 manifest loads with default render_settings."""

    def test_legacy_v1_manifest_loads(self) -> None:
        project = Project.model_validate_json(FIXTURE_V1.read_text())
        assert project.render_settings == RenderSettings()


class TestBackwardCompatRealDeneb21:
    """Test 2 — real deneb_21 manifest loads with default render_settings.

    The file is gitignored output, so the test is skipped on CI / fresh
    clones. Locally it provides confidence against the actual on-disk
    artefact that survives across the development cycle.
    """

    @pytest.mark.skipif(
        not DENEB_21_MANIFEST.exists(),
        reason="deneb_21 capture not present locally",
    )
    def test_real_deneb_21_manifest_loads(self) -> None:
        project = Project.model_validate_json(DENEB_21_MANIFEST.read_text())
        # Either default (capture-only, never edited in renderer) or
        # already-customised after a renderer pass — both are valid.
        assert isinstance(project.render_settings, RenderSettings)


class TestRoundTripNonTrivialAutoStretchParams:
    """Test 3 — non-trivial render_settings survives JSON round-trip."""

    def test_roundtrip_preserves_auto_stretch_params(self) -> None:
        rs = RenderSettings(
            stretch_mode="auto+manual",
            black=0.02,
            white=0.97,
            midtone=1.35,
            auto_stretch_freeze=True,
            auto_stretch_params=AutoStretchParams(
                vmin=[10.5, 12.0, 11.2],
                vmax=[210.0, 215.5, 208.0],
            ),
            linear_pan_blend_tail=8,
            fps=30,
            crf=20,
            transition="linear-pan",
            crossfade_frames=18,
            resolution="1080p",
            speed=0.75,
            align_max_dim=2048,
            align_sigma=3.5,
        )
        project = Project(
            project="rt-test",
            path={"control_points": []},  # type: ignore[arg-type]
            render_settings=rs,
        )
        as_json = project.model_dump_json()
        reloaded = Project.model_validate_json(as_json)
        assert reloaded.render_settings == rs


class TestForwardCompatExtraIgnore:
    """Test 4 — unknown render_settings.* keys are dropped silently."""

    def test_unknown_field_is_dropped(self) -> None:
        project = Project(
            project="fc-test",
            path={"control_points": []},  # type: ignore[arg-type]
        )
        data = json.loads(project.model_dump_json())
        # Inject a future field that this version doesn't know about.
        data["render_settings"]["future_param_xyz"] = 42
        reloaded = Project.model_validate_json(json.dumps(data))
        # Loaded cleanly with defaults; future field is gone.
        assert reloaded.render_settings == RenderSettings()
        assert not hasattr(reloaded.render_settings, "future_param_xyz")


class TestSoftMigration:
    """Test 5 — legacy app-storage values lift onto project + get removed."""

    def test_soft_migrate_default_project_lifts_legacy_values(self) -> None:
        from src.renderer.ui.render_layout import (
            _maybe_soft_migrate_render_settings,
        )

        project = Project(
            project="sm-test",
            path={"control_points": []},  # type: ignore[arg-type]
        )
        # Pretend old app.storage["render"] from a pre-#151 session.
        app_store = {
            "input_dir": "./output/",  # app-level — must NOT migrate
            "output_path": "out.mp4",  # app-level — must NOT migrate
            "stretch_mode": "manual",
            "black": 0.05,
            "white": 0.9,
            "midtone": 1.2,
            "fps": 30,
            "crf": 20,
            "linear_pan_blend_tail": 6,
            "auto_stretch_freeze": False,
        }
        migrated = _maybe_soft_migrate_render_settings(project, app_store)
        assert migrated is True
        # Project picked up the legacy values.
        assert project.render_settings.stretch_mode == "manual"
        assert project.render_settings.black == 0.05
        assert project.render_settings.white == 0.9
        assert project.render_settings.midtone == 1.2
        assert project.render_settings.fps == 30
        assert project.render_settings.crf == 20
        assert project.render_settings.linear_pan_blend_tail == 6
        assert project.render_settings.auto_stretch_freeze is False
        # Legacy project-level keys removed from the source dict.
        for k in (
            "stretch_mode", "black", "white", "midtone", "fps",
            "crf", "linear_pan_blend_tail", "auto_stretch_freeze",
        ):
            assert k not in app_store
        # App-level keys preserved.
        assert app_store["input_dir"] == "./output/"
        assert app_store["output_path"] == "out.mp4"

    def test_soft_migrate_skips_already_customised_project(self) -> None:
        from src.renderer.ui.render_layout import (
            _maybe_soft_migrate_render_settings,
        )

        # Already-customised project (non-default render_settings).
        project = Project(
            project="custom",
            path={"control_points": []},  # type: ignore[arg-type]
            render_settings=RenderSettings(stretch_mode="manual", black=0.1),
        )
        app_store = {"stretch_mode": "auto", "fps": 60}
        migrated = _maybe_soft_migrate_render_settings(project, app_store)
        assert migrated is False
        # Project still carries its own settings.
        assert project.render_settings.stretch_mode == "manual"
        assert project.render_settings.black == 0.1
        # Legacy values UNTOUCHED in app_store (no destructive prune
        # for already-customised projects).
        assert app_store == {"stretch_mode": "auto", "fps": 60}

    def test_soft_migrate_with_no_legacy_keys_is_noop(self) -> None:
        from src.renderer.ui.render_layout import (
            _maybe_soft_migrate_render_settings,
        )

        project = Project(
            project="noop",
            path={"control_points": []},  # type: ignore[arg-type]
        )
        app_store = {"input_dir": "./out/", "render_workers": 4}
        migrated = _maybe_soft_migrate_render_settings(project, app_store)
        assert migrated is False
        assert app_store == {"input_dir": "./out/", "render_workers": 4}
