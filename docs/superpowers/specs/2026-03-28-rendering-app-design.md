# Rendering App — Design Specification

## Overview

Module within AstroNightcrawler for converting captured FITS image sequences into video. Reads the capture output directory (FITS files + manifest.json), applies stretch/tonmapping, and assembles frames into a video file.

Runs either integrated into the Planner App (on powerful machines) or standalone on a separate workstation. Same code, two entry points.

---

## 1. Architecture

### Module Structure

```
src/renderer/
├── __init__.py
├── pipeline.py       # Main rendering pipeline orchestration
├── stretch.py        # FITS → visible image (stretch/tonemap)
├── debayer.py        # Bayer pattern detection and demosaicing
├── alignment.py      # Star alignment between frames (astroalign)
├── transitions.py    # Frame transition effects (crossfade, linear pan)
├── video.py          # ffmpeg video assembly
├── cli.py            # CLI entry point
└── ui/
    ├── __init__.py
    └── render_layout.py  # NiceGUI web UI
```

### Entry Points

```toml
[project.scripts]
nightcrawler = "src.main:main"
nightcrawler-render = "src.renderer.cli:main"
```

Both entry points share the same code. The Planner App can also launch rendering directly after capture via a "Render" button.

### Shared Models

The renderer imports from `src.models.project`:
- `Project` — top-level container with manifest data
- `CapturePoint` — per-point metadata (coordinates, status, files)
- `CaptureSettings` — exposure, gain, spacing, sequence name

---

## 2. Rendering Pipeline

### Pipeline Stages

```
1. Import     → Read manifest.json, locate FITS files
2. Debayer    → Detect and apply demosaicing if needed
3. Stretch    → Convert linear FITS to visible image (auto or manual)
4. Review     → Frame browser for QA (skip/delete bad frames)
5. Align      → Star alignment between adjacent frames (optional, for transitions)
6. Transition → Generate inter-frame transitions (crossfade or linear pan)
7. Encode     → Assemble frames into video via ffmpeg
```

### Stage Details

#### 2.1 Import

Read `manifest.json` from the capture output directory. Extract:
- Frame order from `capture_points` (sorted by index)
- Only include points with `status == "captured"`
- Resolve FITS file paths relative to the manifest directory

**Multiple exposures per point:** When `exposures_per_point > 1`, each point has multiple FITS files. Strategy:
- **v1:** Use the first exposure only (`files[0]`)
- **v2:** Stack all exposures per point (mean/median) before rendering — produces one combined frame per point with improved SNR

**Memory management:** FITS files can be large (35MB raw, ~190MB as float64 numpy array after debayering). Strategy:
- Process frames in pairs — never load more than 2-3 full-resolution frames simultaneously
- Use `astropy.io.fits` with `memmap=True` for lazy loading
- Write intermediate results (debayered, stretched) to disk as 16-bit TIFF or `.npy` files
- Filmstrip thumbnails generated at reduced resolution and cached as JPEG

#### 2.2 Debayer

DSLR cameras with Bayer sensors produce raw CFA (Color Filter Array) data. Mono cameras produce grayscale.

**Detection:**
- Check FITS header `BAYERPAT` (e.g., `RGGB`, `GBRG`)
- If present → apply debayering (demosaicing)
- If absent → treat as mono (or already debayered color)

**Algorithm:** Bilinear interpolation as default (fast), with option for VNG or AHD (higher quality, slower). Uses `colour-demosaicing` library (pure Python/numpy, lightweight).

**Manual override:** User can force debayer on/off and select the Bayer pattern, in case the FITS header is missing or incorrect. CLI flag: `--debayer auto|off|RGGB|GBRG|GRBG|BGGR`.

#### 2.3 Stretch / Tonmapping

FITS data is linear (photon counts). Must be stretched to visible range for display and video.

**Auto-Stretch (default):**
- `ZScaleInterval` from astropy — determines black/white points (same algorithm as ds9/SAOImage)
- `AsinhStretch` — arcsinh transfer function, preserves dynamic range better than linear

**Histogram-based (alternative):**
- Analyze pixel distribution
- Clip at configurable percentiles (e.g., 0.1% and 99.9%)
- Apply curve (linear, gamma, or asinh)

