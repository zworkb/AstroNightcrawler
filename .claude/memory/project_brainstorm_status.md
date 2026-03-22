---
name: Brainstorm status - Sequence Planner
description: Current brainstorming results for the telescope sequence planner project - all key decisions captured
type: project
---

## Projektkonzept

Software zur Planung von Aufnahmesequenzen mit einem ferngesteuerten Teleskop (INDI/EKOS). Man zeichnet einen Pfad auf einer Sternkarte, das Teleskop fährt Punkt für Punkt ab und macht Aufnahmen. Aus den Einzelaufnahmen wird ein Film (24fps, echte Daten, keine Interpolation).

## Entschiedene Punkte

### Architektur
- **Zwei separate Apps**: Planner/Capture auf RPi/StellarMate, Rendering auf leistungsstarkem Desktop
- **Web-Framework**: NiceGUI für beide Apps
- **Schnittstelle**: Verzeichnis mit FITS-Dateien + JSON-Manifest

### Sternkarte
- **Offline-fähig** (kein Internet bei Außeneinsätzen)
- **KStars-Kataloge** als Datenquelle (`/usr/share/kstars/`)
- **Bis Größenklasse 10**, astronomische Objekte, detailliertere Kataloge zum Nachladen
- **Eigenes Rendering** (Canvas/SVG), da Aladin Lite Internet braucht

### Pfad-Zeichnung
- **Splines** mit editierbaren Kontrollpunkten
- Punkt-für-Punkt klicken UND Freihand-Zeichnen
- **Konfigurierbarer Abstand** zwischen Aufnahmepunkten entlang des Splines
- Aufnahmepunkte in der Planungsansicht sichtbar
- Gleichmäßige Verteilung (MVP), variable Geschwindigkeit später

### Aufnahme-Einstellungen
- **Global pro Pfad**: Belichtungszeit und Anzahl Aufnahmen pro Punkt
- FITS-Header enthalten Aufnahme-Metadaten (RA/DEC, EXPTIME, DATE-OBS, etc.)
- Manifest-Datei enthält Sequenzplan (Pfad, Punktzuordnung, Reihenfolge)

### Teleskop-Steuerung
- **Direkte INDI-Verbindung** (Host/Port konfigurierbar) als Hauptmodus
- **EKOS-Sequenzdatei-Export** als Alternative
- Beides unterstützt

### Aufnahmeprozess
- **Vollautomatisch** und **überwacht** (Live-Fortschritt, Pause/Resume)
- Kein automatisches Wetter-Monitoring
- Unterbrechen und an gleicher Stelle weitermachen möglich

### Film-Rendering
- **24fps aus echten Einzelaufnahmen**, keine Interpolation/Morphing
- **CLI + Web-UI** (NiceGUI)
- Rendering-App bekommt Bildverzeichnis + Manifest
- Möglichst wenig manuelle Eingabe nötig

### Kataloge & Daten
- Stellarium Web Engine's eigene Kataloge (Gaia/Hipparcos via HiPS-Tiles)
- Offline: Subset bis Mag 10 lokal vorhalten
- Kein KStars-Katalog-Import nötig — wenn mehr Daten gebraucht werden, App auf stärkerem Rechner laufen lassen
- AGPL-Lizenz der Engine ist OK — ggf. nachinstallation per Script

### Sternkarte / Kartenbibliothek
- **Stellarium Web Engine** (C → WASM + WebGL)
- Aktiv gepflegt, Gaia-Katalog, HiPS-Surveys
- Hat GeoJSON-Modul mit **Path-Typ für kubische Bézier-Kurven** — ideal für Spline-Pfade
- Einbettung in NiceGUI per Custom Element/iframe
- Offline-fähig durch lokales Hosting der skydata/-Verzeichnisse

### Architektur-Entscheidung
- **Ansatz B gewählt**: NiceGUI + eingebettete Stellarium Web Engine
- Single-User, keine Authentifizierung

### Gesamtarchitektur (genehmigt)
**Planner/Capture App** (RPi/StellarMate oder stärkerer Rechner):
- NiceGUI Server (Python) — Web-Framework, State Management
- Sternkarte: Stellarium Web Engine (WASM) + Spline-Overlay (JS)
- Pfad-Editor: Kontrollpunkte, Aufnahmepunkte, Spline-Parameter
- INDI Client: PyINDI / direktes INDI-Protokoll, Teleskop Goto + Kamera Capture
- EKOS Export: Sequenzdatei-Generator (.esq Format)
- Capture Controller: Sequenz-Ablauf, Pause/Resume, Fortschritts-Tracking, FITS + Manifest

**Rendering App** (leistungsstarker Desktop):
- NiceGUI Server — Web-UI für Import, Vorschau, Rendering-Steuerung
- CLI — Headless Rendering per Kommandozeile
- Video Pipeline: FITS → Bildkonvertierung, optional Stacking, Sequenz → Video (24fps, ffmpeg)

**Datenfluss**:
- Planung → Pfad + Einstellungen als Projektdatei (.json)
- Aufnahme → FITS-Dateien + JSON-Manifest in Ausgabeverzeichnis
- Rendering → Verzeichnis einlesen → FITS→PNG → Video (mp4/avi)

### UI-Layout (genehmigt)
- **Hybrid-Layout (Option C)**: Sternkarte als Hauptbereich, Toolbar oben, einklappbares Bottom Panel
- **Toolbar**: Zeichnen, Bewegen, Punkt hinzufügen, Teilen, Undo/Redo, Speichern/Laden, Aufnahme starten
- **Bottom Panel eingeklappt**: Zusammenfassung (Pfad-Info, Belichtung, Abstand, geschätzte Dauer)
- **Bottom Panel aufgeklappt**: Pfad-Einstellungen, Aufnahmepunkt-Liste mit Koordinaten, INDI-Verbindung
- **Aufnahme-Modus**: Fortschrittsanzeige (Punkt X/Y, Aufnahme, Belichtung, Restzeit), Pause/Abbrechen
- Spline-Pfad: orange Kontrollpunkte mit Handles, blaue Aufnahmepunkte entlang des Pfads

## Offene Punkte
- Manifest-Format im Detail
- Rendering-Pipeline Details (Stacking-Optionen?)
- Rendering-App UI