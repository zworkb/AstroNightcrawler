"""Build the bundled catalog CSV at data/catalog.csv.

Two modes:

  * **Base (default, ``--tier 0``)** — downloads OpenNGC, IAU named-star
    list, and merges in Messier + Caldwell cross-references. The result
    lives at ``data/catalog.csv`` and IS committed so end-users get the
    catalog with no internet at run time.

  * **Tier 1/2/3 (``--tier 1`` etc.)** — fetches additional, more
    specialised catalogs from VizieR and writes ``data/catalog_tierN.csv``.
    Those files are **NOT** committed (they're git-ignored); each user who
    wants the extra coverage runs ``make tier-1`` (or ``tier-2``, ``tier-3``)
    once. :func:`src.renderer.catalog.load_catalog` automatically picks
    them up alongside the base CSV.

Base sources:
  * OpenNGC NGC.csv (semicolon-separated, public-domain CSV):
      https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv
  * OpenNGC addendum (well-known objects outside the NGC/IC range):
      https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv
  * IAU Catalog of Star Names (whitespace-separated text):
      https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt
  * Caldwell -> NGC/IC mapping is hard-coded from the well-known list.

Tier sources (VizieR ``asu-tsv`` interface):
  Tier 1 — detailed nebulae & galaxies for astrophotographers:
    * Sharpless 2 (Sh2): 313 H II regions — VII/20/catalog
    * Barnard dark nebulae: 349 entries — VII/220A
    * Arp Peculiar Galaxies: 338 entries — VII/192/arplist
  Tier 2 — reflection nebulae + open clusters:
    * (none implemented yet — vdB/Collinder/HCG IDs need probing)
  Tier 3 — large/faint extended objects:
    * UGC galaxies: 12,921 entries — VII/26D/catalog

Output columns: id, name, ra, dec, mag, type, catalog
  catalog in {"M", "C", "NGC", "IC", "IAU", "Sh2", "B", "Arp", "UGC"}

Run via ``make build-catalog`` (base) or ``make tier-1`` / ``tier-2`` /
``tier-3``. Direct invocation:
``.venv/bin/python scripts/build_catalog.py [--tier N]``.
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger("build_catalog")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUTPUT_CSV = DATA_DIR / "catalog.csv"

OPENNGC_URL = (
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/"
    "database_files/NGC.csv"
)
OPENNGC_ADDENDUM_URL = (
    "https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/"
    "database_files/addendum.csv"
)
IAU_STARS_URL = (
    # WGSN Secretary's mirror; the official iau.org URL went 404
    # mid-2025. This file is the authoritative source the IAU page
    # itself links to.
    "https://www.pas.rochester.edu/~emamajek/WGSN/IAU-CSN.txt"
)
VIZIER_TSV_URL = "https://vizier.cds.unistra.fr/viz-bin/asu-tsv"

# Caldwell -> identifier-in-OpenNGC mapping. Identifiers are either
# "NGC <num>", "IC <num>", or an explicit RA/Dec pair when the object is
# outside NGC/IC (only a handful: Hyades, Double Cluster halves, etc.).
# Source: Sir Patrick Moore's 1995 list, widely tabulated.
CALDWELL_TO_NGC: dict[int, str] = {
    1: "NGC188", 2: "NGC40", 3: "NGC4236", 4: "NGC7023", 5: "IC342",
    6: "NGC6543", 7: "NGC2403", 8: "NGC559", 9: "Sh2-155", 10: "NGC663",
    11: "NGC7635", 12: "NGC6946", 13: "NGC457", 14: "NGC869", 15: "NGC6826",
    16: "NGC7243", 17: "NGC147", 18: "NGC185", 19: "IC5146", 20: "NGC7000",
    21: "NGC4449", 22: "NGC7662", 23: "NGC891", 24: "NGC1275", 25: "NGC2419",
    26: "NGC4244", 27: "NGC6888", 28: "NGC752", 29: "NGC5005", 30: "NGC7331",
    31: "IC405", 32: "NGC4631", 33: "NGC6992", 34: "NGC6960", 35: "NGC4889",
    36: "NGC4559", 37: "NGC6885", 38: "NGC4565", 39: "NGC2392", 40: "NGC3626",
    41: "Hyades", 42: "NGC7006", 43: "NGC7814", 44: "NGC7479", 45: "NGC5248",
    46: "NGC2261", 47: "NGC6934", 48: "NGC2775", 49: "NGC2237", 50: "NGC2244",
    51: "IC1613", 52: "NGC4697", 53: "NGC3115", 54: "NGC2506", 55: "NGC7009",
    56: "NGC246", 57: "NGC6822", 58: "NGC2360", 59: "NGC3242", 60: "NGC4038",
    61: "NGC4039", 62: "NGC247", 63: "NGC7293", 64: "NGC2362", 65: "NGC253",
    66: "NGC5694", 67: "NGC1097", 68: "NGC6729", 69: "NGC6302", 70: "NGC300",
    71: "NGC2477", 72: "NGC55", 73: "NGC1851", 74: "NGC3132", 75: "NGC6124",
    76: "NGC6231", 77: "NGC5128", 78: "NGC6541", 79: "NGC3201", 80: "NGC5139",
    81: "NGC6352", 82: "NGC6193", 83: "NGC4945", 84: "NGC5286", 85: "IC2391",
    86: "NGC6397", 87: "NGC1261", 88: "NGC5823", 89: "NGC6087", 90: "NGC2867",
    91: "NGC3532", 92: "NGC3372", 93: "NGC6752", 94: "NGC4755", 95: "NGC6025",
    96: "NGC2516", 97: "NGC3766", 98: "NGC4609", 99: "Coalsack",
    100: "IC2944", 101: "NGC6744", 102: "IC2602", 103: "NGC2070",
    104: "NGC362", 105: "NGC4833", 106: "NGC104", 107: "NGC6101",
    108: "NGC4372", 109: "NGC3195",
}

# A few Caldwell entries are not in NGC/IC. Hard-code their RA/Dec
# (degrees, J2000), names, and rough magnitudes so the catalog still
# carries them. Sources: SIMBAD / Wikipedia.
CALDWELL_EXTRAS: dict[int, dict] = {
    9:   {"name": "Cave Nebula",          "ra": 343.1167, "dec": 62.6167, "mag": 7.7,  "type": "EmN"},
    41:  {"name": "Hyades",               "ra": 66.7250,  "dec": 15.8667, "mag": 0.5,  "type": "OpCl"},
    99:  {"name": "Coalsack",             "ra": 186.7500, "dec": -63.0000, "mag": 0.0,  "type": "DrkN"},
    100: {"name": "Lambda Centauri Cluster", "ra": 169.4083, "dec": -63.3500, "mag": 4.5, "type": "EmN"},
}


def _fetch_text(url: str) -> str:
    """Download a text document with a friendly timeout + retry message."""
    logger.info("fetching %s", url)
    try:
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as exc:  # pragma: no cover - network
        logger.error("download failed for %s: %s", url, exc)
        raise SystemExit(2) from exc
    return resp.text


def _parse_ra_hms(token: str) -> float | None:
    """Parse 'HH:MM:SS.ss' RA -> degrees."""
    parts = token.strip().split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None
    return (h + m / 60.0 + s / 3600.0) * 15.0


def _parse_dec_dms(token: str) -> float | None:
    """Parse '+/-DD:MM:SS.s' Dec -> degrees."""
    token = token.strip()
    if not token:
        return None
    sign = -1.0 if token.startswith("-") else 1.0
    body = token.lstrip("+-")
    parts = body.split(":")
    if len(parts) != 3:
        return None
    try:
        d, m, s = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        return None
    return sign * (d + m / 60.0 + s / 3600.0)


def _safe_float(s: str) -> float | None:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def parse_openngc(csv_text: str) -> list[dict]:
    """Parse OpenNGC's semicolon CSV into a list of normalized dicts."""
    rows: list[dict] = []
    reader = csv.DictReader(csv_text.splitlines(), delimiter=";")
    for row in reader:
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        ra_deg = _parse_ra_hms(row.get("RA", ""))
        dec_deg = _parse_dec_dms(row.get("Dec", ""))
        if ra_deg is None or dec_deg is None:
            # Stars-only or duplicate entries lack coordinates — skip.
            continue
        # Prefer V (visual) magnitude, fall back to B.
        mag = _safe_float(row.get("V-Mag", "")) or _safe_float(row.get("B-Mag", "")) or 0.0
        obj_type = (row.get("Type") or "").strip() or "?"

        # Determine catalog/id. OpenNGC names look like "NGC0224", "IC0405".
        if name.startswith("NGC"):
            cat = "NGC"
            num = name[3:].lstrip("0") or "0"
            ident = f"NGC {num}"
        elif name.startswith("IC"):
            cat = "IC"
            num = name[2:].lstrip("0") or "0"
            ident = f"IC {num}"
        else:
            cat = "NGC"  # addendum.csv contains Sh2-, Mel-, etc. Bucket as NGC.
            ident = name

        # Friendly name: OpenNGC's "Common names" column.
        common = (row.get("Common names") or "").split(",")[0].strip()
        display = common or ident

        # Messier cross-ref directly from OpenNGC's "M" column.
        m_col = (row.get("M") or "").strip()
        rows.append({
            "_openngc_name": name,
            "id": ident,
            "name": display,
            "ra": ra_deg,
            "dec": dec_deg,
            "mag": mag,
            "type": obj_type,
            "catalog": cat,
            "_m": m_col,
        })
    return rows


