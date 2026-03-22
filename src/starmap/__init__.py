"""Stellarium Web Engine integration for NiceGUI.

Provides a StarMap element that wraps the Stellarium WebAssembly engine
with coordinate conversion, observer control, and mouse-event dispatch.
"""

from src.starmap.engine import StarMap

__all__ = [
    "StarMap",
]
