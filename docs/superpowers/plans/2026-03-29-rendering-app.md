# Rendering App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rendering module that converts captured FITS image sequences into video with stretch/tonmapping, debayering, and frame transitions.

**Architecture:** Module at `src/renderer/` within the existing Nightcrawler project. Pipeline stages (import → debayer → stretch → review → align → transition → encode) are independent functions orchestrated by a pipeline runner. CLI and Web UI share the same pipeline code.

**Tech Stack:** astropy (FITS/stretch), colour-demosaicing, astroalign (star alignment), numpy/scipy (image processing), Pillow (PNG), ffmpeg (video), NiceGUI (web UI), argparse (CLI)

**Spec:** `docs/superpowers/specs/2026-03-28-rendering-app-design.md`

**Coding Standards:** `cleancode.md` + `cleancode-python.md`

---

## File Structure

```
src/renderer/
├── __init__.py              # Exports RenderPipeline
├── importer.py              # Read manifest, resolve FITS paths, load frames
├── debayer.py               # Bayer detection + demosaicing
├── stretch.py               # FITS → 8-bit sRGB (auto/manual stretch)
├── alignment.py             # Star alignment via astroalign
├── transitions.py           # Crossfade + linear pan generation
├── video.py                 # ffmpeg wrapper
├── pipeline.py              # Orchestrates all stages
├── cli.py                   # CLI entry point (nightcrawler-render)
└── ui/
    ├── __init__.py
    └── render_layout.py     # NiceGUI web UI

tests/
├── test_renderer_import.py
├── test_debayer.py
├── test_stretch.py
├── test_alignment.py
├── test_transitions.py
├── test_video.py
└── test_render_pipeline.py
```

---

### Task 1: Project Setup — Dependencies & Entry Point

**Files:**
- Modify: `pyproject.toml`
- Modify: `include.mk`
- Modify: `.env.example`
- Modify: `src/config.py`
- Create: `src/renderer/__init__.py`

- [ ] **Step 1: Add renderer dependencies to pyproject.toml**

Add to `dependencies` list:
```toml
"astroalign>=2.6",
"colour-demosaicing>=0.2",
"Pillow>=10.0",
```

Add entry point:
```toml
[project.scripts]
nightcrawler = "src.main:main"
nightcrawler-render = "src.renderer.cli:main"
```

- [ ] **Step 2: Add render settings to config.py**

```python
# Add to Settings class
render_fps: int = 24
render_crf: int = 18
render_transition: str = "crossfade"
render_crossfade_frames: int = 6
```

- [ ] **Step 3: Add .env.example entries**

```
NC_RENDER_FPS=24
NC_RENDER_CRF=18
NC_RENDER_TRANSITION=crossfade
NC_RENDER_CROSSFADE_FRAMES=6
```

- [ ] **Step 4: Add run-render target to include.mk**

```makefile
.PHONY: run-render
run-render: $(INSTALL_TARGETS) .env
	python -c "from src.renderer.cli import main; main(['--ui'])"
```

- [ ] **Step 5: Create renderer package init**

`src/renderer/__init__.py`:
```python
"""Rendering module for converting FITS sequences to video."""
```

- [ ] **Step 6: Install updated dependencies**

Run: `uv pip install -e .` or `make install`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml include.mk .env.example src/config.py src/renderer/__init__.py
git commit -m "feat: renderer module setup — dependencies, config, entry point"
```

---

### Task 2: FITS Importer — Read Manifest & Load Frames

**Files:**
- Create: `src/renderer/importer.py`
- Create: `tests/test_renderer_import.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the renderer FITS importer."""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from src.models.project import CapturePoint, CaptureSettings, Project, SplinePath, ControlPoint
from src.renderer.importer import load_manifest, load_frame, FrameInfo


@pytest.fixture()
def capture_dir(tmp_path: Path) -> Path:
    """Create a minimal capture directory with manifest + FITS files."""
    # Create two small FITS files
    for i in range(3):
        data = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        hdu = fits.PrimaryHDU(data)
        hdu.header["BAYERPAT"] = "RGGB"
        hdu.header["EXPTIME"] = 5.0
        hdu.writeto(tmp_path / f"seq_{i+1:04d}_001.fits")

    # Create manifest
    project = Project(
        project="test",
        path=SplinePath(control_points=[
            ControlPoint(ra=10.0, dec=40.0),
            ControlPoint(ra=11.0, dec=41.0),
        ]),
        capture_settings=CaptureSettings(exposure_seconds=5.0),
        capture_points=[
            CapturePoint(ra=10.0, dec=40.0, index=0, status="captured",
                         files=["seq_0001_001.fits"]),
            CapturePoint(ra=10.5, dec=40.5, index=1, status="captured",
                         files=["seq_0002_001.fits"]),
            CapturePoint(ra=11.0, dec=41.0, index=2, status="skipped",
                         files=[]),
        ],
    )
    (tmp_path / "manifest.json").write_text(project.model_dump_json(indent=2))
    return tmp_path


class TestLoadManifest:
    def test_loads_captured_points(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert len(frames) == 2  # skipped point excluded

    def test_frame_order(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].index == 0
        assert frames[1].index == 1

    def test_resolves_fits_path(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].fits_path == capture_dir / "seq_0001_001.fits"
        assert frames[0].fits_path.exists()

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_manifest(tmp_path)


class TestLoadFrame:
    def test_returns_numpy_array(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        data = load_frame(frames[0])
        assert isinstance(data, np.ndarray)
        assert data.shape == (100, 100)
        assert data.dtype == np.uint16

    def test_reads_bayer_pattern(self, capture_dir: Path) -> None:
        frames = load_manifest(capture_dir)
        assert frames[0].bayer_pattern == "RGGB"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_renderer_import.py -v`

- [ ] **Step 3: Implement importer**

`src/renderer/importer.py`:
```python
"""Import FITS frames from a capture directory with manifest."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits

from src.models.project import Project

logger = logging.getLogger(__name__)


@dataclass
class FrameInfo:
    """Metadata for a single frame in the render sequence."""

    index: int
    fits_path: Path
    ra: float
    dec: float
    bayer_pattern: str | None = None
    exposure: float = 0.0
    skipped: bool = False


def load_manifest(capture_dir: Path) -> list[FrameInfo]:
    """Read manifest.json and return FrameInfo for captured points.

    Args:
        capture_dir: Directory containing manifest.json and FITS files.

    Returns:
        List of FrameInfo sorted by index, only captured points.
    """
    manifest_path = capture_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"No manifest.json in {capture_dir}"
        raise FileNotFoundError(msg)

    project = Project.model_validate_json(manifest_path.read_text())
    frames: list[FrameInfo] = []

    for point in project.capture_points:
        if point.status != "captured" or not point.files:
            continue
        fits_path = capture_dir / point.files[0]  # v1: first exposure only
        bayer = _read_bayer_pattern(fits_path)
        frames.append(FrameInfo(
            index=point.index,
            fits_path=fits_path,
            ra=point.ra,
            dec=point.dec,
            bayer_pattern=bayer,
            exposure=project.capture_settings.exposure_seconds,
        ))

    frames.sort(key=lambda f: f.index)
    logger.info("Loaded %d frames from %s", len(frames), capture_dir)
    return frames


