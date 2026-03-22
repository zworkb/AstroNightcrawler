# Nightcrawler — Architecture (UML)

## 1. Komponentendiagramm

```mermaid
graph TB
    subgraph Browser
        SWE["Stellarium Web Engine<br/>(WASM/WebGL)"]
        Bridge["bridge.js<br/>Coordinate Conversion"]
        Overlay["path_overlay.js<br/>SVG Spline Editor"]
        NiceGUI_FE["NiceGUI Frontend<br/>(Quasar/Vue)"]
    end

    subgraph "Python Server"
        FastAPI["FastAPI + uvicorn"]
        NiceGUI_BE["NiceGUI Backend"]
        AppState["AppState"]

        subgraph Models
            Project["Project"]
            Spline["Spline Math"]
            Freehand["Freehand (RDP)"]
            Undo["Undo/Redo Stack"]
        end

        subgraph Capture
            Controller["Capture Controller"]
            FITSWriter["FITS Writer"]
        end

        subgraph INDI
            INDIClient["INDI Client"]
            MockINDI["Mock INDI Client"]
        end

        subgraph Export
            EKOS["EKOS Export"]
        end
    end

    subgraph External
        INDIServer["INDI Server<br/>(indiserver)"]
        Telescope["Teleskop"]
        Camera["Kamera"]
    end

    subgraph "Ausgabe"
        FITS["FITS-Dateien"]
        Manifest["manifest.json"]
    end

    NiceGUI_FE <-->|WebSocket| NiceGUI_BE
    NiceGUI_BE --> AppState
    AppState --> Models
    AppState --> Controller
    Controller --> INDIClient
    Controller --> FITSWriter
    INDIClient -->|TCP/XML| INDIServer
    INDIServer --> Telescope
    INDIServer --> Camera
    FITSWriter --> FITS
    Controller --> Manifest
    Bridge <--> SWE
    Overlay --> Bridge
    NiceGUI_FE <-->|JS Interop| Bridge
    NiceGUI_FE <-->|JS Interop| Overlay
    FastAPI --> NiceGUI_BE
```

## 2. Klassendiagramm — Datenmodelle

```mermaid
classDiagram
    class Coordinate {
        +float ra
        +float dec
        +validate_ra()
        +validate_dec()
    }

    class ControlPoint {
        +str|None label
        +Coordinate|None handle_in
        +Coordinate|None handle_out
    }

    class SplinePath {
        +list~ControlPoint~ control_points
        +str spline_type = "cubic_bezier"
        +str coordinate_frame = "J2000"
        +validate_min_points()
    }

    class CaptureSettings {
        +float point_spacing_deg = 0.5
        +float exposure_seconds = 30.0
        +int exposures_per_point = 1
        +int gain = 0
        +int offset = 0
        +int binning = 1
        +validate_binning()
        +validate_exposure()
        +validate_spacing()
    }

    class CapturePoint {
        +int index
        +Literal status = "pending"
        +list~str~ files
        +str|None captured_at
        +filename(exposure: int) str
    }

    class INDIConfig {
        +str host = "localhost"
        +int port = 7624
        +str telescope
        +str camera
    }

    class Project {
        +str version = "1.0"
        +str created
        +str project
        +SplinePath path
        +CaptureSettings capture_settings
        +list~CapturePoint~ capture_points
        +INDIConfig|None indi
    }

    Coordinate <|-- ControlPoint
    Coordinate <|-- CapturePoint
    ControlPoint o-- Coordinate : handle_in / handle_out
    SplinePath *-- ControlPoint : control_points
    Project *-- SplinePath : path
    Project *-- CaptureSettings : capture_settings
    Project *-- CapturePoint : capture_points
    Project o-- INDIConfig : indi
```

## 3. Klassendiagramm — Anwendungslogik

```mermaid
classDiagram
    class AppState {
        +Project project
        +INDIClient indi_client
        +UndoStack undo_stack
        +update_capture_points()
        +save_project(path)
        +load_project(path)
        +start_capture() CaptureController
    }

    class UndoStack {
        -list~str~ _undo_stack
        -list~str~ _redo_stack
        -int max_size = 50
        +push(before, after)
        +undo() str|None
        +redo() str|None
        +can_undo bool
        +can_redo bool
    }

    class INDIClient {
        <<abstract>>
        +bool connected
        +connect(host, port)
        +disconnect()
        +slew_to(ra, dec)
        +wait_for_settle(timeout)
        +capture(params) bytes
        +get_devices() dict
        +abort()
        +reconnect(timeout)
    }

    class MockINDIClient {
        +float slew_delay
        +float settle_delay
        +int fail_count
    }

    class CaptureController {
        +CaptureState state
        +int current_point_index
        +str|None last_error
        +float estimated_remaining_seconds
        +run()
        +pause()
        +resume()
        +cancel()
        +skip_point()
    }

    class FITSWriter {
        +Path output_dir
        +write(point, exposure_num, data) Path
    }

    class CaptureState {
        <<enumeration>>
        IDLE
        RUNNING
        PAUSED
        COMPLETED
        CANCELLED
        ERROR
    }

    INDIClient <|-- MockINDIClient
    AppState --> INDIClient
    AppState --> UndoStack
    AppState --> Project
    CaptureController --> INDIClient
    CaptureController --> FITSWriter
    CaptureController --> Project
    CaptureController --> CaptureState
```

