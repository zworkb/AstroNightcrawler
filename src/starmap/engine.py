"""NiceGUI wrapper around the Stellarium Web Engine.

Provides a ``StarMap`` class that manages a container element, loads
the JavaScript bridge, and exposes async helpers for camera control
and observer settings.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from nicegui import ui

_BRIDGE_JS = Path(__file__).with_name("bridge.js")
_PATH_OVERLAY_JS = Path(__file__).with_name("path_overlay.js")


class StarMap:
    """Interactive sky-map element backed by Stellarium Web Engine.

    Usage::

        star_map = StarMap()
        await star_map.initialize("/static/stellarium/stellarium-web-engine.wasm",
                                  "/static/skydata")
        await star_map.look_at(ra=83.63, dec=22.01, fov=5.0)
    """

    def __init__(self, width: str = "100%", height: str = "600px") -> None:
        """Create the map container and inject the JS bridge.

        Args:
            width:  CSS width of the container.
            height: CSS height of the container.
        """
        self._container_id = f"starmap-{uuid.uuid4().hex[:8]}"
        self._inject_scripts()
        self._create_container(width, height)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _inject_scripts(self) -> None:
        """Load bridge.js (and path_overlay.js if present) into the page."""
        bridge_src = _BRIDGE_JS.read_text(encoding="utf-8")
        ui.add_body_html(f"<script>\n{bridge_src}\n</script>")

        if _PATH_OVERLAY_JS.exists():
            overlay_src = _PATH_OVERLAY_JS.read_text(encoding="utf-8")
            ui.add_body_html(f"<script>\n{overlay_src}\n</script>")

    def _create_container(self, width: str, height: str) -> None:
        """Insert an HTML container div for the engine canvas."""
        style = f"width:{width};height:{height};overflow:hidden;"
        ui.html(f'<div id="{self._container_id}" style="{style}"></div>')

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def initialize(
        self,
        wasm_url: str = "/static/stellarium/stellarium-web-engine.wasm",
        skydata_url: str = "/static/skydata",
    ) -> None:
        """Initialise the Stellarium engine inside the container.

        Args:
            wasm_url:    URL path to the compiled ``.wasm`` file.
            skydata_url: Base URL for sky-data catalogues.
        """
        js = (
            f"await window.stelBridge.initEngine("
            f"'{self._container_id}', '{wasm_url}', '{skydata_url}')"
        )
        await ui.run_javascript(js)

    async def look_at(
        self,
        ra: float,
        dec: float,
        fov: float = 10.0,
        duration: float = 1.0,
    ) -> None:
        """Animate the camera to the given sky position.

        Args:
            ra:       Right ascension in degrees.
            dec:      Declination in degrees.
            fov:      Target field-of-view in degrees.
            duration: Animation duration in seconds.
        """
        js = f"window.stelBridge.lookAt({ra}, {dec}, {fov}, {duration})"
        await ui.run_javascript(js)

    async def set_observer(self, lat: float, lon: float) -> None:
        """Set the geographic observer location.

        Args:
            lat: Latitude in decimal degrees.
            lon: Longitude in decimal degrees.
        """
        js = f"window.stelBridge.setObserver({lat}, {lon})"
        await ui.run_javascript(js)

    async def get_field_of_view(self) -> float:
        """Return the current field-of-view in degrees."""
        result: float = await ui.run_javascript(
            "return window.stelBridge.getFieldOfView()"
        )
        return result

    @property
    def container_id(self) -> str:
        """DOM id of the map container element."""
        return self._container_id