def load_frame(frame: FrameInfo) -> np.ndarray:
    """Load FITS image data as a numpy array.

    Args:
        frame: Frame metadata with fits_path.

    Returns:
        2D numpy array (mono) or 3D (color, already debayered).
    """
    with fits.open(frame.fits_path, memmap=True) as hdul:
        return np.array(hdul[0].data)


def _read_bayer_pattern(fits_path: Path) -> str | None:
    """Read BAYERPAT from FITS header, or None if absent."""
    try:
        with fits.open(fits_path, memmap=True) as hdul:
            return hdul[0].header.get("BAYERPAT")
    except Exception:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_renderer_import.py -v`

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check --fix src/renderer/importer.py tests/test_renderer_import.py
git add src/renderer/ tests/test_renderer_import.py
git commit -m "feat: renderer FITS importer with manifest loading"
```

---

### Task 3: Debayering

**Files:**
- Create: `src/renderer/debayer.py`
- Create: `tests/test_debayer.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for Bayer pattern detection and demosaicing."""

import numpy as np
import pytest

from src.renderer.debayer import debayer_frame, detect_bayer, DebayerMode


class TestDetectBayer:
    def test_returns_pattern_from_header(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.AUTO) == "RGGB"

    def test_auto_mode_none_header_returns_none(self) -> None:
        assert detect_bayer(None, DebayerMode.AUTO) is None

    def test_override_ignores_header(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.GBRG) == "GBRG"

    def test_off_mode_returns_none(self) -> None:
        assert detect_bayer("RGGB", DebayerMode.OFF) is None


class TestDebayerFrame:
    def test_mono_passthrough(self) -> None:
        mono = np.zeros((100, 100), dtype=np.uint16)
        result = debayer_frame(mono, None)
        assert result.ndim == 2
        assert result.shape == (100, 100)

    def test_rggb_produces_color(self) -> None:
        raw = np.random.randint(0, 65535, (100, 100), dtype=np.uint16)
        result = debayer_frame(raw, "RGGB")
        assert result.ndim == 3
        assert result.shape[2] == 3  # RGB
        assert result.shape[0] == 100
        assert result.shape[1] == 100

    def test_already_color_passthrough(self) -> None:
        color = np.zeros((100, 100, 3), dtype=np.uint16)
        result = debayer_frame(color, None)
        assert result.ndim == 3
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement debayering**

`src/renderer/debayer.py`:
```python
"""Bayer pattern detection and demosaicing."""

from __future__ import annotations

import enum
import logging

import numpy as np

logger = logging.getLogger(__name__)


class DebayerMode(enum.Enum):
    """Debayer mode selection."""

    AUTO = "auto"
    OFF = "off"
    RGGB = "RGGB"
    GBRG = "GBRG"
    GRBG = "GRBG"
    BGGR = "BGGR"


def detect_bayer(
    header_pattern: str | None,
    mode: DebayerMode,
) -> str | None:
    """Determine the Bayer pattern to use.

    Args:
        header_pattern: Pattern from FITS BAYERPAT header.
        mode: User-selected debayer mode.

    Returns:
        Bayer pattern string, or None if no debayering needed.
    """
    if mode == DebayerMode.OFF:
        return None
    if mode == DebayerMode.AUTO:
        return header_pattern
    return mode.value


def debayer_frame(
    data: np.ndarray,
    pattern: str | None,
) -> np.ndarray:
    """Apply demosaicing to a raw Bayer frame.

    Args:
        data: 2D raw CFA array or 3D already-debayered array.
        pattern: Bayer pattern (RGGB, GBRG, etc.) or None for mono.

    Returns:
        2D mono array or 3D RGB array.
    """
    if data.ndim == 3:
        return data  # Already color

    if pattern is None:
        return data  # Mono, no debayering

    from colour_demosaicing import demosaicing_CFA_Bayer_bilinear

    logger.info("Debayering with pattern %s", pattern)
    rgb = demosaicing_CFA_Bayer_bilinear(data.astype(np.float64), pattern)
    return rgb.astype(data.dtype)
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_debayer.py -v
.venv/bin/ruff check --fix src/renderer/debayer.py tests/test_debayer.py
git add src/renderer/debayer.py tests/test_debayer.py
git commit -m "feat: Bayer detection and demosaicing"
```

---

### Task 4: Stretch / Tonmapping

**Files:**
- Create: `src/renderer/stretch.py`
- Create: `tests/test_stretch.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for FITS stretch / tonmapping."""

import numpy as np
import pytest

from src.renderer.stretch import (
    StretchParams,
    auto_stretch,
    manual_stretch,
    apply_stretch,
)


class TestAutoStretch:
    def test_returns_8bit_array(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.dtype == np.uint8
        assert result.shape == (100, 100)

    def test_output_uses_full_range(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.max() > 200
        assert result.min() < 50

    def test_color_image(self) -> None:
        data = np.random.randint(1000, 60000, (100, 100, 3), dtype=np.uint16)
        result = auto_stretch(data)
        assert result.dtype == np.uint8
        assert result.shape == (100, 100, 3)


class TestManualStretch:
    def test_custom_black_white(self) -> None:
        data = np.linspace(0, 65535, 256, dtype=np.uint16).reshape(16, 16)
        params = StretchParams(black=0.1, white=0.9, midtone=0.5)
        result = manual_stretch(data, params)
        assert result.dtype == np.uint8

    def test_mono_to_rgb_flag(self) -> None:
        data = np.zeros((16, 16), dtype=np.uint16)
        params = StretchParams()
        result = manual_stretch(data, params, mono_to_rgb=True)
        assert result.ndim == 3
        assert result.shape[2] == 3


class TestApplyStretch:
    def test_auto_mode(self) -> None:
        data = np.random.randint(0, 65535, (50, 50), dtype=np.uint16)
        result = apply_stretch(data, mode="auto")
        assert result.dtype == np.uint8

    def test_histogram_mode(self) -> None:
        data = np.random.randint(0, 65535, (50, 50), dtype=np.uint16)
        result = apply_stretch(data, mode="histogram")
        assert result.dtype == np.uint8
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement stretch**

`src/renderer/stretch.py`:
```python
"""FITS image stretch / tonmapping to visible 8-bit sRGB."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from astropy.visualization import AsinhStretch, ZScaleInterval

logger = logging.getLogger(__name__)


@dataclass
class StretchParams:
    """Manual stretch parameters."""

    black: float = 0.0
    white: float = 1.0
    midtone: float = 0.5


