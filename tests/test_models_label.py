"""Tests for the Label model and Project.labels field."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    Label,
    Project,
    SplinePath,
)


def test_label_minimal_construction():
    """A Label needs id, text, ref_frame_index, x, y; the rest have defaults."""
    label = Label(id="abc-123", text="M27", ref_frame_index=0, x=100.5, y=200.0)
    assert label.color == "#ffff00"
    assert label.font_size == 24
    assert label.marker == "dot"
    assert label.source == "manual"
    assert label.catalog_ra is None


def test_label_validation_rejects_negative_ref_frame_index():
    with pytest.raises(ValidationError):
        Label(id="x", text="t", ref_frame_index=-1, x=0.0, y=0.0)


def test_label_round_trip_through_json():
    """Pydantic JSON round-trip preserves every field."""
    original = Label(
        id="uuid-1",
        text="catalog hit",
        ref_frame_index=3,
        x=512.0,
        y=384.5,
        color="#00ffff",
        font_size=32,
        marker="circle",
        text_offset_x=20,
        text_offset_y=-5,
        source="catalog",
        catalog_ra=299.901,
        catalog_dec=22.721,
        catalog_id="M27",
    )
    rebuilt = Label.model_validate_json(original.model_dump_json())
    assert rebuilt == original


def test_project_labels_defaults_to_empty_list():
    project = Project(
        project="p",
        path=SplinePath(control_points=[]),
    )
    assert project.labels == []
    assert project.north_angle_deg == 0.0


def test_project_loads_old_manifest_without_labels_key():
    """Backward compat: manifests written before this feature must still load."""
    old_manifest = {
        "version": "1.0",
        "created": "2026-01-01T00:00:00",
        "project": "old",
        "path": {"control_points": [], "spline_type": "cubic_bezier", "coordinate_frame": "J2000"},
        "capture_settings": {},
        "capture_points": [],
        "indi": None,
    }
    project = Project.model_validate(old_manifest)
    assert project.labels == []
    assert project.north_angle_deg == 0.0


def test_project_round_trip_with_labels():
    label = Label(id="u1", text="t", ref_frame_index=0, x=10.0, y=20.0)
    project = Project(
        project="p",
        path=SplinePath(control_points=[]),
        labels=[label],
        north_angle_deg=1.5,
    )
    rebuilt = Project.model_validate_json(project.model_dump_json())
    assert rebuilt.labels == [label]
    assert rebuilt.north_angle_deg == 1.5


def test_project_default_wcs_flip_180_is_false():
    project = Project(project="t", path=SplinePath(control_points=[]))
    assert project.wcs_flip_180 is False


def test_project_loads_old_manifest_without_wcs_flip_180():
    project = Project.model_validate({
        "project": "t",
        "path": {"control_points": []},
    })
    assert project.wcs_flip_180 is False
