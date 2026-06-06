# Per-Project WCS 180°-Flip Toggle — Design

Epic: [#121 Usability](https://github.com/zworkb/AstroNightcrawler/issues/121)
Bezugsanalyse: [docs/wcs-pierside-analysis.md](../../wcs-pierside-analysis.md)
Status: Design, ready for implementation plan

## 1. Goal

Ein boolescher Toggle pro Project, mit dem der User den FITS-WCS für
die Catalog-Overlay-Projektion um 180° um die Frame-Mitte (CRPIX)
drehen kann. Default aus. Persistiert in `manifest.json`. User
entscheidet anhand des Bildes visuell, ob er den Flip braucht.

## 2. Warum so

Aus der Analyse ([siehe Doku](../../wcs-pierside-analysis.md)):
- Manche Frames (z. B. leo4) haben einen FITS-WCS, der in der
  Pierside-Rotation 180° verdreht ist; Catalog-Labels landen dann
  point-mirrored im Bild.
- Die Ursache liegt im Capture-Tooling (Ekos/Mount-Treiber-Bug),
  nicht im Renderer. Aus dem Header allein lässt sich nicht
  zuverlässig herleiten, ob ein Flip nötig ist (siehe orion-Gegenbeispiel).
- Plate-Solving wäre die saubere Auflösung, ist aber kein Setup,
  das wir heute haben — separates Issue.

Pro Project, nicht pro Frame: Innerhalb eines Projects ist die
Aufnahme-Konfiguration (Mount-Seite + CROTA) bei allen vorhandenen
Sessions konsistent — kein Mix gefunden. Pro Project hält die UI
einfach und passt zum Pattern von `north_angle_deg`.

Pro Project, nicht pro Capture-Setup: Der User verwendet
unterschiedliche Teleskope/Mounts. Ein globaler Setting würde sich
zwischen Projects gegenseitig überschreiben.

## 3. UI

Im Labels-Panel-Header (wo schon die `Catalog`- und
`Leader`-Checkboxen sitzen) eine dritte Checkbox:

```
[x] Catalog    [ ] Leader    [ ] Flip 180°    [ Add label ]
```

Tooltip: *"WCS um 180° drehen — bei verkehrt herum platzierten
Catalog-Labels (Pierside-Bug im Capture-Tool)."*

`on_change` →
1. `project.wcs_flip_180 = e.value`
2. `_persist_project(state)` (existing pattern für andere
   project-level Mutationen)
3. Re-render des aktuellen Catalog-Overlays (Frame neu projizieren).

Kein modaler Bestätigungs-Dialog. Visuelle Folge ist sofort sichtbar
— der User sieht, ob es passt.

## 4. Datenmodell

`Project.wcs_flip_180: bool = False`, Pydantic-Field mit
`description="Catalog-Overlay 180° um Frame-Mitte drehen — kompensiert
Pierside-Bug in manchen Capture-Tools (siehe Analyse-Doku)."`. Default
False sorgt für rückwärtskompatibles Laden alter Manifests.

Keine Schema-Version-Erhöhung nötig — Pydantic ergänzt fehlende
Felder transparent.

## 5. Renderer-Integration

Eine Helper-Funktion in `src/renderer/wcs.py`:

```python
def apply_wcs_flip(wcs: WCS) -> WCS:
    """Negate the CD/PC matrix — 180° rotation around CRPIX."""
    if wcs.wcs.has_cd():
        wcs.wcs.cd = -wcs.wcs.cd
    else:
        wcs.wcs.pc = -wcs.wcs.get_pc()
    return wcs
```

In `_prepare_catalog_overlay_data` (`render_layout.py`) nach der
WCS-Konstruktion (egal ob aus Header oder synthetic über `build_wcs`)
einmalig:

```python
if project.wcs_flip_180:
    wcs = apply_wcs_flip(wcs)
```

Wirkt damit auch im synthetic-Fallback (header-loses Frame), wo der
Bug zwar nicht direkt auftreten *kann*, aber Konsistenz im
Codeverhalten erhalten bleibt. Kein zweites Vorkommen — der Helper
ist die einzige Quelle der Wahrheit.

## 6. Was sich NICHT ändert

- Render-Pipeline für Filmstrip/Video → unverändert (Catalog-Labels
  werden über `labels.catalog_to_ref_pixel` projiziert, das den
  gleichen WCS-Build-Pfad nutzt → erbt den Flip automatisch).
- Stretch, Bayer-Demosaic, Frame-Resize → unberührt.
- `north_angle_deg` → unverändert; orthogonal zum Flip.
- Andere Projects → Default `False` ändert nichts an existierenden
  Manifests.

## 7. Tests

- `test_apply_wcs_flip_pc`: Synthetic WCS mit PC-Matrix → `apply_wcs_flip`
  negiert PC; ein Pixel (x, y) → world → flipped pixel ist
  punktsymmetrisch zu CRPIX.
- `test_apply_wcs_flip_cd`: WCS mit CD-Matrix (statt PC) → CD wird
  negiert; gleiche Symmetrie-Eigenschaft.
- `test_project_default_wcs_flip_180_false`: Neues Project hat
  `wcs_flip_180 == False`.
- `test_project_loads_old_manifest_without_field`: Manifest aus
  `tests/fixtures/` ohne `wcs_flip_180` lädt mit `False`.
- `test_catalog_overlay_respects_wcs_flip_180`: mit aktivem Flag
  landet ein bekanntes RA/Dec auf dem 180°-gespiegelten Pixel
  (Integrations-Test gegen `_prepare_catalog_overlay_data`).

UI-Persistierung wird durch das existierende `_persist_project`-Pattern
abgedeckt; kein neuer UI-Test nötig.

## 8. Risiken / offene Punkte

- **Doppelflip-Verwirrung**: Wenn der User den Flag versehentlich auf
  einem orion-artigen Project setzt, wandern die Labels weg. Sofort
  visuell sichtbar, sofort umschaltbar — geringer Schaden.
- **Sich ändernde Setups**: Würde Ekos den Bug pro Frame triggern,
  reicht per-Project nicht mehr. Aktuell keine Daten dafür; wenn es
  auftaucht, eskaliert das Feld in ein per-Frame-Override (oder das
  Plate-Solving-Issue zieht).
- **Plate-Solving als Nachfolger**: Dieses Feature ist explizit
  Übergangslösung. Wenn Plate-Solving integriert wird, kann der Flag
  zur Override-Semantik werden ("erzwinge Flip trotz Plate-Solve") oder
  ersatzlos verschwinden — Migration ist trivial (default False).