def auto_stretch(data: np.ndarray) -> np.ndarray:
    """Apply ZScale + AsinhStretch and return 8-bit result.

    Args:
        data: Input array (uint16, float, any shape).

    Returns:
        8-bit numpy array.
    """
    interval = ZScaleInterval()
    stretch = AsinhStretch()
    fdata = data.astype(np.float64)

    if fdata.ndim == 3:
        result = np.empty_like(fdata)
        for ch in range(fdata.shape[2]):
            vmin, vmax = interval.get_limits(fdata[:, :, ch])
            normed = np.clip((fdata[:, :, ch] - vmin) / (vmax - vmin + 1e-10), 0, 1)
            result[:, :, ch] = stretch(normed)
        return (result * 255).astype(np.uint8)

    vmin, vmax = interval.get_limits(fdata)
    normed = np.clip((fdata - vmin) / (vmax - vmin + 1e-10), 0, 1)
    stretched = stretch(normed)
    return (stretched * 255).astype(np.uint8)


def histogram_stretch(data: np.ndarray, low: float = 0.001, high: float = 0.999) -> np.ndarray:
    """Apply percentile-based histogram stretch.

    Args:
        data: Input array.
        low: Lower percentile cutoff.
        high: Upper percentile cutoff.

    Returns:
        8-bit numpy array.
    """
    fdata = data.astype(np.float64)
    vmin = np.percentile(fdata, low * 100)
    vmax = np.percentile(fdata, high * 100)
    normed = np.clip((fdata - vmin) / (vmax - vmin + 1e-10), 0, 1)
    return (normed * 255).astype(np.uint8)


def manual_stretch(
    data: np.ndarray,
    params: StretchParams,
    mono_to_rgb: bool = False,
) -> np.ndarray:
    """Apply manual stretch with user-defined parameters.

    Args:
        data: Input array.
        params: Black/white/midtone settings (0..1 range).
        mono_to_rgb: If True, replicate mono to 3-channel.

    Returns:
        8-bit numpy array.
    """
    fdata = data.astype(np.float64)
    dmax = np.iinfo(data.dtype).max if np.issubdtype(data.dtype, np.integer) else 1.0
    normed = fdata / dmax
    normed = np.clip((normed - params.black) / (params.white - params.black + 1e-10), 0, 1)
    # Midtone gamma correction
    gamma = 1.0 / (params.midtone + 0.01)
    normed = np.power(normed, gamma)
    result = (normed * 255).astype(np.uint8)

    if mono_to_rgb and result.ndim == 2:
        result = np.stack([result, result, result], axis=2)

    return result


def apply_stretch(
    data: np.ndarray,
    mode: str = "auto",
    params: StretchParams | None = None,
    mono_to_rgb: bool = False,
) -> np.ndarray:
    """Apply stretch based on mode selection.

    Args:
        data: Input FITS data.
        mode: "auto", "histogram", or "manual".
        params: Manual parameters (required if mode=="manual").
        mono_to_rgb: Convert mono to 3-channel.

    Returns:
        8-bit sRGB numpy array.
    """
    if mode == "auto":
        result = auto_stretch(data)
    elif mode == "histogram":
        result = histogram_stretch(data)
    elif mode == "manual" and params:
        result = manual_stretch(data, params)
    else:
        result = auto_stretch(data)

    if mono_to_rgb and result.ndim == 2:
        result = np.stack([result, result, result], axis=2)

    return result
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_stretch.py -v
.venv/bin/ruff check --fix src/renderer/stretch.py tests/test_stretch.py
git add src/renderer/stretch.py tests/test_stretch.py
git commit -m "feat: FITS stretch/tonmapping (auto, histogram, manual)"
```

---

### Task 5: Star Alignment

**Files:**
- Create: `src/renderer/alignment.py`
- Create: `tests/test_alignment.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for star alignment between frames."""

import numpy as np
import pytest

from src.renderer.alignment import align_pair, AlignmentResult, compute_crop_margins


class TestAlignPair:
    def test_identical_frames_zero_offset(self) -> None:
        frame = np.random.randint(0, 255, (200, 200), dtype=np.uint8)
        # Add some "stars" (bright points)
        for _ in range(20):
            x, y = np.random.randint(10, 190, 2)
            frame[y-1:y+2, x-1:x+2] = 255
        result = align_pair(frame, frame)
        assert abs(result.dx) < 1.0
        assert abs(result.dy) < 1.0
        assert result.success

    def test_shifted_frame(self) -> None:
        frame = np.zeros((200, 200), dtype=np.uint8)
        for _ in range(30):
            x, y = np.random.randint(20, 180, 2)
            frame[y-1:y+2, x-1:x+2] = 255
        shifted = np.roll(frame, 5, axis=1)  # shift 5px right
        result = align_pair(frame, shifted)
        assert result.success
        assert abs(result.dx - 5.0) < 2.0

    def test_failure_returns_identity(self) -> None:
        blank = np.zeros((50, 50), dtype=np.uint8)
        result = align_pair(blank, blank)
        assert not result.success
        assert result.dx == 0.0
        assert result.dy == 0.0


class TestComputeCropMargins:
    def test_no_shifts(self) -> None:
        results = [AlignmentResult(dx=0, dy=0, success=True)]
        mx, my = compute_crop_margins(results)
        assert mx == 0
        assert my == 0

    def test_bidirectional_shifts(self) -> None:
        results = [
            AlignmentResult(dx=5.0, dy=-3.0, success=True),
            AlignmentResult(dx=-2.0, dy=4.0, success=True),
        ]
        mx, my = compute_crop_margins(results)
        assert mx == 5  # max(abs(5), abs(-2))
        assert my == 4  # max(abs(-3), abs(4))
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement alignment**

`src/renderer/alignment.py`:
```python
"""Star alignment between adjacent frames using astroalign."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AlignmentResult:
    """Result of aligning two frames."""

    dx: float = 0.0
    dy: float = 0.0
    rotation: float = 0.0
    success: bool = False


def align_pair(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
) -> AlignmentResult:
    """Compute the alignment offset between two frames.

    Uses astroalign to find matching star triangles and compute
    the affine transformation.

    Args:
        frame_a: Reference frame (2D uint8 or uint16).
        frame_b: Target frame to align to frame_a.

    Returns:
        AlignmentResult with translation and rotation.
    """
    try:
        import astroalign as aa

        transform, _ = aa.find_transform(frame_b, frame_a)
        dx = transform.translation[0]
        dy = transform.translation[1]
        rotation = math.degrees(math.atan2(transform.params[1][0], transform.params[0][0]))
        logger.info("Alignment: dx=%.1f dy=%.1f rot=%.2f°", dx, dy, rotation)
        return AlignmentResult(dx=dx, dy=dy, rotation=rotation, success=True)
    except Exception as exc:
        logger.warning("Alignment failed: %s", exc)
        return AlignmentResult(success=False)


