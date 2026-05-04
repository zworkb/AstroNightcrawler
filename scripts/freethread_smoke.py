"""Smoke test for free-threaded Python (3.13t) renderer dependencies.

Imports each renderer-relevant package one by one and reports:
- whether it imports cleanly,
- whether the GIL is still disabled afterwards (i.e. the package did not
  trigger an "auto-enable GIL" for missing free-threading support).

Run via:  PYTHON_GIL=0 .venv/bin/python scripts/freethread_smoke.py

Issue: zworkb/AstroNightcrawler#107
"""

from __future__ import annotations

import importlib
import sys
import warnings


PACKAGES = [
    "numpy",
    "scipy",
    "scipy.ndimage",
    "astropy",
    "astropy.io.fits",
    "PIL",
    "PIL.Image",
    "colour_demosaicing",
    "sep",
    "astroalign",
    "nicegui",
    "nicegui.json",
]


def gil_state() -> str:
    if hasattr(sys, "_is_gil_enabled"):
        return "ON" if sys._is_gil_enabled() else "OFF"
    return "n/a"


def main() -> int:
    print(f"Python: {sys.version}")
    print(f"Initial GIL: {gil_state()}")
    print("-" * 72)

    rc = 0
    for name in PACKAGES:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            try:
                importlib.import_module(name)
                gil_after = gil_state()
                gil_warn = [
                    str(item.message)
                    for item in w
                    if "GIL" in str(item.message) or "free-thread" in str(item.message).lower()
                ]
                marker = "OK"
                if gil_after == "ON":
                    marker = "GIL-RE-ENABLED"
                    rc = 1
                print(
                    f"[{marker:>14}] {name:<24} GIL={gil_after} "
                    f"warnings={gil_warn or 'none'}"
                )
            except Exception as exc:  # noqa: BLE001
                rc = 1
                print(f"[         FAIL] {name:<24} {type(exc).__name__}: {exc}")

    print("-" * 72)
    print(f"Final GIL: {gil_state()}")

    # Tiny renderer pipeline construction check — do not actually render.
    print("\nRenderer pipeline construction check:")
    try:
        from src.renderer.pipeline import RenderConfig  # type: ignore[import-not-found]

        cfg = RenderConfig()
        print(f"  RenderConfig instantiated: {cfg!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"  RenderConfig FAILED: {type(exc).__name__}: {exc}")
        rc = 1

    return rc


if __name__ == "__main__":
    sys.exit(main())