## 4. Zustandsdiagramm — Capture Controller

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> RUNNING : run()

    RUNNING --> PAUSED : pause()
    RUNNING --> COMPLETED : alle Punkte erfasst
    RUNNING --> CANCELLED : cancel()
    RUNNING --> PAUSED : Fehler (retry fehlgeschlagen)

    PAUSED --> RUNNING : resume()
    PAUSED --> CANCELLED : cancel()
    PAUSED --> RUNNING : skip_point()

    COMPLETED --> [*]
    CANCELLED --> [*]
```

## 5. Sequenzdiagramm — Aufnahme eines Punktes

```mermaid
sequenceDiagram
    participant CC as CaptureController
    participant INDI as INDIClient
    participant FW as FITSWriter
    participant P as Project

    CC->>CC: point.status = "capturing"
    CC->>INDI: slew_to(ra, dec)
    activate INDI

    alt Slew erfolgreich
        INDI-->>CC: OK
    else Slew Timeout
        CC->>INDI: slew_to(ra, dec) [Retry]
        INDI-->>CC: OK / Fehler → Pause
    end
    deactivate INDI

    CC->>INDI: wait_for_settle(30s)
    INDI-->>CC: settled = true

    loop Für jede Belichtung (1..N)
        CC->>INDI: capture(params)
        activate INDI
        INDI-->>CC: FITS bytes
        deactivate INDI
        CC->>FW: write(point, exp_num, data)
        FW-->>CC: filepath
    end

    CC->>P: point.status = "captured"
    CC->>P: point.captured_at = now()
```

## 6. Sequenzdiagramm — Pfad zeichnen (Browser ↔ Server)

```mermaid
sequenceDiagram
    participant User
    participant Overlay as PathOverlay (JS)
    participant Bridge as StelBridge (JS)
    participant SWE as Stellarium Engine
    participant NiceGUI as NiceGUI (Python)
    participant State as AppState

    User->>Overlay: Klick auf Karte
    Overlay->>Bridge: screenToWorld(x, y)
    Bridge->>SWE: core.screenToWorld([x,y])
    SWE-->>Bridge: [ra, dec]
    Bridge-->>Overlay: {ra, dec}
    Overlay->>NiceGUI: CustomEvent "path_add_point"
    NiceGUI->>State: add ControlPoint(ra, dec)
    State->>State: undo_stack.push(before, after)
    State->>State: update_capture_points()
    NiceGUI->>Overlay: update(controlPoints, capturePoints)
    Overlay->>Bridge: worldToScreen() für jeden Punkt
    Bridge->>SWE: core.worldToScreen()
    Overlay->>Overlay: SVG neu rendern
```

## 7. Sequenzdiagramm — Projekt Lifecycle

```mermaid
sequenceDiagram
    participant User
    participant App as Planner App
    participant FS as Dateisystem
    participant Render as Rendering App

    User->>App: Pfad zeichnen + Einstellungen
    App->>App: Aufnahmepunkte berechnen
    User->>App: "Speichern"
    App->>FS: project.json (status: pending)

    User->>App: "Aufnahme starten"
    loop Für jeden Punkt
        App->>App: Slew → Settle → Capture
        App->>FS: seq_NNNN_MMM.fits
        App->>FS: project.json aktualisieren (status: captured)
    end
    App->>FS: manifest.json in Output-Verzeichnis

    Note over FS: Verzeichnis auf Rendering-Rechner kopieren

    User->>Render: Output-Verzeichnis öffnen
    Render->>FS: manifest.json lesen
    Render->>Render: FITS → Stretch → PNG → Video
    Render->>FS: video.mp4
```

## 8. UI-Layout Diagramm

```mermaid
graph TB
    subgraph "Browser Viewport"
        subgraph Toolbar["Toolbar"]
            Draw["Draw"]
            Freehand["Freehand"]
            Move["Move"]
            AddPt["Add Point"]
            RemPt["Remove"]
            Split["Split"]
            Sep1["│"]
            Undo["Undo"]
            Redo["Redo"]
            Sep2["│"]
            Save["Save"]
            Load["Load"]
            Export["EKOS"]
            Sep3["│"]
            Start["▶ Start Capture"]
        end

        subgraph StarMap["Sternkarte (Hauptbereich)"]
            SWE2["Stellarium Web Engine Canvas"]
            SVG["SVG Overlay (Spline + Punkte)"]
            CoordDisplay["RA/Dec Anzeige"]
        end

        subgraph BottomPanel["Bottom Panel (einklappbar)"]
            subgraph Collapsed["Eingeklappt"]
                Summary["Pfad: 3 ctrl / 8 capture │ 30s × 1 │ 0.5° │ ~4 min"]
            end
            subgraph Expanded["Ausgeklappt"]
                Settings["Einstellungen<br/>Abstand / Belichtung / Gain"]
                PointTable["Aufnahmepunkt-Tabelle<br/># │ RA │ Dec │ Status"]
                INDIPanel["INDI Verbindung<br/>Host │ Port │ Status"]
            end
        end
    end

    Toolbar --> StarMap
    StarMap --> BottomPanel
```