def compute_crop_margins(
    results: list[AlignmentResult],
) -> tuple[int, int]:
    """Compute crop margins from alignment results.

    Returns margins large enough to prevent black edges
    during any pairwise linear pan transition.

    Args:
        results: Alignment results for each adjacent pair.

    Returns:
        Tuple of (margin_x, margin_y) in pixels.
    """
    margin_x = 0.0
    margin_y = 0.0
    for r in results:
        margin_x = max(margin_x, abs(r.dx))
        margin_y = max(margin_y, abs(r.dy))
    return math.ceil(margin_x), math.ceil(margin_y)
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_alignment.py -v
.venv/bin/ruff check --fix src/renderer/alignment.py tests/test_alignment.py
git add src/renderer/alignment.py tests/test_alignment.py
git commit -m "feat: star alignment via astroalign with crop margin calculation"
```

---

### Task 6: Frame Transitions

**Files:**
- Create: `src/renderer/transitions.py`
- Create: `tests/test_transitions.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for frame transition generation."""

import numpy as np
import pytest

from src.renderer.transitions import crossfade, linear_pan
from src.renderer.alignment import AlignmentResult


class TestCrossfade:
    def test_produces_correct_count(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert len(frames) == 4

    def test_first_is_mostly_a(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert frames[0].mean() < 100

    def test_last_is_mostly_b(self) -> None:
        a = np.full((50, 50, 3), 0, dtype=np.uint8)
        b = np.full((50, 50, 3), 255, dtype=np.uint8)
        frames = crossfade(a, b, num_frames=4)
        assert frames[-1].mean() > 150


class TestLinearPan:
    def test_produces_correct_count(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5)
        assert len(frames) == 4

    def test_output_size_is_cropped(self) -> None:
        a = np.full((100, 100, 3), 128, dtype=np.uint8)
        b = np.full((100, 100, 3), 128, dtype=np.uint8)
        align = AlignmentResult(dx=5.0, dy=3.0, success=True)
        frames = linear_pan(a, b, align, num_frames=4, margin_x=5, margin_y=5)
        # Crop: 100 - 2*5 = 90 wide, 100 - 2*5 = 90 tall
        assert frames[0].shape == (90, 90, 3)
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement transitions**

`src/renderer/transitions.py`:
```python
"""Frame transition generation (crossfade and linear pan)."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import shift as ndimage_shift

from src.renderer.alignment import AlignmentResult


def crossfade(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    num_frames: int = 6,
) -> list[np.ndarray]:
    """Generate crossfade transition frames between two images.

    Args:
        frame_a: Starting frame (8-bit).
        frame_b: Ending frame (8-bit).
        num_frames: Number of intermediate frames.

    Returns:
        List of blended frames.
    """
    frames: list[np.ndarray] = []
    for i in range(num_frames):
        alpha = (i + 1) / (num_frames + 1)
        blended = (
            (1 - alpha) * frame_a.astype(np.float32)
            + alpha * frame_b.astype(np.float32)
        )
        frames.append(blended.astype(np.uint8))
    return frames


def linear_pan(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    alignment: AlignmentResult,
    num_frames: int = 6,
    margin_x: int = 0,
    margin_y: int = 0,
) -> list[np.ndarray]:
    """Generate linear pan transition with sub-pixel shifting.

    The crop window slides from frame_a's position to frame_b's
    position over num_frames intermediate frames.

    Args:
        frame_a: Starting frame (8-bit RGB).
        frame_b: Ending frame (8-bit RGB).
        alignment: Offset between the two frames.
        num_frames: Number of intermediate frames.
        margin_x: Horizontal crop margin in pixels.
        margin_y: Vertical crop margin in pixels.

    Returns:
        List of cropped, shifted frames.
    """
    h, w = frame_a.shape[:2]
    crop_h = h - 2 * margin_y
    crop_w = w - 2 * margin_x
    frames: list[np.ndarray] = []

    for i in range(num_frames):
        t = (i + 1) / (num_frames + 1)

        # Interpolate crop window position
        ox = margin_x + t * alignment.dx
        oy = margin_y + t * alignment.dy

        # Blend the two frames
        blended = (
            (1 - t) * frame_a.astype(np.float32)
            + t * frame_b.astype(np.float32)
        )

        # Sub-pixel shift
        shift_vec = [oy - margin_y, ox - margin_x]
        if blended.ndim == 3:
            shift_vec.append(0)
        shifted = ndimage_shift(blended, shift_vec, order=1)

        # Crop to safe area
        cropped = shifted[margin_y:margin_y + crop_h, margin_x:margin_x + crop_w]
        frames.append(cropped.astype(np.uint8))

    return frames
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_transitions.py -v
.venv/bin/ruff check --fix src/renderer/transitions.py tests/test_transitions.py
git add src/renderer/transitions.py tests/test_transitions.py
git commit -m "feat: crossfade and linear pan transitions"
```

---

### Task 7: Video Encoding (ffmpeg)

**Files:**
- Create: `src/renderer/video.py`
- Create: `tests/test_video.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for ffmpeg video encoding."""

import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.renderer.video import check_ffmpeg, write_frame_png, encode_video


class TestCheckFfmpeg:
    def test_returns_true_if_installed(self) -> None:
        # ffmpeg should be available in most dev environments
        result = check_ffmpeg()
        if shutil.which("ffmpeg"):
            assert result is True
        else:
            assert result is False


class TestWriteFramePng:
    def test_writes_png(self, tmp_path: Path) -> None:
        frame = np.full((50, 50, 3), 128, dtype=np.uint8)
        path = write_frame_png(frame, tmp_path, index=0)
        assert path.exists()
        assert path.suffix == ".png"
        img = Image.open(path)
        assert img.size == (50, 50)


class TestEncodeVideo:
    @pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
    def test_produces_mp4(self, tmp_path: Path) -> None:
        # Write some test frames
        for i in range(10):
            frame = np.full((50, 50, 3), i * 25, dtype=np.uint8)
            write_frame_png(frame, tmp_path, index=i)
        output = tmp_path / "test.mp4"
        encode_video(tmp_path, output, fps=10, crf=23)
        assert output.exists()
        assert output.stat().st_size > 100
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement video encoding**

`src/renderer/video.py`:
```python
"""FFmpeg video encoding from frame PNGs."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available on PATH."""
    return shutil.which("ffmpeg") is not None


