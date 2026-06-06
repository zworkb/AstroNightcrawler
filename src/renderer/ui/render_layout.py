"""NiceGUI web UI for the Nightcrawler renderer."""

from __future__ import annotations

import base64
import io
import logging
import uuid
from pathlib import Path

from nicegui import app, ui
from PIL import Image

from src.config import settings
from src.models.project import Label, Project, RenderSettings
from src.renderer.pipeline import ProgressUpdate, RenderConfig, RenderPipeline
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
        # Toggle row (issue #148): full-resolution preview for precise
        # label placement on 8K frames. ``_apply_preview_mode`` swaps
        # class strings + icon/tooltip to reflect the current mode.
        with ui.row().classes("w-full justify-end items-center gap-2"):
            state.preview_detail_button = ui.button(
                icon="fullscreen",
                on_click=lambda: _toggle_preview_detail(state),
            ).props("flat dense round")
        # Scrollable wrapper around the preview image. In detail mode the
        # wrapper bounds the viewport (max-h-screen + overflow-auto) so a
        # native-size frame can be scrolled; in compact mode the wrapper
        # is a transparent passthrough and the image carries max-h-96.
        #
        # We also stack a transparent catalog-overlay div over the image
        # (issue #152): the overlay carries a vanilla-JS pointermove +
        # click handler that does the nearest-object search client-side.
        # The overlay sits inside the wrapper so it follows scroll/resize
        # naturally; ``pointer-events: none`` keeps the underlying click
        # handler (manual Add-label) reachable when catalog mode is off.
        with ui.element("div").classes("w-full") as wrapper:
            state.preview_wrapper = wrapper
            # Inner relatively-positioned container so the absolute
            # overlay aligns to the rendered image, not to the wrapper
            # padding.
            with ui.element("div").classes("relative") as preview_stack:
                state.preview = ui.image().classes("object-contain")
                state.catalog_overlay_id = f"cat-overlay-{id(state)}"
                state.catalog_overlay = ui.element("div").props(
                    f"id={state.catalog_overlay_id}",
                ).classes(
                    "absolute inset-0 pointer-events-none",
                )
            del preview_stack  # only used for the with-context
            # Absolute path of the currently-displayed frame, shown
            # below the preview as a thin caption. Same provenance
            # signal as the filmstrip-thumb tooltip but always visible.
            state.preview_path_label = ui.label("").classes(
                "text-xs text-grey w-full text-center break-all"
            )
        _apply_preview_mode(state)
        # Inject the catalog-overlay script once per page. Mirrors the
        # histogram-overlay pattern (#111) — the JS lives in
        # ``_catalog_overlay_script`` and is parameterised by the
        # overlay id so multiple instances would coexist cleanly.
        ui.add_body_html(_catalog_overlay_script(state.catalog_overlay_id))

        ui.on("label_placement", lambda e: _handle_label_placement(state, e))
        _build_labels_panel(state)
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
            params = StretchParams(
                black=state.black, white=state.white, midtone=state.midtone,
            )
            # Per-frame routing: if the current frame is "reset"
            # (force_fresh_stretch=True), B/W/M edits affect ONLY
            # that frame. Otherwise the project-wide stretch_params
            # carries the change as before.
            idx = state.selected_frame
            frames = state.pipeline.frames
            if (
                state.pipeline.project is not None
                and 0 <= idx < len(frames)
                and getattr(frames[idx], "force_fresh_stretch", False)
            ):
                frames[idx].stretch_override = params
                fits_basename = frames[idx].fits_path.name
                for point in state.pipeline.project.capture_points:
                    for cf in point.frames:
                        if cf.filename == fits_basename:
                            cf.stretch_override = params
                            break
                _persist_project(state)
            else:
                state.pipeline.config.stretch_params = params
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
                black=state.black, white=state.white, midtone=state.midtone,
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
        _save_render_state(state)
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
        _save_render_state(state)
        _update_ref_frame_indicator(state)
        _refresh_histogram(state)
        _schedule_preview_refresh(state)

    def _on_reset_stretch() -> None:
        """Toggle per-frame override on the currently-displayed frame.

        Click flips ``CapturedFrame.force_fresh_stretch`` for the
        current frame only — ON = frame renders with its own fresh
        ZScale (ignoring the project freeze), OFF = back to freeze.
        All other frames unaffected. Useful for outlier exposures
        whose brightness doesn't fit the project-wide freeze (#154
        follow-up smoke test).
        """
        if not state.pipeline or not state.pipeline.project:
            return
        idx = state.selected_frame
        if not (0 <= idx < len(state.pipeline.frames)):
            return
        frame_info = state.pipeline.frames[idx]
        new_value = not bool(
            getattr(frame_info, "force_fresh_stretch", False),
        )
        frame_info.force_fresh_stretch = new_value
        # When turning the override ON, seed the per-frame stretch
        # override from the current project params so the sliders
        # start "where the user was" instead of jumping. When turning
        # OFF, drop the per-frame copy.
        if new_value and getattr(frame_info, "stretch_override", None) is None:
            seed = state.pipeline.config.stretch_params or StretchParams(
                black=state.black, white=state.white, midtone=state.midtone,
            )
            frame_info.stretch_override = StretchParams(
                black=seed.black, white=seed.white, midtone=seed.midtone,
            )
        elif not new_value:
            frame_info.stretch_override = None
        fits_basename = frame_info.fits_path.name
        for point in state.pipeline.project.capture_points:
            for cf in point.frames:
                if cf.filename == fits_basename:
                    cf.force_fresh_stretch = new_value
                    cf.stretch_override = frame_info.stretch_override
                    break
        state.histogram_cache.clear()
        _persist_project(state)
        _refresh_reset_button(state)
        _sync_sliders_to_current_frame(state)
        _refresh_histogram(state)
        _schedule_preview_refresh(state)
        ui.notify(
            f"Frame #{frame_info.index}: "
            + ("eigene Stretch-Params" if new_value else "Projekt-Stretch"),
            type="info", timeout=1500,
        )

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
        state.frame_reset_button = ui.button(
            "Dieses Frame: Reset", icon="restart_alt",
            on_click=lambda: _on_reset_stretch(),
        ).props("dense flat color=grey-6").tooltip(
            "Nur für das aktuell angezeigte Frame: ignoriere die "
            "eingefrorenen Stretch-Parameter und berechne ZScale "
            "frisch. Nützlich für einzelne Outlier-Exposures, deren "
            "Helligkeit nicht zum Projekt-Freeze passt. Erneut "
            "klicken setzt zurück.",
        )

    # Visible only in auto / auto+manual modes — the freeze has no
    # effect in manual/histogram modes. ``bind_visibility_from`` with a
    # custom predicate handles the OR cleanly.
    row.bind_visibility_from(
        state, "stretch_mode",
        backward=lambda mode: mode in ("auto", "auto+manual"),
    )


def _refresh_reset_button(state: _RenderState) -> None:
    """Highlight the per-frame Reset button when the current frame has override on."""
    btn = state.frame_reset_button
    if btn is None or state.pipeline is None:
        return
    idx = state.selected_frame
    if not (0 <= idx < len(state.pipeline.frames)):
        return
    is_overridden = bool(
        getattr(state.pipeline.frames[idx], "force_fresh_stretch", False),
    )
    try:
        if is_overridden:
            btn.props("dense color=primary icon=restart_alt")
        else:
            btn.props("dense flat color=grey-6 icon=restart_alt")
    except RuntimeError:
        pass


