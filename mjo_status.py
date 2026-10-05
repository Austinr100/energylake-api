"""
The MJO status, read-only, from the bank — `GET /api/mjo/status` (d091614).

D-09-25-141 clause 3: the API serves the MJO status and nothing statistical.
No mean, no composite, no forecast: the banked index as the pantry wrote it,
the RMM rotation of two of its columns, and the dates that say how old it is.

THE BANK. `timeseries_values`, dataset `mjo_index_daily`, written by the
pantry's scripts/build_mjo_index.py (d091534): one row per (UTC day, series),
series pc1 / pc2 / amplitude / phase (1..8) / active (0/1), `meta.index_source`
∈ {omi_orig, omi, romi}. 1979-01-01 → 2026-09-30 on 2026-10-05, no missing day.

THE READS (D-09-25-75, d091551). Three statements, each naming one
(dataset, series) per index scan of idx_tsv_series_ts, each bounded by a ts
range, under the route's SET LOCAL statement_timeout:

    NEWEST_SQL  per series, the newest row in [now - 366 d, tomorrow)
    WALK_SQL    per series, every row in [frontier - 39 d, frontier + 1 d)
    SEAM_SQL    on `amplitude`: the newest OMI day in the lookback, then the
                day after it (which must be ROMI to be named as ROMI's first)

No `max(ts)` and no `DISTINCT ON`: CLAUDE.md's trap and d091551.

THE CONVENTION (pantry ingesters/psl_romi.py, the same rotation for OMI and
ROMI): RMM1 = PC2, RMM2 = -PC1, phase = octant(atan2(RMM2, RMM1)). `phase` is
served as banked; `rmm1`/`rmm2` are that rotation of the banked pc1/pc2, and
`octant` is here only so the tests can pin the rotation against banked phases.

NO DAY IS INVENTED. The walk is the 40 calendar days ending on the frontier.
A day with no row at all is absent from `walk` and named in `missing`; a day
with some series absent is served with those fields null and named in
`incomplete`. Nothing is interpolated or carried forward.

This module is pure (no DB, no clock); the route and its memo are in main.py.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional

UTC = timezone.utc

DATASET = "mjo_index_daily"
SERIES = ("pc1", "pc2", "amplitude", "phase", "active")
SEAM_SERIES = "amplitude"

# Pantry scripts/build_mjo_index.py ACTIVE_THRESHOLD / ACTIVE_RULE (main @ c901636).
ACTIVE_THRESHOLD = 1.0
ACTIVE_RULE = "active when amplitude >= 1.0 (exactly 1.0 is active)"
PHASE_CONVENTION = "RMM1 = PC2; RMM2 = -PC1; phase = octant(atan2(RMM2, RMM1))"

WALK_DAYS = 40
NEWEST_LOOKBACK_DAYS = 366       # a bank silent for a year is a 503, not a status
SEAM_LOOKBACK_DAYS = 3660        # the OMI → ROMI seam is looked for this far back

SOURCES = ("omi_orig", "omi", "romi")
SOURCE_NAMES = {
    "omi_orig": "OMI (original, frozen file)",
    "omi": "OMI",
    "romi": "ROMI (real-time OMI)",
}
ATTRIBUTION = {
    "text": "MJO index: NOAA Physical Sciences Laboratory (PSL), OMI and ROMI",
    "url": "https://psl.noaa.gov/mjo/",
}

_SERIES_VALUES = ", ".join(f"('{s}')" for s in SERIES)

NEWEST_SQL = f"""
    SELECT v.series, n.ts
      FROM (VALUES {_SERIES_VALUES}) AS v(series)
      LEFT JOIN LATERAL (
            SELECT t.ts
              FROM timeseries_values t
             WHERE t.dataset = %(dataset)s AND t.series = v.series
               AND t.ts >= %(lo)s AND t.ts < %(hi)s
             ORDER BY t.ts DESC
             LIMIT 1) n ON true
"""

WALK_SQL = f"""
    SELECT v.series, w.ts, w.value, w.source
      FROM (VALUES {_SERIES_VALUES}) AS v(series)
      CROSS JOIN LATERAL (
            SELECT t.ts, t.value::float8 AS value, t.meta->>'index_source' AS source
              FROM timeseries_values t
             WHERE t.dataset = %(dataset)s AND t.series = v.series
               AND t.ts >= %(lo)s AND t.ts < %(hi)s
             ORDER BY t.ts) w
