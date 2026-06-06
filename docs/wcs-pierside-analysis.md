# Catalog Overlay vs. FITS WCS — Pierside-Bug Analyse

Datum: 2026-06-06
Kontext: Issue #152 (Catalog-Overlay), Folgeproblem nach leo4-Frames

## TL;DR

Bei manchen FITS-Frames sind die Catalog-Label-Positionen
**point-mirrored** durch die Frame-Mitte. Erste Hypothese war
"PIERSIDE=EAST braucht 180°-Flip" — **das stimmt nicht**. Die Daten
zeigen drei verschiedene Fälle für PIERSIDE=EAST: leo4 braucht Flip,
orion nicht. Aus dem Header allein lässt sich nicht zuverlässig
entscheiden, ob ein Flip nötig ist.

## Beobachtungen

Drei Frames stichpunktartig durchgemessen, dazu Catalog-Galaxien per
WCS auf das Pixelraster projiziert und mit Sterndetections sowie
sichtbarem Bildinhalt (M42, Crescent, M95/96/105) visuell verglichen.

| Frame             | PIERSIDE | CROTA2     | Richtige Variante |
| ----------------- | -------- | ---------- | ----------------- |
| `leo4_0005`       | EAST     | 34.99°     | **flip**          |
| `cygnus_0057`     | WEST     | 14.22°     | raw               |
| `orion1_0001`     | EAST     | **214.76°**| raw               |

Über alle 40 vorhandenen Projects ist die CROTA2 pro Setup konsistent
(cygnus-Setup ≈ 14°, leo/orion-Setup ≈ 34°). Innerhalb eines Projects
sind alle Frames PIERSIDE-homogen — kein gemischtes Project gefunden.

`orion` hat PIERSIDE=EAST aber CROTA2=214.76° = 34.76° + **180°**.
Ekos hat dort den Pierside-Flip korrekt in CROTA2 mitgeschrieben.
`leo4` hat PIERSIDE=EAST und CROTA2=34.99° — als wäre das Teleskop
auf der WEST-Seite. Ekos hat hier den Flip *nicht* in CROTA2
gespiegelt; vermutlich ein Schreibbug beim Pierside-Wechsel oder eine
Eigenheit des Mount-Treibers.

## Warum die Pierside-Heuristik nicht funktioniert

Ein Helper "negate CD wenn PIERSIDE != normal_side" hätte:
- `leo4`  (EAST, CROTA2=35°)   → flipped (richtig)
- `orion` (EAST, CROTA2=215°)  → flipped (FALSCH — doppelter Flip)
- `cygnus`(WEST, CROTA2=14°)   → no-op  (richtig)

Der Indikator "180°-Flip nötig" liegt nicht im Header. Ohne
Plate-Solving oder visuellen Vergleich kann der Renderer nicht
wissen, welcher Fall vorliegt.

## Methode

Für jedes Frame wurde:
1. Aus dem FITS-Bild das Stretch-Preview gerendert.
2. Alle Catalog-Objekte im FOV doppelt projiziert: einmal mit
   `WCS(header)` direkt, einmal mit `WCS(header).wcs.pc = -pc` (=
   180°-Rotation um CRPIX).
3. Beide Marker-Sets im selben Bild gerendert (Rot = raw, Grün = flip)
   und mit dem tatsächlichen Bildinhalt verglichen.

Quantitatives Match-Distance-Maß (nächste Sterndetection pro
Catalog-Position) war bei dichten Galaxien-Clustern wie leo4 nicht
trennscharf (raw 94 px vs. flip 88 px Median). Visuell dagegen
sofort eindeutig — die Galaxien waren in beiden Fällen klar
identifizierbar.

## Konsequenz

Saubere Lösung wäre Plate-Solving (`astrometry.net`/`solve-field`)
pro Frame. Bis dahin: **manueller per-Project-Toggle** "WCS um 180°
drehen". Default off. User entscheidet visuell, ob die Catalog-Labels
am richtigen Platz sind. Wegen der Setup-Konsistenz innerhalb eines
Projects reicht ein Flag pro Project — pro Frame ist nicht nötig.

Details + Datentabellen siehe Git-Historie dieses Branches und die
Spec-Datei [docs/superpowers/specs/2026-06-06-wcs-180-flip-design.md](superpowers/specs/2026-06-06-wcs-180-flip-design.md).
