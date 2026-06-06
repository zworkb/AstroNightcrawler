# Music Track + Global Render Toggles — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Status: design approved, ready for implementation plan

## 1. Goal

Two related additions to the render pipeline:

1. **Music track** — let the user pick an audio file (mp3 / wav / m4a /
   ogg / flac) per project. The path is stored in `manifest.json`. At
   render time, if a track is configured and the "Musik einbinden"
   checkbox is on, ffmpeg muxes the audio into the final mp4 after the
   video stream is written.

2. **Global render toggles** — two checkboxes in the render-settings
   panel:
   - **Musik einbinden** (controls the music-track post-process)
   - **Labels einbinden** (already exists as `RenderConfig.render_labels`
     but is not exposed in the UI; surface it as a checkbox)

   Both are project-level (apply to the whole render), not per-frame or
   per-label.

## 2. Why now

User asked for it after we walked through the existing ffmpeg "attach
audio to video" pattern. They also surfaced that the labels-in / labels-
out switch already exists in the renderer config (`render_labels`) but
the only way to set it today is via CLI flag — UI users had no toggle.
A music track is a natural companion: rendered sky-path videos want a
background score, and the alternative (post-processing in another tool)
is friction the renderer can absorb cheaply.

## 3. Data model

Add three fields to `RenderSettings` (the per-project, manifest-
persisted side from #151):

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
        "Toggle whether the configured ``music_track`` is attached to "
        "the rendered video. When False, the music path stays "
        "configured but the audio mux is skipped — useful for "
        "comparison renders without changing the track choice."
    ),
)
include_labels: bool = Field(
    default=True,
    description=(
        "Toggle whether project labels are burnt into the rendered "
        "frames. Mirrors the existing ``RenderConfig.render_labels`` "
        "(CLI ``--no-labels``); exposing it here lets the UI honour the "
        "choice per-project."
    ),
)
loop_music: bool = Field(
    default=False,
    description=(
        "When True the music track is looped (ffmpeg "
        "``-stream_loop -1``) so a short audio file covers a longer "
        "video. Default False — single play-through then audio cuts."
    ),
)
```

`include_labels` is wired into the existing `RenderConfig.render_labels`
flow — the renderer reads `render_settings.include_labels` at config-
build time and passes it through. No new code path in
`_render_to_dir`.

Path is **absolute** in v1 for simplicity (works with files outside the
project directory). Relative-path support could be added later if
portability becomes a concern.

## 4. UI

### Music-track row in the render-settings panel

Above the existing fps/crf/speed/resolution row:

```
Musik: [/home/phil/music/aurora.mp3                    ] [Auswählen]  [☐ Musik einbinden]
                                                                       [☐ Labels einbinden]