def write_frame_png(
    frame: np.ndarray,
    output_dir: Path,
    index: int,
) -> Path:
    """Write a frame as a numbered PNG file.

    Args:
        frame: 8-bit RGB numpy array.
        output_dir: Directory to write to.
        index: Frame index (for filename ordering).

    Returns:
        Path to the written PNG file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"frame_{index:06d}.png"
    img = Image.fromarray(frame)
    img.save(path)
    return path


def encode_video(
    frames_dir: Path,
    output_path: Path,
    fps: int = 24,
    crf: int = 18,
) -> None:
    """Encode numbered PNGs to video via ffmpeg.

    Args:
        frames_dir: Directory containing frame_NNNNNN.png files.
        output_path: Output video file path.
        fps: Frames per second.
        crf: Constant rate factor (quality, lower=better).

    Raises:
        RuntimeError: If ffmpeg is not installed or encoding fails.
    """
    if not check_ffmpeg():
        msg = "ffmpeg not found. Install it to encode video."
        raise RuntimeError(msg)

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.png"),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg failed: %s", result.stderr[-500:])
        msg = f"ffmpeg encoding failed (exit {result.returncode})"
        raise RuntimeError(msg)
    logger.info("Video encoded: %s", output_path)
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_video.py -v
.venv/bin/ruff check --fix src/renderer/video.py tests/test_video.py
git add src/renderer/video.py tests/test_video.py
git commit -m "feat: ffmpeg video encoding from frame PNGs"
```

---

### Task 8: Rendering Pipeline — Orchestration

**Files:**
- Create: `src/renderer/pipeline.py`
- Create: `tests/test_render_pipeline.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the rendering pipeline orchestration."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

from src.models.project import (
    CapturePoint, CaptureSettings, ControlPoint, Project, SplinePath,
)
from src.renderer.pipeline import RenderPipeline, RenderConfig


@pytest.fixture()
def capture_dir(tmp_path: Path) -> Path:
    """Create capture dir with 5 small FITS frames."""
    for i in range(5):
        data = np.random.randint(100, 60000, (64, 64), dtype=np.uint16)
        # Add bright "stars" for alignment
        for _ in range(10):
            x, y = np.random.randint(5, 59, 2)
            data[y-1:y+2, x-1:x+2] = 65000
        hdu = fits.PrimaryHDU(data)
        hdu.writeto(tmp_path / f"seq_{i+1:04d}_001.fits")

    project = Project(
        project="test-render",
        path=SplinePath(control_points=[
            ControlPoint(ra=10.0, dec=40.0),
            ControlPoint(ra=15.0, dec=45.0),
        ]),
        capture_settings=CaptureSettings(exposure_seconds=5.0),
        capture_points=[
            CapturePoint(ra=10+i, dec=40+i, index=i, status="captured",
                         files=[f"seq_{i+1:04d}_001.fits"])
            for i in range(5)
        ],
    )
    (tmp_path / "manifest.json").write_text(project.model_dump_json(indent=2))
    return tmp_path


class TestRenderPipeline:
    def test_import_loads_frames(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        assert len(pipeline.frames) == 5

    def test_stretch_produces_8bit(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        stretched = pipeline.stretch_frame(0)
        assert stretched.dtype == np.uint8

    def test_skip_frame(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        pipeline.skip_frame(2)
        assert pipeline.frames[2].skipped

    def test_active_frames_excludes_skipped(self, capture_dir: Path) -> None:
        pipeline = RenderPipeline(capture_dir, RenderConfig())
        pipeline.load()
        pipeline.skip_frame(2)
        active = pipeline.active_frames()
        assert len(active) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement pipeline**

`src/renderer/pipeline.py`:
```python
"""Rendering pipeline orchestration."""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.renderer.alignment import AlignmentResult, align_pair, compute_crop_margins
from src.renderer.debayer import DebayerMode, debayer_frame, detect_bayer
from src.renderer.importer import FrameInfo, load_frame, load_manifest
from src.renderer.stretch import StretchParams, apply_stretch
from src.renderer.transitions import crossfade, linear_pan
from src.renderer.video import check_ffmpeg, encode_video, write_frame_png

logger = logging.getLogger(__name__)


@dataclass
class RenderConfig:
    """Configuration for a render job."""

    fps: int = 24
    crf: int = 18
    stretch_mode: str = "auto"
    stretch_params: StretchParams | None = None
    debayer_mode: DebayerMode = DebayerMode.AUTO
    transition: str = "crossfade"
    crossfade_frames: int = 6
    keep_frames: bool = False
    temp_dir: Path | None = None


class RenderPipeline:
    """Orchestrates the full rendering pipeline.

    Usage:
        pipeline = RenderPipeline(capture_dir, config)
        pipeline.load()
        pipeline.render(output_path)
    """

    def __init__(self, capture_dir: Path, config: RenderConfig) -> None:
        self.capture_dir = capture_dir
        self.config = config
        self.frames: list[FrameInfo] = []
        self._alignments: list[AlignmentResult] = []

    def load(self) -> None:
        """Load manifest and frame metadata."""
        self.frames = load_manifest(self.capture_dir)
        logger.info("Loaded %d frames", len(self.frames))

    def active_frames(self) -> list[FrameInfo]:
        """Return non-skipped frames."""
        return [f for f in self.frames if not f.skipped]

    def skip_frame(self, index: int) -> None:
        """Mark a frame as skipped."""
        for f in self.frames:
            if f.index == index:
                f.skipped = True
                return

    def stretch_frame(self, frame_idx: int) -> np.ndarray:
        """Load, debayer, and stretch a single frame.

        Args:
            frame_idx: Index into self.frames list.

        Returns:
            8-bit sRGB numpy array.
        """
        frame = self.frames[frame_idx]
        data = load_frame(frame)
        pattern = detect_bayer(frame.bayer_pattern, self.config.debayer_mode)
        debayered = debayer_frame(data, pattern)
        stretched = apply_stretch(
            debayered,
            mode=self.config.stretch_mode,
            params=self.config.stretch_params,
            mono_to_rgb=True,
        )
        return stretched

    def render(self, output_path: Path) -> None:
        """Run the full pipeline and produce a video file.

        Args:
            output_path: Path for the output video file.
        """
        if not check_ffmpeg():
            msg = "ffmpeg not found"
            raise RuntimeError(msg)

        active = self.active_frames()
        if len(active) < 2:
            msg = "Need at least 2 frames to render"
            raise RuntimeError(msg)

        temp = self._get_temp_dir()
        try:
            self._render_to_dir(active, temp)
            encode_video(temp, output_path, self.config.fps, self.config.crf)
        finally:
            if not self.config.keep_frames:
                shutil.rmtree(temp, ignore_errors=True)

    def _render_to_dir(self, active: list[FrameInfo], temp: Path) -> None:
        """Process all frames and write PNGs to temp directory."""
        frame_counter = 0

        # Stretch all active frames
        stretched: list[np.ndarray] = []
        for i, frame in enumerate(active):
            idx = self.frames.index(frame)
            logger.info("Processing frame %d/%d", i + 1, len(active))
            stretched.append(self.stretch_frame(idx))

        # Align if needed for linear pan
        margins = (0, 0)
        if self.config.transition == "linear-pan" and len(stretched) > 1:
            self._alignments = []
            for i in range(len(stretched) - 1):
                mono_a = _to_mono(stretched[i])
                mono_b = _to_mono(stretched[i + 1])
                self._alignments.append(align_pair(mono_a, mono_b))
            margins = compute_crop_margins(self._alignments)

        # Generate output frames with transitions
        for i in range(len(stretched)):
            frame = stretched[i]
            if margins != (0, 0):
                mx, my = margins
                frame = frame[my:frame.shape[0]-my, mx:frame.shape[1]-mx]
            write_frame_png(frame, temp, frame_counter)
            frame_counter += 1

            # Add transition frames between this and next
            if i < len(stretched) - 1:
                if self.config.transition == "crossfade":
                    trans = crossfade(stretched[i], stretched[i+1], self.config.crossfade_frames)
                elif self.config.transition == "linear-pan" and self._alignments:
                    trans = linear_pan(
                        stretched[i], stretched[i+1],
                        self._alignments[i],
                        self.config.crossfade_frames,
                        margins[0], margins[1],
                    )
                else:
                    trans = []

                for tf in trans:
                    write_frame_png(tf, temp, frame_counter)
                    frame_counter += 1

        logger.info("Wrote %d total frames to %s", frame_counter, temp)

    def _get_temp_dir(self) -> Path:
        """Get or create the temporary frame directory."""
        if self.config.temp_dir:
            self.config.temp_dir.mkdir(parents=True, exist_ok=True)
            return self.config.temp_dir
        return Path(tempfile.mkdtemp(prefix="nc-render-"))


def _to_mono(frame: np.ndarray) -> np.ndarray:
    """Convert RGB to mono for alignment."""
    if frame.ndim == 3:
        return np.mean(frame, axis=2).astype(np.uint8)
    return frame
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_render_pipeline.py -v
.venv/bin/ruff check --fix src/renderer/pipeline.py tests/test_render_pipeline.py
git add src/renderer/pipeline.py tests/test_render_pipeline.py
git commit -m "feat: rendering pipeline orchestration"
```

---

### Task 9: CLI Entry Point

**Files:**
- Create: `src/renderer/cli.py`

- [ ] **Step 1: Implement CLI**

`src/renderer/cli.py`:
```python
"""CLI entry point for the Nightcrawler renderer."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.config import settings
from src.renderer.debayer import DebayerMode
from src.renderer.pipeline import RenderConfig, RenderPipeline
from src.renderer.stretch import StretchParams


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(
        prog="nightcrawler-render",
        description="Render captured FITS sequences to video.",
    )
    p.add_argument("--input", "-i", required=True, type=Path, help="Capture directory")
    p.add_argument("--output", "-o", type=Path, default=Path("output.mp4"), help="Output video")
    p.add_argument("--fps", type=int, default=settings.render_fps)
    p.add_argument("--crf", type=int, default=settings.render_crf)
    p.add_argument("--stretch", choices=["auto", "histogram", "manual"], default="auto")
    p.add_argument("--black", type=float, default=0.0, help="Manual black point")
    p.add_argument("--white", type=float, default=1.0, help="Manual white point")
    p.add_argument("--midtone", type=float, default=0.5, help="Manual midtone")
    p.add_argument("--transition", choices=["none", "crossfade", "linear-pan"],
                   default=settings.render_transition)
    p.add_argument("--crossfade-frames", type=int, default=settings.render_crossfade_frames)
    p.add_argument("--debayer", choices=["auto", "off", "RGGB", "GBRG", "GRBG", "BGGR"],
                   default="auto")
    p.add_argument("--keep-frames", action="store_true", help="Keep intermediate PNGs")
    p.add_argument("--temp-dir", type=Path, default=None, help="Custom temp directory")
    p.add_argument("--ui", action="store_true", help="Start web UI instead of CLI render")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
    args = parse_args(argv)

    if args.ui:
        _start_ui()
        return

    debayer_map = {
        "auto": DebayerMode.AUTO, "off": DebayerMode.OFF,
        "RGGB": DebayerMode.RGGB, "GBRG": DebayerMode.GBRG,
        "GRBG": DebayerMode.GRBG, "BGGR": DebayerMode.BGGR,
    }

    stretch_params = None
    if args.stretch == "manual":
        stretch_params = StretchParams(black=args.black, white=args.white, midtone=args.midtone)

    config = RenderConfig(
        fps=args.fps,
        crf=args.crf,
        stretch_mode=args.stretch,
        stretch_params=stretch_params,
        debayer_mode=debayer_map[args.debayer],
        transition=args.transition,
        crossfade_frames=args.crossfade_frames,
        keep_frames=args.keep_frames,
        temp_dir=args.temp_dir,
    )

    pipeline = RenderPipeline(args.input, config)
    pipeline.load()
    print(f"Loaded {len(pipeline.frames)} frames from {args.input}")
    pipeline.render(args.output)
    print(f"Video saved to {args.output}")