def derive_messier(ngc_rows: list[dict]) -> list[dict]:
    """Pull Messier entries out of the OpenNGC table via its M column."""
    out: list[dict] = []
    seen: set[int] = set()
    for r in ngc_rows:
        m = r["_m"]
        if not m:
            continue
        try:
            num = int(m)
        except ValueError:
            continue
        if num in seen:
            continue  # M40 etc. have multiple components in OpenNGC; first wins
        seen.add(num)
        out.append({
            "id": f"M{num}",
            "name": r["name"],
            "ra": r["ra"],
            "dec": r["dec"],
            "mag": r["mag"],
            "type": r["type"],
            "catalog": "M",
        })
    return out


def derive_caldwell(ngc_index: dict[str, dict]) -> list[dict]:
    """Build Caldwell entries by cross-referencing the NGC/IC table.

    The OpenNGC ``Name`` column is zero-padded ("NGC0040", "IC0405");
    the Caldwell map uses the colloquial unpadded form ("NGC40"). We
    pad here before lookup so the join hits.
    """
    def _padded(key: str) -> str:
        # "NGC40" -> "NGC0040"; "IC405" -> "IC0405". Leave non-NGC/IC
        # entries (Sh2-155, Hyades, Coalsack) untouched — those go
        # through CALDWELL_EXTRAS or match the OpenNGC name verbatim.
        for prefix in ("NGC", "IC"):
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                return f"{prefix}{int(key[len(prefix):]):04d}"
        return key

    out: list[dict] = []
    for num, ngc_id in CALDWELL_TO_NGC.items():
        lookup = _padded(ngc_id)
        # Some Caldwell entries are not NGC/IC: take the hard-coded fallback.
        ref = ngc_index.get(lookup)
        if ref is None:
            extra = CALDWELL_EXTRAS.get(num)
            if extra is None:
                logger.warning("Caldwell %d (%s) not found in NGC/IC and no extra", num, ngc_id)
                continue
            out.append({
                "id": f"C{num}",
                "name": extra["name"],
                "ra": extra["ra"],
                "dec": extra["dec"],
                "mag": extra["mag"],
                "type": extra["type"],
                "catalog": "C",
            })
            continue
        out.append({
            "id": f"C{num}",
            "name": ref["name"],
            "ra": ref["ra"],
            "dec": ref["dec"],
            "mag": ref["mag"],
            "type": ref["type"],
            "catalog": "C",
        })
    return out