```

- File path is a read-only `ui.input` (user can copy/paste to share).
- **Auswählen** opens the existing `FolderBrowserDialog` in single-file
  mode (returns a single absolute path).
- The two main checkboxes bind directly to
  `render_settings.include_music` and `render_settings.include_labels`.
  Reordering them onto separate lines is fine; they're independent.
- A third small checkbox **"Musik loopen"** (inline with "Musik
  einbinden") binds to `loop_music`. Disabled when `include_music`
  is off.
- Clicking Auswählen with no current music sets the path; clicking
  again replaces it. A small "✕" icon next to the path field clears
  the track (sets `music_track=None`).

### Validation

When the user picks a file:
- If the file doesn't exist or is unreadable, `ui.notify` with a red
  banner and don't update the field.
- If the path's extension isn't in the supported set, `ui.notify` with
  a warning but still accept (ffmpeg might handle it). The supported
  set is informational, not enforced.

## 5. Render pipeline integration

The current `RenderPipeline.render()` writes video frames as PNGs into
a temp dir, then calls ffmpeg to encode them into the output `.mp4`.
That call lives in `src/renderer/video.py:encode_video`.

For music we add a second ffmpeg invocation **after** the video is
encoded:

```python
def mux_audio(
    video_path: Path, audio_path: Path, output_path: Path,
    *, loop: bool = False,
) -> None:
    """Mux ``audio_path`` into ``video_path``, write to ``output_path``.

    Video stream is copied (no re-encode); audio is encoded to AAC
    for broad mp4 compatibility. ``-shortest`` clips to the shorter
    of the two streams. With ``loop=True`` the audio input is opened
    via ``-stream_loop -1`` so a short track repeats to cover a
    longer video; ``-shortest`` then ensures output ends with the
    video.
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

Orchestration in `RenderPipeline.render()`:

1. Render video frames + encode to a temp `output.silent.mp4`.
2. If `config.include_music` AND `config.music_track`:
   - Call `mux_audio(silent, music, final)`.
   - Delete the silent intermediate.
3. Else: rename `silent` → `final`.

When `include_music=False` the music path stays in the manifest but
isn't applied — the silent file is the final output.

When the configured `music_track` file doesn't exist at render time
(deleted, moved, on a network drive that's offline), log a warning
and proceed with the silent file. Don't fail the whole render over a
missing audio file.

### include_labels routing

The existing `RenderConfig.render_labels` field already gates the
PIL-side label burn-in. The new field
`render_settings.include_labels` simply flows into
`config.render_labels` at config-build time
(`_apply_render_settings_to_config` in the UI). No renderer changes.

## 6. Edge cases

- **Music shorter than video**: enable the "Musik loopen" checkbox
  → ffmpeg's `-stream_loop -1` repeats the audio until the video
  ends. With the loop disabled (default) `-shortest` clips the
  output to the audio length and the video gets cut — the file-
  picker tooltip warns about this.
- **Music longer than video**: `-shortest` clips audio at video end.
  Correct behaviour — no action.
- **No ffmpeg available**: `encode_video` already enforces ffmpeg
  presence via `check_ffmpeg`. Same check covers the mux step.
- **Audio format ffmpeg can't decode**: subprocess raises
  `CalledProcessError`; the renderer surfaces the stderr in the UI
  notification.
- **Backward compatibility**: existing manifests without the new
  fields load fine because all three have defaults.

## 7. Out of scope (v1)

- Fade-in / fade-out on the audio. Future enhancement.
- Multiple audio tracks per video (e.g. score + narration).
- Volume / level adjustment. Default = original volume.
- Per-segment music (different track per transition / capture point).
- Relative-path / asset-bundle for music. Absolute path keeps v1
  small.

## 8. Testing

Pytest fixtures under `tests/test_music_mux.py`:

- Mock `subprocess.run` and verify the ffmpeg argv when calling
  `mux_audio` (no actual encoding).
- Round-trip a `RenderSettings` with `music_track` + the two flags
  through `Project.model_dump_json` / `model_validate_json` and
  assert the values survive.
- A render-pipeline integration test that constructs a `RenderConfig`
  with `include_music=True` + a fake music_track path, runs the
  render code path with `subprocess.run` patched, and asserts:
  - `mux_audio` is invoked when both flags are set.
  - Not invoked when `include_music=False`.
  - Not invoked when `music_track is None`.
  - Falls back to the silent file when `music_track` doesn't exist.
- A `mux_audio` test for the loop branch: `loop=True` injects
  `-stream_loop -1` into the argv between the video and audio inputs.

`include_labels` already has end-to-end coverage via the existing
render-pipeline tests (`render_labels=False` produces a clean
output). Add one UI-level assertion that the new checkbox binds
correctly to the project field.

## 9. Implementation order

1. Model: add the three fields to `RenderSettings`. Round-trip
   tests stay green.
2. `mux_audio` helper in `src/renderer/video.py` (next to
   `encode_video`). Unit tests against `subprocess.run` mocks.
3. `RenderPipeline.render` orchestrates the mux step after
   `encode_video`. New pipeline-level tests.
4. UI: music-track row in the render-settings panel; bind two
   checkboxes; file-picker handler. Smoke test on a real project.
5. Wire `render_settings.include_labels` into
   `RenderConfig.render_labels` at config-build time.

Each step ships in one commit; UI + handler land together.
