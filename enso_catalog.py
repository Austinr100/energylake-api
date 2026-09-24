"""
The ENSO catalog, read-only, from the bank — `GET /api/enso/catalog` (d091476).

The pantry's `scripts/enso_catalog_bank.py` (#850, migration 240) derives the
catalog from cpc_oni_monthly / cpc_roni_monthly and replaces every row of three
tables in one transaction:

    enso_catalog_runs   one row per classifier: developing tail, source, counts,
                        catalog_version, computed_at
    enso_episodes       one row per (classifier, episode)
    enso_year_bins      one row per (classifier, ENSO year Jul(E)..Jun(E+1))

This module holds the three reads and `build_payload`, the pure composition the
route serves. The route itself (query, memo, headers) lives in `main.py`.

NUMERICS. `peak_oni`, `peak_window_oni` and `n3_minus_n4` are `numeric` in the
bank, which psycopg hands back as `Decimal`. The `::float8` casts in the SQL are
what turn them into floats; `build_payload` does NOT paper over a missing cast —
it refuses a `Decimal` by name, so a dropped cast fails loudly here instead of
being silently coerced somewhere downstream.

SEASONS. The three-letter season strings are passed through exactly as the bank
wrote them. They do not sort alphabetically into time order (`AMJ` < `DJF` <
`JJA` ...), so `ORDER BY start_year, start_season` alone is not chronological
within a year; `build_payload` re-sorts episodes by (start_year, season index)
using the pantry's own season order (enso/catalog.py `SEASONS`, DJF = 0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

CLASSIFIERS = ("cpc_oni", "roni")
DEFAULT_CLASSIFIER = "cpc_oni"

# The pantry's season order (enso/catalog.py): index 0 = DJF, centred on January.
SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
           "JJA", "JAS", "ASO", "SON", "OND", "NDJ")
_SEASON_INDEX = {s: i for i, s in enumerate(SEASONS)}

# 1. the current run
RUN_SQL = """
    SELECT classifier, developing, source, n_episodes, n_year_bins, catalog_version, computed_at
    FROM enso_catalog_runs WHERE classifier = %(c)s
    ORDER BY computed_at DESC LIMIT 1
"""

# 2. its episodes
EPISODES_SQL = """
    SELECT episode_id, kind, label, start_year, start_season, end_year, end_season, open,
           n_seasons, peak_oni::float8 AS peak_oni, peak_year, peak_season, strength, very_strong, enso_years
    FROM enso_episodes WHERE classifier = %(c)s AND catalog_version = %(v)s
    ORDER BY start_year, start_season
"""

# 3. its year bins
YEAR_BINS_SQL = """
    SELECT enso_year, label, kind, strength, peak_window_oni::float8 AS peak_window_oni, flavor,
           n3_minus_n4::float8 AS n3_minus_n4, concurrent_djf, concurrent_mam, concurrent_jja, concurrent_son,
           episode_id, notes
    FROM enso_year_bins WHERE classifier = %(c)s AND catalog_version = %(v)s
    ORDER BY enso_year
"""

EPISODE_KEYS = ("episode_id", "kind", "label", "start_year", "start_season",
                "end_year", "end_season", "open", "n_seasons", "peak_oni",
                "peak_year", "peak_season", "strength", "very_strong", "enso_years")
YEAR_BIN_KEYS = ("enso_year", "label", "kind", "strength", "peak_window_oni",
                 "flavor", "n3_minus_n4", "concurrent_djf", "concurrent_mam",
                 "concurrent_jja", "concurrent_son", "episode_id", "notes")

_EPISODE_FLOATS = ("peak_oni",)
_YEAR_BIN_FLOATS = ("peak_window_oni", "n3_minus_n4")


def _float(name: str, v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        raise TypeError(f"{name} arrived as Decimal — the ::float8 cast is missing from the SQL")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"{name} is {type(v).__name__}, expected a float")
    return float(v)


def _season(name: str, v: Any) -> str | None:
    if v is None:
        return None
    if v not in _SEASON_INDEX:
        raise ValueError(f"{name} {v!r} is not one of the twelve ONI seasons")
    return v


def _iso_z(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("computed_at must be timezone-aware (timestamptz)")
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _episode(row: Mapping[str, Any]) -> dict:
    out = {k: row[k] for k in EPISODE_KEYS}
    for k in _EPISODE_FLOATS:
        out[k] = _float(k, out[k])
    for k in ("start_season", "end_season", "peak_season"):
        out[k] = _season(k, out[k])
    out["enso_years"] = [int(y) for y in (out["enso_years"] or [])]
    return out


def _year_bin(row: Mapping[str, Any]) -> dict:
    out = {k: row[k] for k in YEAR_BIN_KEYS}
    for k in _YEAR_BIN_FLOATS:
        out[k] = _float(k, out[k])
    return out


def build_payload(run_row: Mapping[str, Any],
                  episode_rows: Iterable[Mapping[str, Any]],
                  bin_rows: Iterable[Mapping[str, Any]]) -> dict:
    """The route's body, keys in contract order. Pure: no DB, no clock.

    `counts` is what the body actually carries (len of each list); the route
    checks it against the run's own n_episodes / n_year_bins before serving."""
    episodes = [_episode(r) for r in episode_rows]
    episodes.sort(key=lambda e: (e["start_year"], _SEASON_INDEX[e["start_season"]]))
    year_bins = [_year_bin(r) for r in bin_rows]
    return {
        "classifier": run_row["classifier"],
        "catalog_version": run_row["catalog_version"],
        "computed_at": _iso_z(run_row["computed_at"]),
        "source": run_row["source"],
        "developing": run_row["developing"],
        "counts": {"episodes": len(episodes), "year_bins": len(year_bins)},
        "episodes": episodes,
        "year_bins": year_bins,
    }


def etag(catalog_version: str) -> str:
    return f'W/"{catalog_version}"'


def etag_matches(if_none_match: str | None, tag: str) -> bool:
    """Weak comparison (RFC 9110 §13.1.2) against a possibly comma-listed header."""
    if not if_none_match:
        return False
    want = tag.removeprefix("W/")
    for cand in if_none_match.split(","):
        cand = cand.strip()
        if cand == "*" or cand.removeprefix("W/") == want:
            return True
    return False
