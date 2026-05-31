"""Tests for the catalog loader + FOV query (#152)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.renderer import catalog as catalog_mod
from src.renderer.catalog import load_catalog, objects_in_fov


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """Each test starts with a fresh module-level cache."""
    catalog_mod._clear_cache()
    yield
    catalog_mod._clear_cache()


def _write_tiny_catalog(path: Path) -> None:
    """Write a 3-row CSV covering all branches of the loader."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["id", "name", "ra", "dec", "mag", "type", "catalog"],
        )
        w.writeheader()
        w.writerow({
            "id": "M27", "name": "Dumbbell Nebula",
            "ra": "299.901", "dec": "22.721",
            "mag": "7.4", "type": "PN", "catalog": "M",
        })
        w.writerow({
            "id": "NGC 6960", "name": "Veil Nebula",
            "ra": "311.492", "dec": "30.595",
            "mag": "7.0", "type": "SNR", "catalog": "NGC",
        })
        w.writerow({
            "id": "Vega", "name": "Vega",
            "ra": "279.235", "dec": "38.784",
            "mag": "0.03", "type": "Star", "catalog": "IAU",
        })


def test_load_catalog_returns_typed_rows(tmp_path):
    """Loader normalizes ra/dec/mag to floats and keeps the other fields as strings."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    rows = load_catalog(csv_path)
    assert len(rows) == 3
    by_id = {r["id"]: r for r in rows}
    assert by_id["M27"]["catalog"] == "M"
    assert isinstance(by_id["M27"]["ra"], float)
    assert isinstance(by_id["M27"]["dec"], float)
    assert isinstance(by_id["M27"]["mag"], float)
    assert by_id["Vega"]["type"] == "Star"


def test_load_catalog_missing_file_raises_with_repair_hint():
    """Loader's FileNotFoundError must mention ``make build-catalog``."""
    missing = Path("/tmp/does-not-exist-152.csv")
    with pytest.raises(FileNotFoundError) as exc_info:
        load_catalog(missing)
    assert "make build-catalog" in str(exc_info.value)


def test_objects_in_fov_finds_close_objects_and_skips_far_ones(tmp_path):
    """1° radius around M27 includes M27 (separation 0) but excludes Vega."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    rows = load_catalog(csv_path)
    hits = objects_in_fov(
        ra_deg=299.901,
        dec_deg=22.721,
        fov_radius_deg=1.0,
        catalog=rows,
    )
    ids = [h["id"] for h in hits]
    assert "M27" in ids
    assert "Vega" not in ids
    # Each hit carries a non-negative separation_deg sorted ascending.
    seps = [h["separation_deg"] for h in hits]
    assert seps == sorted(seps)
    assert seps[0] == pytest.approx(0.0, abs=1e-6)


def test_objects_in_fov_sorted_by_separation(tmp_path):
    """Closer hits come first regardless of CSV row order."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    rows = load_catalog(csv_path)
    # Center between M27 and NGC 6960 (rough midpoint ~ 305, 26)
    hits = objects_in_fov(
        ra_deg=305.0, dec_deg=26.0, fov_radius_deg=30.0,
        catalog=rows,
    )
    # Expect both M27 and NGC 6960 within 30°, sorted by separation.
    assert {"M27", "NGC 6960"}.issubset({h["id"] for h in hits})
    seps = [h["separation_deg"] for h in hits]
    assert seps == sorted(seps)


def test_load_catalog_caches_singleton(tmp_path, monkeypatch):
    """A second call to load_catalog() (no path arg) returns the same list."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    monkeypatch.setattr(catalog_mod, "_CATALOG_PATH", csv_path)
    first = load_catalog()
    second = load_catalog()
    assert first is second  # cache hit returns the same object


def test_load_catalog_picks_up_tier_files(tmp_path, monkeypatch):
    """When tier add-on CSVs exist next to the base, their rows merge in."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    # Tier 1: Sh2-style row.
    tier1_path = tmp_path / "catalog_tier1.csv"
    with tier1_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["id", "name", "ra", "dec", "mag", "type", "catalog"],
        )
        w.writeheader()
        w.writerow({
            "id": "Sh2-155", "name": "Cave Nebula",
            "ra": "343.117", "dec": "62.617",
            "mag": "0.00", "type": "EmN", "catalog": "Sh2",
        })
    # Tier 3: a UGC galaxy.
    tier3_path = tmp_path / "catalog_tier3.csv"
    with tier3_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["id", "name", "ra", "dec", "mag", "type", "catalog"],
        )
        w.writeheader()
        w.writerow({
            "id": "UGC 12158", "name": "UGC 12158",
            "ra": "340.412", "dec": "4.581",
            "mag": "14.20", "type": "Gxy", "catalog": "UGC",
        })
    monkeypatch.setattr(catalog_mod, "_CATALOG_PATH", csv_path)
    rows = load_catalog()
    ids = {r["id"] for r in rows}
    assert {"M27", "Sh2-155", "UGC 12158"}.issubset(ids)


def test_load_catalog_explicit_path_skips_tier_glob(tmp_path):
    """When the caller passes an explicit CSV path, no tier merging happens."""
    csv_path = tmp_path / "catalog.csv"
    _write_tiny_catalog(csv_path)
    tier1_path = tmp_path / "catalog_tier1.csv"
    with tier1_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["id", "name", "ra", "dec", "mag", "type", "catalog"],
        )
        w.writeheader()
        w.writerow({
            "id": "Sh2-155", "name": "Cave", "ra": "343.117", "dec": "62.617",
            "mag": "0.00", "type": "EmN", "catalog": "Sh2",
        })
    rows = load_catalog(csv_path)
    assert {r["id"] for r in rows} == {"M27", "NGC 6960", "Vega"}
