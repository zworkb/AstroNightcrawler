"""Catalog alias priority + co-location resolution.

When multiple catalog entries share a sky position (e.g. M31 = NGC
224 = UGC 454), pick the popular name. See
docs/superpowers/specs/2026-06-06-catalog-alias-priority-design.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np

CATALOG_PRIORITY: dict[str, int] = {"M": 0, "C": 1, "NGC": 2, "IC": 3}


def alias_sort_key(entry: dict[str, Any]) -> tuple[int, str, float, str]:
    """Stable sort: (priority, catalog_alpha, mag, id).

    Lower tuple sorts first. ``catalog_alpha`` is only used for the
    'Rest' bucket (priority 4); for the named tiers it's empty so
    M-entries don't shuffle by ``catalog`` value.
    """
    cat = entry.get("catalog", "")
    prio = CATALOG_PRIORITY.get(cat, 4)
    mag_raw = entry.get("mag")
    try:
        mag = float(mag_raw) if mag_raw is not None else 99.0
    except (TypeError, ValueError):
        mag = 99.0
    return (prio, cat if prio == 4 else "", mag, str(entry.get("id", "")))


def find_aliases(
    target: dict[str, Any],
    catalog: list[dict[str, Any]],
    tol_arcsec: float = 5.0,
) -> list[dict[str, Any]]:
    """Return catalog entries co-located with ``target`` (within tol),
    including target itself, priority-sorted."""
    tol_deg = tol_arcsec / 3600.0
    t_ra, t_dec = target["ra"], target["dec"]
    cos_dec = float(np.cos(np.radians(t_dec)))
    matches = [
        e for e in catalog
        if abs(e["dec"] - t_dec) <= tol_deg
        and abs((e["ra"] - t_ra) * cos_dec) <= tol_deg
    ]
    return sorted(matches, key=alias_sort_key)
