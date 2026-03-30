# Nightcrawler — Architecture (UML)

## 1. Component Diagram — System Overview

```mermaid
graph TB
    subgraph "Planner & Capture App"
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
                INDIClient["INDIClient (ABC)"]
                AsyncAdapter["AsyncINDIAdapter"]
                AsyncClient["AsyncINDIClient"]
                MockINDI["MockINDIClient"]
            end

            subgraph Export
                EKOS["EKOS Export"]
            end

            subgraph UI
                Layout["Layout"]
                Toolbar["Toolbar"]
                BottomPanel["Bottom Panel"]
                CaptureView["Capture View"]
                FolderBrowser["FolderBrowserDialog"]
            end
        end
    end

    subgraph "Rendering App"
        RenderCLI["nightcrawler-render CLI"]
        RenderUI["Renderer Web UI"]
        Pipeline["RenderPipeline"]

        subgraph "Pipeline Stages"
            Importer["Importer<br/>(FITS + manifest)"]
            Debayer["Debayer<br/>(colour-demosaicing)"]
            Stretch["Stretch<br/>(ZScale / Histogram / Manual)"]
            Alignment["Alignment<br/>(astroalign)"]
            Transitions["Transitions<br/>(crossfade / linear-pan)"]
            VideoEnc["Video Encoder<br/>(ffmpeg)"]
        end
    end

    subgraph External
        INDIServer["INDI Server<br/>(indiserver)"]
        Telescope["Telescope"]
        Camera["Camera"]
    end

    subgraph "Shared Data"
        FITS["FITS Files"]
        Manifest["manifest.json"]
        ProjectJSON["project.json"]
    end

    NiceGUI_FE <-->|WebSocket| NiceGUI_BE
    NiceGUI_BE --> AppState
    AppState --> Models
    AppState --> Controller
    Controller --> AsyncAdapter
    AsyncAdapter --> AsyncClient
    AsyncClient -->|TCP/XML| INDIServer
    INDIServer --> Telescope
    INDIServer --> Camera
    Controller --> FITSWriter
    FITSWriter --> FITS
    Controller --> Manifest
    Bridge <--> SWE
    Overlay --> Bridge
    NiceGUI_FE <-->|JS Interop| Bridge
    NiceGUI_FE <-->|JS Interop| Overlay
    FastAPI --> NiceGUI_BE

    Manifest -.->|input| Importer
    FITS -.->|input| Importer
    Importer --> Debayer --> Stretch --> Alignment --> Transitions --> VideoEnc
    RenderCLI --> Pipeline
    RenderUI --> Pipeline
    Pipeline --> Importer
```

## 2. Package Diagram

