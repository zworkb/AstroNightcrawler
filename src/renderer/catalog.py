"""Sky catalog loader + FOV query.

The base catalog (Messier + Caldwell + OpenNGC + IAU named stars) ships
pre-built in ``data/catalog.csv`` — see ``scripts/build_catalog.py``.
Optional add-on catalogs (Sharpless 2, Barnard, Arp, UGC, …) can be
fetched via ``make tier-1``/``tier-2``/``tier-3`` and land at
``data/catalog_tier{1,2,3}.csv``. Those files are git-ignored — each
user decides whether the extra coverage is worth ~12 MB of disk for
their setup. :func:`load_catalog` picks them up transparently when
present.

This module loads everything once into an in-memory list and offers a
single spatial query: ``objects_in_fov``.

Design notes:
  * No pandas dependency. The catalog is ~15k rows (base) or up to ~30k
    with tier 3; a plain ``list[dict]`` plus a vectorised numpy distance
    check is more than fast enough (~5 ms per query). Keeping the
    renderer extra free of pandas matches the rest of the codebase.
  * Loading is lazy and cached at module scope. The first call pays
    ~30 ms; subsequent calls are free.
  * Missing base-file error is explicit and actionable; missing tier
    files are silent (they're opt-in).
"""

from __future__ import annotations

import csv
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np

from src.renderer.catalog_alias import display_label, find_aliases

logger = logging.getLogger(__name__)

# Co-location tolerance for alias clustering. Catalog positions for the
# same physical object commonly agree to <2", so 5" leaves slack for
# epoch/precision differences without merging genuinely separate objects.
_ALIAS_TOL_ARCSEC = 5.0

# Repo-root relative paths. The base CSV is committed; we never auto-
# download. Tier files are opt-in (``make tier-N``) and not committed.
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CATALOG_PATH = _DATA_DIR / "catalog.csv"
_TIER_GLOB = "catalog_tier*.csv"

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


def _read_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Read one catalog CSV; skip rows that fail to coerce."""
    rows: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for raw in csv.DictReader(fh):
            entry = _coerce_row(raw)
            if entry is not None:
                rows.append(entry)
    return rows


def load_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    """Return the bundled catalog (+ any tier files present) as a list.

    Args:
        path: Override the catalog CSV path. Useful for tests; production
            code passes ``None`` and gets the bundled file plus any
            opt-in tier files (``data/catalog_tier{1,2,3}.csv``).

    Returns:
        List of dicts with keys ``id, name, ra, dec, mag, type, catalog``
        (ra/dec/mag are floats; the rest strings). The list is shared —
        callers must not mutate it.

    Raises:
        FileNotFoundError: If the base catalog CSV is missing (the build
            script never ran or the file was not committed). Tier files
            are optional and their absence is silent.
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

    rows: list[dict[str, Any]] = _read_csv(csv_path)
    logger.info("Loaded %d catalog rows from %s", len(rows), csv_path)

    # Tier add-ons live next to the base CSV — fetched via `make tier-N`.
    # When an explicit path override is in play we treat that file as
    # self-contained (test fixtures, smoke probes) and don't crawl.
    if path is None:
        for tier_path in sorted(csv_path.parent.glob(_TIER_GLOB)):
            tier_rows = _read_csv(tier_path)
            logger.info(
                "Loaded %d tier-catalog rows from %s",
                len(tier_rows), tier_path,
            )
            rows.extend(tier_rows)
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
        List of catalog dicts, one per position-cluster (entries within
        ~5" of each other collapse to a single result). The survivor of
        each cluster is the highest-priority entry per
        :data:`CATALOG_PRIORITY` and carries:
          * ``separation_deg``: distance of the survivor itself from the
            FOV center.
          * ``aliases``: ``list[dict]`` of all cluster members
            (priority-sorted, survivor at index 0). Aliases carry the
            raw catalog fields only — no ``separation_deg``.
        Top-level results stay sorted by survivor separation.
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

    # Fast lookup: id -> separation_deg, for picking the survivor's own
    # separation (which may differ from the row that triggered the cluster
    # walk when a lower-priority entry was closer to the FOV center).
    sep_by_id: dict[str, float] = {
        row["id"]: float(s)
        for row, s in zip(matched_sorted, sep_sorted, strict=True)
    }

    result: list[dict[str, Any]] = []
    processed_ids: set[str] = set()
    for row in matched_sorted:
        if row["id"] in processed_ids:
            continue
        cluster = find_aliases(
            row, matched_sorted, tol_arcsec=_ALIAS_TOL_ARCSEC,
        )
        # Mark every cluster member as processed so a later iteration
        # over a co-located row doesn't emit a duplicate top-level entry.
        for member in cluster:
            processed_ids.add(member["id"])
        survivor = cluster[0]
        enriched = dict(survivor)  # don't pollute the shared cache
        enriched["separation_deg"] = sep_by_id[survivor["id"]]
        # Aliases are plain catalog rows (no separation_deg leak) +
        # a ``display`` field with the preferred human-facing name
        # (Messier/Caldwell use ``id``; others use ``name``).
        enriched["aliases"] = [
            {**dict(m), "display": display_label(m)} for m in cluster
        ]
        enriched["display"] = display_label(survivor)
        result.append(enriched)
    # The walk above visits ``matched_sorted`` in ascending separation,
    # but the survivor's own separation may be larger than the row that
    # triggered its cluster. Re-sort to honour the documented contract.
    result.sort(key=lambda r: r["separation_deg"])
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
