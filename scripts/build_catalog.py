"""Build the bundled catalog CSV at data/catalog.csv.

Downloads OpenNGC, the IAU named-star list, and merges in the well-known
Messier + Caldwell cross-references. Intended to be run by a maintainer
~1-2x/year; the result is committed to the repo so end-users never need
internet access at run time.

Sources:
  * OpenNGC NGC.csv (semicolon-separated, public-domain CSV):
      https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv
  * OpenNGC addendum (well-known objects outside the NGC/IC range):
      https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/addendum.csv
  * IAU Catalog of Star Names (whitespace-separated text):
      https://www.iau.org/static/public/themes/naming_stars/IAU-CSN.txt
  * Caldwell -> NGC/IC mapping is hard-coded from the well-known list.

Output columns: id, name, ra, dec, mag, type, catalog
  catalog in {"M", "C", "NGC", "IC", "IAU"}

Run via ``make build-catalog`` (preferred) or directly
``.venv/bin/python scripts/build_catalog.py``.
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

import httpx

logger = logging.getLogger("build_catalog")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_CSV = REPO_ROOT / "data" / "catalog.csv"

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


def write_csv(rows: list[dict], path: Path) -> None:
    """Write the merged catalog with deterministic ordering."""
    # Deterministic order: catalog precedence then numeric id when possible.
    order = {"M": 0, "C": 1, "NGC": 2, "IC": 3, "IAU": 4}

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


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())