def _start_ui() -> None:
    """Start the renderer web UI."""
    from src.renderer.ui.render_layout import start_render_ui
    start_render_ui()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test CLI manually**

```bash
# Create a test capture (if output/ has data)
nightcrawler-render --input ./output/deneb/ --output test.mp4 --transition crossfade
```

- [ ] **Step 3: Lint and commit**

```bash
.venv/bin/ruff check --fix src/renderer/cli.py
git add src/renderer/cli.py
git commit -m "feat: renderer CLI entry point"
```

---

### Task 10: Folder Browser Dialog

**Files:**
- Create: `src/ui/folder_browser.py`
- Create: `tests/test_folder_browser.py`

A reusable NiceGUI dialog for navigating the filesystem and selecting a directory. Shows folders and files, supports clicking into subfolders and navigating up with `..`. Highlights directories containing `manifest.json`.

- [ ] **Step 1: Write failing tests**

```python
"""Tests for the folder browser dialog."""

from pathlib import Path

import pytest

from src.ui.folder_browser import list_directory, DirectoryEntry


class TestListDirectory:
    def test_lists_subdirectories(self, tmp_path: Path) -> None:
        (tmp_path / "subdir1").mkdir()
        (tmp_path / "subdir2").mkdir()
        (tmp_path / "file.txt").write_text("x")
        entries = list_directory(tmp_path)
        dirs = [e for e in entries if e.is_dir]
        assert len(dirs) == 2

    def test_parent_entry(self, tmp_path: Path) -> None:
        child = tmp_path / "child"
        child.mkdir()
        entries = list_directory(child)
        assert entries[0].name == ".."
        assert entries[0].is_dir

    def test_marks_manifest_dirs(self, tmp_path: Path) -> None:
        seq_dir = tmp_path / "deneb"
        seq_dir.mkdir()
        (seq_dir / "manifest.json").write_text("{}")
        entries = list_directory(tmp_path)
        deneb = [e for e in entries if e.name == "deneb"][0]
        assert deneb.has_manifest

    def test_no_parent_at_root(self) -> None:
        entries = list_directory(Path("/"))
        names = [e.name for e in entries]
        assert ".." not in names
```

