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
    GET /api/timeseries/caiso-fuel-mix     fuel mix by hour (M2)
    GET /api/almanac/lmp-shape             LMP shape overlay (M2 — added May 29)
"""

import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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
# Dataset: 'caiso_fuel_mix_hourly' — 758k rows, 2020-01 -> current, hourly.
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

FUEL_MIX_DATASET = "caiso_fuel_mix_hourly"


@app.get("/api/timeseries/caiso-fuel-mix")
async def caiso_fuel_mix(
    limit: int = Query(
        default=24,
        ge=1,
        le=2000,
        description="Number of most-recent HOURS to return. Ignored when "
        "`date` or `start`/`end` is supplied.",
    ),
    date: str | None = Query(
        default=None,
        description="A single Pacific calendar day, YYYY-MM-DD. Returns that "
        "day's 24 hours. Mutually exclusive with start/end.",
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
      * date=YYYY-MM-DD            -> that single Pacific calendar day (24h)
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
        # Default mode: latest `limit` DISTINCT hours (not raw rows), so
        # `limit` means "hours" regardless of how many fuels report each hour.
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