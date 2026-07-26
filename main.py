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
    GET /api/timeseries/caiso-forecast-vs-actual  load/solar/wind actual-vs-forecast, wide,
                                                   date-/range-addressable (?date, ?start/?end)
    GET /api/timeseries/caiso-demand-stack        forward demand-stack (total/net/solar/wind, fc+act)
    GET /api/timeseries/caiso-peak-demand         derived daily peak MW + peak HE per TAC area (from caiso_load_fcst_7day)
    GET /api/timeseries/caiso-hub-lmp      CAISO trading-hub LMP (DA/RTPD/RTD + DART), prev+current PT day
    GET /api/almanac/lmp-shape             LMP shape overlay (M2 — added May 29)
    GET /api/newswire/recent               Joule Newswire items
    GET /api/tape/recent                   Joule Tape items (DEPRECATED — see /api/wire/recent)
    GET /api/briefs/daily/latest           most recent daily Joule brief (Tape 3a)
    GET /api/briefs/daily/{date}           daily Joule brief by date (Tape 3a)
    GET /api/wire/recent                   power-signal filings, successor to /api/tape/recent (Tape 3a)
    GET /api/regulatory/board              regulatory_board view as JSON, body-filterable (D-2026-06-14-03)
    GET /api/joule/chart-brief             latest Joule chart brief by brief_type (#99 render leg)
    GET /api/atlas/pnode-lmp               latest complete CAISO pnode-LMP snapshot, columnar prices-only (D-07-05-09)
    GET /api/atlas/pnode-history           7-day price-component history for one pnode, columnar prices-only (D-07-08)
    GET /api/atlas/constraints             CAISO binding-constraint blotter league table (DAM/RTM, windowed), cached (D-07-11)
    GET /api/atlas/constraints/fingerprint per-node constraint fingerprint (delta/beta by pnode, geo-joined), newest-success-scoped, cached (D-07-12)
    GET /api/atlas/nodes/fingerprint       inverse fingerprint: constraints ranked by how they move one pnode, newest-success-scoped, cached (D-07-12)
    GET /api/nodes/search                  Cockpit node search: up to 50 snapshot-distinct nodes by pnode_id OR plant-name (Atlas crosswalk) substring (+area/node_type), from the latest instant (D-07-21)
    GET /api/nodes/facets                  Cockpit facets: distinct area / node_type categories with node counts over the same latest-instant universe as /nodes/search, cached (D-07-21)
    POST /api/watchboard                   Cockpit/Watchboard v0: polymorphic per-tile board read (tile_type=pnode; views lmp|components|dart|basis), per-tile-isolated (D-07-21)
    GET /api/model-room/cycles             Model Room: published D2 synoptic cycles for a model over the last N UTC dates, from the R2 archive (D-07-22)
    GET /api/model-room/frame/{key}        Model Room: stream one archived D2 frame (PNG/JSON) from R2, d2/-allowlisted, long immutable cache (D-07-22)
"""

import json
import logging
import os
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path as _Path
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
import psycopg
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row

from publication_clock import compute_status as _compute_publication_status
import market_clock as _mc


def _utcnow() -> _datetime:
    """Current instant as a tz-aware UTC datetime.

    A one-line seam so the publication clock's `now` is injectable in tests
    (monkeypatch main._utcnow) without freezing the whole process clock.
    """
    return _datetime.now(_timezone.utc)

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


# Starlette's JSONResponse emits correct UTF-8 bytes (json.dumps(...,
# ensure_ascii=False).encode("utf-8")) but labels them only "application/json"
# — it appends "; charset=utf-8" for text/* media types, not for JSON. A
# browser's raw-JSON view then guesses Latin-1/CP1252 and renders a stored "—"
# (UTF-8 E2 80 94) as "â€"". The bytes are already correct (Fetch's .json()
# decodes them fine), so this is a display-only label fix: make the charset
# explicit on every JSON response. Subclassing keeps response_model
# serialization intact — a raw per-route JSONResponse would bypass Pydantic.
class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="EnergyLake API",
    version="0.2.0",
    description="Read-only pantry access for the EnergyLake frontend.",
    lifespan=lifespan,
    default_response_class=UTF8JSONResponse,
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
    # POST joins GET for the Cockpit/Watchboard batch endpoint (/api/watchboard),
    # the repo's first POST — a batched board read whose body carries the tile
    # list. Every other route stays GET; this is purely additive.
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Ride-along with the pnode-LMP endpoint (D-07-05-09): /api/atlas/pnode-lmp is
# the repo's first multi-MB payload (~14,417-row columnar JSON), so app-wide
# gzip lands here rather than in its own PR. Assumed-safe-by-construction, not
# measured: GZipMiddleware only compresses when the client sends
# Accept-Encoding: gzip, so it is harmless even if Railway's edge already
# compresses — the real end-to-end size/timing is acceptance receipt #5,
# post-deploy. minimum_size=1000 skips tiny bodies (health, error envelopes)
# where framing overhead would dwarf any savings.
app.add_middleware(GZipMiddleware, minimum_size=1000)


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


# Range mode (start/end) cap. ≤ 31 days bounds the per-day response so a
# month-long synthesis read stays one call without unbounded fan-out.
FORECAST_VA_MAX_RANGE_DAYS = 31


def _pacific_day(target_ts) -> str:
    """Pacific calendar day (YYYY-MM-DD) for a tz-aware target_ts. The PT clock
    is the market day boundary, so a UTC-stored interval is bucketed by the PT
    day it falls in (the same boundary the SQL window uses)."""
    return target_ts.astimezone(ZoneInfo(MARKET_TZ)).date().isoformat()


def _shape_fva_points(rows, include_ladder: bool):
    """Wide DB rows -> (points, last_scored_ts).

    The single shaping path shared by default, ?date, and range modes so every
    point object is byte-identical regardless of how the window was selected.
    Rows arrive ascending, so the last is_scored row is the realized seam.
    """
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
    return points, last_scored_ts


@app.get("/api/timeseries/caiso-forecast-vs-actual")
async def caiso_forecast_vs_actual(
    quantity: str = Query(
        ...,
        pattern="^(load|solar|wind)$",
        description="Which quantity to serve: 'load', 'solar', or 'wind'. "
        "Load returns the 7DA/2DA/DAM ladder; solar/wind return DAM only "
        "(day-ahead renewables forecast only at the source).",
    ),
    date: str | None = Query(
        default=None,
        description="A single Pacific calendar day, YYYY-MM-DD (past or "
        "present). Returns that day's forecast-vs-actual series. On a "
        "fully-realized past day the now-seam is suppressed (is_today=false, "
        "seam=null); on today the live realized/forecast seam is returned. "
        "Mutually exclusive with `start`/`end`.",
    ),
    start: str | None = Query(
        default=None,
        description="Range start, Pacific calendar day YYYY-MM-DD (inclusive). "
        "Use with `end`. Range mode returns per-day records keyed by date "
        f"(the synthesis read), capped at {FORECAST_VA_MAX_RANGE_DAYS} days. "
        "When neither `date` nor `start`/`end` is given, a rolling "
        "realized+forecast window is used (see `days_back`/`horizon`).",
    ),
    end: str | None = Query(
        default=None,
        description="Range end, Pacific calendar day YYYY-MM-DD (inclusive). "
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

    Three selection modes (date-addressable spine):
      * date=YYYY-MM-DD           -> a single Pacific calendar day (past or present)
      * start=YYYY-MM-DD&end=...  -> inclusive Pacific-day range, per-day records
      * (neither)                 -> rolling window: today−days_back .. today+horizon (PT)

    Each point is one Pacific-time target interval:
        load:   {ts, actual, "7DA", "2DA", "DAM", is_scored}
        solar/  {ts, actual, "DAM", is_scored}
        wind:

    `actual` is null (and is_scored=false) for unrealized future intervals — the
    forecast continues past the last realized ts, which is the honest seam. The
    actual side keys on `series`; the forecast side on `forecast_type`; the join
    maps the shared semantic key across them. The forecast vintage selected is
    the latest generated for each target_ts, so a past date returns the forecast
    that was published for it (not today's).

    The now-seam is data-extent-derived, never wall-clock: it is the last
    realized interval. In `date`/range mode each day also carries an explicit
    `is_today` + `seam` signal — on a fully-realized PAST day `seam` is null and
    `is_today` is false (no now-line); on today `seam` is the live realized edge.

    Default envelope (no param — byte-for-byte unchanged):
        {
          "quantity": "load", "tz": "America/Los_Angeles",
          "window": {"start": "2026-06-16", "end": "2026-07-01"},
          "products": ["7DA", "2DA", "DAM"],
          "last_scored_ts": "2026-06-22T23:00:00+00:00",
          "count": N,
          "points": [ ... ]
        }

    ?date envelope adds `date`, `is_today`, `seam`; range envelope replaces
    `points` with a `days` array of per-day records, each shaped like ?date.
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
    if use_range:
        span_days = (_date.fromisoformat(end) - _date.fromisoformat(start)).days + 1
        if span_days > FORECAST_VA_MAX_RANGE_DAYS:
            raise HTTPException(
                status_code=400,
                detail=f"range spans {span_days} days; the maximum is "
                f"{FORECAST_VA_MAX_RANGE_DAYS}.",
            )

    # "Today" is a Pacific calendar day — the realized edge and forecast both
    # live on the PT clock (reuse fuel-mix's market timezone).
    today_iso = _datetime.now(ZoneInfo(MARKET_TZ)).date().isoformat()

    if use_date:
        lo_date = hi_date = date
    elif use_range:
        lo_date, hi_date = start, end
    else:
        today_pt = _date.fromisoformat(today_iso)
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
    include_ladder = quantity == "load"

    # Range mode: a thin extension of the single-date path — one window query,
    # then partition the rows into PT calendar days and shape each day exactly
    # like the ?date response. This is the synthesis read (per-day records keyed
    # by date) the weekly/monthly layer batch-reads with no retrofit. Empty days
    # (no forecast spine) are simply absent; a wholly empty range already 404'd.
    if use_range:
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(_pacific_day(r["target_ts"]), []).append(r)
        days: list[dict] = []
        for day_iso in sorted(by_day):
            day_points, day_seam = _shape_fva_points(by_day[day_iso], include_ladder)
            is_today = day_iso == today_iso
            days.append({
                "date": day_iso,
                "is_today": is_today,
                # Past day = fully realized => no now-line (seam null); today =>
                # the live realized edge. Keyed on is_today, not wall-clock.
                "seam": day_seam if is_today else None,
                "last_scored_ts": day_seam,
                "count": len(day_points),
                "points": day_points,
            })
        return {
            "quantity": quantity,
            "tz": MARKET_TZ,
            "window": {"start": lo_date, "end": hi_date},
            "products": products,
            "count": len(days),
            "days": days,
        }

    # Rows are ascending, so the last is_scored row is the seam (last realized).
    points, last_scored_ts = _shape_fva_points(rows, include_ladder)

    # ?date mode: same single-day series plus the explicit seam signal the
    # frontend keys its now-line render on (Phase 2 invariant).
    if use_date:
        is_today = date == today_iso
        return {
            "quantity": quantity,
            "tz": MARKET_TZ,
            "window": {"start": lo_date, "end": hi_date},
            "date": date,
            "is_today": is_today,
            "seam": last_scored_ts if is_today else None,
            "products": products,
            "last_scored_ts": last_scored_ts,
            "count": len(points),
            "points": points,
        }

    # Default mode — byte-for-byte identical to the shipped contract (no
    # date/is_today/seam fields; the existing chart must not move on today).
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


def _shape_brief_with_publication(r: dict) -> dict:
    """`_shape_brief` plus the additive `publication` rider (Market Clock).

    The rider hangs off a single new key so no existing field is touched; it
    reports whether this daily edition is current / pending / overdue against
    the publication clock. Pure shaping stays in `_shape_brief`.
    """
    shaped = _shape_brief(r)
    shaped["publication"] = _compute_publication_status(
        BRIEF_TYPE_DAILY,
        r["brief_date"],
        _utcnow(),
        published_at=r["created_at"],
    ).as_rider()
    return shaped


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

    return _shape_brief_with_publication(row)


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

    return _shape_brief_with_publication(row)


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

# Allowlist of chart-brief types this endpoint will serve. Deliberately a
# membership gate, NOT a fully-generic pass-through: it keeps non-chart
# brief_types (the editorial 'daily' Daily Brief, Wire, etc.) from being pulled
# through this chart-commentary route. Extend it only with chart-commentary
# types.
#
# - caiso_fuel_mix_chart      — the #99 fuel-mix chart brief (original member).
# - caiso_load_deviation      — Today load-deviation chart commentary.
# - caiso_renewables_deviation — Today renewables-deviation chart commentary.
# - caiso_week_ahead       — the 7-day week-ahead outlook chart brief. The
#                               canonical slug matches the pantry writer + the
#                               joule_briefs CHECK (the live row is stored under
#                               this slug); an earlier `_outlook` variant here
#                               drifted from write-side and served body=null.
# - caiso_hub_lmp             — per-hub commentary for the trading-hub LMP chart
#                               (/api/timeseries/caiso-hub-lmp). Unlike every
#                               other member, this type partitions its briefs by
#                               brief_key (one row per hub slug), so a `key`
#                               query param selects the hub — see CHART_BRIEF_KEYS.
CHART_BRIEF_TYPES = frozenset({
    "caiso_fuel_mix_chart",
    "caiso_load_deviation",
    "caiso_renewables_deviation",
    "caiso_week_ahead",
    "caiso_hub_lmp",
})

# Chart-brief types that partition their briefs by brief_key, mapped to the
# closed set of valid keys. A type listed here resolves on
# (brief_type, brief_key[, brief_date]); the `key` query param picks the row and
# is validated against this set (unknown key -> 404). Types NOT listed here
# ignore `key` entirely and resolve on (brief_type[, brief_date]) exactly as
# before — their SQL and params are byte-for-byte unchanged.
#
# Hub slugs mirror the OASIS trading-hub node prefixes (TH_SP15 = TH_SP15_GEN-
# APND); the caiso-hub-lmp brief writer stores one row per hub under these keys.
CHART_BRIEF_KEYS = {
    "caiso_hub_lmp": frozenset({"TH_NP15", "TH_SP15", "TH_ZP26"}),
}

# Default brief_key for a keyed brief_type when `?key` is omitted. A keyed type
# stores NO brief_key='' row — every partition is a real hub slug — so the older
# empty-string default matched zero rows and returned body=null on a bare
# ?brief_type=caiso_hub_lmp (the D-07-13-20 lookup bug). The bare route now
# resolves to the type's primary partition: TH_SP15 (SP15, the CAISO reference /
# flagship trading hub) for caiso_hub_lmp. An explicit ?key still selects any hub.
CHART_BRIEF_DEFAULT_KEY = {
    "caiso_hub_lmp": "TH_SP15",
}


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
    # The resolved brief_key (hub slug) this response was served for. Echoed so a
    # consumer can verify WHICH partition it got — for a keyed type this is the
    # explicit/defaulted hub (e.g. 'TH_NP15'); null for a non-keyed brief_type
    # (which has no partition). Without this echo the served hub was invisible.
    brief_key: Optional[str] = None
    voice_version: Optional[str] = None
    word_count: Optional[int] = None
    generated_at: Optional[str] = None
    id: Optional[int] = None
    # Additive publication rider (Market Clock seed): {status, trade_date,
    # published_at, next_expected_at}. Always present; unscheduled chart types
    # (the deviations, hub) report status='unscheduled' with next_expected_at
    # null. No existing field is touched.
    publication: Optional[dict] = None


# Latest row for a brief_type, newest first. NOT DISTINCT ON (see block above).
# When a specific brief_date is requested, the AND brief_date clause is spliced
# in before the ORDER BY (see _CHART_BRIEF_DATE_FILTER / _CHART_BRIEF_ORDER).
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
"""

# Optional narrowing to one brief_key (hub slug). Spliced in ONLY for keyed
# brief types (CHART_BRIEF_KEYS); absent-key defaults the bound param to the
# type's primary hub (CHART_BRIEF_DEFAULT_KEY) so a keyed type without ?key
# resolves to a real, populated partition (no cross-key latest-row bleed, and no
# empty-string partition that matches nothing). Non-keyed types never carry this
# clause, so their query is unchanged.
_CHART_BRIEF_KEY_FILTER = "      AND brief_key = %(brief_key)s\n"

# Optional narrowing to one calendar date. A later `snapshot` param (intraday
# arc) will hang off the same query for sub-day selection — do NOT build it now.
_CHART_BRIEF_DATE_FILTER = "      AND brief_date = %(brief_date)s\n"

# Newest-row tiebreak. Kept separate so it always lands after the optional date
# filter; if multiple rows match (brief_type[, brief_date]) we take the latest
# created_at — same rule as the no-date case.
_CHART_BRIEF_ORDER = """
    ORDER BY created_at DESC
    LIMIT 1
"""

# The complete set of query params this route accepts. Anything else is a 400,
# not a silent no-op: a silently-ignored selector is exactly how the D-07-13-20
# null bug hid — the empty-key default swallowed every request that meant to pick
# a hub. `brief_key` is the canonical selector name (matches the column); `key`
# is kept as a working alias.
_CHART_BRIEF_ALLOWED_PARAMS = frozenset(
    {"brief_type", "brief_date", "brief_key", "key"}
)


@app.get("/api/joule/chart-brief", response_model=ChartBrief)
async def joule_chart_brief(
    request: Request,
    brief_type: str = Query(
        ...,
        description="Chart-brief discriminator (a chart-commentary type in the "
        "CHART_BRIEF_TYPES allowlist, e.g. 'caiso_fuel_mix_chart', "
        "'caiso_load_deviation', 'caiso_renewables_deviation'). Unknown or "
        "non-chart types are rejected with 400.",
    ),
    brief_date: Optional[_date] = Query(
        None,
        description="Optional calendar date (YYYY-MM-DD) to fetch a specific "
        "day's brief. Omitted -> latest brief by created_at. Malformed dates "
        "are rejected with 422.",
    ),
    brief_key: Optional[str] = Query(
        None,
        description="Canonical selector for one partition of a keyed brief_type "
        "(currently only 'caiso_hub_lmp', keyed by hub slug: 'TH_NP15', "
        "'TH_SP15', 'TH_ZP26'). For keyed types it defaults to the primary hub "
        "('TH_SP15' for caiso_hub_lmp) when omitted; an unrecognized value is "
        "rejected with 404 (no empty-key fallback). Ignored for every non-keyed "
        "brief_type.",
    ),
    key: Optional[str] = Query(
        None,
        description="Deprecated alias for `brief_key` (kept working). Passing "
        "both is allowed only when they agree; a mismatch is a 400.",
    ),
):
    """
    Joule chart brief for a given brief_type, optionally pinned to a date.

    - brief_date omitted -> the single newest row (by created_at) WHERE
      brief_type matches (the #8/#99 behavior, unchanged).
    - brief_date provided -> the brief for (brief_type, brief_date); if several
      rows match, the latest created_at wins.
    - Unknown brief_type (not in the allowlist) -> 400.
    - Unknown query parameter (a name outside _CHART_BRIEF_ALLOWED_PARAMS, e.g. a
      misspelled selector) -> 400. A silently-ignored param is how D-07-13-20
      stayed invisible, so this route fails loud instead of swallowing it.
    - Malformed brief_date -> 422 (FastAPI validates the `date` type for free).
    - For a keyed brief_type (CHART_BRIEF_KEYS, e.g. caiso_hub_lmp) `brief_key`
      (or its alias `key`) selects one hub partition; an unrecognized value -> 404
      (NOT an empty-key fallback). Omitting it resolves to the type's primary hub
      (CHART_BRIEF_DEFAULT_KEY, TH_SP15 for caiso_hub_lmp) — never '', which
      matched no stored row and returned body=null. Passing both `brief_key` and
      `key` with different values -> 400. For non-keyed types the selector is
      ignored and the query is byte-identical to before.
    - The resolved brief_key is echoed in the response (null for non-keyed types),
      so a consumer can verify which hub it was served.
    - No brief for that brief_type[/brief_key][/brief_date] -> 200 with body=null
      (NOT 404), echoing brief_type/brief_key/brief_date, so the dashboard
      degrades quietly.

    Response shape (the contract the dashboard binds to):
        {
          "brief_type": "caiso_hub_lmp",
          "body": "...content_md...",
          "brief_date": "2026-07-13",
          "brief_key": "TH_NP15",
          "voice_version": "v1.5",
          "word_count": 186,
          "generated_at": "2026-07-13T17:32:47.950000+00:00",
          "id": 469
        }
    """
    assert _pool is not None

    # Fail loud on any param outside the allowlist (400), so a misspelled or
    # unsupported selector can never be silently ignored. Runs before the
    # brief_type membership check so an unknown param 400s regardless of type.
    unknown_params = set(request.query_params.keys()) - _CHART_BRIEF_ALLOWED_PARAMS
    if unknown_params:
        raise HTTPException(
            status_code=400,
            detail=f"unknown query parameter(s): {sorted(unknown_params)}. "
            f"Allowed: {sorted(_CHART_BRIEF_ALLOWED_PARAMS)}.",
        )

    # `brief_key` is canonical; `key` is a working alias. Fold them to one
    # selector: if both are given they must agree (else 400 — the request is
    # ambiguous about which hub it wants).
    if brief_key is not None and key is not None and brief_key != key:
        raise HTTPException(
            status_code=400,
            detail=f"conflicting selectors: brief_key='{brief_key}' != "
            f"key='{key}'. Pass one (brief_key is canonical) or matching values.",
        )
    selector = brief_key if brief_key is not None else key

    if brief_type not in CHART_BRIEF_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown brief_type '{brief_type}'. "
            f"Allowed: {sorted(CHART_BRIEF_TYPES)}.",
        )

    query = _CHART_BRIEF_SELECT
    params = {"brief_type": brief_type}

    # Keyed brief_type (only caiso_hub_lmp today): validate + resolve brief_key
    # from the folded `selector` (brief_key/key). A provided-but-unknown value is
    # a 404 — never a silent fall-through. Absent selector resolves to the type's
    # primary partition (CHART_BRIEF_DEFAULT_KEY), NOT '': every hub brief is
    # stored under a hub slug, so the empty-string default matched zero rows and
    # returned body=null (the D-07-13-20 bug). resolved_key is None for non-keyed
    # types, which skip this block, so their SQL/params and their brief_type-level
    # publication clock are byte-for-byte unchanged.
    resolved_key = None
    valid_keys = CHART_BRIEF_KEYS.get(brief_type)
    if valid_keys is not None:
        if selector is not None and selector not in valid_keys:
            raise HTTPException(
                status_code=404,
                detail=f"unknown brief_key '{selector}' for brief_type "
                f"'{brief_type}'. Valid keys: {sorted(valid_keys)}.",
            )
        resolved_key = (
            selector if selector is not None else CHART_BRIEF_DEFAULT_KEY[brief_type]
        )
        query += _CHART_BRIEF_KEY_FILTER
        params["brief_key"] = resolved_key

    if brief_date is not None:
        query += _CHART_BRIEF_DATE_FILTER
        params["brief_date"] = brief_date
    query += _CHART_BRIEF_ORDER

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    now = _utcnow()

    # Empty case: echo brief_type (and brief_date if one was requested),
    # everything else null. 200, not 404. The publication rider still reports
    # (an empty scheduled type reads pending/overdue; unscheduled reads so).
    if row is None:
        return ChartBrief(
            brief_type=brief_type,
            brief_date=brief_date.isoformat() if brief_date is not None else None,
            brief_key=resolved_key,
            publication=_compute_publication_status(
                brief_type, brief_date, now, published_at=None, brief_key=resolved_key
            ).as_rider(),
        )

    bd = row["brief_date"]
    ca = row["created_at"]
    return ChartBrief(
        brief_type=row["brief_type"],
        body=row["content_md"],
        brief_date=bd.isoformat() if bd is not None else None,
        brief_key=resolved_key,
        voice_version=row["voice_version"],
        word_count=row["word_count"],
        generated_at=ca.isoformat() if ca is not None else None,
        id=row["id"],
        publication=_compute_publication_status(
            brief_type, bd, now, published_at=ca, brief_key=resolved_key
        ).as_rider(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# /api/weather/brief/{latest,history} — Kelvin, the Weather Desk (2026-07-20)
#
# The first department agent's daily brief. Written by joule_weather_post.py in
# the pantry (brief_type='weather', single-keyed brief_key=''), stored in
# joule_briefs. Read-only here, and — because the writer UPSERTs a row ONLY when
# the per-sentence verifier PASSES — the mere EXISTENCE of a stored weather row
# is itself the verifier stamp: verified=true is an invariant of the table, not
# a field we have to trust.
#
# Shape mirrors the hub-brief (chart-brief) contract, enriched with the three
# things a weather brief carries that a price brief does not, all sourced from
# columns already on the row (no new pantry behavior, no joule_calls join):
#   * byline    — the ratified constant "By Kelvin · Weather Desk (agent)".
#                 "(agent)" is non-negotiable everywhere the name renders.
#   * dateline  — {date, tz, byline, verified} — what the desk knew, when.
#   * gates + degraded/empty beats — from sources_used (jsonb the writer stamps
#                 with the bundle's build-time freshness verdicts), so the tab's
#                 dateline block can PROVE which inputs were fresh.
#
# Two routes, in the daily-brief path style (dedicated latest + a history list),
# NOT the chart-brief query-param style — a weather section opens with ONE
# living brief and a rolling verification strip, which is exactly these two.
# ═══════════════════════════════════════════════════════════════════════════

BRIEF_TYPE_WEATHER = "weather"

# The ratified byline constant (2026-07-20). Kept here as a constant — not read
# from the row — because it is a ratified invariant of the surface, not
# per-brief data. Matches joule._WEATHER_BYLINE in the pantry; if that ever
# changes, this changes with it (a deliberate two-line coupling, not drift).
WEATHER_BYLINE = "By Kelvin · Weather Desk (agent)"

# Rolling history cap. The Verification tile shows a short trailing window of
# briefs; 14 covers two weeks, 60 is a hard ceiling so a crafted ?limit can't
# ask for the whole table.
_WEATHER_HISTORY_DEFAULT = 14
_WEATHER_HISTORY_MAX = 60

# Full weather row. Selects sources_used (the bundle's build-time gate receipts
# + degraded/empty beat lists) on top of the daily-brief column set.
_WEATHER_BRIEF_SELECT = """
    SELECT
        id,
        brief_date,
        headline,
        content_md,
        word_count,
        voice_version,
        sources_used,
        created_at
    FROM joule_briefs
    WHERE brief_type = %(brief_type)s
"""
_WEATHER_BRIEF_DATE_FILTER = "      AND brief_date = %(brief_date)s\n"
_WEATHER_BRIEF_ORDER_ONE = """
    ORDER BY created_at DESC
    LIMIT 1
"""
_WEATHER_BRIEF_ORDER_MANY = """
    ORDER BY brief_date DESC, created_at DESC
    LIMIT %(limit)s
"""


class WeatherDateline(BaseModel):
    """What the desk knew, when — the block a weather section leads with."""
    date: Optional[str] = None
    tz: str = "America/Los_Angeles"
    byline: str = WEATHER_BYLINE
    # Invariant: a stored weather brief passed its verifier (the writer upserts
    # only on PASS). True whenever a row was served; the empty case leaves the
    # whole dateline null.
    verified: bool = True
    voice_version: Optional[str] = None


class WeatherBrief(BaseModel):
    """Render contract for Kelvin's weather brief. The tab binds to this shape.

    brief_type is always present. Every other field is Optional because the
    empty case (no brief written yet) returns this model with body/dateline/...
    = null rather than a 404 — the section degrades quietly, same as the hub
    brief.
    """
    brief_type: str
    byline: Optional[str] = None
    headline: Optional[str] = None
    body: Optional[str] = None
    brief_date: Optional[str] = None
    word_count: Optional[int] = None
    voice_version: Optional[str] = None
    generated_at: Optional[str] = None
    id: Optional[int] = None
    dateline: Optional[WeatherDateline] = None
    # Build-time freshness receipts from the bundle (sources_used.gates): per
    # dataset {status, lifecycle_status, days_behind}. {} when the row predates
    # the receipt-stamping writer.
    gates: Optional[dict] = None
    degraded_beats: Optional[list] = None
    empty_beats: Optional[list] = None
    bundle_version: Optional[str] = None
    # Additive publication rider (Market Clock), same as the hub brief. Weather
    # is not yet on the publication SCHEDULE (dispatch-only v0), so this reports
    # status='unscheduled' until the cron is promoted — an honest reflection of
    # the surface's current state.
    publication: Optional[dict] = None


class WeatherBriefHistoryItem(BaseModel):
    """One row in the rolling verification strip — summary, not the full body."""
    brief_date: Optional[str] = None
    headline: Optional[str] = None
    word_count: Optional[int] = None
    voice_version: Optional[str] = None
    generated_at: Optional[str] = None
    id: Optional[int] = None
    verified: bool = True
    degraded_beats: Optional[list] = None
    empty_beats: Optional[list] = None


class WeatherBriefHistory(BaseModel):
    brief_type: str
    count: int
    briefs: list[WeatherBriefHistoryItem]


def _weather_sources(row: dict) -> dict:
    """sources_used as a dict, tolerating NULL / non-dict legacy rows."""
    su = row.get("sources_used")
    return su if isinstance(su, dict) else {}


def _shape_weather_brief(row: dict, now: _datetime) -> WeatherBrief:
    bd = row["brief_date"]
    ca = row["created_at"]
    su = _weather_sources(row)
    date_iso = bd.isoformat() if bd is not None else None
    return WeatherBrief(
        brief_type=BRIEF_TYPE_WEATHER,
        byline=WEATHER_BYLINE,
        headline=row.get("headline"),
        body=row.get("content_md"),
        brief_date=date_iso,
        word_count=row.get("word_count"),
        voice_version=row.get("voice_version"),
        generated_at=ca.isoformat() if ca is not None else None,
        id=row.get("id"),
        dateline=WeatherDateline(
            date=date_iso,
            verified=True,
            voice_version=row.get("voice_version"),
        ),
        gates=su.get("gates", {}),
        degraded_beats=su.get("degraded_beats", []),
        empty_beats=su.get("empty_beats", []),
        bundle_version=su.get("bundle_version"),
        publication=_compute_publication_status(
            BRIEF_TYPE_WEATHER, bd, now, published_at=ca,
        ).as_rider(),
    )


@app.get("/api/weather/brief/latest", response_model=WeatherBrief)
async def weather_brief_latest(
    brief_date: Optional[_date] = Query(
        None, description="Pin a specific Pacific brief date (YYYY-MM-DD). "
                          "Omit for the newest brief."),
):
    """The living weather brief — newest by default, or a pinned brief_date.

    Empty case (no brief for the day / none yet) returns 200 with null fields
    and an unscheduled publication rider, never 404 — the section degrades
    quietly exactly like the hub brief.
    """
    assert _pool is not None
    query = _WEATHER_BRIEF_SELECT
    params: dict = {"brief_type": BRIEF_TYPE_WEATHER}
    if brief_date is not None:
        query += _WEATHER_BRIEF_DATE_FILTER
        params["brief_date"] = brief_date
    query += _WEATHER_BRIEF_ORDER_ONE

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    now = _utcnow()
    if row is None:
        return WeatherBrief(
            brief_type=BRIEF_TYPE_WEATHER,
            brief_date=brief_date.isoformat() if brief_date is not None else None,
            publication=_compute_publication_status(
                BRIEF_TYPE_WEATHER, brief_date, now, published_at=None,
            ).as_rider(),
        )
    return _shape_weather_brief(row, now)


@app.get("/api/weather/brief/history", response_model=WeatherBriefHistory)
async def weather_brief_history(
    limit: int = Query(
        _WEATHER_HISTORY_DEFAULT, ge=1, le=_WEATHER_HISTORY_MAX,
        description="How many recent briefs to return (newest first). "
                    f"Default {_WEATHER_HISTORY_DEFAULT}, max {_WEATHER_HISTORY_MAX}."),
):
    """The rolling verification strip — a trailing window of briefs, summary
    only (no body). Feeds the tab's Verification tile. Every returned brief
    verified=true (a stored weather row is a passed row); the list is simply
    empty when no brief exists yet."""
    assert _pool is not None
    query = _WEATHER_BRIEF_SELECT + _WEATHER_BRIEF_ORDER_MANY
    params = {"brief_type": BRIEF_TYPE_WEATHER, "limit": limit}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    items: list[WeatherBriefHistoryItem] = []
    for row in rows or []:
        bd = row["brief_date"]
        ca = row["created_at"]
        su = _weather_sources(row)
        items.append(WeatherBriefHistoryItem(
            brief_date=bd.isoformat() if bd is not None else None,
            headline=row.get("headline"),
            word_count=row.get("word_count"),
            voice_version=row.get("voice_version"),
            generated_at=ca.isoformat() if ca is not None else None,
            id=row.get("id"),
            verified=True,
            degraded_beats=su.get("degraded_beats", []),
            empty_beats=su.get("empty_beats", []),
        ))
    return WeatherBriefHistory(
        brief_type=BRIEF_TYPE_WEATHER, count=len(items), briefs=items,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Kelvin Tier-1 weather tiles — three read-only grids (D-07-20-02, 2026-07-21)
#
# Backs the /weather RESERVED tiles: a D1–D7 temp matrix, a driver/regime
# panel, and a station-walk. Pure reads; no migrations, no crons.
#
# The 17-station WECC basket's N→S order and per-station IANA timezone come
# from station_metadata.json (vendored beside this module from the pantry's
# ruled source — the basket carries no coordinates/tz by design). Deriving a
# tz-aware daily hi/lo from the hourly NWS forecast is the whole build, so the
# tz map is authoritative, never improvised (spec P3 STOP condition).
#
# Data receipts verified live 2026-07-21 (architect + Neon connector):
#   * forecasts_nws is HOURLY, long-format, series '{station_id}_temperature'.
#     GOTCHA: '{station_id}_dew_point_temperature' also ends in '_temperature',
#     so we match the EXACT series string per station, never a LIKE — a naive
#     '%_temperature' would double the basket with dew points.
#   * Stored `value` is CELSIUS even though meta.unit_src='F' (that describes
#     the SOURCE; LAX 21.11 ≈ 70°F confirms). We convert C→°F here.
#   * station_normals_daily is keyed (station_id, month, day); window_label
#     ('2011-2025') is carried in the envelope so the dashboard never hardcodes
#     a "15-yr" label.
# ═══════════════════════════════════════════════════════════════════════════

# The ruled N→S basket (order + IANA tz), loaded once at import. Vendored from
# the pantry so the deployed API has it (Railway ships the repo). Read relative
# to this file, not the CWD.
_STATION_METADATA_PATH = _Path(__file__).with_name("station_metadata.json")


def _load_weather_stations() -> list[dict]:
    """Load the 17-station basket, N→S. Fails loud (spec P3) if the ruled tz is
    absent — an improvised tz map is exactly what this reference exists to avoid.
    """
    with open(_STATION_METADATA_PATH, encoding="utf-8") as fh:
        doc = json.load(fh)
    stations = doc.get("stations") or []
    for s in stations:
        if not s.get("tz") or not s.get("station_id"):
            raise RuntimeError(
                "station_metadata.json is missing tz/station_id for "
                f"{s.get('station_id') or s!r} — refusing to improvise a tz map "
                "(spec P3 STOP)."
            )
    return stations


_WEATHER_STATIONS: list[dict] = _load_weather_stations()

# Temp-matrix knobs.
_TEMP_MATRIX_DAYS = 7                       # D1–D7
_TEMP_MATRIX_PARTIAL_HOURS = 18             # < 18 hourly points → partial cell
_TEMP_MATRIX_DEGRADED_AFTER = _timedelta(hours=24)  # no issuance in 24h → degraded


def _c_to_f(celsius) -> float:
    """°C → °F. Values in forecasts_nws / ghcnd are Celsius (see receipts)."""
    return float(celsius) * 9.0 / 5.0 + 32.0


def _display_f(celsius) -> int:
    """Integer °F for grid display (anomalies keep a decimal; the cell does not)."""
    return int(round(_c_to_f(celsius)))


def _oni_band(value: float | None) -> str | None:
    """Deterministic ENSO band for ONI only — warm ≥ +0.5 / cool ≤ −0.5 /
    neutral. Nothing editorial (spec: the chip states the number and its band,
    no narrative)."""
    if value is None:
        return None
    if value >= 0.5:
        return "warm"
    if value <= -0.5:
        return "cool"
    return "neutral"


@app.get("/api/weather/temp-matrix")
async def weather_temp_matrix():
    """Per station (N→S), the D1–D7 forecast hi/lo grid with normal anomalies.

    Per station we use ONLY the latest issuance of its '{station_id}_temperature'
    series, bucket each hourly target into the station's LOCAL calendar date via
    its IANA tz (D1 = that station's local date at request time), and take
    max/min per day. Each cell carries `hours_covered` and `partial` (< 18h) so
    D1's usual afternoon partial is surfaced, never hidden. A station with no
    issuance in the last 24h is DEGRADED — null cells, its last issued_ts, still
    a row. Always 17 rows. DB unavailable → 503.
    """
    assert _pool is not None

    now = _utcnow()
    stations = _WEATHER_STATIONS
    series_by_station = {s["station_id"]: f"{s['station_id']}_temperature"
                         for s in stations}
    series_list = list(series_by_station.values())

    # Per station, the seven LOCAL dates D1–D7 (D1 = today in that station's tz).
    days_by_station: dict[str, list[_date]] = {}
    for s in stations:
        local_today = now.astimezone(ZoneInfo(s["tz"])).date()
        days_by_station[s["station_id"]] = [
            local_today + _timedelta(days=i) for i in range(_TEMP_MATRIX_DAYS)
        ]
    needed_months = sorted({d.month for days in days_by_station.values()
                            for d in days})

    # (1) latest-issuance hourly rows for every station's temperature series;
    # (2) normals for the (month, day) window (fetched by month, filtered in
    #     Python — trivially small, and avoids composite-tuple IN adaptation).
    forecast_sql = """
        WITH latest AS (
            SELECT series, MAX(issued_ts) AS issued_ts
            FROM forecasts_nws
            WHERE series = ANY(%(series)s)
            GROUP BY series
        )
        SELECT f.series, f.issued_ts, f.target_ts, f.value
        FROM forecasts_nws f
        JOIN latest l ON l.series = f.series AND l.issued_ts = f.issued_ts
        WHERE f.value IS NOT NULL
    """
    normals_sql = """
        SELECT station_id, month, day, tmax_norm_f, tmin_norm_f, window_label
        FROM station_normals_daily
        WHERE station_id = ANY(%(sids)s) AND month = ANY(%(months)s)
    """
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(forecast_sql, {"series": series_list})
                fc_rows = await cur.fetchall()
                await cur.execute(normals_sql, {
                    "sids": list(series_by_station.keys()),
                    "months": needed_months,
                })
                norm_rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # Group forecast rows by series → issued_ts + list of (target_ts, value_c).
    fc_by_series: dict[str, dict] = defaultdict(
        lambda: {"issued_ts": None, "rows": []})
    for r in fc_rows or []:
        e = fc_by_series[r["series"]]
        e["issued_ts"] = r["issued_ts"]
        e["rows"].append((r["target_ts"], r["value"]))

    # Normals lookup keyed (station_id, month, day); window_label for the envelope.
    norm_by_key: dict[tuple, dict] = {}
    window_label: str | None = None
    for r in norm_rows or []:
        norm_by_key[(r["station_id"], r["month"], r["day"])] = r
        if window_label is None:
            window_label = r.get("window_label")

    out_stations = []
    for order, s in enumerate(stations, start=1):
        sid = s["station_id"]
        tz = ZoneInfo(s["tz"])
        series = series_by_station[sid]
        entry = fc_by_series.get(series)
        issued_ts = entry["issued_ts"] if entry else None
        degraded = (
            issued_ts is None
            or (now - issued_ts) > _TEMP_MATRIX_DEGRADED_AFTER
        )

        # Bucket this issuance's hourly points into local dates (skip when
        # degraded — a stale issuance's cells are nulled, not shown).
        buckets: dict[_date, list] = defaultdict(list)
        if not degraded and entry:
            for target_ts, value_c in entry["rows"]:
                local_d = target_ts.astimezone(tz).date()
                buckets[local_d].append(value_c)

        days = []
        for d in days_by_station[sid]:
            vals = buckets.get(d, [])
            if vals:
                hi_f = _display_f(max(vals))
                lo_f = _display_f(min(vals))
                norm = norm_by_key.get((sid, d.month, d.day))
                anom_hi = (round(hi_f - float(norm["tmax_norm_f"]), 1)
                           if norm and norm.get("tmax_norm_f") is not None else None)
                anom_lo = (round(lo_f - float(norm["tmin_norm_f"]), 1)
                           if norm and norm.get("tmin_norm_f") is not None else None)
                hours = len(vals)
                days.append({
                    "date_local": d.isoformat(),
                    "hi_f": hi_f,
                    "lo_f": lo_f,
                    "anom_hi": anom_hi,
                    "anom_lo": anom_lo,
                    "hours_covered": hours,
                    "partial": hours < _TEMP_MATRIX_PARTIAL_HOURS,
                })
            else:
                # No hourly coverage for this local day (degraded station, or a
                # day past the issuance's horizon) — cell present, values null.
                days.append({
                    "date_local": d.isoformat(),
                    "hi_f": None,
                    "lo_f": None,
                    "anom_hi": None,
                    "anom_lo": None,
                    "hours_covered": 0,
                    "partial": True,
                })

        out_stations.append({
            "station_id": sid,
            "name": s.get("name"),
            "metro": s.get("metro"),
            # D-07-22-03 (additive): the display label + USPS state the row carries
            # for the frontend, sourced verbatim from station_metadata.json. Purely
            # additive to the ratified envelope — no existing key renamed or
            # reordered (metro/name/icao/order/tz/... all unchanged).
            "display_name": s.get("display_name"),
            "state": s.get("state"),
            "icao": s.get("icao"),
            "order": order,
            "tz": s["tz"],
            "issued_ts": issued_ts.isoformat() if issued_ts is not None else None,
            "degraded": degraded,
            "days": days,
        })

    return {
        "as_of": now.isoformat(),
        "window_label": window_label,
        "stations": out_stations,
    }


# Driver chips: (display key, timeseries dataset). One series per dataset.
_REGIME_DRIVERS = [
    ("oni", "cpc_oni_monthly"),
    ("roni", "cpc_roni_monthly"),
    ("pdo", "climate_pdo_monthly"),
    ("qbo", "climate_qbo_monthly"),
    ("iod_dmi", "climate_iod_dmi_monthly"),
]

# CPC outlook families → the cpc_outlook_vintage.product code for each.
_REGIME_CPC_FAMILIES = [
    ("6-10 temp", "610temp"),
    ("6-10 precip", "610prcp"),
    ("8-14 temp", "814temp"),
    ("8-14 precip", "814prcp"),
]


@app.get("/api/weather/regime")
async def weather_regime():
    """The driver/regime panel: five teleconnection chips + the CPC outlook
    vintages.

    Driver chips (oni, roni, pdo, qbo, iod_dmi) report the latest monthly value,
    its `as_of` month, and `staleness_days` vs today — NEVER dropped, staleness
    is displayed not filtered. ONLY ONI carries a deterministic warm/cool/neutral
    band; nothing editorial. CPC chips report the latest vintage per family as
    METADATA (issued/valid window, format) with `lean: null` — parsed
    probabilities are the deferred D2 render leg (spec P5), never faked from R2.
    DB unavailable → 503.
    """
    assert _pool is not None

    now = _utcnow()
    today = now.astimezone(ZoneInfo(MARKET_TZ)).date()

    drivers_sql = """
        SELECT DISTINCT ON (dataset) dataset, series, ts, value
        FROM timeseries_values
        WHERE dataset = ANY(%(datasets)s)
        ORDER BY dataset, ts DESC
    """
    cpc_sql = """
        SELECT DISTINCT ON (product)
               product, issued_date, valid_start, valid_end, artifact_format
        FROM cpc_outlook_vintage
        WHERE product = ANY(%(products)s)
        ORDER BY product, issued_date DESC
    """
    cpc_depth_sql = "SELECT MIN(issued_date) AS depth FROM cpc_outlook_vintage"
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(drivers_sql, {
                    "datasets": [ds for _, ds in _REGIME_DRIVERS]})
                driver_rows = await cur.fetchall()
                await cur.execute(cpc_sql, {
                    "products": [p for _, p in _REGIME_CPC_FAMILIES]})
                cpc_rows = await cur.fetchall()
                await cur.execute(cpc_depth_sql)
                depth_row = await cur.fetchone()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    by_dataset = {r["dataset"]: r for r in (driver_rows or [])}
    drivers = []
    for key, dataset in _REGIME_DRIVERS:
        r = by_dataset.get(dataset)
        if r is None:
            drivers.append({
                "key": key, "value": None, "as_of": None,
                "staleness_days": None, "band": None,
            })
            continue
        value = float(r["value"]) if r["value"] is not None else None
        # Monthly drivers are stamped midnight-UTC on the 1st, so the "value
        # month" is the UTC date — converting to Pacific would push a May stamp
        # back into April and mislabel the month.
        as_of = r["ts"].astimezone(_timezone.utc).date()
        drivers.append({
            "key": key,
            "value": value,
            "as_of": as_of.isoformat(),
            "staleness_days": (today - as_of).days,
            "band": _oni_band(value) if key == "oni" else None,
        })

    by_product = {r["product"]: r for r in (cpc_rows or [])}
    cpc_chips = []
    for family, product in _REGIME_CPC_FAMILIES:
        r = by_product.get(product)
        cpc_chips.append({
            "family": family,
            "product": product,
            "issued_date": r["issued_date"].isoformat() if r else None,
            "valid_start": r["valid_start"].isoformat() if r else None,
            "valid_end": r["valid_end"].isoformat() if r else None,
            "artifact_format": r["artifact_format"] if r else None,
            "lean": None,
            "lean_status": "pending render leg",
        })

    depth = depth_row.get("depth") if depth_row else None
    return {
        "as_of": now.isoformat(),
        "drivers": drivers,
        "cpc": {
            "depth": depth.isoformat() if depth is not None else None,
            "chips": cpc_chips,
        },
    }


@app.get("/api/weather/station-walk")
async def weather_station_walk():
    """Per station (N→S), the latest daily observation vs normal.

    Each station reports ITS OWN latest obs_date (as-of grammar, not omission —
    a station missing at the shared latest date still appears at its own latest),
    tmax/tmin (°C→°F), hdd/cdd, basis_complete, the (month, day) normals
    (hdd_norm/cdd_norm/tavg_norm_f), obs−norm anomalies, and days_behind vs
    today. Always 17 rows; window_label carried in the envelope. DB
    unavailable → 503.
    """
    assert _pool is not None

    now = _utcnow()
    today = now.astimezone(ZoneInfo(MARKET_TZ)).date()
    sids = [s["station_id"] for s in _WEATHER_STATIONS]

    # Per station: its latest degree-day row (carries hdd/cdd/tavg_f/basis),
    # the same-date ghcnd obs (tmax/tmin), and that date's normals.
    walk_sql = """
        WITH latest_dd AS (
            SELECT DISTINCT ON (station_id)
                   station_id, obs_date, tavg_f, hdd, cdd, basis_complete
            FROM station_degree_days_daily
            WHERE station_id = ANY(%(sids)s)
            ORDER BY station_id, obs_date DESC
        )
        SELECT dd.station_id, dd.obs_date, dd.tavg_f, dd.hdd, dd.cdd,
               dd.basis_complete,
               g.tmax_c, g.tmin_c,
               n.hdd_norm, n.cdd_norm, n.tavg_norm_f, n.sample_count,
               n.window_label
        FROM latest_dd dd
        LEFT JOIN ghcnd_weather_daily g
               ON g.station_id = dd.station_id AND g.obs_date = dd.obs_date
        LEFT JOIN station_normals_daily n
               ON n.station_id = dd.station_id
              AND n.month = EXTRACT(MONTH FROM dd.obs_date)::int
              AND n.day = EXTRACT(DAY FROM dd.obs_date)::int
    """
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(walk_sql, {"sids": sids})
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    by_station = {r["station_id"]: r for r in (rows or [])}
    window_label = next(
        (r.get("window_label") for r in (rows or []) if r.get("window_label")),
        None,
    )

    def _anom(obs, norm):
        if obs is None or norm is None:
            return None
        return round(float(obs) - float(norm), 1)

    out_stations = []
    for order, s in enumerate(_WEATHER_STATIONS, start=1):
        sid = s["station_id"]
        r = by_station.get(sid)
        if r is None:
            # No degree-day row at all — still a row, as-of null (never omitted).
            out_stations.append({
                "station_id": sid, "name": s.get("name"), "metro": s.get("metro"),
                "icao": s.get("icao"), "order": order, "tz": s["tz"],
                "obs_date": None, "days_behind": None,
                "tmax_f": None, "tmin_f": None, "tavg_f": None,
                "hdd": None, "cdd": None, "basis_complete": None,
                "hdd_norm": None, "cdd_norm": None, "tavg_norm_f": None,
                "anom_tavg": None, "anom_hdd": None, "anom_cdd": None,
                "sample_count": None,
            })
            continue
        obs_date = r["obs_date"]
        tavg_f = r.get("tavg_f")
        hdd = r.get("hdd")
        cdd = r.get("cdd")
        out_stations.append({
            "station_id": sid,
            "name": s.get("name"),
            "metro": s.get("metro"),
            "icao": s.get("icao"),
            "order": order,
            "tz": s["tz"],
            "obs_date": obs_date.isoformat() if obs_date is not None else None,
            "days_behind": (today - obs_date).days if obs_date is not None else None,
            "tmax_f": _display_f(r["tmax_c"]) if r.get("tmax_c") is not None else None,
            "tmin_f": _display_f(r["tmin_c"]) if r.get("tmin_c") is not None else None,
            "tavg_f": tavg_f,
            "hdd": hdd,
            "cdd": cdd,
            "basis_complete": r.get("basis_complete"),
            "hdd_norm": float(r["hdd_norm"]) if r.get("hdd_norm") is not None else None,
            "cdd_norm": float(r["cdd_norm"]) if r.get("cdd_norm") is not None else None,
            "tavg_norm_f": float(r["tavg_norm_f"]) if r.get("tavg_norm_f") is not None else None,
            "anom_tavg": _anom(tavg_f, r.get("tavg_norm_f")),
            "anom_hdd": _anom(hdd, r.get("hdd_norm")),
            "anom_cdd": _anom(cdd, r.get("cdd_norm")),
            "sample_count": r.get("sample_count"),
        })

    return {
        "as_of": now.isoformat(),
        "window_label": window_label,
        "stations": out_stations,
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/weather/delta-board — Weather Phase 2, the weather tab's front door
# (2026-07-26)
#
# Regions × next 7 days, each cell colored by how the load-weighted-temperature
# forecast CHANGED run-over-run — the first Pivotal question ("what changed")
# before any absolute number. Read-time compute over forecasts_nws region
# vintages (series = region id, meta.agg_version = 'asof_v1'); no new tables, no
# writes, no pantry changes.
#
# THE RULED MODEL (spec "Ruled decisions", do not redesign):
#   1. Synoptic bucketing — floor each region event's issued_ts to a 6-hour
#      synoptic window (00/06/12/18Z); the latest event per (region, bucket)
#      wins. Delta = current bucket vs the most recent PRIOR populated bucket for
#      THAT region (buckets are sparse per region — walk back, don't assume
#      adjacency; cap the walk-back at 4 buckets / 24h, else the cell is no-data).
#   2. Cell metric — Δ daily-max LWT °F. Per target day, max over that day's
#      target hours in the current composition minus the same in the prior,
#      compared ONLY over hours present in BOTH buckets. If the shared overlap is
#      too thin to form an honest daily max (< 12 shared hours on a full day; the
#      leading partial day — the forecast's first, issuance-day date — is exempt),
#      the cell is no-data (null) rather than a fake delta.
#   3. Day boundary — region-local, mirrored from the pantry's ruled region→tz
#      table (see _LWT_REGIONS below; that mapping lives only in the pantry, so
#      per spec P3 it is replicated here, never improvised).
#   5. Streak — per region × target day, the number of consecutive
#      bucket-over-bucket deltas with the SAME sign ending at the current bucket
#      ("N runs warmer/cooler in a row"), over the available vintage record,
#      display-capped at 5+.
#
# RE-ISSUE ZEROS (a data reality this endpoint handles deliberately, verified
# live 2026-07-26): NWS re-issues a gridpoint on its own updateTime WITHOUT
# changing the hourly values, so a later synoptic bucket is frequently a
# byte-identical re-composition of the earlier one → an honest Δ0 cell (NOT
# no-data; the two runs genuinely agree). For the CELL this is reported as 0. For
# the STREAK a Δ0 is NEUTRAL — it carries no new forecast, so it neither counts
# as a run nor breaks the trend; the streak reflects the last genuine directional
# move. Treating re-issues as trend-breakers would pin every streak at ≤1 given
# how often the 12Z bucket merely re-stamps 06Z, defeating the "keeps trending
# one way" signal the streak exists to surface.
# ═══════════════════════════════════════════════════════════════════════════

# The 17 LWT regions in the yaml's declaration order (CAISO TACs first, then WECC
# BAs) with each region's DOMINANT day-boundary timezone. REPLICATED verbatim
# from the pantry's ruled source — energylake-pantry
# weights/region_station_weights.yaml (the `tz:` per region), consumed by
# ingesters/lwt.py. That yaml is the single authority for region day boundaries
# and is not exposed to this service, so per the delta-board spec (ruled decision
# #3 / STOP-don't-improvise) it is mirrored here. Keep in sync if the pantry adds
# or re-tzs a region.
_LWT_REGIONS: list[tuple[str, str]] = [
    # CAISO Transmission Access Charge (TAC) areas
    ("PGE-TAC",  "America/Los_Angeles"),
    ("SCE-TAC",  "America/Los_Angeles"),
    ("SDGE-TAC", "America/Los_Angeles"),
    ("VEA-TAC",  "America/Los_Angeles"),
    # WECC balancing authorities (CAISO 7DA feed order)
    ("BPAT",     "America/Los_Angeles"),
    ("AZPS",     "America/Phoenix"),
    ("SRP",      "America/Phoenix"),
    ("NEVP",     "America/Los_Angeles"),
    ("PACE",     "America/Denver"),
    ("PACW",     "America/Los_Angeles"),
    ("PSEI",     "America/Los_Angeles"),
    ("LADWP",    "America/Los_Angeles"),
    ("BANC",     "America/Los_Angeles"),
    ("IPCO",     "America/Boise"),
    ("PNM",      "America/Denver"),
    ("TEPC",     "America/Phoenix"),
    ("EPE",      "America/Denver"),
]

_DELTA_BOARD_DAYS = 7                 # columns: D1 (region-local today) … D7
_DELTA_SYNOPTIC_HOURS = 6             # 00/06/12/18Z synoptic bucketing
_DELTA_WALKBACK_BUCKETS = 4           # cap prior-bucket walk-back at 4 buckets/24h
_DELTA_MIN_SHARED_HOURS = 12          # a full-day daily-max needs ≥12 shared hours
_DELTA_STREAK_CAP = 5                 # display cap — a streak ≥ 5 renders "5+"
_DELTA_LOOKBACK_DAYS = 8              # compute window; bounds the streak history
_DELTA_AGG_VERSION = "asof_v1"        # meta.agg_version marking composed region rows
# The maximum gap a streak / prior walk-back will bridge between two populated
# buckets (4 synoptic slots = 24h).
_DELTA_MAX_GAP = _timedelta(hours=_DELTA_SYNOPTIC_HOURS * _DELTA_WALKBACK_BUCKETS)


def _synoptic_bucket(ts: _datetime) -> _datetime:
    """Floor a tz-aware instant to its 6-hour synoptic window in UTC (00/06/12/18Z)."""
    u = ts.astimezone(_timezone.utc)
    floored_hour = (u.hour // _DELTA_SYNOPTIC_HOURS) * _DELTA_SYNOPTIC_HOURS
    return u.replace(hour=floored_hour, minute=0, second=0, microsecond=0)


def _delta_pair(
    newer: dict, older: dict, tz: ZoneInfo, target_date: _date, leading_exempt: bool
):
    """Δ daily-max °F for `target_date`, comparing ONLY target hours present in
    BOTH compositions (the honest run-over-run daily max).

    `newer`/`older` map target_ts → value (°F). Returns
    (delta_f, current_max_f, prior_max_f, shared_hours) or None when there is no
    shared coverage, or the shared overlap is below the full-day minimum and the
    date is not the leading (partial, issuance-day) column.
    """
    shared: list[tuple[float, float]] = []
    for ts, cur_v in newer.items():
        old_v = older.get(ts)
        if old_v is None:
            continue
        if ts.astimezone(tz).date() == target_date:
            shared.append((cur_v, old_v))
    if not shared:
        return None
    if not leading_exempt and len(shared) < _DELTA_MIN_SHARED_HOURS:
        return None
    cur_max = max(c for c, _ in shared)
    pri_max = max(p for _, p in shared)
    return (round(cur_max - pri_max, 2), round(cur_max, 2), round(pri_max, 2), len(shared))


def _delta_streak(buckets_asc: list, tz: ZoneInfo, target_date: _date) -> tuple[int, int]:
    """Trailing same-sign run of run-over-run deltas for `target_date`, ending at
    the current bucket. Δ0 (a re-issue that changed nothing) is neutral: it is
    skipped, neither counting nor breaking the run. A sign flip, a bucket gap
    > 24h, or a pair with no honest daily max ends the run.

    `buckets_asc` is ascending [(bucket_ts, issued_ts, comp, earliest_date), …].
    Returns (count, direction) where direction is +1 warmer / −1 cooler / 0 none.
    Count is uncapped here; the route applies the 5+ display cap.
    """
    run_sign = 0
    count = 0
    for i in range(len(buckets_asc) - 1, 0, -1):
        newer = buckets_asc[i]
        older = buckets_asc[i - 1]
        if newer[0] - older[0] > _DELTA_MAX_GAP:
            break  # a > 24h gap between populated buckets ends the record we trust
        leading = target_date == newer[3]
        res = _delta_pair(newer[2], older[2], tz, target_date, leading)
        if res is None:
            break  # can't determine this run's direction → the chain ends here
        delta = res[0]
        if delta == 0:
            continue  # re-issue / no change: neutral (see RE-ISSUE ZEROS above)
        sign = 1 if delta > 0 else -1
        if run_sign == 0:
            run_sign = sign
            count = 1
        elif sign == run_sign:
            count += 1
        else:
            break
    return count, run_sign


def _compute_delta_board(rows: list[dict], now: _datetime) -> dict:
    """Pure composition of the delta-board payload from raw forecasts_nws region
    rows (no DB, no clock — `now` is injected). See the section header for the
    ruled model. Always emits all 17 regions in yaml order; regions with sparse
    history degrade to no-data cells, never an error."""
    # region → issued_ts → {"targets": {target_ts: value}, "cov": min, "age": max}
    by_region: dict[str, dict[_datetime, dict]] = defaultdict(dict)
    for r in rows:
        region = r["series"]
        iss = r["issued_ts"]
        val = r["value"]
        if val is None:
            continue
        comp = by_region[region].get(iss)
        if comp is None:
            comp = {"targets": {}, "cov": None, "age": None}
            by_region[region][iss] = comp
        comp["targets"][r["target_ts"]] = float(val)
        cov = r.get("coverage")
        if cov is not None:
            comp["cov"] = cov if comp["cov"] is None else min(comp["cov"], cov)
        age = r.get("max_age_hours")
        if age is not None:
            comp["age"] = age if comp["age"] is None else max(comp["age"], age)

    out_regions = []
    global_current: _datetime | None = None

    for region, tz_name in _LWT_REGIONS:
        tz = ZoneInfo(tz_name)
        today_local = now.astimezone(tz).date()
        target_dates = [today_local + _timedelta(days=i) for i in range(_DELTA_BOARD_DAYS)]

        issuances = by_region.get(region, {})
        # Latest event per synoptic bucket wins.
        bucket_winner: dict[_datetime, _datetime] = {}
        for iss in issuances:
            b = _synoptic_bucket(iss)
            if b not in bucket_winner or iss > bucket_winner[b]:
                bucket_winner[b] = iss
        # Ascending [(bucket_ts, issued_ts, comp_targets, earliest_local_date)].
        buckets_asc = []
        for b in sorted(bucket_winner):
            iss = bucket_winner[b]
            comp = issuances[iss]
            targets = comp["targets"]
            earliest = min((ts.astimezone(tz).date() for ts in targets), default=None)
            buckets_asc.append((b, iss, targets, earliest))

        current = buckets_asc[-1] if buckets_asc else None
        prior = None
        if len(buckets_asc) >= 2:
            cand = buckets_asc[-2]
            if current[0] - cand[0] <= _DELTA_MAX_GAP:  # within the 24h walk-back cap
                prior = cand

        # Envelope-level bookkeeping for the header "AZ vs BZ" label.
        if current is not None and (global_current is None or current[0] > global_current):
            global_current = current[0]

        cur_comp = current[2] if current else {}
        cur_leading = current[3] if current else None
        days = []
        for d in target_dates:
            cell = None
            if current is not None and prior is not None:
                cell = _delta_pair(cur_comp, prior[2], tz, d, leading_exempt=(d == cur_leading))
            count, direction = (0, 0)
            if current is not None:
                count, direction = _delta_streak(buckets_asc, tz, d)
            if cell is None:
                days.append({
                    "date": d.isoformat(),
                    "delta_f": None,
                    "current_max_f": None,
                    "prior_max_f": None,
                    "shared_hours": 0,
                    "streak": min(count, _DELTA_STREAK_CAP),
                    "streak_dir": "warmer" if direction > 0 else "cooler" if direction < 0 else None,
                })
            else:
                delta_f, cur_max, pri_max, shared = cell
                days.append({
                    "date": d.isoformat(),
                    "delta_f": delta_f,
                    "current_max_f": cur_max,
                    "prior_max_f": pri_max,
                    "shared_hours": shared,
                    "streak": min(count, _DELTA_STREAK_CAP),
                    "streak_dir": "warmer" if direction > 0 else "cooler" if direction < 0 else None,
                })

        cov = issuances[current[1]]["cov"] if current else None
        age = issuances[current[1]]["age"] if current else None

        out_regions.append({
            "region": region,
            "tz": tz_name,
            "current_bucket": current[0].isoformat() if current else None,
            "current_issued_ts": current[1].isoformat() if current else None,
            "prior_bucket": prior[0].isoformat() if prior else None,
            "prior_issued_ts": prior[1].isoformat() if prior else None,
            "coverage": cov,
            "max_age_hours": age,
            "days": days,
        })

    # Header label: the board's current bucket is the freshest across the fleet;
    # its prior is the most common prior among regions actually sitting on that
    # bucket (they move together at NWS issuance cadence).
    prior_tally: dict[str, int] = defaultdict(int)
    for reg in out_regions:
        if reg["current_bucket"] == (global_current.isoformat() if global_current else None) \
                and reg["prior_bucket"] is not None:
            prior_tally[reg["prior_bucket"]] += 1
    board_prior = max(prior_tally, key=prior_tally.get) if prior_tally else None

    return {
        "generated_at": now.isoformat(),
        "current_bucket": global_current.isoformat() if global_current else None,
        "prior_bucket": board_prior,
        "days": _DELTA_BOARD_DAYS,
        "streak_cap": _DELTA_STREAK_CAP,
        "regions": out_regions,
    }


# In-process response cache: single entry, 60s TTL. The underlying vintages turn
# over only at NWS issuance cadence, so a brief window absorbs board scans
# without hammering Neon.
_DELTA_BOARD_CACHE_TTL = 60.0
_delta_board_cache: dict[str, tuple[float, dict]] = {}


@app.get("/api/weather/delta-board")
async def weather_delta_board(response: Response):
    """The delta board: 17 LWT regions × next 7 days, each cell the run-over-run
    change in daily-max load-weighted temperature (°F) between the two most
    recent synoptic issuance buckets, plus a same-sign streak per cell. Read-only
    over forecasts_nws region vintages; always 17 regions; sparse history
    degrades to no-data cells. Cached 60s. DB unavailable → 503."""
    assert _pool is not None

    now_mono = time.monotonic()
    cached = _delta_board_cache.get("board")
    if cached is not None and (now_mono - cached[0]) < _DELTA_BOARD_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=60"
        return cached[1]

    now = _utcnow()
    regions = [r for r, _ in _LWT_REGIONS]
    since = now - _timedelta(days=_DELTA_LOOKBACK_DAYS)
    board_sql = """
        SELECT series, issued_ts, target_ts,
               value::float8                   AS value,
               (meta->>'coverage')::float8     AS coverage,
               (meta->>'max_age_hours')::float8 AS max_age_hours
        FROM forecasts_nws
        WHERE series = ANY(%(regions)s)
          AND meta->>'agg_version' = %(agg)s
          AND issued_ts >= %(since)s
    """
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(board_sql, {
                    "regions": regions,
                    "agg": _DELTA_AGG_VERSION,
                    "since": since,
                })
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    payload = _compute_delta_board(rows or [], now)
    _delta_board_cache["board"] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=60"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/weather/meteogram — Weather Phase 2.5, the Delta Board's tap-through
# (2026-07-26)
#
# One overlaid temperature timeline for a REGION or a STATION (the `series` param
# is the discriminator; the payload's `kind` flag tells the UI which). Four
# layers, composed here — the endpoint owns composition, the UI only renders:
#   1. actuals — trailing ~72h.
#        region : timeseries_values dataset 'lwt_temp_hourly' (already °F).
#        station: 'ghcnh_weather_hourly' (history AUTHORITY) with
#                 'nws_obs_hourly' filling only the trailing edge ghcnh has not
#                 yet ingested (the lwt.py composition pattern), °C→°F.
#   2. current issuance — solid forecast, now→horizon: the latest event in the
#        newest populated SYNOPTIC bucket (00/06/12/18Z), reusing the delta
#        board's _synoptic_bucket + walk-back verbatim so the board and the
#        meteogram AGREE on which run is "current" (acceptance #1).
#   3. ghosts — the latest event in each of the 1–2 prior populated buckets,
#        walked back over sparse buckets with the board's 24h gap cap (so the
#        solid + first ghost are exactly the board's current + prior run).
#   4. normals band — daily normal max/min behind everything.
#        station: station_normals_daily directly (its own basis).
#        region : SAME-BASIS composition needs the region's DD-basis station
#                 WEIGHTS, which live only in the pantry (the DD set differs from
#                 the LWT composition set — e.g. PGE-TAC's band excludes KSFO even
#                 after KSFO joins the LWT composition), and those weights are NOT
#                 a stable ruled scalar like the region→tz map, so an API-side copy
#                 would silently DRIFT. The ruled fix landed: the pantry now
#                 PUBLISHES composed region normals as the 'lwt_normals_daily'
#                 dataset — series '<REGION>.TMAX_NORM' / '.TMIN_NORM' (°F), a
#                 366-day climatological year materialized on the 2012 leap
#                 reference year (Feb 29 keyed there), ts at local midnight in the
#                 region tz, DD-basis station set/weights in meta. So this service
#                 READS that DATA (not config): the region branch maps the plotted
#                 local dates onto the 2012 (month, day) keys and serves the band,
#                 exactly the station shape. `normals_basis` names the state
#                 ('station' | 'none' | 'region' | 'region_pending') — additive,
#                 honest: 'region' when the band is non-empty, 'region_pending'
#                 when the dataset has no rows yet for that region (the pantry may
#                 not have written, or the region failed the coverage gate).
#
# Forecast values: REGION series are already °F (like the delta board);
# STATION '{id}_temperature' series are Celsius (unit_src='F' describes the NWS
# SOURCE — the stored value is °C; see the temp-matrix receipts) → °C→°F.
#
# Read-only; no writes; no pantry changes. Cached 60s per series. DB down → 503.
# ═══════════════════════════════════════════════════════════════════════════

_METEOGRAM_ACTUALS_HOURS = 72            # trailing actuals window
_METEOGRAM_MAX_GHOSTS = 2                # up to 2 prior issuances behind the solid
_METEOGRAM_FC_LOOKBACK_DAYS = _DELTA_LOOKBACK_DAYS  # issuance history for the buckets
_METEOGRAM_MODEL = "nws"                 # Phase-3 hook: constant today, one model
_METEOGRAM_CACHE_TTL = 60.0

# region → tz (dict view of the ruled board table) and the station basket lookups
# (GHCN id / ICAO), reused for series resolution.
_LWT_REGION_TZ: dict[str, str] = dict(_LWT_REGIONS)
_STATION_BY_ID: dict[str, dict] = {s["station_id"]: s for s in _WEATHER_STATIONS}
_STATION_BY_ICAO: dict[str, dict] = {
    s["icao"]: s for s in _WEATHER_STATIONS if s.get("icao")
}

# Per-series response cache: 60s TTL (vintages turn over at NWS cadence). Bounded
# by the finite region + station universe.
_meteogram_cache: dict[str, tuple[float, dict]] = {}


def _resolve_meteogram_series(series: str) -> tuple[str, str, str | None, str]:
    """(kind, tz_name, station_id, forecast_series) for a `series` param.

    Region when `series` is one of the 17 LWT regions (forecast series = the
    region id, actuals from the composed lwt_temp_hourly). Otherwise a station,
    resolved by GHCN id, ICAO, or a '<id>_temperature' string; tz comes from the
    ruled station basket (Pacific fallback for an off-basket id, so a station that
    starts flowing forecasts before it joins the basket still renders in a sane
    local frame rather than erroring)."""
    if series in _LWT_REGION_TZ:
        return "region", _LWT_REGION_TZ[series], None, series
    sid = series[: -len("_temperature")] if series.endswith("_temperature") else series
    meta = _STATION_BY_ID.get(sid) or _STATION_BY_ICAO.get(series) or _STATION_BY_ICAO.get(sid)
    if meta is not None:
        sid = meta["station_id"]
        tz_name = meta["tz"]
    else:
        tz_name = "America/Los_Angeles"
    return "station", tz_name, sid, f"{sid}_temperature"


def _meteo_build_issuances(fc_rows: list[dict], is_region: bool, seam: _datetime):
    """(current, ghosts) from raw forecasts_nws rows. Same synoptic bucketing +
    24h-capped walk-back as the delta board, so the solid line and first ghost are
    the board's current and prior runs. Points are clipped to `seam` (the last
    actual instant) forward, so the forecast reads now→horizon and joins the
    actuals as one timeline. Region values are °F as stored; station values are
    °C→°F. Ghosts carry no coverage/max_age badge (they are context)."""
    by_iss: dict[_datetime, dict] = {}
    for r in fc_rows:
        v = r.get("value")
        if v is None:
            continue
        e = by_iss.get(r["issued_ts"])
        if e is None:
            e = {"targets": {}, "cov": None, "age": None}
            by_iss[r["issued_ts"]] = e
        e["targets"][r["target_ts"]] = float(v)
        cov = r.get("coverage")
        if cov is not None:
            e["cov"] = cov if e["cov"] is None else min(e["cov"], cov)
        age = r.get("max_age_hours")
        if age is not None:
            e["age"] = age if e["age"] is None else max(e["age"], age)

    bucket_winner: dict[_datetime, _datetime] = {}
    for iss in by_iss:
        b = _synoptic_bucket(iss)
        if b not in bucket_winner or iss > bucket_winner[b]:
            bucket_winner[b] = iss
    buckets_asc = sorted(bucket_winner.items())  # [(bucket_ts, issued_ts), …]

    def pack(bucket_ts: _datetime, issued_ts: _datetime) -> dict:
        comp = by_iss[issued_ts]
        points = []
        for ts in sorted(comp["targets"]):
            if ts < seam:
                continue
            val = comp["targets"][ts]
            temp = round(val, 1) if is_region else round(_c_to_f(val), 1)
            points.append({"ts": ts.isoformat(), "temp_f": temp})
        return {
            "model": _METEOGRAM_MODEL,
            "bucket": bucket_ts.isoformat(),
            "issued_ts": issued_ts.isoformat(),
            "points": points,
            "coverage": comp["cov"],
            "max_age_hours": comp["age"],
        }

    if not buckets_asc:
        return None, []
    cb, ci = buckets_asc[-1]
    current = pack(cb, ci)
    ghosts = []
    i = len(buckets_asc) - 1
    while len(ghosts) < _METEOGRAM_MAX_GHOSTS and i >= 1:
        nb, _ni = buckets_asc[i]
        ob, oi = buckets_asc[i - 1]
        if nb - ob > _DELTA_MAX_GAP:
            break  # a > 24h gap between populated buckets — same cut as the board
        ghosts.append(pack(ob, oi))
        i -= 1
    return current, ghosts


def _meteo_region_actuals(rows: list[dict]) -> list[tuple[_datetime, float]]:
    """Composed region LWT actuals — already °F — as (ts, °F), ascending."""
    out = [(r["ts"], round(float(r["value"]), 1)) for r in rows if r.get("value") is not None]
    out.sort(key=lambda p: p[0])
    return out


def _meteo_station_actuals(
    ghcnh_rows: list[dict], nws_rows: list[dict]
) -> list[tuple[_datetime, float]]:
    """Station actuals: ghcnh is the history AUTHORITY; nws_obs fills only the
    trailing edge past ghcnh's latest instant (the lwt.py composition pattern).
    °C→°F. Returns (ts, °F) ascending."""
    auth: dict[_datetime, float] = {}
    for r in ghcnh_rows:
        if r.get("value") is not None:
            auth[r["ts"]] = _c_to_f(float(r["value"]))
    latest = max(auth) if auth else None
    for r in nws_rows:
        if r.get("value") is None:
            continue
        ts = r["ts"]
        if latest is None or ts > latest:
            auth[ts] = _c_to_f(float(r["value"]))
    return [(ts, round(auth[ts], 1)) for ts in sorted(auth)]


def _local_date_span(start: _datetime, end: _datetime, tz: ZoneInfo) -> list[_date]:
    """Inclusive local calendar dates from `start` to `end` in `tz`."""
    d = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    out = []
    while d <= last:
        out.append(d)
        d += _timedelta(days=1)
    return out


def _meteo_station_normals(normals_rows: list[dict], dates: list[_date]) -> list[dict]:
    """The daily normals band for a station over `dates`, from station_normals_daily
    keyed (month, day). A day missing either bound is omitted (no faked half-band)."""
    by_key = {(r["month"], r["day"]): r for r in normals_rows}
    out = []
    for d in dates:
        r = by_key.get((d.month, d.day))
        if r and r.get("tmax_norm_f") is not None and r.get("tmin_norm_f") is not None:
            out.append({
                "date": d.isoformat(),
                "normal_max_f": round(float(r["tmax_norm_f"]), 1),
                "normal_min_f": round(float(r["tmin_norm_f"]), 1),
            })
    return out


def _meteo_region_normals(
    normals_rows: list[dict], dates: list[_date], tz: ZoneInfo
) -> list[dict]:
    """The daily normals band for a region over `dates`, from the pantry's
    'lwt_normals_daily' dataset. The two series '<REGION>.TMAX_NORM' / '.TMIN_NORM'
    are a 366-day climatological year (°F) materialized on the 2012 leap reference
    year (Feb 29 keyed there), each row's ts at local midnight in the region tz.
    Each row's (month, day) key is recovered by reading ts back in the region tz
    (inverting the pantry's local-midnight convention), so the plotted local dates
    map straight on — Feb 29 in a leap plotted year lands on the 2012 Feb 29 row.
    A day missing either bound is omitted (no faked half-band; same rule as the
    station band). Emits the exact station shape: {date, normal_max_f, normal_min_f}."""
    max_by_key: dict[tuple[int, int], float] = {}
    min_by_key: dict[tuple[int, int], float] = {}
    for r in normals_rows:
        v = r.get("value")
        if v is None:
            continue
        series = r.get("series", "")
        key = (r["ts"].astimezone(tz).month, r["ts"].astimezone(tz).day)
        if series.endswith(".TMAX_NORM"):
            max_by_key[key] = float(v)
        elif series.endswith(".TMIN_NORM"):
            min_by_key[key] = float(v)
    out = []
    for d in dates:
        key = (d.month, d.day)
        mx = max_by_key.get(key)
        mn = min_by_key.get(key)
        if mx is not None and mn is not None:
            out.append({
                "date": d.isoformat(),
                "normal_max_f": round(mx, 1),
                "normal_min_f": round(mn, 1),
            })
    return out


def _compute_meteogram(
    series: str,
    kind: str,
    tz_name: str,
    now: _datetime,
    actuals_pts: list[tuple[_datetime, float]],
    fc_rows: list[dict],
    normals_rows: list[dict] | None,
) -> dict:
    """Pure composition of the meteogram payload (no DB, no clock — `now` injected).
    See the section header for the ruled model and the region-normals STOP."""
    tz = ZoneInfo(tz_name)
    is_region = kind == "region"
    now_floor = now.replace(minute=0, second=0, microsecond=0)

    actuals_pts = sorted(actuals_pts, key=lambda p: p[0])
    seam = actuals_pts[-1][0] if actuals_pts else now_floor
    current, ghosts = _meteo_build_issuances(fc_rows, is_region, seam)
    actuals = [{"ts": ts.isoformat(), "temp_f": v} for ts, v in actuals_pts]

    # Normals band over the plotted local-date span (actuals start → last forecast
    # target), same span both paths. Region reads the pantry's published
    # lwt_normals_daily (empty until the pantry writes → region_pending); station
    # composes station_normals_daily directly.
    fc_targets = [r["target_ts"] for r in fc_rows if r.get("value") is not None]
    span_start = actuals_pts[0][0] if actuals_pts else (now - _timedelta(hours=_METEOGRAM_ACTUALS_HOURS))
    span_end = max(fc_targets) if fc_targets else seam
    dates = _local_date_span(span_start, span_end, tz)
    if is_region:
        normals = _meteo_region_normals(normals_rows or [], dates, tz)
        normals_basis = "region" if normals else "region_pending"
    else:
        normals = _meteo_station_normals(normals_rows or [], dates)
        normals_basis = "station" if normals else "none"

    return {
        "series": series,
        "kind": kind,
        "tz": tz_name,
        "generated_at": now.isoformat(),
        "actuals": actuals,
        "current": current,
        "ghosts": ghosts,
        "normals": normals,
        # Additive honesty flag (UI-optional): why the band is or isn't present.
        "normals_basis": normals_basis,
    }


_METEO_REGION_ACTUALS_SQL = """
    SELECT ts, value::float8 AS value
    FROM timeseries_values
    WHERE dataset = 'lwt_temp_hourly' AND series = %(s)s
      AND ts >= %(since)s AND ts <= %(now)s
    ORDER BY ts
"""
_METEO_REGION_FC_SQL = """
    SELECT issued_ts, target_ts, value::float8 AS value,
           (meta->>'coverage')::float8      AS coverage,
           (meta->>'max_age_hours')::float8 AS max_age_hours
    FROM forecasts_nws
    WHERE series = %(s)s AND meta->>'agg_version' = %(agg)s
      AND issued_ts >= %(since)s
"""
_METEO_STATION_FC_SQL = """
    SELECT issued_ts, target_ts, value::float8 AS value
    FROM forecasts_nws
    WHERE series = %(s)s AND issued_ts >= %(since)s
"""
_METEO_OBS_SQL = """
    SELECT ts, value::float8 AS value
    FROM timeseries_values
    WHERE dataset = %(ds)s AND series = %(s)s
      AND ts >= %(since)s AND ts <= %(now)s
    ORDER BY ts
"""
_METEO_NORMALS_SQL = """
    SELECT month, day,
           tmax_norm_f::float8 AS tmax_norm_f,
           tmin_norm_f::float8 AS tmin_norm_f
    FROM station_normals_daily
    WHERE station_id = %(sid)s AND month = ANY(%(months)s)
"""
_METEO_REGION_NORMALS_SQL = """
    SELECT series, ts, value::float8 AS value
    FROM timeseries_values
    WHERE dataset = 'lwt_normals_daily' AND series = ANY(%(series)s)
"""


@app.get("/api/weather/meteogram")
async def weather_meteogram(
    response: Response,
    series: str = Query(
        ...,
        min_length=1,
        description="The LWT region id (e.g. 'PGE-TAC') or a station "
        "(GHCN id, ICAO, or '<id>_temperature'). The response's `kind` flag "
        "reports which was resolved.",
    ),
):
    """The meteogram for one series: trailing actuals, the current NWS issuance,
    1–2 ghosted prior issuances (same synoptic buckets as the delta board), and the
    daily normals band. Station bands read station_normals_daily; region bands read
    the pantry's published 'lwt_normals_daily' dataset (`normals_basis='region'`),
    falling back to empty with `normals_basis='region_pending'` when that dataset
    has no rows yet for the region. Read-only; cached 60s per series; DB
    unavailable → 503."""
    assert _pool is not None

    now_mono = time.monotonic()
    cached = _meteogram_cache.get(series)
    if cached is not None and (now_mono - cached[0]) < _METEOGRAM_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=60"
        return cached[1]

    now = _utcnow()
    kind, tz_name, sid, fc_series = _resolve_meteogram_series(series)
    tz = ZoneInfo(tz_name)
    since = now - _timedelta(hours=_METEOGRAM_ACTUALS_HOURS)
    fsince = now - _timedelta(days=_METEOGRAM_FC_LOOKBACK_DAYS)
    normals_rows: list[dict] | None = None

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                if kind == "region":
                    await cur.execute(_METEO_REGION_ACTUALS_SQL,
                                      {"s": series, "since": since, "now": now})
                    act_rows = await cur.fetchall()
                    await cur.execute(_METEO_REGION_FC_SQL,
                                      {"s": series, "agg": _DELTA_AGG_VERSION, "since": fsince})
                    fc_rows = await cur.fetchall()
                    await cur.execute(_METEO_REGION_NORMALS_SQL,
                                      {"series": [f"{series}.TMAX_NORM", f"{series}.TMIN_NORM"]})
                    normals_rows = await cur.fetchall()
                    actuals_pts = _meteo_region_actuals(act_rows or [])
                else:
                    await cur.execute(_METEO_STATION_FC_SQL, {"s": fc_series, "since": fsince})
                    fc_rows = await cur.fetchall()
                    await cur.execute(_METEO_OBS_SQL,
                                      {"ds": "ghcnh_weather_hourly", "s": fc_series,
                                       "since": since, "now": now})
                    ghcnh_rows = await cur.fetchall()
                    await cur.execute(_METEO_OBS_SQL,
                                      {"ds": "nws_obs_hourly", "s": fc_series,
                                       "since": since, "now": now})
                    nws_rows = await cur.fetchall()
                    months = sorted({
                        d.month for d in _local_date_span(
                            since, now + _timedelta(days=_METEOGRAM_FC_LOOKBACK_DAYS), tz)
                    })
                    await cur.execute(_METEO_NORMALS_SQL, {"sid": sid, "months": months})
                    normals_rows = await cur.fetchall()
                    actuals_pts = _meteo_station_actuals(ghcnh_rows or [], nws_rows or [])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    payload = _compute_meteogram(series, kind, tz_name, now, actuals_pts, fc_rows or [], normals_rows)
    _meteogram_cache[series] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=60"
    return payload


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


# ═══════════════════════════════════════════════════════════════════════════
# /api/timeseries/caiso-demand-stack — forward demand-stack substrate (#?, 6/24)
#
# ONE concern: feed the two forward 7-day stacked-bar charts from a single read
# so they stay dimensionally consistent — renewables chart (wind+solar) and
# load chart (net-demand base + wind + solar wedge). Per hourly target over a
# forward frame it returns total demand, net demand, solar, wind — each with a
# forecast and (where realized) an actual value, plus an is_scored now-seam.
# Net demand is derived SERVER-SIDE so `net + wind + solar = total` holds by
# construction and the load chart's top two segments equal the renewables chart
# exactly. Read-only; no schema/ingester/migration change.
#
# REUSE (recon of /api/timeseries/caiso-forecast-vs-actual, the sibling read):
#   (a) latest-vintage selection — DISTINCT ON (...) ORDER BY generated_ts DESC
#       NULLS LAST, issued_ts DESC NULLS LAST. The never-overwrite forecasts_caiso
#       store accumulates vintages; this picks the freshest per target hour and
#       NEVER blends vintages into the subtraction.
#   (b) flatten-to-wide — MAX(value_mw) FILTER (WHERE ...) pivots the per-series
#       rows into columns for one target_ts (here keyed on forecast_type/series
#       rather than product, since the stack spans three series at fixed vintages).
#   (c) is_scored seam — forecast spine LEFT JOIN actuals; is_scored = the load
#       (total-demand) actual exists. The forecast spine is the natural trailing
#       edge; is_scored marks the realized/forecast boundary, same as the sibling.
#   (d) actuals join — actuals_caiso keyed on `series` mapped to forecast_type on
#       target_ts, identical to the sibling endpoint.
# No PT-only label field is added: the sibling endpoint emits `ts` as an ISO
# string with UTC offset and the dashboard localizes client-side — matched here
# so the fetch/parse layer is familiar.
#
# DECISION (baked into the spec, redline-able): the forward DEMAND curve is load
# `7DA` (latest vintage); renewables are `DAM` (the only day-ahead+ issuance at
# the source — solar/wind have no 7-day-ahead forecast, an asymmetry that is real
# and recon-confirmed). Net-demand forecast = 7DA load − DAM solar − DAM wind.
# Single forward curves, vintage-coherent, both reaching +7d. Near-term load
# stitch (DAM/2DA where tighter) is deliberately deferred — it mixes vintages.
#
# NET DEMAND NEVER NULL-POISONED: if any of load/solar/wind is missing for an
# hour, net demand for that hour is NULL — not a partial subtraction against an
# implicit zero (fail-honest, Principle #27). Across the DAM/7DA forward overlap
# this is expected to affect no hours, but the code does not assume it.
# ═══════════════════════════════════════════════════════════════════════════

# Forward demand-stack vintages (the spec's baked-in decision). Load forward
# curve = 7DA; renewables = DAM (only day-ahead+ issuance at the source). The
# LATEST vintage of each is selected; vintages are never blended.
DEMAND_STACK_FORECAST: dict[str, str] = {
    "load": "7DA",
    "solar": "DAM",
    "wind": "DAM",
}

# Default forward frame: today (PT) → today+7d, the +7d edge both the 7DA load
# and the DAM renewables forecasts reach. Inclusive of the end day.
DEMAND_STACK_HORIZON_DAYS = 7


def _net_demand(total: float | None, solar: float | None, wind: float | None) -> float | None:
    """total − solar − wind, but None if ANY input is None (no partial subtraction
    against an implicit zero — net demand is fail-honest, never NULL-poisoned)."""
    if total is None or solar is None or wind is None:
        return None
    return total - solar - wind


@app.get("/api/timeseries/caiso-demand-stack")
async def caiso_demand_stack(
    start: str | None = Query(
        default=None,
        description="Forward-frame start, Pacific calendar day YYYY-MM-DD "
        "(inclusive). Use with `end`. Omitted -> today (PT).",
    ),
    end: str | None = Query(
        default=None,
        description="Forward-frame end, Pacific calendar day YYYY-MM-DD "
        f"(inclusive). Use with `start`. Omitted -> today+{DEMAND_STACK_HORIZON_DAYS}d (PT).",
    ),
):
    """
    CAISO forward demand-stack — one hourly series feeding both stacked-bar charts.

    Each point is one Pacific-time target interval carrying, for total demand,
    net demand, solar and wind, a FORECAST value (present across the whole
    forward frame) and an ACTUAL value (present only left of the now-seam):

        {
          "ts": "2026-06-27T01:00:00+00:00",
          "is_scored": false,
          "total_demand_fc": 31000.0,   # latest 7DA load
          "solar_fc": 0.0,              # latest DAM solar
          "wind_fc": 4200.0,            # latest DAM wind
          "net_demand_fc": 26800.0,     # total − solar − wind, server-side
          "total_demand_act": null,     # load actual
          "solar_act": null,
          "wind_act": null,
          "net_demand_act": null        # total − solar − wind, server-side
        }

    `net + solar + wind = total` holds by construction (the subtraction is done
    here, once), so the load chart's wind+solar wedge equals the renewables
    chart exactly. Net demand is NULL for any hour missing one of its three
    inputs — never a partial subtraction.

    Two selection modes:
      * start=YYYY-MM-DD&end=...  -> explicit inclusive Pacific-day forward frame
      * (neither)                 -> today .. today+{horizon}d (PT)

    Envelope:
        {
          "tz": "America/Los_Angeles",
          "window": {"start": "2026-06-24", "end": "2026-07-01"},
          "sources": {
            "total_demand": "7DA load", "solar": "DAM solar", "wind": "DAM wind",
            "net_demand": "total_demand - solar - wind (server-side)"
          },
          "last_scored_ts": "2026-06-24T18:00:00+00:00",
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
        # "Today" is a Pacific calendar day — the forward frame lives on the PT
        # clock (reuse the sibling endpoint's market timezone).
        today_pt = _datetime.now(ZoneInfo(MARKET_TZ)).date()
        lo_date = today_pt.isoformat()
        hi_date = (today_pt + _timedelta(days=DEMAND_STACK_HORIZON_DAYS)).isoformat()

    # ── Flatten-to-wide across the three series, latest vintage each ────────
    # fc:      latest vintage per (forecast_type, target_ts) — freshest generated
    #          wins; each series is pinned to ONE product so no vintage blending.
    # fc_wide: pivot load/solar/wind forecasts into columns per target_ts.
    # act:     realized load/solar/wind for the same window, keyed on `series`.
    # Final:   forecast spine LEFT JOIN actuals so forward rows survive unscored.
    # PT day boundaries reuse the proven fuel-mix pattern (AT TIME ZONE); end
    # exclusive at (hi + 1 day) so the window is inclusive of `hi`.
    query = """
        WITH fc AS (
            SELECT DISTINCT ON (forecast_type, target_ts)
                   forecast_type, target_ts, value_mw
            FROM forecasts_caiso
            WHERE (
                    (forecast_type = 'load'  AND product = %(load_product)s)
                 OR (forecast_type = 'solar' AND product = %(solar_product)s)
                 OR (forecast_type = 'wind'  AND product = %(wind_product)s)
                  )
              AND target_ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
              AND target_ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
            ORDER BY forecast_type, target_ts,
                     generated_ts DESC NULLS LAST, issued_ts DESC NULLS LAST
        ),
        fc_wide AS (
            SELECT
                target_ts,
                MAX(value_mw) FILTER (WHERE forecast_type = 'load')  AS total_fc,
                MAX(value_mw) FILTER (WHERE forecast_type = 'solar') AS solar_fc,
                MAX(value_mw) FILTER (WHERE forecast_type = 'wind')  AS wind_fc
            FROM fc
            GROUP BY target_ts
        ),
        act AS (
            SELECT
                target_ts,
                MAX(value_mw) FILTER (WHERE series = 'load')  AS total_act,
                MAX(value_mw) FILTER (WHERE series = 'solar') AS solar_act,
                MAX(value_mw) FILTER (WHERE series = 'wind')  AS wind_act
            FROM actuals_caiso
            WHERE series IN ('load', 'solar', 'wind')
              AND target_ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
              AND target_ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
            GROUP BY target_ts
        )
        SELECT
            f.target_ts                  AS target_ts,
            f.total_fc                   AS total_fc,
            f.solar_fc                   AS solar_fc,
            f.wind_fc                    AS wind_fc,
            a.total_act                  AS total_act,
            a.solar_act                  AS solar_act,
            a.wind_act                   AS wind_act,
            (a.total_act IS NOT NULL)    AS is_scored
        FROM fc_wide f
        LEFT JOIN act a ON a.target_ts = f.target_ts
        ORDER BY f.target_ts ASC
    """
    params = {
        "load_product": DEMAND_STACK_FORECAST["load"],
        "solar_product": DEMAND_STACK_FORECAST["solar"],
        "wind_product": DEMAND_STACK_FORECAST["wind"],
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
                f"no demand-stack data for {lo_date}..{hi_date} (Pacific). "
                "Verify forecasts_caiso has load/solar/wind rows in the window."
            ),
        )

    # ── Shape wide points; net demand server-side; track the realized seam ──
    # Rows ascending, so the last is_scored row is the seam (last realized load).
    # is_scored keys on the load (total-demand) actual — the demand spine. Net
    # demand is computed from floats and is NULL when any input is missing, so a
    # scored hour whose renewable actual lags still reports total but null net
    # (fail-honest), rather than subtracting an implicit zero.
    points: list[dict] = []
    last_scored_ts: str | None = None
    for r in rows:
        ts = r["target_ts"].isoformat()
        is_scored = bool(r["is_scored"])

        total_fc = _mw(r["total_fc"])
        solar_fc = _mw(r["solar_fc"])
        wind_fc = _mw(r["wind_fc"])
        total_act = _mw(r["total_act"])
        solar_act = _mw(r["solar_act"])
        wind_act = _mw(r["wind_act"])

        points.append({
            "ts": ts,
            "is_scored": is_scored,
            "total_demand_fc": total_fc,
            "solar_fc": solar_fc,
            "wind_fc": wind_fc,
            "net_demand_fc": _net_demand(total_fc, solar_fc, wind_fc),
            "total_demand_act": total_act,
            "solar_act": solar_act,
            "wind_act": wind_act,
            "net_demand_act": _net_demand(total_act, solar_act, wind_act),
        })
        if is_scored:
            last_scored_ts = ts

    return {
        "tz": MARKET_TZ,
        "window": {"start": lo_date, "end": hi_date},
        "sources": {
            "total_demand": f"{DEMAND_STACK_FORECAST['load']} load",
            "solar": f"{DEMAND_STACK_FORECAST['solar']} solar",
            "wind": f"{DEMAND_STACK_FORECAST['wind']} wind",
            "net_demand": "total_demand - solar - wind (server-side)",
        },
        "last_scored_ts": last_scored_ts,
        "count": len(points),
        "points": points,
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/timeseries/caiso-peak-demand — derived daily peak demand per TAC area.
#
# Source: timeseries_values, dataset 'caiso_load_fcst_7day' — CAISO's 7-day-ahead
# hourly load forecast (OASIS SLD_FCST, market_run_id=7DA) for each of the 36 TAC
# areas it publishes: the 'CA ISO-TAC' system total, the CAISO internal TACs
# (PGE-TAC, SCE-TAC, SDGE-TAC, VEA-TAC, MWD-TAC, BANC*), and the neighbouring WECC
# balancing authorities CAISO forecasts. One series per TAC area; hourly MW; UTC
# interval-beginning; single-issuance and immutable (NOT a vintage — there is one
# issuance to read, so no latest-vintage DISTINCT ON is needed here).
#
# The daily PEAK is DERIVED here at read time, never stored: per TAC area, per
# Pacific operating date, peak_mw = the max hourly MW and peak_he = the HOUR-
# ENDING of the interval that carries it (ties -> earliest hour). CAISO is a
# Pacific market, so operating dates and HE ride the PT clock — the same
# AT-TIME-ZONE discipline the fuel-mix / demand-stack windows use (midnight PT ==
# 07:00 UTC under PDT; a full PT day is 24 hourly rows). Each day also carries
# hours_covered and partial (< 24h) so an edge day the issuance only half-spans
# is surfaced, never hidden.
#
# Peaks ONLY — day-over-day deltas are the frontend's job (the /weather strip
# colours them in its temp-matrix grammar). This feed reads on its DIAGONAL: each
# operating date = its 7-days-prior vintage, so `issued_ts` is the latest vintage
# in the payload (MAX publish_time) and each day carries its own `issued`. Rows
# are ordered system first (CA ISO-TAC), then the IOU TACs (SCE / PGE / SDGE),
# then every other area (role "ba") alphabetically for the strip's "more"
# affordance.
#
# Honest-empty on purpose (differs from the 404 siblings): no banked rows -> 200
# with areas: [] so the strip renders its holding state, not an error. DB
# unavailable -> 503.
# ═══════════════════════════════════════════════════════════════════════════

PEAK_DEMAND_DATASET = "caiso_load_fcst_7day"
_PEAK_DEMAND_FULL_DAY_HOURS = 24  # < 24 hourly points in a PT day -> partial peak

# The strip's primary rows, in ratified order: the CA ISO-TAC system total, then
# the three big IOU TACs. Every other series in the dataset (VEA-TAC, MWD-TAC,
# the BANC sub-LSEs, and the neighbouring WECC BAs) rides along as role "ba" for
# the frontend's "more" affordance, alphabetical after these.
#   (series, label, role)
_PEAK_DEMAND_PRIMARY: list[tuple[str, str, str]] = [
    ("CA ISO-TAC", "CA ISO-TAC", "system"),
    ("SCE-TAC", "SCE", "tac"),
    ("PGE-TAC", "PGE", "tac"),
    ("SDGE-TAC", "SDGE", "tac"),
]
_PEAK_DEMAND_PRIMARY_INDEX = {
    series: i for i, (series, _label, _role) in enumerate(_PEAK_DEMAND_PRIMARY)
}


def _publish_key(ts: str) -> _datetime:
    """Sort key for a publish_time string — the frontier vintage is the MAX. An
    unparseable value sorts before every real timestamp so it never wins."""
    try:
        return _datetime.fromisoformat(ts)
    except ValueError:
        return _datetime.min.replace(tzinfo=_timezone.utc)


def _peak_area_meta(series: str) -> tuple[int, str, str]:
    """(sort_rank, label, role) for a series — primary rows keep their ratified
    rank; everything else sorts after them (rank == len(primary)) as role "ba"
    with the raw series code as its label."""
    idx = _PEAK_DEMAND_PRIMARY_INDEX.get(series)
    if idx is not None:
        _series, label, role = _PEAK_DEMAND_PRIMARY[idx]
        return idx, label, role
    return len(_PEAK_DEMAND_PRIMARY), series, "ba"


@app.get("/api/timeseries/caiso-peak-demand")
async def caiso_peak_demand():
    """Derived daily peak demand (MW + hour-ending) per CAISO TAC area.

    Reads the single immutable 7-day-ahead hourly load forecast
    ('caiso_load_fcst_7day') and, per TAC area per Pacific operating date, takes
    the max hourly MW (`peak_mw`) and the hour-ending of the interval that
    carries it (`peak_he`; ties -> earliest hour). Each day also carries
    `hours_covered` and `partial` (< 24h). Peaks only — the frontend derives the
    day-over-day deltas.

    Rows are ordered system first (CA ISO-TAC), then the IOU TACs (SCE / PGE /
    SDGE), then every other area (role "ba") alphabetically for the "more"
    affordance. This feed reads on its DIAGONAL: each operating date = its
    7-days-prior vintage, so top-level `issued_ts` is the latest vintage in the
    payload (MAX publish_time) and each day carries its own `issued`.

    Envelope:
        {
          "dataset": "caiso_load_fcst_7day",
          "as_of": "2026-07-24T00:00:00+00:00",   # request instant, UTC
          "issued_ts": "2026-07-23T16:10:00+00:00",# MAX publish_time (frontier vintage)
          "tz": "America/Los_Angeles",
          "unit": "MW",
          "operating_dates": ["2026-07-23", ...],  # every PT date the pull spans
          "count": N,                              # number of areas
          "areas": [
            {"series": "CA ISO-TAC", "label": "CA ISO-TAC", "role": "system",
             "order": 0, "days": [
               {"date": "2026-07-23", "peak_mw": 38454.05, "peak_he": 19,
                "hours_covered": 24, "partial": false,
                "issued": "2026-07-16T16:10:00+00:00"}, ...]},  # date - 7d vintage
            ...
          ]
        }

    Honest-empty: no banked rows -> 200 with areas: []. DB unavailable -> 503.
    """
    assert _pool is not None

    now = _utcnow()
    tz = ZoneInfo(MARKET_TZ)

    # Single immutable issuance -> a plain read of the dataset; the peak/argmax
    # is derived in Python (mirrors the temp-matrix per-day bucketing). value is
    # nullable in the schema, so drop nulls before they reach the max.
    query = """
        SELECT ts, series, value, meta
        FROM timeseries_values
        WHERE dataset = %(dataset)s AND value IS NOT NULL
        ORDER BY series ASC, ts ASC
    """
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, {"dataset": PEAK_DEMAND_DATASET})
                rows = await cur.fetchall()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # Honest-empty: the strip renders its holding state, never an error page.
    if not rows:
        return {
            "dataset": PEAK_DEMAND_DATASET,
            "as_of": now.isoformat(),
            "issued_ts": None,
            "tz": MARKET_TZ,
            "unit": "MW",
            "operating_dates": [],
            "count": 0,
            "areas": [],
        }

    # Derive peak per (series, PT operating date). Rows arrive ts-ascending within
    # a series, so a strict `>` keeps the EARLIEST hour on ties. HE = the interval-
    # beginning local hour + 1 (the interval [H:00, H+1:00) ends at hour H+1).
    # Per operating date, the forecast vintage that serves it (its own
    # meta.publish_time). On the diagonal each date carries a single vintage; take
    # the max within a date defensively. Top-level issued_ts is the frontier — the
    # latest of these vintages across the whole payload.
    peaks: dict[str, dict[_date, dict]] = defaultdict(dict)
    all_dates: set[_date] = set()
    date_issued: dict[_date, str] = {}
    for r in rows:
        pt: str | None = None
        meta = r.get("meta")
        if isinstance(meta, dict):
            p = meta.get("publish_time")
            if isinstance(p, str) and p:
                pt = p
        local = r["ts"].astimezone(tz)
        op_date = local.date()
        all_dates.add(op_date)
        if pt is not None:
            prev = date_issued.get(op_date)
            if prev is None or _publish_key(pt) > _publish_key(prev):
                date_issued[op_date] = pt
        he = local.hour + 1
        mw = float(r["value"])
        day = peaks[r["series"]].get(op_date)
        if day is None:
            peaks[r["series"]][op_date] = {
                "peak_mw": mw,
                "peak_he": he,
                "hours_covered": 1,
            }
        else:
            day["hours_covered"] += 1
            if mw > day["peak_mw"]:
                day["peak_mw"] = mw
                day["peak_he"] = he

    operating_dates = sorted(all_dates)

    # Frontier vintage: the latest publish_time in the payload (never the min).
    issued_ts = (
        max(date_issued.values(), key=_publish_key) if date_issued else None
    )

    # Primary rows first (system, then the IOU TACs), then every other area
    # alphabetically under role "ba" for the "more" tray.
    ordered_series = sorted(peaks.keys(), key=lambda s: (_peak_area_meta(s)[0], s))

    areas = []
    for order, series in enumerate(ordered_series):
        _rank, label, role = _peak_area_meta(series)
        by_date = peaks[series]
        days = []
        for d in operating_dates:
            day = by_date.get(d)
            if day is None:
                # This series has no coverage for a date another series carries —
                # a null cell, present but empty (never dropped from the grid).
                days.append({
                    "date": d.isoformat(),
                    "peak_mw": None,
                    "peak_he": None,
                    "hours_covered": 0,
                    "partial": True,
                    "issued": date_issued.get(d),
                })
            else:
                hc = day["hours_covered"]
                days.append({
                    "date": d.isoformat(),
                    "peak_mw": day["peak_mw"],
                    "peak_he": day["peak_he"],
                    "hours_covered": hc,
                    "partial": hc < _PEAK_DEMAND_FULL_DAY_HOURS,
                    "issued": date_issued.get(d),
                })
        areas.append({
            "series": series,
            "label": label,
            "role": role,
            "order": order,
            "days": days,
        })

    return {
        "dataset": PEAK_DEMAND_DATASET,
        "as_of": now.isoformat(),
        "issued_ts": issued_ts,
        "tz": MARKET_TZ,
        "unit": "MW",
        "operating_dates": [d.isoformat() for d in operating_dates],
        "count": len(areas),
        "areas": areas,
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/timeseries/caiso-hub-lmp — the three CAISO trading-hub LMP series
# (DA hourly, RTPD 15-min, RTD 5-min) for the previous + current PT trade date,
# shaped for the dashboard's hub-LMP chart (PR-1).
#
# ONE concern: a read-only window onto timeseries_values serving all three hubs
# (NP15 / SP15 / ZP26) across all three markets, plus two server-derived bundles
# the frontend must NOT re-derive — DART (DA − RTPD) and the on/off-peak DA
# averages — so the quote strip, chart subheads, and the later Joule brief all
# read server-authoritative values.
#
# SUBSTRATE (verified live in Neon 2026-07-05; do not re-derive from memory):
#   timeseries_values(ts timestamptz, dataset, series, value numeric, ...)
#     caiso_lmp_da_hourly  hourly  NP15/SP15/ZP26  (through T−1 today; T lands w/ pantry PR-0)
#     caiso_lmp_rt_15min   15-min (RTPD/FMM)       live, ~30 min lag
#     caiso_lmp_rt_5min    5-min  (RTD)            live, ~15 min lag
#   Series are the bare hub labels (NP15, not TH_NP15_GEN-APND). The legacy
#   TH_*_ONPEAK/OFFPEAK series are ignored.
#
# REUSE (recon of the sibling reads — same shape family, deliberately):
#   * PT day-boundary window (AT TIME ZONE, end-exclusive at hi+1d) — fuel mix.
#   * float()/skip-null value handling — fuel mix.
#   * `ts` emitted as a UTC ISO string, client localizes — demand-stack/f-v-a.
#   * server-derived fields (net demand there; DART + peak here) computed once,
#     never stored — demand-stack precedent.
#
# DART (server-side, hourly): spread = DA hourly price − avg(RTPD intervals in
# that hour). Sign convention POSITIVE = DA over RT. It is DA − RTPD (the banked
# FMM settlement definition) — never RTD. Emitted only for hours where DA exists
# AND ≥1 RTPD interval exists; the current in-progress hour averages over the
# intervals available so far. RTPD 15-min intervals are bucketed to their clock
# hour — PT hour starts land on UTC :00 (whole-hour offset), so a UTC floor is
# the DA hour key.
#
# PEAK (server-side, current PT trade date, from the DA curve): { onpeak_avg,
# offpeak_avg }. RECON NOTE — the bucket series TH_{HUB}_GEN_ONPEAK/OFFPEAK-APND
# could NOT be confirmed live in this build environment (no DB connection here;
# every suite runs against an in-memory fake pool). Per the spec's sanctioned
# fallback we take the COMPUTED-SPLIT path: average the bare hourly DA series
# over the NERC on-peak block (HE7–HE22, Mon–Sat, ex-NERC-holiday), reusing the
# locked _NERC_HOLIDAYS list and the on-peak convention already established by
# the /api/almanac/lmp-shape endpoint. A bucket with no rows on the date (e.g.
# a NERC holiday or Sunday → no on-peak hours) is emitted as null, not a fake 0.
#
# LATEST (ticker block): per hub the most recent value+ts per market. DA's
# "latest" is the DA price for the CURRENT PT hour (it is a schedule, not a
# tape) — null when today's DA has not landed yet (expected until pantry PR-0);
# RTPD/RTD "latest" is the tail of the array (freshest interval).
# ═══════════════════════════════════════════════════════════════════════════

# Market key -> (dataset code, cadence label). Order fixes the output order of
# `sources`/`last_ts`; the reverse map routes fetched rows back to their market.
HUB_LMP_MARKETS: dict[str, dict[str, str]] = {
    "da":   {"dataset": "caiso_lmp_da_hourly", "cadence": "hourly"},
    "rtpd": {"dataset": "caiso_lmp_rt_15min",  "cadence": "15-min"},
    "rtd":  {"dataset": "caiso_lmp_rt_5min",   "cadence": "5-min"},
}
_HUB_LMP_DATASET_TO_MARKET = {m["dataset"]: k for k, m in HUB_LMP_MARKETS.items()}

# Trading hubs, in stable output order (spec order). Same set the almanac
# endpoint validates against.
HUB_LMP_HUBS = ["NP15", "SP15", "ZP26"]

# The locked NERC-holiday list reused as a set for O(1) membership in the
# on/off-peak split (source of truth is _NERC_HOLIDAYS above).
_NERC_HOLIDAY_SET = frozenset(_NERC_HOLIDAYS)


def _lmp_is_onpeak(ts_pt) -> bool:
    """NERC on-peak block test for a Pacific-localized hour-start datetime.

    On-peak = HE7–HE22 (PT start hours 6–21) AND Mon–Sat AND not a NERC
    holiday. Everything else — overnight hours, all of Sunday, NERC holidays —
    is off-peak. Matches the locked block definition the LMP-shape almanac
    endpoint already uses (Mon–Sat ex-holiday), narrowed to the HE7–HE22
    on-peak window per the PR-1 spec.
    """
    if ts_pt.date().isoformat() in _NERC_HOLIDAY_SET:
        return False
    if ts_pt.isoweekday() == 7:  # Sunday — all off-peak
        return False
    return 6 <= ts_pt.hour <= 21  # HE7 (06:00-07:00) .. HE22 (21:00-22:00)


@app.get("/api/timeseries/caiso-hub-lmp")
async def caiso_hub_lmp():
    """
    CAISO trading-hub LMP for the previous + current PT trade date.

    One read serves all three hubs (NP15/SP15/ZP26) across all three markets —
    DA hourly, RTPD 15-min, RTD 5-min — plus two fields derived server-side so
    the frontend never re-computes them: DART (DA − RTPD, positive = DA over RT)
    and the on/off-peak DA averages for the current trade date.

    Prices are plain numbers; null rows are skipped (never emitted as null
    inside the arrays). `ts` is UTC ISO — the client localizes to PT.

    Envelope:
        {
          "tz": "America/Los_Angeles",
          "window": {"start": "2026-07-04", "end": "2026-07-05"},
          "sources": {
            "da":   {"dataset": "caiso_lmp_da_hourly", "cadence": "hourly"},
            "rtpd": {"dataset": "caiso_lmp_rt_15min",  "cadence": "15-min"},
            "rtd":  {"dataset": "caiso_lmp_rt_5min",   "cadence": "5-min"}
          },
          "last_ts": {"da": "...|null", "rtpd": "...", "rtd": "..."},
          "hubs": {
            "NP15": {
              "da":   [{"ts": "...", "price": 41.2}, ...],
              "rtpd": [{"ts": "...", "price": 39.8}, ...],
              "rtd":  [{"ts": "...", "price": 40.1}, ...],
              "dart": [{"ts": "...", "spread": 1.4}, ...]   # DA − avg(RTPD)/hr
            },
            "SP15": {...}, "ZP26": {...}
          },
          "latest": {
            "NP15": {
              "da":   {"ts": "...", "price": 41.2} | null,   # current PT hour
              "rtpd": {"ts": "...", "price": 39.8} | null,   # freshest interval
              "rtd":  {"ts": "...", "price": 40.1} | null
            }, "SP15": {...}, "ZP26": {...}
          },
          "peak": {
            "NP15": {"onpeak_avg": 44.7 | null, "offpeak_avg": 33.1 | null},
            "SP15": {...}, "ZP26": {...}
          }
        }

    Fixed window (v1): no hub, date, or range parameter — the previous + current
    Pacific trade date, computed in America/Los_Angeles. Read-only.
    """
    assert _pool is not None

    # ── Window: previous + current PT trade date ───────────────────────────
    today_pt = _datetime.now(ZoneInfo(MARKET_TZ)).date()
    prev_pt = today_pt - _timedelta(days=1)
    lo_date = prev_pt.isoformat()
    hi_date = today_pt.isoformat()

    # ── One read across all three datasets × three hubs over the PT window ──
    # Non-null filtered in SQL; PT day boundaries reuse the proven fuel-mix
    # pattern (AT TIME ZONE), end-exclusive at (hi + 1 day) so `hi` is included.
    query = """
        SELECT ts, dataset, series, value
        FROM timeseries_values
        WHERE dataset = ANY(%(datasets)s)
          AND series  = ANY(%(hubs)s)
          AND value IS NOT NULL
          AND ts >= (%(lo)s::date)::timestamp AT TIME ZONE %(tz)s
          AND ts <  ((%(hi)s::date) + 1)::timestamp AT TIME ZONE %(tz)s
        ORDER BY dataset, series, ts ASC
    """
    params = {
        "datasets": [m["dataset"] for m in HUB_LMP_MARKETS.values()],
        "hubs": HUB_LMP_HUBS,
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
                f"no CAISO hub-LMP data for {lo_date}..{hi_date} (Pacific). "
                "Verify timeseries_values has the caiso_lmp_da_hourly / "
                "caiso_lmp_rt_15min / caiso_lmp_rt_5min datasets in the window."
            ),
        )

    # ── Partition rows into buckets[market][hub] = [(ts_utc, price), ...] ────
    # Rows arrive ascending, so each series list stays ascending (tail = newest).
    # Null values are already excluded in SQL; the guard keeps the shaping honest
    # if the query ever changes.
    buckets: dict[str, dict[str, list]] = {
        mk: {h: [] for h in HUB_LMP_HUBS} for mk in HUB_LMP_MARKETS
    }
    for r in rows:
        if r["value"] is None:
            continue
        market = _HUB_LMP_DATASET_TO_MARKET.get(r["dataset"])
        if market is None or r["series"] not in buckets[market]:
            continue
        ts_utc = r["ts"].astimezone(_timezone.utc)
        buckets[market][r["series"]].append((ts_utc, float(r["value"])))

    # ── last_ts per market: MAX(ts) among returned rows. The window includes
    # today, so for the live RT feeds this equals the dataset MAX at request
    # time; for DA it is the freshest DA hour present. ────────────────────────
    last_ts: dict[str, str | None] = {}
    for market in HUB_LMP_MARKETS:
        newest = None
        for h in HUB_LMP_HUBS:
            series_rows = buckets[market][h]
            if series_rows and (newest is None or series_rows[-1][0] > newest):
                newest = series_rows[-1][0]
        last_ts[market] = newest.isoformat() if newest is not None else None

    # Current PT hour, UTC-aligned — the key for DA's "latest" schedule read.
    now_pt = _datetime.now(ZoneInfo(MARKET_TZ))
    cur_hour_utc = now_pt.replace(
        minute=0, second=0, microsecond=0
    ).astimezone(_timezone.utc)

    hubs_out: dict[str, dict] = {}
    latest_out: dict[str, dict] = {}
    peak_out: dict[str, dict] = {}

    for hub in HUB_LMP_HUBS:
        da_rows = buckets["da"][hub]
        rtpd_rows = buckets["rtpd"][hub]
        rtd_rows = buckets["rtd"][hub]

        # ── market arrays (prices as plain numbers, ascending) ──────────────
        da_arr = [{"ts": ts.isoformat(), "price": px} for ts, px in da_rows]
        rtpd_arr = [{"ts": ts.isoformat(), "price": px} for ts, px in rtpd_rows]
        rtd_arr = [{"ts": ts.isoformat(), "price": px} for ts, px in rtd_rows]

        # ── DART: hourly DA − avg(RTPD intervals in that hour) ──────────────
        # Bucket RTPD intervals by their clock hour (UTC floor == PT hour start).
        # Emit only hours with a DA price AND ≥1 RTPD interval; the in-progress
        # hour averages over whatever intervals have landed. Sign: + = DA > RT.
        rtpd_by_hour: dict = {}
        for ts, px in rtpd_rows:
            hour_key = ts.replace(minute=0, second=0, microsecond=0)
            rtpd_by_hour.setdefault(hour_key, []).append(px)
        dart_arr = []
        for ts, da_px in da_rows:
            intervals = rtpd_by_hour.get(ts.replace(minute=0, second=0, microsecond=0))
            if not intervals:
                continue
            avg_rtpd = sum(intervals) / len(intervals)
            dart_arr.append({"ts": ts.isoformat(), "spread": round(da_px - avg_rtpd, 2)})

        hubs_out[hub] = {"da": da_arr, "rtpd": rtpd_arr, "rtd": rtd_arr, "dart": dart_arr}

        # ── latest ticker: DA = current PT hour (a schedule); RT = tail ─────
        da_latest = None
        for ts, px in da_rows:
            if ts == cur_hour_utc:
                da_latest = {"ts": ts.isoformat(), "price": px}
                break
        latest_out[hub] = {
            "da": da_latest,
            "rtpd": ({"ts": rtpd_rows[-1][0].isoformat(), "price": rtpd_rows[-1][1]}
                     if rtpd_rows else None),
            "rtd": ({"ts": rtd_rows[-1][0].isoformat(), "price": rtd_rows[-1][1]}
                    if rtd_rows else None),
        }

        # ── peak: current PT trade date on/off-peak averages of the DA curve ─
        # Computed-split path (recon note in the block comment above): average
        # the bare hourly DA series over the NERC on-peak block. A bucket with
        # no rows on the date is null, not a fake zero.
        onpeak_vals, offpeak_vals = [], []
        for ts, px in da_rows:
            ts_pt = ts.astimezone(ZoneInfo(MARKET_TZ))
            if ts_pt.date() != today_pt:
                continue
            (onpeak_vals if _lmp_is_onpeak(ts_pt) else offpeak_vals).append(px)
        peak_out[hub] = {
            "onpeak_avg": round(sum(onpeak_vals) / len(onpeak_vals), 2) if onpeak_vals else None,
            "offpeak_avg": round(sum(offpeak_vals) / len(offpeak_vals), 2) if offpeak_vals else None,
        }

    return {
        "tz": MARKET_TZ,
        "window": {"start": lo_date, "end": hi_date},
        "sources": {
            mk: {"dataset": m["dataset"], "cadence": m["cadence"]}
            for mk, m in HUB_LMP_MARKETS.items()
        },
        "last_ts": last_ts,
        "hubs": hubs_out,
        "latest": latest_out,
        "peak": peak_out,
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/market-clock — the deterministic CAISO market state machine (Ticker v1).
#
# ONE state, computed once, read by every surface (banner ticker, briefs, email
# arc, Paper Desk) so no surface computes its own opinion of whether the DAM has
# published. The state machine itself is pure and lives in market_clock.py; this
# endpoint does only the lake reads it needs and hands them to compute_clock.
#
# Publication detection = "the lake holds the rows": the target trade date is
# tomorrow PT (the auction marching through today's session), and DA_PUBLISHED /
# RT_LIVE are gated on those rows actually being present — the honesty rail.
# The NERC calendar is REUSED from the locked _NERC_HOLIDAY_SET bundle purely as
# block-vocabulary context (Sunday/holiday = off-peak all day); it never
# suppresses a cycle, because the CAISO DAM clears a full 24h every calendar day.
#
# Two tier-1 feeds are read: caiso_lmp_da_hourly (the DA cycle + the SP15 print)
# and caiso_lmp_rt_15min (the live FMM layer). A stale FMM feed, or a DAM overdue
# past its grace, is surfaced as `degraded` naming the feed. DB unavailable -> 503
# (house standard); the clock never blanks for a partial read.
# ═══════════════════════════════════════════════════════════════════════════

# FMM = CAISO Fifteen Minute Market = the RTPD 15-min dataset. The live tape the
# RT_LIVE headline and prints.latest_fmm read from. SP15 is the reference hub.
MARKET_CLOCK_DA_DATASET = "caiso_lmp_da_hourly"
MARKET_CLOCK_FMM_DATASET = "caiso_lmp_rt_15min"
MARKET_CLOCK_REF_HUB = "SP15"
# HE17 (the classic peak marker) = PT start-hour 16. The single on-cycle SP15 DA
# print surfaced when awards are out.
MARKET_CLOCK_PRINT_HE = 17
MARKET_CLOCK_PRINT_START_HOUR = 16
# FMM staleness: RTPD intervals land every 15 min; older than this and the live
# feed is degraded (honesty on the glass).
MARKET_CLOCK_FMM_STALE_AFTER = _timedelta(minutes=60)


def _market_clock_offpeak_all_day(d: _date) -> bool:
    """Block-vocabulary context, REUSING the locked NERC bundle: a date is
    off-peak all day when it is a Sunday or a NERC holiday (no on-peak block
    exists), matching the on/off-peak split _lmp_is_onpeak already encodes."""
    return d.isoweekday() == 7 or d.isoformat() in _NERC_HOLIDAY_SET


@app.get("/api/market-clock")
async def market_clock():
    """
    The single CAISO market state for the ticker chip and every downstream
    surface — deterministic, publication-anchored, zero LLM.

        {
          "state": "RT_LIVE",                     # DA_BIDDING | DA_MARKET_RUNNING
                                                  # | DA_PUBLISHED | RT_LIVE
          "label": "Real-time market live",
          "detail": "FMM SP15 $41.20 · as-of 19:45 PT · DA 2026-07-16 published",
          "trade_date": "2026-07-16",             # the current DA cycle's target
          "next_expected": {"event": "bid_close", "at": "2026-07-16T17:00:00+00:00"},
          "prints": {
            "sp15_da":   {"hub": "SP15", "ts": "...", "price": 40.05, "he": 17} | null,
            "latest_fmm":{"hub": "SP15", "market": "rtpd", "ts": "...", "price": 41.2} | null
          },
          "as_of": "2026-07-16T02:27:30+00:00",
          "degraded": false, "degraded_feeds": [],
          "sources": ["caiso_lmp_da_hourly", "caiso_lmp_rt_15min"]
        }

    Publication detection is the sole authority for DA_PUBLISHED / RT_LIVE — the
    clock can never claim awards the lake does not hold. A stale FMM feed or an
    overdue DAM sets `degraded`. DB unavailable -> 503.
    """
    assert _pool is not None

    now = _utcnow()
    now_pt = now.astimezone(ZoneInfo(MARKET_TZ))
    today_pt = now_pt.date()
    # Target trade date = tomorrow PT: the auction being bid / run / published
    # during today's session.
    target_date = today_pt + _timedelta(days=1)

    # PT-day UTC bounds for the target trade date, and the exact HE17 instant —
    # computed in Python so the query is a plain indexed ts range/equality.
    target_start = _datetime.combine(
        target_date, _time(0, 0), tzinfo=ZoneInfo(MARKET_TZ)
    ).astimezone(_timezone.utc)
    target_end = _datetime.combine(
        target_date + _timedelta(days=1), _time(0, 0), tzinfo=ZoneInfo(MARKET_TZ)
    ).astimezone(_timezone.utc)
    he17_ts = _datetime.combine(
        target_date, _time(MARKET_CLOCK_PRINT_START_HOUR, 0), tzinfo=ZoneInfo(MARKET_TZ)
    ).astimezone(_timezone.utc)

    # One round trip: DA detection (hours present + honest ingest time) for the
    # target date, the on-cycle SP15 DA HE17 print, and the freshest FMM interval.
    query = """
        SELECT
          (SELECT count(*) FROM timeseries_values
             WHERE dataset = %(da)s AND series = %(hub)s
               AND ts >= %(tstart)s AND ts < %(tend)s
               AND value IS NOT NULL)                              AS da_hours,
          (SELECT max(ingested_ts) FROM timeseries_values
             WHERE dataset = %(da)s
               AND ts >= %(tstart)s AND ts < %(tend)s)             AS da_published_at,
          (SELECT value FROM timeseries_values
             WHERE dataset = %(da)s AND series = %(hub)s AND ts = %(he17)s
               AND value IS NOT NULL LIMIT 1)                      AS sp15_da_val,
          (SELECT ts FROM timeseries_values
             WHERE dataset = %(fmm)s AND series = %(hub)s AND value IS NOT NULL
             ORDER BY ts DESC LIMIT 1)                             AS fmm_ts,
          (SELECT value FROM timeseries_values
             WHERE dataset = %(fmm)s AND series = %(hub)s AND value IS NOT NULL
             ORDER BY ts DESC LIMIT 1)                             AS fmm_val
    """
    params = {
        "da": MARKET_CLOCK_DA_DATASET,
        "fmm": MARKET_CLOCK_FMM_DATASET,
        "hub": MARKET_CLOCK_REF_HUB,
        "tstart": target_start,
        "tend": target_end,
        "he17": he17_ts,
    }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                row = await cur.fetchone()
    except Exception as e:
        # DB unavailability -> 503 (house standard; see /health). Fail loud.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    row = row or {}
    da_hours = int(row.get("da_hours") or 0)
    target_published = da_hours >= _mc.PUBLICATION_FLOOR_HOURS

    # ── The on-cycle SP15 DA print (only meaningful once awards are out). ─────
    sp15_da_print = None
    if row.get("sp15_da_val") is not None:
        sp15_da_print = {
            "hub": MARKET_CLOCK_REF_HUB,
            "ts": he17_ts.isoformat(),
            "price": float(row["sp15_da_val"]),
            "he": MARKET_CLOCK_PRINT_HE,
        }

    # ── The live FMM layer + its freshness. ──────────────────────────────────
    latest_fmm = None
    fmm_ts = row.get("fmm_ts")
    degraded_feeds: list[str] = []
    if fmm_ts is not None and row.get("fmm_val") is not None:
        fmm_ts_utc = fmm_ts.astimezone(_timezone.utc)
        latest_fmm = {
            "hub": MARKET_CLOCK_REF_HUB,
            "market": "rtpd",
            "ts": fmm_ts_utc.isoformat(),
            "price": float(row["fmm_val"]),
        }
        if (now - fmm_ts_utc) > MARKET_CLOCK_FMM_STALE_AFTER:
            degraded_feeds.append("rtpd")
    else:
        # No live FMM at all is itself a stale/absent tier-1 feed.
        degraded_feeds.append("rtpd")

    # ── DAM overdue: past bid close + expected publish + grace, still no rows. ─
    overdue_after = (
        _datetime.combine(today_pt, _mc.DA_EXPECTED_PUBLISH_PT, tzinfo=ZoneInfo(MARKET_TZ))
        + _mc.DA_PUBLISH_GRACE
    )
    if not target_published and now_pt >= overdue_after:
        degraded_feeds.append("da")

    da_published_at = row.get("da_published_at")
    if da_published_at is not None:
        da_published_at = da_published_at.astimezone(_timezone.utc)

    clock = _mc.compute_clock(
        now,
        target_date=target_date,
        target_published=target_published,
        da_published_at=da_published_at,
        sp15_da_print=sp15_da_print,
        latest_fmm=latest_fmm,
        target_is_offpeak_all_day=_market_clock_offpeak_all_day(target_date),
        degraded_feeds=degraded_feeds,
    )

    return clock.as_dict(
        sources=[MARKET_CLOCK_DA_DATASET, MARKET_CLOCK_FMM_DATASET]
    )


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/pnode-lmp — latest complete pnode-LMP snapshot (D-07-05-09, PR-2)
#
# ONE concern: serve the newest COMPLETE nodal price picture for a single CAISO
# market as a compact columnar payload the live-nodal-LMP map (PR-3, separate
# repo) joins client-side onto atlas_pnodes.geojson by pnode_id. PRICES ONLY —
# no coordinates, no geometry, ever (the ratified geometry/price split; geometry
# shipped in PR-1 to the pantry).
#
# SOURCE: atlas_pnode_lmp_snapshot (Neon hot tier, 7-day retention, dispatch-only
# writer with ON CONFLICT DO NOTHING). PK is
#   (pnode_id, market, market_date, market_hour, market_interval)
# — snapshot_vintage is NOT in the key, and DO-NOTHING means a re-pulled interval
# keeps its ORIGINAL vintage. So MAX(snapshot_vintage) selects "rows from the
# newest pull," NOT "the newest complete picture" — it is the wrong selector.
#
# SELECTOR (D-07-05-09 adjudication; two-step reshape D-07-17): the latest
# market INSTANT, where an instant is one (market_date, market_hour,
# market_interval). "Latest" = date DESC, hour DESC, interval DESC NULLS LAST.
# The selection is now TWO index-driven steps instead of one COUNT(DISTINCT)
# scan-and-external-sort (which read all ~10M rows/market and spilled ~685 MB to
# disk — 11.5 s warm, ~46.8 s observed end-to-end):
#   1. latest instant  — ORDER BY ... LIMIT 1, a backward index-only scan (ms).
#   2. fetch its rows  — an index range scan (~14.8k rows, sub-100 ms).
# COMPLETENESS is now an absolute row-count gate: the served instant must carry
# >= PNODE_LMP_COMPLETENESS_FLOOR * PNODE_LMP_EXPECTED_NODES rows. If the latest
# instant is thin (a caught mid-dispatch partial write), fall back ONE instant
# and serve whichever of the two is fuller — never silently thin. The served
# count, expected universe, and whether a fallback happened are echoed in the
# response (pnode_count / expected_pnode_count / complete / fell_back) and
# logged. Because one response is exactly one instant, market_date/hour/interval
# are true top-level scalars (no per-node scalar variance is possible).
#
# STALENESS BY DESIGN: the writer has no standing schedule (prove-before-
# automate), so the hot tier is legitimately stale between dispatches. Age is
# NOT an error — the latest complete instant is served regardless of how old it
# is, and the payload's market_date/hour/interval + feed_generated_at ARE the
# staleness signal the dashboard renders. 503 is reserved for "no complete
# instant exists at all" (empty or fully expired table), never mere staleness.
#
# snapshot_vintage is DEMOTED to informational metadata: reported as MAX(vintage)
# among the served rows (the newest pull that touched this instant). It may span
# more than one vintage within an instant (different pnodes pulled in different
# dispatches, each retaining its own vintage) — feed_generated_at, not vintage,
# is the authoritative staleness signal.
#
# ERROR CONTRACT (deliberate fork from caiso-hub-lmp's 404, per adjudication):
#   * unknown market   -> 400 with the allowed values (a client input error).
#   * no instant at all -> 503 (a server-side data-availability condition: feed
#                          failure / expired table — the table holds ZERO rows
#                          for the market), so the map can render an outage state
#                          rather than an empty map. Never an empty-200.
# NOTE (D-07-17 change): 503 now fires only when NO instant exists. A present-
# but-thin instant is SERVED with complete=false (loud, not silent) rather than
# 503'd — "never silently thin" means surface the thin data + the flag, so the
# map can render a partial-coverage state instead of a blank outage.
# NULL price components pass through as JSON null (absence is not zero) — and,
# because the payload is columnar/order-aligned, nulls MUST be kept to preserve
# array alignment. All six arrays are built from one row walk and asserted equal
# length before returning; any skew -> 500, no partial payload.
# ═══════════════════════════════════════════════════════════════════════════

# Stored market symbols (the `market` column's live values, recon 2026-07-05).
# These are the query-param AND payload values; display naming (e.g. "FMM") is a
# dashboard concern, not this endpoint's. Default = RTD: the fastest cadence, the
# "live" market; DAM/RTPD by explicit ?market=.
PNODE_LMP_MARKETS = ("RTD", "RTPD", "DAM")
_PNODE_LMP_MARKET_SET = frozenset(PNODE_LMP_MARKETS)
PNODE_LMP_DEFAULT_MARKET = "RTD"

# Completeness floor for the instant selector: a served instant must carry
# >= 90% of the market's expected node universe, guarding against serving a
# half-written mid-dispatch instant. Applied as `floor * PNODE_LMP_EXPECTED_NODES`
# (see below) — an absolute row-count gate, NOT the old table-wide COUNT(DISTINCT)
# ratio (that shape scanned all ~10M rows per market and externally sorted them;
# the two-step query below is index-only — D-07-17 load-perf arc).
PNODE_LMP_COMPLETENESS_FLOOR = 0.90

# Expected nodal universe per market — the row count a COMPLETE instant carries.
# All three CAISO markets price the same node set (14,827 as of 2026-07-17; was
# 14,417 at the D-07-05 recon — it drifts upward as nodes are added), so one
# constant covers all three. This is a FLOOR-GUARD base, not an exact contract:
# a complete instant that EXCEEDS it still serves; only a materially thin instant
# (a caught mid-dispatch partial write) trips the one-interval fallback. The
# response echoes it as `expected_pnode_count` so the client can render coverage.
# Captain-tunable; split into a per-market dict if the shared-universe fact ever
# stops holding.
PNODE_LMP_EXPECTED_NODES = 14827

# Price component columns emitted as order-aligned arrays (sorted by pnode_id).
PNODE_LMP_COMPONENTS = ("lmp", "energy", "congestion", "loss", "ghg")

# Diagnostics: the completeness gate logs actual-vs-expected node counts and
# every fallback/thin serve, so a partial-write event is visible in Railway logs
# without changing the response contract. Railway captures stdout/stderr.
_pnode_lmp_log = logging.getLogger("energylake.pnode_lmp")

# ── Two-step interval-targeted selection (replaces the scan-and-sort shape) ──
# The (market, market_date, market_hour, market_interval) composite index
# answers all three of these without touching the market's history:
#
#   step 1  latest instant   — backward index-only scan, stops at the first
#                              instant (LIMIT 1); milliseconds.
#   step 2  fetch instant    — bitmap index range scan of one instant; ~14.8k
#                              rows. IS NOT DISTINCT FROM keeps a NULL interval
#                              matching itself (hourly markets).
#   fallback  prev instant   — newest instant strictly BEFORE a given one, same
#                              backward index scan. Run ONLY when step 2 comes
#                              back thin. ROW-value comparison stays on the
#                              composite index; NULLS-LAST ordering means a NULL
#                              interval bound walks to the previous hour (correct
#                              for hourly DAM, whose interval is null/0).
_PNODE_LMP_LATEST_INSTANT_SQL = """
    SELECT market_date, market_hour, market_interval
    FROM atlas_pnode_lmp_snapshot
    WHERE market = %(market)s
    ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
    LIMIT 1
"""

_PNODE_LMP_PREV_INSTANT_SQL = """
    SELECT market_date, market_hour, market_interval
    FROM atlas_pnode_lmp_snapshot
    WHERE market = %(market)s
      AND (market_date, market_hour, market_interval)
          < (%(md)s, %(mh)s, %(mi)s)
    ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
    LIMIT 1
"""

_PNODE_LMP_FETCH_INSTANT_SQL = """
    SELECT pnode_id, lmp, energy, congestion, loss, ghg,
           market_date, market_hour, market_interval,
           snapshot_vintage, feed_generated_at
    FROM atlas_pnode_lmp_snapshot
    WHERE market = %(market)s
      AND market_date = %(md)s
      AND market_hour = %(mh)s
      AND market_interval IS NOT DISTINCT FROM %(mi)s
    ORDER BY pnode_id ASC
"""


def _pnode_utc_iso(ts) -> str | None:
    """timestamptz -> UTC ISO string; None passes through. Matches the repo's
    convention of emitting timestamps in UTC and localizing on the client."""
    return ts.astimezone(_timezone.utc).isoformat() if ts is not None else None


@app.get("/api/atlas/pnode-lmp")
async def atlas_pnode_lmp(
    response: Response,
    market: str = Query(
        default=PNODE_LMP_DEFAULT_MARKET,
        description=(
            "CAISO market symbol. One of "
            f"{', '.join(PNODE_LMP_MARKETS)} (case-insensitive). Default "
            f"{PNODE_LMP_DEFAULT_MARKET} (fastest cadence, the 'live' market). "
            "Unknown values are rejected with 400."
        ),
    ),
):
    """
    Latest pnode-LMP snapshot for one CAISO market — prices only.

    Serves the newest market instant (market_date, market_hour, market_interval)
    as a compact columnar payload the nodal-LMP map joins client-side onto the
    pnode geometry by pnode_id. No coordinates, no geometry. Selection is two
    index-driven steps (latest instant, then fetch its rows) — see the module
    header for why this replaced the old COUNT(DISTINCT) scan-and-sort.

    Columnar payload — all six arrays are order-aligned and sorted by pnode_id:
        {
          "market": "RTD",
          "snapshot_vintage": "2026-07-05T13:45:50+00:00",
          "market_date": "2026-07-05", "market_hour": 9, "market_interval": 8,
          "feed_generated_at": "2026-07-05T...+00:00",
          "pnode_count": 14827, "expected_pnode_count": 14827,
          "complete": true, "fell_back": false,
          "pnode_id":   ["...", ...],
          "lmp":        [41.2, ...],  "energy":     [40.0, ...],
          "congestion": [1.1, ...],   "loss":       [0.1, ...],
          "ghg":        [null, ...]
        }

    completeness/fallback meta:
      * complete    — served node count >= floor * expected universe.
      * fell_back   — the latest instant was a partial mid-dispatch write, so
                      the prior (fuller) instant was served instead. When true,
                      market_date/hour/interval already describe that prior
                      instant. A thin instant is served with complete=false, not
                      hidden — "never silently thin".
      * expected_pnode_count — the universe the floor is measured against.

    Staleness is expected (dispatch-only writer): the latest instant is served
    regardless of age; market_date/hour/interval + feed_generated_at are the
    staleness signal. NULL components pass through as JSON null.

    Sentinel note for the consumer (PR-3): some rows carry all-zero
    lmp/energy/congestion/loss as a no-DA-price sentinel (observed in DAM), NOT
    real prices, and status_flag does not discriminate them. This endpoint
    serves rows faithfully — no filtering, no nulling — so display honesty
    (rendering the no-price state) is the map client's contract, not this
    endpoint's.

    Errors: unknown market -> 400 (allowed values echoed); no instant at all
    (empty/expired table) -> 503; internal array skew -> 500.
    """
    assert _pool is not None

    # ── Validate market (case-insensitive) -> 400 with allowed values ───────
    market_norm = market.strip().upper()
    if market_norm not in _PNODE_LMP_MARKET_SET:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown market '{market}'; "
                f"valid markets are {list(PNODE_LMP_MARKETS)}."
            ),
        )

    # ── Two-step, index-driven selection (see module header). One connection,
    # sequential reads: latest instant (ms) -> its rows (~14.8k). A thin latest
    # instant (partial mid-dispatch write) triggers a one-instant fallback, and
    # we serve whichever of the two is fuller. `min_nodes` is the absolute
    # row-count floor; `fell_back` records whether the prior instant was served.
    min_nodes = PNODE_LMP_COMPLETENESS_FLOOR * PNODE_LMP_EXPECTED_NODES

    def _fetch_params(inst) -> dict:
        return {
            "market": market_norm,
            "md": inst["market_date"],
            "mh": inst["market_hour"],
            "mi": inst["market_interval"],
        }

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                # Step 1 — latest instant (backward index-only scan, LIMIT 1).
                await cur.execute(_PNODE_LMP_LATEST_INSTANT_SQL, {"market": market_norm})
                latest = await cur.fetchone()
                if latest is None:
                    rows, fell_back = [], False
                else:
                    # Step 2 — every row of the latest instant (index range).
                    await cur.execute(_PNODE_LMP_FETCH_INSTANT_SQL, _fetch_params(latest))
                    rows = await cur.fetchall()
                    fell_back = False

                    # Partial-write guard: a latest instant below the floor is a
                    # mid-dispatch write. Fall back ONE instant; keep the fuller.
                    if len(rows) < min_nodes:
                        await cur.execute(
                            _PNODE_LMP_PREV_INSTANT_SQL,
                            {
                                "market": market_norm,
                                "md": latest["market_date"],
                                "mh": latest["market_hour"],
                                "mi": latest["market_interval"],
                            },
                        )
                        prev = await cur.fetchone()
                        if prev is not None:
                            await cur.execute(
                                _PNODE_LMP_FETCH_INSTANT_SQL, _fetch_params(prev)
                            )
                            prev_rows = await cur.fetchall()
                            if len(prev_rows) > len(rows):
                                rows, fell_back = prev_rows, True
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"query failed: {e}")

    # ── No instant at all -> 503 (data unavailable, NOT a missing resource).
    # Staleness alone never reaches here; this is an empty/expired table only.
    # (A present-but-thin instant is served below with complete=false, never
    # 503'd.) The map client distinguishes this from the 400 to render an outage.
    if not rows:
        raise HTTPException(
            status_code=503,
            detail=(
                f"no {market_norm} pnode-LMP instant available. The snapshot hot "
                "tier may be empty or expired (7-day retention, dispatch-only "
                "writer) — a fresh snapshot dispatch is required."
            ),
        )

    # ── Shape columnar. One row walk builds every array so they stay aligned;
    # the instant scalars are constant across rows (one instant by construction)
    # and read from the first row. NULL components pass through as JSON null.
    pnode_id: list = []
    cols: dict[str, list] = {c: [] for c in PNODE_LMP_COMPONENTS}
    for r in rows:
        pnode_id.append(r["pnode_id"])
        for c in PNODE_LMP_COMPONENTS:
            v = r[c]
            cols[c].append(float(v) if v is not None else None)

    # ── Array-alignment guard: unequal lengths -> 500, no partial payload. ──
    n = len(pnode_id)
    if any(len(cols[c]) != n for c in PNODE_LMP_COMPONENTS):
        raise HTTPException(
            status_code=500,
            detail="internal error: pnode-LMP column arrays are misaligned.",
        )

    # snapshot_vintage / feed_generated_at: MAX among served rows (the newest
    # pull / feed generation that touched this instant). Vintage may span the
    # instant; feed_generated_at is the authoritative staleness signal.
    first = rows[0]
    md = first["market_date"]
    vintages = [r["snapshot_vintage"] for r in rows if r["snapshot_vintage"] is not None]
    feeds = [r["feed_generated_at"] for r in rows if r["feed_generated_at"] is not None]
    max_vintage = max(vintages) if vintages else None
    max_feed = max(feeds) if feeds else None

    # ── Completeness verdict + diagnostics. `complete` is the served instant
    # clearing the absolute floor; log actual-vs-expected always, and escalate to
    # a warning when we serve thin (visible in Railway logs; contract unchanged).
    complete = n >= min_nodes
    _pnode_lmp_log.info(
        "pnode-lmp market=%s instant=%s/%s/%s nodes=%d expected~%d complete=%s fell_back=%s",
        market_norm, first["market_date"], first["market_hour"],
        first["market_interval"], n, PNODE_LMP_EXPECTED_NODES, complete, fell_back,
    )
    if not complete:
        _pnode_lmp_log.warning(
            "pnode-lmp SERVED THIN: market=%s nodes=%d < floor=%d "
            "(%.0f%% of expected %d); fell_back=%s",
            market_norm, n, int(min_nodes),
            PNODE_LMP_COMPLETENESS_FLOOR * 100, PNODE_LMP_EXPECTED_NODES, fell_back,
        )

    # Short cache: the feed is dispatch-only, so a 60s window relieves repeated
    # multi-MB reads without hiding a fresh dispatch for long. Endpoint-local.
    response.headers["Cache-Control"] = "max-age=60"

    return {
        "market": market_norm,
        "snapshot_vintage": _pnode_utc_iso(max_vintage),
        "market_date": md.isoformat() if md is not None else None,
        "market_hour": first["market_hour"],
        "market_interval": first["market_interval"],
        "feed_generated_at": _pnode_utc_iso(max_feed),
        "pnode_count": n,
        "expected_pnode_count": PNODE_LMP_EXPECTED_NODES,
        "complete": complete,
        "fell_back": fell_back,
        "pnode_id": pnode_id,
        "lmp": cols["lmp"],
        "energy": cols["energy"],
        "congestion": cols["congestion"],
        "loss": cols["loss"],
        "ghg": cols["ghg"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/pnode-history — 7-day price history for ONE pnode (D-07-08, PR-1)
#
# ONE concern: serve the raw price-component history for a single CAISO pricing
# node from the hot tier, so the map (energylake-dashboard PR-2) can draw a
# click-to-inspect sparkline. Prices only — no geometry, ever (same geometry/
# price split as the sibling /api/atlas/pnode-lmp).
#
# SOURCE: atlas_pnode_lmp_snapshot (the SAME Neon hot tier the sibling reads;
# 7-day retention, dispatch-only writer). This endpoint is a PURE READER: a
# plain range scan over one pnode + one market + a day window, sorted ascending
# by instant. NO completeness gating (unlike the live endpoint) — history's job
# is to show what exists, and partial trailing instants are honest, timestamped
# data. NO server-side thinning, NO server-derived DART (the client computes
# DART = FMM − DAM, ratified positive = RT over DA).
#
# TIME AXIS IS A CLIENT CONCERN (captain-adjudicated 2026-07-08): the hot tier
# carries NO interval-start timestamptz — only market_date / market_hour /
# market_interval (plus ingestion-time feed_generated_at / snapshot_vintage /
# ingested_at, which are the WRONG axis). Rather than bake HE-vs-HB / DST
# assumptions into the API, this endpoint emits those three market-coordinate
# columns verbatim as parallel arrays and the dashboard derives ISO instants
# with its existing HE/interval conventions. The API stays a pure reader.
#
# MARKET TOKENS: the stored `market` symbols RTD | RTPD | DAM, identical to the
# sibling. "FMM" is a dashboard display name only and is rejected with 400 —
# callers send RTPD on the wire.
#
# HOURS: only 24 or 168 (7 days). Coarsened server-side to a calendar-day filter
# (hours/24 days) matching the validated index predicate (Bitmap Index Scan on
# the PK, ~3.6ms) — sub-day precision is impossible without a market timestamp
# and is the client's axis job anyway.
#
# KNOWN / ERROR CONTRACT:
#   * blank pnode_id / bad market / hours∉{24,168} -> 400 (client input error).
#   * unknown pnode_id (absent from the hot-tier universe) -> 400.
#   * known pnode_id with no rows in the window/market -> 200 with EMPTY arrays
#     and known:true (honest: a valid node, simply no history here — never a
#     404/empty pretending, never a fabricated flat line).
#   * DB unavailable -> 503 with the house standard body (see /health). Fail
#     loud — never a 200 with silently truncated data.
# SENTINEL HONESTY: DAM all-zero-component rows pass through as-is (the client
# renders the no-price state); the API never filters or nulls them. NULL
# components pass through as JSON null; columnar arrays are order-aligned and
# asserted equal-length before returning (any skew -> 500, no partial payload).
# ═══════════════════════════════════════════════════════════════════════════

# History shares the sibling's stored market symbols verbatim (RTD | RTPD | DAM)
# and its price-component columns. "FMM" stays a dashboard display name — it is
# NOT accepted here; callers send RTPD. Single source of truth = the LMP consts.
PNODE_HISTORY_MARKETS = PNODE_LMP_MARKETS
_PNODE_HISTORY_MARKET_SET = _PNODE_LMP_MARKET_SET
PNODE_HISTORY_DEFAULT_MARKET = PNODE_LMP_DEFAULT_MARKET  # RTD, matching the sibling
PNODE_HISTORY_COMPONENTS = PNODE_LMP_COMPONENTS

# Only two look-back windows are accepted; any other value is a 400. Each is
# coarsened to a whole-day filter (hours // 24 days) because the hot tier has no
# sub-day market timestamp — the client derives the fine time axis.
PNODE_HISTORY_ALLOWED_HOURS = (24, 168)
PNODE_HISTORY_DEFAULT_HOURS = 24


@app.get("/api/atlas/pnode-history")
async def atlas_pnode_history(
    response: Response,
    pnode_id: str = Query(
        default="",
        description=(
            "CAISO pricing-node id (e.g. KRNCNYN_6_N001). Required; a blank or "
            "unknown pnode_id is rejected with 400."
        ),
    ),
    market: str = Query(
        default=PNODE_HISTORY_DEFAULT_MARKET,
        description=(
            "CAISO market symbol. One of "
            f"{', '.join(PNODE_HISTORY_MARKETS)} (case-insensitive). Default "
            f"{PNODE_HISTORY_DEFAULT_MARKET}. 'FMM' is a display name only and is "
            "rejected with 400 — send RTPD on the wire."
        ),
    ),
    hours: int = Query(
        default=PNODE_HISTORY_DEFAULT_HOURS,
        description=(
            "Look-back window in hours. Only 24 or 168 (7 days) are accepted; any "
            "other value is rejected with 400. Coarsened server-side to a "
            "calendar-day filter (hours/24 days) — the hot tier carries no "
            "sub-day market timestamp, so the client derives the time axis."
        ),
    ),
):
    """
    Raw price-component history for one CAISO pnode from the 7-day hot tier.

    A pure range scan over one pnode + one market + a day window, sorted
    ascending by instant. Columnar payload — every array is order-aligned:
        {
          "pnode_id": "KRNCNYN_6_N001", "market": "RTD",
          "hours": 24, "known": true, "row_count": 13,
          "market_date":     ["2026-07-05", ...],
          "market_hour":     [9, ...],
          "market_interval": [8, ...],   # null for DAM (hourly)
          "lmp":        [41.2, ...],  "energy":     [40.0, ...],
          "congestion": [1.1, ...],   "loss":       [0.1, ...],
          "ghg":        [null, ...]
        }

    The three market-coordinate arrays (market_date / market_hour /
    market_interval) mirror the sibling's exposed fields verbatim; the client
    builds the ISO time axis from them (the hot tier has no market timestamp).
    NULL components pass through as JSON null; DAM all-zero sentinel rows pass
    through as-is (no-price is a client render concern).

    Errors: blank/unknown pnode_id, bad market, or hours∉{24,168} -> 400; a
    known pnode with no rows in the window -> 200 with empty arrays and
    known:true; DB unavailable -> 503; internal array skew -> 500.
    """
    assert _pool is not None

    # ── Validate pnode_id (blank -> 400; unknown pnode handled after the read).
    pid = pnode_id.strip()
    if not pid:
        raise HTTPException(status_code=400, detail="missing pnode_id.")

    # ── Validate market (case-insensitive) -> 400 with allowed values ─────────
    market_norm = market.strip().upper()
    if market_norm not in _PNODE_HISTORY_MARKET_SET:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown market '{market}'; valid markets are "
                f"{list(PNODE_HISTORY_MARKETS)} ('FMM' is a display name — "
                "send RTPD)."
            ),
        )

    # ── Validate hours -> only 24 or 168 -> 400 otherwise ─────────────────────
    if hours not in PNODE_HISTORY_ALLOWED_HOURS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unsupported hours={hours}; valid values are "
                f"{list(PNODE_HISTORY_ALLOWED_HOURS)}."
            ),
        )
    days = hours // 24

    # ── One read, one round trip. `known` is a universe-membership probe (does
    # this pnode appear ANYWHERE in the hot tier, any market/date within
    # retention?); `hist` is the range scan for the requested market + day
    # window. LEFT JOIN from the single known-row to hist means: if hist has
    # rows we get one row per instant (each carrying is_known=true); if hist is
    # empty we still get ONE sentinel row carrying is_known + all-NULL history
    # columns — which is exactly what distinguishes an unknown pnode (400) from
    # a known pnode with no rows in this window (200, empty arrays). No
    # completeness gating; ordered ascending by instant in SQL. market_interval
    # is NULL for DAM, so it sorts NULLS LAST harmlessly (one row per hour).
    query = """
        WITH known AS (
            SELECT EXISTS (
                SELECT 1 FROM atlas_pnode_lmp_snapshot WHERE pnode_id = %(pnode_id)s
            ) AS is_known
        ),
        hist AS (
            SELECT market_date, market_hour, market_interval,
                   lmp, energy, congestion, loss, ghg
            FROM atlas_pnode_lmp_snapshot
            WHERE pnode_id = %(pnode_id)s
              AND market = %(market)s
              AND market_date >= (CURRENT_DATE - %(days)s)
        )
        SELECT k.is_known,
               h.market_date, h.market_hour, h.market_interval,
               h.lmp, h.energy, h.congestion, h.loss, h.ghg
        FROM known k
        LEFT JOIN hist h ON TRUE
        ORDER BY h.market_date ASC NULLS LAST,
                 h.market_hour ASC NULLS LAST,
                 h.market_interval ASC NULLS LAST
    """
    params = {"pnode_id": pid, "market": market_norm, "days": days}

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
    except Exception as e:
        # DB unavailability -> 503 with the house standard body (see /health).
        # Fail loud; never a 200 with truncated data.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # ── `known` is constant across rows (one probe); the known CTE always yields
    # a row, so `rows` is never empty. Real instants carry a non-NULL
    # market_date (the PK); the empty-window LEFT JOIN sentinel carries
    # market_date IS NULL.
    is_known = bool(rows[0]["is_known"]) if rows else False
    hist_rows = [r for r in rows if r["market_date"] is not None]

    # Unknown pnode (absent from the universe) AND no rows -> 400. A known pnode
    # with no rows in the window -> 200 with empty arrays (honest: valid node,
    # no history here) — handled by falling through with empty hist_rows.
    if not hist_rows and not is_known:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown pnode_id '{pid}'; it is not present in the pnode-LMP "
                "hot tier (7-day retention)."
            ),
        )

    # ── Shape columnar. One row walk builds every array so they stay aligned.
    # market_date -> ISO date string; market_hour/market_interval pass through
    # (interval is NULL for DAM). NULL price components pass through as JSON null.
    market_date: list = []
    market_hour: list = []
    market_interval: list = []
    cols: dict[str, list] = {c: [] for c in PNODE_HISTORY_COMPONENTS}
    for r in hist_rows:
        md = r["market_date"]
        market_date.append(md.isoformat() if md is not None else None)
        market_hour.append(r["market_hour"])
        market_interval.append(r["market_interval"])
        for c in PNODE_HISTORY_COMPONENTS:
            v = r[c]
            cols[c].append(float(v) if v is not None else None)

    # ── Array-alignment guard: any skew -> 500, no partial payload. ───────────
    n = len(market_date)
    if (
        len(market_hour) != n
        or len(market_interval) != n
        or any(len(cols[c]) != n for c in PNODE_HISTORY_COMPONENTS)
    ):
        raise HTTPException(
            status_code=500,
            detail="internal error: pnode-history column arrays are misaligned.",
        )

    # Short cache: same 60s window as the live endpoint (dispatch-only feed).
    response.headers["Cache-Control"] = "max-age=60"

    return {
        "pnode_id": pid,
        "market": market_norm,
        "hours": hours,
        # Rows present ⟹ the pnode is in the universe; keep the probe result
        # too so an empty-but-valid window still reports known:true.
        "known": bool(is_known or hist_rows),
        "row_count": n,
        "market_date": market_date,
        "market_hour": market_hour,
        "market_interval": market_interval,
        "lmp": cols["lmp"],
        "energy": cols["energy"],
        "congestion": cols["congestion"],
        "loss": cols["loss"],
        "ghg": cols["ghg"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/constraints — CAISO binding-constraint blotter league table (D-07-11)
#
# ONE concern: the "which constraints hurt most, lately" league table behind the
# Atlas constraint blotter (dashboard half on branch claude/new-session-lru22r).
# One market, one look-back window -> the top-100 constraints by how many hours
# they bound, each with its worst shadow price, its average shadow price WHILE
# bound, when it last bound, and a per-day sparkline of binding hours.
#
# SOURCE (schema-read first, 2026-07-11 — the task's assumed table names
# caiso_binding_constraints_{dam,rtm} do NOT exist; the real shape is one raw
# table + one daily rollup, market is a COLUMN not a table suffix):
#   caiso_binding_constraints        raw interval grain (market, constraint_id,
#                                    constraint_name, constraint_type, ts,
#                                    shadow_price, meta, ingested_ts)
#   caiso_binding_constraints_daily  per (trade_date, market, constraint_id)
#                                    rollup: hours_binding, intervals_binding,
#                                    max/min/mean/max_abs_shadow_price,
#                                    first_binding_ts, last_binding_ts
# The daily rollup IS this endpoint's table: a league table is a daily rollup,
# and its per-trade_date hours_binding is exactly the `trend` sparkline. Markets
# are stored as DAM | RTM (verified DISTINCT).
#
# KEY = constraint_id, NOT constraint_name. constraint_name is nullable and
# non-unique ("Base Case" recurs across different physical constraints), so the
# response `constraint` string is the stable CAISO constraint_id — which is also
# what the Hopland-Cloverdale cross-check keys on (HPLND JT -> CLVRDLJT lives in
# the id, not the name).
#
# WINDOW / TREND AXIS: window ∈ {1d,7d,30d,90d} -> N days. The axis is anchored
# on the latest available trade_date for the market (NOT wall-clock today),
# because the daily rollup legitimately lags the current trading day by ~1 day —
# a today-anchored 1d window would always be empty. So the window is "the last N
# days of data we have," dates [anchor-(N-1) .. anchor], and `stale` (below)
# independently flags if that data itself is old. generate_series builds the full
# N-day axis so every constraint's `trend` is exactly N entries, 0-filled and
# ordered oldest->newest.
#
# AGGREGATION (windowed, per constraint):
#   hours_bound            = SUM(hours_binding)                    (rounded int)
#   max_shadow             = MAX(max_shadow_price)
#   avg_shadow_when_bound  = SUM(mean_shadow_price * intervals_binding)
#                            / SUM(intervals_binding)   -- interval-weighted, so
#                            it equals the true mean of shadow_price over every
#                            binding interval in the window (a plain AVG of the
#                            daily means would misweight short/long days).
#   last_bound_ts          = MAX(last_binding_ts)
#   trend[d]               = that day's hours_binding (0 if it didn't bind)
# Sorted hours_bound DESC (constraint_id tiebreak for determinism), cap 100.
# hours are reported as rounded ints per the response contract; DAM is hourly so
# these are whole by nature, RTM (5-min) is rounded from fractional interval-hours.
#
# STALE / FAIL-HONEST: `as_of` = MAX(last_binding_ts) across the window. stale is
# true when there are NO rows at all OR as_of is older than 48h. Empty/stale data
# is reported faithfully (stale:true, rows possibly empty) — never fabricated.
#   * malformed market or window        -> 400 (terse message, allowed values).
#   * DB unavailable                    -> 503 (house standard; fail loud).
#   * internal trend-axis length skew   -> 500 (no partial payload).
#
# CACHE: in-process, 5-min TTL, keyed (market, window). The feed updates at most
# a few times an hour, so a 5-min window collapses repeated 90d scans to one read
# without hiding fresh data for long. Lock-free: the event loop is single-
# threaded, and a rare double-fill just recomputes and last-write-wins (idempotent).
# ═══════════════════════════════════════════════════════════════════════════

# ── MARKET LABEL TRANSITION (pantry migration 072, not yet applied) ──────────
# The lake is relabeling binding-constraint market values. TODAY the daily rollup
# stores DAM | RTM, where 'RTM' is factually RTD-grain (5-min) data. Migration 072
# will UPDATE market='RTM' -> 'RTD' and widen the CHECK to (DAM, RTPD, RTD); RTPD
# (the FMM settlement lane) rows arrive later still. This endpoint is hardened to
# straddle that relabel with ZERO downtime — it ships & deploys BEFORE 072 runs:
#
#   * Accepted wire tokens (case-insensitive): DAM | RTD | RTM. RTM is a
#     DEPRECATED ALIAS that resolves to the canonical 'RTD' label. Anything else
#     is rejected with 400, exactly as before.
#   * The RESOLVED label is what the response echoes and the cache keys on, so an
#     RTM request and an RTD request share one cache entry and never diverge.
#   * The SQL is label-agnostic: RTD filters market = ANY(['RTD','RTM']) so it
#     returns correct data BOTH before and after 072 UPDATEs the stored symbol.
#     DAM is a single stable label, unchanged.
#
# Accepted wire tokens. Query-param values; case-insensitive on the wire. Note
# these are the ACCEPTED tokens, not the stored symbols — see the resolve/db-label
# maps below. (RTPD is intentionally NOT here yet — prepared but rejected until
# RTPD rows exist in the lake; activation is a one-line uncomment in each map.)
ATLAS_CONSTRAINT_MARKETS = ("DAM", "RTD", "RTM")
_ATLAS_CONSTRAINT_MARKET_SET = frozenset(ATLAS_CONSTRAINT_MARKETS)

# Wire token -> canonical (resolved) market label. RTM is the deprecated alias for
# RTD; DAM and RTD map to themselves. The resolved label is the response echo and
# the cache-key market component.
_ATLAS_CONSTRAINT_MARKET_RESOLVE = {
    "DAM": "DAM",
    "RTD": "RTD",
    "RTM": "RTD",   # deprecated alias -> canonical RTD
    # "RTPD": "RTPD",   # activate with the db-label entry below when RTPD lands
}

# Resolved label -> the set of stored `market` symbols carrying its data during
# the transition. RTD spans BOTH the new 'RTD' and the legacy 'RTM' so the query
# is correct before AND after migration 072 (which UPDATEs 'RTM' -> 'RTD'); once
# 072 has run the 'RTM' element is simply an empty match. DAM is a single symbol.
_ATLAS_CONSTRAINT_DB_LABELS = {
    "DAM": ("DAM",),
    "RTD": ("RTD", "RTM"),
    # RTPD is prepared, NOT exposed. Uncomment this line AND the resolve entry
    # above AND add "RTPD" to ATLAS_CONSTRAINT_MARKETS once RTPD (FMM settlement)
    # rows exist in the lake — the query shape is identical (single-label filter).
    # "RTPD": ("RTPD",),
}

# Accepted look-back windows -> whole-day span. Any other value is a 400.
ATLAS_CONSTRAINT_WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
ATLAS_CONSTRAINT_DEFAULT_WINDOW = "7d"

# League-table cap and the staleness threshold (no row newer than this -> stale).
ATLAS_CONSTRAINT_ROW_CAP = 100
ATLAS_CONSTRAINT_STALE_AFTER = _timedelta(hours=48)

# In-process response cache: (market, window) -> (monotonic_stamp, payload).
_ATLAS_CONSTRAINTS_CACHE_TTL = 300.0  # 5 minutes
_atlas_constraints_cache: dict[tuple[str, str], tuple[float, dict]] = {}


# Resolution families that carry USABLE map geometry: a transmission line
# (line_ids -> atlas_tx_lines), a substation point (sub_id), or a ratified
# gazetteer landmark (landmark_id). These are the "covered" set the coverage
# stats count. 'approx' is a low-confidence substation guess (real sub_id, but
# the map draws it as approximate, not fact); 'unmatched' has nothing to point at.
ATLAS_GEOMETRY_LPL = ("line", "point", "landmark")


def _atlas_geometry_ref(
    resolution, line_ids, sub_id, landmark_id, confidence, match_method
) -> Optional[dict]:
    """Compact geometry pointer bundle for one constraint, or None when there is
    no usable geometry. Pure — unit-tested, and shared by the blotter's per-row
    `geometry_ref` and the geometry endpoint's rows.

    Returns None when the constraint is absent from the newest geometry build
    (`resolution` is None) OR is explicitly 'unmatched' (the resolver found
    nothing to point at) — so `geometry_ref: null` means exactly "no geometry to
    draw." Otherwise carries the resolution plus the pointers the map joins
    client-side: `line_ids` -> atlas_tx_lines, `sub_id` -> a substation point,
    `landmark_id` -> a ratified landmark. 'approx' IS returned (it carries a
    sub_id); its `confidence` is the signal the map renders it approximately."""
    if resolution is None or resolution == "unmatched":
        return None
    return {
        "resolution": resolution,
        "line_ids": list(line_ids) if line_ids is not None else None,
        "sub_id": int(sub_id) if sub_id is not None else None,
        "landmark_id": landmark_id,
        "confidence": _atlas_fp_num(confidence),
        "match_method": match_method,
    }


def _parse_atlas_constraints_params(market: str, window: str) -> tuple[str, str, int]:
    """Validate + normalize (market, window). Returns (market_resolved,
    window_norm, days) or raises HTTPException(400) with a terse message.
    `market_resolved` is the CANONICAL label after alias resolution — the
    deprecated 'RTM' wire token resolves to 'RTD' — and is what the response
    echoes and the cache keys on. Pure — unit-tested."""
    market_norm = market.strip().upper()
    if market_norm not in _ATLAS_CONSTRAINT_MARKET_SET:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown market '{market}'; valid markets are "
                f"{list(ATLAS_CONSTRAINT_MARKETS)}."
            ),
        )
    market_resolved = _ATLAS_CONSTRAINT_MARKET_RESOLVE[market_norm]
    window_norm = window.strip().lower()
    if window_norm not in ATLAS_CONSTRAINT_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown window '{window}'; valid windows are "
                f"{list(ATLAS_CONSTRAINT_WINDOWS)}."
            ),
        )
    return market_resolved, window_norm, ATLAS_CONSTRAINT_WINDOWS[window_norm]


def _build_atlas_constraints_query(
    market_resolved: str, days: int, cap: int
) -> tuple[str, dict]:
    """Build the windowed league-table aggregation SQL + params. Pure (no DB) so
    the SQL shape and param math are unit-testable. The window is anchored on the
    market's latest trade_date; generate_series yields the full N-day trend axis
    (0-filled, oldest->newest) so every `trend` has exactly `days` entries.

    Label-agnostic during the pantry relabel: the resolved market maps to the set
    of stored `market` symbols carrying its data (RTD spans BOTH the new 'RTD' and
    the legacy 'RTM' via `market = ANY(...)`), so results are correct before AND
    after migration 072. DAM is a single stable symbol."""
    db_labels = _ATLAS_CONSTRAINT_DB_LABELS[market_resolved]
    sql = """
        WITH bounds AS (
            SELECT MAX(trade_date) AS anchor
            FROM caiso_binding_constraints_daily
            WHERE market = ANY(%(markets)s)
        ),
        win AS (
            SELECT anchor,
                   (anchor - %(days_back)s)::date AS start_date
            FROM bounds
        ),
        days AS (
            SELECT generate_series(w.start_date, w.anchor, interval '1 day')::date AS d
            FROM win w
            WHERE w.anchor IS NOT NULL
        ),
        scoped AS (
            SELECT c.constraint_id, c.trade_date, c.hours_binding,
                   c.intervals_binding, c.max_shadow_price, c.mean_shadow_price,
                   c.last_binding_ts
            FROM caiso_binding_constraints_daily c, win w
            WHERE c.market = ANY(%(markets)s)
              AND c.trade_date BETWEEN w.start_date AND w.anchor
        ),
        agg AS (
            SELECT constraint_id,
                   SUM(hours_binding) AS hours_bound,
                   MAX(max_shadow_price) AS max_shadow,
                   SUM(mean_shadow_price * intervals_binding)
                       / NULLIF(SUM(intervals_binding), 0) AS avg_shadow_when_bound,
                   MAX(last_binding_ts) AS last_bound_ts
            FROM scoped
            GROUP BY constraint_id
            ORDER BY hours_bound DESC, constraint_id ASC
            LIMIT %(cap)s
        ),
        trend AS (
            SELECT a.constraint_id,
                   array_agg(COALESCE(s.hours_binding, 0) ORDER BY d.d) AS trend
            FROM agg a
            CROSS JOIN days d
            LEFT JOIN scoped s
              ON s.constraint_id = a.constraint_id
             AND s.trade_date = d.d
            GROUP BY a.constraint_id
        ),
        meta AS (
            SELECT MAX(last_binding_ts) AS as_of FROM scoped
        ),
        -- Gazetteer name for each constraint: its RATIFIED landmark, if any.
        -- Many raw constraint_ids share one landmark by design (the Inyokern
        -- family is 7 rows -> one name), so this joins members -> landmarks and
        -- keeps only status='ratified' (provisional/retired never surface). A
        -- constraint is expected to sit in at most one ratified landmark;
        -- DISTINCT ON guards the league-table row count against an accidental
        -- double-membership fanning a row into two.
        landmarks AS (
            SELECT DISTINCT ON (lm.entity_id)
                   lm.entity_id,
                   l.canonical_name AS landmark_name,
                   l.slug           AS landmark_slug,
                   l.function_type  AS landmark_function_type
            FROM atlas_landmark_members lm
            JOIN atlas_landmarks l ON l.landmark_id = lm.landmark_id
            WHERE lm.entity_type = 'constraint'
              AND l.status = 'ratified'
            ORDER BY lm.entity_id, l.landmark_id
        ),
        -- Constraint geometry from the newest-success build (the scoped view
        -- already unions captain overrides + newest build). LEFT-joined so a
        -- constraint with no geometry row simply carries geometry_ref: null;
        -- see _atlas_geometry_ref for the null / 'unmatched' collapse.
        geometry AS (
            SELECT constraint_id, resolution, line_ids, sub_id, landmark_id,
                   match_method, confidence
            FROM constraint_geometry_current
        )
        SELECT a.constraint_id AS constraint,
               a.hours_bound, a.max_shadow, a.avg_shadow_when_bound,
               a.last_bound_ts, t.trend, m.as_of,
               lk.landmark_name, lk.landmark_slug, lk.landmark_function_type,
               g.resolution     AS geom_resolution,
               g.line_ids       AS geom_line_ids,
               g.sub_id         AS geom_sub_id,
               g.landmark_id    AS geom_landmark_id,
               g.match_method   AS geom_match_method,
               g.confidence     AS geom_confidence
        FROM agg a
        JOIN trend t ON t.constraint_id = a.constraint_id
        CROSS JOIN meta m
        LEFT JOIN landmarks lk ON lk.entity_id = a.constraint_id
        LEFT JOIN geometry g ON g.constraint_id = a.constraint_id
        ORDER BY a.hours_bound DESC, a.constraint_id ASC
    """
    params = {"markets": list(db_labels), "days_back": days - 1, "cap": cap}
    return sql, params


def _atlas_constraints_is_stale(as_of, now) -> bool:
    """No data at all, or the newest binding is older than the 48h threshold.
    Pure — unit-tested. `as_of`/`now` are tz-aware datetimes (or as_of None)."""
    if as_of is None:
        return True
    return (now - as_of) > ATLAS_CONSTRAINT_STALE_AFTER


@app.get("/api/atlas/constraints")
async def atlas_constraints(
    response: Response,
    market: str = Query(
        description=(
            "CAISO market symbol. One of "
            f"{', '.join(ATLAS_CONSTRAINT_MARKETS)} (case-insensitive). Required; "
            "unknown values are rejected with 400. RTM is a deprecated alias for "
            "RTD (echoed back as RTD during the label transition)."
        ),
    ),
    window: str = Query(
        default=ATLAS_CONSTRAINT_DEFAULT_WINDOW,
        description=(
            "Look-back window. One of "
            f"{', '.join(ATLAS_CONSTRAINT_WINDOWS)} (default "
            f"{ATLAS_CONSTRAINT_DEFAULT_WINDOW}). Any other value is rejected "
            "with 400."
        ),
    ),
):
    """
    Binding-constraint blotter league table for one CAISO market + window.

    Top-100 constraints by binding hours over the window, each with its worst
    shadow price, interval-weighted average shadow price while bound, last bind
    time, and a per-day binding-hours sparkline:
        {
          "as_of": "2026-07-11T06:00:00+00:00",   # max last_binding_ts in window
          "market": "DAM", "window": "90d",
          "stale": false,                          # no rows OR as_of older than 48h
          "rows": [
            { "constraint": "31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1",
              "hours_bound": 1835, "max_shadow": 553.15802,
              "avg_shadow_when_bound": 160.90376,
              "last_bound_ts": "2026-07-11T06:00:00+00:00",
              "trend": [ ...N per-day hours_bound, oldest->newest... ],
              "landmark": { "name": "Hopland Gate", "slug": "hopland-gate",
                            "function_type": "gate" },   # or null
              "geometry_ref": { "resolution": "line", "line_ids": ["306104"],
                                "sub_id": null, "landmark_id": "lmk_hopland_gate",
                                "confidence": 0.9, "match_method": "br_line" } },
            ...
          ]
        }

    `landmark` is the constraint's ratified gazetteer landmark (many raw
    constraint_ids share one name by design) or null if it belongs to no
    ratified landmark. `geometry_ref` is the constraint's map-geometry pointer
    from the newest-success geometry build (line_ids -> atlas_tx_lines, sub_id ->
    a substation point, landmark_id -> a ratified landmark), or null when the
    constraint has no geometry or resolved 'unmatched'. Sourced from the
    caiso_binding_constraints_daily rollup,
    keyed by constraint_id (constraint_name is nullable/non-unique). The window is
    anchored on the market's latest trade_date (the rollup lags the live day),
    so `trend` always spans N real days; `stale` flags genuinely old/absent data.
    Cached in-process for 5 min per (market, window).

    Errors: bad market/window -> 400; DB unavailable -> 503; internal trend-axis
    skew -> 500. Empty/stale data is reported honestly (stale:true), never faked.
    """
    assert _pool is not None

    market_resolved, window_norm, days = _parse_atlas_constraints_params(
        market, window
    )

    # ── Cache hit (5-min TTL, keyed (resolved market, window)) ───────────────
    # The key uses the RESOLVED label, so the deprecated 'RTM' alias and 'RTD'
    # share one entry and can never return divergent payloads.
    key = (market_resolved, window_norm)
    now_mono = time.monotonic()
    cached = _atlas_constraints_cache.get(key)
    if cached is not None and (now_mono - cached[0]) < _ATLAS_CONSTRAINTS_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=300"
        return cached[1]

    sql, params = _build_atlas_constraints_query(
        market_resolved, days, ATLAS_CONSTRAINT_ROW_CAP
    )
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
    except Exception as e:
        # DB unavailability -> 503 (house standard; see /health). Fail loud.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # ── as_of + stale. No rows ⟺ no data for this market -> as_of null, stale.
    as_of = rows[0]["as_of"] if rows else None
    now_utc = _datetime.now(_timezone.utc)
    stale = _atlas_constraints_is_stale(as_of, now_utc)

    out_rows: list[dict] = []
    for r in rows:
        trend_raw = r["trend"] or []
        # Trend-axis guard: generate_series must yield exactly `days` buckets;
        # any skew is an internal error, not a partial payload.
        if len(trend_raw) != days:
            raise HTTPException(
                status_code=500,
                detail="internal error: constraint trend axis length mismatch.",
            )
        max_shadow = r["max_shadow"]
        avg_shadow = r["avg_shadow_when_bound"]
        # Gazetteer name: the constraint's ratified landmark, or null. The join
        # already filtered to status='ratified', so a null name means "in no
        # ratified landmark" (unnamed, provisional, or retired all land here).
        landmark = (
            {
                "name": r["landmark_name"],
                "slug": r["landmark_slug"],
                "function_type": r["landmark_function_type"],
            }
            if r["landmark_name"] is not None
            else None
        )
        out_rows.append(
            {
                "constraint": r["constraint"],
                # hours reported as whole hours per the contract (DAM hourly is
                # whole by nature; RTM 5-min is rounded from interval-hours).
                "hours_bound": int(round(float(r["hours_bound"]))),
                "max_shadow": float(max_shadow) if max_shadow is not None else None,
                "avg_shadow_when_bound": (
                    round(float(avg_shadow), 5) if avg_shadow is not None else None
                ),
                "last_bound_ts": _pnode_utc_iso(r["last_bound_ts"]),
                "trend": [int(round(float(v))) for v in trend_raw],
                "landmark": landmark,
                # Geometry pointer from the newest-success build (null when the
                # constraint has no geometry row or resolved 'unmatched').
                "geometry_ref": _atlas_geometry_ref(
                    r["geom_resolution"],
                    r["geom_line_ids"],
                    r["geom_sub_id"],
                    r["geom_landmark_id"],
                    r["geom_confidence"],
                    r["geom_match_method"],
                ),
            }
        )

    payload = {
        "as_of": _pnode_utc_iso(as_of),
        "market": market_resolved,
        "window": window_norm,
        "stale": stale,
        "rows": out_rows,
    }

    # Populate the in-process cache and mirror the 5-min TTL on the HTTP header.
    _atlas_constraints_cache[key] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=300"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/constraints/fingerprint — per-node binding-constraint fingerprint
# (D-07-12)
#
# ONE concern: the per-node "fingerprint" of a single binding constraint — for
# one constraint_id + one variant, the nodes whose price moves most WHEN that
# constraint binds, each with its delta/beta, sign-consistency, bound-hour count,
# co-binding fraction, and effective map coordinates. This is the read side of
# last night's pantry "fingerprint" build; the dashboard fingerprint lane
# consumes it and MUST NOT ship until this endpoint is deployed on Railway.
#
# SOURCE (schema-read first, 2026-07-12):
#   fingerprint_builds        build ledger — one row per build attempt
#                             (build_id, params jsonb, source_as_of jsonb,
#                             row_counts jsonb, status, error_message,
#                             started_at, finished_at)
#   constraint_fingerprints   per-node results (constraint_id, variant,
#                             pnode_id, window_start/end, n_bound, n_unbound,
#                             mean_when_bound/unbound, delta, beta,
#                             sign_consistency, co_bind_frac, built_at, build_id)
#   atlas_pnode_geo_corrected captain-verified live geo (14,417 rows, full
#                             universe, zero null base coords, 38 editorial
#                             corrections). Effective coords are
#                             COALESCE(corrected_lat, latitude) /
#                             COALESCE(corrected_lon, longitude) — the coalesce
#                             is MANDATORY (corrections move mislocated nodes up
#                             to ~812 km). NEVER the uncorrected atlas_pnode_geo.
#
# CONSUMER CONTRACT — newest-success scoping (critical): reads are ALWAYS scoped
# to the newest fingerprint_builds row with status='success'. constraint_fingerprints
# is NEVER read unscoped: a refused/failed build can strand orphan rows under a
# non-success build_id, and a newer FAILED build must be ignored in favor of the
# newest SUCCESS. The ledger row also carries source_as_of (echoed as `as_of`),
# the build window (params.window_start/end), and row_counts (drives the honest
# empty-reason below).
#
# ROUTE — query params, NOT a path segment (constraint ids carry spaces, e.g.
# "34116_LE GRAND_115_..."):
#   GET /api/atlas/constraints/fingerprint?constraint_id=<exact>&variant=<v>&limit=<n>
#     constraint_id  required, exact match.
#     variant        optional, default da_fmm; one of the four validated below.
#     limit          optional, default 50, hard-capped (clamped) at 200.
#   Rows ordered ABS(delta) DESC; for the beta variants, fall back to ABS(beta)
#   when delta is null (COALESCE(ABS(delta),ABS(beta))). pnode_id breaks ties.
#
# EMPTY-WITH-REASON (honesty feature): a constraint with no rows in the newest
# build for that variant still returns 200 with nodes:[] and a `reason` derived
# from the ledger row_counts — never a bare empty array. See _atlas_fingerprint_reason.
#
# ADVISORY: co_bind_frac_avg is the mean co_bind_frac over the returned nodes;
# when it exceeds 0.8 a note flags that the deltas are NOT deconfounded (bound
# hours co-occurred with other binding constraints).
#
# CACHE: in-process, 5-min TTL, keyed (constraint_id, variant, limit) — mirrors
# the blotter's cache discipline. Lock-free (single-threaded loop; a rare double
# fill just recomputes, last-write-wins, idempotent).
# ═══════════════════════════════════════════════════════════════════════════

# Accepted fingerprint variants (exact wire tokens). Anything else -> 400.
ATLAS_FP_VARIANTS = (
    "da_fmm",
    "fmm_rtd",
    "congestion_beta_dam",
    "congestion_beta_rtd",
)
_ATLAS_FP_VARIANT_SET = frozenset(ATLAS_FP_VARIANTS)
ATLAS_FP_DEFAULT_VARIANT = "da_fmm"

# The "beta" variants carry a beta column; for these, ordering falls back to
# ABS(beta) when delta is null (COALESCE below). The others always have delta.
_ATLAS_FP_BETA_VARIANTS = frozenset({"congestion_beta_dam", "congestion_beta_rtd"})

# limit: default 50, HARD-CAPPED (clamped, not 422'd) at 200 — mirrors /api/wire.
ATLAS_FP_DEFAULT_LIMIT = 50
ATLAS_FP_MAX_LIMIT = 200

# Advisory trips when the mean co-binding fraction over returned nodes exceeds
# this — deltas are then confounded by co-binding constraints.
ATLAS_FP_CO_BIND_ADVISORY_THRESHOLD = 0.8
_ATLAS_FP_CO_BIND_NOTE = (
    "bound hours had co-binding constraints this window; "
    "deltas are not deconfounded"
)

# In-process response cache: (constraint_id, variant, limit) -> (stamp, payload).
_ATLAS_FP_CACHE_TTL = 300.0  # 5 minutes
_atlas_fingerprint_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}

# Newest SUCCESS build. status='success' is what enforces the consumer contract:
# a newer FAILED build is filtered out, so this resolves to the newest build that
# actually wrote rows — never an orphan-stranding refused build.
_ATLAS_FP_LATEST_SUCCESS_SQL = """
    SELECT build_id, source_as_of, row_counts, params
    FROM fingerprint_builds
    WHERE status = 'success'
    ORDER BY started_at DESC, build_id DESC
    LIMIT 1
"""

# Gazetteer landmark for the requested constraint_id: its RATIFIED landmark, if
# any. Independent of the fingerprint build (landmarks are a naming layer, not a
# per-build result), so this is a third small read keyed on constraint_id alone.
# status='ratified' means provisional/retired never surface; LIMIT 1 (ordered by
# landmark_id) guards against an accidental double-membership. Carries `blurb`,
# which the blotter's per-row landmark omits.
_ATLAS_FP_LANDMARK_SQL = """
    SELECT l.canonical_name AS landmark_name,
           l.slug           AS landmark_slug,
           l.function_type  AS landmark_function_type,
           l.blurb          AS landmark_blurb
    FROM atlas_landmark_members m
    JOIN atlas_landmarks l ON l.landmark_id = m.landmark_id
    WHERE m.entity_type = 'constraint'
      AND m.entity_id = %(constraint_id)s
      AND l.status = 'ratified'
    ORDER BY l.landmark_id
    LIMIT 1
"""


def _parse_atlas_fingerprint_params(
    variant: str, limit: int
) -> tuple[str, int]:
    """Validate + normalize (variant, limit). Returns (variant_norm,
    limit_clamped) or raises HTTPException(400) on an unknown variant. limit is
    CLAMPED to [1, 200] (never rejected). Pure — unit-tested. constraint_id needs
    no normalization (exact match, spaces significant) so it is not handled here."""
    variant_norm = variant.strip()
    if variant_norm not in _ATLAS_FP_VARIANT_SET:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown variant '{variant}'; valid variants are "
                f"{list(ATLAS_FP_VARIANTS)}."
            ),
        )
    limit_clamped = max(1, min(int(limit), ATLAS_FP_MAX_LIMIT))
    return variant_norm, limit_clamped


def _build_atlas_fingerprint_nodes_query(
    build_id: str, constraint_id: str, variant: str, limit: int
) -> tuple[str, dict]:
    """Build the per-node fingerprint SELECT + params. Pure (no DB) so the SQL
    shape, geo-coalesce, and ordering are unit-testable.

    Scoped to the resolved build_id (newest success) + exact constraint_id +
    variant. LEFT JOINs atlas_pnode_geo_corrected so nodes with no geo row are
    KEPT with null lat/lon (nulls counted, not dropped); effective coordinates are
    COALESCE(corrected_*, base) — the captain-ratified corrections. Ordered by
    ABS(delta) DESC; the beta variants fall back to ABS(beta) when delta is null."""
    if variant in _ATLAS_FP_BETA_VARIANTS:
        order_key = "COALESCE(ABS(cf.delta), ABS(cf.beta))"
    else:
        order_key = "ABS(cf.delta)"
    sql = f"""
        SELECT cf.pnode_id,
               cf.delta, cf.beta, cf.sign_consistency, cf.n_bound, cf.co_bind_frac,
               COALESCE(g.corrected_lat, g.latitude) AS lat,
               COALESCE(g.corrected_lon, g.longitude) AS lon
        FROM constraint_fingerprints cf
        LEFT JOIN atlas_pnode_geo_corrected g ON g.pnode_id = cf.pnode_id
        WHERE cf.build_id = %(build_id)s
          AND cf.constraint_id = %(constraint_id)s
          AND cf.variant = %(variant)s
        ORDER BY {order_key} DESC NULLS LAST, cf.pnode_id ASC
        LIMIT %(limit)s
    """
    params = {
        "build_id": build_id,
        "constraint_id": constraint_id,
        "variant": variant,
        "limit": limit,
    }
    return sql, params


def _atlas_fingerprint_reason(row_counts, variant: str, constraint_id: str) -> dict:
    """Derive the honest empty-reason for a constraint absent from the newest
    build's rows for `variant`, from the ledger row_counts jsonb. Pure —
    unit-tested. Returns {"reason": ...} plus, for insufficient_overlap, the
    demoted constraint's bound_hours/joined_hours.

    Precedence follows the contract's listed order (most constraint-specific
    first): insufficient_overlap (demoted, with its numbers) -> below_floor ->
    variant_degenerate (whole variant refused) -> not_in_window (the catch-all).
    below_floor is only positively derivable if the ledger enumerates below-floor
    ids (a list rather than the usual scalar count); otherwise such constraints
    fall through to not_in_window, since row_counts carries no below-floor id list."""
    rc = row_counts or {}
    by_variant = rc.get("by_variant") or {}
    bv = by_variant.get(variant) or {}

    # insufficient_overlap: bound enough hours but too few JOINED (overlap) hours,
    # so the build demoted it. The demoted list carries the per-constraint numbers.
    for entry in bv.get("demoted") or []:
        if entry.get("constraint_id") == constraint_id:
            return {
                "reason": "insufficient_overlap",
                "bound_hours": entry.get("bound_hours"),
                "joined_hours": entry.get("joined_hours"),
            }

    # below_floor: didn't bind enough hours to clear the floor. Only assertable
    # when the ledger lists the ids (defensive — usually a scalar count only).
    below_floor = bv.get("skipped_below_floor")
    if isinstance(below_floor, list) and constraint_id in below_floor:
        return {"reason": "below_floor"}

    # variant_degenerate: the whole variant produced zero node rows and the build
    # refused to write it — so every constraint in it is empty for a systemic reason.
    if variant in (rc.get("degenerate_variants") or []) or bv.get("degenerate"):
        return {"reason": "variant_degenerate"}

    # not_in_window: the constraint simply never surfaced in this build/variant.
    return {"reason": "not_in_window"}


def _atlas_fingerprint_advisory(co_bind_fracs) -> dict:
    """Advisory block from the co_bind_frac values of the returned nodes. Pure —
    unit-tested. co_bind_frac_avg is their mean (None when no rows). When the mean
    exceeds 0.8 a `note` warns the deltas are not deconfounded."""
    vals = [float(v) for v in co_bind_fracs if v is not None]
    if not vals:
        return {"co_bind_frac_avg": None}
    avg = sum(vals) / len(vals)
    out = {"co_bind_frac_avg": round(avg, 6)}
    if avg > ATLAS_FP_CO_BIND_ADVISORY_THRESHOLD:
        out["note"] = _ATLAS_FP_CO_BIND_NOTE
    return out


def _atlas_fp_num(x):
    """Numeric (Decimal/float) -> rounded float, None passes through. Coordinates
    and metrics are emitted at 6 dp — ample for a map and stable across reads."""
    return round(float(x), 6) if x is not None else None


@app.get("/api/atlas/constraints/fingerprint")
async def atlas_constraints_fingerprint(
    response: Response,
    constraint_id: str = Query(
        description=(
            "Exact CAISO constraint_id (case- and space-sensitive; ids contain "
            "spaces, so this is a query param, not a path segment). Required."
        ),
    ),
    variant: str = Query(
        default=ATLAS_FP_DEFAULT_VARIANT,
        description=(
            "Fingerprint variant. One of "
            f"{', '.join(ATLAS_FP_VARIANTS)} (default {ATLAS_FP_DEFAULT_VARIANT}). "
            "Unknown values are rejected with 400."
        ),
    ),
    limit: int = Query(
        default=ATLAS_FP_DEFAULT_LIMIT,
        ge=1,
        description=(
            f"Max nodes returned, ordered by ABS(delta) DESC. Default "
            f"{ATLAS_FP_DEFAULT_LIMIT}; values above the hard cap of "
            f"{ATLAS_FP_MAX_LIMIT} are clamped down to {ATLAS_FP_MAX_LIMIT}."
        ),
    ),
):
    """
    Per-node fingerprint of one binding constraint for one variant.

    Scoped to the NEWEST successful fingerprint build (a newer failed build is
    ignored). Returns the nodes whose price moves most when the constraint binds,
    each with delta/beta, sign-consistency, bound-hour count, co-binding fraction,
    and effective map coordinates (captain-corrected COALESCE):
        {
          "constraint_id": "31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1",
          "variant": "da_fmm",
          "build_id": "fpb_20260712T151820Z_7d",
          "window_start": "2026-07-06", "window_end": "2026-07-12",
          "as_of": { ...source_as_of passthrough... },
          "landmark": { "name": "Inyokern Export Corridor",
                        "slug": "inyokern-export-corridor",
                        "function_type": "corridor",
                        "blurb": "Owens Valley/Kern export path..." },  # or null
          "advisory": { "co_bind_frac_avg": 1.0,
                        "note": "bound hours had co-binding constraints ..." },
          "nodes": [
            { "pnode_id": "CONTROLX_1_N003", "lat": 37.3428, "lon": -118.4719,
              "delta": 286.271099, "beta": null, "sign_consistency": 0.526316,
              "n_bound": 19, "co_bind_frac": 1.0 }, ...
          ]
        }

    A constraint with no rows in the newest build for the variant still returns
    200 with nodes:[] and an honest `reason` (below_floor | insufficient_overlap |
    variant_degenerate | not_in_window) derived from the build ledger's row_counts.

    Errors: bad variant -> 400; DB unavailable / no successful build -> 503.
    """
    assert _pool is not None

    variant_norm, limit_clamped = _parse_atlas_fingerprint_params(variant, limit)

    # ── Cache hit (5-min TTL, keyed (constraint_id, variant, clamped limit)) ──
    key = (constraint_id, variant_norm, limit_clamped)
    now_mono = time.monotonic()
    cached = _atlas_fingerprint_cache.get(key)
    if cached is not None and (now_mono - cached[0]) < _ATLAS_FP_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=300"
        return cached[1]

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1) Resolve the newest SUCCESS build (ledger row: build_id +
                #    source_as_of + row_counts + params). Scoping happens here.
                await cur.execute(_ATLAS_FP_LATEST_SUCCESS_SQL)
                ledger = await cur.fetchone()
                if ledger is None:
                    # No successful build at all -> no data universe. Fail loud.
                    raise HTTPException(
                        status_code=503,
                        detail="no successful fingerprint build available.",
                    )
                build_id = ledger["build_id"]
                # 2) That build's rows for (constraint_id, variant), geo-joined.
                nodes_sql, nodes_params = _build_atlas_fingerprint_nodes_query(
                    build_id, constraint_id, variant_norm, limit_clamped
                )
                await cur.execute(nodes_sql, nodes_params)
                rows = await cur.fetchall()
                # 3) The constraint's ratified gazetteer landmark, if any. Build-
                #    independent (a naming layer), so it is read on constraint_id
                #    alone and returned even for an empty-with-reason payload.
                await cur.execute(
                    _ATLAS_FP_LANDMARK_SQL, {"constraint_id": constraint_id}
                )
                landmark_row = await cur.fetchone()
    except HTTPException:
        raise
    except Exception as e:
        # DB unavailability -> 503 (house standard; see /health). Fail loud.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    params_json = ledger["params"] or {}
    # Top-level landmark object (with blurb), or null if the constraint is in no
    # ratified landmark. Unlike the blotter's per-row landmark, this carries blurb.
    landmark = (
        {
            "name": landmark_row["landmark_name"],
            "slug": landmark_row["landmark_slug"],
            "function_type": landmark_row["landmark_function_type"],
            "blurb": landmark_row["landmark_blurb"],
        }
        if landmark_row is not None
        else None
    )
    payload = {
        "constraint_id": constraint_id,
        "variant": variant_norm,
        "build_id": build_id,
        "window_start": params_json.get("window_start"),
        "window_end": params_json.get("window_end"),
        "as_of": ledger["source_as_of"],
        "landmark": landmark,
    }

    if not rows:
        # Empty-with-reason: never a bare empty array. Reason from the ledger.
        payload["advisory"] = _atlas_fingerprint_advisory([])
        payload["nodes"] = []
        payload.update(
            _atlas_fingerprint_reason(
                ledger["row_counts"], variant_norm, constraint_id
            )
        )
    else:
        nodes = [
            {
                "pnode_id": r["pnode_id"],
                "lat": _atlas_fp_num(r["lat"]),
                "lon": _atlas_fp_num(r["lon"]),
                "delta": _atlas_fp_num(r["delta"]),
                "beta": _atlas_fp_num(r["beta"]),
                "sign_consistency": _atlas_fp_num(r["sign_consistency"]),
                "n_bound": int(r["n_bound"]) if r["n_bound"] is not None else None,
                "co_bind_frac": _atlas_fp_num(r["co_bind_frac"]),
            }
            for r in rows
        ]
        payload["advisory"] = _atlas_fingerprint_advisory(
            [r["co_bind_frac"] for r in rows]
        )
        payload["nodes"] = nodes

    _atlas_fingerprint_cache[key] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=300"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/nodes/fingerprint — inverse fingerprint: constraints by pnode
# (D-07-12)
#
# ONE concern: the INVERSE of /api/atlas/constraints/fingerprint. That endpoint
# fixes a constraint and ranks the nodes it moves; this fixes a PNODE and ranks
# the constraints whose binding moves it. Desk motivation: a trader watching a
# node crater on the DART layer asks "which constraint is doing this?" — until
# now there was no lookup in that direction. Same underlying table
# (constraint_fingerprints), same newest-success build scoping, read the other
# way round (WHERE pnode_id = param, rows ARE constraints).
#
# SOURCE (identical to the forward endpoint's, read the transposed axis):
#   fingerprint_builds        build ledger — newest status='success' scopes reads
#   constraint_fingerprints   per (constraint_id, pnode_id, variant) result rows;
#                             here filtered to one pnode_id, one variant
#   atlas_pnode_geo_corrected pnode universe (14,417 rows) — consulted ONLY to
#                             tell "unknown pnode" from "known pnode, no rows in
#                             this build" in the empty case (no geo is emitted:
#                             the caller already holds the node it tapped).
#   atlas_landmark_members /  gazetteer — each returned CONSTRAINT carries its
#   atlas_landmarks           ratified landmark (name/slug/function_type), joined
#                             per-row via the blotter's DISTINCT ON shape (NOT the
#                             forward endpoint's single top-level landmark: there
#                             the constraint is the subject; here the constraints
#                             are the rows, so the landmark rides each row and
#                             omits `blurb`, exactly like the blotter's).
#
# NEWEST-SUCCESS SCOPING: identical contract to the forward endpoint — reads are
# ALWAYS scoped to the newest fingerprint_builds row with status='success' (a
# newer FAILED build is ignored; constraint_fingerprints is never read unscoped).
# Reuses _ATLAS_FP_LATEST_SUCCESS_SQL and the shared variant set.
#
# ROUTE — query params (pnode ids are plain tokens, but mirror the forward
# endpoint's param style, not a path segment):
#   GET /api/atlas/nodes/fingerprint?pnode_id=<exact>&variant=<v>&limit=<n>
#     pnode_id  required, exact match.
#     variant   optional, default da_fmm; one of the shared four (400 otherwise).
#     limit     optional, default 20, hard-capped (clamped) at 50 — a node's
#               binding-constraint list is short; this is not the forward
#               endpoint's 50/200 (a constraint fans out to many nodes).
#   Rows ordered ABS(delta) DESC; beta variants fall back to ABS(beta) when delta
#   is null (COALESCE). constraint_id breaks ties.
#
# EMPTY handling: nodes are not demoted individually the way constraints are, so
# the ledger row_counts reason machinery does NOT apply. Two honest static
# reasons suffice, disambiguated by a single existence probe against
# atlas_pnode_geo_corrected: `unknown_pnode` (the id is in no geo row at all) vs
# `node_not_in_build` (a real node, just no constraint moved it in this build).
#
# ADVISORY: reuses _atlas_fingerprint_advisory over the returned rows'
# co_bind_frac — same computation, read as "were these constraints' bound hours
# confounded by co-binding" for this one node.
#
# CACHE: in-process, 5-min TTL, keyed (pnode_id, variant, limit) — its own dict,
# same discipline as the forward endpoint's.
# ═══════════════════════════════════════════════════════════════════════════

# limit: default 20, HARD-CAPPED (clamped, not 422'd) at 50. Smaller than the
# forward endpoint's 50/200: a single node is moved by only a handful of
# constraints, whereas one constraint fans out across many nodes.
ATLAS_NODE_FP_DEFAULT_LIMIT = 20
ATLAS_NODE_FP_MAX_LIMIT = 50

# In-process response cache: (pnode_id, variant, limit) -> (stamp, payload).
_atlas_node_fingerprint_cache: dict[tuple[str, str, int], tuple[float, dict]] = {}

# Does this pnode_id exist in the corrected geo universe at all? Drives the
# empty-case reason (unknown_pnode vs node_not_in_build). Existence only — no
# coordinates are emitted by this endpoint.
_ATLAS_NODE_FP_PNODE_EXISTS_SQL = """
    SELECT 1
    FROM atlas_pnode_geo_corrected
    WHERE pnode_id = %(pnode_id)s
    LIMIT 1
"""


def _parse_atlas_node_fingerprint_params(
    variant: str, limit: int
) -> tuple[str, int]:
    """Validate + normalize (variant, limit) for the inverse endpoint. Returns
    (variant_norm, limit_clamped) or raises HTTPException(400) on an unknown
    variant. Shares the forward endpoint's variant set; limit is CLAMPED to
    [1, 50] (never rejected). Pure — unit-tested. pnode_id needs no normalization
    (exact match) so it is not handled here."""
    variant_norm = variant.strip()
    if variant_norm not in _ATLAS_FP_VARIANT_SET:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown variant '{variant}'; valid variants are "
                f"{list(ATLAS_FP_VARIANTS)}."
            ),
        )
    limit_clamped = max(1, min(int(limit), ATLAS_NODE_FP_MAX_LIMIT))
    return variant_norm, limit_clamped


def _build_atlas_node_fingerprint_constraints_query(
    build_id: str, pnode_id: str, variant: str, limit: int
) -> tuple[str, dict]:
    """Build the per-constraint SELECT + params for one pnode. Pure (no DB) so the
    SQL shape, gazetteer join, and ordering are unit-testable.

    Scoped to the resolved build_id (newest success) + exact pnode_id + variant.
    Each constraint row LEFT JOINs its ratified gazetteer landmark via the
    blotter's DISTINCT ON (entity_id) shape — a constraint sits in at most one
    ratified landmark, and DISTINCT ON guards the row count against an accidental
    double-membership fanning a constraint into two rows. Ordered by ABS(delta)
    DESC; the beta variants fall back to ABS(beta) when delta is null."""
    if variant in _ATLAS_FP_BETA_VARIANTS:
        order_key = "COALESCE(ABS(cf.delta), ABS(cf.beta))"
    else:
        order_key = "ABS(cf.delta)"
    sql = f"""
        WITH landmarks AS (
            SELECT DISTINCT ON (lm.entity_id)
                   lm.entity_id,
                   l.canonical_name AS landmark_name,
                   l.slug           AS landmark_slug,
                   l.function_type  AS landmark_function_type
            FROM atlas_landmark_members lm
            JOIN atlas_landmarks l ON l.landmark_id = lm.landmark_id
            WHERE lm.entity_type = 'constraint'
              AND l.status = 'ratified'
            ORDER BY lm.entity_id, l.landmark_id
        )
        SELECT cf.constraint_id,
               cf.delta, cf.beta, cf.sign_consistency, cf.n_bound, cf.co_bind_frac,
               lk.landmark_name, lk.landmark_slug, lk.landmark_function_type
        FROM constraint_fingerprints cf
        LEFT JOIN landmarks lk ON lk.entity_id = cf.constraint_id
        WHERE cf.build_id = %(build_id)s
          AND cf.pnode_id = %(pnode_id)s
          AND cf.variant = %(variant)s
        ORDER BY {order_key} DESC NULLS LAST, cf.constraint_id ASC
        LIMIT %(limit)s
    """
    params = {
        "build_id": build_id,
        "pnode_id": pnode_id,
        "variant": variant,
        "limit": limit,
    }
    return sql, params


@app.get("/api/atlas/nodes/fingerprint")
async def atlas_nodes_fingerprint(
    response: Response,
    pnode_id: str = Query(
        description=(
            "Exact CAISO pnode_id (case-sensitive). Required. The node whose "
            "binding constraints you want ranked."
        ),
    ),
    variant: str = Query(
        default=ATLAS_FP_DEFAULT_VARIANT,
        description=(
            "Fingerprint variant. One of "
            f"{', '.join(ATLAS_FP_VARIANTS)} (default {ATLAS_FP_DEFAULT_VARIANT}). "
            "Unknown values are rejected with 400."
        ),
    ),
    limit: int = Query(
        default=ATLAS_NODE_FP_DEFAULT_LIMIT,
        ge=1,
        description=(
            f"Max constraints returned, ordered by ABS(delta) DESC. Default "
            f"{ATLAS_NODE_FP_DEFAULT_LIMIT}; values above the hard cap of "
            f"{ATLAS_NODE_FP_MAX_LIMIT} are clamped down to {ATLAS_NODE_FP_MAX_LIMIT}."
        ),
    ),
):
    """
    Inverse fingerprint: the constraints whose binding moves one pnode most.

    The mirror image of /api/atlas/constraints/fingerprint — fix a node, rank the
    constraints (not the other way round). Scoped to the NEWEST successful
    fingerprint build (a newer failed build is ignored). Each constraint carries
    its delta/beta, sign-consistency, bound-hour count, co-binding fraction, and
    its ratified gazetteer landmark (per-row, no blurb — the blotter's shape):
        {
          "pnode_id": "CONTROL_7_N022",
          "variant": "da_fmm",
          "build_id": "fpb_20260712T151820Z_7d",
          "window_start": "2026-07-06", "window_end": "2026-07-12",
          "as_of": { ...source_as_of passthrough... },
          "advisory": { "co_bind_frac_avg": 1.0,
                        "note": "bound hours had co-binding constraints ..." },
          "constraints": [
            { "constraint_id": "7690-CONTRL-INYOKN_EXP_NG",
              "landmark": { "name": "Inyokern Export Corridor",
                            "slug": "inyokern-export-corridor",
                            "function_type": "corridor" },   # or null
              "delta": -286.271099, "beta": null,
              "sign_consistency": 0.526316, "n_bound": 19,
              "co_bind_frac": 1.0 }, ...
          ]
        }

    A pnode with no rows in the newest build still returns 200 with
    constraints:[] and an honest static `reason`: `unknown_pnode` (the id is in
    no corrected-geo row at all) or `node_not_in_build` (a real node, but no
    constraint moved it in this build). Nodes are not demoted individually, so the
    forward endpoint's ledger-reason machinery does not apply here.

    Errors: bad variant -> 400; DB unavailable / no successful build -> 503.
    """
    assert _pool is not None

    variant_norm, limit_clamped = _parse_atlas_node_fingerprint_params(
        variant, limit
    )

    # ── Cache hit (5-min TTL, keyed (pnode_id, variant, clamped limit)) ──
    key = (pnode_id, variant_norm, limit_clamped)
    now_mono = time.monotonic()
    cached = _atlas_node_fingerprint_cache.get(key)
    if cached is not None and (now_mono - cached[0]) < _ATLAS_FP_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=300"
        return cached[1]

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1) Resolve the newest SUCCESS build (shared ledger SQL). Scoping
                #    happens here — a newer FAILED build is filtered out.
                await cur.execute(_ATLAS_FP_LATEST_SUCCESS_SQL)
                ledger = await cur.fetchone()
                if ledger is None:
                    # No successful build at all -> no data universe. Fail loud.
                    raise HTTPException(
                        status_code=503,
                        detail="no successful fingerprint build available.",
                    )
                build_id = ledger["build_id"]
                # 2) That build's constraint rows for (pnode_id, variant), each
                #    gazetteer-joined to its ratified landmark.
                rows_sql, rows_params = (
                    _build_atlas_node_fingerprint_constraints_query(
                        build_id, pnode_id, variant_norm, limit_clamped
                    )
                )
                await cur.execute(rows_sql, rows_params)
                rows = await cur.fetchall()
                # 3) ONLY when empty: probe geo to tell unknown_pnode from
                #    node_not_in_build. A real node moved by nothing this build is
                #    a legitimate 200 with node_not_in_build; a typo'd id is
                #    unknown_pnode. No extra read on the hot (non-empty) path.
                pnode_exists = None
                if not rows:
                    await cur.execute(
                        _ATLAS_NODE_FP_PNODE_EXISTS_SQL, {"pnode_id": pnode_id}
                    )
                    pnode_exists = await cur.fetchone() is not None
    except HTTPException:
        raise
    except Exception as e:
        # DB unavailability -> 503 (house standard; see /health). Fail loud.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    params_json = ledger["params"] or {}
    payload = {
        "pnode_id": pnode_id,
        "variant": variant_norm,
        "build_id": build_id,
        "window_start": params_json.get("window_start"),
        "window_end": params_json.get("window_end"),
        "as_of": ledger["source_as_of"],
    }

    if not rows:
        # Empty: never a bare empty array. One honest static reason, chosen by the
        # geo existence probe (nodes aren't demoted, so no ledger reason applies).
        payload["advisory"] = _atlas_fingerprint_advisory([])
        payload["constraints"] = []
        payload["reason"] = (
            "node_not_in_build" if pnode_exists else "unknown_pnode"
        )
    else:
        constraints = [
            {
                "constraint_id": r["constraint_id"],
                "landmark": (
                    {
                        "name": r["landmark_name"],
                        "slug": r["landmark_slug"],
                        "function_type": r["landmark_function_type"],
                    }
                    if r["landmark_name"] is not None
                    else None
                ),
                "delta": _atlas_fp_num(r["delta"]),
                "beta": _atlas_fp_num(r["beta"]),
                "sign_consistency": _atlas_fp_num(r["sign_consistency"]),
                "n_bound": int(r["n_bound"]) if r["n_bound"] is not None else None,
                "co_bind_frac": _atlas_fp_num(r["co_bind_frac"]),
            }
            for r in rows
        ]
        payload["advisory"] = _atlas_fingerprint_advisory(
            [r["co_bind_frac"] for r in rows]
        )
        payload["constraints"] = constraints

    _atlas_node_fingerprint_cache[key] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=300"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/atlas/constraints/geometry — constraint→geometry map layer + coverage
# (D-07-13)
#
# ONE concern: the read side of last night's pantry "constraint geometry" build —
# for every binding constraint the resolver placed, WHAT to draw on the map (a
# transmission line, a substation point, or a ratified landmark) and, at the top,
# an honest coverage summary of how much of the blotter the geometry layer
# actually covers, by COUNT and by congestion DOLLARS. The dashboard geometry
# lane (PR-3, separate, gated on Railway verify) consumes it.
#
# SOURCE (schema-read first, 2026-07-13):
#   constraint_geometry_builds   build ledger — one row per build attempt
#                                (build_id, params jsonb {window_days,rule_version},
#                                source_as_of jsonb, row_counts jsonb, status,
#                                started_at, finished_at). NEWEST status='success'
#                                (excluding the 'cgb_manual' override channel)
#                                scopes reads — same contract as the fingerprint
#                                ledger: a newer FAILED build is ignored.
#   constraint_geometry_current  the served view — UNIONs captain manual overrides
#                                with the newest-success build's rows. Per
#                                constraint: resolution (line|point|landmark|
#                                approx|unmatched), line_ids[], sub_id,
#                                landmark_id, match_method, confidence. Reading the
#                                VIEW (not constraint_geometry) inherits the
#                                newest-success + override scoping for free.
#   caiso_binding_constraints_daily  the daily rollup — supplies each constraint's
#                                congestion-dollar weight for dollar-coverage.
#   atlas_landmarks              ratified-landmark display (name/slug/function_type)
#                                for a row's landmark_id.
#
# COVERAGE — the summary block, the whole point of the endpoint:
#   * count_coverage_pct = (#line + #point + #landmark) / #constraints. The
#     fraction of DISTINCT constraints the layer can place with real geometry.
#     'approx' and 'unmatched' are NOT counted (approx is a guess, not a placement).
#   * dollar_coverage — the same numerator/denominator but WEIGHTED by each
#     constraint's congestion dollars, so it answers "of the money, how much can we
#     draw?" not "of the rows." Weight = SUM(ABS(mean_shadow_price) * hours_binding)
#     over a WINDOW = the geometry build's own params.window_days (default 30),
#     anchored on the daily rollup's latest trade_date (the blotter's anchor
#     convention — NOT wall-clock today; the rollup legitimately lags a day).
#     Two tiers, because approx is cheap-but-uncertain and the desk wants both:
#       strict_pct      = line+point+landmark dollars / all dollars
#       with_approx_pct = (line+point+landmark+approx) dollars / all dollars
#     Coverage-by-dollars runs FAR ahead of coverage-by-count: the constraints
#     that bind hard (and cost most) are the well-known lines we place first.
#
# PER-ROW: constraint + resolution + geometry_ref (the shared pointer bundle;
# null for 'unmatched' — carries resolution='unmatched' at the row level with
# null geometry, so an unplaced constraint is explicit, never a silent omission) +
# its landmark display + dollar_weight. Ordered dollar_weight DESC (the map draws
# the costliest first), constraint_id tiebreak.
#
# ERRORS: DB unavailable / no successful build -> 503 (house standard; the layer
# has no data universe without a build). A build that exists but placed nothing
# still returns 200 with an honest zero-coverage summary and rows[].
#
# CACHE: in-process, 5-min TTL, single payload (no params) — mirrors the sibling
# atlas endpoints' cache discipline; lock-free, last-write-wins, idempotent.
# ═══════════════════════════════════════════════════════════════════════════

# Default look-back for the dollar-coverage weight when the build ledger's params
# carry no window_days. The build writes window_days=30; this is only the belt-
# and-suspenders fallback.
ATLAS_GEOMETRY_DEFAULT_WINDOW_DAYS = 30

# In-process response cache: single payload (endpoint takes no params).
_ATLAS_GEOMETRY_CACHE_TTL = 300.0  # 5 minutes
_atlas_geometry_cache: dict[str, tuple[float, dict]] = {}

# Newest SUCCESS geometry build, excluding the 'cgb_manual' override channel —
# exactly the scoping constraint_geometry_current itself uses, so the ledger row
# we read for metadata (window_days from params, source_as_of) matches the build
# whose rows the view serves. A newer FAILED build is filtered out.
#
# Columns mirror the fingerprint sibling's ledger read exactly: build_id,
# source_as_of, row_counts, params — the only columns the payload consumes.
# (An earlier revision also selected `build_ts`, which does NOT exist on
# constraint_geometry_builds — that column lives on constraint_geometry rows —
# so the whole endpoint 503'd on "column build_ts does not exist". The builds
# ledger carries started_at/finished_at, not build_ts; neither is served, so
# both are left out rather than selected-and-ignored.)
_ATLAS_GEOMETRY_LATEST_SUCCESS_SQL = """
    SELECT build_id, source_as_of, row_counts, params
    FROM constraint_geometry_builds
    WHERE status = 'success' AND build_id <> 'cgb_manual'
    ORDER BY started_at DESC, build_id DESC
    LIMIT 1
"""


def _atlas_geometry_window_days(params) -> int:
    """The dollar-coverage window in days: the geometry build's own
    params.window_days, or the default fallback. Coerced to a positive int; any
    junk falls back. Pure — unit-tested."""
    try:
        wd = int((params or {}).get("window_days"))
    except (TypeError, ValueError):
        return ATLAS_GEOMETRY_DEFAULT_WINDOW_DAYS
    return wd if wd > 0 else ATLAS_GEOMETRY_DEFAULT_WINDOW_DAYS


def _atlas_geometry_dollar(x) -> Optional[float]:
    """Congestion-dollar weight (Decimal/float) -> float rounded to 2 dp; None
    passes through. Dollars, so 2 dp — unlike the 6-dp coordinate rounding."""
    return round(float(x), 2) if x is not None else None


def _build_atlas_geometry_query(days_back: int) -> tuple[str, dict]:
    """Build the geometry-layer SQL + params. Pure (no DB) so the SQL shape and
    window math are unit-testable.

    One walk of constraint_geometry_current (the newest-success + override view),
    LEFT-joined to (a) each constraint's congestion-dollar weight over the window
    [latest trade_date - days_back .. latest trade_date] and (b) its ratified
    landmark display by landmark_id. dollar_weight COALESCEs to 0 for a constraint
    that did not bind in the window (e.g. a manual override outside it). Ordered
    dollar_weight DESC so the costliest constraints lead; constraint_id tiebreak
    for determinism."""
    sql = """
        WITH anchor AS (
            SELECT MAX(trade_date) AS a FROM caiso_binding_constraints_daily
        ),
        dollars AS (
            SELECT d.constraint_id,
                   SUM(ABS(d.mean_shadow_price) * d.hours_binding) AS dollar_weight
            FROM caiso_binding_constraints_daily d, anchor
            WHERE anchor.a IS NOT NULL
              AND d.trade_date BETWEEN (anchor.a - %(days_back)s) AND anchor.a
            GROUP BY d.constraint_id
        )
        SELECT g.constraint_id AS constraint,
               g.constraint_name, g.constraint_type, g.resolution,
               g.line_ids, g.sub_id, g.landmark_id, g.match_method, g.confidence,
               COALESCE(dol.dollar_weight, 0) AS dollar_weight,
               l.canonical_name AS landmark_name,
               l.slug           AS landmark_slug,
               l.function_type  AS landmark_function_type
        FROM constraint_geometry_current g
        LEFT JOIN dollars dol ON dol.constraint_id = g.constraint_id
        LEFT JOIN atlas_landmarks l
               ON l.landmark_id = g.landmark_id AND l.status = 'ratified'
        ORDER BY dollar_weight DESC, g.constraint_id ASC
    """
    return sql, {"days_back": days_back}


def _atlas_geometry_summary(rows, window_days: int) -> dict:
    """Coverage summary over the served geometry rows. Pure — unit-tested.

    Each row is a dict carrying `resolution` and `dollar_weight` (Decimal/float).
    count_coverage_pct is the line+point+landmark share of DISTINCT constraints;
    dollar_coverage weights the same numerators by congestion dollars, at two
    tiers (strict = line+point+landmark; with_approx also folds in approx). Pcts
    are None when their denominator is zero (no constraints / no dollars) — never
    a divide-by-zero, never a fabricated 0.0."""
    n = len(rows)
    by_resolution: dict[str, int] = {}
    for r in rows:
        res = r["resolution"]
        by_resolution[res] = by_resolution.get(res, 0) + 1

    n_lpl = sum(by_resolution.get(k, 0) for k in ATLAS_GEOMETRY_LPL)

    def _w(r):
        w = r["dollar_weight"]
        return float(w) if w is not None else 0.0

    total_d = sum(_w(r) for r in rows)
    lpl_d = sum(_w(r) for r in rows if r["resolution"] in ATLAS_GEOMETRY_LPL)
    approx_d = sum(_w(r) for r in rows if r["resolution"] == "approx")

    def _pct(num, den):
        return round(100.0 * num / den, 1) if den else None

    return {
        "n_constraints": n,
        "by_resolution": by_resolution,
        "count_coverage_pct": _pct(n_lpl, n),
        "dollar_coverage": {
            "window_days": window_days,
            "strict_pct": _pct(lpl_d, total_d),
            "with_approx_pct": _pct(lpl_d + approx_d, total_d),
            "total_dollar_weight": round(total_d, 2),
        },
    }


@app.get("/api/atlas/constraints/geometry")
async def atlas_constraints_geometry(response: Response):
    """
    Constraint→geometry map layer for the newest-success geometry build, with a
    count- and dollar-coverage summary.

    Every binding constraint the resolver placed, with WHAT to draw (line_ids ->
    atlas_tx_lines, sub_id -> a substation point, landmark_id -> a ratified
    landmark), plus a summary of how much of the blotter the layer covers by count
    and by congestion dollars:
        {
          "build_id": "cgb_20260713T224545Z_30d",
          "rule_version": "geom_v1",
          "as_of": { ...source_as_of passthrough... },
          "summary": {
            "n_constraints": 144,
            "by_resolution": { "line": 25, "point": 6, "landmark": 7,
                               "approx": 67, "unmatched": 39 },
            "count_coverage_pct": 26.4,
            "dollar_coverage": { "window_days": 30, "strict_pct": 58.8,
                                 "with_approx_pct": 87.7,
                                 "total_dollar_weight": 870327.5 }
          },
          "rows": [
            { "constraint": "31336_HPLND JT_60.0_31370_CLVRDLJT_60.0_BR_1 _1",
              "constraint_name": "PG1 EGLRK-SLVR-FLTN 115_M",
              "constraint_type": "BR", "resolution": "line",
              "geometry_ref": { "resolution": "line", "line_ids": ["306104"],
                                "sub_id": null, "landmark_id": "lmk_hopland_gate",
                                "confidence": 0.9, "match_method": "br_line" },
              "landmark": { "name": "Hopland Gate", "slug": "hopland-gate",
                            "function_type": "gate" },
              "dollar_weight": 30251.4 },
            ...
            { "constraint": "34540_HENRITTA_70.0_30881_HENRIETA_230_XF_4",
              "constraint_name": "Base Case", "constraint_type": "XF",
              "resolution": "unmatched", "geometry_ref": null,
              "landmark": null, "dollar_weight": 812.0 },
            ...
          ]
        }

    An 'unmatched' constraint carries resolution='unmatched' at the row level with
    geometry_ref: null (explicit, never a silent omission). Coverage runs ahead by
    dollars (the costliest constraints are the well-known lines placed first).
    Reads are scoped to the newest successful build; cached in-process for 5 min.

    Errors: DB unavailable / no successful build -> 503. A build that placed
    nothing still returns 200 with a zero-coverage summary and rows:[].
    """
    assert _pool is not None

    now_mono = time.monotonic()
    cached = _atlas_geometry_cache.get("payload")
    if cached is not None and (now_mono - cached[0]) < _ATLAS_GEOMETRY_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=300"
        return cached[1]

    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                # 1) Newest-success build ledger (metadata + the dollar window).
                await cur.execute(_ATLAS_GEOMETRY_LATEST_SUCCESS_SQL)
                ledger = await cur.fetchone()
                if ledger is None:
                    raise HTTPException(
                        status_code=503,
                        detail="no successful constraint-geometry build available.",
                    )
                params_json = ledger["params"] or {}
                window_days = _atlas_geometry_window_days(params_json)
                # 2) The served view's rows, dollar-weighted + landmark-joined.
                sql, sql_params = _build_atlas_geometry_query(window_days - 1)
                await cur.execute(sql, sql_params)
                rows = await cur.fetchall()
    except HTTPException:
        raise
    except Exception as e:
        # DB unavailability -> 503 (house standard; see /health). Fail loud.
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    out_rows = [
        {
            "constraint": r["constraint"],
            "constraint_name": r["constraint_name"],
            "constraint_type": r["constraint_type"],
            "resolution": r["resolution"],
            "geometry_ref": _atlas_geometry_ref(
                r["resolution"],
                r["line_ids"],
                r["sub_id"],
                r["landmark_id"],
                r["confidence"],
                r["match_method"],
            ),
            "landmark": (
                {
                    "name": r["landmark_name"],
                    "slug": r["landmark_slug"],
                    "function_type": r["landmark_function_type"],
                }
                if r["landmark_name"] is not None
                else None
            ),
            "dollar_weight": _atlas_geometry_dollar(r["dollar_weight"]),
        }
        for r in rows
    ]

    payload = {
        "build_id": ledger["build_id"],
        "rule_version": params_json.get("rule_version"),
        "as_of": ledger["source_as_of"],
        "summary": _atlas_geometry_summary(rows, window_days),
        "rows": out_rows,
    }

    _atlas_geometry_cache["payload"] = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=300"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/nodes/search  +  /api/watchboard — Cockpit / Watchboard v0 (PR-1, D-07-21)
#
# TWO endpoints behind the general Cockpit tile surface:
#   * GET  /api/nodes/search  — type-ahead over the snapshot node universe.
#   * POST /api/watchboard    — a batched, per-tile-isolated board read.
#
# DESIGN RULING (encoded, not debated): the Cockpit is a GENERAL tile surface;
# the /api/watchboard contract is polymorphic on `tile_type`. v0 implements
# tile_type="pnode" ONLY. Any other tile_type returns an explicit PER-TILE error
# {"error": "unknown_tile_type"} — never a guess, never a 500 for the whole
# batch. tile_type="series" (arbitrary dataset/series tiles) and a
# /api/series/catalog browse endpoint are v1 and deliberately FENCED OUT here;
# nothing in v0's shapes precludes them.
#
# ── P5 TIME CONVENTION (VERIFIED at build, not assumed) ─────────────────────
# The snapshot hot tier carries NO interval-start timestamptz — only
# market_date / market_hour (HE, 1-24) / market_interval (DAM:0 · RTPD:1-4 ·
# RTD:1-12). The sibling /api/atlas/pnode-history deliberately PUNTS the time
# axis to the client for exactly this reason. This lane cannot punt — basis and
# dart must align a snapshot interval to a hub interval on a common UTC instant.
#
# So we reconstruct the interval-start and CONFORM TO THE EXISTING HUB READER
# (/api/timeseries/caiso-hub-lmp), which is the repo's convention-of-record:
# timeseries_values.ts is interval-start UTC, and "PT hour starts land on UTC
# :00" (whole-hour offset). The reconstruction, verified live 2026-07-21 against
# both the hub grid and Postgres AT TIME ZONE:
#
#   interval_start (PT wall-clock, naive)
#       = market_date + (market_hour - 1)h + interval_offset
#   interval_offset = 0                        for DAM (hourly, interval 0)
#                   = (market_interval - 1) * step_minutes   for RT markets
#   step_minutes: DAM 60 · RTPD 15 · RTD 5
#   interval_start_utc = interval_start.localize(America/Los_Angeles).to_utc()
#
# DST is handled by tz localization (zoneinfo), NEVER by offset math — the same
# rail the whole repo runs on. Worked receipts (all confirmed live):
#   DAM  2026-07-21 HE12 int0  -> PT 11:00 -> 2026-07-21T18:00Z  (hub DA grid ✓)
#   RTPD 2026-07-21 HE12 int4  -> PT 11:45 -> 2026-07-21T18:45Z  (hub FMM grid ✓)
#   RTD  2026-07-21 HE12 int10 -> PT 11:45 -> 2026-07-21T18:45Z  (hub RTD grid ✓)
# Basis hand-check (BLUELAKE_7_GN001, RTPD, that interval): node 36.92829 −
# hub SP15 11.25682 = 25.67147.
#
# ── PERFORMANCE (schema-read-first, incl. pg_indexes 2026-07-21) ────────────
# Indexes present: PK (pnode_id, market, market_date, market_hour,
# market_interval) and idx_lmp_snap_market_time (market, market_date,
# market_hour, market_interval). Consequences, both measured on the live tier:
#   * A DISTINCT-over-all-history node search TIMES OUT (>60s) — it scans ~2.4M
#     rows/market. INSTEAD, node search derives the universe from the LATEST
#     INSTANT (idx_lmp_snap_market_time, backward index-only scan, LIMIT 1) and
#     substring-filters that one ~14.8k-row instant: ~14 ms. This is the exact
#     two-step shape the /api/atlas/pnode-lmp load-perf arc (D-07-17) landed on.
#   * The board read is BATCHED: pnode_id = ANY(...) + market + the P2 sentinel
#     floor, which rides the PK bitmap index (3 pnodes × full retention = ~2k
#     rows in 12 ms). One snapshot query per distinct market, one hub query for
#     all basis tiles — a full 24-tile board stays interactive.
#
# ── P2 SENTINEL (proven by test) ────────────────────────────────────────────
# RTD+RTPD carry rows with market_date = 0001-01-01. EVERY query in this lane
# filters market_date >= '2020-01-01'. Because the hot tier is a ~8-day rolling
# window, that floor also bounds each batched read to the retained window.
#
# ── ERROR CONTRACT ──────────────────────────────────────────────────────────
# Batch always returns 200 with a per-tile map; failures are per-tile, honest
# error objects, never a 500 for the whole board:
#   unknown_tile_type · unknown_view · unknown_market · missing_pnode_id ·
#   unknown_hub_ref · dart_requires_rt_market · internal_error
# A known node with no rows in the window is NOT an error — it is an honest
# empty tile (as_of=null, lag_minutes=null, empty=true, empty payload arrays).
# Only genuine ENVELOPE faults 400: non-JSON body, no tiles[] array, >24 tiles,
# a tile without a usable string tile_id (it is the response key), duplicate
# tile_ids. DB unreachable -> 503 (house standard).
# ═══════════════════════════════════════════════════════════════════════════

# Shared vocabulary. Markets reuse the sibling LMP consts (RTD | RTPD | DAM) so
# there is ONE source of truth; "FMM" stays a dashboard display name and is
# rejected on the wire (send RTPD), matching /api/atlas/pnode-history.
WATCHBOARD_MAX_TILES = 24
WATCHBOARD_TILE_TYPE = "pnode"
WATCHBOARD_VIEWS = ("lmp", "components", "dart", "basis")

# P2 sentinel floor — applied by EVERY query in this lane. Also bounds a batched
# read to the ~8-day retained window.
COCKPIT_SENTINEL_MIN_DATE = "2020-01-01"

# Interval-step minutes per market (P1 cadence): the (market_interval - 1)
# multiplier in the P5 interval-start reconstruction. DAM is hourly (interval 0).
COCKPIT_MARKET_STEP_MIN = {"DAM": 60, "RTPD": 15, "RTD": 5}

# P4 market map -> the hub dataset a basis tile joins against.
COCKPIT_MARKET_TO_HUB = {
    "DAM":  "caiso_lmp_da_hourly",   # DA hourly
    "RTPD": "caiso_lmp_rt_15min",    # FMM 15-min
    "RTD":  "caiso_lmp_rt_5min",     # RTD 5-min
}

# Hub reference series for basis (P3): the bare hub labels, default SP15.
COCKPIT_HUB_DEFAULT_REF = "SP15"
COCKPIT_HUB_REF_SET = frozenset(HUB_LMP_HUBS)  # NP15 / SP15 / ZP26

# Sparkline / run windows: 24h for the RT feeds, 7d for the DA schedule, anchored
# on the latest AVAILABLE interval (staleness-by-design — never wall-clock now,
# which would blank a legitimately stale board). dart is hourly -> a 24h run.
COCKPIT_RT_WINDOW = _timedelta(hours=24)
COCKPIT_DA_WINDOW = _timedelta(days=7)
COCKPIT_DART_WINDOW = _timedelta(hours=24)

# Explicit per-tile window override (v0.2, Atlas chart grammar). A tile may pass
# window="24h" | "7d"; when absent, the per-view DEFAULT is preserved verbatim
# (24h RT · 7d DA · 24h dart) so pinned payloads are unchanged. "7d" on an RT
# view widens to the full snapshot-retention series (RTD 5-min ≈ up to ~2,016
# pts, RTPD ≈ 672); the batched read is already bounded to retention by the P2
# floor, and each tile reports the window it served + its point count.
COCKPIT_WINDOWS = {
    "24h": _timedelta(hours=24),
    "7d": _timedelta(days=7),
}

# Node search reads the universe from ONE market's latest instant (all three
# markets price the same node set — P1). DAM is the cleanest: a full 24h
# schedule, interval 0, always the complete universe.
NODE_SEARCH_MARKET = "DAM"
NODE_SEARCH_MAX = 50

_cockpit_log = logging.getLogger("energylake.cockpit")


def _cockpit_num(x):
    """Numeric -> float; None passes through (absence is not zero)."""
    return float(x) if x is not None else None


def _cockpit_like_escape(s: str) -> str:
    """Escape LIKE/ILIKE metacharacters so a user substring matches literally.
    Paired with `ESCAPE '\\'` in the query. Order matters — backslash first."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cockpit_interval_start_utc(market, market_date, market_hour, market_interval):
    """Reconstruct a snapshot interval's start instant as tz-aware UTC (P5).

    PT wall-clock start = market_date + (market_hour-1)h + interval_offset, then
    localized to America/Los_Angeles and converted to UTC. DST is resolved by
    zoneinfo localization (the offset for that wall-clock instant), NEVER by
    fixed offset math — verified to match both the hub `ts` grid and Postgres
    AT TIME ZONE at build time. Conforms to the hub reader's convention.
    """
    step = COCKPIT_MARKET_STEP_MIN.get(market, 60)
    if market == "DAM" or market_interval is None:
        offset_min = 0
    else:
        offset_min = (int(market_interval) - 1) * step
    naive_pt = _datetime.combine(market_date, _time()) + _timedelta(
        hours=int(market_hour) - 1, minutes=offset_min
    )
    local = naive_pt.replace(tzinfo=ZoneInfo(MARKET_TZ))
    return local.astimezone(_timezone.utc)


def _cockpit_window_delta(market) -> "_timedelta":
    """Sparkline/run look-back for a market: 7d for DA, 24h for the RT feeds."""
    return COCKPIT_DA_WINDOW if market == "DAM" else COCKPIT_RT_WINDOW


def _cockpit_effective_window(window, market, view) -> tuple:
    """Resolve the (label, delta) a tile serves. An explicit window="24h"|"7d"
    overrides; when absent (None), the per-view default is preserved verbatim —
    24h for RT lmp/components/basis and dart, 7d for DA — so default payloads are
    byte-for-byte unchanged from v0.1."""
    if window in COCKPIT_WINDOWS:
        return window, COCKPIT_WINDOWS[window]
    # Default (window is None): the v0.1 per-view behavior.
    if view == "dart":
        return "24h", COCKPIT_DART_WINDOW
    if market == "DAM":
        return "7d", COCKPIT_DA_WINDOW
    return "24h", COCKPIT_RT_WINDOW


def _cockpit_window(rows, delta):
    """Rows (ascending by `ts`) within [latest.ts - delta, latest.ts]. Anchored
    on the latest AVAILABLE interval so a stale board still renders its window."""
    if not rows:
        return []
    end = rows[-1]["ts"]
    start = end - delta
    return [r for r in rows if r["ts"] >= start]


def _cockpit_components(row) -> dict:
    """The five price components of one snapshot row (nulls preserved)."""
    return {
        "lmp": row["lmp"], "energy": row["energy"], "congestion": row["congestion"],
        "loss": row["loss"], "ghg": row["ghg"],
    }


# ── Per-view builders. Each is pure: (rows already shaped + sorted ascending)
# -> (as_of_dt | None, body_dict). as_of=None ⟹ honest empty tile. ────────────

def _cockpit_build_lmp(rows, delta):
    """lmp view: latest LMP + its components + an LMP sparkline over `delta`."""
    if not rows:
        return None, {"latest": None, "sparkline": []}
    win = _cockpit_window(rows, delta)
    latest = rows[-1]
    return latest["ts"], {
        "latest": _cockpit_components(latest),
        "sparkline": [{"ts_utc": r["ts"].isoformat(), "lmp": r["lmp"]} for r in win],
    }


def _cockpit_build_components(rows, delta):
    """components view: latest energy/congestion/loss/ghg + a congestion sparkline."""
    if not rows:
        return None, {"latest": None, "congestion_sparkline": []}
    win = _cockpit_window(rows, delta)
    latest = rows[-1]
    return latest["ts"], {
        "latest": {
            "energy": latest["energy"], "congestion": latest["congestion"],
            "loss": latest["loss"], "ghg": latest["ghg"],
        },
        "congestion_sparkline": [
            {"ts_utc": r["ts"].isoformat(), "congestion": r["congestion"]} for r in win
        ],
    }


def _cockpit_build_dart(da_rows, rt_rows, delta):
    """dart view: DA(HE) − RT(aligned interval), a signed hourly spread + run.

    RT intervals are bucketed to their clock hour (UTC floor == PT hour start,
    per the hub reader) and averaged; a spread is emitted only for hours that
    carry a DA price AND >=1 RT interval. Sign per spec/hub reader: POSITIVE =
    DA over RT. DAM interval-start already lands on UTC :00, the hour key.
    """
    rt_by_hour: dict = {}
    for r in rt_rows:
        if r["lmp"] is None:
            continue
        hk = r["ts"].replace(minute=0, second=0, microsecond=0)
        rt_by_hour.setdefault(hk, []).append(r["lmp"])

    spreads = []  # (hour_key_dt, entry)
    for r in da_rows:
        if r["lmp"] is None:
            continue
        hk = r["ts"].replace(minute=0, second=0, microsecond=0)
        ivs = rt_by_hour.get(hk)
        if not ivs:
            continue
        avg_rt = sum(ivs) / len(ivs)
        spreads.append((hk, {
            "ts_utc": hk.isoformat(),
            "spread": round(r["lmp"] - avg_rt, 5),
            "da": r["lmp"],
            "rt": round(avg_rt, 5),
        }))

    if not spreads:
        return None, {"latest": None, "run": []}
    end = spreads[-1][0]
    start = end - delta
    run = [e for (hk, e) in spreads if hk >= start]
    return end, {"latest": run[-1], "run": run}


def _cockpit_build_basis(rows, hub_ts_map, delta):
    """basis view: node − hub_ref at UTC-aligned same-market intervals.

    basis = snapshot.lmp − hub.value on a common interval-start instant. An
    interval with no aligned hub value is SKIPPED (honest — never fabricated).
    """
    aligned = []  # (ts_dt, entry)
    for r in rows:
        if r["lmp"] is None:
            continue
        hv = hub_ts_map.get(r["ts"])
        if hv is None:
            continue
        aligned.append((r["ts"], {
            "ts_utc": r["ts"].isoformat(),
            "basis": round(r["lmp"] - hv, 5),
            "node": r["lmp"],
            "hub": hv,
        }))

    if not aligned:
        return None, {"latest": None, "run": []}
    end = aligned[-1][0]
    start = end - delta
    run = [e for (ts, e) in aligned if ts >= start]
    return end, {"latest": run[-1], "run": run}


# ── Connection lifecycle ─────────────────────────────────────────────────────
# Neon idle-closes server-side sockets, so the shared pool can hand out a
# connection whose TCP/SSL layer is already dead — the first statement then
# raises "SSL connection has been closed unexpectedly" (psycopg.OperationalError)
# / an InterfaceError. The proven endpoints acquire the same way
# (`async with _pool.connection()`), and this lane conforms to that per-request
# pattern; the ONE addition is recovery: run the read, and on a dropped
# connection RETRY ONCE. Re-entering `_pool.connection()` returns (and the pool
# discards) the stale connection, so the retry runs on a live one. A read that
# raises anything else, or fails twice, propagates to the caller unchanged.
_COCKPIT_DEAD_CONN_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)


async def _cockpit_read(sql: str, params: dict) -> list:
    """Acquire from the shared pool, run one query, return its rows — retrying
    ONCE on a dropped/stale connection (see the note above). Mirrors the app's
    per-request acquisition; it never opens a module-level or long-lived
    connection of its own."""
    assert _pool is not None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            async with _pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    return await cur.fetchall()
        except _COCKPIT_DEAD_CONN_ERRORS as e:
            last_exc = e  # stale connection — loop retries on a fresh one
    assert last_exc is not None
    raise last_exc


# ── Node search ──────────────────────────────────────────────────────────────
# Universe = the latest instant (index-driven). A node qualifies if its pnode_id
# matches `q` OR it is reached from a plant whose name matches `q` via the Atlas
# plant<->pnode crosswalk (atlas_pnode_crosswalk.plant_code -> atlas_plants).
# plant_match collapses the (tiny) crosswalk to one row per pnode carrying the
# q-matching plant name (if any) and a representative plant name; the result is
# INTERSECTED with the priced universe (a LEFT JOIN from universe), so only real,
# currently-priced nodes are ever returned and each still carries node_type/area.
# plant_name is surfaced when known (required for a crosswalk match).
_NODE_SEARCH_SQL = """
    WITH latest AS (
        SELECT market_date, market_hour, market_interval
        FROM atlas_pnode_lmp_snapshot
        WHERE market = %(market)s
          AND market_date >= %(sentinel)s
        ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
        LIMIT 1
    ),
    universe AS (
        SELECT s.pnode_id, s.node_type, s.area
        FROM atlas_pnode_lmp_snapshot s, latest l
        WHERE s.market = %(market)s
          AND s.market_date = l.market_date
          AND s.market_hour = l.market_hour
          AND s.market_interval IS NOT DISTINCT FROM l.market_interval
          AND (%(area)s::text IS NULL OR upper(s.area) = upper(%(area)s))
          AND (%(node_type)s::text IS NULL OR upper(s.node_type) = upper(%(node_type)s))
    ),
    plant_match AS (
        SELECT x.pnode_id,
               min(p.plant_name) FILTER (
                   WHERE p.plant_name ILIKE %(q)s ESCAPE '\\'
               ) AS matched_plant,
               min(p.plant_name) AS any_plant
        FROM atlas_pnode_crosswalk x
        JOIN atlas_plants p ON p.plant_code = x.plant_code
        GROUP BY x.pnode_id
    )
    SELECT u.pnode_id, u.node_type, u.area,
           COALESCE(pm.matched_plant, pm.any_plant) AS plant_name,
           (pm.matched_plant IS NOT NULL) AS matched_via_plant
    FROM universe u
    LEFT JOIN plant_match pm ON pm.pnode_id = u.pnode_id
    WHERE u.pnode_id ILIKE %(q)s ESCAPE '\\'
       OR pm.matched_plant IS NOT NULL
    ORDER BY u.pnode_id
    LIMIT %(limit)s
"""


@app.get("/api/nodes/search")
async def nodes_search(
    q: str = Query(
        default="",
        description=(
            "Case-insensitive substring matched against pnode_id. Empty browses "
            "the first matches by pnode_id. LIKE metacharacters are matched "
            "literally."
        ),
    ),
    area: Optional[str] = Query(
        default=None,
        description="Optional exact area filter (case-insensitive), e.g. CA, BPAT.",
    ),
    node_type: Optional[str] = Query(
        default=None,
        description="Optional exact node_type filter (case-insensitive), e.g. GEN, LOAD.",
    ),
):
    """
    Cockpit node search — up to 50 snapshot-distinct nodes.

    Returns {pnode_id, node_type, area, plant_name?} for nodes whose pnode_id
    contains `q` (case-insensitive substring) OR that are reached from a plant
    whose name matches `q` via the Atlas plant<->pnode crosswalk — so "Alamitos"
    or "Diablo" finds the ALAMT*/DIABLO* nodes. Optionally narrowed by exact
    area / node_type. The universe is the latest DAM instant (all markets price
    the same node set), so the read is index-driven — never a scan of all
    history — and results are always real, currently-priced nodes.

    `plant_name` is present when a plant is associated with the node (always for
    a crosswalk match; `matched_via_plant` flags how the node qualified); it is
    null for a pnode_id-only match with no crosswalk entry.

    Response:
        {
          "query": "Alamitos", "area": null, "node_type": null, "count": 5,
          "matches": [{"pnode_id": "ALAMT3G_7_B1", "node_type": "GEN",
                       "area": "CA", "plant_name": "AES Alamitos LLC",
                       "matched_via_plant": true}, ...]
        }

    No matches -> 200 with an empty list. DB unavailable -> 503.
    """
    assert _pool is not None

    q_clean = q.strip()
    area_f = area.strip() if isinstance(area, str) and area.strip() else None
    node_type_f = node_type.strip() if isinstance(node_type, str) and node_type.strip() else None
    params = {
        "market": NODE_SEARCH_MARKET,
        "sentinel": COCKPIT_SENTINEL_MIN_DATE,
        "q": "%" + _cockpit_like_escape(q_clean) + "%",
        "area": area_f,
        "node_type": node_type_f,
        "limit": NODE_SEARCH_MAX,
    }

    # Single (non-tiled) endpoint: recover a stale connection via retry-once, and
    # surface any hard failure as the house-standard 503 (conforms to the proven
    # endpoints — a search either queries fine or is honestly unavailable).
    try:
        rows = await _cockpit_read(_NODE_SEARCH_SQL, params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    matches = [
        {
            "pnode_id": r["pnode_id"],
            "node_type": r["node_type"],
            "area": r["area"],
            "plant_name": r["plant_name"],
            "matched_via_plant": bool(r["matched_via_plant"]),
        }
        for r in rows
    ]
    return {
        "query": q_clean,
        "area": area_f,
        "node_type": node_type_f,
        "count": len(matches),
        "matches": matches,
    }


# ── Facets ────────────────────────────────────────────────────────────────────
# Dropdown feed for the Cockpit node picker (v0.3): the distinct `area` and
# `node_type` categories — each with its node count — that actually exist in the
# node universe, so the client can offer real, node-backed choices instead of a
# blind text box.
#
# UNIVERSE = the EXACT universe /api/nodes/search browses: the latest DAM instant
# (index-driven LIMIT 1), P2-sentinel-floored, all three markets pricing the same
# node set (P1). Sharing the universe is the whole point — a category offered
# here is guaranteed to return nodes there; the dropdowns never promise a filter
# the search can't satisfy.
#
# One query. The `latest`/`universe` CTEs are byte-for-byte the search lane's
# (minus the pnode_id/plant projection), then two GROUPed aggregates are stacked
# with a `kind` discriminator. `latest` is cross-joined into each branch so every
# row carries the instant identity (it is a single row -> constant, so grouping
# by it changes nothing) — that is the `as_of` reconstruction input, read once.
# NULL area/node_type rows are dropped in the Python layer: a null category is
# not a value search's `upper(area) = upper(%(area)s)` filter can ever match, so
# offering it would break the "never promise nodes the search can't find" rule.
_NODE_FACETS_SQL = """
    WITH latest AS (
        SELECT market_date, market_hour, market_interval
        FROM atlas_pnode_lmp_snapshot
        WHERE market = %(market)s
          AND market_date >= %(sentinel)s
        ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
        LIMIT 1
    ),
    universe AS (
        SELECT s.node_type, s.area
        FROM atlas_pnode_lmp_snapshot s, latest l
        WHERE s.market = %(market)s
          AND s.market_date = l.market_date
          AND s.market_hour = l.market_hour
          AND s.market_interval IS NOT DISTINCT FROM l.market_interval
    )
    SELECT 'area' AS kind, u.area AS label, count(*) AS n,
           l.market_date AS md, l.market_hour AS mh, l.market_interval AS mi
    FROM universe u CROSS JOIN latest l
    GROUP BY u.area, l.market_date, l.market_hour, l.market_interval
    UNION ALL
    SELECT 'node_type' AS kind, u.node_type AS label, count(*) AS n,
           l.market_date AS md, l.market_hour AS mh, l.market_interval AS mi
    FROM universe u CROSS JOIN latest l
    GROUP BY u.node_type, l.market_date, l.market_hour, l.market_interval
"""

# In-process cache. The universe turns over once a day (a new DAM instant), so a
# single slot on a 5-min TTL — mirroring the constraints/fingerprint discipline —
# is ample and keeps a picker-open storm off the DB. No params -> one slot.
_NODE_FACETS_CACHE_TTL = 300.0  # 5 minutes
_node_facets_cache: "tuple[float, dict] | None" = None


@app.get("/api/nodes/facets")
async def nodes_facets(response: Response):
    """
    Cockpit facets — the selectable `area` and `node_type` categories, counted.

    Feeds the node picker's dropdowns (v0.3). Each entry is a category that
    genuinely exists in the node universe /api/nodes/search browses — the latest
    DAM instant, P2-floored — with the number of nodes carrying it, so the client
    can render "LOAD · 8,214" and know the filter will return that many nodes.
    Lists are ordered by count descending, then label ascending. NULL categories
    (not matchable by search's exact filter) are omitted.

    Response:
        {
          "areas":      [{"area": "CA", "count": 12043}, ...],
          "node_types": [{"node_type": "LOAD", "count": 8214}, ...],
          "as_of": "2026-07-21T07:00:00+00:00"   // latest DAM instant, UTC (P5)
        }

    Empty universe -> empty lists, as_of null. DB unavailable -> 503. The per-list
    counts sum to the (non-null) universe size. Cached in-process (5-min TTL).
    """
    global _node_facets_cache
    assert _pool is not None

    now_mono = time.monotonic()
    if _node_facets_cache is not None and (now_mono - _node_facets_cache[0]) < _NODE_FACETS_CACHE_TTL:
        response.headers["Cache-Control"] = "max-age=300"
        return _node_facets_cache[1]

    params = {"market": NODE_SEARCH_MARKET, "sentinel": COCKPIT_SENTINEL_MIN_DATE}
    try:
        rows = await _cockpit_read(_NODE_FACETS_SQL, params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    areas: list[dict] = []
    node_types: list[dict] = []
    as_of = None
    for r in rows:
        if r["label"] is None:  # a null category is not a search-selectable filter
            continue
        if as_of is None:
            as_of = _cockpit_interval_start_utc(
                NODE_SEARCH_MARKET, r["md"], r["mh"], r["mi"]
            ).isoformat()
        if r["kind"] == "area":
            areas.append({"area": r["label"], "count": int(r["n"])})
        else:
            node_types.append({"node_type": r["label"], "count": int(r["n"])})

    # Biggest categories first (then alphabetical) — the useful dropdown order.
    areas.sort(key=lambda d: (-d["count"], d["area"]))
    node_types.sort(key=lambda d: (-d["count"], d["node_type"]))

    payload = {"areas": areas, "node_types": node_types, "as_of": as_of}
    _node_facets_cache = (now_mono, payload)
    response.headers["Cache-Control"] = "max-age=300"
    return payload


# ── Watchboard batch ──────────────────────────────────────────────────────────
# One snapshot query per distinct market (pnode_id = ANY, P2 floor -> retained
# window, PK bitmap index); one hub query for every basis tile at once.
_COCKPIT_SNAPSHOT_SQL = """
    SELECT pnode_id, market_date, market_hour, market_interval,
           lmp, energy, congestion, loss, ghg,
           feed_generated_at, snapshot_vintage
    FROM atlas_pnode_lmp_snapshot
    WHERE market = %(market)s
      AND pnode_id = ANY(%(pnodes)s)
      AND market_date >= %(sentinel)s
    ORDER BY pnode_id, market_date, market_hour, market_interval
"""

_COCKPIT_HUB_SQL = """
    SELECT ts, dataset, series, value
    FROM timeseries_values
    WHERE dataset = ANY(%(datasets)s)
      AND series  = ANY(%(series)s)
      AND value IS NOT NULL
      AND ts >= %(lo)s
    ORDER BY dataset, series, ts
"""


def _watchboard_validate_tile(t: dict) -> dict:
    """Per-tile validation. Returns a plan dict — either {"error": ...[, detail]}
    (an honest per-tile error) or a resolved
    {"pnode", "market", "view", "hub_ref"} spec. Never raises."""
    if t.get("tile_type") != WATCHBOARD_TILE_TYPE:
        # v0 implements pnode only; anything else is an explicit per-tile error.
        return {"error": "unknown_tile_type"}

    view = t.get("view")
    view_n = view.strip().lower() if isinstance(view, str) else ""
    if view_n not in WATCHBOARD_VIEWS:
        return {"error": "unknown_view", "detail": f"valid views: {list(WATCHBOARD_VIEWS)}"}

    pid = t.get("pnode_id")
    if not isinstance(pid, str) or not pid.strip():
        return {"error": "missing_pnode_id"}
    pid = pid.strip()

    market = t.get("market")
    market_n = market.strip().upper() if isinstance(market, str) else ""
    if market_n not in _PNODE_LMP_MARKET_SET:
        return {
            "error": "unknown_market",
            "detail": (
                f"valid markets: {list(PNODE_LMP_MARKETS)} "
                "('FMM' is a display name — send RTPD)"
            ),
        }

    # dart is DA − RT: the tile's market is the RT leg, so DAM is nonsensical.
    if view_n == "dart" and market_n == "DAM":
        return {
            "error": "dart_requires_rt_market",
            "detail": "dart is DA − RT; send market RTD or RTPD (the RT leg).",
        }

    hub_ref = None
    if view_n == "basis":
        raw = t.get("hub_ref")
        hub_ref = raw.strip().upper() if isinstance(raw, str) and raw.strip() else COCKPIT_HUB_DEFAULT_REF
        if hub_ref not in COCKPIT_HUB_REF_SET:
            return {
                "error": "unknown_hub_ref",
                "detail": f"valid hub_ref: {sorted(COCKPIT_HUB_REF_SET)}",
            }

    # Optional window override (v0.2). Absent -> None (per-view default). Checked
    # last so it never changes the precedence of the existing per-tile errors.
    window = t.get("window")
    if window is not None:
        window = window.strip().lower() if isinstance(window, str) else window
        if window not in COCKPIT_WINDOWS:
            return {
                "error": "bad_window",
                "detail": f"valid windows: {sorted(COCKPIT_WINDOWS)}",
            }

    return {
        "pnode": pid, "market": market_n, "view": view_n,
        "hub_ref": hub_ref, "window": window,
    }


@app.post("/api/watchboard")
async def watchboard(request: Request):
    """
    Cockpit / Watchboard v0 — a batched, per-tile-isolated board read.

    Body: {"tiles": [{"tile_id", "tile_type": "pnode", "pnode_id", "market",
    "view": lmp|components|dart|basis, "hub_ref"?, "window"?}]} — at most 24
    tiles. Optional per-tile window="24h"|"7d" (v0.2) overrides the per-view
    default (24h RT · 7d DA · 24h dart); an unknown value is a per-tile
    {error: "bad_window"}. "7d" on an RT view widens to the retained series.

    Response is keyed by tile_id; every tile carries `as_of` + `lag_minutes`,
    the `window` it served and its `points` count, then a payload by view
    (lmp / components / dart / basis). Per-tile failures are honest per-tile
    error objects; the batch always returns 200. See the module header for the
    full error and time-alignment contracts.

        {
          "count": 2,
          "tiles": {
            "t1": {"tile_type": "pnode", "pnode_id": "...", "market": "RTD",
                   "view": "lmp", "window": "24h", "points": 288,
                   "as_of": "...Z", "lag_minutes": 15,
                   "empty": false, "latest": {...}, "sparkline": [...]},
            "t2": {"error": "unknown_tile_type"}
          }
        }
    """
    assert _pool is not None

    # ── Envelope validation (the only 400s) ─────────────────────────────────
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be valid JSON.")
    if not isinstance(body, dict) or not isinstance(body.get("tiles"), list):
        raise HTTPException(
            status_code=400, detail="body must be an object with a 'tiles' array."
        )
    tiles = body["tiles"]
    if len(tiles) > WATCHBOARD_MAX_TILES:
        raise HTTPException(
            status_code=400,
            detail=f"too many tiles: {len(tiles)} > {WATCHBOARD_MAX_TILES}.",
        )

    # tile_id is the response key: it must be present, a non-empty string, and
    # unique. A violation corrupts the keyed contract, so it is an envelope 400.
    keys = []
    for i, t in enumerate(tiles):
        if not isinstance(t, dict):
            raise HTTPException(status_code=400, detail=f"tile[{i}] must be an object.")
        tid = t.get("tile_id")
        if not isinstance(tid, str) or not tid.strip():
            raise HTTPException(
                status_code=400,
                detail=f"tile[{i}] is missing a string tile_id (the response key).",
            )
        keys.append(tid)
    if len(set(keys)) != len(keys):
        raise HTTPException(
            status_code=400, detail="tile_id values must be unique (they key the response)."
        )

    # ── Per-tile validation + data-requirement collection ───────────────────
    plans: dict = {}
    snap_need: dict = {}          # market -> set(pnode_id)
    hub_need_datasets: set = set()
    hub_need_series: set = set()
    for t in tiles:
        tid = t["tile_id"]
        plan = _watchboard_validate_tile(t)
        plans[tid] = plan
        if "error" in plan:
            continue
        snap_need.setdefault(plan["market"], set()).add(plan["pnode"])
        if plan["view"] == "dart":
            snap_need.setdefault("DAM", set()).add(plan["pnode"])  # the DA leg
        if plan["view"] == "basis":
            hub_need_datasets.add(COCKPIT_MARKET_TO_HUB[plan["market"]])
            hub_need_series.add(plan["hub_ref"])

    # ── Batched reads: one query per distinct market + one hub query, each with
    # retry-once recovery (see _cockpit_read — Neon idle-closes sockets). A
    # source that STILL fails after the retry is RECORDED, not raised: the tiles
    # depending on it get an honest per-tile db_error below, while tiles whose
    # data loaded fine still render. This is the contract's distinction —
    # db_error = "the read failed"; empty = "queried fine, zero rows".
    snap: dict = {}              # (market, pnode) -> ascending rows [{ts, lmp, ...}]
    hub_map: dict = {}           # (dataset, series) -> {ts_utc: value}
    failed_markets: set = set()  # markets whose snapshot read failed (post-retry)
    hub_failed = False           # the hub read failed (post-retry)

    for market_n, pnodes in snap_need.items():
        try:
            rows = await _cockpit_read(
                _COCKPIT_SNAPSHOT_SQL,
                {
                    "market": market_n,
                    "pnodes": list(pnodes),
                    "sentinel": COCKPIT_SENTINEL_MIN_DATE,
                },
            )
        except Exception:
            _cockpit_log.exception("watchboard snapshot read failed: market=%s", market_n)
            failed_markets.add(market_n)
            continue
        for r in rows:
            ts = _cockpit_interval_start_utc(
                market_n, r["market_date"], r["market_hour"], r["market_interval"],
            )
            snap.setdefault((market_n, r["pnode_id"]), []).append({
                "ts": ts,
                "lmp": _cockpit_num(r["lmp"]),
                "energy": _cockpit_num(r["energy"]),
                "congestion": _cockpit_num(r["congestion"]),
                "loss": _cockpit_num(r["loss"]),
                "ghg": _cockpit_num(r["ghg"]),
            })

    if hub_need_datasets:
        try:
            rows = await _cockpit_read(
                _COCKPIT_HUB_SQL,
                {
                    "datasets": list(hub_need_datasets),
                    "series": list(hub_need_series),
                    "lo": _utcnow() - _timedelta(days=8),
                },
            )
        except Exception:
            _cockpit_log.exception("watchboard hub read failed")
            hub_failed = True
        else:
            for r in rows:
                if r["value"] is None:
                    continue
                ts = r["ts"].astimezone(_timezone.utc)
                hub_map.setdefault((r["dataset"], r["series"]), {})[ts] = float(r["value"])

    # ── Shape each tile (isolated: one tile's bug never sinks the batch) ─────
    now = _utcnow()
    out: dict = {}
    for tid, plan in plans.items():
        if "error" in plan:
            out[tid] = {"error": plan["error"]}
            if "detail" in plan:
                out[tid]["detail"] = plan["detail"]
            continue

        pid, market_n, view_n, hub_ref = (
            plan["pnode"], plan["market"], plan["view"], plan["hub_ref"]
        )

        # A required source that failed its read (post-retry) -> honest per-tile
        # db_error, NEVER an empty tile. dart needs its RT market AND DAM; basis
        # needs its market AND the hub.
        if (
            market_n in failed_markets
            or (view_n == "dart" and "DAM" in failed_markets)
            or (view_n == "basis" and hub_failed)
        ):
            out[tid] = {"error": "db_error"}
            continue

        try:
            rows = snap.get((market_n, pid), [])
            win_label, delta = _cockpit_effective_window(plan["window"], market_n, view_n)

            if view_n == "lmp":
                as_of, body_out = _cockpit_build_lmp(rows, delta)
                series = body_out["sparkline"]
            elif view_n == "components":
                as_of, body_out = _cockpit_build_components(rows, delta)
                series = body_out["congestion_sparkline"]
            elif view_n == "dart":
                da_rows = snap.get(("DAM", pid), [])
                as_of, body_out = _cockpit_build_dart(da_rows, rows, delta)
                body_out = {"da_market": "DAM", "rt_market": market_n, **body_out}
                series = body_out["run"]
            else:  # basis
                ds = COCKPIT_MARKET_TO_HUB[market_n]
                hub_ts_map = hub_map.get((ds, hub_ref), {})
                as_of, body_out = _cockpit_build_basis(rows, hub_ts_map, delta)
                body_out = {"hub_ref": hub_ref, **body_out}
                series = body_out["run"]

            lag = None if as_of is None else max(0, int((now - as_of).total_seconds() // 60))
            out[tid] = {
                "tile_type": WATCHBOARD_TILE_TYPE,
                "pnode_id": pid,
                "market": market_n,
                "view": view_n,
                # v0.2 additive fields: the window actually served + its point
                # count (the sparkline/run shapes above are unchanged).
                "window": win_label,
                "points": len(series),
                "as_of": as_of.isoformat() if as_of is not None else None,
                "lag_minutes": lag,
                "empty": as_of is None,
                **body_out,
            }
        except Exception as e:  # noqa: BLE001 — per-tile isolation is the contract
            _cockpit_log.exception("watchboard tile %s failed", tid)
            out[tid] = {"error": "internal_error", "detail": str(e)}

    return {"count": len(out), "tiles": out}


# ═══════════════════════════════════════════════════════════════════════════
# Model Room — R2 archive proxy (D2 render frames)
# ═══════════════════════════════════════════════════════════════════════════
#
# Two read-only proxy endpoints onto the D2 render archive in Cloudflare R2
# (the ratified Model Room spec; the key scheme is FROZEN). The FHR-sequence
# render leg writes under the extended layout (pantry d2/sequence.py):
#
#   d2/renders/{model}/{date}/{cycle}Z/{param}/{region}/f{fhr:03d}.png
#   d2/renders/{model}/{date}/{cycle}Z/manifest.json   ← written LAST
#     model   e.g. "gfs"       (lowercase feed slug)
#     date    e.g. "20260723"  (YYYYMMDD, the run's UTC date)
#     cycle   e.g. "00"        (two-digit UTC synoptic hour; the "Z" is the
#                               literal partition suffix carried in the key)
#
# The manifest is written LAST by the render CLI, and only after its
# fail-fraction gate passes — so its PRESENCE is the honest "this cycle is
# complete-as-declared" assertion. The Phase-A seed objects at the DIFFERENT
# prefix d2/synoptic/{model}/{date}/{cycle}Z/ are untouched and still servable
# through /frame, but they no longer make a cycle "available" (see the seam
# flip in _cycle_is_available: manifest-presence over d2/renders/ replaced the
# seed-era object COUNT over d2/synoptic/, so those loose seed cycles now drop
# off the /cycles listing — ruled-expected).
#
#   GET /api/model-room/cycles?model=gfs&days=7
#       → [{model, date, cycle}] for the cycles whose render manifest is present
#         under d2/renders/{model}/ within the last `days` UTC dates, newest
#         first. Availability = manifest-presence — see _cycle_is_available.
#   GET /api/model-room/frame/{key}
#       → streams the R2 object (PNG or JSON) with a long immutable cache. The
#         key is VALIDATED against the d2/ prefix allowlist (no traversal, no
#         other prefixes) BEFORE it ever touches R2.
#
# Auth: a READ-ONLY R2 token scoped to d2/ on the archive bucket, provisioned by
# the captain (account-token doctrine) and supplied via the R2_* env vars below.
# When those are absent the endpoints answer 503 and the rest of the API is
# unaffected — boto3/R2 is imported and touched ONLY inside this section, lazily.
# ═══════════════════════════════════════════════════════════════════════════

# R2 (S3-compatible) config — all via environment (set in Railway). The token is
# READ-ONLY and d2/-scoped; this service never writes to the archive bucket.
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_ARCHIVE_BUCKET = os.environ.get("R2_ARCHIVE_BUCKET", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
# Endpoint defaults to the account's R2 S3 endpoint; override only if the token
# is issued against a custom/jurisdictional endpoint.
R2_ENDPOINT = os.environ.get("R2_ENDPOINT", "") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else ""
)

# The ONE allowed prefix + the frozen key scheme. The Model Room serves only the
# d2/ subtree; nothing else is reachable through the frame proxy. Cycle listing
# reads the render leg's d2/renders/ subtree; the frame proxy still serves ANY
# d2/ key (d2/renders AND the seed-era d2/synoptic objects alike).
_MODEL_ROOM_KEY_PREFIX = "d2/"
_MODEL_ROOM_RENDERS_ROOT = "d2/renders/"
_MODEL_ROOM_MANIFEST_LEAF = "manifest.json"
_MODEL_ROOM_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_MODEL_ROOM_DATE_RE = re.compile(r"^\d{8}$")          # YYYYMMDD partition token
_MODEL_ROOM_CYCLE_DIR_RE = re.compile(r"^(\d{2})Z$")  # "18Z" -> cycle "18"
_MODEL_ROOM_MAX_DAYS = 31

# Content types the proxy serves. R2's stored ContentType wins; this is the
# fallback when the object carries none.
_FRAME_CONTENT_TYPES = {
    ".png": "image/png",
    ".json": "application/json",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_FRAME_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"

_r2_client = None


def _model_room_configured() -> bool:
    """True once the captain has provisioned the read-only R2 token (all four
    connection env vars present). Endpoints answer 503 until then."""
    return bool(R2_ARCHIVE_BUCKET and R2_ACCESS_KEY_ID
                and R2_SECRET_ACCESS_KEY and R2_ENDPOINT)


def _get_r2_client():
    """Lazily build (and cache) the read-only S3 client for R2. boto3 is imported
    HERE, not at module top, so the rest of the API neither imports nor depends
    on it until a Model Room route is actually hit."""
    global _r2_client
    if _r2_client is None:
        try:
            import boto3
            from botocore.config import Config as _BotoConfig
        except ImportError as e:  # pragma: no cover - deploy misconfiguration
            raise HTTPException(
                status_code=503,
                detail=f"model room storage client unavailable: {e}",
            )
        _r2_client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",  # R2 ignores region, but SigV4 still needs one
            config=_BotoConfig(
                signature_version="s3v4",
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _r2_client


def _r2_error_code(e) -> str:
    """Best-effort S3 error code from a botocore ClientError (or the class name)."""
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        return str(resp.get("Error", {}).get("Code") or type(e).__name__)
    return type(e).__name__


def _r2_error_status(e) -> int:
    """A genuinely-absent object is a 404; anything else (auth/config/transport)
    is our side, surfaced as 502 so the caller never sees an opaque 500."""
    return 404 if _r2_error_code(e) in ("NoSuchKey", "404", "NoSuchBucket") else 502


def _validate_frame_key(key: str) -> str:
    """Validate a frame key against the d2/ allowlist. Rejects traversal,
    absolute paths, empty/dot segments, NUL/backslash, and any prefix other than
    d2/. Returns the key unchanged when safe; raises HTTPException otherwise.

    This runs BEFORE any R2 call, so a hostile key never reaches the bucket.
    """
    if not key or "\x00" in key or "\\" in key:
        raise HTTPException(status_code=400, detail="invalid frame key")
    # Reject absolute paths and any traversal / empty / dot segments. A leading
    # slash yields an empty first segment; a trailing slash (a directory, not an
    # object) yields an empty last segment — both caught here, as is "..".
    segments = key.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise HTTPException(status_code=400, detail="invalid frame key (traversal)")
    # Single allowed prefix; the key must live UNDER it (never the bare prefix).
    if segments[0] != "d2" or not key.startswith(_MODEL_ROOM_KEY_PREFIX):
        raise HTTPException(
            status_code=403, detail="frame key outside the d2/ allowlist")
    return key


def _frame_content_type(key: str) -> str:
    dot = key.rfind(".")
    ext = key[dot:].lower() if dot != -1 else ""
    return _FRAME_CONTENT_TYPES.get(ext, "application/octet-stream")


def _r2_common_prefixes(client, bucket: str, prefix: str) -> list[str]:
    """Immediate child 'directory' names under prefix (delimiter='/'), stripped
    of the prefix and the trailing slash. Paginated."""
    out: list[str] = []
    token = None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        for cp in resp.get("CommonPrefixes", []):
            child = cp.get("Prefix", "")[len(prefix):].rstrip("/")
            if child:
                out.append(child)
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


def _cycle_manifest_key(model: str, date: str, cycle: str) -> str:
    """The render-cycle completion manifest key under d2/renders/ — the exact
    object pantry's d2.sequence.sequence_manifest_key writes:
    d2/renders/{model}/{YYYYMMDD}/{cc}Z/manifest.json. `cycle` is the two-digit
    dir token (e.g. "00"), carried through as the "Z"-suffixed partition."""
    return (f"{_MODEL_ROOM_RENDERS_ROOT}{model}/{date}/{cycle}Z/"
            f"{_MODEL_ROOM_MANIFEST_LEAF}")


def _cycle_is_available(client, bucket: str, manifest_key: str) -> bool:
    """AVAILABILITY SEAM (manifest-presence). A (model, date, cycle) is available
    iff its render manifest object exists under d2/renders/ — the manifest the
    pantry d2.sequence CLI writes LAST, only once its fail-fraction gate passes.
    Presence IS the whole assertion; the seed-era d2/synoptic object-COUNT path
    is gone (seed cycles with no manifest under d2/renders drop off the listing —
    ruled-expected). A MaxKeys=1 prefix list (not head_object) keeps this on the
    one S3 verb the rest of the module already speaks."""
    resp = client.list_objects_v2(Bucket=bucket, Prefix=manifest_key, MaxKeys=1)
    return any(obj.get("Key") == manifest_key for obj in resp.get("Contents", []))


@app.get("/api/model-room/cycles")
async def model_room_cycles(
    model: str = Query(..., description="feed slug, e.g. gfs"),
    days: int = Query(
        7, ge=1, le=_MODEL_ROOM_MAX_DAYS,
        description="how many UTC dates back to scan (inclusive of today)"),
):
    """The published render cycles for `model` over the last `days` UTC dates,
    newest first, wrapped in the platform envelope: {"cycles": [{model, date,
    cycle}]}. A cycle is 'published' when its render manifest object is present
    under d2/renders/{model}/{date}/{cycle}Z/manifest.json — manifest-presence is
    the availability assertion in _cycle_is_available.

    R2 unprovisioned → 503. Bad model slug → 400.
    """
    if not _MODEL_ROOM_MODEL_RE.match(model):
        raise HTTPException(status_code=400, detail="invalid model slug")
    if not _model_room_configured():
        raise HTTPException(status_code=503, detail="model room storage not configured")

    client = _get_r2_client()
    bucket = R2_ARCHIVE_BUCKET
    model_root = f"{_MODEL_ROOM_RENDERS_ROOT}{model}/"

    # UTC date cutoff (inclusive). date tokens are YYYYMMDD, so a lexicographic
    # compare against the cutoff token is identical to a chronological one.
    cutoff_token = (_utcnow().date() - _timedelta(days=days - 1)).strftime("%Y%m%d")

    def _list_cycles() -> list[tuple[str, str]]:
        date_dirs = _r2_common_prefixes(client, bucket, model_root)
        keep_dates = sorted(
            d for d in date_dirs
            if _MODEL_ROOM_DATE_RE.match(d) and d >= cutoff_token
        )
        found: list[tuple[str, str]] = []
        for date_token in keep_dates:
            date_root = f"{model_root}{date_token}/"
            # The cycle 'directories' under this date; a cycle is AVAILABLE only
            # when its manifest.json leaf exists (a render mid-flight has frames
            # but no manifest yet, so it is correctly excluded).
            for cyc_dir in _r2_common_prefixes(client, bucket, date_root):
                m = _MODEL_ROOM_CYCLE_DIR_RE.match(cyc_dir)
                if not m:
                    continue
                cycle = m.group(1)
                if _cycle_is_available(
                        client, bucket,
                        _cycle_manifest_key(model, date_token, cycle)):
                    found.append((date_token, cycle))
        return found

    try:
        found = await run_in_threadpool(_list_cycles)
    except HTTPException:
        raise
    except Exception as e:  # botocore ClientError / transport
        raise HTTPException(
            status_code=502, detail=f"cycle listing failed: {_r2_error_code(e)}")

    # Newest first: date desc, then cycle desc. Wrapped in the {"cycles": [...]}
    # platform envelope (the convention every other route follows — no bare array).
    found.sort(reverse=True)
    return {"cycles": [{"model": model, "date": d, "cycle": c} for d, c in found]}


@app.get("/api/model-room/frame/{key:path}")
async def model_room_frame(
    key: str = Path(..., description="R2 object key under d2/"),
):
    """Stream one archived frame object (PNG or JSON) from R2, long-immutable
    cached. The key is validated against the d2/ allowlist FIRST — no traversal,
    no prefixes other than d2/. Missing object → 404; R2 unprovisioned → 503.
    """
    safe_key = _validate_frame_key(key)
    if not _model_room_configured():
        raise HTTPException(status_code=503, detail="model room storage not configured")

    client = _get_r2_client()

    def _fetch():
        obj = client.get_object(Bucket=R2_ARCHIVE_BUCKET, Key=safe_key)
        return obj, obj["Body"].read()

    try:
        obj, body = await run_in_threadpool(_fetch)
    except HTTPException:
        raise
    except Exception as e:  # botocore ClientError / transport
        raise HTTPException(
            status_code=_r2_error_status(e),
            detail=f"frame fetch failed: {_r2_error_code(e)}")

    # Honor the object's stored content-type; fall back to the extension.
    ctype = obj.get("ContentType") or _frame_content_type(safe_key)
    headers = {"Cache-Control": _FRAME_IMMUTABLE_CACHE}
    etag = obj.get("ETag")
    if etag:
        headers["ETag"] = etag
    return Response(content=body, media_type=ctype, headers=headers)