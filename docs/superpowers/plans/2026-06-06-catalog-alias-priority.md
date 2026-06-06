# Catalog Alias Priority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bei Catalog-Treffern mit mehreren Catalog-Einträgen (z. B. M31 = NGC 224 = UGC 454) den bevorzugten Namen (M > C > NGC > IC > Rest) als Default anbieten, alle Aliase im Hover-Tooltip sichtbar machen, im Label-Dialog per Dropdown auswählbar machen. Text-Feld bleibt editierbar. Catalog-CSV und Project-Schema unverändert.

**Architecture:** Neues Modul `src/renderer/catalog_alias.py` mit `CATALOG_PRIORITY`, `alias_sort_key`, `find_aliases`. `objects_in_fov` dedup-iert pro Position-Cluster (höchst priorisierter überlebt) und enriched `aliases`. JS-Hover-Overlay rendert `aliases.map(.name).join(" / ")`. Label-Dialog liest `aliases` aus dem state-FOV-Cache via `catalog_id`-Lookup und rendert `ui.select` über dem Text-Feld.

**Tech Stack:** Python stdlib (`bisect` für O(log n) Dedup nicht nötig — Naive O(n²) bei ~30 Matches reicht), `ui.select` aus NiceGUI 2.x, existing JS-Overlay-Pipeline.

**Spec:** [docs/superpowers/specs/2026-06-06-catalog-alias-priority-design.md](../specs/2026-06-06-catalog-alias-priority-design.md).

---

## File Map

| Path | Role | Touch |
|---|---|---|
| `src/renderer/catalog_alias.py` | `CATALOG_PRIORITY`, `alias_sort_key`, `find_aliases` | Create |
| `src/renderer/catalog.py` | `objects_in_fov` dedup + alias-enrichment | Modify |
| `src/renderer/ui/render_layout.py` | JS-Overlay (alias join), Label-Dialog (`ui.select`), Cache-Lookup im Click-Handler | Modify (3 spots) |
| `tests/test_catalog_alias.py` | Unit-Tests für Helper + Find | Create |
| `tests/test_catalog.py` | Integrationstests für `objects_in_fov`-Enrichment | Modify (+2 tests) |

---

### Task 1: catalog_alias Modul + Unit-Tests

**Files:**
- Create: `src/renderer/catalog_alias.py`
- Create: `tests/test_catalog_alias.py`

- [ ] **Step 1.1: Failing tests schreiben** (TDD)

`tests/test_catalog_alias.py`:

```python
"""Tests for catalog alias priority + co-location resolution."""

from __future__ import annotations

import pytest

from src.renderer.catalog_alias import (
    CATALOG_PRIORITY, alias_sort_key, find_aliases,
)


def _row(catalog: str, name: str, ra: float, dec: float, **extra) -> dict:
    return {"id": f"{catalog}-{name}", "name": name, "ra": ra, "dec": dec,
            "mag": extra.get("mag", 9.0), "type": "G", "catalog": catalog}


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
    # M32 is ~24 arcmin (1440 arcsec) away — clearly out of 5 arcsec tol
    neighbor = _row("M", "M32", 10.674, 40.865)
    catalog = [target, neighbor]
    aliases = find_aliases(target, catalog, tol_arcsec=5.0)
    assert aliases == [target]
```

Run: `PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_catalog_alias.py -q` — alle 5 müssen fail-en (Modul fehlt).

- [ ] **Step 1.2: Modul implementieren**

`src/renderer/catalog_alias.py`:

```python
"""Catalog alias priority + co-location resolution.

When multiple catalog entries share a sky position (e.g. M31 = NGC
224 = UGC 454), pick the popular name. See
docs/superpowers/specs/2026-06-06-catalog-alias-priority-design.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np

CATALOG_PRIORITY: dict[str, int] = {"M": 0, "C": 1, "NGC": 2, "IC": 3}


def alias_sort_key(entry: dict[str, Any]) -> tuple[int, str, float, str]:
    """Stable sort: (priority, catalog_alpha, mag, id).

    Lower tuple sorts first. `catalog_alpha` is only used for the
    'Rest' bucket (priority 4); for the named tiers it's empty so
    M-entries don't shuffle by `catalog` value.
    """
    cat = entry.get("catalog", "")
    prio = CATALOG_PRIORITY.get(cat, 4)
    mag_raw = entry.get("mag")
    try:
        mag = float(mag_raw) if mag_raw is not None else 99.0
    except (TypeError, ValueError):
        mag = 99.0
    return (prio, cat if prio == 4 else "", mag, str(entry.get("id", "")))


def find_aliases(
    target: dict[str, Any],
    catalog: list[dict[str, Any]],
    tol_arcsec: float = 5.0,
) -> list[dict[str, Any]]:
    """Return catalog entries co-located with target (within tol),
    including target itself, priority-sorted."""
    tol_deg = tol_arcsec / 3600.0
    t_ra, t_dec = target["ra"], target["dec"]
    cos_dec = np.cos(np.radians(t_dec))
    matches = [
        e for e in catalog
        if abs(e["dec"] - t_dec) <= tol_deg
        and abs((e["ra"] - t_ra) * cos_dec) <= tol_deg
    ]
    return sorted(matches, key=alias_sort_key)
```

