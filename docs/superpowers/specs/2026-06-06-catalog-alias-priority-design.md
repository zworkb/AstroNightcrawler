# Catalog Alias Priority — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Status: Design, ready for implementation plan

## 1. Goal

Wenn ein Himmelsobjekt mehrere Catalog-Einträge teilt (z. B. M31 =
NGC 224 = UGC 454), soll der Renderer dem User die Alternativen
sichtbar anbieten und den populären Namen bevorzugen — statt willkürlich
einen Eintrag aus der CSV-Iterationsreihenfolge zu zeigen.

Zwei Surfaces betroffen:
- **Hover-Tooltip im Live-Preview**: zeigt alle Aliase nebeneinander
  ("M31 / NGC 224 / UGC 454"), Reihenfolge nach Priorität.
- **Label-Dialog (Catalog-Snap)**: Listbox mit allen Aliasen,
  Default-Auswahl = bevorzugter Eintrag, User kann umwählen,
  Text-Feld bleibt frei editierbar.

## 2. Warum so

Im Catalog gibt es ~510 Positions-Cluster mit gemischten
Catalog-Werten (M+NGC, NGC+UGC, IC+NGC, …). Heute trifft der
nächste-Treffer-Algorithmus den ersten Eintrag in CSV-Reihenfolge —
das ist für den User Glücksspiel ("warum heißt M31 plötzlich UGC
454?"). Eine Catalog-Dedup beim Laden wäre eine Lösung, verliert
aber Information; der User wollte explizit alle Aliase sichtbar
behalten.

Pro-User-Auswahl statt automatischer Substitution: der User behält
Kontrolle. "M31" ist die typische Wahl, manchmal aber will man "NGC
224" oder den Cross-Reference-Hinweis "M31 (NGC 224)" — nicht durch
eine Hard-Coded-Priorität bestimmen.

## 3. Datenmodell

**Keine Änderung am Catalog-CSV-Schema**, keine Project-Schema-Änderung.

Ein neues Modul `src/renderer/catalog_alias.py`:

```python
CATALOG_PRIORITY: dict[str, int] = {"M": 0, "C": 1, "NGC": 2, "IC": 3}
# "Rest" → 4 + alphabetic (stabile Reihenfolge unter den niedrig-priorisierten)

def alias_sort_key(entry: dict) -> tuple[int, str, float, str]:
    """Stable key: (priority, catalog_alpha, mag, id)."""
    cat = entry.get("catalog", "")
    prio = CATALOG_PRIORITY.get(cat, 4)
    return (prio, cat if prio == 4 else "", float(entry.get("mag") or 99.0), entry.get("id", ""))


def find_aliases(target: dict, catalog: list[dict], tol_arcsec: float = 5.0) -> list[dict]:
    """Return catalog entries co-located with `target` (within tol),
    including target itself, priority-sorted."""
```

`objects_in_fov` wird zweistufig erweitert:

1. **Cluster-Dedup vor Return**: Treffer werden nach
   Position-Tolerance (~5 arcsec) gruppiert; **pro Cluster überlebt
   nur der höchst-priorisierte Eintrag** als Top-Level-Result. Damit
   tauchen M31 und NGC 224 (gleiche Position) nicht beide als
   eigenständige Treffer im JS auf — der M-Eintrag schluckt die
   anderen.
2. **Alias-Enrichment**: Der überlebende Top-Level-Eintrag bekommt
   `aliases: list[dict]` angehängt — eine priority-sortierte Liste
   ALLER Catalog-Einträge im Cluster (inkl. sich selbst). Wenn der
   Cluster nur ein Element hat, gilt `aliases == [self]`.

`aliases[0]` ist immer identisch mit dem Top-Level-Eintrag.
`aliases[i]` enthält die gleichen Felder wie der Catalog-Row (`id,
name, ra, dec, mag, type, catalog`); `separation_deg` wird NICHT auf
die Aliase kopiert (irrelevant, Frontend sortiert nur die Top-Level).

## 4. Hover-Tooltip

Im JS-Overlay-Script (`_catalog_overlay_script` in
`render_layout.py`): wo aktuell `hit.obj.name` gerendert wird, ersetzt
durch:

```javascript
const names = (hit.obj.aliases || [hit.obj]).map(a => a.name);
const display = names.join(" / ");
```

Beispiel: `"M31 / NGC 224 / UGC 454"`. Backward-Compat-Fallback auf
`hit.obj.name` falls die `aliases`-Property fehlt (alte Slices im
Cache).

Layout: Tooltip-Box wächst horizontal — keine Truncation in V1
(Out-of-Scope: max-3-mit-overflow ist Folgearbeit).

## 5. Label-Dialog

In `_open_create_dialog` (Python): wenn `catalog_meta` vorhanden UND
`catalog_meta["aliases"]` mehrere Einträge hat, ein neues
`ui.select`-Widget über das Text-Feld:

```
[ Alias: ▾ M31                ]
[ Text:  M31                  ]
[ ... bestehende Felder ...   ]
```

Verhalten:
- Optionen = `aliases` als `{value: id, label: name}`-Paare.
- Pre-Selection = `aliases[0]` (Highest Priority).
- `on_change` → `text_input.value = selected_alias.name`.
- User kann das Text-Feld danach beliebig editieren ("M31 (Andromeda)"
  o. ä.). Die Edit überschreibt die `on_change`-Bindung NICHT —
  das Text-Feld ist die Source of Truth, der Select steuert nur den
  Default.

Wenn nur 1 Alias existiert (single-entry): kein Dropdown, Optik
unverändert.

**Wie `aliases` ins Python kommen**: Der JS-Overlay-Click-Event muss
NICHT die ganze Alias-Liste mitsenden (würde JSON-Bloat erzeugen).
Stattdessen sendet er weiterhin nur `catalog_id` + `catalog_name` wie
heute; der Python-Handler schlägt im **state-seitigen FOV-Cache**
(`state.catalog_fov_cache[frame_idx]`) anhand `catalog_id` nach und
zieht das `aliases`-Feld des Eintrags. Der Cache wird in
`_compute_catalog_fov_slice` ohnehin schon befüllt — kein zusätzlicher
Roundtrip nötig.

`catalog_meta` (Python-seitig im Dialog) wird also um
`aliases: list[dict]` erweitert, befüllt aus dem Lookup.

Persistierung: keine. Der gewählte Name landet als plain `label.text`
im Project — exakt wie heute. Project-Schema bleibt unverändert.

## 6. Was sich NICHT ändert

- Catalog-CSV-Build (`scripts/build_catalog.py`) unverändert.
- Project-Schema unverändert.
- Burn-In Label-Rendering im Video-Pfad — Labels sind dort schon
  fester Text.
- Bestehende Single-Match-Catalog-Objekte (~95 % der Treffer): UX
  unverändert (Listbox erscheint nicht).

## 7. Tests

**`tests/test_catalog_alias.py` (neu):**
- `test_alias_sort_key_priority_order`: M < C < NGC < IC < Rest.
- `test_alias_sort_key_tie_break_by_mag`: bei gleicher Priorität
  hellster zuerst.
- `test_find_aliases_returns_target_alone_if_isolated`: keine
  Co-Lokalisierten → `[target]`.
- `test_find_aliases_co_located_cluster_priority_sorted`: M + NGC +
  UGC am selben Ort → `[M, NGC, UGC]`.
- `test_find_aliases_tolerance_excludes_neighbors`: Nachbarobjekt
  > 5 arcsec wird NICHT als Alias erkannt.

**`tests/test_catalog.py` Ergänzung:**
- `test_objects_in_fov_enriches_aliases`: gemischter Fixture-Cluster
  → jeder Result-Eintrag hat `aliases`-Liste, korrekt sortiert.
- `test_objects_in_fov_single_entry_self_alias`: isoliertes Objekt
  → `aliases == [self]`.

**UI**: kein direkter Widget-Test (NiceGUI-Wiring wie beim
Flip-Toggle nur Browser-Smoke). Die Default-Select-Logik lebt als
1-Liner in einer Helper-Funktion und ist über die Catalog-Alias-Tests
abgedeckt.

## 8. Risiken / offene Punkte

- **Tolerance-Wert (5 arcsec)**: Catalog enthält teils auf 4
  Dezimalstellen gerundete Positionen — 5 arcsec ist ~0.0014°, klar
  über der Rundungsungenauigkeit (~0.4 arcsec) und unter typischen
  Galaxien-Pair-Trennungen (M31/M32 ≈ 24 arcmin). Sollte stabil sein,
  müsste aber an einem realen Cluster verifiziert werden (Test).
- **Performance**: `find_aliases` ist O(n) pro Catalog-Match — bei
  ~30 Matches im FOV und 14k Einträgen sind das 420k Vergleiche pro
  FOV-Slice. Vermutlich vernachlässigbar (Slice ist gecacht), bei
  Performance-Symptom: Spatial-Index oder Cluster-Pre-Compute beim
  Catalog-Load.
- **JS-Caching alter FOV-Slices ohne `aliases`-Feld**: Cache-Key ist
  `frame_idx + catalog_version` — wenn der User upgrade-t und ein
  alter Cache durchschlägt, fällt der Hover auf `hit.obj.name`
  zurück. Selbstheilung beim nächsten Refresh.
- **Listbox bei 10+ Aliasen**: theoretisch möglich (manche FK6-Sterne
  haben viele Catalog-IDs), aber wenn der Catalog im Repo das nicht
  hergibt, bleibt es bei 2-4 Aliasen. NiceGUI `ui.select` scrollt
  ohnehin.
