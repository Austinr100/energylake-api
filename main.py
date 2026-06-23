"""
EnergyLake API — read-only window onto the Neon pantry.

Milestone 1 of the May 20 roadmap: the FastAPI backend on Railway.
Nothing on the Vercel frontend renders without this pipe.

Architecture:
    pantry (Neon Postgres)  ->  THIS API (Railway)  ->  Vercel frontend

This service is READ-ONLY. It never writes to the pantry. Ingestion
stays in the energylake-pantry repo (scrapers + ingesters). This repo
only serves data that already exists.

Driver note: uses psycopg3 (psycopg[binary]) rather than asyncpg, because
asyncpg has no prebuilt wheel for Python 3.14 and fails to compile from
source. psycopg ships binary wheels for 3.14 — no compiler needed locally
or on Railway. psycopg's async API (AsyncConnectionPool) gives the same
non-blocking behavior.

Endpoints:
    GET /health                            health check
    GET /api/timeseries/caiso-fuel-mix     fuel mix at 5-min grain (M2)
    GET /api/timeseries/caiso-forecast-vs-actual  load/solar/wind actual-vs-forecast, wide
    GET /api/almanac/lmp-shape             LMP shape overlay (M2 — added May 29)
    GET /api/newswire/recent               Joule Newswire items
    GET /api/tape/recent                   Joule Tape items (DEPRECATED — see /api/wire/recent)
    GET /api/briefs/daily/latest           most recent daily Joule brief (Tape 3a)
    GET /api/briefs/daily/{date}           daily Joule brief by date (Tape 3a)
    GET /api/wire/recent                   power-signal filings, successor to /api/tape/recent (Tape 3a)
    GET /api/regulatory/board              regulatory_board view as JSON, body-filterable (D-2026-06-14-03)
    GET /api/joule/chart-brief             latest Joule chart brief by brief_type (#99 render leg)
"""

import os
import re
from contextlib import asynccontextmanager
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import timedelta as _timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

# ---------------------------------------------------------------------------
# Configuration (all via environment variables — set these in Railway)
# ---------------------------------------------------------------------------
# NEON_DATABASE_URL : the same connection string the pantry uses.
#   psycopg accepts both "postgres://" and "postgresql://" — no rewrite needed.
# ALLOWED_ORIGINS   : comma-separated list of frontend origins for CORS,
#   e.g. "https://energylake.io,https://www.energylake.io,http://localhost:3000"
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("NEON_DATABASE_URL", "")

ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000",
    ).split(",")
    if o.strip()
]

# A single shared async connection pool, opened on startup, closed on shutdown.
# Railway hobby + Neon both have modest connection caps, so keep it small.
_pool: AsyncConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    if not DATABASE_URL:
        # Fail loud, not silent (Principle #27): if the env var is missing,
        # we want a clear error at startup, not empty query results later.
        raise RuntimeError("NEON_DATABASE_URL is not set")
    _pool = AsyncConnectionPool(
        conninfo=DATABASE_URL,
        min_size=1,
        max_size=5,
        open=False,  # open explicitly below (avoids deprecation warning)
        kwargs={"row_factory": dict_row},
    )
    await _pool.open()
    yield
    await _pool.close()


app = FastAPI(
    title="EnergyLake API",
    version="0.2.0",
    description="Read-only pantry access for the EnergyLake frontend.",
    lifespan=lifespan,
)

# Vercel preview deployments get a fresh, unpredictable origin per build, so
# they can't be enumerated in ALLOWED_ORIGINS. This regex (Starlette applies it
# with fullmatch) is anchored on our real Vercel project slug "energylake" AND
# our team suffix, so it covers both preview shapes —
#   energylake-<hash>-austinrodriguez221-6328s-projects.vercel.app
#   energylake-git-<branch>-austinrodriguez221-6328s-projects.vercel.app
# — while other teams' "energylake*" projects won't match. Purely additive:
# allow_origins (prod: energylake.io / www, via env) is unchanged.
VERCEL_PREVIEW_ORIGIN_REGEX = (
    r"https://energylake-[a-z0-9-]+-austinrodriguez221-6328s-projects\.vercel\.app"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=VERCEL_PREVIEW_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
# Shared constants + validators
# ═══════════════════════════════════════════════════════════════════════════

# CAISO is a Pacific market. All day boundaries are Pacific calendar days,
# not UTC days — confirmed against real data: midnight PT == 07:00 UTC (PDT),
# a full PT day returns exactly 24 hourly rows. Postgres does the conversion
# with AT TIME ZONE, so day math stays correct across the PST/PDT switch.
MARKET_TZ = "America/Los_Angeles"

# YYYY-MM-DD, used to validate `date`/`start`/`end` before they touch SQL.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_date(s: str | None) -> bool:
    return s is not None and bool(_DATE_RE.match(s))


def _parse_int_list(
    s: str,
    *,
    field_name: str,
    valid_range: tuple[int, int] | None = None,
) -> list[int]:
    """Parse 'a,b,c' -> [a,b,c]; raise HTTPException on bad input."""
    try:
        out = [int(x.strip()) for x in s.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"`{field_name}` must be a comma-separated list of integers; got '{s}'.",
        )
    if not out:
        raise HTTPException(status_code=400, detail=f"`{field_name}` cannot be empty.")
    if valid_range is not None:
        lo, hi = valid_range
        bad = [x for x in out if x < lo or x > hi]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"`{field_name}` values must be between {lo} and {hi}; got {bad}.",
            )
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Health check — the first thing to verify after deploy.
# Hit GET /health on the Railway URL; should return {"status":"ok","db":"ok"}.
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    assert _pool is not None
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                await cur.fetchone()
        return {"status": "ok", "db": "ok"}
    except Exception as e:  # surface DB problems explicitly
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# CAISO fuel mix — the first real data endpoint (Milestone 2).
#
# Schema CONFIRMED via information_schema.columns (2026-05-22):
#   timeseries_values(ts timestamptz, dataset text, series text,
#                     value numeric, meta jsonb, ingested_ts timestamptz)
#
# Dataset: 'caiso_fuel_mix_5min' — CAISO fuel mix at 5-minute grain, on a
#   rolling ~30-day hot tier (purpose-built store, NOT the hourly table; it
#   shares the same timeseries_values schema and series set). Up to 288 rows
#   per Pacific calendar day vs. 24 for the hourly source it replaced.
# Series (14): batteries, biogas, biomass, coal, geothermal, imports,
#   large_hydro, natural_gas, nuclear, other, renewables, small_hydro,
#   solar, wind. Values are MW.
#
# IMPORTANT DATA NOTES (carried from the schema peek):
#   * 'renewables' is a ROLL-UP that overlaps solar/wind/geothermal/biomass/
#     biogas/small_hydro. Do NOT stack it together with the granular fuels or
#     the chart double-counts. The frontend should chart the granular fuels
#     OR 'renewables', not both. This endpoint returns everything; the
#     CHOICE of what to stack lives in the chart.
#   * Negative values are REAL: batteries charging and imports exporting show
#     as negatives. They are signal, not error — do not clamp. The stacked
#     area should allow a below-zero region (matches CAISO Today's Outlook).
#   * A given hour may omit a series (e.g. 'renewables' absent in some hours).
#     The pivot below tolerates that — missing fuels are simply absent from
#     that hour's object rather than breaking the response.
# ═══════════════════════════════════════════════════════════════════════════

# Repointed hourly -> 5-min (2026-06-23). Same timeseries_values schema and the
# same 14 series (renewables still a roll-up; granular fuels charted, not it).
# Only the grain/source changes: a Pacific calendar day is now up to 288 points
# instead of 24. PT day-boundary SQL below is unchanged — it stays correct.
FUEL_MIX_DATASET = "caiso_fuel_mix_5min"


