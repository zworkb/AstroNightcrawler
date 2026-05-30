"""Tests for src.ui.overlay_sync helpers.

Focused on the _display_status derivation that drives capture-point
coloring on the star map (issue #143).
"""

from __future__ import annotations

from src.models.project import CapturedFrame, CapturePoint
from src.ui.overlay_sync import _display_status


def _good(name: str) -> CapturedFrame:
    return CapturedFrame(filename=name, status="good")


class TestDisplayStatus:
    def test_open_when_no_frames(self) -> None:
        cp = CapturePoint(index=0, ra=0.0, dec=0.0, target_subs=3)
        assert _display_status(cp) == "open"

    def test_partial_when_some_good_frames(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=3,
            frames=[_good("a.fits"), _good("b.fits")],
        )
        assert _display_status(cp) == "partial"

    def test_complete_when_target_reached(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=2,
            frames=[_good("a.fits"), _good("b.fits")],
        )
        assert _display_status(cp) == "complete"

    def test_skipped_takes_precedence_over_frames(self) -> None:
        # Skipped wins even when good_count would otherwise mark the
        # point complete -- a skipped point reads as grey on the map.
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=2, skipped=True,
            frames=[_good("a.fits"), _good("b.fits")],
        )
        assert _display_status(cp) == "skipped"

    def test_rejected_frames_do_not_count_as_partial(self) -> None:
        cp = CapturePoint(
            index=0, ra=0.0, dec=0.0, target_subs=2,
            frames=[CapturedFrame(filename="a.fits", status="rejected")],
        )
        assert _display_status(cp) == "open"
