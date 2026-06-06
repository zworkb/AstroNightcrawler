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


def test_pipeline_calls_mux_when_track_and_toggle_set(tmp_path, monkeypatch):
    """RenderPipeline.render() invokes mux_audio when include_music + music_track + file exists."""
    from unittest.mock import MagicMock
    from src.renderer.pipeline import RenderConfig, RenderPipeline

    fake_mux = MagicMock()
    fake_encode = MagicMock(
        side_effect=lambda temp, out_path, *args, **kwargs: out_path.write_bytes(b""),
    )
    monkeypatch.setattr("src.renderer.pipeline.mux_audio", fake_mux)
    monkeypatch.setattr("src.renderer.pipeline.encode_video", fake_encode)
    monkeypatch.setattr("src.renderer.pipeline.check_ffmpeg", lambda: True)

    music_file = tmp_path / "music.mp3"
    music_file.write_bytes(b"")

    config = RenderConfig(
        music_track=str(music_file),
        include_music=True,
        loop_music=False,
    )
    pipeline = RenderPipeline(tmp_path, config)
    pipeline.frames = []
    monkeypatch.setattr(
        pipeline, "active_frames", lambda: [object(), object()],
    )
    monkeypatch.setattr(
        pipeline, "_render_to_dir", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        pipeline, "_get_temp_dir", lambda: tmp_path / "tempdir",
    )
    (tmp_path / "tempdir").mkdir()

    pipeline.render(tmp_path / "out.mp4")
    assert fake_mux.called
    _, kwargs = fake_mux.call_args
    assert kwargs.get("loop") is False


def test_pipeline_skips_mux_when_include_music_false(tmp_path, monkeypatch):
    """When include_music=False the mux step is skipped even if music_track is set."""
    from unittest.mock import MagicMock
    from src.renderer.pipeline import RenderConfig, RenderPipeline

    fake_mux = MagicMock()
    fake_encode = MagicMock(
        side_effect=lambda temp, out_path, *args, **kwargs: out_path.write_bytes(b""),
    )
    monkeypatch.setattr("src.renderer.pipeline.mux_audio", fake_mux)
    monkeypatch.setattr("src.renderer.pipeline.encode_video", fake_encode)
    monkeypatch.setattr("src.renderer.pipeline.check_ffmpeg", lambda: True)

    music_file = tmp_path / "music.mp3"
    music_file.write_bytes(b"")

    config = RenderConfig(
        music_track=str(music_file), include_music=False,
    )
    pipeline = RenderPipeline(tmp_path, config)
    monkeypatch.setattr(
        pipeline, "active_frames", lambda: [object(), object()],
    )
    monkeypatch.setattr(
        pipeline, "_render_to_dir", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        pipeline, "_get_temp_dir", lambda: tmp_path / "td",
    )
    (tmp_path / "td").mkdir()

    pipeline.render(tmp_path / "out.mp4")
    assert not fake_mux.called


def test_pipeline_falls_back_to_silent_when_track_missing(tmp_path, monkeypatch):
    """Missing music file → silent render becomes the final output, no exception."""
    from unittest.mock import MagicMock
    from src.renderer.pipeline import RenderConfig, RenderPipeline

    fake_mux = MagicMock()
    fake_encode = MagicMock(
        side_effect=lambda temp, out_path, *args, **kwargs: out_path.write_bytes(b""),
    )
    monkeypatch.setattr("src.renderer.pipeline.mux_audio", fake_mux)
    monkeypatch.setattr("src.renderer.pipeline.encode_video", fake_encode)
    monkeypatch.setattr("src.renderer.pipeline.check_ffmpeg", lambda: True)

    config = RenderConfig(
        music_track="/nonexistent/music.mp3", include_music=True,
    )
    pipeline = RenderPipeline(tmp_path, config)
    monkeypatch.setattr(
        pipeline, "active_frames", lambda: [object(), object()],
    )
    monkeypatch.setattr(
        pipeline, "_render_to_dir", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        pipeline, "_get_temp_dir", lambda: tmp_path / "td",
    )
    (tmp_path / "td").mkdir()

    out_path = tmp_path / "out.mp4"
    pipeline.render(out_path)
    assert not fake_mux.called
    assert out_path.exists()