- [ ] **Step 1.3: Tests grün**

`PYTHON_GIL=0 .venv/bin/python -m pytest tests/test_catalog_alias.py -q` — 5/5 pass.

---

### Task 2: `objects_in_fov`-Enrichment + Integrationstests

**Files:**
- Modify: `src/renderer/catalog.py`
- Modify: `tests/test_catalog.py`

- [ ] **Step 2.1: Failing tests in `tests/test_catalog.py` ergänzen**

```python
def test_objects_in_fov_dedup_keeps_highest_priority_only():
    """A position-cluster of M + NGC + UGC returns ONLY the M entry
    at top level; the others appear in M's aliases list."""
    from src.renderer.catalog import objects_in_fov
    m = {"id": "M31", "name": "M31", "ra": 10.685, "dec": 41.269,
         "mag": 3.4, "type": "G", "catalog": "M"}
    ngc = {"id": "NGC224", "name": "NGC 224", "ra": 10.685, "dec": 41.269,
           "mag": 3.4, "type": "G", "catalog": "NGC"}
    ugc = {"id": "UGC454", "name": "UGC 454", "ra": 10.685, "dec": 41.269,
           "mag": 3.4, "type": "G", "catalog": "UGC"}
    catalog = [ugc, ngc, m]
    result = objects_in_fov(10.685, 41.269, 0.5, catalog=catalog)
    assert len(result) == 1
    assert result[0]["catalog"] == "M"
    assert [a["catalog"] for a in result[0]["aliases"]] == ["M", "NGC", "UGC"]


def test_objects_in_fov_single_entry_self_alias():
    """An isolated catalog entry has aliases == [self]."""
    from src.renderer.catalog import objects_in_fov
    obj = {"id": "X", "name": "X", "ra": 0.0, "dec": 0.0,
           "mag": 9.0, "type": "G", "catalog": "NGC"}
    result = objects_in_fov(0.0, 0.0, 0.5, catalog=[obj])
    assert len(result) == 1
    assert len(result[0]["aliases"]) == 1
    assert result[0]["aliases"][0]["id"] == "X"
```

Run targeted: tests fail (no `aliases` field).

- [ ] **Step 2.2: `objects_in_fov` erweitern**

In `src/renderer/catalog.py`, am Ende von `objects_in_fov` (nach dem `for row, s in zip(...)`-Loop), ein Post-Processing-Block. Vor dem `return result`:

```python
from src.renderer.catalog_alias import alias_sort_key, find_aliases

# Cluster-Dedup + Alias-Enrichment:
# - Group matched_sorted into position-clusters (5 arcsec tolerance).
# - Keep the highest-priority entry per cluster as the top-level result.
# - Attach the full cluster (priority-sorted) as `aliases` on the survivor.
deduped: list[dict[str, Any]] = []
seen_cluster_idxs: set[int] = set()
# Build a parallel list of "is in some cluster's group" markers so we
# can iterate the sep-sorted result while keeping the closest-first
# top-level order.
for i, row in enumerate(result):
    if i in seen_cluster_idxs:
        continue
    cluster = find_aliases(row, result, tol_arcsec=5.0)
    # Match cluster members back to their index in `result` (by id):
    cluster_ids = {c["id"] for c in cluster}
    member_idxs = {
        j for j, r in enumerate(result) if r["id"] in cluster_ids
    }
    seen_cluster_idxs |= member_idxs
    survivor = dict(cluster[0])  # priority-winner
    # Preserve separation_deg from the original matched-entry of the
    # survivor (search by id in result):
    for r in result:
        if r["id"] == survivor["id"]:
            survivor["separation_deg"] = r["separation_deg"]
            break
    survivor["aliases"] = [
        {k: a[k] for k in ("id", "name", "ra", "dec", "mag", "type", "catalog")
         if k in a}
        for a in cluster
    ]
    deduped.append(survivor)
return deduped
```

(Implementer darf eleganter umsetzen; entscheidend ist die Semantik: ein Top-Level-Eintrag pro Cluster, `aliases[0]` ≡ self.)

- [ ] **Step 2.3: Tests grün, kein Regress**

```bash
PYTHON_GIL=0 .venv/bin/python -m pytest -q --ignore=tests/test_main.py
```