```mermaid
graph TB
    subgraph "src/"
        main["main.py<br/>(nightcrawler entry)"]
        config["config.py<br/>(pydantic-settings)"]
        app_state["app_state.py"]

        subgraph "src/models/"
            project_mod["project.py<br/>Project, Coordinate,<br/>SplinePath, CapturePoint,<br/>CaptureSettings, INDIConfig"]
            spline_mod["spline.py<br/>Cubic Bézier math"]
            freehand_mod["freehand.py<br/>RDP simplification"]
            undo_mod["undo.py<br/>UndoStack"]
        end

        subgraph "src/ui/"
            layout_mod["layout.py"]
            toolbar_mod["toolbar.py"]
            bottom_mod["bottom_panel.py"]
            capture_view_mod["capture_view.py"]
            folder_browser_mod["folder_browser.py<br/>FolderBrowserDialog"]
            overlay_sync_mod["overlay_sync.py"]
        end

        subgraph "src/starmap/"
            engine_mod["engine.py<br/>StarMap"]
            bridge_mod["bridge.js"]
            path_overlay_mod["path_overlay.js"]
            projection_mod["projection.py"]
        end

        subgraph "src/capture/"
            controller_mod["controller.py<br/>CaptureController"]
            fits_writer_mod["fits_writer.py<br/>FITSWriter"]
        end

        subgraph "src/indi/"
            indi_client_mod["client.py<br/>INDIClient (ABC)"]
            async_adapter_mod["async_adapter.py<br/>AsyncINDIAdapter"]
            mock_mod["mock.py<br/>MockINDIClient"]
            real_mod["real_client.py<br/>(PyIndi, legacy)"]

            subgraph "src/indi/asynclient/"
                async_client_mod["client.py<br/>AsyncINDIClient"]
                protocol_mod["protocol.py<br/>INDIXMLParser,<br/>INDIDevice, INDIVector"]
            end
        end

        subgraph "src/export/"
            ekos_mod["ekos.py<br/>EKOS/KStars export"]
        end

        subgraph "src/renderer/"
            pipeline_mod["pipeline.py<br/>RenderPipeline, RenderConfig"]
            cli_mod["cli.py<br/>(nightcrawler-render entry)"]
            importer_mod["importer.py<br/>FrameInfo, load_manifest"]
            debayer_mod["debayer.py<br/>DebayerMode"]
            stretch_mod["stretch.py<br/>StretchParams, auto/histogram/manual"]
            alignment_mod["alignment.py<br/>AlignmentResult, align_pair"]
            transitions_mod["transitions.py<br/>crossfade, linear_pan"]
            video_mod["video.py<br/>encode_video, write_frame_png"]

            subgraph "src/renderer/ui/"
                render_layout_mod["render_layout.py<br/>Renderer Web UI"]
            end
        end
    end

    main --> app_state
    main --> config
    app_state --> project_mod
    app_state --> controller_mod
    app_state --> indi_client_mod
    controller_mod --> async_adapter_mod
    async_adapter_mod --> async_client_mod
    controller_mod --> fits_writer_mod
    pipeline_mod --> importer_mod
    pipeline_mod --> debayer_mod
    pipeline_mod --> stretch_mod
    pipeline_mod --> alignment_mod
    pipeline_mod --> transitions_mod
    pipeline_mod --> video_mod
    importer_mod --> project_mod
    render_layout_mod --> pipeline_mod
    render_layout_mod --> folder_browser_mod
    cli_mod --> pipeline_mod
```

## 3. Class Diagram — Data Models (shared by Planner and Renderer)

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

