# Music Track + Render Toggles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-project music track (path stored in manifest.json) muxed into the rendered mp4 via ffmpeg post-process. Two global checkboxes in the render-settings panel: "Musik einbinden" gates the audio mux; "Labels einbinden" exposes the existing `RenderConfig.render_labels` flag to the UI.

**Architecture:** Three new fields on `RenderSettings` (`music_track`, `include_music`, `include_labels`). New `mux_audio()` helper in `src/renderer/video.py` next to `encode_video()`. `RenderPipeline.render()` calls it after the silent video is encoded, when both flags + a present file allow. UI: file-picker row + two checkboxes in the existing render-settings panel.

**Tech Stack:** Pydantic v2 (existing `RenderSettings` model), stdlib `subprocess`/`pathlib`, NiceGUI 2.x (`ui.input`, `ui.button`, `ui.checkbox`, existing `FolderBrowserDialog`).

**Spec:** [docs/superpowers/specs/2026-06-06-music-track-and-render-toggles-design.md](../specs/2026-06-06-music-track-and-render-toggles-design.md).

---

## File Map

| Path | Role | Touch |
|---|---|---|
| `src/models/project.py` | `RenderSettings` model — three new fields | Modify |
| `src/renderer/video.py` | New `mux_audio()` helper | Modify |
| `src/renderer/pipeline.py` | `RenderPipeline.render()` post-process | Modify |
| `src/renderer/ui/render_layout.py` | Music-track row + two checkboxes + handler | Modify |
| `tests/test_render_settings.py` | Round-trip new fields | Modify |
| `tests/test_music_mux.py` | Unit tests for `mux_audio` + pipeline orchestration | Create |

---

### Task 1: Model fields + round-trip tests

**Files:**
- Modify: `src/models/project.py` (`RenderSettings` class)
- Modify: `tests/test_render_settings.py`

- [ ] **Step 1.1: Write failing tests**

In `tests/test_render_settings.py`, append:

```python
def test_render_settings_round_trip_music_track_and_toggles(tmp_path):
    """Music-track path + include_music + include_labels + loop_music persist via JSON."""
    from src.models.project import (
        CaptureSettings, Project, RenderSettings, SplinePath,
    )
    rs = RenderSettings(
        music_track="/home/user/music/aurora.mp3",
        include_music=False,
        include_labels=False,
        loop_music=False,
    )
    proj = Project(
        project="t",
        path=SplinePath(control_points=[]),
        capture_settings=CaptureSettings(),
        capture_points=[],
        render_settings=rs,
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(proj.model_dump_json(indent=2))
    reloaded = Project.model_validate_json(manifest.read_text())
    assert reloaded.render_settings.music_track == "/home/user/music/aurora.mp3"
    assert reloaded.render_settings.include_music is False
    assert reloaded.render_settings.include_labels is False
    assert reloaded.render_settings.loop_music is False


def test_render_settings_defaults_when_field_missing():
    """Manifests written before this issue still load with sensible defaults."""
    from src.models.project import RenderSettings
    raw_json = '{"black": 0.0, "white": 1.0, "midtone": 0.5}'
    rs = RenderSettings.model_validate_json(raw_json)
    assert rs.music_track is None
    assert rs.include_music is True
    assert rs.include_labels is True
    assert rs.loop_music is True
```

- [ ] **Step 1.2: Run — they fail with `AttributeError` or similar**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_render_settings.py -v -k "music_track or toggles"
```

Expected: FAIL on the new attribute names.

- [ ] **Step 1.3: Add the three fields to `RenderSettings`**

In `src/models/project.py`, locate `class RenderSettings(BaseModel):`. Append after the last existing field:

```python
    music_track: str | None = Field(
        default=None,
        description=(
            "Absolute path to an audio file (mp3, wav, m4a, ogg, flac) "
            "muxed into the rendered video when ``include_music`` is True. "
            "``None`` means no track configured."
        ),
    )
    include_music: bool = Field(
        default=True,
        description=(
            "Toggle whether the configured ``music_track`` is attached "
            "to the rendered video. When False, the music path stays "
            "configured but the audio mux is skipped."
        ),
    )
    include_labels: bool = Field(
        default=True,
        description=(
            "Toggle whether project labels are burnt into the rendered "
            "frames. Flows into ``RenderConfig.render_labels`` at "
            "config-build time."
        ),
    )
    loop_music: bool = Field(
        default=True,
        description=(
            "When True the music track is looped via ffmpeg "
            "``-stream_loop -1`` so a short audio file covers the "
            "full video. Default True — typical use case."
        ),
    )
```

- [ ] **Step 1.4: Run — tests pass**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_render_settings.py -v
```

Expected: all pass, including the two new ones.