@app.get("/api/timeseries/caiso-fuel-mix")
async def caiso_fuel_mix(
    limit: int = Query(
        default=24,
        ge=1,
        le=2000,
        description="Number of most-recent 5-minute intervals to return. "
        "Ignored when `date` or `start`/`end` is supplied.",
    ),
    date: str | None = Query(
        default=None,
        description="A single Pacific calendar day, YYYY-MM-DD. Returns that "
        "day's 5-minute points (up to 288). Mutually exclusive with start/end.",
    ),
    start: str | None = Query(
        default=None,
        description="Range start, Pacific calendar day, YYYY-MM-DD (inclusive). "
        "Use with `end`.",
    ),
    end: str | None = Query(
        default=None,
        description="Range end, Pacific calendar day, YYYY-MM-DD (inclusive). "
        "Use with `start`.",
    ),
    shape: str = Query(
        default="pivot",
        pattern="^(pivot|long)$",
        description="'pivot' = one object per hour with a key per fuel "
        "(ready for stacked-area charts). 'long' = one object per "
        "fuel per hour (raw rows).",
    ),
):
    """
    CAISO fuel mix in MW, newest first.

    Three selection modes (in priority order):
      * date=YYYY-MM-DD            -> that single Pacific calendar day (up to 288 pts)
      * start=YYYY-MM-DD&end=...   -> inclusive Pacific-day range
      * (neither)                  -> latest `limit` hours (default 24)

    Day boundaries are Pacific (America/Los_Angeles), matching the chart's
    hour labels and CAISO Today's Outlook.

    shape=pivot (default):
        [{"ts": "...", "solar": 18550.0, "wind": 4696.0, "batteries": -1845.0, ...}, ...]
    shape=long:
        [{"ts": "...", "series": "solar", "value": 18550.0}, ...]
    """
    assert _pool is not None

    # ── Decide selection mode + validate (fail loud on bad input) ──────────
    use_date = date is not None
    use_range = start is not None or end is not None

    if use_date and use_range:
        raise HTTPException(
            status_code=400,
            detail="Use either `date` or `start`/`end`, not both.",
        )
    if use_range and not (start is not None and end is not None):
        raise HTTPException(
            status_code=400,
            detail="`start` and `end` must be supplied together.",
        )
    for label, val in (("date", date), ("start", start), ("end", end)):
        if val is not None and not _valid_date(val):
            raise HTTPException(
                status_code=400,
                detail=f"`{label}` must be YYYY-MM-DD; got '{val}'.",
            )
    if use_range and start > end:  # type: ignore[operator]
        raise HTTPException(
            status_code=400, detail="`start` must be on or before `end`."
        )

    # ── Build the query for the chosen mode ────────────────────────────────
    # Pacific-day boundary pattern (confirmed against real data):
    #   ts >= (DATE)::timestamp AT TIME ZONE 'America/Los_Angeles'
    #   ts <  (DATE + 1)::timestamp AT TIME ZONE 'America/Los_Angeles'
    # The "+1 day" on the end is what makes the range inclusive of `end`.
    if use_date or use_range:
        lo = date if use_date else start
        hi = date if use_date else end
        query = """
            SELECT ts, series, value
            FROM timeseries_values
            WHERE dataset = %(dataset)s
              AND ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
              AND ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
            ORDER BY ts DESC, series ASC
        """
        params = {
            "dataset": FUEL_MIX_DATASET,
            "lo": lo,
            "hi": hi,
            "tz": MARKET_TZ,
        }
    else:
        # Default mode: latest `limit` DISTINCT timestamps (not raw rows), so
        # `limit` means "5-min intervals" regardless of how many fuels report
        # at each timestamp.
        query = """
            WITH recent_hours AS (
                SELECT DISTINCT ts
                FROM timeseries_values
                WHERE dataset = %(dataset)s
                ORDER BY ts DESC
                LIMIT %(limit)s
            )
            SELECT v.ts, v.series, v.value
            FROM timeseries_values v
            JOIN recent_hours h ON v.ts = h.ts
            WHERE v.dataset = %(dataset)s
            ORDER BY v.ts DESC, v.series ASC
        """
        params = {"dataset": FUEL_MIX_DATASET, "limit": limit}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if not rows:
        # In DEFAULT mode, empty is suspicious (the dataset always has recent
        # hours) — fail loud per Principle #27. In DATE/RANGE mode, empty just
        # means the user picked a day with no data (e.g. before 2020 or a
        # future date) — that's a legitimate 404 with a different message.
        if use_date or use_range:
            window = date if use_date else f"{start}..{end}"
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no '{FUEL_MIX_DATASET}' data for {window} (Pacific). "
                    "Data runs 2020-01 to current."
                ),
            )
        raise HTTPException(
            status_code=404,
            detail=(
                f"no rows for dataset '{FUEL_MIX_DATASET}'. "
                "Verify the dataset name in timeseries_values."
            ),
        )

    if shape == "long":
        return [
            {
                "ts": r["ts"].isoformat(),
                "series": r["series"],
                "value": float(r["value"]),
            }
            for r in rows
        ]

    # pivot: collapse to one object per timestamp, fuel names as keys.
    pivoted: dict[str, dict] = {}
    for r in rows:
        key = r["ts"].isoformat()
        bucket = pivoted.setdefault(key, {"ts": key})
        bucket[r["series"]] = float(r["value"])
    # rows come newest-first; dict preserves insertion order in py3.7+
    return list(pivoted.values())


# ═══════════════════════════════════════════════════════════════════════════
# CAISO forecast-vs-actual — load / solar / wind (D-18-07 data layer, PRs #76–81).
#
# Two stores, keyed differently (confirmed via recon; map across in the join):
#
#   forecasts_caiso  — the VINTAGE store, keyed (forecast_type, product):
#       forecast_type : 'load' | 'solar' | 'wind'
#       product       : the vintage rung — '7DA' | '2DA' | 'DAM'
#       target_ts, generated_ts, issued_ts, value_mw, meta
#     Load carries the full ladder (7DA targets reach +7 days). Solar & wind
#     carry DAM ONLY — CAISO's SLD_REN_FCST is day-ahead; there is no 7-day-ahead
#     renewables forecast at the source. That asymmetry is real, not a gap.
#
#   actuals_caiso    — the realized store, keyed `series`, latest-wins:
#       series : 'load' | 'solar' | 'wind'
#       target_ts, value_mw, meta
#     Realized through ~yesterday 23:00 PT by design; the actual line ends at the
#     last realized interval and the forecast continues past it.
#
# JOIN ASYMMETRY: forecast side keys on `forecast_type`, actual side on `series`.
# Same semantic key ('load'/'solar'/'wind'), different column name — mapped below.
#
# READ-TIME FLATTEN-TO-WIDE (banked contract — done here, never stored): per
# target_ts, collapse the latest vintage of each product into columns
# (actual | DAM [| 2DA | 7DA]), LEFT JOIN the actual onto the forecast spine so
# unrealized future rows survive with is_scored=false. Storage stays vintaged;
# this query collapses it for display. No future-padded axis: the forecast spine
# is the natural trailing edge, and is_scored marks the realized/forecast seam.
#
# NOTE: live information_schema recon for a pre-existing forecast_vs_actual view
# was not possible in this build environment (no DB connection), so the endpoint
# does the flatten-to-wide join itself rather than read an unconfirmed view.
# ═══════════════════════════════════════════════════════════════════════════

FORECAST_TABLE = "forecasts_caiso"
ACTUAL_TABLE = "actuals_caiso"

# Which forecast vintages each quantity carries at the source. Load runs the
# full ladder; solar/wind are DAM-only (day-ahead renewables forecast only).
FORECAST_PRODUCTS: dict[str, list[str]] = {
    "load": ["7DA", "2DA", "DAM"],
    "solar": ["DAM"],
    "wind": ["DAM"],
}


