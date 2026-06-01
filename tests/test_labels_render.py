"""Tests for the PIL-based _draw_labels helper."""

from __future__ import annotations

import numpy as np
import pytest

from src.models.project import Label
from src.renderer.labels import _draw_labels


def _blank(width: int = 200, height: int = 100) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_draw_labels_no_labels_returns_unchanged():
    img = _blank()
    out = _draw_labels(img, labels=[], offsets=[], frame_dims=(200, 100))
    assert np.array_equal(out, img)


def test_draw_labels_in_view_changes_pixels():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=100.0, y=50.0,
                  color="#ffffff", marker="dot")
    out = _draw_labels(img, labels=[label], offsets=[(0.0, 0.0)],
                       frame_dims=(200, 100))
    assert not np.array_equal(out, img)
    # the dot at the label position should be non-zero
    assert out[50, 100].sum() > 0


def test_draw_labels_out_of_view_leaves_image_unchanged():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=10.0, y=10.0)
    # cumulative offset of 1000 px shoves the label out of frame
    out = _draw_labels(img, labels=[label], offsets=[(1000.0, 0.0)],
                       frame_dims=(200, 100))
    assert np.array_equal(out, img)


def test_draw_labels_marker_none_still_draws_text():
    img = _blank()
    label = Label(id="a", text="HELLO", ref_frame_index=0, x=20.0, y=50.0,
                  marker="none", color="#ffffff")
    out = _draw_labels(img, labels=[label], offsets=[(0.0, 0.0)],
                       frame_dims=(200, 100))
    # text should leave SOME non-zero pixels in the area to the right of (20, 50)
    region = out[30:70, 20:200]
    assert region.sum() > 0


def test_draw_labels_offsets_length_must_match_labels():
    img = _blank()
    label = Label(id="a", text="X", ref_frame_index=0, x=0.0, y=0.0)
    with pytest.raises(ValueError):
        _draw_labels(img, labels=[label], offsets=[],
                     frame_dims=(200, 100))


def test_draw_labels_with_leader_line_draws_pixels_between_marker_and_text():
    """leader='line' colors at least one pixel along the marker→text segment."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="dot",
        text_offset_x=150, text_offset_y=-60,
        leader="line",
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # Check a 5×10 region around the geometric midpoint between marker
    # (100, 100) and the bbox-edge endpoint (≈250, 54).  The exact
    # endpoint depends on font metrics, so we use a window rather than
    # a single pixel to stay robust across systems.
    region = out[68:78, 170:181]
    assert region.max() > 0, f"expected pixels in mid region, got max {region.max()}"


def test_draw_labels_leader_none_draws_no_pixels_between():
    """Default leader='none' must NOT light the midpoint."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="dot",
        text_offset_x=150, text_offset_y=-60,
        # leader omitted → defaults to "none"
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    region = out[68:78, 170:181]
    assert region.max() == 0, f"unexpected pixel with leader='none': {region.max()}"