"""

SEAM_SQL = """
    SELECT o.ts AS omi_last, o.source AS omi_last_source,
           r.ts AS next_day, r.source AS next_source
      FROM (SELECT t.ts, t.meta->>'index_source' AS source
              FROM timeseries_values t
             WHERE t.dataset = %(dataset)s AND t.series = %(series)s
               AND t.ts >= %(lo)s AND t.ts < %(hi)s
               AND t.meta->>'index_source' = 'omi'
             ORDER BY t.ts DESC
             LIMIT 1) o
      LEFT JOIN LATERAL (
            SELECT t.ts, t.meta->>'index_source' AS source
              FROM timeseries_values t
             WHERE t.dataset = %(dataset)s AND t.series = %(series)s
               AND t.ts > o.ts AND t.ts < %(hi)s
             ORDER BY t.ts
             LIMIT 1) r ON true
"""


# ── pure pieces ─────────────────────────────────────────────────────────────

def as_date(ts: Any) -> date:
    """A banked `ts` (timestamptz at UTC midnight) → its UTC calendar date."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            raise ValueError("ts must be timezone-aware (timestamptz)")
        t = ts.astimezone(UTC)
        if (t.hour, t.minute, t.second, t.microsecond) != (0, 0, 0, 0):
            raise ValueError(f"ts {t.isoformat()} is not a UTC midnight")
        return t.date()
    if isinstance(ts, date):
        return ts
    raise TypeError(f"ts is {type(ts).__name__}")


