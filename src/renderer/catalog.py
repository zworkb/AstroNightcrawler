"""Sky catalog loader + FOV query.

The catalog (Messier + Caldwell + OpenNGC + IAU named stars) ships
pre-built in ``data/catalog.csv`` — see ``scripts/build_catalog.py``.
This module loads it once into an in-memory list and offers a single
spatial query: ``objects_in_fov``.

Design notes:
  * No pandas dependency. The catalog is ~15k rows; a plain
    ``list[dict]`` plus a vectorised numpy distance check is more
    than fast enough (~5 ms per query). Keeping the renderer extra
    free of pandas matches the rest of the codebase.
  * Loading is lazy and cached at module scope. The first call pays
    ~30 ms; subsequent calls are free.
  * Missing-file error is explicit and actionable (Issue #152 DoD).
"""

from __future__ import annotations

import csv
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Repo-root relative path. The CSV is committed; we never auto-download.
_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "catalog.csv"

_CACHE: list[dict[str, Any]] | None = None


def _coerce_row(raw: dict[str, str]) -> dict[str, Any] | None:
    """Parse one CSV row into typed fields; return ``None`` on bad data."""
    try:
        return {
            "id": raw["id"],
            "name": raw["name"],
            "ra": float(raw["ra"]),
            "dec": float(raw["dec"]),
            "mag": float(raw["mag"]),
            "type": raw["type"],
            "catalog": raw["catalog"],
        }
    except (KeyError, ValueError):
        return None


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the bundled catalog as a list of dicts (cached).

    Args:
        path: Override the catalog CSV path. Useful for tests; production
            code passes ``None`` and gets the bundled file.

    Returns:
        List of dicts with keys ``id, name, ra, dec, mag, type, catalog``
        (ra/dec/mag are floats; the rest strings). The list is shared —
        callers must not mutate it.

    Raises:
        FileNotFoundError: If the catalog CSV is missing (the build script
            never ran or the file was not committed).
    """
    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE

    csv_path = path if path is not None else _CATALOG_PATH
    if not csv_path.exists():
        msg = (
            f"Catalog missing at {csv_path}. "
            "Run `make build-catalog` to regenerate it from upstream "
            "sources (OpenNGC + IAU)."
        )
        raise FileNotFoundError(msg)

    rows: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            entry = _coerce_row(raw)
            if entry is not None:
                rows.append(entry)

    logger.info("Loaded %d catalog rows from %s", len(rows), csv_path)
    if path is None:
        _CACHE = rows
    return rows


def _clear_cache() -> None:
    """Test hook — drop the module-level cache."""
    global _CACHE
    _CACHE = None


def objects_in_fov(
    ra_deg: float,
    dec_deg: float,
    fov_radius_deg: float,
    *,
    catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return catalog rows within ``fov_radius_deg`` of (ra, dec).

    Uses the great-circle haversine distance (vectorised over numpy
    arrays) so the result is correct near the celestial poles where a
    flat ``sqrt(dRA^2 + dDec^2)`` would blow up.

    Args:
        ra_deg: Field-of-view center RA in degrees (J2000).
        dec_deg: Field-of-view center Dec in degrees (J2000).
        fov_radius_deg: Half-angle of the search cone in degrees.
        catalog: Optional injection point for tests. Defaults to the
            bundled catalog via :func:`load_catalog`.

    Returns:
        List of catalog dicts, each enriched with a ``separation_deg``
        float, sorted by separation (closest first).
    """
    cat = catalog if catalog is not None else load_catalog()
    if not cat:
        return []

    ras = np.array([r["ra"] for r in cat])
    decs = np.array([r["dec"] for r in cat])

    sep = _angular_separation_deg(ra_deg, dec_deg, ras, decs)
    mask = sep <= fov_radius_deg
    idxs = np.argsort(sep[mask])
    matched = [r for r, m in zip(cat, mask, strict=True) if m]
    # Re-attach the separation; argsort gives us the order within the
    # matched subset.
    matched_sorted = [matched[i] for i in idxs]
    sep_sorted = sep[mask][idxs]
    result: list[dict[str, Any]] = []
    for row, s in zip(matched_sorted, sep_sorted, strict=True):
        enriched = dict(row)  # don't pollute the shared cache
        enriched["separation_deg"] = float(s)
        result.append(enriched)
    return result


def _angular_separation_deg(
    ra1: float,
    dec1: float,
    ra2: np.ndarray,
    dec2: np.ndarray,
) -> np.ndarray:
    """Vectorised great-circle angular separation in degrees.

    Haversine on the celestial sphere — correct in all sky regions
    including near the poles where Euclidean RA/Dec differences fail.
    """
    phi1 = math.radians(dec1)
    phi2 = np.radians(dec2)
    dphi = phi2 - phi1
    dlam = np.radians(ra2 - ra1)
    a = (
        np.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2.0) ** 2
    )
    return np.degrees(2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0))))