def _mw(v) -> float | None:
    """numeric|None -> float|None, JSON-safe (matches fuel-mix's float() cast)."""
    return None if v is None else float(v)


@app.get("/api/timeseries/caiso-forecast-vs-actual")
async def caiso_forecast_vs_actual(
    quantity: str = Query(
        ...,
        pattern="^(load|solar|wind)$",
        description="Which quantity to serve: 'load', 'solar', or 'wind'. "
        "Load returns the 7DA/2DA/DAM ladder; solar/wind return DAM only "
        "(day-ahead renewables forecast only at the source).",
    ),
    start: str | None = Query(
        default=None,
        description="Window start, Pacific calendar day YYYY-MM-DD (inclusive). "
        "Use with `end`. When omitted, a rolling realized+forecast window is "
        "used (see `days_back`/`horizon`).",
    ),
    end: str | None = Query(
        default=None,
        description="Window end, Pacific calendar day YYYY-MM-DD (inclusive). "
        "Use with `start`.",
    ),
    days_back: int = Query(
        default=7,
        ge=1,
        le=60,
        description="Default-mode realized history: PT days back from today to "
        "include. Ignored when `start`/`end` is supplied.",
    ),
    horizon: int = Query(
        default=8,
        ge=1,
        le=14,
        description="Default-mode forecast horizon: PT days forward from today "
        "to include (8 covers the 7DA edge + buffer). Ignored when `start`/`end` "
        "is supplied. The forecast spine, not this bound, sets the trailing edge.",
    ),
):
    """
    CAISO actual-vs-forecast for one quantity, flattened to wide per target_ts.

    Two selection modes:
      * start=YYYY-MM-DD&end=...  -> explicit inclusive Pacific-day window
      * (neither)                 -> rolling window: today−days_back .. today+horizon (PT)

    Each point is one Pacific-time target interval:
        load:   {ts, actual, "7DA", "2DA", "DAM", is_scored}
        solar/  {ts, actual, "DAM", is_scored}
        wind:

    `actual` is null (and is_scored=false) for unrealized future intervals — the
    forecast continues past the last realized ts, which is the honest seam. The
    actual side keys on `series`; the forecast side on `forecast_type`; the join
    maps the shared semantic key across them.

    Envelope:
        {
          "quantity": "load", "tz": "America/Los_Angeles",
          "window": {"start": "2026-06-16", "end": "2026-07-01"},
          "products": ["7DA", "2DA", "DAM"],
          "last_scored_ts": "2026-06-22T23:00:00+00:00",
          "count": N,
          "points": [ ... ]
        }
    """
    assert _pool is not None

    # ── Decide selection mode + validate (fail loud on bad input) ──────────
    use_range = start is not None or end is not None
    if use_range and not (start is not None and end is not None):
        raise HTTPException(
            status_code=400,
            detail="`start` and `end` must be supplied together.",
        )
    for label, val in (("start", start), ("end", end)):
        if val is not None and not _valid_date(val):
            raise HTTPException(
                status_code=400,
                detail=f"`{label}` must be YYYY-MM-DD; got '{val}'.",
            )
    if use_range and start > end:  # type: ignore[operator]
        raise HTTPException(
            status_code=400, detail="`start` must be on or before `end`."
        )

    if use_range:
        lo_date, hi_date = start, end
    else:
        # "Today" is a Pacific calendar day — the realized edge and forecast
        # both live on the PT clock (reuse fuel-mix's market timezone).
        today_pt = _datetime.now(ZoneInfo(MARKET_TZ)).date()
        lo_date = (today_pt - _timedelta(days=days_back)).isoformat()
        hi_date = (today_pt + _timedelta(days=horizon)).isoformat()

    products = FORECAST_PRODUCTS[quantity]

    # ── Flatten-to-wide at read time ───────────────────────────────────────
    # fc: latest vintage per (target_ts, product) — freshest generated wins.
    # fc_wide: pivot the products into columns for this target_ts.
    # act: realized values for the same quantity, keyed on `series`.
    # Final: forecast spine LEFT JOIN actual, so future rows survive unscored.
    # PT day boundaries reuse the proven fuel-mix pattern (AT TIME ZONE), end
    # exclusive at (hi + 1 day) so the window is inclusive of `hi`.
    query = """
        WITH fc AS (
            SELECT DISTINCT ON (target_ts, product)
                   target_ts, product, value_mw
            FROM forecasts_caiso
            WHERE forecast_type = %(quantity)s
              AND product = ANY(%(products)s)
              AND target_ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
              AND target_ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
            ORDER BY target_ts, product,
                     generated_ts DESC NULLS LAST, issued_ts DESC NULLS LAST
        ),
        fc_wide AS (
            SELECT
                target_ts,
                MAX(value_mw) FILTER (WHERE product = '7DA') AS sevenda,
                MAX(value_mw) FILTER (WHERE product = '2DA') AS twoda,
                MAX(value_mw) FILTER (WHERE product = 'DAM') AS dam
            FROM fc
            GROUP BY target_ts
        ),
        act AS (
            SELECT target_ts, value_mw
            FROM actuals_caiso
            WHERE series = %(quantity)s
              AND target_ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
              AND target_ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
        )
        SELECT
            f.target_ts        AS target_ts,
            a.value_mw         AS actual,
            f.sevenda          AS sevenda,
            f.twoda            AS twoda,
            f.dam              AS dam,
            (a.value_mw IS NOT NULL) AS is_scored
        FROM fc_wide f
        LEFT JOIN act a ON a.target_ts = f.target_ts
        ORDER BY f.target_ts ASC
    """
    params = {
        "quantity": quantity,
        "products": products,
        "lo": lo_date,
        "hi": hi_date,
        "tz": MARKET_TZ,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no '{quantity}' forecast-vs-actual data for "
                f"{lo_date}..{hi_date} (Pacific). Verify forecasts_caiso has "
                f"forecast_type='{quantity}' rows in the window."
            ),
        )

    # ── Shape wide points; honor is_scored; track the realized seam ────────
    # Rows are ascending, so the last is_scored row is the seam (last realized).
    include_ladder = quantity == "load"
    points: list[dict] = []
    last_scored_ts: str | None = None
    for r in rows:
        ts = r["target_ts"].isoformat()
        is_scored = bool(r["is_scored"])
        point: dict = {"ts": ts, "actual": _mw(r["actual"])}
        if include_ladder:
            point["7DA"] = _mw(r["sevenda"])
            point["2DA"] = _mw(r["twoda"])
        point["DAM"] = _mw(r["dam"])
        point["is_scored"] = is_scored
        points.append(point)
        if is_scored:
            last_scored_ts = ts

    return {
        "quantity": quantity,
        "tz": MARKET_TZ,
        "window": {"start": lo_date, "end": hi_date},
        "products": products,
        "last_scored_ts": last_scored_ts,
        "count": len(points),
        "points": points,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Almanac LMP shape endpoint — added May 29, 2026.
#
# Dataset: 'caiso_lmp_da_hourly' — 260k+ rows across SP15/NP15/ZP26,
# Jan 2016 -> current. Backfilled May 29 from CAISO OASIS Historical Data
# Downloader (oasis-bulk.caiso.com) for Jan 2016-Feb 2023, plus existing
# gridstatus coverage Feb 2023-current.
#
# Known coverage gaps (documented, not bugs):
#   - 2016-02-21 through 2016-08-20 (CAISO archive hole)
#   - 2019-01-01 (XML-only zip in OASIS archive; gridstatus didn't backfill)
#
# This endpoint feeds the Almanac's multi-year shape overlay charts. It
# returns 24-hour-by-year price averages in wide format, ready for the
# Sp15ShapeChart React component's data contract.
#
# Block conventions match shape_blocks_v1_sp15.sql exactly:
#   - Hour-ending in Pacific time (HE7 = the hour spanning 06:00-07:00 PT)
#   - On-peak day = Mon-Sat AND not a NERC holiday
#   - When on_peak_days_only=true (default), only those days contribute
# ═══════════════════════════════════════════════════════════════════════════

LMP_DATASET = "caiso_lmp_da_hourly"

# Valid trading hubs in caiso_lmp_da_hourly.series.
# Maps to OASIS NODE_IDs TH_SP15_GEN-APND / TH_NP15_GEN-APND / TH_ZP26_GEN-APND
# (the ingester does this rename on the way in).
_VALID_HUBS = {"SP15", "NP15", "ZP26"}

# NERC holidays for 2016-2026. These match shape_blocks_v1_sp15.sql's locked
# list. Hardcoded rather than computed because observance conventions vary;
# the locked list is the source of truth. When extending past 2026, add the
# year's six holidays here.
_NERC_HOLIDAYS = [
    "2016-01-01", "2016-05-30", "2016-07-04", "2016-09-05", "2016-11-24", "2016-12-26",
    "2017-01-02", "2017-05-29", "2017-07-04", "2017-09-04", "2017-11-23", "2017-12-25",
    "2018-01-01", "2018-05-28", "2018-07-04", "2018-09-03", "2018-11-22", "2018-12-25",
    "2019-01-01", "2019-05-27", "2019-07-04", "2019-09-02", "2019-11-28", "2019-12-25",
    "2020-01-01", "2020-05-25", "2020-07-03", "2020-09-07", "2020-11-26", "2020-12-25",
    "2021-01-01", "2021-05-31", "2021-07-05", "2021-09-06", "2021-11-25", "2021-12-24",
    "2022-12-26", "2022-05-30", "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-05-29", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-05-27", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-05-26", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
]


@app.get("/api/almanac/lmp-shape")
async def almanac_lmp_shape(
    hub: str = Query(
        default="SP15",
        description="Trading hub: SP15, NP15, or ZP26.",
    ),
    months: str = Query(
        default="3,4,5",
        description="Comma-separated month numbers 1-12. Default 3,4,5 (spring).",
    ),
    years: str = Query(
        default="2017,2020,2023,2024,2025,2026",
        description="Comma-separated years to overlay. Default reproduces the "
        "decade-context shape chart.",
    ),
    on_peak_days_only: bool = Query(
        default=True,
        description="If true (default), only Mon-Sat ex-NERC-holidays are "
        "included — matches the locked block definition. If false, all days.",
    ),
):
    """
    Day-ahead LMP shape — 24-hour averages, one row per hour-ending,
    one column per year. Filterable by hub, months, and years.

    Returns wide-format ready for the Sp15ShapeChart `ShapeRow` data contract:

        {
          "data": [
            {"he": 1, "2017": 32.83, "2020": 38.25, "2023": 60.07, ...},
            {"he": 2, ...},
            ... 24 rows total
          ],
          "years": [2017, 2020, 2023, 2024, 2025, 2026],
          "meta": {
            "hub": "SP15",
            "months": [3, 4, 5],
            "n_days_per_year": {"2017": 78, "2020": 77, ...},
            "on_peak_days_only": true,
            "unit": "$/MWh"
          }
        }

    Defaults reproduce the canonical SP15-spring-decade chart (v2b).
    """
    assert _pool is not None

    # ── Validate inputs ───────────────────────────────────────────────────
    hub_upper = hub.upper()
    if hub_upper not in _VALID_HUBS:
        raise HTTPException(
            status_code=400,
            detail=f"`hub` must be one of {sorted(_VALID_HUBS)}; got '{hub}'.",
        )

    month_list = _parse_int_list(months, field_name="months", valid_range=(1, 12))
    year_list = _parse_int_list(years, field_name="years", valid_range=(2016, 2026))

    # ── Build the query ───────────────────────────────────────────────────
    # Pacific-time conversion + month/year/holiday filtering happens here.
    # Conventions (HE = hour + 1, dow 1=Mon..7=Sun, on-peak Mon-Sat ex-holiday)
    # match shape_blocks_v1_sp15.sql exactly.
    query = """
        WITH holidays AS (
            SELECT UNNEST(%(holidays)s::date[]) AS holiday
        ),
        hourly AS (
            SELECT
                v.value::float AS price,
                (v.ts AT TIME ZONE 'America/Los_Angeles')::date AS d,
                EXTRACT(YEAR  FROM v.ts AT TIME ZONE 'America/Los_Angeles')::int AS yr,
                EXTRACT(MONTH FROM v.ts AT TIME ZONE 'America/Los_Angeles')::int AS mo,
                EXTRACT(HOUR  FROM v.ts AT TIME ZONE 'America/Los_Angeles')::int + 1 AS he,
                EXTRACT(ISODOW FROM v.ts AT TIME ZONE 'America/Los_Angeles')::int AS dow
            FROM timeseries_values v
            WHERE v.dataset = %(dataset)s
              AND v.series  = %(hub)s
              AND EXTRACT(YEAR  FROM v.ts AT TIME ZONE 'America/Los_Angeles') = ANY(%(years)s::int[])
              AND EXTRACT(MONTH FROM v.ts AT TIME ZONE 'America/Los_Angeles') = ANY(%(months)s::int[])
        ),
        filtered AS (
            SELECT h.*
            FROM hourly h
            LEFT JOIN holidays hol ON hol.holiday = h.d
            WHERE
              CASE
                WHEN %(on_peak_only)s
                THEN (h.dow BETWEEN 1 AND 6) AND hol.holiday IS NULL
                ELSE TRUE
              END
        )
        SELECT
            yr,
            he,
            ROUND(AVG(price)::numeric, 2)::float AS avg_price,
            COUNT(*) AS n
        FROM filtered
        GROUP BY yr, he
        ORDER BY yr, he
    """
    params = {
        "dataset": LMP_DATASET,
        "hub": hub_upper,
        "years": year_list,
        "months": month_list,
        "holidays": _NERC_HOLIDAYS,
        "on_peak_only": on_peak_days_only,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no '{LMP_DATASET}' data for hub={hub_upper}, months={month_list}, "
                f"years={year_list}. Pantry coverage: 2016-01-01 to current "
                "with documented gaps (Feb-Aug 2016, 2019-01-01)."
            ),
        )

    # ── Pivot from long-format (yr, he, value) to wide-format ─────────────
    # Each output row: {he, "2017": price, "2020": price, ...}
    # Also collect per-year day counts for the meta block.
    by_he: dict[int, dict] = {}
    n_days_per_year: dict[str, int] = {}

    for r in rows:
        he = int(r["he"])
        yr = int(r["yr"])
        avg = r["avg_price"]
        n = int(r["n"])

        bucket = by_he.setdefault(he, {"he": he})
        bucket[str(yr)] = avg

        # Per-year day count is identical across HEs (each day contributes
        # one row per HE), so we capture it from whichever HE we see first.
        yr_key = str(yr)
        if yr_key not in n_days_per_year:
            n_days_per_year[yr_key] = n

    # Output: 24 rows sorted by HE
    data = [by_he[he] for he in sorted(by_he.keys())]

    # Years present (may exclude any requested year that had no data)
    years_present = sorted({int(yr) for yr in n_days_per_year.keys()})

    return {
        "data": data,
        "years": years_present,
        "meta": {
            "hub": hub_upper,
            "months": month_list,
            "n_days_per_year": n_days_per_year,
            "on_peak_days_only": on_peak_days_only,
            "unit": "$/MWh",
        },
    }

