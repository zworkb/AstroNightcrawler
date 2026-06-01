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
        offset_radius=0,
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


def test_draw_labels_with_leader_arrow_paints_arrowhead_at_marker():
    """leader='arrow' lights pixels in the arrowhead triangle near the marker."""
    img = _blank(width=400, height=200)
    label = Label(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0,
        color="#ffffff", marker="none",  # marker draws nothing of its own
        text_offset_x=150, text_offset_y=0,  # horizontal — arrow base is east of marker
        leader="arrow",
        font_size=24,
        offset_radius=0,
    )
    out = _draw_labels(
        img, labels=[label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # Arrow length = max(4, 24/3) = 8 px; half-width = max(2, 24/4) = 6 px.
    # The triangle sits between x=100 (tip) and x=108 (base centre);
    # a pixel at (x=105, y=100) is squarely inside (center row).
    assert out[100, 105].max() > 0, (
        f"expected arrowhead pixel at (105, 100), got {out[100, 105]}"
    )
    # Also verify the lower half of the arrowhead triangle is filled
    # (y=104 is 4 rows below center, still within half_w=6 at x=105-107).
    assert out[104, 105].max() > 0, (
        f"expected arrowhead pixel at (105, 104), got {out[104, 105]}"
    )


def test_draw_labels_leader_line_has_no_arrowhead_extras():
    """leader='line' does NOT fill the arrowhead-only region."""
    img_arrow = _blank(width=400, height=200)
    img_line = _blank(width=400, height=200)
    base = dict(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0, color="#ffffff", marker="none",
        text_offset_x=150, text_offset_y=0, font_size=24,
        offset_radius=0,
    )
    arrow_label = Label(**base, leader="arrow")
    line_label = Label(**base, leader="line")
    out_arrow = _draw_labels(
        img_arrow, labels=[arrow_label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    out_line = _draw_labels(
        img_line, labels=[line_label], offsets=[(0.0, 0.0)],
        frame_dims=(400, 200),
    )
    # (x=105, y=104) is inside the arrow triangle's lower half (4 rows below
    # the horizontal centre; the line-only label has no pixels there).
    assert out_arrow[104, 105].max() > 0
    assert out_line[104, 105].max() == 0


def test_draw_labels_leader_offset_radius_clears_endpoints():
    """offset_radius>0 leaves empty space around both target and text."""
    img_gap = _blank(width=600, height=200)
    img_nogap = _blank(width=600, height=200)
    base = dict(
        id="a", text="X", ref_frame_index=0,
        x=100.0, y=100.0, color="#ffffff", marker="none",
        text_offset_x=400, text_offset_y=0, font_size=24,
        leader="line",
    )
    label_gap = Label(**base, offset_radius=50)
    label_nogap = Label(**base, offset_radius=0)
    out_gap = _draw_labels(
        img_gap, labels=[label_gap], offsets=[(0.0, 0.0)],
        frame_dims=(600, 200),
    )
    out_nogap = _draw_labels(
        img_nogap, labels=[label_nogap], offsets=[(0.0, 0.0)],
        frame_dims=(600, 200),
    )
    # 20 px past the target the no-gap line is lit but the gap line
    # isn't — the gap=50 cleared the 50-px zone around the marker.
    assert out_nogap[100, 120].max() > 0
    assert out_gap[100, 120].max() == 0
    # The midpoint between marker (100, 100) and text-anchor (500, 100)
    # is at (300, 100). The gap-line is still lit there (well inside
    # the visible middle segment).
    assert out_gap[100, 300].max() > 0