- [ ] **Step 2: Run tests to verify they fail**

- [ ] **Step 3: Implement folder browser**

`src/ui/folder_browser.py`:
```python
"""Reusable folder browser dialog for NiceGUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from nicegui import ui


@dataclass
class DirectoryEntry:
    """A single entry in a directory listing."""

    name: str
    path: Path
    is_dir: bool
    has_manifest: bool = False
    size: int = 0


def list_directory(path: Path) -> list[DirectoryEntry]:
    """List entries in a directory.

    Directories first (sorted), then files (sorted).
    Directories containing manifest.json are flagged.

    Args:
        path: Directory to list.

    Returns:
        List of DirectoryEntry, with '..' first if not root.
    """
    entries: list[DirectoryEntry] = []

    # Parent navigation (not at filesystem root)
    if path.parent != path:
        entries.append(DirectoryEntry(
            name="..", path=path.parent, is_dir=True,
        ))

    dirs: list[DirectoryEntry] = []
    files: list[DirectoryEntry] = []

    for item in sorted(path.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            has_manifest = (item / "manifest.json").exists()
            dirs.append(DirectoryEntry(
                name=item.name, path=item, is_dir=True,
                has_manifest=has_manifest,
            ))
        else:
            files.append(DirectoryEntry(
                name=item.name, path=item, is_dir=False,
                size=item.stat().st_size,
            ))

    return entries + dirs + files


class FolderBrowserDialog:
    """A dialog for navigating and selecting a directory.

    Usage:
        dialog = FolderBrowserDialog(on_select=my_callback)
        dialog.open(start_path)
    """

    def __init__(self, on_select: Callable[[Path], None]) -> None:
        """Init with selection callback.

        Args:
            on_select: Called with the selected directory Path.
        """
        self._on_select = on_select
        self._current: Path = Path.cwd()
        self._dialog: ui.dialog | None = None

    def open(self, start_path: Path | None = None) -> None:
        """Open the dialog at the given path."""
        self._current = start_path or Path.cwd()
        self._show()

    def _show(self) -> None:
        """Build and show the dialog."""
        if self._dialog:
            self._dialog.close()

        with ui.dialog() as self._dialog, ui.card().classes("w-96"):
            ui.label("Select Capture Directory").classes("text-lg font-bold")
            ui.label(str(self._current)).classes("text-xs text-grey break-all")
            ui.separator()

            entries = list_directory(self._current)
            with ui.column().classes("w-full max-h-80 overflow-y-auto gap-0"):
                for entry in entries:
                    self._render_entry(entry)

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("Cancel", on_click=self._dialog.close).props("flat")
                has_manifest = (self._current / "manifest.json").exists()
                select_btn = ui.button(
                    "Select",
                    on_click=lambda: self._select(),
                    color="green" if has_manifest else "primary",
                )
                if has_manifest:
                    select_btn.tooltip("Contains manifest.json")

        self._dialog.open()

    def _render_entry(self, entry: DirectoryEntry) -> None:
        """Render a single directory entry row."""
        icon = "folder" if entry.is_dir else "description"
        color = "text-green" if entry.has_manifest else ""

        with ui.row().classes(
            f"w-full items-center gap-2 px-2 py-1 cursor-pointer hover:bg-gray-800 {color}"
        ).on("click", lambda _, e=entry: self._on_click(e)):
            ui.icon(icon).classes("text-lg")
            ui.label(entry.name).classes("flex-grow")
            if entry.has_manifest:
                ui.badge("manifest", color="green").props("dense")
            elif not entry.is_dir:
                size_kb = entry.size // 1024
                ui.label(f"{size_kb} KB").classes("text-xs text-grey")

    def _on_click(self, entry: DirectoryEntry) -> None:
        """Handle click on an entry."""
        if entry.is_dir:
            self._current = entry.path
            if self._dialog:
                self._dialog.close()
            self._show()

    def _select(self) -> None:
        """Confirm selection of current directory."""
        if self._dialog:
            self._dialog.close()
        self._on_select(self._current)
```