def parse_iau_stars(text: str) -> list[dict]:
    """Parse the IAU-CSN.txt fixed-width listing.

    Layout (whitespace-separated after stripping comments):
        Name/ASCII Name/Diacritics Designation ID ID Con # WDS_J mag bnd HIP HD RA(J2000) Dec(J2000) Date [Notes]

    The Name/ASCII column is always a single ASCII token (e.g.
    "Absolutno", "Achernar", "Alfa") — multi-word names are unique to
    Name/Diacritics, which we ignore. So splitting on whitespace and
    indexing column 0 for the name is safe.

    RA/Dec are in decimal degrees in columns -4 and -3 (when a Notes
    field is present) or -3 and -2 (when it's absent). We anchor on
    "two consecutive parseable floats near the end" and walk back to
    find Vmag (column 8 by spec, but we lookup defensively).
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        if not raw or raw.startswith("#") or raw.startswith("$"):
            continue
        parts = raw.split()
        if len(parts) < 12:
            continue
        # Find the (RA, Dec) pair by scanning the last few columns for
        # two consecutive valid floats. The Notes field at column -1
        # may be a "*" or absent — we scan window (-4..-1).
        ra = dec = None
        for end in range(len(parts), max(len(parts) - 5, 1), -1):
            try:
                cand_dec = float(parts[end - 1])
                cand_ra = float(parts[end - 2])
            except (ValueError, IndexError):
                continue
            # Sanity-bound: RA in [0, 360], Dec in [-90, 90].
            if 0.0 <= cand_ra < 360.0 and -90.0 <= cand_dec <= 90.0:
                ra, dec = cand_ra, cand_dec
                break
        if ra is None or dec is None:
            continue
        # Vmag is column 8 in the spec; if that doesn't parse, try the
        # surrounding columns.
        vmag = None
        for idx in (8, 7, 9):
            if 0 <= idx < len(parts):
                try:
                    vmag = float(parts[idx])
                    break
                except ValueError:
                    continue
        if vmag is None:
            vmag = 0.0
        name = parts[0]
        if name in seen:
            continue
        seen.add(name)
        rows.append({
            "id": name,
            "name": name,
            "ra": ra,
            "dec": dec,
            "mag": vmag,
            "type": "Star",
            "catalog": "IAU",
        })
    return rows


# ----------------------------------------------------------------------
# Tier sources (VizieR)
# ----------------------------------------------------------------------


def _fetch_vizier_tsv(source: str) -> list[dict[str, str]]:
    """Fetch a VizieR catalog via ``asu-tsv``, return parsed rows.

    The VizieR TSV stream has a ~30-line ``#``-prefixed preamble, then a
    3-line header (column names / units / separator dashes), then the
    rows. ``_RAJ2000`` and ``_DEJ2000`` are auto-computed columns that
    VizieR adds when we pass ``-out.add=_RAJ2000,_DEJ2000`` — they're
    decimal degrees regardless of the catalog's native frame.
    """
    logger.info("fetching VizieR catalog %s", source)
    try:
        resp = httpx.get(
            VIZIER_TSV_URL,
            params={
                "-source": source,
                "-out.add": "_RAJ2000,_DEJ2000",
                "-out.max": "unlimited",
            },
            timeout=120.0,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:  # pragma: no cover - network
        logger.error("download failed for %s: %s", source, exc)
        raise SystemExit(2) from exc
    # Detect VizieR-side errors that come back with HTTP 200.
    err = re.search(r"#INFO\s+Error=([^\n]+)", resp.text)
    if err:
        logger.error("VizieR returned error for %s: %s", source, err.group(1).strip())
        raise SystemExit(2)

    # Strip comment lines and the unit/separator header rows, then parse
    # the actual TSV with the column-name row as fieldnames.
    body_lines = [
        line for line in resp.text.splitlines()
        if line and not line.startswith("#")
    ]
    if len(body_lines) < 4:
        return []
    fieldnames = body_lines[0].split("\t")
    # body_lines[1] = units, body_lines[2] = ----- dashes
    data_lines = body_lines[3:]
    rows: list[dict[str, str]] = []
    for line in data_lines:
        # VizieR pads trailing fields with empty strings; tabs are
        # significant so use a plain split.
        values = line.split("\t")
        if len(values) < len(fieldnames):
            values += [""] * (len(fieldnames) - len(values))
        rows.append(dict(zip(fieldnames, values, strict=False)))
    return rows


def _vizier_coords(row: dict[str, str]) -> tuple[float, float] | None:
    """Extract ``_RAJ2000``/``_DEJ2000`` decimal degrees from a VizieR row."""
    try:
        return float(row["_RAJ2000"].strip()), float(row["_DEJ2000"].strip())
    except (KeyError, ValueError):
        return None


def fetch_sharpless2() -> list[dict]:
    """Sharpless 2 (1959): 313 galactic H II regions."""
    rows = _fetch_vizier_tsv("VII/20/catalog")
    out: list[dict] = []
    for r in rows:
        coords = _vizier_coords(r)
        if coords is None:
            continue
        ra, dec = coords
        try:
            num = int(r["Sh2"].strip())
        except (KeyError, ValueError):
            continue
        out.append({
            "id": f"Sh2-{num}",
            "name": f"Sh2-{num}",
            "ra": ra,
            "dec": dec,
            "mag": 0.0,  # Sharpless catalog has no magnitudes
            "type": "EmN",
            "catalog": "Sh2",
        })
    return out


def fetch_barnard() -> list[dict]:
    """Barnard (1927): ~349 dark nebulae catalogued from photographic plates."""
    rows = _fetch_vizier_tsv("VII/220A")
    out: list[dict] = []
    for r in rows:
        coords = _vizier_coords(r)
        if coords is None:
            continue
        ra, dec = coords
        bn = r.get("Barn", "").strip()
        if not bn:
            continue
        # Barn comes as a number or number+letter (e.g. "33", "142a"). Keep verbatim.
        out.append({
            "id": f"B{bn}",
            "name": f"Barnard {bn}",
            "ra": ra,
            "dec": dec,
            "mag": 0.0,  # dark nebulae have no traditional magnitude
            "type": "DrkN",
            "catalog": "B",
        })
    return out


def fetch_arp() -> list[dict]:
    """Arp (1966) Peculiar Galaxies: 338 entries; Webb (1996) data."""
    rows = _fetch_vizier_tsv("VII/192/arplist")
    out: list[dict] = []
    for r in rows:
        coords = _vizier_coords(r)
        if coords is None:
            continue
        ra, dec = coords
        try:
            num = int(r["Arp"].strip())
        except (KeyError, ValueError):
            continue
        vt = _safe_float(r.get("VT", "")) or 0.0
        cross = (r.get("Name") or "").strip()
        display = f"Arp {num}" + (f" ({cross})" if cross else "")
        out.append({
            "id": f"Arp {num}",
            "name": display,
            "ra": ra,
            "dec": dec,
            "mag": vt,
            "type": "Gxy",
            "catalog": "Arp",
        })
    return out


def fetch_ugc() -> list[dict]:
    """Uppsala General Catalogue of Galaxies (Nilson 1973): 12,921 galaxies.

    Large catalog — adds significant size to the bundle. Mostly useful
    for galaxy hunters; many entries are too faint for amateur scopes
    but the brightest are popular wide-field targets.
    """
    rows = _fetch_vizier_tsv("VII/26D/catalog")
    out: list[dict] = []
    for r in rows:
        coords = _vizier_coords(r)
        if coords is None:
            continue
        ra, dec = coords
        ugc = r.get("UGC", "").strip()
        if not ugc:
            continue
        mag = _safe_float(r.get("Pmag", "")) or 0.0
        out.append({
            "id": f"UGC {ugc}",
            "name": f"UGC {ugc}",
            "ra": ra,
            "dec": dec,
            "mag": mag,
            "type": "Gxy",
            "catalog": "UGC",
        })
    return out


# Tier routing — each tier maps to a list of (label, fetcher) pairs. Add
# new catalogs by writing a fetch_X() above and registering it here.
TIER_SOURCES: dict[int, list[tuple[str, Callable[[], list[dict]]]]] = {
    1: [
        ("Sharpless 2", fetch_sharpless2),
        ("Barnard", fetch_barnard),
        ("Arp", fetch_arp),
    ],
    2: [
        # TODO: vdB (van den Bergh reflection nebulae), Collinder open
        # clusters, HCG (Hickson Compact Groups). VizieR IDs still need
        # probing — the asu-tsv "table 'X' does not exist" error happens
        # frequently because catalog publishers don't follow a uniform
        # table-naming scheme.
    ],
    3: [
        ("UGC", fetch_ugc),
        # TODO: LBN (Lynds Bright Nebulae), LDN (Lynds Dark Nebulae).
    ],
}


# ----------------------------------------------------------------------
# Writers
# ----------------------------------------------------------------------


def write_csv(rows: list[dict], path: Path) -> None:
    """Write a catalog list with deterministic ordering."""
    # Deterministic order: catalog precedence then numeric id when possible.
    order = {
        "M": 0, "C": 1, "NGC": 2, "IC": 3, "IAU": 4,
        "Sh2": 10, "B": 11, "Arp": 12,
        "UGC": 20,
    }

    def sort_key(r: dict) -> tuple:
        cat_rank = order.get(r["catalog"], 99)
        ident = r["id"]
        # Try to peel a number off (M27 -> 27, NGC 6960 -> 6960).
        digits = "".join(c for c in ident if c.isdigit())
        num = int(digits) if digits else 0
        return (cat_rank, num, ident)

    rows = sorted(rows, key=sort_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["id", "name", "ra", "dec", "mag", "type", "catalog"],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "id": r["id"],
                "name": r["name"],
                "ra": f"{r['ra']:.6f}",
                "dec": f"{r['dec']:.6f}",
                "mag": f"{r['mag']:.2f}",
                "type": r["type"],
                "catalog": r["catalog"],
            })


def build_base() -> int:
    """Build the committed base catalog (M + C + NGC + IC + IAU)."""
    ngc_text = _fetch_text(OPENNGC_URL)
    addendum_text = _fetch_text(OPENNGC_ADDENDUM_URL)
    iau_text = _fetch_text(IAU_STARS_URL)

    ngc_rows = parse_openngc(ngc_text)
    ngc_rows += parse_openngc(addendum_text)
    messier = derive_messier(ngc_rows)
    ngc_index = {r["_openngc_name"]: r for r in ngc_rows}
    caldwell = derive_caldwell(ngc_index)
    iau = parse_iau_stars(iau_text)

    # Strip private fields from the NGC/IC rows before writing.
    ngc_clean = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in ngc_rows
    ]

    merged = messier + caldwell + ngc_clean + iau

    write_csv(merged, OUTPUT_CSV)
    logger.info("wrote %d rows to %s", len(merged), OUTPUT_CSV)
    logger.info(
        "breakdown: M=%d C=%d NGC=%d IC=%d IAU=%d",
        sum(1 for r in merged if r["catalog"] == "M"),
        sum(1 for r in merged if r["catalog"] == "C"),
        sum(1 for r in merged if r["catalog"] == "NGC"),
        sum(1 for r in merged if r["catalog"] == "IC"),
        sum(1 for r in merged if r["catalog"] == "IAU"),
    )
    return 0


def build_tier(tier: int) -> int:
    """Build a tier-N add-on catalog (NOT committed; user fetches on demand)."""
    sources = TIER_SOURCES.get(tier, [])
    if not sources:
        logger.error(
            "tier %d has no sources registered — see TIER_SOURCES in this script",
            tier,
        )
        return 1
    out_path = DATA_DIR / f"catalog_tier{tier}.csv"
    merged: list[dict] = []
    for label, fetcher in sources:
        try:
            rows = fetcher()
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001 — log + continue, don't abort the tier
            logger.error("tier %d: %s fetch failed: %s", tier, label, exc)
            continue
        logger.info("tier %d: %s -> %d rows", tier, label, len(rows))
        merged.extend(rows)
    if not merged:
        logger.error("tier %d produced no rows; not writing file", tier)
        return 1
    write_csv(merged, out_path)
    by_cat: dict[str, int] = {}
    for r in merged:
        by_cat[r["catalog"]] = by_cat.get(r["catalog"], 0) + 1
    logger.info("wrote %d rows to %s", len(merged), out_path)
    logger.info(
        "breakdown: %s",
        " ".join(f"{k}={v}" for k, v in sorted(by_cat.items())),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--tier",
        type=int,
        default=0,
        choices=(0, 1, 2, 3),
        help=(
            "Catalog tier: 0 = committed base (M+C+NGC+IC+IAU, default), "
            "1/2/3 = optional add-ons written to data/catalog_tierN.csv. "
            "See `make tier-1`/`tier-2`/`tier-3`."
        ),
    )
    args = parser.parse_args(argv)
    if args.tier == 0:
        return build_base()
    return build_tier(args.tier)


if __name__ == "__main__":
    sys.exit(main())