def _sync_sliders_to_current_frame(state: _RenderState) -> None:
    """Reseed B/W/M slider values from the active source on frame change.

    When the current frame is overridden (``force_fresh_stretch=True``),
    sliders show that frame's ``stretch_override``. Otherwise they show
    the project-wide ``config.stretch_params``. ``state._seeding`` is
    flipped while writing so the seed assignment doesn't fire
    ``_on_param_change`` and round-trip back into the model.
    """
    if state.pipeline is None:
        return
    idx = state.selected_frame
    if not (0 <= idx < len(state.pipeline.frames)):
        return
    frame = state.pipeline.frames[idx]
    is_overridden = bool(getattr(frame, "force_fresh_stretch", False))
    override = getattr(frame, "stretch_override", None)
    if is_overridden and override is not None:
        sp = override
    else:
        sp = state.pipeline.config.stretch_params or StretchParams()
    state._seeding = True
    try:
        state.black = sp.black
        state.white = sp.white
        state.midtone = sp.midtone
        if state.black_input is not None:
            state.black_input.set_value(sp.black)
        if state.white_input is not None:
            state.white_input.set_value(sp.white)
        if state.midtone_input is not None:
            state.midtone_input.set_value(sp.midtone)
    finally:
        state._seeding = False
    _refresh_histogram_overlay(state)


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
        black=state.black, white=state.white, midtone=state.midtone,
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
    with ui.row().classes("w-full items-center gap-2"):
        music_input = ui.input(
            label="Musik (.mp3 / .wav / .m4a / .ogg / .flac)",
            value=state.music_track or "",
            placeholder="Absoluter Pfad zum Audio-File (optional)",
        ).classes("flex-grow").bind_value(state, "music_track").tooltip(
            "Wird beim Render per ffmpeg als Tonspur an das Video "
            "angehängt. Pfad in den Project-Settings persistiert.",
        )

        def _pick_music() -> None:
            from src.ui.folder_browser import FolderBrowserDialog

            def _on_pick(path: Path) -> None:
                state.music_track = str(path)
                music_input.set_value(str(path))
                _save_render_state(state)

            # Start in the music file's parent dir if one is set,
            # otherwise the user's home — a saner default than CWD
            # for picking media files.
            if state.music_track:
                start = Path(state.music_track).parent
            else:
                start = Path.home()
            if not start.exists():
                start = Path.cwd()
            FolderBrowserDialog(
                on_select=_on_pick,
                select_files=True,
                extensions=[".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"],
                title="Musik auswählen",
            ).open(start)

        ui.button(
            "Auswählen", icon="audio_file", on_click=_pick_music,
        ).props("dense").tooltip(
            "File-Picker für den Musik-Track",
        )

        def _clear_music() -> None:
            state.music_track = None
            music_input.set_value("")
            _save_render_state(state)

        ui.button(
            icon="close", on_click=_clear_music,
        ).props("dense flat").tooltip(
            "Music-Track aus der Konfiguration entfernen",
        )

    with ui.row().classes("w-full items-center gap-4 ml-1"):
        ui.checkbox("Musik einbinden").bind_value(
            state, "include_music",
        ).tooltip(
            "Wenn aktiv und ein Music-Track ausgewählt ist, wird "
            "die Audiospur per ffmpeg an das gerenderte Video "
            "angehängt.",
        )
        loop_chk = ui.checkbox("Musik loopen").bind_value(
            state, "loop_music",
        ).tooltip(
            "Wenn das Audio kürzer ist als das Video, wird es "
            "wiederholt bis das Video endet (ffmpeg "
            "-stream_loop -1). Wirkt nur wenn 'Musik einbinden' "
            "aktiv ist.",
        )
        # Disable loop when include_music is off so the checkbox
        # state matches the effective behaviour.
        loop_chk.bind_enabled_from(state, "include_music")
        ui.checkbox("Labels einbinden").bind_value(
            state, "include_labels",
        ).tooltip(
            "Wenn deaktiviert wird das Video ohne Label-Overlays "
            "gerendert, auch wenn welche im Projekt definiert sind.",
        )

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
                # Persist immediately on output-browse selection. The
                # capture-dir browse path persists indirectly via the
                # auto-triggered Load; the output browse has no Load
                # equivalent, so the save needs to happen here.
                _save_render_state(state)

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
        ui.number(
            label="Workers", value=state.render_workers,
            min=-1, max=64, step=1,
        ).bind_value(state, "render_workers").tooltip(
            "Parallel workers for alignment + stretch. "
            "-1 = all CPU cores. Default 4 (balance of speed vs. memory; "
            "~50 MB/worker for alignment, ~78 MB/worker for stretch).",
        )
        ui.number(
            label="Blend tail frames", value=state.linear_pan_blend_tail,
            min=0, max=120, step=1,
        ).bind_value(state, "linear_pan_blend_tail").tooltip(
            "Anzahl Frames am Ende der linear-pan-Transition über die "
            "Frame_a→Frame_b geblendet wird. Glättet Belichtungssprünge "
            "zwischen Keyframes. 0 = kein Blending. "
            "Empfohlen: 6-8 (~1/4 der Crossfade-Frames).",
        )


# Per-machine fields — stay in ``app.storage.general["render"]``. These
# are paths (machine-specific) and hardware/UI prefs (also machine-
# specific). See issue #151 for the rationale behind the split.
_APP_PERSISTED_FIELDS: tuple[str, ...] = (
    "input_dir",
    "output_path",
    "render_workers",
    "preview_detail_mode",
    "catalog_modifier_active",
    "leader_modifier_active",
    "last_label_color",
    "last_label_font_size",
    "last_label_offset_radius",
)

# Per-project fields — serialised into ``manifest.json`` via
# ``Project.render_settings`` (issue #151). These are the "look" and
# output-format decisions for *this* project: stretch tuning, output
# format, alignment params. Manual stretch handles (black/white/midtone)
# ARE included here now — they're per-project tuning, not per-session.
_PROJECT_PERSISTED_FIELDS: tuple[str, ...] = (
    "fps",
    "crf",
    "speed",
    "crossfade_frames",
    "stretch_mode",
    "transition",
    "resolution",
    "align_max_dim",
    "align_sigma",
    "linear_pan_blend_tail",
    "black",
    "white",
    "midtone",
    "auto_stretch_freeze",
    "auto_stretch_params",
    "music_track",
    "include_music",
    "include_labels",
    "loop_music",
)

# Kept for backward compat with any external code that imported the old
# name. New code should use ``_APP_PERSISTED_FIELDS`` /
# ``_PROJECT_PERSISTED_FIELDS`` explicitly.
_PERSISTED_FIELDS: tuple[str, ...] = (
    _APP_PERSISTED_FIELDS + _PROJECT_PERSISTED_FIELDS
)


def _load_render_state() -> dict:
    """Return the persisted render-UI dict (empty if no prior session).

    Returns only the app-level fields after #151 — project-level fields
    are loaded from the manifest's ``render_settings`` once a project is
    opened. Legacy project-level keys still present in app.storage are
    left in place here; they get soft-migrated on first project load
    via :func:`_maybe_soft_migrate_render_settings`.
    """
    return app.storage.general.get("render", {})


def _save_render_state(state: _RenderState) -> None:
    """Persist the render UI state across both storage layers.

    App-level fields (paths, workers, UI prefs) → ``app.storage.general``.
    Project-level fields (stretch, output format, alignment) → into the
    loaded project's ``render_settings`` + manifest.json.

    Issue #151: this is the write-side counterpart to the load split.
    """
    # 1) App-level → app.storage.general (machine-global).
    app.storage.general["render"] = {
        k: getattr(state, k) for k in _APP_PERSISTED_FIELDS
    }

    # 2) Project-level → manifest.json (per-project).
    pipeline = state.pipeline
    if pipeline is None or pipeline.project is None:
        return  # no project loaded yet; nothing to persist project-side
    rs = pipeline.project.render_settings
    for attr in _PROJECT_PERSISTED_FIELDS:
        # ``auto_stretch_params`` is a pydantic model on _RenderState and
        # is the same type on ``RenderSettings`` — assigning it through is
        # safe; pydantic will validate on the next dump/load cycle.
        setattr(rs, attr, getattr(state, attr))
    _persist_project(state)


