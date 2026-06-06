"""Integration tests for the per-project ``wcs_flip_180`` toggle (#157).

The flag lives on :class:`Project` and is wired into the Catalog-Overlay
projection path in :func:`_compute_catalog_fov_slice`. These tests exercise
the real function end-to-end with a minimally-stubbed RenderState/Pipeline,
covering both WCS construction branches (``WCS(header)`` and the
``build_wcs`` synthetic fallback) so we know the flip lands in both.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from src.models.project import (
    CapturePoint,
    CaptureSettings,
    ControlPoint,
    Project,
    SplinePath,
)
from src.renderer.pipeline import RenderConfig, RenderPipeline
from src.renderer.ui.render_layout import _compute_catalog_fov_slice


# --- Fixtures ---------------------------------------------------------------


# Realistic-ish leo4 numbers from issue #157: 6224x4168 sensor at
# CDELT ≈ 0.000416 deg/px (≈ 1.5 arcsec/px). We use a smaller surrogate
# to keep test fixture I/O cheap, but pick the same scale to anchor the
# pixel-math in the issue's actual numbers.
_PX_SCALE_DEG = 0.000416
_PX_SCALE_ARCSEC = _PX_SCALE_DEG * 3600.0
_FRAME_W, _FRAME_H = 1280, 720
_CENTER_RA, _CENTER_DEC = 180.0, 45.0


def _stub_catalog() -> list[dict]:
    """Five evenly-spread synthetic catalog rows around the test FOV."""
    rows: list[dict] = []
    # All offsets stay inside ±0.2° = ~480 px at 1.5"/px so they project
    # well within the 1280x720 test frame.
    offsets = [
        ("c0", 0.00, 0.00),
        ("c1", 0.10, 0.05),
        ("c2", -0.08, 0.07),
        ("c3", 0.05, -0.06),
        ("c4", -0.04, -0.03),
    ]
    for cid, dra, ddec in offsets:
        rows.append({
            "id": cid, "name": cid,
            "ra": _CENTER_RA + dra, "dec": _CENTER_DEC + ddec,
            "mag": 5.0, "type": "Star", "catalog": "T",
        })
    return rows


def _write_fits(path: Path, header_keys: dict | None = None) -> None:
    """Write a tiny FITS with the requested header card additions."""
    data = np.zeros((_FRAME_H, _FRAME_W), dtype=np.uint16)
    hdu = fits.PrimaryHDU(data)
    if header_keys:
        for k, v in header_keys.items():
            hdu.header[k] = v
    hdu.writeto(path, overwrite=True)


def _make_pipeline(capture_dir: Path, flip: bool, *,
                   plate_solved: bool = False) -> RenderPipeline:
    """Build a 1-frame pipeline rooted at ``capture_dir``."""
    header: dict | None = None
    if plate_solved:
        header = {
            "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN",
            "CRVAL1": _CENTER_RA, "CRVAL2": _CENTER_DEC,
            "CRPIX1": _FRAME_W / 2.0 + 1.0,
            "CRPIX2": _FRAME_H / 2.0 + 1.0,
            "CDELT1": -_PX_SCALE_DEG, "CDELT2": _PX_SCALE_DEG,
            "CROTA2": 0.0,
        }
    _write_fits(capture_dir / "frame_0001_001.fits", header)
    project = Project(
        project="flip-test",
        path=SplinePath(control_points=[
            ControlPoint(ra=_CENTER_RA, dec=_CENTER_DEC),
            ControlPoint(ra=_CENTER_RA + 0.01, dec=_CENTER_DEC + 0.01),
        ]),
        capture_settings=CaptureSettings(exposure_seconds=1.0),
        capture_points=[
            CapturePoint(
                ra=_CENTER_RA, dec=_CENTER_DEC, index=0,
                status="captured", files=["frame_0001_001.fits"],
            ),
        ],
        wcs_flip_180=flip,
    )
    (capture_dir / "manifest.json").write_text(
        project.model_dump_json(indent=2),
    )
    pipeline = RenderPipeline(capture_dir, RenderConfig())
    pipeline.load()
    return pipeline


@pytest.fixture()
def patch_catalog(monkeypatch):
    """Replace ``objects_in_fov`` with one that returns our stub rows."""
    rows = _stub_catalog()

    def _fake_in_fov(ra_deg, dec_deg, fov_radius_deg, *, catalog=None):
        # Ignore the FOV filter — all stub rows are inside by construction;
        # this keeps the test independent of the bundled data/ catalog.
        return [dict(r, separation_deg=0.0) for r in rows]

    monkeypatch.setattr(
        "src.renderer.catalog.objects_in_fov", _fake_in_fov,
    )
    return rows


# --- Tests ------------------------------------------------------------------


def test_flag_off_baseline_projects_unflipped(tmp_path, patch_catalog):
    """With ``wcs_flip_180=False`` the projection matches build_wcs as-is."""
    pipeline = _make_pipeline(tmp_path, flip=False)
    state = SimpleNamespace(pipeline=pipeline)

    payload = _compute_catalog_fov_slice(state, 0)

    by_id = {o["id"]: o for o in payload["objects"]}
    # The pointing-center row (c0) lands at the geometric pixel center.
    assert "c0" in by_id, payload
    cx, cy = _FRAME_W / 2.0, _FRAME_H / 2.0
    assert by_id["c0"]["pixel_x"] == pytest.approx(cx, abs=1.0)
    assert by_id["c0"]["pixel_y"] == pytest.approx(cy, abs=1.0)


def test_flag_on_synthetic_wcs_path_flips_around_center(
    tmp_path, patch_catalog,
):
    """``build_wcs`` fallback path: every object point-mirrors through CRPIX."""
    # Same fixture, two pipelines — one with the flag off (reference),
    # one with it on. CRPIX is at frame center per build_wcs convention.
    dir_off = tmp_path / "off"
    dir_on = tmp_path / "on"
    dir_off.mkdir()
    dir_on.mkdir()

    pipe_off = _make_pipeline(dir_off, flip=False)
    pipe_on = _make_pipeline(dir_on, flip=True)

    off = _compute_catalog_fov_slice(
        SimpleNamespace(pipeline=pipe_off), 0,
    )
    on = _compute_catalog_fov_slice(
        SimpleNamespace(pipeline=pipe_on), 0,
    )

    off_by_id = {o["id"]: o for o in off["objects"]}
    on_by_id = {o["id"]: o for o in on["objects"]}
    shared = set(off_by_id) & set(on_by_id)
    assert shared, "no overlap between flipped/unflipped projections"

    cx, cy = _FRAME_W / 2.0, _FRAME_H / 2.0
    for cid in shared:
        a = off_by_id[cid]
        b = on_by_id[cid]
        # Point-mirror around (cx, cy): b = 2*c - a, ±1 px slack for the
        # CRPIX 0/1-indexed half-pixel + projection round-trip drift.
        assert b["pixel_x"] == pytest.approx(2 * cx - a["pixel_x"], abs=1.5)
        assert b["pixel_y"] == pytest.approx(2 * cy - a["pixel_y"], abs=1.5)


def test_flag_on_plate_solved_header_path_flips(tmp_path, patch_catalog):
    """``WCS(header)`` path: flip must also apply when the header has CTYPE."""
    dir_off = tmp_path / "off"
    dir_on = tmp_path / "on"
    dir_off.mkdir()
    dir_on.mkdir()

    pipe_off = _make_pipeline(dir_off, flip=False, plate_solved=True)
    pipe_on = _make_pipeline(dir_on, flip=True, plate_solved=True)

    off = _compute_catalog_fov_slice(
        SimpleNamespace(pipeline=pipe_off), 0,
    )
    on = _compute_catalog_fov_slice(
        SimpleNamespace(pipeline=pipe_on), 0,
    )

    off_by_id = {o["id"]: o for o in off["objects"]}
    on_by_id = {o["id"]: o for o in on["objects"]}
    shared = set(off_by_id) & set(on_by_id)
    assert shared, "no overlap between flipped/unflipped projections"

    # CRPIX comes from the FITS header — matches frame center by design.
    cx, cy = _FRAME_W / 2.0, _FRAME_H / 2.0
    for cid in shared:
        a = off_by_id[cid]
        b = on_by_id[cid]
        assert b["pixel_x"] == pytest.approx(2 * cx - a["pixel_x"], abs=1.5)
        assert b["pixel_y"] == pytest.approx(2 * cy - a["pixel_y"], abs=1.5)