def day_ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def rmm(pc1: Optional[float], pc2: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """(RMM1, RMM2) = (PC2, -PC1); None where its PC is absent."""
    return (pc2, None if pc1 is None else -pc1)


def octant(rmm1: float, rmm2: float) -> int:
    """Pantry ingesters/psl_romi.rmm_phase: the 8-phase octant of atan2(RMM2, RMM1),
    [-180,-135) → 1 … [135,180] → 8."""
    ang = math.degrees(math.atan2(rmm2, rmm1))
    return min(int((ang + 180.0) // 45.0), 7) + 1


def is_active(amplitude: float) -> bool:
    return amplitude >= ACTIVE_THRESHOLD


def windows(now: datetime) -> dict:
    """The reads' ts ranges that do not depend on the frontier."""
    today = now.astimezone(UTC).date()
    hi = day_ts(today + timedelta(days=1))
    return {
        "today": today,
        "newest_lo": day_ts(today - timedelta(days=NEWEST_LOOKBACK_DAYS)),
        "newest_hi": hi,
        "seam_lo": day_ts(today - timedelta(days=SEAM_LOOKBACK_DAYS)),
        "seam_hi": hi,
    }


def frontier_of(newest_rows: Iterable[Mapping[str, Any]]) -> Optional[date]:
    """The newest banked day: the newest of the per-series newest rows."""
    days = [as_date(r["ts"]) for r in newest_rows if r.get("ts") is not None]
    return max(days) if days else None


def walk_range(frontier: date) -> dict:
    return {"lo": day_ts(frontier - timedelta(days=WALK_DAYS - 1)),
            "hi": day_ts(frontier + timedelta(days=1))}


def _num(series: str, v: Any) -> float:
    if isinstance(v, bool) or v is None:
        raise TypeError(f"{series} value is {v!r}")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{series} value {v!r} is not finite")
    return f


def _day(d: date, vals: Mapping[str, float], sources: set) -> dict:
    pc1, pc2 = vals.get("pc1"), vals.get("pc2")
    r1, r2 = rmm(pc1, pc2)
    ph, act = vals.get("phase"), vals.get("active")
    if ph is not None and (ph != int(ph) or not 1 <= ph <= 8):
        raise ValueError(f"{d} phase {ph} is not an integer 1..8")
    if act is not None and act not in (0.0, 1.0):
        raise ValueError(f"{d} active {act} is not 0 or 1")
    if len(sources) > 1:
        raise ValueError(f"{d} carries more than one index_source: {sorted(sources)}")
    src = next(iter(sources)) if sources else None
    return {
        "date": d.isoformat(),
        "pc1": pc1, "pc2": pc2, "rmm1": r1, "rmm2": r2,
        "phase": None if ph is None else int(ph),
        "amplitude": vals.get("amplitude"),
        "active": None if act is None else act == 1.0,
        "source": src,
    }


def build_walk(frontier: date, rows: Iterable[Mapping[str, Any]]) -> tuple[list, list, list]:
    """(walk, missing, incomplete) over the WALK_DAYS calendar days ending on
    `frontier`, oldest first. A day with no row is absent and named in
    `missing`; a day lacking some series is served with nulls and named in
    `incomplete`."""
    first = frontier - timedelta(days=WALK_DAYS - 1)
    vals: dict[date, dict[str, float]] = {}
    srcs: dict[date, set] = {}
    for r in rows:
        d = as_date(r["ts"])
        if not first <= d <= frontier:
            raise ValueError(f"{r['series']} row {d} is outside the walk {first}..{frontier}")
        s = r["series"]
        if s not in SERIES:
            raise ValueError(f"unexpected series {s!r}")
        if s in vals.setdefault(d, {}):
            raise ValueError(f"{s} has more than one row on {d}")
        vals[d][s] = _num(s, r["value"])
        if r.get("source") is not None:
            srcs.setdefault(d, set()).add(r["source"])
    walk, missing, incomplete = [], [], []
    for i in range(WALK_DAYS):
        d = first + timedelta(days=i)
        if d not in vals:
            missing.append(d.isoformat())
            continue
        lacking = [s for s in SERIES if s not in vals[d]]
        if lacking:
            incomplete.append({"date": d.isoformat(), "lacking": lacking})
        walk.append(_day(d, vals[d], srcs.get(d, set())))
    return walk, missing, incomplete


def build_sources(seam_row: Optional[Mapping[str, Any]]) -> dict:
    """The OMI → ROMI seam, as banked: OMI's last day and ROMI's first."""
    omi_last = romi_first = None
    note = None
    if seam_row is None or seam_row.get("omi_last") is None:
        note = f"no OMI day banked in the last {SEAM_LOOKBACK_DAYS} days"
    else:
        omi_last = as_date(seam_row["omi_last"]).isoformat()
        nxt = seam_row.get("next_day")
        if nxt is None:
            note = "no day is banked after OMI's last; ROMI has not begun"
        elif seam_row.get("next_source") != "romi":
            note = (f"the day after OMI's last ({as_date(nxt).isoformat()}) is "
                    f"{seam_row.get('next_source')!r}, not ROMI")
        else:
            romi_first = as_date(nxt).isoformat()
    return {
        "omi_last": omi_last,
        "romi_first": romi_first,
        "seam": (f"OMI through {omi_last}; ROMI from {romi_first}"
                 if omi_last and romi_first else None),
        "note": note,
        "names": SOURCE_NAMES,
        "rule": "each day comes from exactly one source; nothing is blended at the seam",
    }


def build_status(*, now: datetime, frontier: date, walk_rows: Iterable[Mapping[str, Any]],
                 seam_row: Optional[Mapping[str, Any]]) -> dict:
    """The route's body. Pure. `frontier` is the newest banked day (the route
    503s before calling here when there is none)."""
    today = now.astimezone(UTC).date()
    walk, missing, incomplete = build_walk(frontier, walk_rows)
    newest = walk[-1] if walk and walk[-1]["date"] == frontier.isoformat() else None
    return {
        "label": "MJO status",
        "dataset": DATASET,
        "today": None if newest is None else {
            "date": newest["date"],
            "phase": newest["phase"],
            "amplitude": newest["amplitude"],
            "active": newest["active"],
            "index_source": newest["source"],
        },
        "frontier": {
            "date": frontier.isoformat(),
            "age_days": (today - frontier).days,
            "as_of": today.isoformat(),
        },
        "walk": walk,
        "walk_window": {
            "first": (frontier - timedelta(days=WALK_DAYS - 1)).isoformat(),
            "last": frontier.isoformat(),
            "days": WALK_DAYS,
            "served": len(walk),
            "missing": len(missing),
            "missing_dates": missing,
            "incomplete": incomplete,
        },
        "active_threshold": ACTIVE_THRESHOLD,
        "active_rule": ACTIVE_RULE,
        "phase_convention": PHASE_CONVENTION,
        "sources": build_sources(seam_row),
        "attribution": ATTRIBUTION,
    }