def _persist_project(state: _RenderState) -> None:
    """Write the (possibly modified) project back to manifest.json.

    Called after any in-UI mutation of the labels list or render
    settings so the change survives a tab close even before the next
    render.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    manifest_path = state.pipeline.capture_dir / "manifest.json"
    manifest_path.write_text(state.pipeline.project.model_dump_json(indent=2))


def _maybe_soft_migrate_render_settings(
    project: Project,
    app_store: dict,
) -> bool:
    """One-time lift of legacy app.storage render fields into the project.

    Before #151 the project-level fields (stretch_mode, B/W/M, fps,
    crf, etc.) were stored in ``app.storage.general["render"]``. On the
    first project-open after upgrade we want to preserve any tuning the
    user already invested — without it the user would see their
    stretch/output settings silently revert to defaults.

    Strategy:
        * Only migrate when the project's ``render_settings`` is still
          the pristine default (i.e. the manifest was written before
          this issue or by capture-only). A project that already carries
          customised settings wins — we never overwrite per-project
          tuning with legacy machine-global values.
        * Lift each legacy key into ``project.render_settings`` if it
          maps to a known project-level field, then drop it from the
          app-store dict so we don't migrate twice.

    Args:
        project: The just-loaded project (mutated in place on migration).
        app_store: Mutable mapping behind ``app.storage.general["render"]``
            (mutated in place: legacy project-level keys removed).

    Returns:
        ``True`` if any field was migrated, ``False`` otherwise.
    """
    if project.render_settings != RenderSettings():
        return False  # already customised; never overwrite per-project tuning
    legacy: dict[str, object] = {
        k: app_store[k] for k in _PROJECT_PERSISTED_FIELDS if k in app_store
    }
    if not legacy:
        return False
    for key, value in legacy.items():
        # Pydantic will coerce numbers (e.g. int from JSON) and validate
        # ``auto_stretch_params`` if it's a dict; if validation fails we
        # leave the field at its default rather than crashing the load.
        try:
            setattr(project.render_settings, key, value)
        except Exception:  # noqa: BLE001 — best-effort migration
            logger.warning(
                "soft-migrate: dropping invalid legacy value for %s=%r",
                key, value,
            )
    for key in legacy:
        app_store.pop(key, None)
    logger.info(
        "soft-migrated %d legacy render fields from app.storage into project "
        "manifest: %s", len(legacy), sorted(legacy.keys()),
    )
    return True


def _apply_render_settings_to_state(
    state: _RenderState,
    rs: RenderSettings,
) -> None:
    """Copy a project's ``render_settings`` onto the live UI state.

    Used after loading a project so the renderer UI immediately reflects
    *that project's* look/output decisions rather than whatever was left
    on screen from the previously-opened project.

    NiceGUI's ``bind_value`` propagates the state changes to the bound
    number inputs (B/W/M, fps, crf, etc.) automatically. The histogram
    overlay's drag handles however are HTML/JS elements positioned
    against ``window.__histState`` — those don't track state mutations
    unless we explicitly re-emit the JS-side positions. Same for the
    rendered preview which uses the stretch params on every refresh.
    See #111 (histogram widget) and #110 (live preview).
    """
    for attr in _PROJECT_PERSISTED_FIELDS:
        setattr(state, attr, getattr(rs, attr))
    # NiceGUI's bind_value reliably tracks INPUT→state but not always
    # state→INPUT after a programmatic setattr on a plain class. Force
    # the bound number inputs to re-read state via set_value so the
    # widgets visually update on project load. The histogram drag
    # handles are JS-overlay positions and need _refresh_histogram_overlay
    # below; the preview JPEG needs a re-render via _schedule_preview_refresh.
    if state.black_input is not None:
        state.black_input.set_value(state.black)
    if state.white_input is not None:
        state.white_input.set_value(state.white)
    if state.midtone_input is not None:
        state.midtone_input.set_value(state.midtone)
    if state.histogram_chart is not None:
        _refresh_histogram_overlay(state)
    _schedule_preview_refresh(state)


def _build_labels_panel(state: _RenderState) -> None:
    """Collapsible Labels list + edit/delete + click-to-add toggle."""
    with ui.expansion("Labels", icon="label").classes("w-full") as exp:
        state.labels_panel = exp
        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full items-center justify-end gap-3"):
                state.catalog_modifier_checkbox = ui.checkbox(
                    "Catalog",
                    value=state.catalog_modifier_active,
                    on_change=lambda e: _toggle_catalog_modifier(state, e.value),
                ).props("dense").tooltip(
                    "Beim Klicken aufs nächste Catalog-Objekt im FOV snappen",
                )
                state.leader_modifier_checkbox = ui.checkbox(
                    "Leader",
                    value=state.leader_modifier_active,
                    on_change=lambda e: _toggle_leader_modifier(state, e.value),
                ).props("dense").tooltip(
                    "Leader-Linie zwischen Marker und Text zeichnen",
                )
                state.wcs_flip_checkbox = ui.checkbox(
                    "Flip 180°",
                    value=(
                        state.pipeline.project.wcs_flip_180
                        if state.pipeline and state.pipeline.project
                        else False
                    ),
                    on_change=lambda e: _toggle_wcs_flip(state, e.value),
                ).props("dense").tooltip(
                    "WCS um 180° drehen — bei verkehrt platzierten "
                    "Catalog-Labels (Pierside-Bug im Capture-Tool).",
                )
                state.add_label_button = ui.button(
                    "Add label", icon="add",
                    on_click=lambda: _arm_add_label(state),
                ).props("dense flat")
                _refresh_add_label_tooltip(state)
            state.labels_list_container = ui.column().classes("w-full gap-1")
        _refresh_labels_list(state)


def _refresh_labels_list(state: _RenderState) -> None:
    """Re-render the labels list from the current project."""
    if not state.labels_list_container:
        return
    state.labels_list_container.clear()
    if not state.pipeline or not state.pipeline.project:
        with state.labels_list_container:
            ui.label("(load a capture first)").classes("text-grey text-xs")
        return
    labels = state.pipeline.project.labels
    if not labels:
        with state.labels_list_container:
            ui.label("(no labels yet)").classes("text-grey text-xs")
        return
    with state.labels_list_container:
        for label in labels:
            _render_label_row(state, label)


def _render_label_row(state: _RenderState, label: Label) -> None:
    """One row in the labels list.

    Clicking the label's text or its (x, y) coords jumps the preview
    to ``label.ref_frame_index`` — the frame the label was anchored
    on. The clicked row gets a subtle background tint so the user
    sees which label is currently 'in focus'. Edit/delete buttons
    keep their dedicated handlers (#154).
    """
    row_classes = "w-full items-center gap-2 rounded"
    if state.selected_label_id == label.id:
        row_classes += " bg-blue-grey-8 px-2"
    with ui.row().classes(row_classes):
        ui.html(
            f'<span style="display:inline-block;width:12px;height:12px;'
            f'background:{label.color};border-radius:50%"></span>',
        )
        text_widget = ui.label(label.text or "(empty)").classes(
            "flex-grow text-sm cursor-pointer hover:underline",
        ).tooltip(
            f"Klick → springt auf Frame {label.ref_frame_index}",
        )
        coord_widget = ui.label(f"({int(label.x)},{int(label.y)})").classes(
            "text-xs text-grey cursor-pointer",
        )
        text_widget.on(
            "click",
            lambda l=label: _jump_to_label_frame(state, l),
        )
        coord_widget.on(
            "click",
            lambda l=label: _jump_to_label_frame(state, l),
        )
        ui.button(
            icon="edit",
            on_click=lambda l=label: _open_edit_popover(state, l),
        ).props("flat dense")
        ui.button(
            icon="delete", color="red",
            on_click=lambda l=label: _delete_label(state, l),
        ).props("flat dense")


def _jump_to_label_frame(state: _RenderState, label: Label) -> None:
    """Load the preview frame that owns ``label`` (its ref_frame_index).

    Also highlights the row in the labels list as the currently-active
    selection so the user can see at a glance which label they last
    navigated to.
    """
    state.selected_label_id = label.id
    _refresh_labels_list(state)
    if not state.pipeline:
        return
    frame_idx = label.ref_frame_index
    if not (0 <= frame_idx < len(state.pipeline.frames)):
        return
    import asyncio
    asyncio.create_task(_show_preview(state, frame_idx))


def _delete_label(state: _RenderState, label: Label) -> None:
    """Remove ``label`` from the project, persist, and refresh the list."""
    if not state.pipeline or not state.pipeline.project:
        return
    state.pipeline.project.labels = [
        x for x in state.pipeline.project.labels if x.id != label.id
    ]
    _persist_project(state)
    _refresh_labels_list(state)


def _open_edit_popover(state: _RenderState, label: Label) -> None:
    """Open an inline dialog to edit a label's properties."""
    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("Edit label").classes("text-md font-bold")
        text_in = ui.input("Text", value=label.text)
        color_in = ui.input("Color (hex)", value=label.color)
        font_size_in = ui.number(
            "Font size", value=label.font_size, min=6, max=200,
        )
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value=label.marker, label="Marker",
        )
        leader_in = ui.select(
            {"none": "no leader", "line": "line", "arrow": "arrow"},
            value=label.leader, label="Leader",
        )
        offset_radius_in = ui.number(
            "Offset radius (px)", value=label.offset_radius,
            min=0, max=500,
        ).tooltip(
            "Pixel-Abstand zwischen Linie und Target/Text — verhindert "
            "dass die Linie das Objekt überdeckt. Ignoriert bei "
            "leader='none'.",
        )
        # Force set_value so widget.value isn't None on untouched fields
        # (same Quasar binding gotcha as in the create dialog).
        font_size_in.set_value(label.font_size)
        offset_radius_in.set_value(label.offset_radius)

        def _num(widget, fallback: int) -> int:
            v = widget.value
            return int(v) if v is not None else int(fallback)

        with ui.row().classes("w-full justify-end"):
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def _save() -> None:
                label.text = text_in.value or ""
                label.color = color_in.value or label.color
                label.font_size = _num(font_size_in, label.font_size)
                label.marker = marker_in.value or "dot"
                label.leader = leader_in.value or "none"
                label.offset_radius = _num(offset_radius_in, label.offset_radius)
                state.last_label_color = label.color
                state.last_label_font_size = label.font_size
                state.last_label_offset_radius = label.offset_radius
                _save_render_state(state)
                _persist_project(state)
                _refresh_labels_list(state)
                _schedule_preview_refresh(state)
                dialog.close()

            ui.button("Save", color="primary", on_click=_save)
    dialog.open()


def _toggle_catalog_modifier(state: _RenderState, checked: bool) -> None:
    """Persist the Catalog modifier state and refresh the JS overlay.

    Catalog modifier on → next Add-Label click snaps the marker to
    the nearest catalog object in the current FOV. Pre-loading the
    FOV slice keeps the snap responsive (no first-click stall).
    """
    state.catalog_modifier_active = bool(checked)
    _save_render_state(state)
    _push_overlay_state(state)
    _refresh_add_label_tooltip(state)
    if state.catalog_modifier_active:
        _refresh_catalog_fov_slice(state)