# =============================================================================
# Append this block to energylake-api/main.py
# Insert at the END of the file, after the almanac_lmp_shape route.
# =============================================================================
#
# Newswire endpoint — Joule-rewritten headlines + captions from the
# narrative corpus.
#
# Architecture:
#   commentary (raw RSS/scrape titles, bodies, URLs)
#     + narrative_mentions (energy-topic relevance filter)
#     + joule_calls (Joule-rewritten headline + caption)
#     -> served as JSON to Newswire.tsx on /overview
#
# Editorial pipeline producing the data this endpoint serves:
#   scrapers (daily 08:00 PT)
#     -> narrative_tagger (writes narrative_mentions)
#     -> joule_newswire_post.py (writes joule_calls)
#     -> THIS ENDPOINT reads the joined view
#
# Filtering: we only return rows that:
#   1. fired >=1 narrative_mention (energy-relevance signal)
#   2. have a write_newswire_headline joule_call at the current voice version
# A row missing a caption is fine — caption is optional by design.
#
# Voice version: we serve the most-recent voice version per row, so when
# Joule's voice guide bumps from v2 -> v3, the endpoint serves v3 outputs
# automatically as the post-processor catches up.
# =============================================================================

# Pinned to keep all Newswire query knobs in one place.
NEWSWIRE_DEFAULT_LIMIT = 20
NEWSWIRE_MAX_LIMIT = 100


