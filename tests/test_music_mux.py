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
