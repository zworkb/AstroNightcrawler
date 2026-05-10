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