**Manual controls:**
- Black point (shadows)
- White point (highlights)
- Midtone / gamma
- Live preview on current frame

**Global vs per-frame:** Settings are global by default (same stretch for all frames). Per-frame override possible but not required for v1.

**Output color space:** Stretched images are converted to **8-bit sRGB** for video output (H.264 default profile is 8-bit). Mono images are replicated to 3-channel grayscale. 10-bit output is a future enhancement.

#### 2.4 Review — Frame Browser

**Filmstrip view:**
- Horizontal scrollable row of thumbnails (stretched, debayered)
- Click on thumbnail → large preview with stretch controls
- Frame metadata displayed: index, RA/Dec, timestamp, exposure

**Frame management:**
- Mark frames as "skip" (excluded from video but not deleted)
- Delete frames (remove from video, keep FITS file)
- Drag to reorder (future enhancement)

#### 2.5 Star Alignment

Uses [astroalign](https://github.com/quatrope/astroalign) v2.6.2 to register adjacent frames.

**Purpose:** Compute the affine transformation (shift, rotation, scale) between each pair of adjacent frames. This is needed for:
1. Quality check — large unexpected shifts indicate tracking problems
2. Linear pan transitions — smooth sub-pixel interpolation between frames

**Process:**
1. For each pair (frame n, frame n+1): find matching star triangles
2. Compute affine transformation matrix (2×3)
3. Extract translation vector (dx, dy in pixels)
4. Store transformations for the transition stage

**When alignment fails:** Fall back to identity (no shift). Log a warning. The frame is still included but the transition will be a simple cut or crossfade.

#### 2.6 Transitions

Two modes:

**Crossfade (v1 — simple):**
- Between frame n and frame n+1, generate `k` intermediate frames
- Each intermediate frame: `alpha * frame_n + (1 - alpha) * frame_n+1`
- `k` determined by desired smoothness (e.g., 6 frames = 0.25s crossfade at 24fps)
- No alignment needed

**Linear Pan (v1 — with alignment):**
- Uses the alignment offset between frame n and frame n+1
- Generates intermediate frames by linearly interpolating the camera position
- Each intermediate frame is a sub-pixel shifted version of the blend
- Requires a **cropping pre-pass:**
  1. Compute alignment offset for each adjacent pair (n, n+1)
  2. Determine the maximum pairwise offset across all pairs
  3. Derive crop size that guarantees no black edges during any pairwise transition
  4. Apply the linear interpolation within the cropped area per pair

**Cropping calculation:**

The crop size is determined by the **maximum pairwise offset** across all adjacent frame pairs — not cumulative offsets across the whole sequence. Each pair's interpolation is independent.

```
crop_margin_x = 0
crop_margin_y = 0

For each adjacent pair (n, n+1):
  shift = alignment_offset(n, n+1)  # (dx, dy) in pixels
  crop_margin_x = max(crop_margin_x, abs(shift.dx))
  crop_margin_y = max(crop_margin_y, abs(shift.dy))

# Factor 2: margin needed on BOTH sides since shifts can go either direction
crop_width = original_width - 2 * crop_margin_x
crop_height = original_height - 2 * crop_margin_y
```

The crop window is anchored at `(crop_margin_x, crop_margin_y)` and shifts linearly to `(crop_margin_x + shift.dx, crop_margin_y + shift.dy)` over the intermediate frames. The `2×` factor ensures no black edges appear regardless of shift direction.

#### 2.7 Video Encoding

Uses ffmpeg via subprocess.

**Parameters:**
| Setting | Default | Description |
|---------|---------|-------------|
| FPS | 24 | Frames per second |
| Codec | H.264 | Video codec |
| Container | mp4 | Output format |
| CRF | 18 | Quality (lower = better, 18-28 typical) |
| Resolution | native | From FITS, or downscale factor |

**Process:**
1. Check ffmpeg availability at pipeline start (fail early with clear error)
2. Estimate disk space needed (frame count × frame size) and warn if insufficient
3. Write stretched/transitioned frames as numbered 8-bit sRGB PNGs to a temp directory (`tempfile.mkdtemp()`, or custom path via `--temp-dir`)
4. Call ffmpeg: `ffmpeg -framerate 24 -i frame_%06d.png -c:v libx264 -crf 18 output.mp4`
5. Clean up temp PNGs (or keep with `--keep-frames` for debugging)
6. On failure: capture ffmpeg stderr, surface meaningful error message, clean up temp directory

---

## 3. User Interface

### 3.1 CLI

```bash
# Simple: auto-stretch, no transitions
nightcrawler-render --input ./output/deneb/ --output deneb.mp4

# With options
nightcrawler-render \
  --input ./output/deneb/ \
  --output deneb.mp4 \
  --fps 24 \
  --stretch auto \
  --transition crossfade \
  --crossfade-frames 6 \
  --crf 18

# Linear pan (alignment is implied, no separate --align flag needed)
nightcrawler-render \
  --input ./output/deneb/ \
  --output deneb.mp4 \
  --transition linear-pan

# Override debayer detection
nightcrawler-render \
  --input ./output/deneb/ \
  --output deneb.mp4 \
  --debayer RGGB

# Keep intermediate frames for debugging
nightcrawler-render \
  --input ./output/deneb/ \
  --output deneb.mp4 \
  --keep-frames --temp-dir ./render-temp/
```

### 3.2 Web UI (NiceGUI)

Accessible via:
- Standalone: `nightcrawler-render --ui` (starts web server)
- Integrated: "Render" tab/button in the Planner App

**Layout:**

```
┌─────────────────────────────────────────────────────┐
│ [Import Dir] [▶ Render] [Settings ⚙]               │
├─────────────────────────────────────────────────────┤
│                                                     │
│              Large Preview Frame                    │
│              (with stretch applied)                 │
│                                                     │
├─────────────────────────────────────────────────────┤
│ Stretch:  [Auto ▾]  Black [===] White [===] Mid [=] │
│ Debayer:  [Auto ▾]  Pattern [RGGB ▾]               │
├─────────────────────────────────────────────────────┤
│ ◀ [thumb1][thumb2][thumb3][thumb4][thumb5]... ▶     │
│    Filmstrip — click to select, right-click to skip │
├─────────────────────────────────────────────────────┤
│ Transition: [Crossfade ▾]  Frames: [6]             │
│ Output: [deneb.mp4]  FPS: [24]  CRF: [18]         │
│ Progress: [████████░░░░░░░░] 45% — Encoding...     │
└─────────────────────────────────────────────────────┘
```

---

## 4. Technology Stack

| Component | Technology |
|-----------|-----------|
| FITS reading | astropy.io.fits |
| Stretch/tonemap | astropy.visualization (ZScaleInterval, AsinhStretch) |
| Debayering | colour-demosaicing |
| Star alignment | astroalign 2.6.2 |
| Image processing | numpy, scipy.ndimage (sub-pixel shifts) |
| Video encoding | ffmpeg (subprocess) |
| Web UI | NiceGUI |
| CLI | argparse or typer |

### Dependencies (additions to pyproject.toml)

```toml
"astroalign>=2.6",
"colour-demosaicing>=0.2",
"Pillow>=10.0",             # PNG writing
```

---

## 5. Configuration

Additional settings in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `NC_RENDER_FPS` | `24` | Default video framerate |
| `NC_RENDER_CRF` | `18` | Default video quality |
| `NC_RENDER_TRANSITION` | `crossfade` | Default transition type |
| `NC_RENDER_CROSSFADE_FRAMES` | `6` | Frames per crossfade transition |

**Precedence:** CLI flags override `.env` variables, which override built-in defaults.

---

## 6. Build Integration

Add to `include.mk`:
- `run-render` target — starts the renderer web UI (`nightcrawler-render --ui`)
- Depends on `$(INSTALL_TARGETS)` and `.env`

Add to `pyproject.toml`:
- `nightcrawler-render` entry point in `[project.scripts]`

---

## 7. Future Enhancements (out of scope for v1)

- Per-frame stretch override
- Drag-to-reorder frames in the browser
- Stacking integration (Siril or built-in)
- Audio track overlay
- Title/watermark overlay
- Multiple output formats (WebM, GIF)
- GPU-accelerated stretch/alignment
- Batch rendering of multiple sequences
