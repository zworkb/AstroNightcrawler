# Stellarium Web Engine — Build & Setup

Die Sternkarte basiert auf der [Stellarium Web Engine](https://github.com/Stellarium/stellarium-web-engine), einer C-Bibliothek die nach WebAssembly (WASM) kompiliert wird und im Browser läuft.

**Lizenz:** AGPL-3.0 — wird separat installiert, nicht mit der App gebundelt.

## Schnellstart

```bash
# Einmalig: Stellarium bauen (dauert ~10 min beim ersten Mal)
./scripts/build_stellarium.sh

# App starten
sequence-planner
```

Das war's. Das Script erledigt alles automatisch.

## Was das Build-Script macht

`scripts/build_stellarium.sh` führt 4 Schritte aus:

### 1. Emscripten SDK installieren

[Emscripten](https://emscripten.org/) ist ein C/C++ → WebAssembly Compiler. Das Script:
- Klont das Emscripten SDK nach `~/emsdk` (falls nicht vorhanden)
- Installiert Version **2.0.34** (neuere Versionen sind inkompatibel mit dem Stellarium-Code)
- Aktiviert den Compiler (`emcc`)

Falls du das SDK woanders installiert hast, setze `EMSDK_DIR`:
```bash
EMSDK_DIR=/pfad/zu/emsdk ./scripts/build_stellarium.sh
```

### 2. Stellarium Web Engine klonen

- Klont das [GitHub-Repo](https://github.com/Stellarium/stellarium-web-engine) nach `build/stellarium-web-engine/`
- Bei erneutem Ausführen: aktualisiert den bestehenden Checkout

### 3. WASM kompilieren

- Installiert `scons` (Build-Tool) im Projekt-venv falls nötig
- Patcht den SConstruct um Compiler-Warnungen zu unterdrücken (Kompatibilität mit neueren Emscripten-Versionen)
- Baut mit `make js` — produziert:
  - `stellarium-web-engine.js` (~79 KB) — JavaScript-Loader
  - `stellarium-web-engine.wasm` (~1,2 MB) — WebAssembly-Binary

### 4. Artefakte installieren

- Kopiert `.js` und `.wasm` nach `static/stellarium/`
- Kopiert die Test-Sternkataloge nach `skydata/`

## Verzeichnisstruktur nach dem Build

```
sequence-planner/
├── static/
│   └── stellarium/
│       ├── stellarium-web-engine.js    ← JS-Loader (79 KB)
│       └── stellarium-web-engine.wasm  ← WASM-Binary (1,2 MB)
├── skydata/                            ← Sternkataloge (HiPS-Tiles)
│   ├── stars/                          ← Sternkatalog (bis Mag 7)
│   │   ├── properties
│   │   └── Norder1/Dir0/*.eph
│   ├── dso/                            ← Deep-Sky-Objekte
│   ├── skycultures/western/            ← Sternbilder (westlich)
│   ├── surveys/milkyway/               ← Milchstraße
│   └── landscapes/guereins/            ← Horizontlandschaft
├── build/                              ← Build-Verzeichnis (gitignored)
│   └── stellarium-web-engine/          ← Geklonter Quellcode
└── ~/emsdk/                            ← Emscripten SDK (außerhalb des Projekts)
```

## Voraussetzungen

- **Git** — zum Klonen der Repos
- **Python 3.11+** — für scons (Build-Tool)
- **Projekt-venv** — muss vorher existieren (`./scripts/setup.sh`)
- ~500 MB Festplatte für Emscripten SDK
- ~100 MB für den Stellarium-Build
- Internetverbindung (nur beim ersten Mal)

**Nicht nötig:** cmake, make (wird vom Script verwendet, ist auf Linux/macOS vorinstalliert)

## Häufige Probleme

### `emcc not found`
Emscripten ist nicht im PATH. Das Script installiert es automatisch — normalerweise tritt das nicht auf. Falls doch:
```bash
source ~/emsdk/emsdk_env.sh
```

### Build-Fehler mit neuerer Emscripten-Version
Das Script verwendet absichtlich Emscripten 2.0.34. Falls du eine andere Version brauchst:
```bash
EMSDK_VERSION=2.0.34 ./scripts/build_stellarium.sh
```

### `scons not found`
Das Script installiert scons automatisch im Projekt-venv. Falls das fehlschlägt:
```bash
.venv/bin/python -m pip install scons
```

### Rebuild erzwingen
```bash
rm -rf build/stellarium-web-engine
./scripts/build_stellarium.sh
```

## Wie die Integration funktioniert

```
Browser                              Server (NiceGUI/FastAPI)
┌──────────────────────┐             ┌──────────────────────┐
│                      │   HTTP GET  │                      │
│  bridge.js           │────────────→│ /static/stellarium/  │
│  ├─ lädt .js         │             │   stellarium-web-    │
│  ├─ initialisiert    │             │   engine.js + .wasm  │
│  │  WASM-Engine      │             │                      │
│  └─ registriert      │   HTTP GET  │ /skydata/            │
│     Datenquellen     │────────────→│   stars/, dso/,      │
│                      │             │   skycultures/, ...  │
│  Stellarium Engine   │             │                      │
│  ├─ rendert Canvas   │             └──────────────────────┘
│  ├─ lädt HiPS-Tiles  │
│  └─ Koordinaten-API  │
└──────────────────────┘
```

1. `bridge.js` lädt die `stellarium-web-engine.js` per `<script>` Tag
2. Die JS-Datei lädt die `.wasm`-Binary und initialisiert die Engine
3. Nach Init registriert `bridge.js` die Datenquellen (Sterne, DSOs, Sternbilder)
4. Die Engine lädt HiPS-Tiles on-demand aus dem `skydata/`-Verzeichnis
5. Alles wird lokal serviert — **kein Internet nötig** nach der Installation

## Für andere Rechner

Die `.js` und `.wasm` Dateien sind **plattformunabhängig** — einmal kompilieren, überall verwenden. Um die Sternkarte auf einem anderen Rechner zu nutzen:

1. Kopiere `static/stellarium/` (79 KB + 1,2 MB)
2. Kopiere `skydata/` (~50 MB mit Test-Daten)
3. Fertig — kein Emscripten nötig auf dem Zielrechner

Alternativ: das Build-Script auf dem Zielrechner ausführen (baut alles von Quellcode).