## 4. Class Diagram — Planner Application Logic

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

    class AsyncINDIAdapter {
        -AsyncINDIClient _inner
        +connect(host, port)
        +disconnect()
        +unpark()
        +slew_to(ra, dec)
        +wait_for_settle(timeout) bool
        +capture(params) bytes
        +get_devices() dict
        +abort()
    }

    class AsyncINDIClient {
        +bool connected
        +dict devices
        +connect(host, port)
        +disconnect()
        +enable_blob(device, mode)
        +send_number(device, vector, members)
        +send_switch(device, vector, members)
        +wait_for_blob(timeout) bytes|None
        +find_device_with_property(name) str|None
        +get_vector(device, vector) INDIVector|None
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

    class FolderBrowserDialog {
        -Callable _on_select
        -Path _current
        +open(start_path)
    }

    class StarMap {
        +initialize(wasm_path, skydata_path)
        +look_at(ra, dec, fov)
    }

    INDIClient <|-- AsyncINDIAdapter
    INDIClient <|-- MockINDIClient
    AsyncINDIAdapter --> AsyncINDIClient : wraps
    AppState --> INDIClient
    AppState --> UndoStack
    AppState --> Project
    CaptureController --> INDIClient
    CaptureController --> FITSWriter
    CaptureController --> Project
    CaptureController --> CaptureState
```

## 5. Class Diagram — Renderer

```mermaid
classDiagram
    class RenderConfig {
        +int fps = 24
        +int crf = 18
        +str stretch_mode = "auto"
        +StretchParams|None stretch_params
        +DebayerMode debayer_mode = AUTO
        +str transition = "crossfade"
        +int crossfade_frames = 24
        +str resolution = "native"
        +bool keep_frames = False
        +Path|None temp_dir
    }

    class RenderPipeline {
        +Path capture_dir
        +RenderConfig config
        +list~FrameInfo~ frames
        +load()
        +active_frames() list~FrameInfo~
        +skip_frame(index)
        +stretch_frame(frame_idx) ndarray
        +render(output_path, on_progress)
    }

    class FrameInfo {
        +int index
        +Path fits_path
        +float ra
        +float dec
        +str|None bayer_pattern
        +float exposure
        +bool skipped = False
    }

    class StretchParams {
        +float black = 0.0
        +float white = 1.0
        +float midtone = 0.5
    }

    class DebayerMode {
        <<enumeration>>
        AUTO
        OFF
        RGGB
        GBRG
        GRBG
        BGGR
    }

    class AlignmentResult {
        +float dx = 0.0
        +float dy = 0.0
        +float rotation = 0.0
        +bool success = False
    }

    RenderPipeline --> RenderConfig
    RenderPipeline --> FrameInfo
    RenderPipeline --> AlignmentResult
    RenderConfig --> StretchParams
    RenderConfig --> DebayerMode
```

## 6. State Diagram — Capture Controller

```mermaid
stateDiagram-v2
    [*] --> IDLE

    IDLE --> RUNNING : run()

    RUNNING --> PAUSED : pause()
    RUNNING --> COMPLETED : all points captured
    RUNNING --> CANCELLED : cancel()
    RUNNING --> PAUSED : error (retry failed)

    PAUSED --> RUNNING : resume()
    PAUSED --> CANCELLED : cancel()
    PAUSED --> RUNNING : skip_point()

    COMPLETED --> [*]
    CANCELLED --> [*]
```

## 7. Sequence Diagram — Render Pipeline

```mermaid
sequenceDiagram
    participant User
    participant CLI as nightcrawler-render
    participant RP as RenderPipeline
    participant Imp as Importer
    participant Deb as Debayer
    participant Str as Stretch
    participant Alg as Alignment
    participant Trans as Transitions
    participant Vid as Video Encoder
    participant FS as Filesystem

    User->>CLI: --input ./output --transition linear-pan
    CLI->>RP: RenderPipeline(capture_dir, config)
    CLI->>RP: load()
    RP->>Imp: load_manifest(capture_dir)
    Imp->>FS: read manifest.json
    Imp->>FS: read BAYERPAT from each FITS header
    Imp-->>RP: list[FrameInfo]

    CLI->>RP: render(output_path)

    Note over RP: Stage 1 — Alignment (raw frames)
    loop For each adjacent pair
        RP->>FS: load raw FITS (mono)
        RP->>Alg: align_pair(frame_a, frame_b)
        Alg-->>RP: AlignmentResult(dx, dy)
    end
    RP->>Alg: filter_outlier_alignments()

    Note over RP: Stage 2 — Stream debayer + stretch + transitions
    loop For each frame (streaming, 2 in memory)
        RP->>FS: load FITS data
        RP->>Deb: debayer_frame(data, pattern)
        Deb-->>RP: RGB array
        RP->>Str: apply_stretch(rgb, mode)
        Str-->>RP: 8-bit sRGB

        alt Has previous frame
            RP->>Trans: linear_pan(prev, current, alignment, N)
            Trans-->>RP: list[transition_frames]
            loop Each transition frame
                RP->>Vid: write_frame_png(frame, temp_dir)
            end
        end
    end

    Note over RP: Stage 3 — Encode
    RP->>Vid: encode_video(temp_dir, output.mp4, fps, crf)
    Vid->>FS: ffmpeg → output.mp4
    Vid-->>RP: done
    RP-->>CLI: done
    CLI-->>User: "Video saved to output.mp4"
```

## 8. Sequence Diagram — Capture Flow

```mermaid
sequenceDiagram
    participant User
    participant App as Planner App
    participant CC as CaptureController
    participant Adapter as AsyncINDIAdapter
    participant Client as AsyncINDIClient
    participant Server as INDI Server
    participant FW as FITSWriter
    participant FS as Filesystem

    User->>App: Draw path + configure settings
    App->>App: Compute capture points on spline
    User->>App: "Start Capture"
    App->>Adapter: connect(host, port)
    Adapter->>Client: connect(host, 7624)
    Client->>Server: TCP connect + getProperties
    Client-->>Adapter: devices discovered
    Adapter->>Client: enable_blob(camera)
    App->>CC: run()

    CC->>Adapter: unpark()
    Adapter->>Client: send_switch(TELESCOPE_PARK, UNPARK=On)

    loop For each CapturePoint
        CC->>CC: point.status = "capturing"
        CC->>Adapter: slew_to(ra, dec)
        Adapter->>Client: send_number(EQUATORIAL_EOD_COORD)
        Client->>Server: XML command
        Server-->>Client: state → Ok
        Adapter-->>CC: slew complete

        CC->>Adapter: wait_for_settle(30s)
        Adapter-->>CC: settled

        loop For each exposure (1..N)
            CC->>Adapter: capture(params)
            Adapter->>Client: send_number(CCD_EXPOSURE)
            Client->>Server: start exposure
            Server-->>Client: BLOB (FITS bytes)
            Client-->>Adapter: raw bytes
            Adapter-->>CC: FITS data
            CC->>FW: write(point, exp_num, data)
            FW->>FS: seq_NNNN_MMM.fits
        end

        CC->>FS: update manifest.json
        CC->>CC: point.status = "captured"
    end
```

## 9. Sequence Diagram — Path Drawing (Browser <-> Server)

```mermaid
sequenceDiagram
    participant User
    participant Overlay as PathOverlay (JS)
    participant Bridge as StelBridge (JS)
    participant SWE as Stellarium Engine
    participant NiceGUI as NiceGUI (Python)
    participant State as AppState

    User->>Overlay: Click on map
    Overlay->>Bridge: screenToWorld(x, y)
    Bridge->>SWE: core.screenToWorld([x,y])
    SWE-->>Bridge: [ra, dec]
    Bridge-->>Overlay: {ra, dec}
    Overlay->>NiceGUI: CustomEvent "path_add_point"
    NiceGUI->>State: add ControlPoint(ra, dec)
    State->>State: undo_stack.push(before, after)
    State->>State: update_capture_points()
    NiceGUI->>Overlay: update(controlPoints, capturePoints)
    Overlay->>Bridge: worldToScreen() for each point
    Bridge->>SWE: core.worldToScreen()
    Overlay->>Overlay: Re-render SVG
```

## 10. Sequence Diagram — Project Lifecycle

```mermaid
sequenceDiagram
    participant User
    participant Planner as Planner App
    participant FS as Filesystem
    participant Render as Rendering App

    User->>Planner: Draw path + configure settings
    Planner->>Planner: Compute capture points
    User->>Planner: "Save"
    Planner->>FS: project.json (status: pending)

    User->>Planner: "Start Capture"
    loop For each point
        Planner->>Planner: Slew → Settle → Capture
        Planner->>FS: seq_NNNN_MMM.fits
        Planner->>FS: manifest.json (updated after each capture)
    end

    Note over FS: Copy capture directory to rendering workstation

    User->>Render: nightcrawler-render --input ./output
    Render->>FS: Read manifest.json
    Render->>Render: Import → Debayer → Stretch → Align → Transitions
    Render->>FS: ffmpeg → video.mp4
```

## 11. UI Layout — Planner

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

        subgraph StarMap["Star Map (main area)"]
            SWE2["Stellarium Web Engine Canvas"]
            SVG["SVG Overlay (Spline + Points)"]
            CoordDisplay["RA/Dec Display"]
        end

        subgraph BottomPanel["Bottom Panel (collapsible)"]
            subgraph Collapsed["Collapsed"]
                Summary["Path: 3 ctrl / 8 capture | 30s x 1 | 0.5 deg | ~4 min"]
            end
            subgraph Expanded["Expanded"]
                Settings["Settings<br/>Spacing / Exposure / Gain"]
                PointTable["Capture Point Table<br/># | RA | Dec | Status"]
                INDIPanel["INDI Connection<br/>Host | Port | Status"]
            end
        end
    end

    Toolbar --> StarMap
    StarMap --> BottomPanel
```