def _toggle_leader_modifier(state: _RenderState, checked: bool) -> None:
    """Persist the Leader modifier state and refresh the JS overlay."""
    state.leader_modifier_active = bool(checked)
    _save_render_state(state)
    _push_overlay_state(state)
    _refresh_add_label_tooltip(state)


def _toggle_wcs_flip(state: _RenderState, checked: bool) -> None:
    """Persist the WCS 180°-flip flag and rebuild the catalog overlay.

    The flag is a project-level setting (different telescopes need it
    differently — see #157). Toggling invalidates the cached FOV slice
    so the next refresh recomputes with the new orientation.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    state.pipeline.project.wcs_flip_180 = bool(checked)
    _persist_project(state)
    state.catalog_fov_cache.clear()
    _refresh_catalog_fov_slice(state)


def _arm_add_label(state: _RenderState) -> None:
    """Enter one-shot Add-Label mode (#154).

    Sets ``click_to_add_active=True`` so the JS overlay routes the
    next click(s) into ``label_placement`` events. After the dialog
    closes (Save or Cancel) the caller invokes ``_disarm_add_label``
    so the next preview click falls through to normal behaviour.
    """
    state.click_to_add_active = True
    state.pending_placement = None
    _push_overlay_state(state)
    _refresh_add_label_button_visual(state)
    _refresh_catalog_fov_slice(state)


def _disarm_add_label(state: _RenderState) -> None:
    """Exit Add-Label mode without committing anything."""
    state.click_to_add_active = False
    state.pending_placement = None
    _push_overlay_state(state)
    _refresh_add_label_button_visual(state)


def _refresh_add_label_button_visual(state: _RenderState) -> None:
    """Highlight the Add-Label button while it's armed."""
    btn = state.add_label_button
    if btn is None:
        return
    try:
        if state.click_to_add_active:
            btn.props("dense color=primary")
        else:
            btn.props("dense flat")
    except RuntimeError:
        pass


def _refresh_add_label_tooltip(state: _RenderState) -> None:
    """Tooltip describes the current modifier combo's behaviour."""
    btn = state.add_label_button
    if btn is None:
        return
    cat = state.catalog_modifier_active
    leader = state.leader_modifier_active
    if cat and leader:
        msg = "1 Klick → Text platzieren, Leader-Linie zum nächsten Catalog-Objekt"
    elif cat:
        msg = "1 Klick → Label am nächsten Catalog-Objekt im FOV"
    elif leader:
        msg = "2 Klicks → 1. Target, 2. Textposition, mit Leader-Linie"
    else:
        msg = "1 Klick → Label an Klick-Position"
    try:
        btn.tooltip(msg)
    except RuntimeError:
        pass


def _push_overlay_state(state: _RenderState) -> None:
    """Broadcast the three flags + add-mode to the JS overlay.

    Pointer events on the overlay are enabled iff either modifier is
    on OR Add-Label is armed — the modifiers alone enable the catalog
    tooltip on hover; Add-Label enables click capture.
    """
    overlay_id = state.catalog_overlay_id
    if not overlay_id:
        return
    import json as _json
    payload = {
        "addMode": bool(state.click_to_add_active),
        "catalogModifier": bool(state.catalog_modifier_active),
        "leaderModifier": bool(state.leader_modifier_active),
    }
    try:
        ui.run_javascript(
            f"if (window.__catalogOverlaySetState) "
            f"{{ window.__catalogOverlaySetState({overlay_id!r}, {_json.dumps(payload)}); }}",
        )
    except RuntimeError:
        pass


def _refresh_catalog_fov_slice(state: _RenderState) -> None:
    """Compute the FOV-slice of catalog objects for the current frame and ship it to JS.

    Cached per ``(frame_idx, catalog version)`` on ``state`` so
    re-selecting the same frame is essentially free (#152).
    """
    if not (state.catalog_modifier_active
            or state.leader_modifier_active
            or state.click_to_add_active):
        return
    pipeline = state.pipeline
    if pipeline is None or pipeline.project is None:
        return
    if not pipeline.project.capture_points:
        return

    frame_idx = state.selected_frame
    cached = state.catalog_fov_cache.get(frame_idx)
    if cached is not None:
        payload = cached
    else:
        try:
            payload = _compute_catalog_fov_slice(state, frame_idx)
        except FileNotFoundError as exc:
            logger.warning("catalog missing: %s", exc)
            try:
                ui.notify(
                    "Catalog data/catalog.csv missing — "
                    "run `make build-catalog`",
                    type="negative", timeout=6000,
                )
            except RuntimeError:
                pass
            return
        except Exception:
            logger.exception(
                "catalog FOV-slice failed for frame %d", frame_idx,
            )
            return
        state.catalog_fov_cache[frame_idx] = payload

    overlay_id = state.catalog_overlay_id
    try:
        import json as _json
        ui.run_javascript(
            f"if (window.__catalogOverlaySetObjects) "
            f"{{ window.__catalogOverlaySetObjects("
            f"{overlay_id!r}, {_json.dumps(payload)}); }}",
        )
    except RuntimeError:
        pass


def _compute_catalog_fov_slice(
    state: _RenderState,
    frame_idx: int,
) -> dict:
    """Return a JSON-ready dict describing the catalog objects visible in ``frame_idx``.

    Shape::

        {
            "frame_index": int,
            "frame_dims": [orig_w, orig_h],
            "objects": [
                {
                    "id": str, "name": str, "ra": float, "dec": float,
                    "mag": float, "type": str, "catalog": str,
                    "pixel_x": float, "pixel_y": float,
                    "separation_deg": float,
                },
                ...
            ],
        }
    """
    from astropy.io import fits
    from astropy.wcs import WCS

    from src.renderer.catalog import objects_in_fov
    from src.renderer.wcs import (
        apply_wcs_flip,
        build_wcs,
        pixel_scale_from_fits_header,
        project_catalog_to_pixels,
    )

    pipeline = state.pipeline
    assert pipeline is not None and pipeline.project is not None  # checked by caller
    project = pipeline.project
    ref_point = next(
        (p for p in project.capture_points if p.index == frame_idx),
        project.capture_points[0],
    )

    debayered = pipeline.debayered_frame(ref_point.index)
    orig_h, orig_w = debayered.shape[:2]

    # Capture apps (Ekos, NINA) write a full WCS into every frame —
    # CRVAL, CRPIX, CDELT, CROTA, CTYPE — that already encodes the real
    # camera rotation, the meridian-flip pierside, and east/north
    # direction signs that vary per setup. Trying to hand-roll the
    # equivalent ourselves was wrong twice in a row (#152 smoke tests);
    # the right move is to defer to astropy and only fall back to our
    # synthetic WCS when the header is genuinely missing.
    fits_path = pipeline.frames[ref_point.index].fits_path
    try:
        header = fits.getheader(fits_path)
    except (OSError, ValueError):
        header = None

    center_ra, center_dec = ref_point.ra, ref_point.dec
    wcs: WCS | None = None
    scale: float | None = None
    if header is not None and "CTYPE1" in header and "CRVAL1" in header:
        try:
            wcs = WCS(header)
            center_ra = float(header["CRVAL1"])
            center_dec = float(header["CRVAL2"])
        except Exception:  # noqa: BLE001 — astropy raises a heap of types
            wcs = None
    scale = pixel_scale_from_fits_header(header)
    if scale is None:
        scale = settings.pixel_scale_arcsec
    if wcs is None:
        wcs = build_wcs(
            center_ra_deg=center_ra,
            center_dec_deg=center_dec,
            frame_dims=(orig_w, orig_h),
            pixel_scale_arcsec=scale,
            north_angle_deg=project.north_angle_deg,
        )
    if project.wcs_flip_180:
        wcs = apply_wcs_flip(wcs)

    # FOV-radius: the larger of the half-diagonal in degrees. Pick a
    # generous multiplier (1.1x) so objects near the corners aren't
    # clipped by sub-degree projection error.
    half_diag_arcsec = (
        ((orig_w ** 2 + orig_h ** 2) ** 0.5) / 2.0
        * scale
    )
    fov_radius_deg = (half_diag_arcsec / 3600.0) * 1.1

    matches = objects_in_fov(
        center_ra, center_dec, fov_radius_deg,
    )
    with_pixels = project_catalog_to_pixels(matches, wcs, (orig_w, orig_h))
    return {
        "frame_index": frame_idx,
        "frame_dims": [orig_w, orig_h],
        "objects": with_pixels,
    }


def _catalog_overlay_script(overlay_id: str) -> str:
    """Vanilla-JS overlay: hover -> nearest-object tooltip, click -> emit event.

    Same pattern as the histogram drag-handle overlay (#111): we
    inject one ``<script>`` block per page, parameterised by the
    overlay id so multiple instances coexist safely. The script
    listens on pointermove/click on the overlay div, runs a linear
    nearest-search over the (≤ a few hundred) catalog points the
    server pushed, and renders a floating tooltip purely in DOM
    (no WebSocket roundtrip per move).
    """
    return f"""<script>
(function() {{
  const OVERLAY_ID = {overlay_id!r};

  if (!window.__catalogOverlayState) window.__catalogOverlayState = {{}};
  const state = window.__catalogOverlayState[OVERLAY_ID] = (
    window.__catalogOverlayState[OVERLAY_ID]
    || {{addMode: false, catalogModifier: false, leaderModifier: false,
         objects: [], frameIndex: 0,
         natW: 0, natH: 0, tooltipEl: null,
         pendingTarget: null, leaderCanvas: null}}
  );

  function ensureTooltip(overlay) {{
    if (state.tooltipEl && document.body.contains(state.tooltipEl)) {{
      return state.tooltipEl;
    }}
    const el = document.createElement('div');
    el.style.cssText = (
      'position: fixed; pointer-events: none; z-index: 9999; '
      + 'background: rgba(20,20,25,0.92); color: #fff; '
      + 'padding: 4px 8px; border-radius: 4px; font-size: 12px; '
      + 'font-family: sans-serif; display: none; '
      + 'border: 1px solid rgba(255,255,255,0.2); white-space: nowrap;'
    );
    document.body.appendChild(el);
    state.tooltipEl = el;
    return el;
  }}


  // Project a screen-space coordinate inside the overlay onto the
  // original-frame pixel space. The overlay is sized exactly like the
  // <img>, so its bounding rect gives us the displayed size; we scale
  // by (natW/dispW) to get natural-image pixels and again by
  // (orig/nat) — which is 1.0 here because the JPEG is downsampled
  // BEFORE we ship it to the browser. The orig->nat scale lives in
  // the payload's frame_dims relative to the image's naturalWidth.
  function overlayToOrigPixel(overlay, evt) {{
    // Use the IMG's bounding rect, not the overlay's. The overlay div
    // sits inside ``preview_stack`` and is sized ``absolute inset-0``
    // (fills the parent). NiceGUI's layout may add padding/margin so
    // the parent is slightly larger than the IMG, which would shift
    // every click a few pixels to the right (#153 smoke).
    const img = overlay.parentElement
      ? overlay.parentElement.querySelector('img')
      : null;
    const rect = (img && img.getBoundingClientRect)
      ? img.getBoundingClientRect()
      : overlay.getBoundingClientRect();
    const dispW = Math.max(1, rect.width);
    const dispH = Math.max(1, rect.height);
    const cssX = evt.clientX - rect.left;
    const cssY = evt.clientY - rect.top;
    const natW = (img && img.naturalWidth) || dispW;
    const natH = (img && img.naturalHeight) || dispH;
    const natX = cssX * (natW / dispW);
    const natY = cssY * (natH / dispH);
    const origW = (state.frameDims && state.frameDims[0]) ? state.frameDims[0] : natW;
    const origH = (state.frameDims && state.frameDims[1]) ? state.frameDims[1] : natH;
    const scale = origW / Math.max(1, natW);
    return {{
      origX: natX * scale,
      origY: natY * scale,
      cssX, cssY,
      origW, origH,
    }};
  }}

  function nearest(origX, origY) {{
    let best = null;
    let bestD2 = Infinity;
    for (const obj of state.objects) {{
      const dx = obj.pixel_x - origX;
      const dy = obj.pixel_y - origY;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestD2) {{
        bestD2 = d2;
        best = obj;
      }}
    }}
    return best ? {{obj: best, dist: Math.sqrt(bestD2)}} : null;
  }}

  function ensureLeaderCanvas(overlay) {{
    if (state.leaderCanvas && overlay.contains(state.leaderCanvas)) {{
      const rect = overlay.getBoundingClientRect();
      state.leaderCanvas.width = rect.width;
      state.leaderCanvas.height = rect.height;
      return state.leaderCanvas;
    }}
    const c = document.createElement('canvas');
    const rect = overlay.getBoundingClientRect();
    c.width = rect.width;
    c.height = rect.height;
    c.style.cssText = (
      'position: absolute; top: 0; left: 0; pointer-events: none; '
      + 'width: 100%; height: 100%;'
    );
    overlay.appendChild(c);
    state.leaderCanvas = c;
    return c;
  }}

  function projOrigToCss(overlay, origX, origY) {{
    // Match overlayToOrigPixel: project into the IMG's coord system,
    // then translate to overlay-rect-relative coords (the canvas the
    // rubber-band draws on is sized to the overlay rect, so the
    // rubber-band start point must be in overlay-rel space).
    const overlayRect = overlay.getBoundingClientRect();
    const img = overlay.parentElement
      ? overlay.parentElement.querySelector('img')
      : null;
    const imgRect = (img && img.getBoundingClientRect)
      ? img.getBoundingClientRect()
      : overlayRect;
    const natW = (img && img.naturalWidth) || imgRect.width;
    const natH = (img && img.naturalHeight) || imgRect.height;
    const origW = (state.frameDims && state.frameDims[0]) || natW;
    const origH = (state.frameDims && state.frameDims[1]) || natH;
    // orig px → img-rel CSS px → overlay-rel CSS px (for canvas drawing).
    return {{
      cssX: origX * (imgRect.width / Math.max(1, origW)) + (imgRect.left - overlayRect.left),
      cssY: origY * (imgRect.height / Math.max(1, origH)) + (imgRect.top - overlayRect.top),
    }};
  }}

  function drawRubberBand(overlay, ev) {{
    if (!state.pendingTarget) return;
    const c = ensureLeaderCanvas(overlay);
    const ctx = c.getContext('2d');
    ctx.clearRect(0, 0, c.width, c.height);
    const rect = overlay.getBoundingClientRect();
    const start = projOrigToCss(overlay, state.pendingTarget.origX, state.pendingTarget.origY);
    ctx.strokeStyle = 'rgba(255, 255, 0, 0.9)';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(start.cssX, start.cssY);
    ctx.lineTo(ev.clientX - rect.left, ev.clientY - rect.top);
    ctx.stroke();
  }}

  function onMove(ev) {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) return;

    // Rubber-band: only when Add-Mode armed, Leader on, and first
    // click has landed.
    if (state.addMode && state.leaderModifier && state.pendingTarget) {{
      drawRubberBand(overlay, ev);
    }}

    // Catalog tooltip: enabled whenever Catalog modifier is on,
    // regardless of Add-Mode. Pure information overlay.
    if (state.catalogModifier && state.objects && state.objects.length > 0) {{
      const proj = overlayToOrigPixel(overlay, ev);
      const hit = nearest(proj.origX, proj.origY);
      if (hit) {{
        const tip = ensureTooltip(overlay);
        const id = hit.obj.id || '';
        const name = hit.obj.name || id;
        const label = id && id !== name ? name + ' (' + id + ')' : name;
        // Cursor → object distance in arcmin. Derive deg/orig-px
        // from the hit object's known angular separation from the
        // frame centre + its pixel offset from the same centre —
        // works without us shipping the WCS down to the JS side
        // (#152, restored after the #154 refactor).
        const objDistFromCentre = Math.max(1e-6, Math.hypot(
          hit.obj.pixel_x - proj.origW / 2,
          hit.obj.pixel_y - proj.origH / 2,
        ));
        const degPerOrigPx = (hit.obj.separation_deg !== undefined)
          ? hit.obj.separation_deg / objDistFromCentre : 0;
        const arcmin = (hit.dist * degPerOrigPx * 60.0).toFixed(1);
        tip.textContent = label + "  ·  " + arcmin + "'";
        tip.style.left = (ev.clientX + 14) + 'px';
        tip.style.top = (ev.clientY + 14) + 'px';
        tip.style.display = 'block';
        return;
      }}
    }}
    if (state.tooltipEl) state.tooltipEl.style.display = 'none';
  }}

  function onLeave() {{
    if (state.tooltipEl) state.tooltipEl.style.display = 'none';
  }}

  function clearRubberBand() {{
    state.pendingTarget = null;
    if (state.leaderCanvas) {{
      const ctx = state.leaderCanvas.getContext('2d');
      ctx.clearRect(0, 0, state.leaderCanvas.width, state.leaderCanvas.height);
    }}
  }}

  function emitPlacement(payload) {{
    try {{
      if (window.emitEvent) {{
        window.emitEvent('label_placement', payload);
      }}
    }} catch (e) {{
      console.warn('label_placement emit failed', e);
    }}
  }}

  function onClick(ev) {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay || !state.addMode) return;
    const proj = overlayToOrigPixel(overlay, ev);

    if (state.leaderModifier && !state.pendingTarget && !state.catalogModifier) {{
      // Manual leader: first of two clicks.
      state.pendingTarget = {{origX: proj.origX, origY: proj.origY}};
      return;
    }}

    if (state.leaderModifier && state.pendingTarget) {{
      // Manual leader: second click — commit.
      emitPlacement({{
        kind: 'manual_leader',
        target_x: state.pendingTarget.origX,
        target_y: state.pendingTarget.origY,
        text_x: proj.origX, text_y: proj.origY,
        ref_frame_index: state.frameIndex,
      }});
      clearRubberBand();
      return;
    }}

    if (state.catalogModifier) {{
      if (!state.objects || state.objects.length === 0) {{
        // No catalog data in FOV — fall through to manual so the
        // user's click isn't wasted (#154 §12).
        emitPlacement({{
          kind: 'manual',
          target_x: proj.origX, target_y: proj.origY,
          ref_frame_index: state.frameIndex,
        }});
        return;
      }}
      const hit = nearest(proj.origX, proj.origY);
      if (!hit) return;
      if (state.leaderModifier) {{
        emitPlacement({{
          kind: 'catalog_leader',
          target_x: hit.obj.pixel_x, target_y: hit.obj.pixel_y,
          text_x: proj.origX, text_y: proj.origY,
          ref_frame_index: state.frameIndex,
          catalog_id: hit.obj.id, catalog_name: hit.obj.name,
          ra: hit.obj.ra, dec: hit.obj.dec,
        }});
      }} else {{
        emitPlacement({{
          kind: 'catalog',
          target_x: hit.obj.pixel_x, target_y: hit.obj.pixel_y,
          ref_frame_index: state.frameIndex,
          catalog_id: hit.obj.id, catalog_name: hit.obj.name,
          ra: hit.obj.ra, dec: hit.obj.dec,
        }});
      }}
      return;
    }}

    // No modifiers: plain manual placement.
    emitPlacement({{
      kind: 'manual',
      target_x: proj.origX, target_y: proj.origY,
      ref_frame_index: state.frameIndex,
    }});
  }}

  function onKeyDown(ev) {{
    if (ev.key !== 'Escape') return;
    if (!state.leaderModifier || !state.pendingTarget) return;
    clearRubberBand();
    ev.preventDefault();
  }}

  function attach() {{
    const overlay = document.getElementById(OVERLAY_ID);
    if (!overlay) {{ setTimeout(attach, 50); return; }}
    if (overlay.__catBound) {{ return; }}
    overlay.__catBound = true;
    overlay.addEventListener('pointermove', onMove);
    overlay.addEventListener('pointerleave', onLeave);
    overlay.addEventListener('click', onClick);
    document.addEventListener('keydown', onKeyDown);
  }}

  window.__catalogOverlaySetState = window.__catalogOverlaySetState
    || function(id, payload) {{
      const s = window.__catalogOverlayState && window.__catalogOverlayState[id];
      if (!s || !payload) return;
      s.addMode = !!payload.addMode;
      s.catalogModifier = !!payload.catalogModifier;
      s.leaderModifier = !!payload.leaderModifier;
      const overlay = document.getElementById(id);
      if (overlay) {{
        const active = s.addMode || s.catalogModifier;
        overlay.style.pointerEvents = active ? 'auto' : 'none';
        overlay.style.cursor = s.addMode ? 'crosshair' : (active ? 'default' : '');
      }}
      if (!s.addMode) {{
        s.pendingTarget = null;
        if (s.leaderCanvas) {{
          const ctx = s.leaderCanvas.getContext('2d');
          ctx.clearRect(0, 0, s.leaderCanvas.width, s.leaderCanvas.height);
        }}
      }}
      if (s.tooltipEl && !s.catalogModifier) {{
        s.tooltipEl.style.display = 'none';
      }}
    }};

  window.__catalogOverlaySetObjects = window.__catalogOverlaySetObjects
    || function(id, payload) {{
      const s = window.__catalogOverlayState && window.__catalogOverlayState[id];
      if (!s) return;
      s.objects = (payload && payload.objects) || [];
      s.frameIndex = (payload && payload.frame_index) || 0;
      s.frameDims = (payload && payload.frame_dims) || null;
    }};

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', attach);
  }} else {{
    attach();
  }}
}})();
</script>"""


def _apply_preview_mode(state: _RenderState) -> None:
    """Apply the wrapper/image class strings for the current preview mode.

    Detail mode: wrapper carries ``max-h-screen overflow-auto`` so the
    image stays inside the viewport but scrolls if larger; image renders
    at native size (``object-contain`` only, no width clamp).

    Compact mode: wrapper is a passthrough; image is constrained to
    ``w-full max-h-96`` — the pre-#148 behaviour.

    Also updates the toggle button's icon + tooltip so the affordance
    reflects the *next* action, not the current state.
    """
    wrapper = state.preview_wrapper
    image = state.preview
    button = state.preview_detail_button
    if wrapper is None or image is None:
        return
    if state.preview_detail_mode:
        wrapper.classes(replace="w-full max-h-screen overflow-auto")
        image.classes(replace="object-contain")
        if button is not None:
            button.props("icon=fullscreen_exit")
            button.tooltip("Kompakte Ansicht")
    else:
        wrapper.classes(replace="w-full")
        image.classes(replace="w-full max-h-96 object-contain")
        if button is not None:
            button.props("icon=fullscreen")
            button.tooltip("Vorschau in Vollauflösung")


def _toggle_preview_detail(state: _RenderState) -> None:
    """Flip the preview detail mode, re-apply classes, and persist."""
    state.preview_detail_mode = not state.preview_detail_mode
    _apply_preview_mode(state)
    _save_render_state(state)


def _handle_label_placement(state: _RenderState, event) -> None:  # noqa: ANN001
    """Single click-event handler for all four modifier combinations.

    Receives the JS overlay's ``label_placement`` event, extracts the
    placement geometry + catalog metadata from the payload, then opens
    the create-dialog with the right pre-fills.
    """
    if not state.pipeline or not state.pipeline.project:
        return
    if not state.click_to_add_active:
        return
    args = event.args or {}
    if isinstance(args, list) and args:
        args = args[0]
    if not isinstance(args, dict):
        return

    try:
        kind = args.get("kind") or "manual"
        target_x = float(args["target_x"])
        target_y = float(args["target_y"])
        ref_frame_index = int(args.get("ref_frame_index", state.selected_frame))
    except (KeyError, TypeError, ValueError):
        logger.warning("label_placement payload malformed: %r", args)
        return

    text_pos: tuple[float, float] | None = None
    if "text_x" in args and "text_y" in args:
        try:
            text_pos = (float(args["text_x"]), float(args["text_y"]))
        except (TypeError, ValueError):
            text_pos = None

    catalog_meta: dict | None = None
    if kind in ("catalog", "catalog_leader"):
        try:
            catalog_meta = {
                "catalog_id": args.get("catalog_id"),
                "catalog_name": args.get("catalog_name"),
                "ra": float(args["ra"]),
                "dec": float(args["dec"]),
            }
        except (KeyError, TypeError, ValueError):
            catalog_meta = None

    leader_default = (
        "line" if kind in ("manual_leader", "catalog_leader") else "none"
    )

    _open_create_dialog(
        state,
        ref_frame_index=ref_frame_index,
        target=(target_x, target_y),
        text=text_pos,
        catalog_meta=catalog_meta,
        leader_default=leader_default,
    )


def _open_create_dialog(
    state: _RenderState,
    *,
    ref_frame_index: int,
    target: tuple[float, float],
    text: tuple[float, float] | None,
    catalog_meta: dict | None,
    leader_default: str,
) -> None:
    """Open the new-label dialog with prefilled values per modifier matrix."""
    target_x, target_y = target
    if text is not None:
        text_x, text_y = text
    else:
        text_x, text_y = target_x + 12, target_y

    default_text = (
        (catalog_meta.get("catalog_name") or catalog_meta.get("catalog_id") or "?")
        if catalog_meta else "Label"
    )
    # With a leader line the marker is redundant — the line already
    # points at the target. Default to no marker so the target stays
    # visible (#154 follow-up).
    if leader_default != "none":
        default_marker = "none"
    elif catalog_meta:
        default_marker = "circle"
    else:
        default_marker = "dot"

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label("New label").classes("text-md font-bold")
        text_in = ui.input("Text", value=default_text)
        color_in = ui.input("Color (hex)", value=state.last_label_color)
        font_size_in = ui.number(
            "Font size", value=state.last_label_font_size, min=6, max=200,
        )
        marker_in = ui.select(
            ["none", "dot", "cross", "circle"],
            value=default_marker, label="Marker",
        )
        leader_in = ui.select(
            {"none": "no leader", "line": "line", "arrow": "arrow"},
            value=leader_default, label="Leader",
        )
        offset_radius_in = ui.number(
            "Offset radius (px)", value=state.last_label_offset_radius,
            min=0, max=500,
        ).tooltip(
            "Pixel-Abstand zwischen Linie und Target/Text — verhindert "
            "dass die Linie das Objekt überdeckt. Ignoriert bei "
            "leader='none'.",
        )
        # NiceGUI's ``ui.number(value=X)`` propagates X to the DOM but
        # widget.value may still report None until the user actively
        # edits the field — same Quasar/Vue binding gotcha as in #131.
        # Force a Python-side ``set_value`` so reading ``.value`` in
        # _save returns the prefill instead of None.
        font_size_in.set_value(state.last_label_font_size)
        offset_radius_in.set_value(state.last_label_offset_radius)

        def _num(widget, fallback: int) -> int:
            """Read a ``ui.number`` value, treating None as 'untouched'."""
            v = widget.value
            return int(v) if v is not None else int(fallback)

        with ui.row().classes("w-full justify-end"):
            def _cancel() -> None:
                dialog.close()
                _disarm_add_label(state)

            ui.button("Cancel", on_click=_cancel).props("flat")

            def _save() -> None:
                label = Label(
                    id=str(uuid.uuid4()),
                    text=text_in.value or default_text,
                    ref_frame_index=ref_frame_index,
                    x=target_x, y=target_y,
                    color=color_in.value or state.last_label_color,
                    font_size=_num(font_size_in, state.last_label_font_size),
                    marker=marker_in.value or default_marker,
                    text_offset_x=int(round(text_x - target_x)),
                    text_offset_y=int(round(text_y - target_y)),
                    leader=leader_in.value or "none",
                    offset_radius=_num(
                        offset_radius_in, state.last_label_offset_radius,
                    ),
                    source="catalog" if catalog_meta else "manual",
                    catalog_id=(catalog_meta or {}).get("catalog_id"),
                    catalog_ra=(catalog_meta or {}).get("ra"),
                    catalog_dec=(catalog_meta or {}).get("dec"),
                )
                state.pipeline.project.labels.append(label)
                state.last_label_color = label.color
                state.last_label_font_size = label.font_size
                state.last_label_offset_radius = label.offset_radius
                _save_render_state(state)
                _persist_project(state)
                _refresh_labels_list(state)
                _schedule_preview_refresh(state)
                dialog.close()
                _disarm_add_label(state)

            ui.button("Save", color="primary", on_click=_save)
    dialog.open()


class _RenderState:
    """Mutable state for the render UI."""

    def __init__(self) -> None:
        """Initialize default render state.

        After #151 only the app-level fields (paths, workers, UI prefs)
        come from ``app.storage.general["render"]``. Project-level
        fields (stretch tuning, output format, alignment) are seeded
        from :class:`RenderSettings` defaults and overwritten when a
        project is actually loaded.
        """
        stored = _load_render_state()
        # ---- App-level (machine-global) ----
        self.input_dir: str = stored.get("input_dir", "./output/")
        self.output_path: str = stored.get("output_path", "output.mp4")
        # Worker count for alignment + stretch (issue #120). -1 means
        # all CPU cores; default 4 is a memory-safe baseline. The
        # source priority is GUI (this field) > CLI > env > settings;
        # ``settings.render_workers`` already absorbs the env var via
        # pydantic_settings, so picking it as the fallback here gives
        # us "env wins over default" for free on first launch.
        self.render_workers: int = stored.get(
            "render_workers", settings.render_workers,
        )

        # ---- Project-level (issue #151: per-project, in manifest) ----
        # Initialised from RenderSettings defaults so the UI has sane
        # starting values before any project is loaded. Once a project
        # is opened, ``_apply_render_settings_to_state`` overwrites
        # these with that project's persisted values.
        _rs_defaults = RenderSettings()
        self.stretch_mode: str = _rs_defaults.stretch_mode
        self.black: float = _rs_defaults.black
        self.white: float = _rs_defaults.white
        self.midtone: float = _rs_defaults.midtone
        self.transition: str = _rs_defaults.transition
        self.fps: int = _rs_defaults.fps
        self.crf: int = _rs_defaults.crf
        self.speed: float = _rs_defaults.speed
        self.crossfade_frames: int = _rs_defaults.crossfade_frames
        self.align_max_dim: int = _rs_defaults.align_max_dim
        self.align_sigma: float = _rs_defaults.align_sigma
        # Tail-blend frames for linear_pan transitions (issue #126).
        # 0 (default) = no blending; pre-#126 byte-identical output.
        self.linear_pan_blend_tail: int = _rs_defaults.linear_pan_blend_tail
        self.resolution: str = _rs_defaults.resolution
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
        # Music-track fields (issue #156). Persisted per-project.
        self.music_track: str | None = None
        self.include_music: bool = True
        self.include_labels: bool = True
        self.loop_music: bool = True
        self.auto_stretch_ref_frame: int | None = None
        # UI handles populated by ``_build_auto_freeze_controls`` —
        # updated by ``_update_ref_frame_indicator`` whenever the
        # reference frame or selected frame changes.
        self.auto_stretch_ref_label: ui.label | None = None
        self.auto_stretch_apply_button: ui.button | None = None
        self.auto_stretch_freeze_row: ui.row | None = None
        self.frame_reset_button: ui.button | None = None
        # Labels panel (issue #131). ``labels_panel`` is the outer
        # ``ui.expansion``; ``labels_list_container`` is the inner column
        # whose children are rebuilt by ``_refresh_labels_list``.
        # ``click_to_add_active`` is the one-shot "Add Label armed"
        # flag, flipped by ``_arm_add_label`` / ``_disarm_add_label``
        # (#154). While True, the JS overlay routes the next click(s)
        # into a ``label_placement`` event.
        self.labels_panel: ui.expansion | None = None
        self.labels_list_container: ui.column | None = None
        # ID of the row currently highlighted in the labels list — set
        # when the user clicks a row (#154 follow-up). Ephemeral: not
        # persisted, resets to ``None`` on page reload.
        self.selected_label_id: str | None = None
        self.click_to_add_active: bool = False
        # Holds the first click of a two-click leader placement; cleared
        # when the second click arrives, ESC cancels, or Add-Label
        # disarms. Runtime-only — never persisted.
        self.pending_placement: tuple[float, float] | None = None
        # Add-label modifiers (issue #154). Catalog and Leader used to
        # be exclusive modes (#152, #153) — they're now persistent
        # checkboxes that shape what the one-shot "Add Label" button
        # does. The legacy ``catalog_mode_active`` / ``leader_mode_active``
        # keys soft-migrate on first read so users who had a mode on
        # before the upgrade keep their preference.
        self.catalog_modifier_active: bool = bool(
            stored.get("catalog_modifier_active",
                       stored.get("catalog_mode_active", False)),
        )
        self.leader_modifier_active: bool = bool(
            stored.get("leader_modifier_active",
                       stored.get("leader_mode_active", False)),
        )
        # Drop legacy keys so we don't migrate twice and they don't
        # diverge from the new ones.
        for legacy in ("catalog_mode_active", "leader_mode_active"):
            stored.pop(legacy, None)
        self.catalog_overlay_id: str = ""
        self.catalog_overlay: ui.element | None = None
        self.catalog_modifier_checkbox: ui.checkbox | None = None
        self.leader_modifier_checkbox: ui.checkbox | None = None
        self.wcs_flip_checkbox: ui.checkbox | None = None
        self.add_label_button: ui.button | None = None
        self.catalog_fov_cache: dict[int, dict] = {}
        # Preview display mode (issue #148). False = compact (max-h-96,
        # fast overview); True = detail (native size, scrollable — for
        # pixel-precise label placement on high-res frames).
        self.preview_detail_mode: bool = stored.get(
            "preview_detail_mode", False,
        )
        # Sticky label-styling defaults (#154 follow-up). Color, font
        # size, and offset radius pre-fill the next create/edit dialog
        # with whatever the user last saved. Marker and leader stay
        # modifier-driven so toggling Leader still flips marker to
        # "none" and back without depending on stale stickies.
        self.last_label_color: str = stored.get("last_label_color", "#ffff00")
        self.last_label_font_size: int = int(
            stored.get("last_label_font_size", 24),
        )
        self.last_label_offset_radius: int = int(
            stored.get("last_label_offset_radius", 50),
        )
        # UI handles for the wrapper + toggle button, populated by
        # ``create_render_layout``. ``_apply_preview_mode`` reads them
        # to switch class strings + button icon/tooltip on demand.
        self.preview_wrapper: ui.element | None = None
        self.preview_path_label: ui.label | None = None
        self.preview_detail_button: ui.button | None = None


async def _load(state: _RenderState) -> None:
    """Load a capture directory asynchronously.

    Args:
        state: Mutable render UI state.
    """
    import asyncio

    if state.loading:
        ui.notify("Load already in progress", type="warning")
        return
    # Snapshot the legacy app.storage dict BEFORE the first _save_render_state
    # call below — that call wipes the dict down to the new app-only fields
    # and would erase any legacy project-level keys (stretch_mode, fps, …)
    # we want to soft-migrate into the about-to-be-loaded project (#151).
    legacy_app_store = dict(app.storage.general.get("render", {}))
    # Persist current UI state on Load too (not just Render): committing to
    # a capture directory is the natural moment to remember it for next
    # time, even if the user never proceeds to render. With #151 this
    # also writes the OUTGOING project's render_settings back to its
    # manifest before we open the new project — so tuning never leaks
    # between projects via in-memory state.
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
        # New project => recompute the catalog FOV-slice from scratch.
        state.catalog_fov_cache.clear()
        # Soft-migrate any legacy app.storage render fields into the
        # just-loaded project (#151). The first ``_save_render_state``
        # above already pruned the live ``app.storage["render"]`` down
        # to the new app-only fields, so we pass the pre-prune snapshot
        # ``legacy_app_store`` to recover the project-level values. The
        # migration drops those keys from ``legacy_app_store`` — we
        # don't need to mirror that into the live dict because it
        # already lacks them. After migration we apply the project's
        # render_settings to the UI state so the user sees the project's
        # own look.
        if pipeline.project is not None:
            migrated = _maybe_soft_migrate_render_settings(
                pipeline.project,
                legacy_app_store,
            )
            if migrated:
                # Persist the migrated values into the manifest now so a
                # crash before the user touches anything doesn't lose them.
                _persist_project(state)
            _apply_render_settings_to_state(
                state, pipeline.project.render_settings,
            )
        # Seed initial auto-stretch params from frame 0 so freeze=True
        # has something sensible at first paint. The user can switch
        # the reference frame later via "Aktuelles Frame übernehmen".
        # If frame 0 fails to load for any reason we fall back to
        # params=None — the freeze becomes effectively inactive until
        # the user sets a reference manually.
        #
        # #151: if the project already carries auto_stretch_params (set
        # earlier in this session or migrated/loaded from the manifest),
        # keep them — they're the user's frozen reference, recomputing
        # would flicker. Only seed when no params were applied.
        state.auto_stretch_ref_frame = None
        if state.auto_stretch_params is None and pipeline.frames:
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
            _refresh_labels_list(state)
            # Push the persisted catalog-mode toggle state into the JS
            # overlay now that a project is loaded (issue #152). Also
            # primes the FOV-slice if mode was already on from a prior
            # session — saves the user a second click.
            _push_overlay_state(state)
            if (state.catalog_modifier_active
                    or state.leader_modifier_active
                    or state.click_to_add_active):
                _refresh_catalog_fov_slice(state)
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
    # Tooltip shows the absolute FITS path so the user can verify which
    # capture file is which without leaving the renderer.
    fits_path = ""
    if state.pipeline and 0 <= idx < len(state.pipeline.frames):
        fits_path = str(state.pipeline.frames[idx].fits_path.resolve())
    card = ui.card().classes("cursor-pointer").on(
        "click", lambda _, ii=idx: _show_preview(state, ii),
    )
    if fits_path:
        card.tooltip(fits_path)
    with card:
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
    # the user navigates away from the current reference, refresh the
    # per-frame Reset button to reflect this frame's override, and
    # reseed the B/W/M sliders from whichever stretch_params source
    # is active for the new frame (project or per-frame).
    _update_ref_frame_indicator(state)
    _refresh_reset_button(state)
    _sync_sliders_to_current_frame(state)
    # Recompute the catalog FOV-slice for the new frame and ship it
    # to the JS overlay (issue #152). Cheap when cached.
    if (state.catalog_modifier_active
            or state.leader_modifier_active
            or state.click_to_add_active):
        _refresh_catalog_fov_slice(state)

    # Keep the pipeline config in sync with state so the preview
    # honors the freeze toggle and current frozen params (WYSIWYG —
    # preview matches what the render will produce).
    state.pipeline.config.auto_stretch_freeze = state.auto_stretch_freeze
    state.pipeline.config.auto_stretch_params = state.auto_stretch_params

    def _build() -> str | None:
        try:
            stretched = state.pipeline.stretch_frame(frame_idx)
            img = Image.fromarray(stretched)
            orig_w, orig_h = img.size
            # The preview <img> is constrained to max-h-96 (~384 px);
            # 1280 px gives plenty of headroom for zoom/expand without
            # blowing past the WebSocket message size limit.
            img.thumbnail((1280, 1280))
            thumb_w, thumb_h = img.size

            # Burn in the labels whose reference frame is this frame.
            # In the preview we deliberately don't use the alignment
            # chain — when no render has run yet ``_alignments`` is
            # empty and ``cumulative_offset`` falls back to (0, 0),
            # which would draw every label at its stored pixel for
            # every frame. That's misleading. Showing only labels
            # anchored on the displayed frame is honest and gives the
            # user instant visual feedback right after a click.
            if state.pipeline.project:
                preview_scale = thumb_w / orig_w if orig_w else 1.0
                from src.renderer.labels import _draw_labels  # local import to avoid cycle at module load
                # Scale ALL pixel-sized fields by preview_scale, not just
                # the marker position. text_offset_x/y and font_size live
                # in orig-pixel space; copying them through without
                # scaling makes leader lines render ~1/preview_scale
                # times longer than the user clicked (#153 smoke). The
                # final render writes labels to the full-resolution
                # frame and is unaffected — this only matters for the
                # preview JPEG.
                # Keep font_size at the orig-pixel value: the preview
                # is for label-placement feedback, not WYSIWYG with the
                # final video. Scaling fonts down by ~5x would make
                # them illegible (font_size=24 → 5px in the thumb).
                here = [
                    lbl.model_copy(update={
                        "x": lbl.x * preview_scale,
                        "y": lbl.y * preview_scale,
                        "text_offset_x": int(round(lbl.text_offset_x * preview_scale)),
                        "text_offset_y": int(round(lbl.text_offset_y * preview_scale)),
                        # offset_radius is a pixel measurement too — leaving
                        # it at orig-pixel value in a thumb-scaled frame
                        # collapses the leader line (gap > line length) so
                        # the preview shows almost no visible leader, while
                        # the final video correctly scales it (#154 follow-up).
                        "offset_radius": int(round(lbl.offset_radius * preview_scale)),
                    })
                    for lbl in state.pipeline.project.labels
                    if lbl.ref_frame_index == frame_idx
                ]
                if here:
                    import numpy as np
                    arr = np.asarray(img)
                    arr = _draw_labels(
                        arr, here, [(0.0, 0.0)] * len(here),
                        (thumb_w, thumb_h),
                    )
                    img = Image.fromarray(arr)

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
        # Caption below the image — absolute path of the displayed
        # frame so the user always sees which file is on screen.
        if state.preview_path_label is not None:
            try:
                fits_path = str(
                    state.pipeline.frames[frame_idx].fits_path.resolve()
                )
            except (AttributeError, IndexError):
                fits_path = ""
            state.preview_path_label.text = fits_path
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

    # Shared state polled by ``ui.timer`` and written by the render
    # thread. Holds the latest ``ProgressUpdate`` (or ``None`` until the
    # first increment fires). The pipeline's ``_PhaseProgress`` already
    # serialises increments under a lock, so a plain assignment here is
    # safe — the GIL guarantees a single reference store is atomic.
    progress_state: dict[str, ProgressUpdate | None] = {"latest": None}

    def on_progress(update: ProgressUpdate) -> None:
        progress_state["latest"] = update

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
    progress_state: dict[str, ProgressUpdate | None],
) -> None:
    """Read shared progress state and update the UI.

    Single bar with a phase-dependent colour: orange while we are in the
    ``"prepare"`` (alignment) phase, primary while in ``"render"``. The
    bar value is the *within-phase* fraction — it resets at the phase
    boundary so the user gets clean "5/12 done" feedback per phase
    rather than a single bar that crawls weirdly because alignment
    timing is unknown until measured (#123). The status label spells
    out the phase + counter ("Preparing: pair 5/12") so there's
    no ambiguity about which phase the bar is showing.

    Args:
        state: Mutable render UI state.
        progress_state: Dict holding the latest :class:`ProgressUpdate`
            written by the render thread under key ``"latest"``.
    """
    update = progress_state["latest"]
    if update is None:
        return
    if update.total > 0 and state.progress:
        state.progress.value = update.current / update.total
        # Distinct colour per phase so the bar reset at the boundary
        # reads as "different work, same control" rather than a glitch.
        color = "orange" if update.phase == "prepare" else "primary"
        state.progress.props(f"color={color}")
    if state.status_label:
        state.status_label.text = update.label


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
            black=state.black, white=state.white, midtone=state.midtone,
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
        render_workers=int(state.render_workers),
        linear_pan_blend_tail=int(state.linear_pan_blend_tail),
        render_labels=state.include_labels,
        music_track=state.music_track,
        include_music=state.include_music,
        loop_music=state.loop_music,
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
