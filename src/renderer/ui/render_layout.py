"""NiceGUI web UI for the Nightcrawler renderer."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from nicegui import ui
from PIL import Image

from src.config import settings
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

    ui.run_with(
        fapp, title="Nightcrawler Renderer",
        dark=True, storage_secret="nc-render",
    )
    uvicorn.run(fapp, host=settings.host, port=settings.port + 1)


def create_render_layout() -> None:
    """Build the renderer UI layout."""
    state = _RenderState()

    with ui.column().classes("w-full p-4 gap-4"):
        _build_top_bar(state)
        state.preview = ui.image().classes("w-full max-h-96 object-contain")
        _build_stretch_controls(state)
        state.filmstrip = ui.row().classes(
            "w-full overflow-x-auto gap-1 py-2",
        )
        _build_output_settings(state)
        state.progress = ui.linear_progress(value=0).classes("w-full")
        state.status_label = ui.label("")


def _build_top_bar(state: _RenderState) -> None:
    """Build the top bar with browse, load, and render buttons.

    Args:
        state: Mutable render UI state.
    """
    with ui.row().classes("w-full items-center gap-2"):
        ui.input(
            label="Capture Directory", value="./output/",
        ).bind_value(state, "input_dir")

        def _browse() -> None:
            from src.ui.folder_browser import FolderBrowserDialog

            def _on_select(path: Path) -> None:
                state.input_dir = str(path)
                # Use ui.timer to trigger load in the correct NiceGUI context
                ui.timer(0.1, lambda: _load(state), once=True)

            FolderBrowserDialog(on_select=_on_select).open(
                Path(state.input_dir),
            )

        ui.button("Browse", icon="folder_open", on_click=_browse)
        ui.button("Load", on_click=lambda: _load(state))
        ui.button(
            "Render", icon="play_arrow", color="green",
            on_click=lambda: _render(state),
        )


def _build_stretch_controls(state: _RenderState) -> None:
    """Build stretch parameter sliders.

    Args:
        state: Mutable render UI state.
    """
    with ui.row().classes("w-full items-center gap-4"):
        ui.select(
            ["auto", "histogram", "manual"], value="auto",
            label="Stretch",
        ).bind_value(state, "stretch_mode")
        ui.slider(
            min=0.0, max=0.5, step=0.01, value=0.0,
        ).bind_value(state, "black").props("label")
        ui.label("Black")
        ui.slider(
            min=0.5, max=1.0, step=0.01, value=1.0,
        ).bind_value(state, "white").props("label")
        ui.label("White")
        ui.slider(
            min=0.1, max=2.0, step=0.1, value=0.5,
        ).bind_value(state, "midtone").props("label")
        ui.label("Midtone")


def _build_output_settings(state: _RenderState) -> None:
    """Build output format controls.

    Args:
        state: Mutable render UI state.
    """
    with ui.row().classes("w-full items-center gap-4"):
        ui.select(
            ["none", "crossfade", "linear-pan"], value="crossfade",
            label="Transition",
        ).bind_value(state, "transition")
        ui.number(
            label="FPS", value=24, min=1, max=120,
        ).bind_value(state, "fps")
        ui.number(
            label="CRF", value=18, min=1, max=51,
        ).bind_value(state, "crf")
        ui.select(
            ["native", "4k", "1440p", "1080p", "720p"],
            value="native", label="Resolution",
        ).bind_value(state, "resolution")
        ui.input(
            label="Output", value="output.mp4",
        ).bind_value(state, "output_path")

        def _browse_output() -> None:
            from src.ui.folder_browser import FolderBrowserDialog

            def _on_select(path: Path) -> None:
                current_name = Path(state.output_path).name or "output.mp4"
                state.output_path = str(path / current_name)

            start = Path(state.output_path).parent
            if not start.exists():
                start = Path.cwd()
            FolderBrowserDialog(on_select=_on_select).open(start)

        ui.button(
            "Browse", icon="folder_open", on_click=_browse_output,
        ).props("dense")

    # Advanced settings (collapsible)
    with ui.expansion("Advanced Settings", icon="settings").classes("w-full"), \
         ui.row().classes("w-full items-center gap-4"):
        ui.number(
            label="Frames/Transition", value=state.crossfade_frames,
            min=2, max=120, step=1,
        ).bind_value(state, "crossfade_frames").tooltip(
            "Interpolated frames between key frames",
        )
        ui.number(
            label="Align Max Dim (px)", value=state.align_max_dim,
            min=512, max=8192, step=256,
        ).bind_value(state, "align_max_dim").tooltip(
            "Alignment downsampling (higher=slower)",
        )
        ui.number(
            label="Align Sigma", value=state.align_sigma,
            min=0.5, max=10.0, step=0.5,
        ).bind_value(state, "align_sigma").tooltip(
            "Star detection sensitivity (lower=more)",
        )


class _RenderState:
    """Mutable state for the render UI."""

    def __init__(self) -> None:
        """Initialize default render state."""
        self.input_dir: str = "./output/"
        self.stretch_mode: str = "auto"
        self.black: float = 0.0
        self.white: float = 1.0
        self.midtone: float = 0.5
        self.transition: str = settings.render_transition
        self.fps: int = settings.render_fps
        self.crf: int = settings.render_crf
        self.crossfade_frames: int = settings.render_crossfade_frames
        self.align_max_dim: int = settings.render_align_max_dim
        self.align_sigma: float = settings.render_align_sigma
        self.resolution: str = settings.render_resolution
        self.output_path: str = "output.mp4"
        self.pipeline: RenderPipeline | None = None
        self.preview: ui.image | None = None
        self.filmstrip: ui.row | None = None
        self.progress: ui.linear_progress | None = None
        self.status_label: ui.label | None = None
        self.selected_frame: int = 0


async def _load(state: _RenderState) -> None:
    """Load a capture directory asynchronously.

    Args:
        state: Mutable render UI state.
    """
    import asyncio

    capture_dir = Path(state.input_dir)
    config = RenderConfig(stretch_mode=state.stretch_mode)
    state.pipeline = RenderPipeline(capture_dir, config)

    _set_render_status(state, "Loading manifest...", 0.1)
    await asyncio.to_thread(state.pipeline.load)

    n = len(state.pipeline.frames)
    ui.notify(f"Loaded {n} frames — generating thumbnails...")
    _set_render_status(state, f"Thumbnails 0/{n}...", 0.2)

    # Generate ALL thumbnails in background thread (data only, no UI)
    def _gen_all_thumbs() -> list[str | None]:
        thumbs: list[str | None] = []
        for i in range(n):
            thumbs.append(_make_thumbnail(state, i))
        return thumbs

    thumbnails = await asyncio.to_thread(_gen_all_thumbs)

    # Build filmstrip in UI thread (all at once)
    if state.filmstrip:
        state.filmstrip.clear()
        with state.filmstrip:
            for i, thumb in enumerate(thumbnails):
                if thumb:
                    _render_thumb_card(
                        state, i, state.pipeline.frames[i].index, thumb,
                    )

    _set_render_status(state, "", 0)
    _show_preview(state, 0)
    ui.notify(f"Ready — {n} frames loaded")


def _update_filmstrip(state: _RenderState) -> None:
    """Rebuild the filmstrip thumbnails.

    Args:
        state: Mutable render UI state.
    """
    if not state.pipeline or not state.filmstrip:
        return
    state.filmstrip.clear()
    with state.filmstrip:
        for i, frame in enumerate(state.pipeline.frames):
            idx = i  # capture for closure
            thumb = _make_thumbnail(state, i)
            if thumb:
                _render_thumb_card(state, idx, frame.index, thumb)


def _render_thumb_card(
    state: _RenderState,
    idx: int,
    frame_index: int,
    thumb: str,
) -> None:
    """Render a single filmstrip thumbnail card.

    Args:
        state: Mutable render UI state.
        idx: Index into pipeline frames list.
        frame_index: Capture point index for label.
        thumb: Base64 data URI for thumbnail image.
    """
    with ui.card().classes("cursor-pointer").on(
        "click", lambda _, ii=idx: _show_preview(state, ii),
    ):
        ui.image(thumb).classes("w-16 h-16 object-cover")
        ui.label(f"#{frame_index}").classes("text-xs text-center")


def _make_thumbnail(state: _RenderState, frame_idx: int) -> str | None:
    """Generate a base64 thumbnail for a frame.

    Args:
        state: Mutable render UI state.
        frame_idx: Index into pipeline frames list.

    Returns:
        Base64 data URI string, or None on failure.
    """
    if not state.pipeline:
        return None
    try:
        import numpy as np

        from src.renderer.importer import load_frame

        frame = state.pipeline.frames[frame_idx]
        data = load_frame(frame)
        # Quick downscale before expensive processing
        step = max(1, min(data.shape[0], data.shape[1]) // 64)
        small = data[::step, ::step]
        # Simple auto-stretch on the small version
        fdata = small.astype(np.float32)
        vmin, vmax = np.percentile(fdata, [1, 99])
        normed = np.clip((fdata - vmin) / (vmax - vmin + 1), 0, 1)
        rgb = (normed * 255).astype(np.uint8)
        if rgb.ndim == 2:
            rgb = np.stack([rgb, rgb, rgb], axis=2)
        img = Image.fromarray(rgb)
        img.thumbnail((64, 64))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        logger.warning("Thumbnail failed for frame %d", frame_idx)
        return None


def _show_preview(state: _RenderState, frame_idx: int) -> None:
    """Show a full-size preview of a frame.

    Args:
        state: Mutable render UI state.
        frame_idx: Index into pipeline frames list.
    """
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
    except Exception:
        logger.exception("Preview failed for frame %d", frame_idx)


async def _render(state: _RenderState) -> None:
    """Run the full render pipeline.

    Args:
        state: Mutable render UI state.
    """
    if not state.pipeline:
        ui.notify("Load a capture directory first", type="warning")
        return

    config = _build_render_config(state)
    state.pipeline.config = config
    import asyncio

    progress_state: dict[str, int] = {"current": 0, "total": 1}

    def on_progress(current: int, total: int) -> None:
        progress_state["current"] = current
        progress_state["total"] = total

    _set_render_status(state, "Rendering...", 0.0)
    timer = ui.timer(
        0.5,
        lambda: _update_render_progress(state, progress_state),
    )

    try:
        output = Path(state.output_path)
        await asyncio.to_thread(state.pipeline.render, output, on_progress)
        ui.notify(f"Video saved: {output}", type="positive")
    except Exception as exc:
        logger.exception("Render failed: %s", exc)
        ui.notify(f"Render failed: {exc}", type="negative")
    finally:
        timer.cancel()
        _set_render_status(state, "", 0)


def _update_render_progress(
    state: _RenderState,
    progress_state: dict[str, int],
) -> None:
    """Read shared progress state and update the UI.

    Args:
        state: Mutable render UI state.
        progress_state: Dict with 'current' and 'total' keys updated by
            the render thread.
    """
    total = progress_state["total"]
    current = progress_state["current"]
    if total > 0 and state.progress:
        state.progress.value = current / total
    if state.status_label:
        state.status_label.text = f"Rendering frame {current}/{total}..."


def _build_render_config(state: _RenderState) -> RenderConfig:
    """Build RenderConfig from current UI state.

    Args:
        state: Mutable render UI state.

    Returns:
        Configured RenderConfig.
    """
    stretch_params = None
    if state.stretch_mode == "manual":
        stretch_params = StretchParams(
            state.black, state.white, state.midtone,
        )
    # Apply alignment settings to global config before render
    settings.render_align_max_dim = int(state.align_max_dim)
    settings.render_align_sigma = float(state.align_sigma)

    return RenderConfig(
        fps=int(state.fps),
        crf=int(state.crf),
        stretch_mode=state.stretch_mode,
        stretch_params=stretch_params,
        transition=state.transition,
        crossfade_frames=int(state.crossfade_frames),
        resolution=state.resolution,
    )


def _set_render_status(
    state: _RenderState,
    text: str,
    progress: float,
) -> None:
    """Update the render status label and progress bar.

    Args:
        state: Mutable render UI state.
        text: Status text to display.
        progress: Progress bar value (0..1).
    """
    if state.status_label:
        state.status_label.text = text
    if state.progress:
        state.progress.value = progress
