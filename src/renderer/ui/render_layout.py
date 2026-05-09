"""NiceGUI web UI for the Nightcrawler renderer."""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from nicegui import app, ui
from PIL import Image

from src.config import settings
from src.renderer.pipeline import RenderConfig, RenderPipeline
from src.renderer.stretch import (
    AutoStretchParams,
    StretchParams,
    compute_auto_stretch_params,
    compute_histogram,
    derive_manual_params_from_auto,
    derive_manual_params_from_auto_then_identity,
    derive_manual_params_from_histogram,
)

# Number of histogram bins displayed in the widget.
_HIST_BINS = 256
# Throttle (ms) between Python-side handle_drag events emitted from JS.
# Live preview itself is debounced by ``_schedule_preview_refresh`` (150 ms),
# so a slightly tighter throttle here keeps the UI feeling responsive.
_DRAG_THROTTLE_MS = 50
# Maximum histogram x-axis zoom factor. At zoom=1 the chart spans [0, 1];
# at zoom=N the visible range shrinks to [0, 1/N], anchored at the left
# edge. 50x lets users zoom into the lowest 2% of the value range, which
# is typically where astro background + faint structure live.
_HIST_ZOOM_MAX = 50.0

logger = logging.getLogger(__name__)


def start_render_ui() -> None:
    """Start the renderer as a standalone NiceGUI app."""
    import uvicorn
    from fastapi import FastAPI

    fapp = FastAPI(title="Nightcrawler Renderer")

    @ui.page("/")
    def index() -> None:
        create_render_layout()

    # reconnect_timeout governs Socket.IO heartbeats in NiceGUI 3.x:
    #   ping_interval = max(reconnect_timeout * 0.8, 4)
    #   ping_timeout  = max(reconnect_timeout * 0.4, 2)
    # Renders include CPU-heavy phases (astroalign find_transform takes
    # 7-18s per pair, holding the GIL most of that time). With the default
    # of 3.0 the browser declares the WS dead after ~6s and auto-reloads
    # the page mid-render. 60s gives ~72s tolerance — comfortably above
    # any single GIL-held phase we expect.
    ui.run_with(
        fapp, title="Nightcrawler Renderer",
        dark=True, reconnect_timeout=60.0,
        storage_secret="nc-render",
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
    """Build mode select, interactive histogram widget, and numeric inputs.

    Args:
        state: Mutable render UI state.
    """
    def _on_param_change() -> None:
        # Skip side-effects while we're seeding values from auto-derive,
        # so a single mode switch doesn't fire 3 redundant refreshes.
        if state._seeding:
            return
        if not state.pipeline:
            return
        state.pipeline.config.stretch_mode = state.stretch_mode
        if state.stretch_mode in ("manual", "auto+manual"):
            state.pipeline.config.stretch_params = StretchParams(
                state.black, state.white, state.midtone,
            )
        # Repaint handles + tone curve immediately — no need to wait for
        # the (debounced) preview refresh to land.
        _refresh_histogram_overlay(state)
        _schedule_preview_refresh(state)

    def _on_mode_change() -> None:
        if not state.pipeline:
            return
        # The bound value (state.stretch_mode) is already the NEW mode;
        # config.stretch_mode is still the OLD one until we update it.
        prev_mode = state.pipeline.config.stretch_mode
        new_mode = state.stretch_mode

        # Seed manual params from the previous mode's stretch result when
        # transitioning INTO manual. This gives the user a starting point
        # that reproduces what they were just looking at.
        if new_mode == "manual" and prev_mode != "manual":
            try:
                debayered = state.pipeline.debayered_frame(state.selected_frame)
                if prev_mode == "histogram":
                    seeded = derive_manual_params_from_histogram(debayered)
                elif prev_mode == "auto":
                    seeded = derive_manual_params_from_auto(debayered)
                else:
                    seeded = derive_manual_params_from_auto(debayered)
                state._seeding = True
                try:
                    state.black = seeded.black
                    state.white = seeded.white
                    state.midtone = seeded.midtone
                finally:
                    state._seeding = False
            except Exception as exc:
                logger.warning(
                    "Could not seed manual params from %s-stretch: %s",
                    prev_mode, exc,
                )

        # Entering auto+manual: seed identity params so the user sees
        # the pure auto-stretch first and tunes from there.
        if new_mode == "auto+manual" and prev_mode != "auto+manual":
            import numpy as np
            seeded = derive_manual_params_from_auto_then_identity(
                np.empty(0),  # data is unused; kept for symmetry
            )
            state._seeding = True
            try:
                state.black = seeded.black
                state.white = seeded.white
                state.midtone = seeded.midtone
            finally:
                state._seeding = False

        state.pipeline.config.stretch_mode = new_mode
        if new_mode in ("manual", "auto+manual"):
            state.pipeline.config.stretch_params = StretchParams(
                state.black, state.white, state.midtone,
            )
        _refresh_histogram_overlay(state)
        # Mode switch may change the histogram source bucket
        # (raw <-> auto-stretched); ``_schedule_preview_refresh`` runs
        # ``_show_preview`` which fetches the histogram with the
        # matching kind for the new mode.
        _schedule_preview_refresh(state)

    with ui.column().classes("w-full gap-2"):
        with ui.row().classes("w-full items-center gap-4"):
            ui.select(
                options={
                    "auto": "auto",
                    "histogram": "histogram",
                    "manual": "manual",
                    "auto+manual": "Manual (linked to auto)",
                },
                value="histogram",
                label="Stretch", on_change=_on_mode_change,
            ).bind_value(state, "stretch_mode")
            ui.label(
                "Drag the B / W / M handles on the histogram below "
                "(active in manual modes).",
            ).classes("text-xs text-gray-400")
            ui.label(
                "Histogram shows the auto-stretched image — "
                "tune B/W/M on top of it.",
            ).classes("text-xs text-gray-400").bind_visibility_from(
                state, "stretch_mode", value="auto+manual",
            )

        _build_auto_freeze_controls(state)

        _build_histogram_widget(state, on_param_change=_on_param_change)

        with ui.row().classes("w-full items-center gap-4"):
            state.black_input = ui.number(
                label="Black", min=0.0, max=1.0, step=0.001,
                format="%.4f", on_change=_on_param_change,
            ).bind_value(state, "black").classes("w-28")
            state.white_input = ui.number(
                label="White", min=0.0, max=1.0, step=0.001,
                format="%.4f", on_change=_on_param_change,
            ).bind_value(state, "white").classes("w-28")
            state.midtone_input = ui.number(
                label="Midtone (gamma)", min=0.1, max=5.0, step=0.05,
                format="%.2f", on_change=_on_param_change,
            ).bind_value(state, "midtone").classes("w-32")

    # Wire the JS->Python bridge for handle drags. Registered exactly once
    # per page; the JS overlay emits ``handle_drag`` events with payload
    # ``{which: 'black'|'white'|'midtone', value: float}``.
    def _on_handle_drag_event(e) -> None:  # noqa: ANN001 — NiceGUI event arg
        args = e.args
        if isinstance(args, list) and args:
            args = args[0]
        if not isinstance(args, dict):
            return
        which = args.get("which")
        value = args.get("value")
        if which not in {"black", "white", "midtone"} or value is None:
            return
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            return
        _on_handle_drag(state, which, value_f)

    ui.on("histogram_handle_drag", _on_handle_drag_event)


def _build_auto_freeze_controls(state: _RenderState) -> None:
    """Build the auto-stretch freeze row (switch + reference indicator).

    The row sits between the stretch-mode dropdown and the histogram
    widget. It's only visible when the active mode is ``auto`` or
    ``auto+manual`` — in ``manual`` and ``histogram`` modes the freeze
    has no effect, so the controls would be confusing.

    Wiring:
      - The switch toggles ``state.auto_stretch_freeze``. Turning it on
        with no params yet computes them on the currently-selected
        frame (so the user gets immediate, sensible behavior).
      - The "Aktuelles Frame übernehmen" button recomputes the params
        from ``state.selected_frame`` and updates ``ref_frame``.
      - When the user navigates the filmstrip to a frame ≠ ref_frame
        the button highlights (primary color) — see issue #114 thread.

    Args:
        state: Mutable render UI state.
    """
    def _on_freeze_toggle() -> None:
        if not state.pipeline:
            return
        if state.auto_stretch_freeze and state.auto_stretch_params is None:
            try:
                data = state.pipeline.debayered_frame(state.selected_frame)
                state.auto_stretch_params = compute_auto_stretch_params(data)
                state.auto_stretch_ref_frame = state.selected_frame
            except Exception as exc:
                logger.warning(
                    "Could not compute auto-stretch params on toggle: %s", exc,
                )
        # Auto-stretched buckets in the cache are now stale (whether we
        # turned freeze on or off, the auto-stretched data changes).
        state.histogram_cache.clear()
        _update_ref_frame_indicator(state)
        _refresh_histogram(state)
        _schedule_preview_refresh(state)

    def _on_set_ref_frame() -> None:
        if not state.pipeline:
            return
        try:
            data = state.pipeline.debayered_frame(state.selected_frame)
            state.auto_stretch_params = compute_auto_stretch_params(data)
            state.auto_stretch_ref_frame = state.selected_frame
        except Exception as exc:
            logger.warning(
                "Could not compute auto-stretch params from frame %d: %s",
                state.selected_frame, exc,
            )
            return
        state.histogram_cache.clear()
        _update_ref_frame_indicator(state)
        _refresh_histogram(state)
        _schedule_preview_refresh(state)

    row = ui.row().classes("w-full items-center gap-3")
    state.auto_stretch_freeze_row = row
    with row:
        ui.switch("Auto-Stretch einfrieren").bind_value(
            state, "auto_stretch_freeze",
        ).on_value_change(lambda _e: _on_freeze_toggle()).tooltip(
            "Verwendet die ZScale-Parameter eines Referenz-Frames für "
            "alle Frames im Render. Verhindert Helligkeits-Flackern "
            "und liefert WYSIWYG (Preview = Render).",
        )
        state.auto_stretch_ref_label = ui.label(
            "Referenz: —",
        ).classes("text-xs text-gray-400")
        state.auto_stretch_apply_button = ui.button(
            "Aktuelles Frame übernehmen",
            on_click=lambda: _on_set_ref_frame(),
        ).props("dense color=grey-7")

    # Visible only in auto / auto+manual modes — the freeze has no
    # effect in manual/histogram modes. ``bind_visibility_from`` with a
    # custom predicate handles the OR cleanly.
    row.bind_visibility_from(
        state, "stretch_mode",
        backward=lambda mode: mode in ("auto", "auto+manual"),
    )


def _update_ref_frame_indicator(state: _RenderState) -> None:
    """Sync the reference-frame label and apply-button highlight.

    Called whenever the reference frame changes (toggle, button click)
    OR the selected frame changes (filmstrip navigation). The button
    highlight communicates "the reference is stale, you might want to
    update it" — primary color when ``selected_frame != ref_frame``,
    de-emphasized grey otherwise.
    """
    if state.auto_stretch_ref_label is not None:
        ref = state.auto_stretch_ref_frame
        try:
            state.auto_stretch_ref_label.text = (
                f"Referenz: Frame {ref}" if ref is not None else "Referenz: —"
            )
        except RuntimeError:
            # Client gone — safe to skip.
            pass
    if state.auto_stretch_apply_button is not None:
        is_stale = (
            state.auto_stretch_ref_frame is not None
            and state.auto_stretch_ref_frame != state.selected_frame
        )
        try:
            if is_stale:
                state.auto_stretch_apply_button.props(
                    "dense color=primary",
                )
            else:
                state.auto_stretch_apply_button.props(
                    "dense color=grey-7",
                )
        except RuntimeError:
            pass


def _build_histogram_widget(
    state: _RenderState,
    *,
    on_param_change,  # noqa: ANN001 — keep callable typing minimal
) -> None:
    """Render the ECharts histogram + draggable HTML handle overlay.

    Layout: a relatively-positioned container hosts the ECharts canvas
    (filling it) and an absolutely-positioned overlay with three drag
    handles (black, white) and a midtone marker on a thin track below.

    Args:
        state: Mutable render UI state.
        on_param_change: Callback invoked indirectly by the drag handler;
            kept here as documentation — the active wiring is via
            ``ui.on('histogram_handle_drag', ...)`` set up in
            ``_build_stretch_controls``.
    """
    del on_param_change  # only used implicitly via ui.on registration

    # Unique id so multiple instances on the same page wouldn't collide.
    overlay_id = f"hist-overlay-{id(state)}"
    state.histogram_overlay_id = overlay_id

    # Initial empty / placeholder data — replaced on the first
    # _refresh_histogram call once a frame is loaded.
    initial_options = _build_echart_options(
        log_counts=None,
        black=state.black,
        white=state.white,
        midtone=state.midtone,
        zoom=state.histogram_zoom,
    )

    # Zoom slider sits above the chart. We expose an internal "slider"
    # value in [0, 1] and map it exponentially to a zoom factor so the
    # 1x..2x range gets as much travel as the 25x..50x range:
    #   zoom = 10 ** (slider * log10(_HIST_ZOOM_MAX))
    # That makes mid-slider ≈ sqrt(_HIST_ZOOM_MAX) ≈ 7x for max=50.
    import math

    log_zoom_max = math.log10(_HIST_ZOOM_MAX)

    def _slider_to_zoom(s: float) -> float:
        return float(10 ** (max(0.0, min(1.0, s)) * log_zoom_max))

    def _zoom_to_slider(z: float) -> float:
        z = max(1.0, min(_HIST_ZOOM_MAX, float(z)))
        return float(math.log10(z) / log_zoom_max) if log_zoom_max > 0 else 0.0

    # Use a tiny mutable container to pass the slider value through the
    # bind without adding a separate state field (we already have
    # state.histogram_zoom for the canonical zoom factor).
    zoom_slider_holder: dict = {"slider": _zoom_to_slider(state.histogram_zoom)}

    def _on_zoom_slider_change(e) -> None:  # noqa: ANN001
        slider_val = float(e.value) if e.value is not None else 0.0
        zoom_slider_holder["slider"] = slider_val
        state.histogram_zoom = _slider_to_zoom(slider_val)
        _on_zoom_change(state)

    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Zoom").classes("text-xs text-gray-400 w-10")
        ui.slider(
            min=0.0, max=1.0, step=0.005,
            value=zoom_slider_holder["slider"],
            on_change=_on_zoom_slider_change,
        ).props("dense").classes("flex-grow")
        zoom_label = ui.label(
            f"{state.histogram_zoom:.1f}x",
        ).classes("text-xs text-gray-400 w-12 text-right")
        state.histogram_zoom_label = zoom_label

    # Stack chart + overlay. The chart fills the container; the overlay
    # sits on top with pointer-events disabled by default — only the
    # individual handles re-enable pointer events for themselves.
    with ui.element("div").classes(
        "relative w-full",
    ).style("height: 220px"):
        state.histogram_chart = ui.echart(initial_options).classes(
            "absolute inset-0 w-full h-full",
        )
        # Overlay container — we render the handles inside via raw HTML
        # so the JS pointerdown/move/up wiring is straightforward.
        with ui.element("div").props(f'id={overlay_id}').classes(
            "absolute inset-0 pointer-events-none",
        ):
            ui.html(_overlay_html()).classes("w-full h-full")

    ui.add_body_html(_overlay_script(overlay_id))


def _overlay_html() -> str:
    """Static HTML for the three drag handles + midtone track."""
    # Heights chosen so:
    #   - main histogram area takes top ~85%
    #   - midtone track sits at the bottom ~15%
    # Handles are 24px wide (tap-friendly). The visible bar is the inner
    # 4 px line — the wider transparent area is the hit target.
    return (
        '<div class="hist-handles" '
        'style="position:absolute; inset:0;">'

        # Histogram main area (top portion) where black & white live.
        '<div class="hist-main" '
        'style="position:absolute; left:0; right:0; top:0; bottom:30px;">'

        '<div class="hist-handle" data-which="black" '
        'style="position:absolute; top:0; bottom:0; width:24px; '
        'transform: translateX(-12px); cursor: ew-resize; '
        'pointer-events: auto; touch-action: none;">'
        '<div style="position:absolute; left:11px; top:0; width:2px; '
        'height:100%; background:#1ea1ff; opacity:0.85;"></div>'
        '<div style="position:absolute; left:50%; top:6px; '
        'transform:translateX(-50%); width:14px; height:14px; '
        'background:#1ea1ff; border-radius:50%; border:2px solid #fff; '
        'box-shadow:0 0 4px rgba(0,0,0,0.6);"></div>'
        '<div style="position:absolute; left:50%; bottom:4px; '
        'transform:translateX(-50%); color:#1ea1ff; font-size:10px; '
        'font-weight:600; user-select:none;">B</div>'
        '</div>'

        '<div class="hist-handle" data-which="white" '
        'style="position:absolute; top:0; bottom:0; width:24px; '
        'transform: translateX(-12px); cursor: ew-resize; '
        'pointer-events: auto; touch-action: none;">'
        '<div style="position:absolute; left:11px; top:0; width:2px; '
        'height:100%; background:#ffd24a; opacity:0.85;"></div>'
        '<div style="position:absolute; left:50%; top:6px; '
        'transform:translateX(-50%); width:14px; height:14px; '
        'background:#ffd24a; border-radius:50%; border:2px solid #fff; '
        'box-shadow:0 0 4px rgba(0,0,0,0.6);"></div>'
        '<div style="position:absolute; left:50%; bottom:4px; '
        'transform:translateX(-50%); color:#ffd24a; font-size:10px; '
        'font-weight:600; user-select:none;">W</div>'
        '</div>'

        '</div>'  # /hist-main

        # Midtone track (bottom thin row, scale 0.1..5).
        '<div class="hist-mid-track" '
        'style="position:absolute; left:0; right:0; bottom:0; height:26px; '
        'border-top:1px solid rgba(255,255,255,0.1);">'
        '<div style="position:absolute; left:30px; right:10px; '
        'top:50%; height:2px; transform:translateY(-50%); '
        'background:rgba(255,255,255,0.18);"></div>'
        '<div class="hist-handle" data-which="midtone" '
        'style="position:absolute; top:0; bottom:0; width:24px; '
        'transform: translateX(-12px); cursor: ew-resize; '
        'pointer-events: auto; touch-action: none;">'
        '<div style="position:absolute; left:50%; top:50%; '
        'transform:translate(-50%, -50%); width:12px; height:12px; '
        'background:#9eff9e; border-radius:3px; border:2px solid #fff; '
        'box-shadow:0 0 4px rgba(0,0,0,0.6);"></div>'
        '<div style="position:absolute; left:50%; bottom:-2px; '
        'transform:translateX(-50%); color:#9eff9e; font-size:10px; '
        'font-weight:600; user-select:none;">M</div>'
        '</div>'
        '</div>'  # /hist-mid-track

        '</div>'
    )


def _overlay_script(overlay_id: str) -> str:
    """JS that wires up pointer events on the three handles.

    The script mounts itself idempotently on overlay_id and:
      - reads handle positions from window.__histState[overlay_id]
        (Python pushes updates here via ``run_javascript``);
      - on pointer drag, computes the normalized x in [0, 1] (or the
        midtone gamma in [0.1, 5]) and calls ``emitEvent``.
    """
    # ECharts grid left/right padding must match the values passed to
    # _build_echart_options below — otherwise the handle x-positions
    # would not align with the histogram x-axis.
    grid_left_px = 30
    grid_right_px = 10
    throttle_ms = _DRAG_THROTTLE_MS
    return f"""<script>
(function() {{
  const OVERLAY_ID = {overlay_id!r};
  const GRID_LEFT = {grid_left_px};
  const GRID_RIGHT = {grid_right_px};
  const THROTTLE = {throttle_ms};
  const MID_MIN = 0.1;
  const MID_MAX = 5.0;

  if (!window.__histState) window.__histState = {{}};
  const state = window.__histState[OVERLAY_ID] = (
    window.__histState[OVERLAY_ID]
    || {{black: 0.0, white: 1.0, midtone: 1.0, zoom: 1.0}}
  );
  // Older sessions may have pre-zoom state cached on window — keep them
  // working by defaulting zoom to 1.0 if missing.
  if (typeof state.zoom !== 'number' || !(state.zoom >= 1.0)) {{
    state.zoom = 1.0;
  }}

  function clamp(v, lo, hi) {{
    return Math.max(lo, Math.min(hi, v));
  }}

  function visibleMax() {{
    return 1.0 / Math.max(1.0, state.zoom || 1.0);
  }}

  function plotRect(overlay) {{
    // The ECharts canvas fills the same container as the overlay;
    // we just inset by GRID_LEFT/GRID_RIGHT to match the grid box.
    const r = overlay.getBoundingClientRect();
    const left = r.left + GRID_LEFT;
    const right = r.right - GRID_RIGHT;
    return {{left, right, width: Math.max(1, right - left)}};
  }}

  function applyPositions() {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) return;
    const main = overlay.querySelector('.hist-main');
    const mid = overlay.querySelector('.hist-mid-track');
    if (!main || !mid) return;
    const mainRect = main.getBoundingClientRect();
    const midRect = mid.getBoundingClientRect();
    const mainW = Math.max(1, mainRect.width - GRID_LEFT - GRID_RIGHT);
    const midW = Math.max(1, midRect.width - GRID_LEFT - GRID_RIGHT);
    const vmax = visibleMax();
    // Place a black/white handle. If the canonical value is outside the
    // visible window [0, vmax], pin the handle at the right edge and
    // mark it 'out-of-range' so styling can hint that it's offscreen.
    const setBwX = (which, value) => {{
      const el = overlay.querySelector(
        '.hist-handle[data-which="' + which + '"]'
      );
      if (!el) return;
      const v = clamp(value, 0, 1);
      const offscreen = v > vmax + 1e-9;
      const visV = Math.min(v, vmax);
      // visV / vmax maps the value to a [0, 1] fraction of the visible
      // range; multiplying by mainW gives pixels inside the plot box.
      const x = GRID_LEFT + (visV / Math.max(vmax, 1e-9)) * mainW;
      el.style.left = x + 'px';
      if (offscreen) {{
        el.classList.add('out-of-range');
        el.style.opacity = '0.45';
      }} else {{
        el.classList.remove('out-of-range');
        el.style.opacity = '';
      }}
    }};
    setBwX('black', state.black);
    setBwX('white', state.white);
    // Midtone is on its own scale (gamma 0.1..5) — independent of the
    // histogram x-axis zoom.
    const midNorm = clamp(
      (state.midtone - MID_MIN) / (MID_MAX - MID_MIN), 0, 1,
    );
    const midEl = overlay.querySelector(
      '.hist-handle[data-which="midtone"]'
    );
    if (midEl) {{
      midEl.style.left = (GRID_LEFT + midNorm * midW) + 'px';
    }}
  }}

  // Expose so Python can push fresh values on bind/seed.
  window.__histApplyPositions = window.__histApplyPositions || {{}};
  window.__histApplyPositions[OVERLAY_ID] = function(b, w, m) {{
    state.black = b; state.white = w; state.midtone = m;
    applyPositions();
  }};

  // Keep handle positions correct on resize.
  window.addEventListener('resize', applyPositions);

  function attach() {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) {{ setTimeout(attach, 50); return; }}
    if (overlay.__bound) {{ applyPositions(); return; }}
    overlay.__bound = true;

    let activeHandle = null;
    let lastEmit = 0;
    let lastPayload = null;

    function emit(payload) {{
      lastPayload = payload;
      const now = performance.now();
      if (now - lastEmit < THROTTLE) return;
      lastEmit = now;
      try {{
        if (window.emitEvent) {{
          window.emitEvent('histogram_handle_drag', payload);
        }}
      }} catch (e) {{ console.warn('emitEvent failed', e); }}
    }}

    function flushPending() {{
      if (lastPayload) {{
        try {{
          if (window.emitEvent) {{
            window.emitEvent('histogram_handle_drag', lastPayload);
          }}
        }} catch (e) {{}}
        lastPayload = null;
      }}
    }}

    function onMove(ev) {{
      if (!activeHandle) return;
      ev.preventDefault();
      const which = activeHandle.dataset.which;
      const isMidtone = which === 'midtone';
      const parent = isMidtone
        ? overlay.querySelector('.hist-mid-track')
        : overlay.querySelector('.hist-main');
      if (!parent) return;
      const r = parent.getBoundingClientRect();
      const innerLeft = r.left + GRID_LEFT;
      const innerW = Math.max(1, r.width - GRID_LEFT - GRID_RIGHT);
      const norm = clamp((ev.clientX - innerLeft) / innerW, 0, 1);

      if (isMidtone) {{
        const value = MID_MIN + norm * (MID_MAX - MID_MIN);
        state.midtone = value;
        emit({{which: 'midtone', value: value}});
      }} else {{
        // norm is the [0, 1] fraction of the visible plot area; scale
        // by the visible max (= 1/zoom) to recover the canonical value.
        const vmax = visibleMax();
        let value = clamp(norm * vmax, 0, 1);
        if (which === 'black') {{
          value = clamp(value, 0, state.white - 0.001);
          state.black = value;
        }} else {{
          value = clamp(value, state.black + 0.001, 1);
          state.white = value;
        }}
        emit({{which, value}});
      }}
      applyPositions();
    }}

    function onUp(ev) {{
      if (!activeHandle) return;
      try {{ activeHandle.releasePointerCapture(ev.pointerId); }} catch (e) {{}}
      activeHandle = null;
      flushPending();
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
      document.removeEventListener('pointercancel', onUp);
    }}

    overlay.querySelectorAll('.hist-handle').forEach((h) => {{
      h.addEventListener('pointerdown', (ev) => {{
        ev.preventDefault();
        activeHandle = h;
        try {{ h.setPointerCapture(ev.pointerId); }} catch (e) {{}}
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp);
        document.addEventListener('pointercancel', onUp);
      }});
    }});

    applyPositions();
  }}

  // DOM may not be ready yet (script injected via ui.add_body_html
  // before the overlay div is mounted by NiceGUI's diff). Poll briefly.
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', attach);
  }} else {{
    attach();
  }}
}})();
</script>"""


def _build_echart_options(
    log_counts,  # noqa: ANN001 — np.ndarray | None, kept untyped to avoid import
    *,
    black: float,
    white: float,
    midtone: float,
    zoom: float = 1.0,
) -> dict:
    """Construct an ECharts options dict for the histogram + tone curve.

    The chart uses two y-axes: index 0 is the (log) histogram counts;
    index 1 is the linear 0..1 tone-curve mapping. Bins are plotted as
    ``[edge_x, log_count]`` pairs so the line is independent of bin
    count.

    Args:
        log_counts: ``(bins,)`` or ``(bins, 3)`` log10(count+1) array,
            or ``None`` to draw an empty placeholder.
        black: Current black point in [0, 1] (drawn as markLine).
        white: Current white point in [0, 1] (drawn as markLine).
        midtone: Current midtone gamma — used only for the tone curve.
        zoom: x-axis zoom factor; visible x-range is ``[0, 1/zoom]``.
            Bin/curve data is unchanged — ECharts simply clips it.

    Returns:
        ECharts options dict ready for ``ui.echart``.
    """
    series: list[dict] = []
    if log_counts is None:
        # Placeholder series so ECharts doesn't complain about empty data.
        series.append({
            "name": "histogram",
            "type": "line",
            "data": [[0.0, 0.0], [1.0, 0.0]],
            "showSymbol": False,
            "lineStyle": {"width": 1, "color": "#888"},
            "areaStyle": {"opacity": 0.3, "color": "#888"},
            "yAxisIndex": 0,
            "animation": False,
        })
    else:
        edges_x = [i / _HIST_BINS for i in range(_HIST_BINS)]
        if log_counts.ndim == 2:
            colors = ["#ff5555", "#55ff55", "#5588ff"]
            names = ["R", "G", "B"]
            for ch in range(log_counts.shape[1]):
                ch_data = [
                    [edges_x[i], float(log_counts[i, ch])]
                    for i in range(_HIST_BINS)
                ]
                series.append({
                    "name": names[ch],
                    "type": "line",
                    "data": ch_data,
                    "showSymbol": False,
                    "lineStyle": {"width": 1, "color": colors[ch]},
                    "areaStyle": {"opacity": 0.18, "color": colors[ch]},
                    "yAxisIndex": 0,
                    "animation": False,
                })
        else:
            mono_data = [
                [edges_x[i], float(log_counts[i])]
                for i in range(_HIST_BINS)
            ]
            series.append({
                "name": "histogram",
                "type": "line",
                "data": mono_data,
                "showSymbol": False,
                "lineStyle": {"width": 1, "color": "#bbbbbb"},
                "areaStyle": {"opacity": 0.3, "color": "#bbbbbb"},
                "yAxisIndex": 0,
                "animation": False,
            })

    # Tone-curve series — 64 samples of the manual_stretch mapping
    # ((x - black)/(white - black))^(1/(midtone+0.01)) on yAxisIndex 1.
    series.append({
        "name": "curve",
        "type": "line",
        "data": _tone_curve_points(black=black, white=white, midtone=midtone),
        "showSymbol": False,
        "lineStyle": {"width": 1.5, "color": "#ffffff", "opacity": 0.7},
        "yAxisIndex": 1,
        "z": 5,
        "animation": False,
    })

    visible_max = 1.0 / max(zoom, 1.0)
    return {
        "grid": {"left": 30, "right": 10, "top": 8, "bottom": 30},
        "xAxis": {
            "type": "value", "min": 0, "max": visible_max,
            "axisLabel": {"fontSize": 9, "color": "#aaa"},
            "splitLine": {"show": False},
        },
        "yAxis": [
            {
                "type": "value",
                "show": False,
                "min": 0,
            },
            {
                "type": "value",
                "show": False,
                "min": 0, "max": 1,
            },
        ],
        "tooltip": {"show": False},
        "legend": {"show": False},
        "animation": False,
        "series": series,
    }


def _tone_curve_points(
    *, black: float, white: float, midtone: float, n: int = 64,
) -> list[list[float]]:
    """Sample the manual stretch tone curve as ``[x, y]`` pairs.

    The mapping mirrors :func:`src.renderer.stretch.manual_stretch`:
    clip ``(x - black) / (white - black)`` to ``[0, 1]`` then raise to
    ``1 / (midtone + 0.01)``.
    """
    if white <= black:
        white = black + 1e-3
    gamma = 1.0 / (max(midtone, 0.0) + 0.01)
    span = white - black
    points: list[list[float]] = []
    for i in range(n + 1):
        x = i / n
        if x <= black:
            y = 0.0
        elif x >= white:
            y = 1.0
        else:
            t = (x - black) / span
            y = t ** gamma
        points.append([x, y])
    return points


def _refresh_histogram(state: _RenderState) -> None:
    """Rebuild the histogram series for the currently selected frame.

    Cheap on cache hits (just a dict lookup + chart options replacement);
    on miss, computes via :func:`compute_histogram` synchronously. The
    caller is responsible for offloading to a thread when called from an
    async context (see ``_show_preview``).
    """
    chart = state.histogram_chart
    if chart is None:
        return
    kind = _histogram_kind_for_mode(state.stretch_mode)
    hist = state.histogram_cache.get((state.selected_frame, kind))
    log_counts = hist["log_counts"] if hist is not None else None
    chart.options.clear()
    chart.options.update(_build_echart_options(
        log_counts=log_counts,
        black=state.black,
        white=state.white,
        midtone=state.midtone,
        zoom=state.histogram_zoom,
    ))
    try:
        chart.update()
    except RuntimeError:
        # UI gone — caller already logs in those paths.
        pass
    _refresh_histogram_overlay(state)


def _refresh_histogram_overlay(state: _RenderState) -> None:
    """Push current B/W/M values + mode-aware visual state to the overlay.

    Repositions the three drag handles in the browser and dims the
    overlay when the mode is not ``"manual"`` (handles still visible
    for info but not interactive).
    """
    if not state.histogram_overlay_id:
        return
    chart = state.histogram_chart
    if chart is not None:
        # Repaint just the tone curve — cheap, keeps visual feedback
        # synchronized with the numeric inputs / drag state.
        try:
            series = chart.options.get("series") or []
            for s in series:
                if s.get("name") == "curve":
                    s["data"] = _tone_curve_points(
                        black=state.black,
                        white=state.white,
                        midtone=state.midtone,
                    )
                    break
            chart.update()
        except RuntimeError:
            pass

    is_interactive = state.stretch_mode in ("manual", "auto+manual")
    overlay_id = state.histogram_overlay_id
    # Push current zoom into the JS state too — handle positions need it
    # to convert between normalized [0, 1] values and pixel x within the
    # visible [0, 1/zoom] window.
    js = (
        f"(function(){{"
        f"  const ov = document.getElementById({overlay_id!r});"
        f"  if (ov) {{"
        f"    ov.style.opacity = {1.0 if is_interactive else 0.4};"
        f"    ov.style.pointerEvents = "
        f"      {'\"auto\"' if is_interactive else '\"none\"'};"
        f"  }}"
        f"  if (window.__histState && window.__histState[{overlay_id!r}]) {{"
        f"    window.__histState[{overlay_id!r}].zoom = "
        f"      {float(state.histogram_zoom)};"
        f"  }}"
        f"  const fn = (window.__histApplyPositions || {{}})[{overlay_id!r}];"
        f"  if (fn) fn({float(state.black)}, {float(state.white)},"
        f"            {float(state.midtone)});"
        f"}})();"
    )
    try:
        ui.run_javascript(js)
    except RuntimeError:
        # Outside of an active client context — happens during seeding
        # before the page is fully mounted. Safe to skip.
        pass


def _on_handle_drag(state: _RenderState, which: str, value: float) -> None:
    """Handle a drag event from the JS overlay.

    Args:
        state: Mutable render UI state.
        which: One of ``"black"``, ``"white"``, ``"midtone"``.
        value: Normalized value (``[0, 1]`` for black/white, gamma in
            ``[0.1, 5]`` for midtone).
    """
    if state._seeding:
        return
    if not state.pipeline:
        return
    # Only the manual modes actually feed drags into the pipeline; in
    # other modes the handles are visually dimmed and pointer-events are
    # off, but JS-level guards can race so double-check here.
    if state.stretch_mode not in ("manual", "auto+manual"):
        return

    if which == "black":
        new_black = max(0.0, min(value, state.white - 0.001))
        state.black = float(new_black)
    elif which == "white":
        new_white = max(state.black + 0.001, min(value, 1.0))
        state.white = float(new_white)
    elif which == "midtone":
        state.midtone = float(max(0.1, min(value, 5.0)))
    else:
        return

    state.pipeline.config.stretch_mode = state.stretch_mode
    state.pipeline.config.stretch_params = StretchParams(
        state.black, state.white, state.midtone,
    )
    _refresh_histogram_overlay(state)
    _schedule_preview_refresh(state)


def _on_zoom_change(state: _RenderState) -> None:
    """Repaint the histogram with the new zoom factor.

    Zoom is purely visual: ``state.black/white/midtone`` and the rendered
    preview are unchanged, so we skip ``_schedule_preview_refresh`` here.
    We do, however, need to:

      * Rebuild the chart's xAxis.max so ECharts clips to the new visible
        range. Calling ``_refresh_histogram`` is the simplest path.
      * Push the new zoom into ``window.__histState[overlay_id].zoom``
        (handled by ``_refresh_histogram_overlay``) and reposition the
        handles, which now scale to a smaller visible width.
    """
    # Update the small label next to the slider.
    if state.histogram_zoom_label is not None:
        try:
            state.histogram_zoom_label.set_text(
                f"{state.histogram_zoom:.1f}x",
            )
        except RuntimeError:
            pass

    # Cheap chart-side rebuild: only the xAxis.max changes; the histogram
    # bin/curve data is unchanged.
    _refresh_histogram(state)


def _histogram_kind_for_mode(mode: str) -> str:
    """Pick the histogram source bucket that matches a stretch mode.

    The ``auto+manual`` mode operates on the auto-stretched 8-bit image,
    so its histogram must come from that data. All other modes use the
    raw debayered frame (``manual`` for backwards compatibility, ``auto``
    and ``histogram`` because they're non-interactive — see issue #112
    decision table).
    """
    return "auto-stretched" if mode == "auto+manual" else "raw"


async def _get_histogram(
    state: _RenderState,
    frame_idx: int,
    kind: str = "raw",
) -> dict | None:
    """Fetch a frame's histogram, computing it off-thread if not cached.

    Args:
        state: Mutable render UI state.
        frame_idx: Frame index in the pipeline.
        kind: ``"raw"`` for the debayered frame, ``"auto-stretched"``
            for the post-auto-stretch uint8 result. Determines both
            the cache bucket and the data source.

    Returns:
        Histogram dict (see :func:`compute_histogram`), or ``None`` if
        the pipeline isn't loaded yet.
    """
    if not state.pipeline:
        return None
    cache_key = (frame_idx, kind)
    hit = state.histogram_cache.get(cache_key)
    if hit is not None:
        return hit

    import asyncio

    # Snapshot the frozen-auto params at request time so a concurrent
    # toggle/recompute can't race the worker thread reading state.
    frozen_params = (
        state.auto_stretch_params if state.auto_stretch_freeze else None
    )

    def _work() -> dict | None:
        try:
            if kind == "auto-stretched":
                data = state.pipeline.auto_stretched_frame(
                    frame_idx, params=frozen_params,
                )
            else:
                data = state.pipeline.debayered_frame(frame_idx)
            return compute_histogram(data, bins=_HIST_BINS)
        except Exception:
            logger.exception(
                "Histogram failed for frame %d (kind=%s)", frame_idx, kind,
            )
            return None

    hist = await asyncio.to_thread(_work)
    if hist is not None:
        state.histogram_cache[cache_key] = hist
    return hist


def _schedule_preview_refresh(state: _RenderState) -> None:
    """Debounced preview refresh — coalesces rapid slider changes."""
    if state.preview_refresh_timer is not None:
        try:
            state.preview_refresh_timer.cancel()
        except Exception:
            pass

    def _fire() -> None:
        state.preview_refresh_timer = None
        if state.pipeline:
            import asyncio
            asyncio.create_task(_show_preview(state, state.selected_frame))

    state.preview_refresh_timer = ui.timer(0.15, _fire, once=True)


def _build_output_settings(state: _RenderState) -> None:
    """Build output format controls.

    Args:
        state: Mutable render UI state.
    """
    with ui.row().classes("w-full items-center gap-4"):
        ui.select(
            ["none", "crossfade", "linear-pan"], value="linear-pan",
            label="Transition",
        ).bind_value(state, "transition")
        ui.number(
            label="FPS", value=24, min=1, max=120,
        ).bind_value(state, "fps")
        ui.number(
            label="CRF", value=18, min=1, max=51,
        ).bind_value(state, "crf")
        ui.number(
            label="Speed", value=1.0, min=0.1, max=10.0, step=0.1, format="%.2f",
        ).bind_value(state, "speed").tooltip(
            "Playback speed multiplier (1=normal, 2=2x faster, 0.5=half)",
        )
        ui.select(
            ["native", "4k", "1440p", "1080p", "720p"],
            value="720p", label="Resolution",
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


# Names of _RenderState attributes that survive across sessions via
# app.storage.general["render"]. Manual stretch handles (black/white/
# midtone) are intentionally excluded — they're per-image tuning,
# overwritten on every mode switch by the auto-seeding logic. The
# auto-stretch reference anchors are also excluded (per-reference-frame,
# don't generalise). See issue #122 for the full reasoning.
_PERSISTED_FIELDS: tuple[str, ...] = (
    "input_dir",
    "output_path",
    "fps",
    "crf",
    "speed",
    "crossfade_frames",
    "stretch_mode",
    "transition",
    "resolution",
    "align_max_dim",
    "align_sigma",
)


def _load_render_state() -> dict:
    """Return the persisted render-UI dict (empty if no prior session)."""
    return app.storage.general.get("render", {})


def _save_render_state(state: _RenderState) -> None:
    """Snapshot persistable _RenderState fields into app.storage.general."""
    app.storage.general["render"] = {
        k: getattr(state, k) for k in _PERSISTED_FIELDS
    }


class _RenderState:
    """Mutable state for the render UI."""

    def __init__(self) -> None:
        """Initialize default render state."""
        stored = _load_render_state()
        self.input_dir: str = stored.get("input_dir", "./output/")
        self.stretch_mode: str = stored.get("stretch_mode", "histogram")
        self.black: float = 0.0
        self.white: float = 1.0
        self.midtone: float = 1.0
        self.transition: str = stored.get("transition", "linear-pan")
        self.fps: int = stored.get("fps", settings.render_fps)
        self.crf: int = stored.get("crf", settings.render_crf)
        self.speed: float = stored.get("speed", settings.render_speed)
        self.crossfade_frames: int = stored.get(
            "crossfade_frames", settings.render_crossfade_frames,
        )
        self.align_max_dim: int = stored.get(
            "align_max_dim", settings.render_align_max_dim,
        )
        self.align_sigma: float = stored.get(
            "align_sigma", settings.render_align_sigma,
        )
        self.resolution: str = stored.get("resolution", "720p")
        self.output_path: str = stored.get("output_path", "output.mp4")
        self.pipeline: RenderPipeline | None = None
        self.preview: ui.image | None = None
        self.filmstrip: ui.row | None = None
        self.progress: ui.linear_progress | None = None
        self.status_label: ui.label | None = None
        self.selected_frame: int = 0
        self.loading: bool = False
        self.preview_refresh_timer: ui.timer | None = None
        # Re-entry guard: when we seed black/white/midtone from auto-derive,
        # the bound sliders fire on_change. We skip those handlers to avoid
        # 3 redundant pipeline updates and refresh-schedules per mode switch.
        self._seeding: bool = False
        # Histogram widget — populated by _build_histogram_widget and
        # refreshed via _refresh_histogram on every frame change.
        self.histogram_chart: ui.echart | None = None
        self.histogram_overlay_id: str = ""
        # Per-frame histogram cache. Keyed on ``(frame_idx, kind)`` where
        # ``kind`` is ``"raw"`` (debayered uint16) or ``"auto-stretched"``
        # (uint8 after ZScale+Asinh) — separate buckets so switching
        # between ``manual`` and ``auto+manual`` doesn't re-compute the
        # 50-100 ms auto-stretch each time. Cleared in ``_load``.
        # Drag events repaint the curve only, so we never recompute the
        # heavy histogram during a drag.
        self.histogram_cache: dict[tuple[int, str], dict] = {}
        # Number-input refs (kept so seed-paths can update them via the
        # standard bind mechanism — no special wiring needed here).
        self.black_input: ui.number | None = None
        self.white_input: ui.number | None = None
        self.midtone_input: ui.number | None = None
        # Histogram x-axis zoom factor. 1.0 = full [0, 1] range visible
        # (default). Higher values zoom into the LEFT part: visible range
        # is [0, 1/zoom]. Purely visual — does not affect actual stretch
        # parameters or the rendered preview. Range is 1.0..50.0; the UI
        # uses exponential mapping for finer control near 1x.
        self.histogram_zoom: float = 1.0
        # Label showing the current zoom factor next to the slider.
        self.histogram_zoom_label: ui.label | None = None
        # Auto-stretch freeze (issue #114). When enabled, ``auto_stretch_params``
        # captures ZScale vmin/vmax from a reference frame and is reused
        # for all frames during render — eliminates brightness flicker
        # in auto / auto+manual modes and gives WYSIWYG previews.
        self.auto_stretch_freeze: bool = True
        self.auto_stretch_params: AutoStretchParams | None = None
        self.auto_stretch_ref_frame: int | None = None
        # UI handles populated by ``_build_auto_freeze_controls`` —
        # updated by ``_update_ref_frame_indicator`` whenever the
        # reference frame or selected frame changes.
        self.auto_stretch_ref_label: ui.label | None = None
        self.auto_stretch_apply_button: ui.button | None = None
        self.auto_stretch_freeze_row: ui.row | None = None


async def _load(state: _RenderState) -> None:
    """Load a capture directory asynchronously.

    Args:
        state: Mutable render UI state.
    """
    import asyncio

    if state.loading:
        ui.notify("Load already in progress", type="warning")
        return
    # Persist current UI state on Load too (not just Render): committing to
    # a capture directory is the natural moment to remember it for next
    # time, even if the user never proceeds to render.
    _save_render_state(state)
    state.loading = True
    try:
        capture_dir = Path(state.input_dir)
        config = RenderConfig(stretch_mode=state.stretch_mode)
        # Build pipeline locally — only publish to state after load completes,
        # so concurrent code paths never see a half-initialized pipeline.
        pipeline = RenderPipeline(capture_dir, config)

        _set_render_status(state, "Loading manifest...", 0.1)
        await asyncio.to_thread(pipeline.load)
        state.pipeline = pipeline
        # New pipeline => stale histograms are no longer valid.
        state.histogram_cache.clear()
        # Seed initial auto-stretch params from frame 0 so freeze=True
        # has something sensible at first paint. The user can switch
        # the reference frame later via "Aktuelles Frame übernehmen".
        # If frame 0 fails to load for any reason we fall back to
        # params=None — the freeze becomes effectively inactive until
        # the user sets a reference manually.
        state.auto_stretch_params = None
        state.auto_stretch_ref_frame = None
        if pipeline.frames:
            try:
                ref_data = await asyncio.to_thread(
                    pipeline.debayered_frame, 0,
                )
                state.auto_stretch_params = await asyncio.to_thread(
                    compute_auto_stretch_params, ref_data,
                )
                state.auto_stretch_ref_frame = 0
            except Exception as exc:
                logger.warning(
                    "Could not seed auto-stretch params from frame 0: %s",
                    exc,
                )
        _update_ref_frame_indicator(state)

        n = len(pipeline.frames)
        ui.notify(f"Loaded {n} frames — generating thumbnails...")
        _set_render_status(state, f"Thumbnails 0/{n}...", 0.2)

        # Generate thumbnails in batches so the UI gets progress updates
        # between batches (each await yields to the event loop).
        batch_size = 8
        thumbnails: list[str | None] = []
        for batch_start in range(0, n, batch_size):
            batch_end = min(batch_start + batch_size, n)

            def _gen_batch(start: int = batch_start, end: int = batch_end) -> list[str | None]:
                return [_make_thumbnail(state, i) for i in range(start, end)]

            batch_thumbs = await asyncio.to_thread(_gen_batch)
            thumbnails.extend(batch_thumbs)
            progress = 0.2 + 0.7 * batch_end / n
            _set_render_status(state, f"Thumbnails {batch_end}/{n}...", progress)

        # Build filmstrip in UI thread. If the page was closed during the
        # await above, the filmstrip's client is gone and any UI access
        # raises RuntimeError — bail out cleanly in that case.
        try:
            if state.filmstrip:
                state.filmstrip.clear()
                with state.filmstrip:
                    for i, thumb in enumerate(thumbnails):
                        if thumb:
                            _render_thumb_card(
                                state, i, pipeline.frames[i].index, thumb,
                            )

            _set_render_status(state, "", 0)
            await _show_preview(state, 0)
            ui.notify(f"Ready — {n} frames loaded")
        except RuntimeError as exc:
            logger.info("UI gone before load finished: %s", exc)
    finally:
        state.loading = False


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


def _thumb_cache_path(frame_path: Path) -> Path:
    """Return the disk-cache path for a frame's thumbnail."""
    return frame_path.parent / ".thumbs" / f"{frame_path.stem}.jpg"


def _make_thumbnail(state: _RenderState, frame_idx: int) -> str | None:
    """Generate a base64 thumbnail for a frame.

    Uses an on-disk cache at ``<capture_dir>/.thumbs/<name>.jpg`` so
    repeated loads of the same capture directory are near-instant.
    Cache is invalidated when the FITS file mtime is newer.

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
        cache_path = _thumb_cache_path(frame.fits_path)

        # Cache hit: FITS unchanged since cache was written
        if (
            cache_path.exists()
            and cache_path.stat().st_mtime >= frame.fits_path.stat().st_mtime
        ):
            b64 = base64.b64encode(cache_path.read_bytes()).decode()
            return f"data:image/jpeg;base64,{b64}"

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
        jpeg_bytes = buf.getvalue()

        # Write to cache (best-effort — don't fail thumbnail on cache write error)
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(jpeg_bytes)
        except OSError as exc:
            logger.debug("Thumb cache write failed for %s: %s", cache_path, exc)

        b64 = base64.b64encode(jpeg_bytes).decode()
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        logger.warning("Thumbnail failed for frame %d", frame_idx)
        return None


async def _show_preview(state: _RenderState, frame_idx: int) -> None:
    """Show a downsized preview of a frame.

    Heavy work (stretch + JPEG encode) runs in a thread so the event
    loop stays responsive — otherwise the WebSocket heartbeat fails
    during long stretches and the browser disconnects.

    The preview is downsized to ~1280 px before JPEG encoding so the
    base64 data URI stays well under the Socket.IO message size limit.

    Args:
        state: Mutable render UI state.
        frame_idx: Index into pipeline frames list.
    """
    if not state.pipeline or not state.preview:
        return
    state.selected_frame = frame_idx
    # Selected frame changed — re-evaluate ref-frame staleness so the
    # "Aktuelles Frame übernehmen" button highlights immediately when
    # the user navigates away from the current reference.
    _update_ref_frame_indicator(state)

    # Keep the pipeline config in sync with state so the preview
    # honors the freeze toggle and current frozen params (WYSIWYG —
    # preview matches what the render will produce).
    state.pipeline.config.auto_stretch_freeze = state.auto_stretch_freeze
    state.pipeline.config.auto_stretch_params = state.auto_stretch_params

    def _build() -> str | None:
        try:
            stretched = state.pipeline.stretch_frame(frame_idx)
            img = Image.fromarray(stretched)
            # The preview <img> is constrained to max-h-96 (~384 px);
            # 1280 px gives plenty of headroom for zoom/expand without
            # blowing past the WebSocket message size limit.
            img.thumbnail((1280, 1280))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return f"data:image/jpeg;base64,{b64}"
        except Exception:
            logger.exception("Preview failed for frame %d", frame_idx)
            return None

    import asyncio
    src = await asyncio.to_thread(_build)
    if src is None:
        return
    try:
        state.preview.set_source(src)
    except RuntimeError:
        logger.info("Preview UI gone before update")
        return

    # Keep the histogram widget in sync with the displayed frame. The
    # heavy compute happens off-thread inside ``_get_histogram``; cache
    # hits are essentially free. Source bucket follows the active mode:
    # ``auto+manual`` reads the auto-stretched data, everything else
    # reads raw debayered.
    kind = _histogram_kind_for_mode(state.stretch_mode)
    await _get_histogram(state, frame_idx, kind=kind)
    try:
        _refresh_histogram(state)
    except RuntimeError:
        logger.info("Histogram UI gone before update")


async def _render(state: _RenderState) -> None:
    """Run the full render pipeline.

    Args:
        state: Mutable render UI state.
    """
    _save_render_state(state)
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
        # The render can take minutes — the user may have closed the tab,
        # in which case any UI access raises RuntimeError. The video is
        # already saved to disk, so just log and move on.
        try:
            ui.notify(f"Video saved: {output}", type="positive")
        except RuntimeError:
            logger.info("Render completed but UI gone: %s", output)
    except Exception as exc:
        logger.exception("Render failed: %s", exc)
        try:
            ui.notify(f"Render failed: {exc}", type="negative")
        except RuntimeError:
            pass
    finally:
        try:
            timer.cancel()
        except RuntimeError:
            pass
        try:
            _set_render_status(state, "", 0)
        except RuntimeError:
            pass


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
    if state.stretch_mode in ("manual", "auto+manual"):
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
        speed=float(state.speed),
        auto_stretch_freeze=state.auto_stretch_freeze,
        auto_stretch_params=state.auto_stretch_params,
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