Baseline ist nach #157 bei 291. Neu: +5 aus Task 1, +2 hier → 298. Existierende `_compute_catalog_fov_slice`-Tests dürfen NICHT scheitern — sie rufen `objects_in_fov` mit monkey-patches und prüfen Pixel-Positionen; die Dedup ändert nur Mehrfachtreffer im Cluster, was die Tests in #157 nicht nutzen.

---

### Task 3: Hover-Tooltip (JS-Overlay)

**Files:**
- Modify: `src/renderer/ui/render_layout.py` (`_catalog_overlay_script`, ~line 2415)

- [ ] **Step 3.1:** In `_catalog_overlay_script`, wo aktuell `hit.obj.name` für die Tooltip-Anzeige verwendet wird, ersetzen durch:

```javascript
const aliases = (hit.obj.aliases || [hit.obj]);
const display = aliases.map(a => a.name).join(" / ");
```

`display` ist dann der String für den Tooltip-Inhalt. Fallback auf `[hit.obj]` falls Cache noch alt ist (kein `aliases`-Feld). Identisches Pattern bei beiden Vorkommen (line 2415 und 2423).

- [ ] **Step 3.2: Browser-Smoke**

App starten, Frame öffnen, Catalog-Modifier aktivieren, über ein M-Objekt hovern → Tooltip zeigt "M31 / NGC 224 / UGC 454". Über ein Single-Entry-Objekt → unverändert (z.B. "M42").

---

### Task 4: Label-Dialog Listbox

**Files:**
- Modify: `src/renderer/ui/render_layout.py` (`_handle_label_placement` ~line 2580, `_open_create_dialog` ~line 2597)

- [ ] **Step 4.1: Cache-Lookup im Click-Handler**

Im Handler, wo aus dem `args` der `catalog_meta` gebaut wird (~line 2576-2581), nach dem `catalog_meta`-Build:

```python
if catalog_meta is not None:
    fov_slice = state.catalog_fov_cache.get(state.selected_frame)
    if fov_slice:
        for obj in fov_slice.get("objects", []):
            if obj.get("id") == catalog_meta["catalog_id"]:
                catalog_meta["aliases"] = obj.get("aliases", [])
                break
    catalog_meta.setdefault("aliases", [])
```

- [ ] **Step 4.2: `ui.select` im Dialog**

In `_open_create_dialog`, vor dem Text-Input, wenn `catalog_meta and len(catalog_meta.get("aliases", [])) > 1`:

```python
aliases = catalog_meta["aliases"]
options = {a["id"]: a["name"] for a in aliases}
default_text = aliases[0]["name"]
alias_select = ui.select(
    options=options,
    value=aliases[0]["id"],
    label="Catalog-Eintrag",
).classes("w-full")
# Bind: changing the select updates the text input default.
alias_select.on_value_change(
    lambda e: setattr(text_input, "value", options[e.value])
)
```

(Der exakte Code-Spot hängt davon ab, wo `text_input` instantiiert wird — Implementer matched die existing Dialog-Struktur an.)

Wenn nur 1 Alias (= self): kein Select-Widget, Verhalten exakt wie heute.

- [ ] **Step 4.3: Browser-Smoke**

App starten, Frame mit M-Objekt im FOV öffnen, Catalog-Modifier an, klicken auf M31-Position → Dialog öffnet, Listbox zeigt M31/NGC 224/UGC 454 (M31 vorausgewählt), Text-Feld zeigt "M31". Auf NGC 224 umstellen → Text-Feld wird "NGC 224". Manuell zu "M31 (Andromeda)" editieren → bleibt erhalten beim Speichern.

---

### Task 5: Doku-Pointer

- [ ] **Step 5.1:** Im Issue (das in der Bezugsanalyse referenziert ist) den `Closes #...` Footer für den finalen Commit vermerken. Spec/Plan sind schon committed.

---

## Akzeptanzkriterien

- [ ] `catalog_alias.py` deckt M > C > NGC > IC > Rest sortiert ab, Tie-Break per `mag`, dann `id`.
- [ ] `objects_in_fov`-Output ist dedupliziert: ein Top-Level-Eintrag pro Position-Cluster, `aliases[0]` == self.
- [ ] Hover-Tooltip zeigt alle Aliase priorisiert ("M31 / NGC 224 / UGC 454").
- [ ] Label-Dialog: bei >1 Alias erscheint Dropdown, Default = highest priority, Wechsel updated Text-Feld, manuelle Edits bleiben.
- [ ] Single-Entry-Objekte zeigen unverändertes UI/UX.
- [ ] Keine existierenden Tests scheitern; Suite nach Implementation bei 298.

## Out of Scope

- Konfigurierbare Priority via UI/Setting.
- Spatial-Index für `find_aliases` (Performance-Optimierung).
- Hover-Tooltip-Truncation bei vielen Aliasen (max-N + "+N more").