- [ ] **Step 1.5: Full suite — no regressions**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Expected: 272 passed (270 existing + 2 new).

- [ ] **Step 1.6: Commit**

```bash
git add src/models/project.py tests/test_render_settings.py
git commit -m "feat(models): RenderSettings.music_track / include_music / include_labels"
```

---

### Task 2: `mux_audio` helper + unit tests

**Files:**
- Modify: `src/renderer/video.py`
- Create: `tests/test_music_mux.py`

- [ ] **Step 2.1: Failing test for `mux_audio` argv**

Create `tests/test_music_mux.py`:

```python
"""Tests for the ffmpeg-audio mux helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.renderer.video import mux_audio


def test_mux_audio_invokes_ffmpeg_with_expected_argv(tmp_path):
    """``mux_audio`` builds the correct ffmpeg command."""
    video = tmp_path / "silent.mp4"
    audio = tmp_path / "music.mp3"
    output = tmp_path / "final.mp4"
    video.write_bytes(b"")
    audio.write_bytes(b"")

    with patch("src.renderer.video.subprocess.run") as run:
        mux_audio(video, audio, output)
        args = run.call_args[0][0]
        assert args[0] == "ffmpeg"
        assert "-i" in args and str(video) in args and str(audio) in args
        assert "-c:v" in args and "copy" in args
        assert "-c:a" in args and "aac" in args
        assert "-shortest" in args
        assert args[-1] == str(output)


def test_mux_audio_raises_on_ffmpeg_failure(tmp_path):
    """Failure surfaces as ``CalledProcessError`` from subprocess.run."""
    import subprocess
    video = tmp_path / "v.mp4"; video.write_bytes(b"")
    audio = tmp_path / "a.mp3"; audio.write_bytes(b"")
    output = tmp_path / "out.mp4"

    with patch(
        "src.renderer.video.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "ffmpeg"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            mux_audio(video, audio, output)


def test_mux_audio_loop_injects_stream_loop_flag(tmp_path):
    """``loop=True`` adds ``-stream_loop -1`` between video and audio inputs."""
    video = tmp_path / "v.mp4"; video.write_bytes(b"")
    audio = tmp_path / "a.mp3"; audio.write_bytes(b"")
    output = tmp_path / "out.mp4"

    with patch("src.renderer.video.subprocess.run") as run:
        mux_audio(video, audio, output, loop=True)
        argv = run.call_args[0][0]
        # The pattern is ``-i video -stream_loop -1 -i audio``.
        i_video = argv.index(str(video))
        i_audio = argv.index(str(audio))
        assert "-stream_loop" in argv
        loop_idx = argv.index("-stream_loop")
        assert i_video < loop_idx < i_audio
        assert argv[loop_idx + 1] == "-1"
```