@app.get("/api/newswire/recent")
async def newswire_recent(
    limit: int = Query(
        default=NEWSWIRE_DEFAULT_LIMIT,
        ge=1,
        le=NEWSWIRE_MAX_LIMIT,
        description="Maximum number of items to return, newest first. "
        f"Default {NEWSWIRE_DEFAULT_LIMIT}, max {NEWSWIRE_MAX_LIMIT}.",
    ),
    since_days: int = Query(
        default=30,
        ge=1,
        le=365,
        description="Only include items published within the last N days. "
        "Default 30.",
    ),
):
    """
    Joule-rewritten Newswire items, newest first.

    Returns the editorial-voice headline + (optional) caption per
    energy-relevant commentary row, sourced from the narrative corpus
    after Joule post-processing.

    Response shape:
        {
          "data": [
            {
              "ts": "2026-05-29T16:00:22Z",
              "source_code": "constellation_emu",
              "headline": "Joule headline...",
              "caption": "optional caption" | "",
              "raw_title": "original source title",
              "url": "https://..."
            },
            ...
          ],
          "meta": {
            "limit": 20,
            "since_days": 30,
            "returned": 20
          }
        }

    Notes:
      - `caption` is "" when there's no editorial value beyond the
        headline. The frontend should render the caption block
        conditionally on caption.length > 0.
      - `url` is the link to the original source, not a Newswire
        permalink. Click-through goes to the publisher.
      - Items where Joule has not yet produced a headline are
        omitted entirely — they show up once the post-processor runs.
    """
    assert _pool is not None

    query = """
        WITH headlines AS (
            SELECT DISTINCT ON (jc.input->>'raw_title')
                jc.input->>'raw_title'   AS raw_title,
                jc.output                AS headline,
                jc.meta->>'voice_version' AS voice_version,
                jc.created_at
            FROM joule_calls jc
            WHERE jc.method = 'write_newswire_headline'
              AND length(trim(jc.output)) > 0
            ORDER BY jc.input->>'raw_title', jc.created_at DESC
        ),
        captions AS (
            SELECT DISTINCT ON (jc.input->>'headline')
                jc.input->>'headline'    AS headline,
                jc.output                AS caption,
                jc.meta->>'voice_version' AS voice_version,
                jc.created_at
            FROM joule_calls jc
            WHERE jc.method = 'write_newswire_caption'
            ORDER BY jc.input->>'headline', jc.created_at DESC
        ),
        energy_relevant AS (
            SELECT DISTINCT c.commentary_id
            FROM commentary c
            JOIN narrative_mentions nm ON nm.commentary_id = c.commentary_id
        )
        SELECT
            c.published_ts                       AS ts,
            c.source_code                        AS source_code,
            h.headline                           AS headline,
            COALESCE(cap.caption, '')            AS caption,
            c.title                              AS raw_title,
            c.url                                AS url
        FROM commentary c
        JOIN energy_relevant er ON er.commentary_id = c.commentary_id
        JOIN headlines h        ON h.raw_title    = c.title
        LEFT JOIN captions cap  ON cap.headline   = h.headline
        WHERE c.published_ts > now() - (%(since_days)s || ' days')::interval
        ORDER BY c.published_ts DESC
        LIMIT %(limit)s
    """
    params = {
        "since_days": str(since_days),
        "limit": limit,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    # Normalize timestamps to ISO 8601 with Z suffix so the frontend
    # can parse with `new Date(...)` consistently.
    data = []
    for r in rows:
        ts = r["ts"]
        # psycopg returns timezone-aware datetimes; isoformat() preserves tz
        ts_iso = ts.isoformat() if ts is not None else None
        data.append({
            "ts": ts_iso,
            "source_code": r["source_code"],
            "headline": r["headline"],
            "caption": r["caption"] or "",
            "raw_title": r["raw_title"],
            "url": r["url"],
        })

    return {
        "data": data,
        "meta": {
            "limit": limit,
            "since_days": since_days,
            "returned": len(data),
        },
    }

# =============================================================================
# /api/tape/recent
# =============================================================================
#
# Tape endpoint — Joule-rewritten headlines for material SEC filings (and
# eventually IR press) from the 24-company power-sector watchlist.
#
# Architecture:
#   tape_filings (SEC 8-K/10-Q/10-K/425 + future IR press, all body-cleaned)
#     + joule_calls (Joule-rewritten headline via write_tape_headline)
#     -> served as JSON to the right column of Newswire.tsx on /overview
#
# Pipeline producing the data this endpoint serves:
#   scrapers.sec_filings (daily 08:00 PT, future: + utility_ir_press)
#     -> joule_tape_post.py (writes joule_calls)
#     -> THIS ENDPOINT reads the joined view
#
# Filtering:
#   Only rows that have a write_tape_headline joule_call at one of the
#   ALLOWLISTED voice versions are returned. This decouples the writer (which
#   can regenerate headlines at a new voice version) from the reader (which
#   pins to whatever versions are shipped). When voice bumps, the allowlist
#   constant below is the single line that ships the new voice.
#
# Voice allowlist + newest-wins (D-2026-06-11-01): pantry PR #37 introduces
# v3.1 corrections, written as NEW joule_calls rows that supersede the v3
# original for the same external_id. The join therefore selects the NEWEST
# write_tape_headline per external_id across the allowlist (DISTINCT ON
# external_id ORDER BY created_at DESC):
#   * a v3.1 correction supersedes its v3 original (the original stale-caption
#     fix is preserved — newest wins);
#   * a filing with no v3.1 row keeps its v3 headline (still visible).
# Pinning to a single version would regress one of these: pinned-at-v3 hides
# every correction, pinned-at-v3.1 blanks every headline not re-enriched.
# =============================================================================

# Pinned to keep all Tape query knobs in one place.
TAPE_DEFAULT_LIMIT = 20
TAPE_MAX_LIMIT = 100
# Base pinned voice. Reported in meta.voice_version (kept byte-compatible) and
# used in docstring response examples.
TAPE_VOICE_VERSION = "v3"
# Voice allowlist for the headline join (D-2026-06-11-01). Newest matching
# joule_call per external_id wins; see the block comment above.
TAPE_VOICE_VERSIONS = ("v3", "v3.1")


@app.get("/api/tape/recent")
async def tape_recent(
    limit: int = Query(
        default=TAPE_DEFAULT_LIMIT,
        ge=1,
        le=TAPE_MAX_LIMIT,
        description="Maximum number of items to return, newest first. "
        f"Default {TAPE_DEFAULT_LIMIT}, max {TAPE_MAX_LIMIT}.",
    ),
    since_days: int = Query(
        default=1500,
        ge=1,
        le=2000,
        description="Only include filings published within the last N days. "
        "Default 1500 (~4 years), max 2000.",
    ),
):
    """
    Joule-rewritten Tape items, newest first.

    DEPRECATED (D-2026-06-10-03): prefer GET /api/wire/recent, which returns
    this same shape plus `is_power_signal`, and adds an optional `stream`
    filter over `source_type`. This endpoint is retained byte-compatible for
    the live homepage Newswire and will be removed once the dashboard PR
    migrates the Newswire to /api/wire/recent.

    As of D-2026-06-10-03 this endpoint now applies the power-signal
    predicate (`is_power_signal IS DISTINCT FROM FALSE`) so it matches the
    set served by /api/wire/recent — rows explicitly flagged as non-power
    signal are excluded; NULL (unclassified) rows are kept.

    Returns trader-grade editorial headlines for material SEC filings from
    the 24-company power-sector watchlist, paired with their original
    filing metadata (ticker, form, item codes, link to SEC).

    Response shape:
        {
          "data": [
            {
              "ts": "2026-06-02T16:15:28+00:00",
              "ticker": "CEG",
              "form_type": "8-K",
              "item_codes": ["8.01", "9.01"],
              "headline": "CEG selling shareholders launch secondary...",
              "raw_title": "Other Material Event",
              "url": "https://www.sec.gov/Archives/edgar/...",
              "source_type": "sec_filing",
              "external_id": "0001104659-26-069482"
            },
            ...
          ],
          "meta": {
            "limit": 20,
            "since_days": 1500,
            "returned": 20,
            "voice_version": "v3"
          }
        }

    Notes:
      - `headline` is always non-empty (rows lacking Joule output are
        excluded by the JOIN).
      - `url` is the SEC EDGAR document link, not a Newswire permalink.
        Click-through goes to the original filing.
      - `raw_title` is the synthetic title built by sec_filings._build_title
        from form type + 8-K item code labels — useful for hover tooltip
        context on the frontend.
      - `item_codes` may be `null` for non-8-K forms (10-Q, 10-K, 425).
    """
    assert _pool is not None

    query = """
        SELECT
            tf.published_ts                  AS ts,
            tf.ticker                        AS ticker,
            tf.form_type                     AS form_type,
            tf.meta->'item_codes'            AS item_codes,
            jc.output                        AS headline,
            tf.title                         AS raw_title,
            tf.filing_url                    AS url,
            tf.source_type                   AS source_type,
            tf.external_id                   AS external_id
        FROM tape_filings tf
        -- Newest write_tape_headline per external_id across the voice
        -- allowlist (D-2026-06-11-01). v3.1 corrections (pantry PR #37) are
        -- written as superseding joule_calls rows; DISTINCT ON + created_at
        -- DESC makes a correction supersede its v3 original, while filings
        -- with no v3.1 keep their v3 headline. The non-empty output filter
        -- lives inside so an empty row never supersedes a good headline.
        JOIN (
            SELECT DISTINCT ON (input->>'external_id')
                input->>'external_id'  AS external_id,
                output                 AS output,
                created_at             AS created_at
            FROM joule_calls
            WHERE method = 'write_tape_headline'
              AND meta->>'voice_version' = ANY(%(voice_versions)s)
              AND output IS NOT NULL
              AND length(trim(output)) > 0
            ORDER BY input->>'external_id', created_at DESC
        ) jc ON jc.external_id = tf.external_id
        WHERE
          -- D-2026-06-10-03: power-signal predicate. IS DISTINCT FROM FALSE
          -- keeps NULL (unclassified) rows and drops only explicit FALSE.
          tf.is_power_signal IS DISTINCT FROM FALSE
          AND tf.published_ts > now() - (%(since_days)s || ' days')::interval
        ORDER BY tf.published_ts DESC, jc.created_at DESC
        LIMIT %(limit)s
    """
    params = {
        "voice_versions": list(TAPE_VOICE_VERSIONS),
        "since_days": str(since_days),
        "limit": limit,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    # Normalize timestamps to ISO 8601 so the frontend can parse with
    # `new Date(...)` consistently. Matches the Newswire endpoint pattern.
    data = []
    for r in rows:
        ts = r["ts"]
        ts_iso = ts.isoformat() if ts is not None else None
        data.append({
            "ts": ts_iso,
            "ticker": r["ticker"],
            "form_type": r["form_type"],
            "item_codes": r["item_codes"],
            "headline": r["headline"],
            "raw_title": r["raw_title"],
            "url": r["url"],
            "source_type": r["source_type"],
            "external_id": r["external_id"],
        })

    return {
        "data": data,
        "meta": {
            "limit": limit,
            "since_days": since_days,
            "returned": len(data),
            "voice_version": TAPE_VOICE_VERSION,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# Daily briefs — Tape rebuild Phase 3a (2026-06-10)
#
# Joule's daily editorial brief: a single markdown narrative per day,
# produced by the brief writer and stored in joule_briefs.
#
# Table (per the Phase 3a brief; read-only, same Neon pantry):
#   joule_briefs(brief_date date, brief_type text, headline text,
#                content_md text, word_count int, voice_version text,
#                created_at timestamptz, ...)
#
# These endpoints are read-only and serve only brief_type='daily'. They
# return a single brief object (not a {data, meta} envelope) because the
# consumer renders one brief at a time.
# ═══════════════════════════════════════════════════════════════════════════

BRIEF_TYPE_DAILY = "daily"

# The column set both daily-brief endpoints return, kept in one place so the
# two queries (latest / by-date) stay shape-identical.
_BRIEF_SELECT = """
    SELECT
        brief_date,
        headline,
        content_md,
        word_count,
        voice_version,
        created_at
    FROM joule_briefs
    WHERE brief_type = %(brief_type)s
"""


def _shape_brief(r: dict) -> dict:
    """Normalize one joule_briefs row to the JSON brief contract."""
    bd = r["brief_date"]
    ca = r["created_at"]
    return {
        "brief_date": bd.isoformat() if bd is not None else None,
        "headline": r["headline"],
        "content_md": r["content_md"],
        "word_count": r["word_count"],
        "voice_version": r["voice_version"],
        "created_at": ca.isoformat() if ca is not None else None,
    }


@app.get("/api/briefs/daily/latest")
async def briefs_daily_latest():
    """
    Most recent daily Joule brief.

    Returns the single newest row WHERE brief_type='daily', ordered by
    brief_date DESC. 404 if no daily brief exists yet.

    Response shape:
        {
          "brief_date": "2026-06-10",
          "headline": "...",
          "content_md": "# ...\\n\\n...",
          "word_count": 412,
          "voice_version": "v3",
          "created_at": "2026-06-10T13:00:00+00:00"
        }
    """
    assert _pool is not None

    query = _BRIEF_SELECT + "\n    ORDER BY brief_date DESC\n    LIMIT 1\n"
    params = {"brief_type": BRIEF_TYPE_DAILY}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="no daily brief found (joule_briefs has no brief_type='daily' rows yet).",
        )

    return _shape_brief(row)


@app.get("/api/briefs/daily/{date}")
async def briefs_daily_by_date(
    date: _date = Path(
        ...,
        description="Brief date as an ISO calendar day, YYYY-MM-DD. "
        "Non-ISO input is rejected with 422.",
    ),
):
    """
    Daily Joule brief for an exact date.

    `date` must be an ISO 8601 calendar day (YYYY-MM-DD); anything else is
    rejected with 422 by request validation. 404 if no daily brief exists
    for that date.

    Same response shape as /api/briefs/daily/latest.
    """
    assert _pool is not None

    query = _BRIEF_SELECT + "\n      AND brief_date = %(brief_date)s\n    LIMIT 1\n"
    params = {"brief_type": BRIEF_TYPE_DAILY, "brief_date": date}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"no daily brief for {date.isoformat()}.",
        )

    return _shape_brief(row)


# ═══════════════════════════════════════════════════════════════════════════
# /api/joule/chart-brief — the render leg for Joule chart briefs (#99)
#
# #99 writes 'caiso_fuel_mix_chart' briefs into joule_briefs on the 1:41am
# cron, but nothing reads them yet. This endpoint serves the most recent brief
# for a chart, parameterized by brief_type so the load/renewables chart briefs
# drop in later behind the same route (one endpoint, not N).
#
# Latest-row, NOT DISTINCT ON: we take the newest row by created_at, which
# sidesteps the voice-version-leak debt on the newswire DISTINCT ON query
# (stale older-voice rows slipping through). The newest fuel-mix row is the
# current voice by construction. Only PASS briefs are ever written
# (fail-loud-no-write), and there is no status column, so no status filter is
# needed — any row present is publishable.
#
# Empty case is a 200 with body=null (the brief_type is echoed back), NOT a
# 404: the dashboard must degrade quietly when no brief exists yet, never
# error. Unknown brief_type is a 400.
# ═══════════════════════════════════════════════════════════════════════════

# Allowlist of chart-brief types this endpoint will serve. Starts with just the
# fuel-mix chart; load/renewables types drop in here once #99 writes them.
CHART_BRIEF_TYPES = frozenset({"caiso_fuel_mix_chart"})


class ChartBrief(BaseModel):
    """
    Render contract for a Joule chart brief. The dashboard binds to this shape.

    brief_type is always present (echoed from the validated query param). Every
    other field is Optional because the empty case (no brief written yet)
    returns this model with body/brief_date/... = null rather than a 404.
    """

    brief_type: str
    body: Optional[str] = None
    brief_date: Optional[str] = None
    voice_version: Optional[str] = None
    word_count: Optional[int] = None
    generated_at: Optional[str] = None
    id: Optional[int] = None


# Latest row for a brief_type, newest first. NOT DISTINCT ON (see block above).
_CHART_BRIEF_SELECT = """
    SELECT
        id,
        brief_type,
        brief_date,
        content_md,
        voice_version,
        word_count,
        created_at
    FROM joule_briefs
    WHERE brief_type = %(brief_type)s
    ORDER BY created_at DESC
    LIMIT 1
"""


@app.get("/api/joule/chart-brief", response_model=ChartBrief)
async def joule_chart_brief(
    brief_type: str = Query(
        ...,
        description="Chart-brief discriminator. Currently only "
        "'caiso_fuel_mix_chart'. Unknown types are rejected with 400.",
    ),
):
    """
    Most recent Joule chart brief for a given brief_type.

    Returns the single newest row (by created_at) WHERE brief_type matches.

    - Unknown brief_type (not in the allowlist) -> 400.
    - No brief written yet -> 200 with body=null (NOT 404), so the dashboard
      degrades quietly.

    Response shape (the contract the dashboard binds to):
        {
          "brief_type": "caiso_fuel_mix_chart",
          "body": "...content_md...",
          "brief_date": "2026-06-22",
          "voice_version": "v2.1",
          "word_count": 71,
          "generated_at": "2026-06-23T11:45:37.734038+00:00",
          "id": 18
        }
    """
    assert _pool is not None

    if brief_type not in CHART_BRIEF_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown brief_type '{brief_type}'. "
            f"Allowed: {sorted(CHART_BRIEF_TYPES)}.",
        )

    params = {"brief_type": brief_type}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_CHART_BRIEF_SELECT, params)
                row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    # Empty case: echo the brief_type, everything else null. 200, not 404.
    if row is None:
        return ChartBrief(brief_type=brief_type)

    bd = row["brief_date"]
    ca = row["created_at"]
    return ChartBrief(
        brief_type=row["brief_type"],
        body=row["content_md"],
        brief_date=bd.isoformat() if bd is not None else None,
        voice_version=row["voice_version"],
        word_count=row["word_count"],
        generated_at=ca.isoformat() if ca is not None else None,
        id=row["id"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# /api/wire/recent — Tape rebuild Phase 3a (2026-06-10)
#
# Successor to /api/tape/recent. Same item shape, plus `is_power_signal`,
# plus an optional `stream` filter over tape_filings.source_type.
#
# Filtering:
#   * is_power_signal IS DISTINCT FROM FALSE — keep power signals AND
#     unclassified (NULL) rows; drop only rows explicitly flagged FALSE.
#   * Same Joule-headline join as the Tape endpoint (write_tape_headline at
#     the pinned voice version), so every returned row has a headline.
#   * Optional stream filter narrows source_type to one known stream.
#
# limit defaults to 50 and is HARD-CAPPED (clamped, not 422'd) at 200, so a
# caller asking for more simply gets 200.
# ═══════════════════════════════════════════════════════════════════════════

WIRE_DEFAULT_LIMIT = 50
WIRE_MAX_LIMIT = 200


class WireStream(str, Enum):
    """Known tape_filings.source_type values accepted by the `stream` filter."""

    sec_filing = "sec_filing"
    ir_press = "ir_press"


@app.get("/api/wire/recent")
async def wire_recent(
    stream: WireStream | None = Query(
        default=None,
        description="Optional source_type filter. One of 'sec_filing' or "
        "'ir_press'. Unknown values are rejected with 422.",
    ),
    limit: int = Query(
        default=WIRE_DEFAULT_LIMIT,
        ge=1,
        description="Maximum number of items to return, newest first. "
        f"Default {WIRE_DEFAULT_LIMIT}; values above the hard cap of "
        f"{WIRE_MAX_LIMIT} are clamped down to {WIRE_MAX_LIMIT}.",
    ),
):
    """
    Power-signal tape filings with Joule headlines, newest first.

    Successor to /api/tape/recent: identical item shape, plus the
    `is_power_signal` flag, plus an optional `stream` filter.

    Returns rows from tape_filings where `is_power_signal IS DISTINCT FROM
    FALSE` (power signals and unclassified rows; explicit non-signals are
    dropped) that have a Joule `write_tape_headline` at the pinned voice
    version. Ordered by published_ts DESC.

    Response shape:
        {
          "data": [
            {
              "ts": "2026-06-02T16:15:28+00:00",
              "ticker": "CEG",
              "form_type": "8-K",
              "item_codes": ["8.01", "9.01"],
              "headline": "CEG selling shareholders launch secondary...",
              "raw_title": "Other Material Event",
              "url": "https://www.sec.gov/Archives/edgar/...",
              "source_type": "sec_filing",
              "external_id": "0001104659-26-069482",
              "is_power_signal": true
            },
            ...
          ],
          "meta": {
            "limit": 50,
            "stream": "sec_filing" | null,
            "returned": 50,
            "voice_version": "v3"
          }
        }
    """
    assert _pool is not None

    # Hard cap: clamp rather than reject, so over-large requests still succeed.
    effective_limit = min(limit, WIRE_MAX_LIMIT)

    query = """
        SELECT
            tf.published_ts                  AS ts,
            tf.ticker                        AS ticker,
            tf.form_type                     AS form_type,
            tf.meta->'item_codes'            AS item_codes,
            jc.output                        AS headline,
            tf.title                         AS raw_title,
            tf.filing_url                    AS url,
            tf.source_type                   AS source_type,
            tf.external_id                   AS external_id,
            tf.is_power_signal               AS is_power_signal
        FROM tape_filings tf
        -- Newest write_tape_headline per external_id across the voice
        -- allowlist (D-2026-06-11-01); see /api/tape/recent for the full
        -- rationale. v3.1 corrections supersede their v3 originals; filings
        -- with no v3.1 keep their v3 headline.
        JOIN (
            SELECT DISTINCT ON (input->>'external_id')
                input->>'external_id'  AS external_id,
                output                 AS output,
                created_at             AS created_at
            FROM joule_calls
            WHERE method = 'write_tape_headline'
              AND meta->>'voice_version' = ANY(%(voice_versions)s)
              AND output IS NOT NULL
              AND length(trim(output)) > 0
            ORDER BY input->>'external_id', created_at DESC
        ) jc ON jc.external_id = tf.external_id
        WHERE tf.is_power_signal IS DISTINCT FROM FALSE
          AND (%(stream)s::text IS NULL OR tf.source_type = %(stream)s)
        ORDER BY tf.published_ts DESC, jc.created_at DESC
        LIMIT %(limit)s
    """
    params = {
        "voice_versions": list(TAPE_VOICE_VERSIONS),
        "stream": stream.value if stream is not None else None,
        "limit": effective_limit,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    data = []
    for r in rows:
        ts = r["ts"]
        ts_iso = ts.isoformat() if ts is not None else None
        data.append({
            "ts": ts_iso,
            "ticker": r["ticker"],
            "form_type": r["form_type"],
            "item_codes": r["item_codes"],
            "headline": r["headline"],
            "raw_title": r["raw_title"],
            "url": r["url"],
            "source_type": r["source_type"],
            "external_id": r["external_id"],
            "is_power_signal": r["is_power_signal"],
        })

    return {
        "data": data,
        "meta": {
            "limit": effective_limit,
            "stream": stream.value if stream is not None else None,
            "returned": len(data),
            "voice_version": TAPE_VOICE_VERSION,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/regulatory/board — Regulatory Board (D-2026-06-14-03, step 2)
#
# Serves the `regulatory_board` SQL view (pantry migration 043) as JSON for the
# frontend's Regulatory Board page. The view owns the editorial fields AND the
# salience formula; this endpoint is a thin read-through — it does not invent
# fields or recompute salience. Same read-only Neon pantry, same conn/CORS
# conventions as the rest of this service.
#
# View columns surfaced (one row per docket item):
#   id, body, docket, title, summary, theme[], status, importance,
#   market_impact, impact_note, trading_angle, is_editorial, key_dates[],
#   next_date, days_until, source_url[], salience, provenance, on_board
#
# Default: on-board items only (WHERE on_board). `?include_resolved=true`
# returns the full set (resolved items included, still scored by the view).
#
# `?body=` is a GENERAL filter (NOT CAISO-hardcoded): accepts any known body,
# comma-separated for multiple (e.g. ?body=CAISO, ?body=FERC,CPUC). The CAISO
# page calls ?body=CAISO to embed its slice, but the door is built wide for
# every body. Unknown bodies fail loud with 400 (Principle #27).
# ═══════════════════════════════════════════════════════════════════════════

# Known regulatory bodies, the validation set for the `?body=` filter. This is
# the single place the accepted-body list lives; extend here when the pantry
# starts tracking a new body.
REGULATORY_BODIES = (
    "FERC", "CPUC", "CAISO", "NERC", "CARB", "NRC", "CA_LEG", "REGIONAL", "BPA",
)
_REGULATORY_BODY_SET = frozenset(REGULATORY_BODIES)


def _parse_body_filter(s: str) -> list[str]:
    """Parse 'FERC,CPUC' -> ['FERC','CPUC']; validate against the known body
    set (case-insensitive); raise HTTPException(400) on empty or unknown.
    Order is preserved and duplicates collapsed so the bound array is clean."""
    raw = [x.strip().upper() for x in s.split(",") if x.strip()]
    if not raw:
        raise HTTPException(
            status_code=400, detail="`body` cannot be empty when supplied."
        )
    unknown = [b for b in raw if b not in _REGULATORY_BODY_SET]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown body value(s) {unknown}; "
                f"valid bodies are {list(REGULATORY_BODIES)}."
            ),
        )
    seen: list[str] = []
    for b in raw:
        if b not in seen:
            seen.append(b)
    return seen


def _shape_board_item(r: dict) -> dict:
    """Normalize one regulatory_board row to the JSON board contract.

    Pass the view through verbatim — the only conversions are JSON-safety:
    next_date -> ISO string (or null), salience -> float. theme / key_dates /
    source_url arrive from the view as JSON-ready lists; market_impact is the
    view's own value ('na' when none); impact_note / trading_angle are null
    when absent. Nothing here recomputes or invents a field."""
    nd = r["next_date"]
    sal = r["salience"]
    return {
        "id": r["id"],
        "body": r["body"],
        "docket": r["docket"],
        "title": r["title"],
        "summary": r["summary"],
        "theme": r["theme"],
        "status": r["status"],
        "importance": r["importance"],
        "market_impact": r["market_impact"],
        "impact_note": r["impact_note"],
        "trading_angle": r["trading_angle"],
        "is_editorial": r["is_editorial"],
        "key_dates": r["key_dates"],
        "next_date": nd.isoformat() if nd is not None else None,
        "days_until": r["days_until"],
        "source_url": r["source_url"],
        "salience": float(sal) if sal is not None else None,
        "provenance": r["provenance"],
    }


@app.get("/api/regulatory/board")
async def regulatory_board(
    include_resolved: bool = Query(
        default=False,
        description="If false (default), return only on-board items "
        "(WHERE on_board). If true, return all items including resolved ones "
        "(still scored by the view).",
    ),
    body: str | None = Query(
        default=None,
        description="Optional regulatory-body filter. One body or a "
        "comma-separated list (e.g. 'CAISO' or 'FERC,CPUC'). Case-insensitive. "
        f"Valid bodies: {', '.join(REGULATORY_BODIES)}. Unknown values are "
        "rejected with 400. Defaults to all bodies.",
    ),
):
    """
    Regulatory Board — the `regulatory_board` view served as JSON.

    Default returns on-board items only; `?include_resolved=true` returns the
    full set. `?body=` filters to one or more bodies (general, not hardcoded).
    Ordered by salience DESC, importance DESC, body ASC. `as_of` is the
    database's CURRENT_DATE.

    Response shape:
        {
          "as_of": "2026-06-14",
          "count": 22,
          "items": [
            {
              "id": "ferc_rm26_4", "body": "FERC", "docket": "RM26-4",
              "title": "...", "summary": "...",
              "theme": ["Large_Load", "Interconnection"],
              "status": "pending_decision", "importance": 5,
              "market_impact": "bullish", "impact_note": "...",
              "trading_angle": "...", "is_editorial": true,
              "key_dates": [{"type": "decision_expected", "date": "2026-06-30"}],
              "next_date": "2026-06-30", "days_until": 16,
              "source_url": ["https://..."], "salience": 11.0,
              "provenance": "curated"
            },
            ...
          ]
        }

    Notes:
      - `market_impact` is "na" when none; `impact_note` / `trading_angle` are
        null when absent — both come straight from the view.
      - `salience` is owned by the view; this endpoint never recomputes it.
    """
    assert _pool is not None

    bodies = _parse_body_filter(body) if body is not None else None

    # The view owns the columns and the salience formula; we read them through.
    # WHERE: default to on-board only; include_resolved flips to the full set.
    # The body filter binds a text[] (or NULL = all bodies) and matches with
    # = ANY(...), so one body or many costs the same single bound parameter.
    query = """
        SELECT
            id,
            body,
            docket,
            title,
            summary,
            theme,
            status,
            importance,
            market_impact,
            impact_note,
            trading_angle,
            is_editorial,
            key_dates,
            next_date,
            days_until,
            source_url,
            salience,
            provenance,
            CURRENT_DATE AS as_of
        FROM regulatory_board
        WHERE (%(include_resolved)s OR on_board)
          AND (%(bodies)s::text[] IS NULL OR body = ANY(%(bodies)s))
        ORDER BY salience DESC, importance DESC, body ASC
    """
    params = {
        "include_resolved": include_resolved,
        "bodies": bodies,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    items = [_shape_board_item(r) for r in rows]

    # as_of is the DB's CURRENT_DATE, carried on each row. An empty result (a
    # body filter that matches nothing, or an all-resolved board) is a valid
    # 200 with count 0 — fall back to the server's date only for that edge so
    # the contract field is never missing.
    as_of = rows[0]["as_of"].isoformat() if rows else _date.today().isoformat()

    return {
        "as_of": as_of,
        "count": len(items),
        "items": items,
    }