- [ ] **Step 4: Run tests, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_folder_browser.py -v
.venv/bin/ruff check --fix src/ui/folder_browser.py tests/test_folder_browser.py
git add src/ui/folder_browser.py tests/test_folder_browser.py
git commit -m "feat: reusable folder browser dialog with manifest detection"
```

---

### Task 11: Web UI — Render Layout

**Files:**
- Create: `src/renderer/ui/__init__.py`
- Create: `src/renderer/ui/render_layout.py`

- [ ] **Step 1: Implement render UI**

`src/renderer/ui/__init__.py`:
```python
"""Renderer web UI."""
```

`src/renderer/ui/render_layout.py`:
```python
"""NiceGUI web UI for the Nightcrawler renderer."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

import numpy as np
from nicegui import app, ui
from PIL import Image

from src.config import settings
from src.renderer.debayer import DebayerMode
from src.renderer.pipeline import RenderConfig, RenderPipeline
from src.renderer.stretch import StretchParams

logger = logging.getLogger(__name__)


def start_render_ui() -> None:
    """Start the renderer as a standalone NiceGUI app."""
    import uvicorn
    from fastapi import FastAPI

    fapp = FastAPI(title="Nightcrawler Renderer")

    @ui.page("/")
    def index() -> None:
        create_render_layout()

    ui.run_with(fapp, title="Nightcrawler Renderer", dark=True, storage_secret="nc-render")
    uvicorn.run(fapp, host=settings.host, port=settings.port + 1)


def create_render_layout() -> None:
    """Build the renderer UI layout."""
    state = _RenderState()

    with ui.column().classes("w-full p-4 gap-4"):
        # Top bar
        with ui.row().classes("w-full items-center gap-2"):
            ui.input(label="Capture Directory", value="./output/").bind_value(state, "input_dir")
            def _browse() -> None:
                from src.ui.folder_browser import FolderBrowserDialog
                def _on_select(path: Path) -> None:
                    state.input_dir = str(path)
                    _load(state)
                FolderBrowserDialog(on_select=_on_select).open(Path(state.input_dir))
            ui.button("Browse", icon="folder_open", on_click=_browse)
            ui.button("Load", on_click=lambda: _load(state))
            ui.button("Render", icon="play_arrow", color="green",
                      on_click=lambda: _render(state))

        # Preview
        state.preview = ui.image().classes("w-full max-h-96 object-contain")

        # Stretch controls
        with ui.row().classes("w-full items-center gap-4"):
            ui.select(["auto", "histogram", "manual"], value="auto",
                      label="Stretch").bind_value(state, "stretch_mode")
            ui.slider(min=0.0, max=0.5, step=0.01, value=0.0).bind_value(state, "black") \
                .props("label")
            ui.label("Black")
            ui.slider(min=0.5, max=1.0, step=0.01, value=1.0).bind_value(state, "white") \
                .props("label")
            ui.label("White")
            ui.slider(min=0.1, max=2.0, step=0.1, value=0.5).bind_value(state, "midtone") \
                .props("label")
            ui.label("Midtone")

        # Filmstrip
        state.filmstrip = ui.row().classes("w-full overflow-x-auto gap-1 py-2")

        # Output settings
        with ui.row().classes("w-full items-center gap-4"):
            ui.select(["none", "crossfade", "linear-pan"], value="crossfade",
                      label="Transition").bind_value(state, "transition")
            ui.number(label="FPS", value=24, min=1, max=120).bind_value(state, "fps")
            ui.number(label="CRF", value=18, min=1, max=51).bind_value(state, "crf")
            ui.input(label="Output", value="output.mp4").bind_value(state, "output_path")

        # Progress
        state.progress = ui.linear_progress(value=0).classes("w-full")
        state.status_label = ui.label("")


class _RenderState:
    """Mutable state for the render UI."""

    def __init__(self) -> None:
        self.input_dir: str = "./output/"
        self.stretch_mode: str = "auto"
        self.black: float = 0.0
        self.white: float = 1.0
        self.midtone: float = 0.5
        self.transition: str = "crossfade"
        self.fps: int = 24
        self.crf: int = 18
        self.output_path: str = "output.mp4"
        self.pipeline: RenderPipeline | None = None
        self.preview: ui.image | None = None
        self.filmstrip: ui.row | None = None
        self.progress: ui.linear_progress | None = None
        self.status_label: ui.label | None = None
        self.selected_frame: int = 0


async def _load(state: _RenderState) -> None:
    """Load a capture directory."""
    capture_dir = Path(state.input_dir)
    config = RenderConfig(stretch_mode=state.stretch_mode)
    state.pipeline = RenderPipeline(capture_dir, config)
    state.pipeline.load()
    ui.notify(f"Loaded {len(state.pipeline.frames)} frames")
    _update_filmstrip(state)
    _show_preview(state, 0)


def _update_filmstrip(state: _RenderState) -> None:
    """Rebuild the filmstrip thumbnails."""
    if not state.pipeline or not state.filmstrip:
        return
    state.filmstrip.clear()
    with state.filmstrip:
        for i, frame in enumerate(state.pipeline.frames):
            idx = i  # capture for closure
            thumb = _make_thumbnail(state, i)
            if thumb:
                with ui.card().classes("cursor-pointer").on("click", lambda _, ii=idx: _show_preview(state, ii)):
                    ui.image(thumb).classes("w-16 h-16 object-cover")
                    ui.label(f"#{frame.index}").classes("text-xs text-center")


def _make_thumbnail(state: _RenderState, frame_idx: int) -> str | None:
    """Generate a base64 thumbnail for a frame."""
    if not state.pipeline:
        return None
    try:
        stretched = state.pipeline.stretch_frame(frame_idx)
        # Downscale for thumbnail
        img = Image.fromarray(stretched)
        img.thumbnail((128, 128))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:
        logger.warning("Thumbnail failed for frame %d: %s", frame_idx, exc)
        return None


def _show_preview(state: _RenderState, frame_idx: int) -> None:
    """Show a full-size preview of a frame."""
    if not state.pipeline or not state.preview:
        return
    state.selected_frame = frame_idx
    try:
        stretched = state.pipeline.stretch_frame(frame_idx)
        img = Image.fromarray(stretched)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()
        state.preview.set_source(f"data:image/jpeg;base64,{b64}")
    except Exception as exc:
        logger.error("Preview failed: %s", exc)


async def _render(state: _RenderState) -> None:
    """Run the full render pipeline."""
    if not state.pipeline:
        ui.notify("Load a capture directory first", type="warning")
        return

    config = RenderConfig(
        fps=int(state.fps),
        crf=int(state.crf),
        stretch_mode=state.stretch_mode,
        stretch_params=StretchParams(state.black, state.white, state.midtone)
        if state.stretch_mode == "manual" else None,
        transition=state.transition,
    )
    state.pipeline.config = config

    if state.status_label:
        state.status_label.text = "Rendering..."
    if state.progress:
        state.progress.value = 0.5

    try:
        output = Path(state.output_path)
        state.pipeline.render(output)
        ui.notify(f"Video saved: {output}", type="positive")
    except Exception as exc:
        ui.notify(f"Render failed: {exc}", type="negative")
    finally:
        if state.status_label:
            state.status_label.text = ""
        if state.progress:
            state.progress.value = 0
```

- [ ] **Step 2: Test manually**

```bash
nightcrawler-render --ui
# Open browser, load a capture directory, preview frames, render
```

- [ ] **Step 3: Lint and commit**

```bash
.venv/bin/ruff check --fix src/renderer/ui/
git add src/renderer/ui/
git commit -m "feat: renderer web UI with filmstrip, preview, and stretch controls"
```

---

### Task 12: Integration — Render Button in Planner App

**Files:**
- Modify: `src/ui/toolbar.py`

- [ ] **Step 1: Add Render button**

In `_render_action_tools()` in `toolbar.py`, add a "Render" button after "Start Capture":

```python
ui.button(
    "Render",
    icon="movie",
    on_click=self._action("open_render"),
    color="orange",
).tooltip("Open Renderer")
```

- [ ] **Step 2: Add callback in layout.py**

Wire the "open_render" callback to open the render UI in a new tab:

```python
async def on_open_render() -> None:
    ui.navigate.to(f"http://localhost:{settings.port + 1}", new_tab=True)

callbacks["open_render"] = on_open_render
```

- [ ] **Step 3: Lint and commit**

```bash
.venv/bin/ruff check --fix src/ui/toolbar.py src/ui/layout.py
git add src/ui/toolbar.py src/ui/layout.py
git commit -m "feat: Render button in planner toolbar"
```

---

## Self-Review Checklist

| Spec Requirement | Task |
|---|---|
| Import manifest + FITS | Task 2 |
| Debayering (auto + override) | Task 3 |
| Stretch (auto, histogram, manual) | Task 4 |
| 8-bit sRGB output, mono→RGB | Task 4 |
| Frame browser (filmstrip) | Task 10 |
| Frame skip/delete | Task 8 (pipeline.skip_frame) |
| Star alignment (astroalign) | Task 5 |
| Crossfade transition | Task 6 |
| Linear pan with cropping | Task 6 |
| Video encoding (ffmpeg) | Task 7 |
| CLI entry point | Task 9 |
| Web UI | Task 10 |
| Integration with Planner | Task 11 |
| Memory management (memmap, pairwise) | Task 2 (load_frame with memmap) |
| Multiple exposures (v1: first only) | Task 2 (files[0]) |
| Config in .env | Task 1 |
| Makefile targets | Task 1 |
| Dependencies | Task 1 |