- [ ] **Step 2.2: Run — fails with ImportError**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_music_mux.py -v
```

Expected: `ImportError: cannot import name 'mux_audio' from 'src.renderer.video'`.

- [ ] **Step 2.3: Implement `mux_audio`**

In `src/renderer/video.py`, append:

```python
def mux_audio(
    video_path: Path, audio_path: Path, output_path: Path,
    *, loop: bool = False,
) -> None:
    """Mux ``audio_path`` into ``video_path`` and write ``output_path``.

    Video stream is copied (no re-encode); audio is converted to AAC
    @ 192 kbit/s for broad mp4 compatibility. ``-shortest`` trims the
    output to the shorter of the two streams.

    When ``loop=True`` the audio input is opened with
    ``-stream_loop -1`` so a short track repeats until the longer
    video finishes — ``-shortest`` then ends the output at the video.
    Raises ``subprocess.CalledProcessError`` if ffmpeg returns
    non-zero.
    """
    cmd = ["ffmpeg", "-y", "-i", str(video_path)]
    if loop:
        cmd += ["-stream_loop", "-1"]
    cmd += [
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
```

`subprocess` is already imported at the top of `video.py`; verify with grep and add the import only if missing.

- [ ] **Step 2.4: Run — tests pass**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_music_mux.py -v
```

Expected: 2/2 pass.

- [ ] **Step 2.5: Full suite**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Expected: 275 passed.

- [ ] **Step 2.6: Commit**

```bash
git add src/renderer/video.py tests/test_music_mux.py
git commit -m "feat(renderer): mux_audio helper attaches audio to encoded video via ffmpeg"
```

---

### Task 3: Pipeline orchestration

**Files:**
- Modify: `src/renderer/pipeline.py`
- Modify: `tests/test_music_mux.py` (add pipeline-level test)

- [ ] **Step 3.1: Locate the ffmpeg encode call**

In `src/renderer/pipeline.py`, find where `encode_video` is called inside `render()`. Identify the temp video path it writes to and the final `output_path`.

- [ ] **Step 3.2: Replace the direct rename with conditional mux**

Where `render()` currently has:

```python
encode_video(temp_dir, output_path, fps=..., crf=..., ...)
```

Replace with:

```python
silent_path = output_path.with_suffix(".silent.mp4") if (
    self.config.include_music and self.config.music_track
) else output_path
encode_video(temp_dir, silent_path, fps=..., crf=..., ...)

if (
    self.config.include_music
    and self.config.music_track
):
    music_path = Path(self.config.music_track)
    if music_path.exists():
        try:
            mux_audio(
                silent_path, music_path, output_path,
                loop=self.config.loop_music,
            )
            silent_path.unlink(missing_ok=True)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Audio mux failed (%s) — keeping silent render at %s",
                exc, silent_path,
            )
            silent_path.replace(output_path)
    else:
        logger.warning(
            "Music track %s not found — keeping silent render",
            music_path,
        )
        silent_path.replace(output_path)
```

`subprocess` and `mux_audio` will need to be imported at the top of `pipeline.py` if not already present.

Also: `RenderConfig` needs `music_track: str | None = None`, `include_music: bool = True`, and `loop_music: bool = True` mirrored from `RenderSettings`. Add them next to the existing `render_labels` field.

The bridging that maps `render_settings` to `config` lives in `_apply_render_settings_to_config` (or equivalent) inside `render_layout.py`. We'll wire that in Task 4.

- [ ] **Step 3.3: Pipeline-level tests**

Append to `tests/test_music_mux.py`:

```python
def test_pipeline_calls_mux_when_track_and_toggle_set(tmp_path, monkeypatch):
    """Render() invokes mux_audio when include_music + music_track."""
    from unittest.mock import MagicMock
    from src.renderer.pipeline import RenderConfig
    fake_mux = MagicMock()
    monkeypatch.setattr("src.renderer.pipeline.mux_audio", fake_mux)
    # … construct a minimal pipeline + run render() against tmp_path …
    # (Boilerplate omitted — use the same minimal fixture pattern as
    #  the existing pipeline tests under tests/test_render_pipeline.py;
    #  see the ``_make_project`` helper there.)
    # Assertion:
    assert fake_mux.called
```

The detailed pipeline fixture is identical to the one in
`tests/test_render_pipeline.py` (it patches `encode_video` so no real
ffmpeg runs). Copy the helper rather than re-import — pipeline tests
keep their fixtures local.

- [ ] **Step 3.4: Full suite — green**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Expected: 276 passed (one new pipeline test).

- [ ] **Step 3.5: Commit**

```bash
git add src/renderer/pipeline.py tests/test_music_mux.py
git commit -m "feat(renderer): RenderPipeline.render() muxes audio after encode when configured"
```

---

### Task 4: UI — music-track row + two checkboxes

**Files:**
- Modify: `src/renderer/ui/render_layout.py`

- [ ] **Step 4.1: Find `_build_output_settings`**

Search for `def _build_output_settings` in `src/renderer/ui/render_layout.py`. That function builds the fps/crf/speed/resolution row in the right pane.

- [ ] **Step 4.2: Add the music-track row above the existing fields**

Inside `_build_output_settings`, immediately after the `with ui.row(...)` opening, insert:

```python
        with ui.row().classes("w-full items-center gap-2"):
            music_input = ui.input(
                "Musik (.mp3 / .wav / .m4a / .ogg / .flac)",
                value=state.music_track or "",
            ).classes("flex-grow").props("readonly dense")
            music_input.bind_value_from(state, "music_track").bind_visibility_from(
                state, "stretch_mode", backward=lambda _m: True,
            )

            def _pick_music() -> None:
                from src.ui.folder_browser import FolderBrowserDialog
                def _on_select(p) -> None:
                    if p is None:
                        return
                    state.music_track = str(p)
                    music_input.set_value(str(p))
                    _save_render_state(state)
                FolderBrowserDialog(
                    on_select=_on_select, files_only=True,
                ).open(Path(state.input_dir))

            ui.button(
                "Auswählen", icon="audio_file",
                on_click=_pick_music,
            ).props("dense flat")

            def _clear_music() -> None:
                state.music_track = None
                music_input.set_value("")
                _save_render_state(state)

            ui.button(
                icon="close", on_click=_clear_music,
            ).props("dense flat").tooltip(
                "Music-Track aus der Konfiguration entfernen",
            )

        with ui.row().classes("w-full items-center gap-4 ml-1"):
            ui.checkbox(
                "Musik einbinden",
            ).bind_value(state, "include_music").tooltip(
                "Wenn aktiv und ein Music-Track ausgewählt ist, wird "
                "die Audiospur per ffmpeg an das gerenderte Video "
                "angehängt.",
            )
            ui.checkbox(
                "Musik loopen",
            ).bind_value(
                state, "loop_music",
            ).bind_enabled_from(
                state, "include_music",
            ).tooltip(
                "Wenn das Audio kürzer ist als das Video, wird es "
                "wiederholt bis das Video endet (ffmpeg "
                "-stream_loop -1). Wirkt nur wenn 'Musik einbinden' "
                "aktiv ist.",
            )
            ui.checkbox(
                "Labels einbinden",
            ).bind_value(state, "include_labels").tooltip(
                "Wenn deaktiviert wird das Video ohne Label-Overlays "
                "gerendert, auch wenn welche im Projekt definiert sind.",
            )
```

If `FolderBrowserDialog` doesn't already support `files_only=True`,
fall back to the standard open + a manual file-existence check after
the user selects a path. That detail lives in `src/ui/folder_browser.py`;
inspect it before assuming.

- [ ] **Step 4.3: Add the state fields**

In `_RenderState.__init__`, find the cluster of fields like
`self.render_workers`, `self.preview_detail_mode`, etc., and add:

```python
        self.music_track: str | None = None
        self.include_music: bool = True
        self.include_labels: bool = True
        self.loop_music: bool = True
```

In `_PROJECT_PERSISTED_FIELDS` (the tuple near the top of the file
that lists which `_RenderState` attrs map into `RenderSettings`), add
the three new field names:

```python
_PROJECT_PERSISTED_FIELDS = (
    # ... existing entries ...
    "music_track",
    "include_music",
    "include_labels",
    "loop_music",
)
```

This is enough to round-trip the values via the existing
`_save_render_state` / `_apply_render_settings_to_state` machinery
(see #151).

- [ ] **Step 4.4: Map `include_labels` to `RenderConfig.render_labels`**

In the function that builds the `RenderConfig` for an actual render
(search for `RenderConfig(` in `render_layout.py`), set:

```python
config = RenderConfig(
    # ... existing args ...
    render_labels=state.include_labels,
    music_track=state.music_track,
    include_music=state.include_music,
    loop_music=state.loop_music,
)
```

Add `music_track`, `include_music`, and `loop_music` to `RenderConfig`
as well — the pipeline already reads them in Task 3.

- [ ] **Step 4.5: Smoke test**

```bash
make run-render
```

Load a project. Verify:

1. The music input + Auswählen/✕ buttons + two checkboxes are visible
   above the fps/crf row.
2. Clicking Auswählen opens the folder browser; picking an `.mp3`
   stores the absolute path in the input field and persists it on
   manifest save.
3. Toggling "Musik einbinden" off → no audio in the next render.
4. Toggling "Labels einbinden" off → labels disappear from the
   rendered output (verify by previewing one frame and checking with
   a clean vs labelled render).
5. Reload the page — both checkbox states and the music path survive.

- [ ] **Step 4.6: Commit**

```bash
git add src/renderer/ui/render_layout.py src/renderer/pipeline.py
git commit -m "feat(ui): music-track picker + include_music / include_labels checkboxes"
```

---

### Task 5: Push + close

- [ ] **Step 5.1: Push**

```bash
git push origin master
```

- [ ] **Step 5.2: Close the issue**

```bash
gh issue close <NUMBER> --comment "Shipped music-track + render toggles — per-project music path stored on RenderSettings, ffmpeg mux post-process, two global checkboxes for music + labels. Spec: docs/superpowers/specs/2026-06-06-music-track-and-render-toggles-design.md"
```

---

## Self-Review

**Spec coverage:**

| Spec § | Task |
|---|---|
| 3 Data model (3 new fields) | Task 1 |
| 4 UI music row + checkboxes | Task 4 |
| 5 Render pipeline integration (mux + include_labels) | Tasks 2 + 3 + 4 |
| 6 Edge cases (missing file, ffmpeg failure) | Task 3 step 3.2 (fallback to silent on failure / missing) |
| 8 Testing (round-trip, mux argv, pipeline) | Tasks 1 + 2 + 3 |

**Placeholder scan:** the pipeline-level test in Task 3.3 references
`tests/test_render_pipeline.py`'s minimal fixture and asks the
implementer to copy that helper. That's a real piece of work, not a
placeholder — the implementer reads the file and ports the fixture
without inventing new structure.

**Type consistency:** `music_track: str | None` matches between
`RenderSettings`, `RenderConfig`, and `_RenderState`. `include_music`
and `include_labels` are `bool` everywhere. The renderer's existing
`RenderConfig.render_labels` keeps its name; `include_labels` is the
UI-facing field that maps into it.
