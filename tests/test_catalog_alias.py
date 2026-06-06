"""Tests for catalog alias priority + co-location resolution."""

from __future__ import annotations

import types

import pytest

from src.renderer.catalog_alias import (
    CATALOG_PRIORITY, alias_sort_key, display_label, find_aliases,
)


def _row(catalog: str, name: str, ra: float, dec: float, **extra) -> dict:
    return {
        "id": f"{catalog}-{name}", "name": name, "ra": ra, "dec": dec,
        "mag": extra.get("mag", 9.0), "type": "G", "catalog": catalog,
    }


def test_alias_sort_key_priority_order():
    keys = [alias_sort_key(_row(c, "x", 0, 0)) for c in
            ["IC", "M", "UGC", "NGC", "C"]]
    sorted_keys = sorted(range(len(keys)), key=lambda i: keys[i])
    # Expected order in input: M(1), C(4), NGC(3), IC(0), UGC(2)
    assert sorted_keys == [1, 4, 3, 0, 2]


def test_alias_sort_key_tie_break_by_mag():
    a = alias_sort_key(_row("NGC", "a", 0, 0, mag=8.0))
    b = alias_sort_key(_row("NGC", "b", 0, 0, mag=9.0))
    assert a < b  # heller zuerst


def test_find_aliases_returns_target_alone_if_isolated():
    target = _row("NGC", "lonely", 10.0, 20.0)
    catalog = [target, _row("NGC", "far", 30.0, 40.0)]
    aliases = find_aliases(target, catalog)
    assert aliases == [target]


def test_find_aliases_co_located_cluster_priority_sorted():
    m = _row("M", "M31", 10.685, 41.269)
    ngc = _row("NGC", "NGC 224", 10.685, 41.269)
    ugc = _row("UGC", "UGC 454", 10.685, 41.269)
    catalog = [ugc, ngc, m]
    aliases = find_aliases(ugc, catalog)
    assert [a["catalog"] for a in aliases] == ["M", "NGC", "UGC"]


def test_find_aliases_tolerance_excludes_neighbors():
    target = _row("M", "M31", 10.685, 41.269)
    # M32 is ~24 arcmin away — well outside 5 arcsec tolerance.
    neighbor = _row("M", "M32", 10.674, 40.865)
    catalog = [target, neighbor]
    aliases = find_aliases(target, catalog, tol_arcsec=5.0)
    assert aliases == [target]


def test_attach_aliases_to_catalog_meta_from_fov_cache():
    """Click-handler helper copies aliases from the FOV cache onto catalog_meta."""
    from src.renderer.ui.render_layout import _attach_aliases_to_catalog_meta

    aliases = [
        {"id": "M-M31", "name": "M31", "catalog": "M",
         "ra": 10.685, "dec": 41.269, "mag": 3.4, "type": "G"},
        {"id": "NGC-NGC 224", "name": "NGC 224", "catalog": "NGC",
         "ra": 10.685, "dec": 41.269, "mag": 3.4, "type": "G"},
        {"id": "UGC-UGC 454", "name": "UGC 454", "catalog": "UGC",
         "ra": 10.685, "dec": 41.269, "mag": 3.4, "type": "G"},
    ]
    state = types.SimpleNamespace(
        selected_frame=2,
        catalog_fov_cache={2: {"objects": [
            {"id": "M-M31", "name": "M31", "ra": 10.685, "dec": 41.269,
             "aliases": aliases},
        ]}},
    )
    meta = {
        "catalog_id": "M-M31", "catalog_name": "M31",
        "ra": 10.685, "dec": 41.269,
    }
    _attach_aliases_to_catalog_meta(state, meta)
    assert meta["aliases"] == aliases


def test_attach_aliases_no_cache_entry_falls_back_to_empty():
    from src.renderer.ui.render_layout import _attach_aliases_to_catalog_meta

    state = types.SimpleNamespace(selected_frame=0, catalog_fov_cache={})
    meta = {"catalog_id": "M-M31", "catalog_name": "M31",
            "ra": 10.0, "dec": 41.0}
    _attach_aliases_to_catalog_meta(state, meta)
    assert meta["aliases"] == []


def test_attach_aliases_noop_when_meta_none():
    from src.renderer.ui.render_layout import _attach_aliases_to_catalog_meta

    state = types.SimpleNamespace(selected_frame=0, catalog_fov_cache={})
    # Must not raise.
    _attach_aliases_to_catalog_meta(state, None)


def test_display_label_uses_id_for_messier():
    """Messier id (e.g. 'M65') is the popular name — not the NGC alias."""
    m65 = {"id": "M65", "name": "NGC 3623", "catalog": "M"}
    assert display_label(m65) == "M65"


def test_display_label_uses_id_for_caldwell():
    """Caldwell id (e.g. 'C1') wins over the NGC name in the row."""
    c1 = {"id": "C1", "name": "NGC 188", "catalog": "C"}
    assert display_label(c1) == "C1"


def test_display_label_uses_name_for_ngc():
    """NGC entries: name == id typically — pick name."""
    ngc = {"id": "NGC 3623", "name": "NGC 3623", "catalog": "NGC"}
    assert display_label(ngc) == "NGC 3623"


def test_display_label_uses_id_when_name_empty_for_messier():
    m = {"id": "M99", "name": "", "catalog": "M"}
    assert display_label(m) == "M99"


def test_display_label_falls_back_for_unknown_catalog():
    obj = {"id": "X-1", "name": "X-1", "catalog": "UGC"}
    assert display_label(obj) == "X-1"
