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
    GET /api/almanac/lmp-shape             LMP shape overlay (M2 — added May 29). NOTE: a literal path inside the Almanac prefix; it wins over /api/almanac/{series} only because it is registered first
    GET /api/almanac                       The Almanac v0: the shelf — every published issue as a card, newest first, ?series= filter; bare array, read_minutes derived (2026-08-07)
    GET /api/almanac/{series}              The Almanac v0: one series page — latest issue in full + archive stubs behind it (latest never repeated in archive); intro from a code-side registry, null at v0 (2026-08-07)
    GET /api/almanac/{series}/{issue}      The Almanac v0: one issue in full, body blocks served exactly as stored; a draft is a 404, identical to an absence (2026-08-07)
    GET /api/newswire/recent               Joule Newswire items
    GET /api/tape/recent                   Joule Tape items (DEPRECATED — see /api/wire/recent)
    GET /api/briefs/daily/latest           most recent daily Joule brief (Tape 3a)
    GET /api/briefs/daily/{date}           daily Joule brief by date (Tape 3a)
    GET /api/wire/recent                   power-signal filings, successor to /api/tape/recent (Tape 3a)
    GET /api/weather/outlooks              CPC climate-outlook tab: shelves of outlook products (freshness + published graphic registry + discussion) + state outlooks, honest-absence + fail-loud (2026-07-29)
    GET /api/weather/dd/regions            Degree Day Ledger: the five ratified composite vectors — members, weights, vector version (v1), vintage; weight_sum reported not asserted (2026-08-10)
    GET /api/weather/dd/daily              Degree Day Ledger: daily composite HDD/CDD vs normals per (region, date), every completeness field the view publishes riding on every row; 3 ms underneath (2026-08-10)
    GET /api/weather/dd/cumulative         Degree Day Ledger: MTD + STD for every region on one date, off migration 164's views. THE SLOW ONE — ~52 s board-wide, measured — so it is built once for all five regions and cached 30 min with stale-while-revalidate; a NULL total stays null and carries its own day counts in `absence` (2026-08-10)
    GET /api/weather/dd/socalgas-grade     Degree Day Ledger: our composite vs SoCalGas's published composite + the delta, with window and all-time summaries over arbiter_present days only (2026-08-10)
    GET /api/weather/dd/forecast           Degree Day Ledger: newest forecast issuance per target date PLUS the run-over-run delta against the prior issuance; board is honestly empty until the builder's maiden run (2026-08-10)
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
    GET /api/analytics/structures/catalog  Structures room: the banked-reality menu — legs/blocks/gas indices with measured depth, cadence + staleness, cached (2026-07-30)
    POST /api/analytics/structures/evaluate Structures room: structure definition in, payoff diagram + month-by-month historical replay out, stateless (2026-07-30)
    GET /api/analytics/structures/screener Structures room: one structure swept across legs, ranked by realized payoff, bounded + runtime-stamped (2026-07-30)
    GET /api/analytics/node-search         Analytics Department v0 (Nodes room): find a priced node by pnode_id or Atlas plant name (2026-07-30)
    GET /api/analytics/node/{pnode_id}     Analytics Department v0 (Nodes room): the node package — identity/DART envelope/basis/components/monthly ledger, all over banked depth (2026-07-30)
    GET /api/analytics/movers              Analytics Department v0 (Nodes room): top-50 both directions by DART or basis over 1d/7d, index-served chunked reads, cached (2026-07-30)
    GET /api/analytics/node/{pnode_id}/ladder  Regime Package v0: per-HE percentile ladder + regime chip over banked depth; server-side depth floor (min/mid/max below n=15) (2026-07-30)
    GET /api/analytics/node/{pnode_id}/grid    Regime Package v0: HE1-24 x banked-dates heat grid with both margins, scale cap stated, dates never padded (2026-07-30)
    GET /api/analytics/tb4                 Regime Package v0: top-bottom-4 spread per day — hub scope full depth + monthly long view, node scope honest N, regime-shift flag with its arithmetic (2026-07-30)
    GET /api/analytics/movers-grid         Regime Package v0: top-25 both directions x HE1-24 window-mean cells; ranks via /movers in-process, fills 50 nodes off the PK, cached (2026-07-30)
    GET /api/analytics/node/{pnode_id}/daily   Nodal Daily Serving v0: the DURABLE atlas_node_daily_stats record newest-first (cap 90) — DART day/on/off-peak, both tb4 legs, basis, matched-hour counts, calendar flags, CB stamp; NERC 6x16 on-peak (NOT 7x16), Sunday/holiday NULL is calendar not absence, gaps never filled (2026-07-31)
    GET /api/analytics/nodes/{pnode_id}/hourly Node Hourly Regime v0: per-HE envelope/ladder/regime chip/stance/outliers over the last `days` days ENDING YESTERDAY from atlas_node_hourly_stats, plus today's partial read live from the snapshot (DAM+RTPD); today is never in the distribution it is placed against; per-HE coverage on every row, no depth-floor suppression, basis born complete but dark; `<metric>.history` carries the raw banked {trade_date, he, value} cells for the heat grid, present cells only, n_cells == n_banked_cells (2026-08-01)
    GET /api/desk/blotter                  Paper Desk v0: every paper_journal kind='bid' row, newest first, status OPEN/PENDING/SETTLED derived server-side (notes are never bids) (2026-07-31)
    GET /api/desk/book                     Paper Desk v0: desk totals — open count + gross MW, settled win/loss/flat, cumulative P&L, avg pnl/MWh; zero-fill is flat, no intraday mark (2026-07-31)
    GET /api/desk/equity                   Paper Desk v0: cumulative settled pnl_dollars by trade_date, settled rows only, gaps left as gaps (no interpolation) (2026-07-31)
    GET /api/desk/by-node                  Paper Desk v0: per-pnode aggregates — n, win rate, total P&L, avg pnl/MWh; nothing-settled nodes sort last, never dropped (2026-07-31)
    GET /api/desk/by-play                  Paper Desk v0: per-screen aggregates — writer carries no machine-readable play key, so play is null with the reason attached (2026-07-31)
    GET /api/nodes/{pnode_id}/package       Node Analytics v0: the whole per-node package in ONE call off ONE windowed PK read — identity (components_from derived per node) / per-HE dam+rt profile / month x HE heatmap / GridStatus-bin distribution / TB2+TB4 in $/kW-month / the six WECC blocks monthly + last-31-days daily / basis + DART / RT-coverage suppression rule on the wire; window derived from the node's own bank, absence never zero-filled (2026-08-12)
    GET /api/hubs/{hub}/blocks              Node Analytics v0: the same six blocks on the hub DA curve from timeseries_values (SP15/NP15/ZP26), measured depth 2016-01-01 onward — years deeper than the nodal bank; daily granularity bounded and the bound stated; reconciled to caiso_blocks_daily at 0.00000000 (2026-08-12)
    GET /api/nodes/top-movers               Node Analytics v0: top-50 by window-avg DART or basis_sp15 off the DURABLE hourly bank, hour-weighted, days_present on every row; chunked one index-served read per date because the whole-range GROUP BY is a measured 24.9 s seq scan — cap 7 days, stated (2026-08-12)
    GET /api/interconnection/summary       Interconnection Queue v0: every aggregate in ONE call — snapshot chip, by_fuel (rollup; active + all_statuses), by_queue_year (the 27-year vintage chart), by_proposed_cod_year (nulls counted as `undated`, never zero-dated), by_county (top 25 + named `other` remainder), by_transmission_owner, attrition per vintage (withdrawn = withdrawn_date IS NOT NULL, with the 39 status-WITHDRAWN-without-date rows reported beside it); the fuel rollup is a PARSE, so unmapped raw strings ride the response in `unmapped_types` + `Other.raw_types` (2026-08-12)
    GET /api/interconnection/projects      Interconnection Queue v0: the faceted table, paged (page_size <= 100) — status/fuel/county/to/min_mw/max_mw/queue_year/q filters, every filter a bound parameter and `sort`/`order` whitelisted KEYS into code-side SQL fragments (hostile sort -> 400); rows carry raw generation_type AND rollup fuel, plus first_seen_at and the withdrawn fields (2026-08-12)
    GET /api/interconnection/events        Interconnection Queue v0: tape_interconnection_events newest-first — the lineage the upsert-in-place queue table cannot give. Populated (8 status_change rows, 2026-06-12..07-31); an empty ledger is an honest 200 with rows:[] and a note, never a reconstruction (2026-08-12)
    GET /api/lab/paper-desk                Lab Paper Desk v0: the whole desk in ONE payload off ONE read — blotter (+ rationale, inputs_as_of, paper_bid_curves count) / equity by settled_at / by_node / by_play / book; status is settled|pending|VOID and voids ride the blotter, excluded from every aggregate and counted in book (2026-08-06)
"""

import asyncio
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
from typing import Any, Optional
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

# DD_WARM_ON_STARTUP : "0"/"false" opts a container out of pre-building the
#   Degree Day Ledger's cumulative board at boot (default on). The build is
#   detached and fail-soft either way; this only exists so a container that must
#   come up without touching the slow 164 views can say so.
DD_WARM_ON_STARTUP = os.environ.get(
    "DD_WARM_ON_STARTUP", "1").strip().lower() not in ("0", "false", "no", "off")

# A single shared async connection pool, opened on startup, closed on shutdown.
# Railway hobby + Neon both have modest connection caps, so keep it small.
_pool: AsyncConnectionPool | None = None

_pool_log = logging.getLogger("energylake.pool")


# ── STALE CONNECTION ON WAKE — validate-on-checkout ─────────────────────────
# THE FINDING (ruled 2026-07-31). Neon idle-closes server-side sockets, and a
# Railway service that has been quiet overnight wakes up holding a pool full of
# connections whose TCP/SSL layer is already dead. Nothing tells the pool: the
# first statement a route runs is what discovers it, raising
# "SSL connection has been closed unexpectedly" (psycopg.OperationalError) or an
# InterfaceError. The user sees a 503 on the FIRST request after a quiet period
# and a clean 200 on the retry — the classic stale-connection-on-wake shape.
#
# THE FIX, and where it lives. `check` is psycopg_pool's validate-on-checkout
# hook: it runs on EVERY `_pool.connection()` acquisition, before the caller
# gets the connection. If it raises, the pool discards that connection and hands
# over the next one instead (`_getconn_with_check_loop`), so a dead connection
# is never handed to a route at all. That is the ONE genuinely shared
# acquisition path in this file — every route, without exception, acquires via
# `_pool.connection()` — which is why the fix goes here and NOT into any route.
# No route, no lane helper, and no SQL changed for this.
#
# THE COST, stated. One extra round-trip per request (~1-3 ms to Neon us-east-2
# from Railway). That is the price of never serving a wake-up 503, and it is
# paid on the acquisition, not per query — a route that makes five reads inside
# one acquisition pays it once.
#
# WHAT THIS DOES NOT COVER. A pre-ping proves the connection was alive a
# millisecond ago, not that it survives the query. A connection that dies
# MID-statement still raises, and the retry-once wrapper in `_cockpit_read`
# (used by the Cockpit + Analytics lanes) remains the belt to that suspenders.
# The two are complementary and neither replaces the other.
_POOL_DEAD_CONN_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)


async def _pool_pre_ping(conn) -> None:
    """Validate-on-checkout: the cheap `SELECT 1` pre-ping.

    Installed as the shared pool's `check` callback, so it runs on every
    acquisition and every route inherits it. Returns quietly when the
    connection is live; raises when it is not, which is the signal
    psycopg_pool needs to discard it and hand the caller a fresh one.

    Deliberately a bare `SELECT 1` rather than psycopg_pool's own
    `AsyncConnectionPool.check_connection`, which toggles autocommit on and
    off around an empty statement — three round-trips where one will do, on a
    path that runs before every single request.
    """
    try:
        await conn.execute("SELECT 1")
    except _POOL_DEAD_CONN_ERRORS as e:
        # Expected on wake: log at INFO, not WARNING. The pool's discard-and-
        # re-acquire is the recovery, and it is working when this line appears.
        _pool_log.info("pre-ping failed, discarding stale connection: %s", e)
        raise


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
        check=_pool_pre_ping,  # validate-on-checkout; see the note above
    )
    await _pool.open()
    # Degree Day Ledger: warm the cumulative board off the request path. The 164
    # views cost ~52 s board-wide (measured; see the /api/weather/dd/* note), so
    # boot pays it instead of the first browser. Detached and fail-soft — startup
    # never waits on it and never fails because of it. Env-gated so a container
    # that must come up instantly can opt out.
    _warm_task = None
    if DD_WARM_ON_STARTUP:
        _warm_task = asyncio.create_task(_dd_warm_cumulative())
    yield
    if _warm_task is not None and not _warm_task.done():
        _warm_task.cancel()
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

# ═══════════════════════════════════════════════════════════════════════════
# THE NERC HOLIDAY CALENDAR — rule-based, corrected 2026-07-31.
#
# WHAT THIS REPLACED, AND WHY. This was a hardcoded 2016-2026 list described as
# "the locked list is the source of truth". It was not: it applied FEDERAL
# observance, which rolls a Saturday holiday BACK to the preceding Friday. NERC
# does not do that. Under the NERC/WECC block definition Saturday is ALREADY an
# on-peak day (6x16 is Mon-Sat), so a Saturday July 4th genuinely stays an
# on-peak day and genuinely is not a holiday — rolling it to Friday wrongly
# blanks a Friday's on-peak block and wrongly lights up a Saturday's.
#
# THE RULE, which is now computed rather than transcribed:
#
#     New Year's Day      January 1           (fixed)
#     Memorial Day        last Monday in May  (floating)
#     Independence Day    July 4              (fixed)
#     Labor Day           1st Monday in Sep   (floating)
#     Thanksgiving        4th Thursday in Nov (floating)
#     Christmas Day       December 25         (fixed)
#
#     A fixed-date holiday falling on a SUNDAY is observed the following MONDAY.
#     A fixed-date holiday falling on a SATURDAY is NOT moved.
#
# The three floating holidays are pinned to a weekday by construction and can
# never need an observance shift; the shift only ever runs on the three fixed
# ones.
#
# THE FOUR DATES THIS CHANGED, all Saturday-holiday cases, verified against the
# live `market_calendar` table (66 rows, region='CAISO', 2020-2030) on
# 2026-07-31:
#
#     was 2020-07-03  ->  2020-07-04   (Jul 4 2020 was a Saturday)
#     was 2021-12-24  ->  2021-12-25   (Dec 25 2021 was a Saturday)
#     was 2026-07-03  ->  2026-07-04   (Jul 4 2026 is a Saturday)
#     2022-01-01 was MISSING ENTIRELY — the old list carried "2022-12-26" twice
#                 and lost New Year's 2022 to the typo. Jan 1 2022 was a
#                 Saturday, so under the true rule it is observed on the day.
#
# Everything else the old list held was already right, including every
# Sunday->Monday shift it did carry (2017-01-02, 2021-07-05, 2022-12-26,
# 2023-01-02, 2016-12-26). Consumers therefore see IDENTICAL output except on
# the four dates above, where the old output was simply wrong.
#
# THE REFERENCE IMPLEMENTATION is energylake-pantry `nerc_calendar.py`, which
# reproduces all 66 rows of `market_calendar` exactly and whose module docstring
# names this file as the outlier needing exactly this correction. The two now
# agree by construction; `tests/test_nerc_calendar.py` pins that agreement
# against dates cut from the live table.
#
# NOT the 7x16 block. The Analytics room's `7x16` is HE7-22 on all seven days,
# holiday-blind, and that room calls it on-peak in its own vocabulary. This is
# the NERC 6x16 calendar and the two must never be conflated.
_NERC_HOLIDAY_NAMES = (
    "new_years", "memorial", "independence", "labor", "thanksgiving", "christmas",
)

# The span the materialized list below covers. Wider than the old list's
# 2016-2026 because a rule does not expire and the list is only a cache of it;
# 2016 is the floor of caiso_lmp_da_hourly, and the ceiling is far enough out
# that nothing re-litigates this before the decade turns.
_NERC_CALENDAR_MIN_YEAR = 2016
_NERC_CALENDAR_MAX_YEAR = 2035


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _date:
    """The n-th `weekday` of (year, month). `weekday` is Mon=0..Sun=6."""
    first = _date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> _date:
    """The LAST `weekday` of (year, month). `weekday` is Mon=0..Sun=6."""
    last = (_date(year, 12, 31) if month == 12
            else _date(year, month + 1, 1) - _timedelta(days=1))
    return last - _timedelta(days=(last.weekday() - weekday) % 7)


def _nerc_observed(d: _date) -> _date:
    """Apply NERC observance to a FIXED-date holiday.

    Sunday -> the following Monday. Saturday is NOT moved — that is the federal
    rule, not this one. Every other weekday is already its own observed date.
    """
    return d + _timedelta(days=1) if d.weekday() == 6 else d


def nerc_holidays(year: int) -> dict:
    """The six OBSERVED NERC holidays for `year`, as {date: name}.

    Names are stable identifiers, not display strings — they are what the
    receipts print and what the tests pin.
    """
    return {
        _nerc_observed(_date(year, 1, 1)):   "new_years",
        _last_weekday(year, 5, 0):           "memorial",      # last Mon in May
        _nerc_observed(_date(year, 7, 4)):   "independence",
        _nth_weekday(year, 9, 0, 1):         "labor",         # 1st Mon in Sep
        _nth_weekday(year, 11, 3, 4):        "thanksgiving",  # 4th Thu in Nov
        _nerc_observed(_date(year, 12, 25)): "christmas",
    }


def is_nerc_holiday(d: _date) -> bool:
    """True if `d` is an OBSERVED NERC holiday.

    Checks the PRIOR year too: New Year's observed can land on January 2nd, so
    2023-01-02 belongs to nerc_holidays(2023) — but a shifted Christmas from the
    preceding year must not be missed either. Both neighbouring years are cheap
    to build and their union is unambiguous.
    """
    return d in nerc_holidays(d.year) or d in nerc_holidays(d.year - 1)


# The rule, materialized once as ISO strings. `_NERC_HOLIDAYS` keeps its old
# name, type (list[str]) and sort order, because it is passed straight into SQL
# as `%(holidays)s::date[]` by /api/almanac/lmp-shape and read as a set by the
# hub-LMP and market-clock lanes. Only the CONTENTS changed, and only on the
# four dates named above.
_NERC_HOLIDAYS = sorted(
    d.isoformat()
    for y in range(_NERC_CALENDAR_MIN_YEAR, _NERC_CALENDAR_MAX_YEAR + 1)
    for d in nerc_holidays(y)
)


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
# /api/weather/outlooks — CPC climate-outlook tab (2026-07-29)
#
# Lights the merged Outlooks dashboard tab. Doctrine: PUBLISH DATA, NEVER
# REPLICATE CONFIG. The graphic registry the frontend deleted (the old CPC_MAPS
# const) now lives here, ONCE, server-side, and is *published* in the response —
# the frontend renders whatever arrives and must never regrow a hardcoded map
# list.
#
# Sources (Neon, read-only):
#   forecasts_climate_outlook — six outlook_type families; per type serve the
#     LATEST row by generated_ts (supplies each tile's issued_date).
#   forecasts_state_outlook   — per outlook_period, the latest generated_ts
#     batch; supplies each family's valid_start/valid_end window.
#   (cpc_outlook_vintage is the shapefile ANALYTICAL lane — NOT touched here.)
#
# THE CONTRACT IS THE CONSUMER'S. This response is shaped to exactly what
# dashboard `OutlooksShelves` (src/components/weather/OutlooksShelves.tsx, via
# useOutlooks + the isOutlooks guard in outlooks.ts) reads, and nothing else:
#
#   {as_of, shelves: [{id, depth, kelvin, products: [OutlookProduct]}]}
#
# with shelf ids "cpc" | "hazards" | "drought" in reading order. The frontend
# owns the shelf titles, source notes and empty copy; the feed carries identity
# and products only ("a shelf is FEED-DRIVEN, never a registry const").
#
# Honest absence, in the grammar this consumer actually implements: an absent
# shelf is `products: []`, NOT `data: null`. The isOutlooks guard requires
# Array.isArray(products) on EVERY shelf — a null there fails the guard, and a
# failed guard blanks all three shelves behind "The outlook feed is
# unavailable." So hazards and drought ship as empty product lists (the frontend
# then renders its own "No hazard outlooks issued.") and carry an additive
# `reason` naming why the lane is empty. `reason` is documentation for direct
# API callers — the frontend ignores it — but it is the honest record that these
# shelves are empty because the source lane does not exist, not because CPC
# issued nothing today.
#
# A CPC tile is emitted ONLY for a graphic in _VERIFIED_GRAPHIC_IDS. This
# contract has no url-null slot: an unverified url would render as a live <img>
# and paint the shelf's amber GRAPHIC UNAVAILABLE tile. Omitting the product is
# the honest-absence equivalent here — the shelf shows what is verified, and an
# entirely unverified shelf falls through to the frontend's empty state.
#
# Fail-loud: a DB failure is a 503 with a plain body, never a fabricated 200
# with empty shelves. A 200 here asserts the warehouse was actually read.
#
# Read-only; no writes; no pantry changes. Cache-Control: public, max-age=300
# (CPC issues ~daily; a 5-min edge cache is generous). DB down → 503.
# ═══════════════════════════════════════════════════════════════════════════

# The published graphic registry — the reincarnation of the frontend's deleted
# CPC_MAPS, in its one legal home. Keyed by product_id (the same key the response
# nests graphics under), each value a list of graphic entries. ENSO is
# discussion-only (no map). `url` is the CPC product-image hotlink; `link_url`
# is the human CPC product page. The shape is COURIER-COMPATIBLE: when the
# courier follow-on banks these PNGs to R2, only `url` changes — nothing else
# moves.
#
# STOP-gate (spec): every `url` must be verified at build time to resolve to a
# current image (HTTP 200 + image/* content-type) before it is served live —
# "do not trust URL patterns from memory or from this spec." A graphic_id is
# served with its live `url` ONLY if it is in `_VERIFIED_GRAPHIC_IDS` below;
# otherwise it ships as honest absence ({url: null, reason:
# "source_url_unverified"}) with `link_url` still populated. Run
# scripts/verify_outlook_graphics.py from a network with egress to
# www.cpc.ncep.noaa.gov to produce the verification log and populate that set.
OUTLOOK_GRAPHICS: dict[str, list[dict]] = {
    "cpc_6_10_day": [
        {"graphic_id": "610temp", "title": "Temperature Probability", "kind": "temp",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/610day/610temp.new.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/610day/"},
        {"graphic_id": "610prcp", "title": "Precipitation Probability", "kind": "pcpn",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/610day/610prcp.new.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/610day/"},
    ],
    "cpc_8_14_day": [
        {"graphic_id": "814temp", "title": "Temperature Probability", "kind": "temp",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/814day/814temp.new.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/814day/"},
        {"graphic_id": "814prcp", "title": "Precipitation Probability", "kind": "pcpn",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/814day/814prcp.new.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/814day/"},
    ],
    "cpc_week_3_4": [
        {"graphic_id": "wk34temp", "title": "Temperature Probability", "kind": "temp",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/WK34/gifs/WK34temp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/WK34/"},
        {"graphic_id": "wk34prcp", "title": "Precipitation Probability", "kind": "pcpn",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/WK34/gifs/WK34prcp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/WK34/"},
    ],
    "cpc_30_day": [
        {"graphic_id": "monthtemp", "title": "Temperature Probability", "kind": "temp",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/30day/off14_temp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/30day/"},
        {"graphic_id": "monthprcp", "title": "Precipitation Probability", "kind": "pcpn",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/30day/off14_prcp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/30day/"},
    ],
    "cpc_90_day": [
        {"graphic_id": "seastemp", "title": "Temperature Probability", "kind": "temp",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/long_range/lead01/off01_temp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/long_range/"},
        {"graphic_id": "seasprcp", "title": "Precipitation Probability", "kind": "pcpn",
         "url": "https://www.cpc.ncep.noaa.gov/products/predictions/long_range/lead01/off01_prcp.gif",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/predictions/long_range/"},
    ],
    "enso": [],  # ENSO is discussion-only — no map.
}

# The non-CPC shelves' graphics. Same STOP-gate, same honest-absence spelling as
# OUTLOOK_GRAPHICS — the difference is that NO warehouse lane backs these, so
# there is no outlook_type to join on and each entry carries its own label /
# measure. Per the v0 metadata rule the tiles ship issued/valid null (the frame
# renders that as "——", the spelling the week-3-4 tiles already set on screen);
# issuance is NOT scraped from page text and NOT parsed out of filenames.
#
# Every url here was taken from the `img src` its source's live product page
# declares — see the verification log in this commit. A source whose page does
# not declare a static src (a JS map viewer) has NO entry: the product is
# omitted, which is this contract's honest absence, rather than pattern-guessed.
SHELF_GRAPHICS: dict[str, list[dict]] = {
    # EMPTY — see _OUTLOOK_SHELF_ABSENCE. SPC (convective + fire weather) and WPC
    # (excessive rainfall) render their outlooks through JS map viewers that
    # declare no product `img src`, so none of the eight hazard products could be
    # sourced by the ruled method. They are omitted, not guessed.
    "hazards": [],
    "drought": [
        {"graphic_id": "cpc_mdo", "label": "MONTHLY", "measure": "DROUGHT OUTLOOK",
         "title": "CPC monthly drought outlook",
         "url": "https://www.cpc.ncep.noaa.gov/products/expert_assessment/mdohomeweb.png",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/expert_assessment/mdo_summary.php"},
        {"graphic_id": "cpc_sdo", "label": "SEASONAL", "measure": "DROUGHT OUTLOOK",
         "title": "CPC seasonal drought outlook",
         "url": "https://www.cpc.ncep.noaa.gov/products/expert_assessment/sdohomeweb.png",
         "attribution": "NOAA CPC",
         "link_url": "https://www.cpc.ncep.noaa.gov/products/expert_assessment/sdo_summary.php"},
    ],
}

# Which graphic_ids passed the build-time STOP-gate (200 + image/*) and may be
# served with a live `url`. Populated from a real run of
# scripts/verify_outlook_graphics.py on 2026-07-29 against the sources' own
# product pages (verification log in the commit messages). Every url in both
# registries was re-sourced by reading the `img src` the page actually declares
# — never a guessed pattern — then verified 200 + image/*. Re-run the script
# whenever a source moves a path; anything that fails is dropped from this set
# and its tile is omitted rather than served with a dead url.
_VERIFIED_GRAPHIC_IDS: set[str] = {
    # outlooks · cpc shelf
    "610temp", "610prcp", "814temp", "814prcp", "wk34temp", "wk34prcp",
    "monthtemp", "monthprcp", "seastemp", "seasprcp",
    # outlooks · drought shelf
    "cpc_mdo", "cpc_sdo",
    # drivers · mjo shelf
    "mjo_rmm_obs", "mjo_rmm_gefs", "mjo_rmm_verif7", "mjo_olr_hov",
    "mjo_olr_spatial", "mjo_u850_hov", "mjo_u200_hov", "mjo_vpot200_hov",
    # drivers · teleconnections shelf
    "tele_ao_obs", "tele_ao_gefs", "tele_nao_obs", "tele_nao_gefs",
    "tele_pna_obs", "tele_pna_gefs", "tele_aao_obs", "tele_aao_gefs",
    # drivers · enso shelf
    "enso_ssta_week", "enso_nino_ts", "enso_ssta_tlon", "enso_subsurface",
    "enso_olr_tlon", "enso_uv850", "enso_uv200",
}

# The shelves the consumer keys off, in reading order. The frontend supplies
# each shelf's title, source note and empty copy; `id` is the only thing it
# joins on. `reason` is set on the shelves with no source lane behind them.
_OUTLOOK_SHELF_IDS: list[str] = ["cpc", "hazards", "drought"]

# Why a shelf is empty — and ONLY while it is empty. A reason here states that no
# source lane feeds the shelf at all, which is a different claim from "the source
# issued nothing today"; the moment a shelf carries products its reason is
# dropped (see _assemble_outlooks), so the two can never be asserted together.
#
# drought is NOT listed: it carries the two CPC drought outlooks. The U.S.
# Drought Monitor CONUS map is still missing from it — the USDM page declares
# only a DATED snapshot path (…/data/png/YYYYMMDD/YYYYMMDD_usdm.png) and no
# stable "current" url, so serving it would freeze on one week's map and, with
# issued shipping null per the v0 metadata rule, the tile could not even say
# which week. Omitted until a stable url or an issuance lane exists.
_OUTLOOK_SHELF_ABSENCE: dict[str, str] = {
    "hazards": "source_lane_not_built:spc_wpc_hazard_outlooks",
}

# The `label` half of the frontend's `${label} · ${measure}` tile title, per CPC
# family. En dash matches the dashboard's own day-range grammar (cpcTileTitle).
# product_id == forecasts_climate_outlook.outlook_type. ENSO is discussion-only
# — it has no graphic, so it contributes no tile to the shelf.
_CPC_TILE_LABELS: dict[str, str] = {
    "cpc_6_10_day": "6–10 DAY",
    "cpc_8_14_day": "8–14 DAY",
    "cpc_week_3_4": "WEEK 3–4",
    "cpc_30_day": "MONTHLY",
    "cpc_90_day": "SEASONAL",
}

# The `measure` half, off the registry's own `kind`.
_CPC_TILE_MEASURES: dict[str, str] = {
    "temp": "TEMPERATURE",
    "pcpn": "PRECIPITATION",
}

# Long-form family names, used only to build each tile's `alt` text.
_OUTLOOK_PRODUCT_TITLES: dict[str, str] = {
    "cpc_6_10_day": "6–10 day outlook",
    "cpc_8_14_day": "8–14 day outlook",
    "cpc_week_3_4": "week 3–4 outlook",
    "cpc_30_day": "monthly (30-day) outlook",
    "cpc_90_day": "seasonal (90-day) outlook",
}

_OUTLOOK_CLIMATE_SQL = """
    SELECT DISTINCT ON (outlook_type)
           outlook_type, generated_ts, discussion_text, source_url, char_count
    FROM forecasts_climate_outlook
    ORDER BY outlook_type, generated_ts DESC
"""

# Latest generated_ts batch per outlook_period, all states in that batch.
_OUTLOOK_STATE_SQL = """
    SELECT s.outlook_period, s.generated_ts, s.valid_start, s.valid_end,
           s.state_code, s.temp_anomaly, s.pcpn_anomaly
    FROM forecasts_state_outlook s
    JOIN (
        SELECT outlook_period, MAX(generated_ts) AS mx
        FROM forecasts_state_outlook
        GROUP BY outlook_period
    ) latest
      ON latest.outlook_period = s.outlook_period
     AND latest.mx = s.generated_ts
    ORDER BY s.outlook_period, s.state_code
"""


# Kelvin's per-horizon read — the kelvin_by_horizon slot the dashboard's
# OutlooksShelves opened and the feed has been shipping dark.
#
# ONE ROW PER HORIZON, NEWEST FIRST. `DISTINCT ON (horizon)` with the matching
# ORDER BY is the index-aligned form (idx_kelvin_outlook_reads_serving is
# (horizon, read_date DESC)), so this is an index scan per horizon rather than a
# sort over the table.
#
# THE STALENESS BOUND IS DELIBERATE AND IT IS THE WHOLE SAFETY PROPERTY HERE.
# A caption is written about ONE issuance of a daily product. If the caption lane
# stops running, an unbounded query keeps serving last week's sentence under
# today's map — the reader sees a live-looking read of an outlook it no longer
# describes, and nothing anywhere goes red, because the row is present and
# well-formed. So a caption older than the bound is simply not served: the
# section renders its honest dark slot, which is the same thing it renders today
# and is a state the page already handles. Better a dark slot than a confident
# wrong one.
#
# 2 days, not 1: CPC issues daily and the desk writes daily, but a single skipped
# Actions run (cron is best-effort) must not blank the section — that would make
# a routine platform hiccup indistinguishable from a broken lane. Two days
# tolerates one miss and refuses two.
_outlooks_log = logging.getLogger("energylake.outlooks")

_OUTLOOK_KELVIN_STALE_DAYS = 2

_OUTLOOK_KELVIN_SQL = """
    SELECT DISTINCT ON (horizon)
           horizon, caption, read_date, issued_date
    FROM kelvin_outlook_reads
    WHERE read_date >= CURRENT_DATE - %s::int
    ORDER BY horizon, read_date DESC
"""


def _kelvin_by_horizon(kelvin_rows: list[dict]) -> dict[str, str] | None:
    """{HorizonId: caption} for the CPC shelf, or None when nothing is served.

    None — not {} — when there is no caption at all. The dashboard reads
    `cpc?.kelvin_by_horizon?.[horizon.id] ?? null` and treats both identically,
    but the field is documented OPTIONAL and the feed shipping today omits it, so
    omitting it when empty keeps this endpoint byte-identical to its current
    output until the caption lane actually writes. A behaviour change should
    arrive with content, not before it.

    NO KEY IS INVENTED. Only horizons with a served caption appear; every other
    section takes its `?? null` and renders the honest dark slot it already
    renders today. Writing a placeholder string for a dark horizon would put
    words under a section heading describing a product the desk does not have.
    """
    out = {r["horizon"]: r["caption"] for r in kelvin_rows
           if (r.get("caption") or "").strip()}
    return out or None


def _kelvin_meta_by_horizon(kelvin_rows: list[dict]) -> dict[str, dict] | None:
    """{HorizonId: {read_date, issued_date}} for exactly the horizons
    _kelvin_by_horizon serves — the caption's provenance stamp.

    THE KEY SETS ARE IDENTICAL BY CONSTRUCTION: same rows, same blank-caption
    filter. A meta entry without a caption would stamp a read that is not being
    served; a caption without a stamp would leave the dashboard's trail-warning
    lane unable to ask the one question it exists to ask — "was this sentence
    written about the issuance rendered under it?".

    Dates serve as ISO date strings. `issued_date` may be null when the caption
    row did not carry one (rows written before the stamp landed) — null is
    served as null, and the trail check treats an unstamped caption as
    UNVERIFIABLE, never as fresh. Absence stated, not filled.

    None — not {} — when nothing is served: the same optional-field discipline
    as kelvin_by_horizon, so the two fields appear together or not at all and
    an uncaptioned feed stays byte-identical to today's output.
    """
    out = {
        r["horizon"]: {
            "read_date": r["read_date"].isoformat()
                         if r.get("read_date") is not None else None,
            "issued_date": r["issued_date"].isoformat()
                           if r.get("issued_date") is not None else None,
        }
        for r in kelvin_rows
        if (r.get("caption") or "").strip()
    }
    return out or None


def _outlook_valid_windows(state_rows: list[dict]) -> dict[str, tuple]:
    """{outlook_period: (valid_start, valid_end)} off the latest state batch.
    The state lane is the only place the warehouse states a family's validity
    window, and every state in a batch shares it — so the first row per period
    wins. Periods the lane does not carry simply have no window."""
    windows: dict[str, tuple] = {}
    for r in state_rows:
        p = r["outlook_period"]
        if p in windows:
            continue
        vs, ve = r.get("valid_start"), r.get("valid_end")
        windows[p] = (
            vs.isoformat() if vs is not None else None,
            ve.isoformat() if ve is not None else None,
        )
    return windows


def _artifact_format(url: str) -> str | None:
    """The banked artifact's format, off the url's own extension ("gif"). The
    frontend surfaces it as the caption's hover title."""
    head, _, ext = url.rpartition(".")
    return ext.lower() if head and ext and "/" not in ext else None


def _cpc_product(product_id: str, g: dict, row: dict | None,
                 window: tuple) -> dict:
    """One OutlookProduct tile. `product` is the join key AND the stable id the
    build-time STOP-gate verifies; `image_url` is the gate-verified url. Dates
    are honest nulls when the warehouse has no row / no state batch for the
    family — the frontend captions those as "—" rather than inventing a
    window."""
    label = _CPC_TILE_LABELS[product_id]
    measure = _CPC_TILE_MEASURES.get(g["kind"], g["kind"].upper())
    family = _OUTLOOK_PRODUCT_TITLES.get(product_id, product_id)
    vs, ve = window
    return {
        "product": g["graphic_id"],
        "label": label,
        "measure": measure,
        "image_url": g["url"],
        "alt": f"CPC {family} — {g['title'].lower()}",
        "issued_date": row["generated_ts"].date().isoformat() if row else None,
        "valid_start": vs,
        "valid_end": ve,
        "artifact_format": _artifact_format(g["url"]),
    }


def _cpc_shelf_products(climate_rows: list[dict], state_rows: list[dict]) -> list[dict]:
    """The CPC shelf's tiles, in registry reading order (6-10 → 8-14 → wk3-4 →
    monthly → seasonal, temp before pcpn). ONLY gate-verified graphics are
    emitted: this contract has no url-null slot, so an unverified url would
    render as a broken tile rather than an honest gap."""
    latest_by_type = {r["outlook_type"]: r for r in climate_rows}
    windows = _outlook_valid_windows(state_rows)
    products: list[dict] = []
    for product_id, graphics in OUTLOOK_GRAPHICS.items():
        if product_id not in _CPC_TILE_LABELS:
            continue  # enso — discussion-only, contributes no tile
        for g in graphics:
            if g["graphic_id"] not in _VERIFIED_GRAPHIC_IDS:
                continue
            products.append(_cpc_product(
                product_id, g, latest_by_type.get(product_id),
                windows.get(product_id, (None, None)),
            ))
    return products


def _shelf_graphic_products(registry: dict[str, list[dict]], shelf_id: str) -> list[dict]:
    """A registry-driven shelf's tiles, in registry order. Same OutlookProduct
    shape and same gate rule as the CPC shelf; the dates ship null because no
    warehouse lane states these issuances yet (v0 metadata rule — the frame
    renders that as "——" rather than the desk inventing a window). Shared by the
    outlooks shelves and every drivers shelf."""
    products: list[dict] = []
    for g in registry.get(shelf_id, []):
        if g["graphic_id"] not in _VERIFIED_GRAPHIC_IDS:
            continue
        products.append({
            "product": g["graphic_id"],
            "label": g["label"],
            "measure": g["measure"],
            "image_url": g["url"],
            "alt": g["title"].lower(),
            "issued_date": None,
            "valid_start": None,
            "valid_end": None,
            "artifact_format": _artifact_format(g["url"]),
        })
    return products


def _assemble_outlooks(
    as_of: _datetime, climate_rows: list[dict], state_rows: list[dict],
    kelvin_rows: list[dict] | None = None,
) -> dict:
    """Pure assembly of the outlooks payload (no DB, no clock — `as_of`
    injected). All three shelves are ALWAYS emitted, in reading order, each with
    a `products` LIST (never null — the consumer's shape guard requires an array
    on every shelf).

    `depth` ships null: there is no archive lane to state an envelope depth from.

    `kelvin` (the SHELF-level read) also stays null, and that is not an
    oversight. The CPC shelf renders as five HORIZON sections, and one shelf
    sentence cannot narrate five leads — printing it under 6-10 DAY and under
    SEASONAL alike would attribute a read to a horizon it was never written
    about. The per-horizon reads go in `kelvin_by_horizon`; `kelvin` remains the
    read for a shelf rendered WHOLE (hazards, drought), which have no read lane
    yet either.
    """
    kelvin_rows = kelvin_rows or []
    shelves = []
    for shelf_id in _OUTLOOK_SHELF_IDS:
        products = (
            _cpc_shelf_products(climate_rows, state_rows)
            if shelf_id == "cpc"
            else _shelf_graphic_products(SHELF_GRAPHICS, shelf_id)
        )
        shelf = {
            "id": shelf_id,
            "depth": None,
            "kelvin": None,
            "products": products,
        }
        if shelf_id == "cpc":
            by_horizon = _kelvin_by_horizon(kelvin_rows)
            if by_horizon:
                shelf["kelvin_by_horizon"] = by_horizon
                # The stamp rides only where a caption rides — one condition,
                # so the two fields cannot drift apart-present.
                meta = _kelvin_meta_by_horizon(kelvin_rows)
                if meta:
                    shelf["kelvin_meta_by_horizon"] = meta
        # A reason is an assertion that NOTHING feeds this shelf — it cannot
        # stand next to products. Whichever way the registry moves, the two stay
        # mutually exclusive without anyone having to remember to unset it.
        reason = _OUTLOOK_SHELF_ABSENCE.get(shelf_id)
        if reason and not products:
            shelf["reason"] = reason
        shelves.append(shelf)
    return {"as_of": as_of.isoformat(), "shelves": shelves}



@app.get("/api/weather/outlooks")
async def weather_outlooks(response: Response):
    """The outlooks feed: three shelves — cpc · hazards · drought — each a list
    of outlook-graphic products with their issued/valid window. The CPC shelf is
    built from the climate + state outlook lanes and carries only gate-verified
    graphics; hazards and drought ship empty with a `reason` naming the unbuilt
    source lane. Shape is exactly the dashboard's OutlooksShelves contract.
    Read-only; Cache-Control public max-age=300; DB unavailable → 503."""
    assert _pool is not None
    as_of = _utcnow()
    try:
        async with _pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_OUTLOOK_CLIMATE_SQL)
                climate_rows = await cur.fetchall()
                await cur.execute(_OUTLOOK_STATE_SQL)
                state_rows = await cur.fetchall()
                # Kelvin's per-horizon reads. TOLERATES AN ABSENT TABLE, and
                # only that: `kelvin_outlook_reads` is created by pantry
                # migration 151, which the captain applies independently of this
                # deploy, so the two can legitimately be out of step for a
                # window. A hard failure here would 503 the WHOLE outlooks tab —
                # three shelves of graphics that have nothing to do with
                # captions — over a table that is simply not created yet. The
                # savepoint is required because a failed statement poisons the
                # surrounding transaction on PostgreSQL.
                #
                # Scoped deliberately to UndefinedTable. Any OTHER database
                # error still propagates to the 503 below: a 200 must keep
                # asserting the warehouse was actually read, and swallowing
                # everything here would turn a broken query into a silently
                # captionless page.
                kelvin_rows = []
                try:
                    await cur.execute("SAVEPOINT kelvin_reads")
                    await cur.execute(_OUTLOOK_KELVIN_SQL,
                                      (_OUTLOOK_KELVIN_STALE_DAYS,))
                    kelvin_rows = await cur.fetchall()
                    await cur.execute("RELEASE SAVEPOINT kelvin_reads")
                except psycopg.errors.UndefinedTable:
                    await cur.execute("ROLLBACK TO SAVEPOINT kelvin_reads")
                    _outlooks_log.info(
                        "outlooks: kelvin_outlook_reads does not exist yet "
                        "(pantry migration 151 not applied) — serving the "
                        "shelf without kelvin_by_horizon, exactly as before.")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    payload = _assemble_outlooks(as_of, climate_rows or [], state_rows or [],
                                 kelvin_rows or [])
    response.headers["Cache-Control"] = "public, max-age=300"
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# /api/weather/drivers — Climate Drivers Observatory v0 (2026-07-29)
#
# The Weather desk's third sub-tab. SAME top-level grammar as
# /api/weather/outlooks — {as_of, shelves:[{id, depth, kelvin, products:[]}]} —
# so the dashboard's GraphicsShelf/useOutlooks machinery assembles it without a
# new component family, and the same isOutlooks-shaped guard passes.
#
# Three shelves: mjo · teleconnections · enso. Every url was read off the `img
# src` its CPC product page declares and verified 200 + image/* by
# scripts/verify_outlook_graphics.py (log in the commit message). Same STOP-gate
# as the outlooks registries: an id absent from _VERIFIED_GRAPHIC_IDS is omitted,
# never served with a dead url.
#
# NO WAREHOUSE LANE. This endpoint reads no DB — the registry is the whole
# payload — so there is no 503 path and no query. Per the v0 metadata rule every
# tile's issued/valid ships null (the frame renders "——"); issuance is neither
# scraped from page text nor parsed out of filenames. Index *data* lanes (the
# RMM revival) are a separate pantry arc; nothing here derives or interprets a
# value. No lean, no labels beyond what the source's own alt text says.
#
# Read-only. Cache-Control: public, max-age=300.
# ═══════════════════════════════════════════════════════════════════════════

_DRIVERS_SHELF_IDS: list[str] = ["mjo", "teleconnections", "enso"]

# Products that the ruled method could NOT source, pinned here so the gap is a
# stated fact rather than a silent short shelf. Neither is served:
#
#   ECMWF / ECMWF-extended RMM phase diagrams — CPC's MJO page declares no
#     ECMWF graphic at all (zero `ecmwf`/`ecmf` references in the page source).
#     Treated as expected-fail per the ruling: one extraction attempt, omitted,
#     moved on. The GEFS RMM plume and the CPC WH-index observed panel below are
#     unaffected.
#   CPC official ENSO probability bar chart — the ENSO advisory page declares no
#     product image (chrome only), and the monitoring page does not carry it.
#
# Both are re-sourceable the moment a CPC page declares them, or via the courier
# lane. Reconstructing their paths stays forbidden.
_DRIVERS_OMITTED: dict[str, str] = {
    "ecmwf_rmm": "source_page_declares_no_src:cpc_mjo_page_has_no_ecmwf_graphic",
    "enso_probability": "source_page_declares_no_src:cpc_enso_advisory_declares_no_product_image",
}

# The drivers graphic registry. Same entry shape as SHELF_GRAPHICS. `title` is
# taken from the source page's own alt text wherever the page supplies one, so
# the tile says what CPC says it is — the desk adds no interpretation.
DRIVERS_GRAPHICS: dict[str, list[dict]] = {
    # Source page: /products/precip/CWlink/MJO/mjo.shtml
    "mjo": [
        {"graphic_id": "mjo_rmm_obs", "label": "RMM INDEX", "measure": "OBSERVED",
         "title": "CPC version of the WH MJO index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/mjo/img/rmm_obs40.png"},
        {"graphic_id": "mjo_rmm_gefs", "label": "RMM INDEX", "measure": "GEFS PLUME",
         "title": "GFS MJO index ensemble plume",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/mjo/img/GEFS_BC.png"},
        {"graphic_id": "mjo_rmm_verif7", "label": "RMM INDEX", "measure": "7-DAY VERIFICATION",
         "title": "7-day verification of MJO index from GFS",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/mjo/img/rmm_verif1_GEFS_BC.png"},
        {"graphic_id": "mjo_olr_hov", "label": "OLR ANOMALY", "measure": "TIME–LONGITUDE",
         "title": "time-longitude OLR anomalies",
         "url": "https://www.cpc.ncep.noaa.gov/products/intraseasonal/olr_hov_last180days_2.gif"},
        {"graphic_id": "mjo_olr_spatial", "label": "OLR ANOMALY", "measure": "SPATIAL",
         "title": "spatial OLR anomalies",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/olra_last30days-3plots_2.gif"},
        {"graphic_id": "mjo_u850_hov", "label": "850-hPa ZONAL WIND", "measure": "TIME–LONGITUDE",
         "title": "time-longitude 850 zonal wind",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/hov_u850_2.gif"},
        {"graphic_id": "mjo_u200_hov", "label": "200-hPa ZONAL WIND", "measure": "TIME–LONGITUDE",
         "title": "time-longitude 200 zonal wind",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/MJO/hov_u200_2.gif"},
        {"graphic_id": "mjo_vpot200_hov", "label": "200-hPa VELOCITY POTENTIAL",
         "measure": "TIME–LONGITUDE",
         "title": "time-longitude 200 velocity potential",
         "url": "https://www.cpc.ncep.noaa.gov/products/intraseasonal/tlon_vpot_web_2.gif"},
    ],
    # Source pages: daily_ao_index/ao.shtml · pna/nao.shtml · pna/pna.shtml ·
    # daily_ao_index/aao/aao.shtml. Each declares exactly two panels — the
    # observed index and the GEFS forecast — so the shelf is 4 indices x 2.
    "teleconnections": [
        {"graphic_id": "tele_ao_obs", "label": "AO", "measure": "OBSERVED",
         "title": "Arctic Oscillation observed index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/ao.obs.gif"},
        {"graphic_id": "tele_ao_gefs", "label": "AO", "measure": "GEFS FORECAST",
         "title": "Arctic Oscillation GEFS forecast index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/ao.gefs.fcst.png"},
        {"graphic_id": "tele_nao_obs", "label": "NAO", "measure": "OBSERVED",
         "title": "North Atlantic Oscillation observed index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/nao.mrf.obs.gif"},
        {"graphic_id": "tele_nao_gefs", "label": "NAO", "measure": "GEFS FORECAST",
         "title": "North Atlantic Oscillation GEFS forecast index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/nao.gefs.fcst.png"},
        {"graphic_id": "tele_pna_obs", "label": "PNA", "measure": "OBSERVED",
         "title": "Pacific/North American pattern observed index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/pna.mrf.obs.gif"},
        {"graphic_id": "tele_pna_gefs", "label": "PNA", "measure": "GEFS FORECAST",
         "title": "Pacific/North American pattern GEFS forecast index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/pna.gefs.fcst.png"},
        {"graphic_id": "tele_aao_obs", "label": "AAO", "measure": "OBSERVED",
         "title": "Antarctic Oscillation observed index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/aao.obs.gif"},
        {"graphic_id": "tele_aao_gefs", "label": "AAO", "measure": "GEFS FORECAST",
         "title": "Antarctic Oscillation GEFS forecast index",
         "url": "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/aao/aao.gefs.fcst.png"},
    ],
    # Source page: /products/precip/CWlink/MJO/enso.shtml (titles are that
    # page's own alt text).
    "enso": [
        {"graphic_id": "enso_ssta_week", "label": "SST ANOMALY", "measure": "WEEKLY MAP",
         "title": "weekly sea surface temperature anomalies",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/ssta-week.gif"},
        {"graphic_id": "enso_nino_ts", "label": "NIÑO REGIONS", "measure": "SST TIME SERIES",
         "title": "time series of weekly sea surface temperature anomalies for the 4 Niño regions",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/ssta_c.gif"},
        {"graphic_id": "enso_ssta_tlon", "label": "SST ANOMALY", "measure": "TIME–LONGITUDE",
         "title": "time-longitude section of sea surface temperature anomalies (5°N–5°S)",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/ssttlon5_c.gif"},
        {"graphic_id": "enso_subsurface", "label": "EQUATORIAL SUBSURFACE",
         "measure": "TEMPERATURE ANOMALY",
         "title": "equatorial Pacific temperature-depth anomalies",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/ocean/anim/wkxzteq_anm.gif"},
        {"graphic_id": "enso_olr_tlon", "label": "OLR ANOMALY", "measure": "TIME–LONGITUDE",
         "title": "time-longitude section of anomalous OLR",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/olra-30d.gif"},
        {"graphic_id": "enso_uv850", "label": "850-hPa WIND", "measure": "ANOMALY · 30 DAY",
         "title": "850 hPa winds for the last 30 days",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/uv850-30d.gif"},
        {"graphic_id": "enso_uv200", "label": "200-hPa WIND", "measure": "ANOMALY · 30 DAY",
         "title": "200 hPa winds for the last 30 days",
         "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso_update/uv200-30d.gif"},
    ],
}


def _assemble_drivers(as_of: _datetime) -> dict:
    """Pure assembly of the drivers payload. All three shelves are ALWAYS
    emitted, in reading order, each with a `products` LIST (never null — the
    consumer's shape guard requires an array on every shelf). `depth` and
    `kelvin` ship null: no archive lane states an envelope depth, and the
    drivers stanza that fills Kelvin's per-shelf read does not exist yet."""
    return {
        "as_of": as_of.isoformat(),
        "shelves": [
            {
                "id": shelf_id,
                "depth": None,
                "kelvin": None,
                "products": _shelf_graphic_products(DRIVERS_GRAPHICS, shelf_id),
            }
            for shelf_id in _DRIVERS_SHELF_IDS
        ],
    }


@app.get("/api/weather/drivers")
async def weather_drivers(response: Response):
    """The climate-drivers tab: three shelves — mjo · teleconnections · enso —
    of gate-verified CPC graphics, in the same grammar as /api/weather/outlooks.
    Registry-only: no DB read, so no 503 path. Every tile's issued/valid ships
    null (v0 has no issuance lane). Cache-Control public, max-age=300."""
    payload = _assemble_drivers(_utcnow())
    response.headers["Cache-Control"] = "public, max-age=300"
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

# The rule-derived NERC-holiday calendar reused as a set of ISO strings for O(1)
# membership in the on/off-peak split (source of truth is the `nerc_holidays`
# rule above, materialized into _NERC_HOLIDAYS).
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

# ═══════════════════════════════════════════════════════════════════════════
# THE ANALYTICS DEPARTMENT, ROOM 2 — STRUCTURES  (/api/analytics/structures/*)
# ═══════════════════════════════════════════════════════════════════════════
#
# The payoff sandbox: swaps, monthly-average (Asian) options, HRCOs. Explore,
# replay, rank. NEVER ADVISE.
#
# THE POSTURE — three rails, load-bearing, and the reason this room is allowed
# to exist at all. They are restated in structures.py (the arithmetic) and in
# every payload this section emits, because a calculator that forgets them is
# indistinguishable from a pricing desk:
#
#   1. NO FORWARD PRICING. v0 computes payoff GIVEN user inputs and REALIZED
#      history. No vol surfaces, no premium valuation, no forecast of any
#      settle. The words "would have paid" appear wherever history is shown.
#
#   2. DEPTH-HONEST EVERYWHERE. Every replay states its window. Hub strips run
#      years deep; nodal legs run weeks. A month is banked WHOLE or it is not
#      replayed — see the measured depth report below.
#
#   3. RANKINGS ARE DESCRIPTIVE SORTS of historical arithmetic ("ranked by
#      realized payoff over N banked months"), never a recommendation register.
#
# ───────────────────────────────────────────────────────────────────────────
# THE MEASURED DEPTH REPORT (2026-07-30, against the live pantry). Every
# number here was counted, not assumed, and the endpoints recompute rather
# than trust these — they are written down so a reader knows what shipped.
#
# POWER LEGS (dataset caiso_lmp_da_hourly, DA hourly, $/MWh):
#   * Hubs SP15 / NP15 / ZP26 — 88,344 hourly rows each, 2016-01-01..2026-07-31.
#     118 BANKED COMPLETE 7x16 MONTHS each (2016-01..2026-06). The four
#     non-banked months are exactly the coverage holes this repo already
#     documents — 2016-02 (20/29d), 2016-08 (11/31d), 2019-01 (30/31d) — plus
#     the in-progress 2026-07. 2016-03..2016-07 are wholly absent (the CAISO
#     archive hole). The completeness gate REPRODUCING the documented holes
#     with no special-casing is this lane's strongest arbiter.
#   * Nodal legs — 2,376 `*-APND` series, 2026-06-07..2026-07-31, of which
#     2,342 carry identity in atlas_pnode_universe. ZERO BANKED COMPLETE
#     MONTHS: June 2026 is missing days 1-6 (history starts mid-month) and
#     July 2026 stands at 485/496 block hours. The first complete nodal month
#     will be 2026-07, the moment 2026-07-31 finishes publishing. Nodal replay
#     is therefore HONEST ABSENCE today — computed, never hardcoded, so it
#     lights up on its own.
#
# NODAL LEG NAMESPACE — A SPEC DEVIATION, STATED LOUDLY. The spec asks for the
# leg picker "via the same node-search component v0 built". /api/nodes/search
# browses atlas_pnode_lmp_snapshot: 14,827 nodes over an 8-DAY hot tier, and
# NOT ONE of its pnode_ids appears among the 2,376 replay-capable `-APND`
# series (measured: the intersection is empty; only 8 `-APND` ids exist in the
# snapshot at all, all BCHA_5_*). Pointing this room's builder at that picker
# would offer thousands of legs that can never be replayed. So the COMPONENT
# is reused but its universe is not: /catalog serves the replay-capable leg
# universe, and a `node` leg outside it is a 400 that says so. Never promise a
# leg the replay cannot serve.
#
# GAS INDICES — the lane's first act was the gas-leg reality check, and the
# STOP-gate did NOT trip: banked, usable gas series exist. What is held:
#   * eia_gas_henry_hub_daily / HENRY_HUB — 5,066 rows, 2006-06-19..2026-07-20.
#     BUSINESS-DAY cadence (~19-22 prints/month, no weekend rows). The only
#     gas index with depth matching the hub power strips. Superseded by
#     eia_gas_spot_prices (migration 105) but RETAINED as archive, and the
#     succession note says plainly there is no backfill path for the new lane.
#   * eia_gas_spot_prices — 10 regional indices INCLUDING socal_border,
#     pge_citygate, el_paso_san_juan, northwest_sumas. Daily business-day
#     cadence, $/MMBtu. Depth 2026-07-09..2026-07-28: 14 prints, NO COMPLETE
#     MONTH. Offered by name; will not replay a month until one banks.
#   * caiso_fuel_prices — 126 CAISO fuel regions (PRC_FUEL), CALENDAR-daily
#     (weekend rows present), 2017-11..2026-05-30, dispatch-canonical. Deep but
#     PATCHY (2026-03 holds 9 days, 2026-04 none, 2026-05 twenty-one) and
#     STALE since 2026-05-30 with lifecycle_status 'planned'.
#
# DATA-ROADMAP GAPS FILED (not improvised around — rail 1 forbids a proxy):
#   (a) No daily SoCal CITYGATE index is banked anywhere. socal_border is
#       held, and it is a different delivery point; the calculator names it
#       socal_border and never calls it citygate.
#   (b) The dispatch-canonical caiso_fuel_prices lane is ~2 months stale and
#       marked 'planned'. It is offered with its staleness stated in days.
#   (c) eia_gas_spot_prices has no banked complete month, so the WECC-relevant
#       physical indices cannot yet replay one. Henry Hub can — and Henry Hub
#       is NOT the right basis for a California HRCO. The calculator says so
#       in the catalog rather than quietly defaulting a user into it.
#   (d) NERC-holiday calendar divergence — RESOLVED IN THIS FILE 2026-07-31,
#       still open in the block menu. The old note here claimed main.py used
#       "OBSERVED dates (2026-07-03)" against a pantry lane using "ACTUAL
#       dates". Both halves were wrong: main.py was applying FEDERAL observance
#       (Saturday rolled back to Friday), and the pantry's calendar does shift
#       Sunday to Monday, so it was never "actual dates" either. main.py now
#       computes the true NERC rule and agrees with `market_calendar` on all 66
#       rows. 6x16 stays ABSENT from this room's block menu — not for want of a
#       calendar any more, but because nothing has yet reconciled the block
#       ARITHMETIC against the pantry's caiso_blocks_daily column. See
#       structures.py. The three blocks shipped are holiday-independent.
#
# MEASURED RUNTIMES (EXPLAIN ANALYZE, cold buffers, live pantry):
#   * one hub leg, full 118-month depth  ->  472 ms (58,896 hrs -> 3,681 days)
#   * 3 hubs, 24-month window            ->   75 ms (default screener shape)
#   * 3 hubs, full depth                 ->  907 ms
#   * 40 nodal legs, full nodal depth     -> 1,470 ms (of which ~105 ms was a
#     LIKE-based leg discovery scan — which is why legs are ALWAYS bound by an
#     explicit series list here, never discovered inside the sweep query)
# Every read is index-served by idx_tsv_series_ts (dataset, series, ts DESC).
# ═══════════════════════════════════════════════════════════════════════════

import structures as _st

STRUCTURES_PREFIX = "/api/analytics/structures"

# Power leg source. Same dataset the almanac + hub-LMP lanes read.
_STRUCT_POWER_DATASET = LMP_DATASET  # caiso_lmp_da_hourly

# The replay-capable hub legs. Deliberately the SAME set _VALID_HUBS pins, so
# a hub that replays here is a hub that charts everywhere else.
_STRUCT_HUB_LEGS = tuple(sorted(_VALID_HUBS))  # NP15, SP15, ZP26

# Nodal legs live in the same dataset under an "-APND" suffix. The suffix is
# the namespace marker (see the deviation note above), so it is appended on
# the way into SQL and stripped on the way out.
_STRUCT_NODE_SUFFIX = "-APND"

# Gas datasets, by the id the wire uses. Cadence and unit are stated as FACTS
# ABOUT THE BANKED SERIES, measured on 2026-07-30, not aspirations:
#   cadence "business_day"  -> Mon-Fri ex-holiday prints; non-trade days carry
#                              the prior trade day (the gas-day convention).
#   cadence "calendar_day"  -> a print for every calendar day.
_STRUCT_GAS_DATASETS: dict[str, dict] = {
    "eia_hh": {
        "id": "eia_hh",
        "dataset": "eia_gas_henry_hub_daily",
        "label": "EIA Henry Hub spot (daily archive)",
        "unit": "$/MMBtu",
        "cadence": "business_day",
        "basis_note": (
            "Henry Hub is the national benchmark, NOT a California delivered-gas "
            "basis. An HRCO struck off Henry Hub for a CAISO leg omits the "
            "basis to the burner tip. It is offered because it is the only gas "
            "index banked deeply enough to replay the hub power strips."
        ),
        "lifecycle": (
            "Superseded by eia_gas_spot_prices (migration 105, 2026-07-28) and "
            "retained as archive; the successor has no backfill path."
        ),
    },
    "eia_spot": {
        "id": "eia_spot",
        "dataset": "eia_gas_spot_prices",
        "label": "EIA daily regional gas spot",
        "unit": "$/MMBtu",
        "cadence": "business_day",
        "basis_note": (
            "Regional physical delivery points. socal_border and pge_citygate "
            "are the WECC-relevant legs. socal_border is the BORDER, not the "
            "SoCal citygate — no citygate index is banked (roadmap gap)."
        ),
        "lifecycle": "Active. Backfill starts 2026-07-09; no month has banked yet.",
    },
    "caiso_fuel": {
        "id": "caiso_fuel",
        "dataset": "caiso_fuel_prices",
        "label": "CAISO fuel-region reference price (PRC_FUEL)",
        "unit": "$/MMBtu",
        "cadence": "calendar_day",
        "basis_note": (
            "Dispatch-canonical: what CAISO itself uses to clear the day-ahead "
            "market. Region ids ending 'ghg' bake in cap-and-trade allowance "
            "cost. The closest banked thing to a California burner-tip basis."
        ),
        "lifecycle": (
            "lifecycle_status 'planned' in the pantry and stale since "
            "2026-05-30 — staleness is reported in days on every catalog read."
        ),
    },
}

# gas_index ids on the wire are "<dataset_key>.<series>", e.g.
# "eia_hh.HENRY_HUB", "eia_spot.socal_border", "caiso_fuel.frscec". Explicit
# and collision-free across the three lanes.
_STRUCT_GAS_ID_SEP = "."

# Lanes that stay VISIBLE in the catalog inventory as reference data but are NOT
# selectable as an HRCO strike basis. Visibility and selectability are different
# claims: the catalog reports what is banked, this set reports what a strike may
# be struck off. Keyed by lane id -> the reason, verbatim, that the 400 states.
_STRUCT_GAS_LANES_NOT_SELECTABLE: dict[str, str] = {
    "caiso_fuel": (
        "caiso_fuel_prices is lifecycle_status 'planned' (demoted by migration "
        "117) and ~60 days stale, and its fuel-region prices do not behave like "
        "a burner-tip index: frscec at ~$8/MMBtu against SP15 at ~$32/MWh is a "
        "market heat rate of 3.9 MMBtu/MWh, which no gas unit runs. It remains "
        "in the catalog inventory as reference data; it is not a strike basis."
    ),
}

# How far back to reach for a gas carry-in print so the FIRST day of a replay
# window can resolve under the gas-day convention. 10 days clears any holiday
# weekend; the fill span itself is still gated at 4 days per month in
# structures.replay (a longer run means the index has a hole, not a weekend).
_STRUCT_GAS_CARRY_IN_DAYS = 10

# Screener bounds. PROPOSED BY THIS LANE, MEASURED (see the runtime block
# above), and stated in the payload so a caller never mistakes a bound for a
# complete sweep:
#   * the three hubs are the default sweep — 75 ms at 24 months, 907 ms at
#     full depth;
#   * nodal legs are opt-in via an explicit list, capped at 40 (~1.5 s cold).
# No silent truncation: an over-cap request is a 400, and the payload always
# carries `bound` describing what was swept.
_STRUCT_SCREENER_MAX_NODE_LEGS = 40

# In-process catalog cache. The catalog is a depth report over ~137 gas series
# plus three hub strips, and it turns over once a day when the ingesters run —
# so one slot on a 15-minute TTL, mirroring the constraints/facets discipline.
_STRUCT_CATALOG_CACHE_TTL = 900.0
_struct_catalog_cache: "tuple[float, dict] | None" = None

# The posture, as the payload states it. Emitted on EVERY response in this
# section — an endpoint that can be consumed without the rails is an endpoint
# that will be.
_STRUCT_POSTURE = {
    "what_this_is": (
        "A calculator and a replay engine. It computes what a structure pays "
        "given your inputs and realized history."
    ),
    "what_this_is_not": (
        "Not a pricing desk and not advice. No option premium, no volatility, "
        "no greeks, no forward curve, no forecast of any settle."
    ),
    "rails": [
        "No forward pricing — payoff is computed GIVEN inputs and realized history.",
        "Depth-honest — every replay states its window; a month is banked whole or not replayed.",
        "Rankings are descriptive sorts of historical arithmetic, never a recommendation register.",
    ],
    "history_wording": "would have paid",
}


def _struct_gas_id(dataset_key: str, series: str) -> str:
    return f"{dataset_key}{_STRUCT_GAS_ID_SEP}{series}"


def _struct_split_gas_id(gas_id: str) -> tuple[str, str]:
    """'eia_spot.socal_border' -> ('eia_spot', 'socal_border')."""
    key, _, series = gas_id.partition(_STRUCT_GAS_ID_SEP)
    if not key or not series or key not in _STRUCT_GAS_DATASETS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"malformed gas_index '{gas_id}'; expected "
                f"'<lane>.<series>' where lane is one of "
                f"{sorted(_STRUCT_GAS_DATASETS)}."
            ),
        )
    return key, series


def _struct_leg_series(leg: dict) -> str:
    """Map a validated leg to its timeseries_values.series name."""
    if leg["kind"] == "hub":
        return leg["id"]
    return leg["id"] if leg["id"].endswith(_STRUCT_NODE_SUFFIX) else (
        leg["id"] + _STRUCT_NODE_SUFFIX
    )


def _struct_leg_display(kind: str, series: str) -> dict:
    if kind == "hub":
        return {"kind": "hub", "id": series}
    return {"kind": "node",
            "id": series[: -len(_STRUCT_NODE_SUFFIX)]
            if series.endswith(_STRUCT_NODE_SUFFIX) else series}


# ── The power read ───────────────────────────────────────────────────────────
# One index-served range scan per sweep, aggregated to (series, PT day) in SQL.
# Daily grain — NOT monthly — because the daily-exercise HRCO needs a per-day
# block average, and a monthly average is recoverable from dailies by
# hour-weighting while the reverse is not. The completeness gate then runs in
# Python, where the expected hour count per date comes from the tz database
# (23/24/25-hour days) rather than a calendar hardcoded into SQL.
_STRUCT_POWER_SQL = """
    SELECT v.series AS series,
           (v.ts AT TIME ZONE 'America/Los_Angeles')::date AS d,
           SUM(v.value::numeric) AS block_sum,
           COUNT(*) AS n_hours
    FROM timeseries_values v
    WHERE v.dataset = %(dataset)s
      AND v.series = ANY(%(series_list)s::text[])
      AND (%(start)s::timestamptz IS NULL OR v.ts >= %(start)s::timestamptz)
      AND EXTRACT(HOUR FROM v.ts AT TIME ZONE 'America/Los_Angeles')
          = ANY(%(hours)s::int[])
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

# ── The gas read ─────────────────────────────────────────────────────────────
# One series, one range scan, ordered ascending. The forward-fill to non-trade
# days happens in Python so the fill span per day is VISIBLE and gateable —
# a SQL LATERAL would hide how far each print was carried.
_STRUCT_GAS_SQL = """
    SELECT (v.ts AT TIME ZONE 'UTC')::date AS d,
           v.value::numeric AS price
    FROM timeseries_values v
    WHERE v.dataset = %(dataset)s
      AND v.series  = %(series)s
      AND (%(start)s::date IS NULL OR (v.ts AT TIME ZONE 'UTC')::date >= %(start)s::date)
    ORDER BY 1
"""

# ── The gas inventory read (catalog) ────────────────────────────────────────
# Per-series first/last/held-day counts across the three gas lanes. Small
# tables (caiso_fuel_prices ~130k rows, the EIA lanes ~5k), one grouped scan
# per lane via idx_tsv_dataset_ts.
_STRUCT_GAS_INVENTORY_SQL = """
    SELECT v.dataset AS dataset,
           v.series  AS series,
           MIN((v.ts AT TIME ZONE 'UTC')::date) AS first_day,
           MAX((v.ts AT TIME ZONE 'UTC')::date) AS last_day,
           COUNT(DISTINCT (v.ts AT TIME ZONE 'UTC')::date) AS days_held
    FROM timeseries_values v
    WHERE v.dataset = ANY(%(datasets)s::text[])
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

# ── The nodal leg universe read (catalog) ───────────────────────────────────
# The replay-capable nodal legs, with the depth each actually carries. Bounded
# by construction: one grouped scan of the dataset's nodal series, and the
# route returns counts plus a bounded sample rather than 2,376 rows on every
# catalog hit.
_STRUCT_NODE_UNIVERSE_SQL = """
    SELECT v.series AS series,
           MIN((v.ts AT TIME ZONE 'America/Los_Angeles')::date) AS first_day,
           MAX((v.ts AT TIME ZONE 'America/Los_Angeles')::date) AS last_day,
           COUNT(*) AS n_hours
    FROM timeseries_values v
    WHERE v.dataset = %(dataset)s
      AND v.series LIKE %(suffix_pattern)s
    GROUP BY 1
    ORDER BY 1
"""


async def _struct_read(sql: str, params: dict) -> list:
    """One read, retry-once on a dropped connection, 503 on hard failure.

    Same discipline as the Cockpit lane's _cockpit_read: Neon idles pooled
    connections out, and a structures page open in a tab is exactly the shape
    that finds a stale one.
    """
    assert _pool is not None
    last: Exception | None = None
    for _attempt in range(2):
        try:
            async with _pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    return await cur.fetchall()
        except Exception as e:  # noqa: BLE001 — retried once, then surfaced
            last = e
    raise HTTPException(status_code=503, detail=f"db unavailable: {last}")


def _struct_resolve_gas(rows: list, days: list) -> dict:
    """Resolve a gas price for every power day under the gas-day convention.

    Non-trade days (weekends, gas holidays) carry the LAST TRADE DAY's print —
    that is what the market does (the weekend package prices Sat/Sun/Mon), not
    interpolation and not invented data. Each resolved day reports the trade
    date it came from and how many days it was carried, so the caller can see
    exactly how a month was assembled and structures.replay can gate a carry
    longer than a holiday weekend.

    A CALENDAR-daily index should never carry at all; a span > 0 there means
    the index has a hole, and the span is reported so the month gets gated.
    """
    held = {r["d"]: float(r["price"]) for r in rows if r["price"] is not None}
    out: dict = {}
    trade_dates = sorted(held)
    i = 0
    last_d = None
    for day in days:
        while i < len(trade_dates) and trade_dates[i] <= day:
            last_d = trade_dates[i]
            i += 1
        if last_d is None:
            out[day] = {"price": None, "trade_date": None, "fill_span_days": None}
            continue
        out[day] = {
            "price": held[last_d],
            "trade_date": last_d,
            "fill_span_days": (day - last_d).days,
        }
    return out


def _struct_gas_meta(dataset_key: str, series: str, inv: dict,
                     today: _date) -> dict:
    """A gas index as the catalog states it: name, cadence, depth, staleness."""
    lane = _STRUCT_GAS_DATASETS[dataset_key]
    first_day = inv.get("first_day")
    last_day = inv.get("last_day")
    stale_days = (today - last_day).days if last_day else None
    return {
        "id": _struct_gas_id(dataset_key, series),
        "lane": dataset_key,
        "series": series,
        "dataset": lane["dataset"],
        "label": f"{lane['label']} — {series}",
        "unit": lane["unit"],
        "cadence": lane["cadence"],
        "cadence_statement": (
            "Business-day prints only; non-trade days carry the prior trade "
            "day's index (the gas-day convention)."
            if lane["cadence"] == "business_day" else
            "A print for every calendar day; any carry-forward means the index "
            "has a hole, not a weekend."
        ),
        "first_day": first_day.isoformat() if first_day else None,
        "last_day": last_day.isoformat() if last_day else None,
        "days_held": int(inv["days_held"]) if inv.get("days_held") else 0,
        "stale_days": stale_days,
        "basis_note": lane["basis_note"],
        "lifecycle": lane["lifecycle"],
        "selectable": dataset_key not in _STRUCT_GAS_LANES_NOT_SELECTABLE,
        "not_selectable_reason": _STRUCT_GAS_LANES_NOT_SELECTABLE.get(dataset_key),
    }


# ── GET /api/analytics/structures/catalog ────────────────────────────────────

@app.get(f"{STRUCTURES_PREFIX}/catalog")
async def structures_catalog(response: Response):
    """
    The banked-reality menu: what this room can actually build and replay.

    The builder panel reads this and offers NOTHING ELSE. That is the whole
    contract — a structure the catalog does not list is a structure the
    calculator will refuse, and a leg or gas index the catalog does not list is
    one the replay cannot serve. Nothing about the menu is hardcoded on the
    client, and the depth figures are MEASURED on every (cached) read rather
    than written down, so the room tells the truth as the pantry changes.

    Envelope:
        {
          "as_of": "...",
          "posture": {...the three rails...},
          "structures": [{id, label, requires, settlement, needs_gas, y_basis}],
          "blocks":     [{id, label, definition, nominal_hours_per_day}],
          "legs": {
            "hubs":  [{id, kind, banked_months, first_month, last_month, ...}],
            "nodal": {universe_size, banked_months, replayable_legs, ...}
          },
          "gas_indices": [{id, lane, series, cadence, first_day, last_day,
                           days_held, stale_days, basis_note, lifecycle}],
          "gas_leg_reality_check": {...the STOP-gate verdict + filed gaps...},
          "bounds": {...screener bounds, measured...},
          "sources": {...}
        }

    Cached 15 minutes in-process. DB unavailable -> 503.
    """
    global _struct_catalog_cache
    now = time.time()
    if _struct_catalog_cache is not None:
        stamped, cached = _struct_catalog_cache
        if now - stamped < _STRUCT_CATALOG_CACHE_TTL:
            response.headers["X-Cache"] = "hit"
            return cached

    today = _utcnow().date()

    # ── Hub depth: measure the banked complete months per hub, per block.
    # 7x16 is the headline (the captain's settlement grammar), but every block
    # is reported because their DST hour counts differ — a caller choosing 7x24
    # deserves 7x24's depth, not 7x16's.
    #
    # One index-served scan per block rather than one ATC scan re-bucketed:
    # the read aggregates to (series, PT day) in SQL, so the individual hours
    # are already gone by the time the rows arrive and a subset block cannot be
    # recovered from them. Three narrow scans beat shipping 265k hourly rows to
    # Python to re-bucket. This is the only route here that reads full depth
    # unwindowed, which is why it is the one that gets cached.
    hub_depth: dict[str, dict] = {}
    for block_id in _st.BLOCKS:
        rows = await _struct_read(_STRUCT_POWER_SQL, {
            "dataset": _STRUCT_POWER_DATASET,
            "series_list": list(_STRUCT_HUB_LEGS),
            "start": None,
            "hours": _st.block_hour_list(block_id),
        })
        # Seed EVERY hub leg, so a block that returned no rows reports
        # banked_months 0 rather than vanishing from depth_by_block. A missing
        # key would leave the client unable to tell "no depth here" from "this
        # block is not offered" — which is the honest-absence rule applied to
        # the menu itself.
        per_leg: dict[str, list] = {leg: [] for leg in _STRUCT_HUB_LEGS}
        for r in rows:
            per_leg.setdefault(r["series"], []).append(
                {"day": r["d"], "block_sum": r["block_sum"], "n_hours": r["n_hours"]}
            )
        for leg_series, daily in per_leg.items():
            probe = _st.normalize_definition({
                "structure": "swap", "fixed_price": 0.0, "block": block_id,
                "leg": {"kind": "hub", "id": leg_series}, "size_mw": 1.0,
            })
            rep = _st.replay(probe, daily)
            hub_depth.setdefault(leg_series, {})[block_id] = {
                "banked_months": rep["months"],
                "first_month": rep["window"]["first_month"],
                "last_month": rep["window"]["last_month"],
                "months_rejected": len(rep["months_rejected"]),
                "depth_statement": rep["depth_statement"],
            }

    hubs = [
        {
            "kind": "hub",
            "id": leg,
            "label": f"TH_{leg}_GEN — CAISO {leg} trading hub",
            "source": {"dataset": _STRUCT_POWER_DATASET, "market": "DA", "cadence": "hourly"},
            "depth_by_block": hub_depth.get(leg, {}),
        }
        for leg in _STRUCT_HUB_LEGS
    ]

    # ── Nodal universe: how many legs exist and how many can replay a month.
    node_rows = await _struct_read(_STRUCT_NODE_UNIVERSE_SQL, {
        "dataset": _STRUCT_POWER_DATASET,
        "suffix_pattern": f"%{_STRUCT_NODE_SUFFIX}",
    })
    node_first = min((r["first_day"] for r in node_rows), default=None)
    node_last = max((r["last_day"] for r in node_rows), default=None)

    # A nodal leg can replay iff at least one calendar month between its first
    # and last banked day is whole. Cheap sufficient test without re-reading
    # every leg's hours: a leg whose span does not CONTAIN a whole calendar
    # month cannot possibly bank one. Legs that clear that test are then
    # gated for real by /evaluate, which reads their hours.
    def _spans_whole_month(a: _date | None, b: _date | None) -> bool:
        if a is None or b is None:
            return False
        probe = _date(a.year, a.month, 1)
        if a.day != 1:
            probe = (_date(a.year + 1, 1, 1) if a.month == 12
                     else _date(a.year, a.month + 1, 1))
        dim = _st.days_in_month(probe.year, probe.month)
        return b >= _date(probe.year, probe.month, dim)

    replayable = [r["series"] for r in node_rows
                  if _spans_whole_month(r["first_day"], r["last_day"])]

    nodal = {
        "universe_size": len(node_rows),
        "history_begins": node_first.isoformat() if node_first else None,
        "history_ends": node_last.isoformat() if node_last else None,
        "legs_spanning_a_whole_month": len(replayable),
        "sample_legs": [_struct_leg_display("node", r["series"])["id"]
                        for r in node_rows[:25]],
        "source": {"dataset": _STRUCT_POWER_DATASET, "market": "DA",
                   "cadence": "hourly", "series_suffix": _STRUCT_NODE_SUFFIX},
        "depth_statement": (
            f"{len(node_rows)} nodal legs banked "
            f"{node_first.isoformat() if node_first else '—'} through "
            f"{node_last.isoformat() if node_last else '—'}; "
            f"{len(replayable)} span at least one whole calendar month. "
            "Nodal legs run WEEKS where hub strips run YEARS — a nodal replay "
            "is not comparable to a hub replay."
        ),
        "picker_note": (
            "This is NOT the /api/nodes/search universe. That lane browses an "
            "8-day hot tier (atlas_pnode_lmp_snapshot) whose pnode_ids do not "
            "intersect these replay-capable series at all. The leg picker "
            "component is shared; its universe is this list."
        ),
    }

    # ── Gas indices: the banked inventory, by name, with cadence + staleness.
    inv_rows = await _struct_read(_STRUCT_GAS_INVENTORY_SQL, {
        "datasets": [lane["dataset"] for lane in _STRUCT_GAS_DATASETS.values()],
    })
    by_dataset = {lane["dataset"]: key for key, lane in _STRUCT_GAS_DATASETS.items()}
    gas_indices = []
    for r in inv_rows:
        key = by_dataset.get(r["dataset"])
        if key is None:
            continue
        gas_indices.append(_struct_gas_meta(key, r["series"], r, today))
    gas_indices.sort(key=lambda g: (g["lane"], g["series"]))

    hrco_available = bool(gas_indices)
    reality_check = {
        "verdict": "PASS" if hrco_available else "STOP",
        "statement": (
            "Banked gas series exist, so HRCO ships. The calculator offers "
            "exactly the indices below, by name, each with its measured "
            "cadence, depth and staleness."
            if hrco_available else
            "No banked gas series is usable. HRCO renders as an honest absence "
            "('requires gas index — coming'); the swap and Asian structures "
            "are unaffected."
        ),
        "gaps_filed": [
            "No daily SoCal CITYGATE index is banked. socal_border is held and "
            "is a different delivery point; it is never labelled citygate.",
            "caiso_fuel_prices (dispatch-canonical) is lifecycle 'planned' and "
            "stale — staleness is reported in days per index.",
            "eia_gas_spot_prices has no banked complete month yet, so the "
            "WECC-relevant physical indices cannot replay one.",
            "Henry Hub is the only gas index deep enough to match the hub power "
            "strips, and it is NOT a California delivered-gas basis.",
        ],
        "no_proxy_rule": (
            "A missing index is a filed gap, never an improvised proxy. The "
            "calculator will not substitute one basis for another."
        ),
    }

    body = {
        "as_of": _utcnow().isoformat(),
        "posture": _STRUCT_POSTURE,
        "structures": [
            {
                "id": s["id"],
                "label": s["label"],
                "requires": list(s["requires"]),
                "settlement": s["settlement"],
                "needs_gas": s["needs_gas"],
                "y_basis": s["y_basis"],
                "available": (not s["needs_gas"]) or hrco_available,
                "unavailable_reason": (
                    None if (not s["needs_gas"]) or hrco_available
                    else "requires gas index — coming"
                ),
            }
            for s in _st.STRUCTURES.values()
        ],
        "blocks": [
            {
                "id": b["id"],
                "label": b["label"],
                "definition": b["definition"],
                "nominal_hours_per_day": b["nominal_hours_per_day"],
                "dst_note": (
                    "Hour counts are derived per date from the tz database: "
                    "7x16 is 16 hours every day, 7x24 is 23/24/25 across the "
                    "PST/PDT switch, and 7x8 absorbs the difference at 7/8/9."
                ),
            }
            for b in _st.BLOCKS.values()
        ],
        "blocks_omitted": [
            {
                "id": "6x16",
                "reason": (
                    "The NERC on-peak block needs a holiday calendar. This API "
                    "now computes the true NERC rule (fixed-date holiday on a "
                    "Sunday is observed the following Monday; on a Saturday it "
                    "is NOT moved) and agrees with the pantry's market_calendar "
                    "on all 66 rows — the calendar divergence that previously "
                    "blocked this block is fixed as of 2026-07-31. 6x16 is "
                    "still withheld because no lane has yet reconciled the "
                    "block ARITHMETIC against the pantry's own "
                    "caiso_blocks_daily column, and shipping an unreconciled "
                    "block is how two numbers that should match quietly stop "
                    "matching. Filed, not papered over."
                ),
            }
        ],
        "legs": {"hubs": hubs, "nodal": nodal},
        "gas_indices": gas_indices,
        "gas_leg_reality_check": reality_check,
        "bounds": {
            "screener_default_legs": list(_STRUCT_HUB_LEGS),
            "screener_max_node_legs": _STRUCT_SCREENER_MAX_NODE_LEGS,
            "measured_runtimes_ms": {
                "one_hub_leg_full_depth": 472,
                "three_hubs_24_months": 75,
                "three_hubs_full_depth": 907,
                "forty_node_legs_full_nodal_depth": 1470,
            },
            "bound_statement": (
                "The three hubs are the default sweep. Nodal legs are opt-in "
                "via an explicit list, capped at "
                f"{_STRUCT_SCREENER_MAX_NODE_LEGS}. An over-cap request is a "
                "400, never a silent truncation."
            ),
        },
        "sources": {
            "power": {"dataset": _STRUCT_POWER_DATASET, "market": "DA",
                      "cadence": "hourly", "unit": "$/MWh", "tz": MARKET_TZ},
            "gas": [
                {"lane": k, "dataset": v["dataset"], "unit": v["unit"],
                 "cadence": v["cadence"]}
                for k, v in _STRUCT_GAS_DATASETS.items()
            ],
            "block_arbiter": (
                "7x16 and 7x24 computed from caiso_lmp_da_hourly by these hour "
                "sets reproduce the independently-built caiso_blocks_daily lane "
                "to the last decimal on every fully-covered day checked."
            ),
        },
    }
    _struct_catalog_cache = (now, body)
    response.headers["X-Cache"] = "miss"
    return body


# ── POST /api/analytics/structures/evaluate ──────────────────────────────────
#
# Stateless. The structure definition IS the contract: what normalize_definition
# accepts and what this route returns are pinned in tests/test_structures.py
# next to the Python mirror of the client's shape guard. Nothing is stored — no
# saved structures in v0 (that is Phase 2, with the budget overlay).

async def _struct_evaluate(definition: dict) -> dict:
    """Shared engine for /evaluate and the screener's per-leg sweep.

    Reads the power leg (and the gas leg for an HRCO), gates months whole, and
    returns the diagram + replay envelope. Pure arithmetic lives in
    structures.py; this function is the read and the assembly.
    """
    block = definition["block"]
    leg_series = _struct_leg_series(definition["leg"])

    # Window: trim the READ, not just the result. `window_months` counts BANKED
    # months, and a month can be gated out, so the read reaches back further
    # than the request asks for (padding for gated months) and the exact
    # trim-to-N happens in structures.replay against banked months only.
    start = None
    wm = definition.get("window_months")
    if wm is not None:
        # Pad by 6 months so gated months inside the window (a coverage hole)
        # cannot silently shorten the replay below the requested count.
        back = wm + 6
        anchor = _utcnow().date().replace(day=1)
        yr, mo = anchor.year, anchor.month - back
        while mo <= 0:
            mo += 12
            yr -= 1
        start = _datetime(yr, mo, 1, tzinfo=_timezone.utc) - _timedelta(days=2)

    rows = await _struct_read(_STRUCT_POWER_SQL, {
        "dataset": _STRUCT_POWER_DATASET,
        "series_list": [leg_series],
        "start": start,
        "hours": _st.block_hour_list(block),
    })
    daily = [
        {"day": r["d"], "block_sum": r["block_sum"], "n_hours": r["n_hours"]}
        for r in rows if r["series"] == leg_series
    ]

    # ── Gas leg (HRCO only) ──────────────────────────────────────────────────
    gas_by_day = None
    gas_meta = None
    latest_gas = None
    if _st.needs_gas(definition):
        lane_key, series = _struct_split_gas_id(definition["gas_index"])
        lane = _STRUCT_GAS_DATASETS[lane_key]
        gas_start = None
        if daily:
            gas_start = min(r["day"] for r in daily) - _timedelta(
                days=_STRUCT_GAS_CARRY_IN_DAYS
            )
        gas_rows = await _struct_read(_STRUCT_GAS_SQL, {
            "dataset": lane["dataset"],
            "series": series,
            "start": gas_start,
        })
        if not gas_rows:
            # The index name validated against the catalog, but this lane holds
            # nothing in range. Honest absence, not a 500.
            gas_by_day = {}
        else:
            gas_by_day = _struct_resolve_gas(
                gas_rows, sorted({r["day"] for r in daily})
            )
            last = gas_rows[-1]
            latest_gas = {"date": last["d"].isoformat(), "price": float(last["price"])}
        gas_meta = _struct_gas_meta(
            lane_key, series,
            {
                "first_day": gas_rows[0]["d"] if gas_rows else None,
                "last_day": gas_rows[-1]["d"] if gas_rows else None,
                "days_held": len(gas_rows),
            },
            _utcnow().date(),
        )

    rep = _st.replay(definition, daily, gas_by_day)

    # ── The diagram ──────────────────────────────────────────────────────────
    # RAIL 1: an HRCO's strike is heat_rate x gas + adder, so the diagram needs
    # a gas price. It uses the MOST RECENT BANKED PRINT and labels it with its
    # date — a realized observation, never a forward mark. If no gas is banked,
    # the diagram is honestly withheld rather than drawn off a guess.
    diagram_strike = None
    diagram_basis = None
    if _st.needs_gas(definition):
        if latest_gas is None:
            diagram_basis = {
                "available": False,
                "reason": (
                    "No banked print for this gas index, so no strike can be "
                    "derived. The payoff shape needs a gas price; it is "
                    "withheld rather than drawn off an assumption."
                ),
            }
        else:
            diagram_strike = (definition["heat_rate"] * latest_gas["price"]
                              + definition["vom_adder"])
            diagram_basis = {
                "available": True,
                "gas_price": latest_gas["price"],
                "gas_as_of": latest_gas["date"],
                "strike": round(diagram_strike, 6),
                "derivation": (
                    f"strike = heat_rate {definition['heat_rate']} x gas "
                    f"{latest_gas['price']} + adder {definition['vom_adder']}"
                ),
                "reason": (
                    "Struck off the most recent BANKED gas print, stated with "
                    "its date. A realized observation, not a forward mark."
                ),
            }

    # MWh for the diagram's dollar axis: the LAST banked month's block hours,
    # labelled. Never a guessed 30-day month — if nothing is banked, the
    # diagram ships per-MWh only and the client scales.
    diagram_mwh = None
    diagram_mwh_basis = None
    if rep["rows"]:
        last_row = rep["rows"][-1]
        diagram_mwh = last_row["mwh"]
        diagram_mwh_basis = (
            f"{last_row['month']} — {last_row['block_hours']} {block} hours x "
            f"{definition['size_mw']} MW"
        )

    if _st.needs_gas(definition) and diagram_strike is None:
        diagram = None
    else:
        diagram = _st.payoff_diagram(definition, strike=diagram_strike,
                                     mwh=diagram_mwh)
        diagram["mwh_basis"] = diagram_mwh_basis

    return {
        "posture": _STRUCT_POSTURE,
        "definition": definition,
        "leg_resolved": {"series": leg_series,
                         **_struct_leg_display(definition["leg"]["kind"], leg_series)},
        "diagram": diagram,
        "diagram_basis": diagram_basis,
        "replay": rep,
        "gas_index": gas_meta,
        "sources": {
            "power": {"dataset": _STRUCT_POWER_DATASET, "market": "DA",
                      "cadence": "hourly", "unit": "$/MWh", "tz": MARKET_TZ,
                      "block": block,
                      "block_definition": _st.BLOCKS[block]["definition"]},
            "gas": (None if gas_meta is None else
                    {"dataset": gas_meta["dataset"], "series": gas_meta["series"],
                     "unit": gas_meta["unit"], "cadence": gas_meta["cadence"]}),
        },
    }


async def _struct_known_gas_ids() -> set[str]:
    """Every SELECTABLE banked gas index id, for definition validation.

    Reads the catalog's own inventory so /evaluate and /catalog can never
    disagree about what is banked, then drops the lanes that are banked but not
    a legitimate strike basis (_STRUCT_GAS_LANES_NOT_SELECTABLE). Being on the
    catalog's inventory is not the same claim as being on the menu.
    """
    rows = await _struct_read(_STRUCT_GAS_INVENTORY_SQL, {
        "datasets": [lane["dataset"] for lane in _STRUCT_GAS_DATASETS.values()],
    })
    by_dataset = {lane["dataset"]: key for key, lane in _STRUCT_GAS_DATASETS.items()
                  if key not in _STRUCT_GAS_LANES_NOT_SELECTABLE}
    return {
        _struct_gas_id(by_dataset[r["dataset"]], r["series"])
        for r in rows if r["dataset"] in by_dataset
    }


def _struct_guard_gas_lane(gas_index) -> None:
    """400 with the REASON when a request strikes off a non-selectable lane.

    Without this the caller only sees 'not a banked gas index', which is a lie
    about caiso_fuel.* — it IS banked, it is just not a strike basis.
    """
    if not isinstance(gas_index, str):
        return
    lane_key = gas_index.strip().partition(_STRUCT_GAS_ID_SEP)[0]
    reason = _STRUCT_GAS_LANES_NOT_SELECTABLE.get(lane_key)
    if reason:
        raise HTTPException(
            status_code=400,
            detail=f"gas_index: '{gas_index.strip()}' is not a selectable gas "
                   f"index. {reason}",
        )


@app.post(f"{STRUCTURES_PREFIX}/evaluate")
async def structures_evaluate(request: Request):
    """
    Structure definition in — payoff diagram + historical replay out.

    Stateless and side-effect free. Request body (the pinned contract):
        {
          "structure": "swap" | "asian_call" | "asian_put"
                     | "asian_digital_call" | "asian_digital_put"
                     | "hrco_monthly" | "hrco_daily",
          "leg": {"kind": "hub" | "node", "id": "SP15"},
          "block": "7x16" | "7x8" | "7x24",        # default 7x16
          "size_mw": 50,                            # default 1
          "fixed_price": 26.0,                      # swap
          "strike": 26.0,                           # asian + digital
          "payout": 100000.0,                       # digital (lump sum $/month)
          "heat_rate": 7.5,                         # hrco (MMBtu/MWh)
          "vom_adder": 3.50,                        # hrco, default 0
          "gas_index": "eia_hh.HENRY_HUB",          # hrco, from /catalog
          "window_months": 24                       # null = all banked months
        }

    Response carries `diagram` (a pure function of the inputs — renders even
    when the replay is empty), `replay` (month-by-month over banked history,
    with the depth statement and every gated month's receipt), and the posture
    rails. An unknown structure/block/leg/gas index, or a malformed number, is
    a 400 that NAMES the offending field. DB unavailable -> 503.

    The replay is realized history only: every figure is what the structure
    WOULD HAVE PAID. There is no premium, no valuation, and no forecast.
    """
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON.")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object.")

    # Validate the gas index against the banked inventory, but only pay for the
    # inventory read when the structure actually needs one.
    known = None
    kind = raw.get("structure")
    if isinstance(kind, str) and _st.STRUCTURES.get(kind, {}).get("needs_gas"):
        _struct_guard_gas_lane(raw.get("gas_index"))
        known = await _struct_known_gas_ids()

    try:
        definition = _st.normalize_definition(raw, known_gas_indices=known)
    except _st.StructureError as e:
        raise HTTPException(status_code=400, detail=f"{e.field}: {e.message}")

    # A node leg must be in the replay-capable universe. Rejecting here — with
    # the reason — is what keeps the room from offering a leg it cannot serve
    # (see the namespace deviation note at the top of this section).
    if definition["leg"]["kind"] == "node":
        leg_series = _struct_leg_series(definition["leg"])
        probe = await _struct_read(
            "SELECT 1 AS ok FROM timeseries_values WHERE dataset = %(dataset)s "
            "AND series = %(series)s LIMIT 1",
            {"dataset": _STRUCT_POWER_DATASET, "series": leg_series},
        )
        if not probe:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"leg.id: '{definition['leg']['id']}' is not a "
                    "replay-capable nodal leg. The replay-capable universe is "
                    f"served by GET {STRUCTURES_PREFIX}/catalog (legs.nodal); "
                    "it is NOT the /api/nodes/search universe."
                ),
            )

    return await _struct_evaluate(definition)


# ── GET /api/analytics/structures/screener ───────────────────────────────────

@app.get(f"{STRUCTURES_PREFIX}/screener")
async def structures_screener(
    response: Response,
    structure: str = Query(
        default="asian_call",
        description=f"Structure id. One of {sorted(_st.STRUCTURES)}.",
    ),
    block: str = Query(default="7x16", description="Block id: 7x16, 7x8 or 7x24."),
    size_mw: float = Query(default=1.0, description="Size in MW. Default 1."),
    fixed_price: Optional[float] = Query(default=None, description="Swap fixed price."),
    strike: Optional[float] = Query(default=None, description="Option strike, $/MWh."),
    payout: Optional[float] = Query(default=None, description="Digital payout, $/month."),
    heat_rate: Optional[float] = Query(default=None, description="HRCO heat rate, MMBtu/MWh."),
    vom_adder: Optional[float] = Query(default=None, description="HRCO VOM adder, $/MWh."),
    gas_index: Optional[str] = Query(default=None, description="HRCO gas index id from /catalog."),
    window_months: Optional[int] = Query(
        default=24,
        description="Banked months to replay per leg. Omit or pass 0 for all banked months.",
    ),
    legs: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated leg ids to sweep. Hub ids (SP15/NP15/ZP26) and "
            "nodal ids may be mixed. Omitted sweeps the three hubs. Nodal legs "
            f"are capped at {_STRUCT_SCREENER_MAX_NODE_LEGS}."
        ),
    ),
    direction: str = Query(
        default="desc",
        description="Sort direction of the realized-payoff ranking: desc or asc.",
    ),
):
    """
    One structure definition swept across legs, ranked by realized payoff.

    RAIL 3 IN THE PAYLOAD, NOT JUST THE DOCSTRING: this is a descriptive sort
    of historical arithmetic. Legs that cannot be replayed are returned in
    `unavailable` WITH their reason rather than dropped — a leg missing from a
    ranking would itself read as a ranking. And because depth is NOT uniform
    (hub strips run years, nodal legs run weeks), the payload states whether
    the ranked legs share a window and refuses to imply they do when they
    do not.

    Bounds are explicit and stated in the payload: the three hubs by default,
    nodal legs opt-in via `legs` and capped at 40 (the measured bound — see
    _STRUCT_SCREENER_MAX_NODE_LEGS). An over-cap request is a 400 — never a
    silent truncation.

    Errors: unknown structure/block/leg/gas index or a bad number -> 400
    naming the field; over-cap nodal request -> 400; DB unavailable -> 503.
    """
    # ── Resolve the leg list ─────────────────────────────────────────────────
    if legs is None or not legs.strip():
        leg_ids = list(_STRUCT_HUB_LEGS)
    else:
        leg_ids = [x.strip().upper() for x in legs.split(",") if x.strip()]
        if not leg_ids:
            raise HTTPException(status_code=400, detail="`legs` cannot be empty.")

    hub_set = {h.upper() for h in _STRUCT_HUB_LEGS}
    resolved: list[dict] = []
    for lid in leg_ids:
        kind = "hub" if lid in hub_set else "node"
        resolved.append({"kind": kind, "id": lid})
    n_nodes = sum(1 for l in resolved if l["kind"] == "node")
    if n_nodes > _STRUCT_SCREENER_MAX_NODE_LEGS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"`legs` requests {n_nodes} nodal legs; the measured cap is "
                f"{_STRUCT_SCREENER_MAX_NODE_LEGS} (~1.5 s cold for 40 legs at "
                "full nodal depth). Narrow the list — the sweep will not "
                "silently truncate it."
            ),
        )

    known = None
    if _st.STRUCTURES.get(structure, {}).get("needs_gas"):
        known = await _struct_known_gas_ids()

    # ── Build one definition per leg from the shared knobs ───────────────────
    base = {
        "structure": structure,
        "block": block,
        "size_mw": size_mw,
        "window_months": None if not window_months else window_months,
    }
    for field, value in (("fixed_price", fixed_price), ("strike", strike),
                         ("payout", payout), ("heat_rate", heat_rate),
                         ("vom_adder", vom_adder), ("gas_index", gas_index)):
        if value is not None:
            base[field] = value

    started = time.perf_counter()
    results: list[dict] = []
    definition_echo: dict | None = None
    for leg in resolved:
        try:
            definition = _st.normalize_definition({**base, "leg": leg},
                                                  known_gas_indices=known)
        except _st.StructureError as e:
            raise HTTPException(status_code=400, detail=f"{e.field}: {e.message}")
        if definition_echo is None:
            definition_echo = {k: v for k, v in definition.items() if k != "leg"}
        out = await _struct_evaluate(definition)
        rep = out["replay"]
        results.append({
            "leg": out["leg_resolved"],
            "months": rep["months"],
            "first_month": rep["window"]["first_month"],
            "last_month": rep["window"]["last_month"],
            "total": rep["total"],
            "total_mwh": rep["total_mwh"],
            "payoff_per_mwh": (round(rep["total"] / rep["total_mwh"], 6)
                               if rep["total_mwh"] else None),
            "months_paying": sum(1 for r in rep["rows"] if r["payoff"] > 0),
            "best_month": (max(rep["rows"], key=lambda r: r["payoff"])["month"]
                           if rep["rows"] else None),
            "worst_month": (min(rep["rows"], key=lambda r: r["payoff"])["month"]
                            if rep["rows"] else None),
            "depth_statement": rep["depth_statement"],
            "reason": rep["reason"],
        })

    try:
        ranking = _st.rank_legs(results, direction=direction)
    except _st.StructureError as e:
        raise HTTPException(status_code=400, detail=f"{e.field}: {e.message}")

    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
    response.headers["X-Query-Ms"] = str(elapsed_ms)

    return {
        "as_of": _utcnow().isoformat(),
        "posture": _STRUCT_POSTURE,
        "definition": definition_echo,
        "bound": {
            "legs_requested": len(resolved),
            "hub_legs": len(resolved) - n_nodes,
            "node_legs": n_nodes,
            "max_node_legs": _STRUCT_SCREENER_MAX_NODE_LEGS,
            "truncated": False,
            "statement": (
                f"Swept {len(resolved)} leg(s) — every leg requested was swept; "
                "nothing was truncated. Nodal legs are capped at "
                f"{_STRUCT_SCREENER_MAX_NODE_LEGS} and an over-cap request is "
                "rejected rather than trimmed."
            ),
        },
        "runtime_ms": elapsed_ms,
        **ranking,
        "sources": {
            "power": {"dataset": _STRUCT_POWER_DATASET, "market": "DA",
                      "cadence": "hourly", "unit": "$/MWh", "tz": MARKET_TZ},
        },
    }

# ═══════════════════════════════════════════════════════════════════════════
# THE ANALYTICS DEPARTMENT, ROOM 1 — NODES  (the Node Analytics Package)
# spec 2026-07-30. Sibling of ROOM 2 — STRUCTURES, directly above.
#
#   GET /api/analytics/node-search?q=        find a node
#   GET /api/analytics/node/{pnode_id}       the package (identity/dart/basis/
#                                            components/ledger)
#   GET /api/analytics/movers?window=&metric=  top-50 both directions
#
# ── HOUSE AXIOMS (pinned by this lane's tests) ──────────────────────────────
# FMM = RTPD. DART = FMM − DA, POSITIVE = RT ABOVE DA. RTD is display context
# and is NEVER a settlement leg here — this lane reads DAM and RTPD only.
#
# NOTE ON SIGN, read this before "fixing" anything: the ratified nodal
# convention (see /api/atlas/pnode-history, "DART = FMM − DAM, ratified
# positive = RT over DA") is what this lane implements. The OLDER hub reader
# (/api/timeseries/caiso-hub-lmp) and the Watchboard `dart` tile emit the
# OPPOSITE sign under the field name `spread` (DA − RT, "positive = DA over
# RT"). Both live in this file. They are different fields on different
# endpoints and neither is wrong — but they are NOT interchangeable, and a
# dashboard that mixes them draws an inverted chart. This lane's field is
# named `dart`, never `spread`, precisely so the two never get confused.
#
# ── BANKED DEPTH IS THE HEADLINE, NOT A FOOTNOTE (STOP-gate 3, TRIPPED) ────
# atlas_pnode_lmp_snapshot is a HOT TIER with ~7-day retention and a
# dispatch-only writer that has NO standing schedule. Measured 2026-07-30:
#
#   market   min_date     max_date     calendar dates   COMPLETE days
#   DAM      2026-07-22   2026-07-29   8                6  (07-23..07-28)
#   RTPD     2026-07-22   2026-07-29   8                6  (07-23..07-28)
#
# 07-22 is partial (opens HE22) and 07-29 is partial (ends HE21). So a
# "trailing window" is EIGHT DATES, not the ~21 the spec's example caption
# assumed, and every per-HE distribution has N=6..8 — never more.
#
# CONSEQUENCES, stated rather than papered over:
#   * The monthly ledger has exactly ONE row (2026-07), and that row is 8
#     days of a 31-day month. There are NO prior full months in depth. The
#     ledger code generalizes to many months; the DATA does not yet.
#   * A p5/p95 band computed on N=7 is very nearly the min/max of seven
#     numbers. The bands are real and honestly labelled, but they are NOT a
#     climatology. Every band carries its own `n`.
#   * `complete: false` on a month/block means the banked window does not
#     cover it end-to-end. It is never silently dropped.
# EVERY response carries `depth` + `as_of`. Nothing in this lane implies
# history the pantry does not hold.
#
# ── HUB PAIRING RULE v0 (STOP-gate 1, RESOLVED — attribute found) ───────────
# The spec guessed at "geographic zone membership via universe/gazetteer
# attributes". There is no zone attribute: `area` is a BALANCING AUTHORITY
# (CA, BPAT, APS, PACE...), not NP15/SP15/ZP26, and nothing carries a lat/lon
# zone polygon. What DOES exist is `atlas_pnode_universe.th_hub` — CAISO's own
# published node→trading-hub attribution. That is the pairing, used verbatim:
#
#   TH_NP15_GEN-APND -> NP15     TH_PACE_GEN-APND -> (no hub price series)
#   TH_SP15_GEN-APND -> SP15     TH_PACW_GEN-APND -> (no hub price series)
#   TH_ZP26_GEN-APND -> ZP26
#
# NO GEOGRAPHIC INFERENCE. A node is paired because CAISO says so, or it is
# not paired and gets `hub: null` + an ABSENT basis panel. Measured coverage
# against the 14,827-node priced universe (2026-07-30):
#
#   all priced nodes          1,579 / 14,827  = 10.7%
#   CA-area nodes             1,579 /  4,252  = 37.1%
#   CA-area GEN nodes         1,487 /  1,860  = 79.9%   <- the asset-manager wedge
#   LOAD nodes                   92 / 10,943  =  0.8%
#
# Read that bottom row before wiring the dashboard: basis is a GENERATOR
# story. Most LOAD nodes and essentially every non-CAISO WEIM node (BPAT,
# APS, PACE...) have no CAISO trading hub, which is CORRECT — not a gap to
# fill with a guess. TH_PACE/TH_PACW are paired by CAISO but have no banked
# hub price series, so they are reported as paired-but-unpriceable
# (`hub_priceable: false`) rather than silently treated as unpaired.
#
# TH hub nodes themselves are NOT in the snapshot table (verified: no
# TH_*_GEN-APND rows in the priced universe) — the hub leg always comes from
# timeseries_values. That standing schema fact holds.
#
# ── THE 42GB DISCIPLINE (115's lesson) — every shape stated ─────────────────
# One correction to the spec's premise: the 42GB table is `timeseries_values`
# (43 GB). `atlas_pnode_lmp_snapshot` is 6.8 GB / ~23M rows. Both are handled
# under the same rule.
#
# The load-bearing fact, measured on this compute (Neon 0.25-2 CU, pg17):
# the planner ABANDONS idx_lmp_snap_market_time for any predicate matching
# more than roughly 5% of the snapshot table and parallel-seq-scans instead.
# Observed, all EXPLAIN (ANALYZE) on real data:
#
#   shape                                        rows      plan            time
#   market=DAM  + 1 date                          311k     bitmap index    156 ms
#   market=DAM  + 7 dates                         2.2M     SEQ SCAN       2,231 ms
#   market=RTPD + 1 date                          1.4M     SEQ SCAN       1,387 ms
#   market=RTPD + 7 dates (GROUP BY node,d,he)    9.5M     SEQ SCAN + spill  38 s
#   DISTINCT (d,he,iv) over RTPD 7 dates          9.5M     SEQ SCAN      18,071 ms
#   market=RTPD + 1 date + 1 hour                  59k     bitmap index     62 ms
#   market=RTPD + 1 date + 6 hours (VALUES join)  356k     bitmap index    226 ms
#   market=RTPD + 1 date + 24 hours (VALUES join) 1.4M     SEQ SCAN       1,818 ms
#   market=DAM  + 1 date + 6 hours (VALUES join)   89k     INDEX SCAN     2,430 ms
#   market=DAM  + 1 date + ANY(24 hours)          356k     bitmap heap    1,254 ms
#
# So the movers lane chunks EVERY read, and the two markets get DIFFERENT
# shapes — the last two rows are why. RTPD reads in six-hour chunks via an
# inline VALUES grid of (hour, 1/k) PLUS a redundant `market_hour BETWEEN`
# bound; both parts are load-bearing (the VALUES grid puts the tiny relation on
# the outer side of a nested loop, giving one bitmap index scan per hour, and
# the BETWEEN bound keeps the row estimate under the cliff). DAM reads a whole
# date per chunk via `market_hour = ANY(hours)`: its rows are 4x sparser per
# hour, and forcing the RTPD shape onto it collapses the nested loop into a
# plain Index Scan that costs 2,430 ms for a QUARTER of the rows.
# Grouping by (pnode_id, market_hour) instead of pnode_id alone adds an
# external-merge sort; the 1/k weighting below is what avoids that.
#
# WEIGHTED SINGLE-PASS TRICK. The exact statistic is the mean over matched
# hours of (hourly-mean RTPD − DA). Computing it the obvious way needs a
# GROUP BY (pnode_id, market_date, market_hour) over 9.5M rows, which sorts
# and spills 430 MB. It is avoidable because RTPD interval coverage is
# NODE-INDEPENDENT — the writer pulls the whole universe per dispatch, so a
# ragged hour is ragged for every node at once (verified: 2026-07-23 HE17
# carries 3 intervals x 14,827 nodes = 44,481 rows exactly; HE24 carries
# 2 x 14,827 = 29,654). Therefore the per-hour interval count k(d,he) is a
# SCALAR GRID, readable from ONE reference node off the PK index in ~7 ms,
# and
#     mean_h RTavg(h)  =  sum_i  lmp_i / (H * k(d,he of i))
# which is a single HashAggregate keyed on pnode_id — no sort, no spill.
# `_an_movers_grid` reads that grid; `_AN_MOVERS_RT_CHUNK_SQL` applies it.
#
# Per-node reads (the package) need none of this: one pnode + one market off
# the PK index is ~192 rows (DAM) / ~700 rows (RTPD) and is index-only.
#
# ── ERROR CONTRACT (conforms to the proven endpoints) ───────────────────────
#   * blank / unknown pnode_id                 -> 400 (matches pnode-history)
#   * bad window / metric / limit              -> 400 with allowed values
#   * a known node with no rows in depth       -> 200, honest empty payload
#   * a known node with no CAISO hub           -> 200, hub:null, basis absent
#   * DB unavailable                           -> 503 (house standard)
# A node that exists but has nothing to say is never a 404 and never a
# fabricated flat line.
# ═══════════════════════════════════════════════════════════════════════════

_analytics_log = logging.getLogger("energylake.analytics")

# The two settlement legs. RTD is deliberately absent (house axiom).
AN_DA_MARKET = "DAM"
AN_RT_MARKET = "RTPD"

# P2 sentinel floor — RTD/RTPD carry market_date = 0001-01-01 rows. Shared with
# the Cockpit lane so there is ONE floor in this file.
AN_SENTINEL_MIN_DATE = COCKPIT_SENTINEL_MIN_DATE

# Pairing rule v0. CAISO's published th_hub -> the hub label used by
# timeseries_values. A th_hub absent from this map is paired-but-unpriceable.
AN_TH_HUB_TO_ZONE = {
    "TH_NP15_GEN-APND": "NP15",
    "TH_SP15_GEN-APND": "SP15",
    "TH_ZP26_GEN-APND": "ZP26",
}
# Paired by CAISO, but no banked hub price series exists for these.
AN_TH_HUB_UNPRICEABLE = ("TH_PACE_GEN-APND", "TH_PACW_GEN-APND")

# The hub DA dataset the basis leg joins against (hourly, PT-aligned via ts).
AN_HUB_DA_DATASET = "caiso_lmp_da_hourly"

# Blocks. 7x16 is HE7..HE22 on ALL days (7 days a week x 16 hours) — this is
# the spec's explicit definition and it is NOT the NERC on-peak block that
# _lmp_is_onpeak() implements (Mon-Sat ex-holiday). Off-peak is the complement.
AN_ONPEAK_HE_LO = 7
AN_ONPEAK_HE_HI = 22
AN_BLOCK_ONPEAK = "7x16"
AN_BLOCK_OFFPEAK = "offpeak"

# The envelope's quantiles, in monotone order. Pinned by test.
AN_ENVELOPE_QUANTILES = (("p5", 0.05), ("p25", 0.25), ("p50", 0.50),
                         ("p75", 0.75), ("p95", 0.95))

# Movers.
AN_MOVERS_WINDOWS = {"1d": 1, "7d": 7}
AN_MOVERS_METRICS = ("dart", "basis")
AN_MOVERS_TOP_N = 50

# ── CONVERGENCE-BIDDING ELIGIBILITY FILTER (cb_only) ────────────────────────
# lane ruled 2026-07-31.
#
# THE CONTRACT IS THE TABLE'S, NOT THIS LANE'S. `caiso_cb_eligible_nodes` is
# VINTAGED: every OASIS ATL_PNODE pull lands as a new vintage_date and nothing
# is ever deleted or overwritten. Its COMMENT states the consumer contract in
# one line, and this is that line implemented:
#
#     WHERE cb_eligible AND vintage_date = (SELECT max(vintage_date)
#                                             FROM caiso_cb_eligible_nodes)
#
# NEVER READ IT UNSCOPED. An unscoped read unions every vintage ever pulled and
# silently answers with a node that WAS eligible at some point in history. The
# scoping subquery below is not defensive style — it is the contract.
#
# ABSENT != INELIGIBLE. The export carries the FULL pnode universe per vintage
# with an explicit Y/N flag, so a node missing from the vintage is a node the
# export did not mention, not a node that was refused. `cb_only=true` filters to
# the explicit YES set and reports its size; it does not attempt to speak for
# the nodes the vintage is silent about.
#
# WHERE THE FILTER IS APPLIED, and why it is not in the SQL. The movers read is
# a chunked universe-wide sweep whose shape is measured and pinned (see the
# query-plan note on the endpoint); threading an id list into those chunks would
# change the plan the measurements were taken against. The eligible set is one
# small keyed read (3,082 ids at the 2026-07-16 vintage), so the filter is
# applied to the per-node statistic AFTER the sweep and BEFORE the ranking. The
# sweep's cost is identical either way; only the ranking universe narrows.
#
# DEFAULT false. The public boards rank the whole priced universe exactly as
# they did before this parameter existed — same reads, same ranking, same
# numbers. Off, the filter costs ZERO extra reads: the eligible set is not
# fetched at all, and the echoed block says so with nulls rather than a stale
# count.
AN_CB_ELIGIBLE_TABLE = "caiso_cb_eligible_nodes"

# The contract, verbatim on the wire so a consumer can check the server did it.
AN_CB_VINTAGE_RULE = (
    "current vintage only: WHERE cb_eligible AND vintage_date = "
    "(SELECT max(vintage_date) FROM caiso_cb_eligible_nodes). The table is "
    "vintaged and append-only - every OASIS ATL_PNODE pull is a new "
    "vintage_date and none is ever deleted, so an UNSCOPED read would union "
    "every vintage ever pulled and answer with nodes that were eligible at "
    "some point in history. Absent from the vintage != ineligible: the export "
    "carries the full pnode universe with an explicit Y/N flag, so this filters "
    "to the explicit YES set only."
)

# One read, the contract exactly. Index-served by idx_ccen_vintage_eligible
# (vintage_date) WHERE cb_eligible.
_AN_CB_ELIGIBLE_SQL = """
    SELECT node_id,
           (SELECT max(vintage_date) FROM caiso_cb_eligible_nodes) AS vintage_date
    FROM caiso_cb_eligible_nodes
    WHERE cb_eligible
      AND vintage_date = (SELECT max(vintage_date) FROM caiso_cb_eligible_nodes)
"""


async def _an_cb_eligible() -> tuple:
    """The current-vintage CB-eligible node set, as (ids, vintage_date).

    `vintage_date` is None only when the table is empty, in which case the id
    set is empty too and the caller reports an eligible universe of 0 rather
    than silently ranking everything.
    """
    rows = await _an_read(_AN_CB_ELIGIBLE_SQL, {})
    ids = {r["node_id"] for r in rows}
    vintage = _an_as_date(rows[0]["vintage_date"]) if rows else None
    return ids, vintage


def _an_cb_block(cb_only: bool, vintage, universe, excluded) -> dict:
    """The echoed filter block, present on every movers response.

    Additive and always present, so a consumer never has to guess whether the
    board it is reading was filtered. With the filter OFF the counts are null,
    not zero — nothing was read, so there is no number to report and a 0 would
    read as "no eligible nodes exist".
    """
    return {
        "cb_only": cb_only,
        "applied": cb_only,
        "source": AN_CB_ELIGIBLE_TABLE,
        "vintage_rule": AN_CB_VINTAGE_RULE,
        "vintage_date": vintage.isoformat() if vintage else None,
        "eligible_universe": universe,
        "excluded_not_cb_eligible": excluded,
        "note": (None if cb_only else
                 "filter off (default): the whole priced universe ranks, and "
                 "the eligible set was not read - the null counts mean not "
                 "measured, not zero"),
    }
# Six hours: the widest chunk that stays on idx_lmp_snap_market_time (see the
# measured table above). Do not raise this without re-running the EXPLAINs.
AN_MOVERS_CHUNK_HOURS = 6
# How many chunk reads run concurrently. The pool is small and the compute is
# 2 CU, so this is deliberately modest — it cuts wall-clock without stampeding.
AN_MOVERS_CONCURRENCY = 4
# Movers is a screen over a hot tier whose writer has no standing schedule, so
# the answer changes only when a dispatch lands. Cache per (metric, window).
AN_MOVERS_CACHE_TTL = 300.0

# Node search.
AN_SEARCH_MAX = 50

_an_movers_cache: "dict[tuple, tuple[float, dict]]" = {}

AN_PT = ZoneInfo("America/Los_Angeles")


# ── Small numeric helpers ────────────────────────────────────────────────────

def _an_f(x):
    """Decimal/numeric -> float; None passes through (absence is not zero)."""
    return float(x) if x is not None else None


def _an_r(x, nd: int = 4):
    """Round for the wire, preserving None. 4dp — prices are dollars/MWh and
    the hand-verification in the handoff is done at this precision."""
    return None if x is None else round(float(x), nd)


def _an_mean(vals):
    """Arithmetic mean of a list of floats, or None when empty."""
    return (sum(vals) / len(vals)) if vals else None


def _an_percentile_cont(sorted_vals, q: float):
    """Postgres `percentile_cont(q)` — linear interpolation between the two
    closest ranks. Implemented explicitly (not statistics.quantiles) so the
    handoff's SQL cross-check and this code agree to the last decimal.

    `sorted_vals` MUST already be sorted ascending and non-empty.
    """
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return float(sorted_vals[lo]) + frac * (float(sorted_vals[hi]) - float(sorted_vals[lo]))


def _an_envelope(vals):
    """The p5/p25/p50/p75/p95 envelope + mean + n over one HE's samples.

    Monotone BY CONSTRUCTION: every quantile reads the same ascending list, and
    percentile_cont is non-decreasing in q. The suite pins that property anyway
    (acceptance 3) because it is the one thing a chart cannot survive breaking.
    Returns None when there is nothing to describe — never a zero-filled band.
    """
    if not vals:
        return None
    s = sorted(float(v) for v in vals)
    out = {"n": len(s), "mean": _an_r(_an_mean(s))}
    for name, q in AN_ENVELOPE_QUANTILES:
        out[name] = _an_r(_an_percentile_cont(s, q))
    return out


def _an_band_of(value, env) -> "str | None":
    """Which envelope band a realized value lands in — the chart's dot colour.
    Absence in, absence out."""
    if value is None or not env:
        return None
    if env["p5"] is None:
        return None
    if value < env["p5"]:
        return "below_p5"
    if value < env["p25"]:
        return "p5_p25"
    if value < env["p50"]:
        return "p25_p50"
    if value < env["p75"]:
        return "p50_p75"
    if value <= env["p95"]:
        return "p75_p95"
    return "above_p95"


# ── The class/context grammar (shared by the package and the Regime lane) ────
#
# `_an_band_of` above names SIX classes. They are the department's ONE class
# vocabulary: the dart latest-day rows have always carried them under the field
# name `band`, and from the Regime lane the basis per-HE rows carry the SAME
# six names under `class`. Both are read by the dashboard's `classifyBand`
# (which accepts `band ?? class`), so a consumer never has to learn two words
# for one idea. `_an_context_label` renders the DISPLAY string, and it is the
# one place the depth grammar bites: percentile words are earned at
# AN_PERCENTILE_FLOOR complete samples and refused below it.
#
# This mirrors the dashboard's `bandLabel(band, n)` in
# src/components/analytics/analytics.ts VERBATIM — same floor, same six
# strings, same fallback — so the server and the client cannot drift into two
# different readings of the same class.

# Complete-day floor below which percentile VOCABULARY is a costume for
# min/max. Mirrors the dashboard's PERCENTILE_FLOOR. Pinned by test.
AN_PERCENTILE_FLOOR = 15


def _an_context_label(band, n: int) -> "str | None":
    """One class name -> the words a surface may print at depth `n`.

    At or above the floor the percentile names are accurate and are used. Below
    it the words describe where the value LANDED instead, because "above p95"
    over seven samples means "the highest of seven" and should say so.
    """
    if not band:
        return None
    if n >= AN_PERCENTILE_FLOOR:
        if band.startswith("above_"):
            return f"above {band[6:]}"
        if band.startswith("below_"):
            return f"below {band[6:]}"
        return band.replace("_", "–")
    return {
        "above_p95": "above the observed range",
        "p75_p95": "upper range",
        "p50_p75": "above median",
        "p25_p50": "below median",
        "p5_p25": "lower range",
        "below_p5": "below the observed range",
    }.get(band, "within range")


# ── Stance verdicts (the counted one-liner over a panel) ────────────────────
#
# A stance is a TALLY OF CLASSES and nothing else — no model, no re-derivation
# from levels, no adjective beyond the mapped verdict word. The rule is the
# majority of quartile extremes: hours at/above the upper-range class against
# hours at/below the lower-range class.
#
# THE COUNT IS PART OF THE VERDICT. `line` always carries the arithmetic and is
# built in the same expression as `verdict`, so there is no code path that can
# emit a bare BULLISH. When nothing is classed the object is an honest absence
# with a reason and carries NO verdict key at all.
#
# The wording mirrors the dashboard's `stance.ts` BYTE FOR BYTE (same counts,
# same arrow, same reads) so the two computations of one truth agree exactly.
# Note the reads are DART-worded ("real time ran above...") and the dashboard
# applies them to BOTH metrics; that oddity is reproduced rather than improved,
# because a server line that read better but differently would be a second
# truth. Rewording is a dashboard-lane change, and it belongs there.
AN_STANCE_UPPER_CLASSES = ("above_p95", "p75_p95")
AN_STANCE_MIDDLE_CLASSES = ("p50_p75", "p25_p50")
AN_STANCE_LOWER_CLASSES = ("p5_p25", "below_p5")

AN_STANCE_READS = {
    "BULLISH": "real time ran above its banked shape in more hours than below",
    "BEARISH": "real time ran below its banked shape in more hours than above",
    "NEUTRAL": "real time split evenly against its banked shape",
}

AN_STANCE_RULE = ("majority of quartile extremes: hours at/above the upper-range "
                  "class against hours at/below the lower-range class")


def _an_stance_side(band) -> "str | None":
    """Which side of the ladder a class sits on. Unknown/absent -> None, which
    is counted NOWHERE — never guessed into a tail."""
    if band in AN_STANCE_UPPER_CLASSES:
        return "upper"
    if band in AN_STANCE_MIDDLE_CLASSES:
        return "middle"
    if band in AN_STANCE_LOWER_CLASSES:
        return "lower"
    return None


def _an_stance(bands, reason: str) -> dict:
    """Tally classes into the panel's verdict. `reason` is used when nothing is
    classed, which is an absence, not a NEUTRAL."""
    upper = lower = middle = 0
    for b in bands:
        side = _an_stance_side(b)
        if side == "upper":
            upper += 1
        elif side == "lower":
            lower += 1
        elif side == "middle":
            middle += 1
    n = upper + lower + middle
    if n == 0:
        return {"available": False, "reason": reason}
    verdict = ("BULLISH" if upper > lower
               else "BEARISH" if lower > upper else "NEUTRAL")
    return {
        "available": True,
        "verdict": verdict,
        "upper": upper, "lower": lower, "middle": middle, "n": n,
        "line": f"{upper}h upper / {lower}h lower of {n} banked → {AN_STANCE_READS[verdict]}",
        "rule": AN_STANCE_RULE,
    }


def _an_classing(entries, value_key: str) -> dict:
    """Server-supplied class + context for ONE hour, against that hour's OWN
    banked history (never cross-node, never cross-hour).

    The classed value is the LATEST banked day's value at this hour — the same
    reading the dart latest-day rows carry — and `current_date` travels with it
    because the banked frontier is ragged (a mid-dispatch day holds HE1..HE16,
    so HE20's latest day is yesterday while HE3's is today).

    The history INCLUDES the classed value, exactly as the department's envelope
    spans every banked day including the latest.
    """
    if not entries:
        return {"current": None, "current_date": None, "class": None,
                "context": None, "class_n": 0}
    vals = [e[value_key] for e in entries]
    env = _an_envelope(vals)
    latest = max(entries, key=lambda e: e["date"])
    band = _an_band_of(latest[value_key], env)
    n = env["n"] if env else 0
    return {
        "current": _an_r(latest[value_key]),
        "current_date": latest["date"].isoformat(),
        "class": band,
        "context": _an_context_label(band, n),
        "class_n": n,
    }


def _an_is_onpeak_he(he: int) -> bool:
    """7x16 block test: HE7..HE22, EVERY day of the week. Not NERC on-peak."""
    return AN_ONPEAK_HE_LO <= he <= AN_ONPEAK_HE_HI


def _an_block_of(he: int) -> str:
    return AN_BLOCK_ONPEAK if _an_is_onpeak_he(he) else AN_BLOCK_OFFPEAK


def _an_iso_d(d) -> "str | None":
    """A market_date -> ISO date string. psycopg may hand back date or datetime."""
    if d is None:
        return None
    return (d.date() if isinstance(d, _datetime) else d).isoformat()


def _an_as_date(d):
    """Normalize a market_date to a plain `date` for arithmetic."""
    if d is None:
        return None
    return d.date() if isinstance(d, _datetime) else d


# ── Hub pairing (rule v0) ───────────────────────────────────────────────────

def _an_pair_hub(th_hub, in_universe: bool = True) -> dict:
    """CAISO's published th_hub -> this lane's pairing verdict.

    Four honest outcomes, never a guess:
      paired + priceable   -> a hub label with a banked price series
      paired + unpriceable -> CAISO pairs it, the pantry has no series
      unpaired             -> CAISO attributes no trading hub to this node
      not_in_universe      -> the node has no row in atlas_pnode_universe at
                              all, so CAISO has said NOTHING about its hub

    The fourth is why `in_universe` exists. A NULL th_hub and an absent row
    both arrive here as a falsy th_hub, and they are different facts: the
    first is CAISO's answer (the common one - 21,821 of 24,098 nodes measured
    2026-08-01), the second is the absence of an answer. Collapsing them would
    put words in CAISO's mouth about a node it has never listed. Callers that
    have already proven the row exists (the node package and the daily lane
    both 400 on an unpriced id) leave the default in place and get the
    three-verdict behaviour they have always had.
    """
    if not in_universe:
        return {"th_hub": None, "hub": None, "hub_priceable": False,
                "pairing": "not_in_universe", "pairing_rule": "caiso_th_hub_v0"}
    if not th_hub:
        return {"th_hub": None, "hub": None, "hub_priceable": False,
                "pairing": "unpaired", "pairing_rule": "caiso_th_hub_v0"}
    zone = AN_TH_HUB_TO_ZONE.get(th_hub)
    if zone:
        return {"th_hub": th_hub, "hub": zone, "hub_priceable": True,
                "pairing": "paired", "pairing_rule": "caiso_th_hub_v0"}
    return {"th_hub": th_hub, "hub": None, "hub_priceable": False,
            "pairing": "paired_unpriceable", "pairing_rule": "caiso_th_hub_v0"}


# ── Depth (the honesty fields every response carries) ───────────────────────

def _an_depth(rows) -> dict:
    """Shape the per-market depth probe into the response's `depth` object.

    `rows` are (market, min_date, max_date) — one per market probed. days is
    inclusive of both ends (8 dates spanning 07-22..07-29 reports 8), and
    `complete_days` is deliberately NOT guessed here: partial edge dates are
    visible in the per-HE `n` counts, which is where they matter.
    """
    per_market = {}
    spans = []
    for r in rows:
        mn, mx = _an_as_date(r["min_date"]), _an_as_date(r["max_date"])
        days = ((mx - mn).days + 1) if (mn and mx) else 0
        per_market[r["market"]] = {
            "min_date": mn.isoformat() if mn else None,
            "max_date": mx.isoformat() if mx else None,
            "days": days,
        }
        if days:
            spans.append(days)
    return {
        "source": "atlas_pnode_lmp_snapshot",
        "retention": "hot tier, ~7-day rolling; dispatch-only writer, no standing schedule",
        "markets": per_market,
        # The window every windowed stat downstream actually rides: the
        # narrower of the two settlement legs.
        "depth_days": min(spans) if spans else 0,
    }


_AN_DEPTH_SQL = """
    SELECT m.market,
           (SELECT min(s.market_date) FROM atlas_pnode_lmp_snapshot s
             WHERE s.market = m.market AND s.market_date >= %(sentinel)s) AS min_date,
           (SELECT max(s.market_date) FROM atlas_pnode_lmp_snapshot s
             WHERE s.market = m.market AND s.market_date >= %(sentinel)s) AS max_date
    FROM (VALUES (%(da)s), (%(rt)s)) AS m(market)
"""


def _an_depth_params() -> dict:
    return {"sentinel": AN_SENTINEL_MIN_DATE, "da": AN_DA_MARKET, "rt": AN_RT_MARKET}


# ── The DART leg ────────────────────────────────────────────────────────────

def _an_rt_hourly(rt_rows) -> dict:
    """RTPD interval rows -> {(date, he): (mean_lmp, interval_count)}.

    The hourly FMM price is the straight mean of the intervals PRESENT in that
    hour. Ragged hours (a missed dispatch leaves 2 or 3 intervals instead of 4)
    are averaged over what exists and the count travels with the value so the
    thinness is visible rather than assumed away.
    """
    acc: dict = {}
    for r in rt_rows:
        v = _an_f(r["lmp"])
        if v is None:
            continue
        key = (_an_as_date(r["market_date"]), int(r["market_hour"]))
        bucket = acc.setdefault(key, [])
        bucket.append(v)
    return {k: (sum(v) / len(v), len(v)) for k, v in acc.items()}


def _an_dart_series(da_rows, rt_rows) -> list:
    """The node's DART series: one entry per (date, HE) carrying BOTH legs.

    DART = FMM − DA (positive = RT above DA). An hour with only one leg is
    SKIPPED, never half-computed. Ascending by (date, HE).
    """
    rt = _an_rt_hourly(rt_rows)
    out = []
    for r in da_rows:
        da = _an_f(r["lmp"])
        if da is None:
            continue
        key = (_an_as_date(r["market_date"]), int(r["market_hour"]))
        hit = rt.get(key)
        if hit is None:
            continue
        rt_mean, ivs = hit
        out.append({
            "date": key[0], "he": key[1],
            "da": da, "rt": rt_mean, "dart": rt_mean - da,
            "rt_intervals": ivs,
        })
    out.sort(key=lambda e: (e["date"], e["he"]))
    return out


def _an_build_dart(da_rows, rt_rows) -> dict:
    """The `dart` stanza: per-HE mean + envelope over banked depth, plus the
    latest banked day's realized DART per HE placed against those bands.

    The envelope spans EVERY banked day including the latest — "over banked
    depth" means exactly that. With 6-8 samples per HE the bands are a small-N
    description, not a climatology; each band states its own n so the caption
    can say so.
    """
    series = _an_dart_series(da_rows, rt_rows)
    if not series:
        return {
            "convention": "dart = fmm(rtpd) - da; positive = rt above da",
            "per_he": [], "latest_day": None, "n_days": 0, "n_hours": 0,
            "window": None,
            "stance": {"available": False,
                       "reason": "no banked hour carries both a DA and an FMM leg"},
        }

    by_he: dict = defaultdict(list)
    for e in series:
        by_he[e["he"]].append(e["dart"])

    per_he = []
    for he in sorted(by_he):
        env = _an_envelope(by_he[he])
        per_he.append({"he": he, "mean": env["mean"], "n": env["n"],
                       "envelope": {k: env[k] for k, _q in AN_ENVELOPE_QUANTILES}})

    env_by_he = {row["he"]: row["envelope"] for row in per_he}
    latest_date = max(e["date"] for e in series)
    latest_hours = [e for e in series if e["date"] == latest_date]
    latest_hours.sort(key=lambda e: e["he"])

    dates = sorted({e["date"] for e in series})
    latest_rows = [
        {
            "he": e["he"],
            "da": _an_r(e["da"]), "rt": _an_r(e["rt"]),
            "dart": _an_r(e["dart"]),
            "rt_intervals": e["rt_intervals"],
            "band": _an_band_of(e["dart"], env_by_he.get(e["he"])),
        }
        for e in latest_hours
    ]
    return {
        "convention": "dart = fmm(rtpd) - da; positive = rt above da",
        "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat()},
        "n_days": len(dates),
        "n_hours": len(series),
        "per_he": per_he,
        "latest_day": {
            "date": latest_date.isoformat(),
            "hours": latest_rows,
        },
        # The counted verdict over the latest day's classes — the same tally the
        # dashboard's computeDartStance performs, served so every consumer reads
        # one truth instead of each deriving its own.
        "stance": _an_stance(
            [r["band"] for r in latest_rows],
            "no hour on the latest banked day carries a class"),
    }


# ── The basis leg (cross-store join, UTC<->PT handled explicitly) ────────────

def _an_hub_map(hub_rows) -> dict:
    """Hub DA rows -> {(pt_date, he): value}.

    The SQL does the timezone work (`ts AT TIME ZONE 'America/Los_Angeles'`,
    HE = local hour + 1) because ts is the interval START in UTC and the
    snapshot's market_hour is PT hour-ENDING. Verified against real rows:
    ts 2026-07-23T07:00Z -> PT 2026-07-23 00:00 -> HE1. This is the whole
    UTC<->PT seam; it lives in one place on purpose.
    """
    out = {}
    for r in hub_rows:
        v = _an_f(r["value"])
        if v is None:
            continue
        out[(_an_as_date(r["d"]), int(r["he"]))] = v
    return out


def _an_basis_series(da_rows, hub_rows) -> list:
    """node DAM − hub DAM per (date, HE). An hour missing either leg is
    skipped, never fabricated."""
    hub = _an_hub_map(hub_rows)
    out = []
    for r in da_rows:
        node = _an_f(r["lmp"])
        if node is None:
            continue
        d, he = _an_as_date(r["market_date"]), int(r["market_hour"])
        hv = hub.get((d, he))
        if hv is None:
            continue
        out.append({"date": d, "he": he, "node": node, "hub": hv,
                    "basis": node - hv})
    out.sort(key=lambda e: (e["date"], e["he"]))
    return out


def _an_block_rollup(entries) -> dict:
    """Roll a set of (node, hub) hours into a block average.

    basis is reported as avg(node) − avg(hub) — the difference of block
    averages, which is what a variable-margin update actually wants. Because
    the join is balanced (every retained hour carries both legs) this equals
    avg(node − hub); the suite pins that identity so the two can never drift.
    """
    if not entries:
        return None
    node = _an_mean([e["node"] for e in entries])
    hub = _an_mean([e["hub"] for e in entries])
    return {
        "node": _an_r(node), "hub": _an_r(hub), "basis": _an_r(node - hub),
        "n_hours": len(entries),
        "n_days": len({e["date"] for e in entries}),
    }


def _an_month_key(d) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _an_month_complete(month: str, first_d, last_d) -> bool:
    """True only when banked depth covers that calendar month end-to-end."""
    y, m = (int(x) for x in month.split("-"))
    start = _date(y, m, 1)
    end = _date(y + (m == 12), (m % 12) + 1, 1) - _timedelta(days=1)
    return first_d <= start and last_d >= end


def _an_build_basis(da_rows, hub_rows, pairing) -> dict:
    """The `basis` stanza: per-HE means, the 7x16 / off-peak block cuts, and
    the calendar-month cuts (MTD + any prior full months within depth).

    When the node has no priceable CAISO hub this is an ABSENCE — `available:
    false` plus the reason — not an empty-but-present panel and never zeros.
    """
    if not pairing["hub_priceable"]:
        reason = ("node has no CAISO trading hub in atlas_pnode_universe.th_hub"
                  if pairing["pairing"] == "unpaired"
                  else f"paired to {pairing['th_hub']}, which has no banked hub price series")
        return {
            "available": False,
            "reason": reason,
            "hub": None, "pairing": pairing["pairing"],
            "per_he": [], "blocks": {}, "months": [],
            "stance": {"available": False, "reason": reason},
        }

    series = _an_basis_series(da_rows, hub_rows)
    if not series:
        reason = "no banked hour carries both a node DAM price and a hub DAM price"
        return {
            "available": False,
            "reason": reason,
            "hub": pairing["hub"], "pairing": pairing["pairing"],
            "per_he": [], "blocks": {}, "months": [],
            "stance": {"available": False, "reason": reason},
        }

    first_d = min(e["date"] for e in series)
    last_d = max(e["date"] for e in series)

    by_he: dict = defaultdict(list)
    for e in series:
        by_he[e["he"]].append(e)
    per_he = []
    for he in sorted(by_he):
        entries = by_he[he]
        roll = _an_block_rollup(entries)
        # SERVER-SIDE BASIS CLASSING. Each hour's latest banked basis, classed
        # against THAT HOUR'S OWN banked basis history, in the department's one
        # class vocabulary. This is what lights the dashboard's basis stance —
        # it has shipped as a gated null-returning detector precisely because
        # these two fields did not exist.
        per_he.append({"he": he, "block": _an_block_of(he), **roll,
                       **_an_classing(entries, "basis")})

    blocks = {}
    for label in (AN_BLOCK_ONPEAK, AN_BLOCK_OFFPEAK):
        roll = _an_block_rollup([e for e in series if _an_block_of(e["he"]) == label])
        if roll:
            blocks[label] = roll

    by_month: dict = defaultdict(list)
    for e in series:
        by_month[_an_month_key(e["date"])].append(e)
    months = []
    for month in sorted(by_month):
        entries = by_month[month]
        row = {"month": month,
               "complete": _an_month_complete(month, first_d, last_d),
               "first_date": min(e["date"] for e in entries).isoformat(),
               "last_date": max(e["date"] for e in entries).isoformat()}
        for label in (AN_BLOCK_ONPEAK, AN_BLOCK_OFFPEAK):
            row[label] = _an_block_rollup(
                [e for e in entries if _an_block_of(e["he"]) == label])
        months.append(row)

    return {
        "available": True,
        "hub": pairing["hub"],
        "pairing": pairing["pairing"],
        "definition": {
            "basis": "node DAM − hub DAM, per HE",
            AN_BLOCK_ONPEAK: f"HE{AN_ONPEAK_HE_LO}-HE{AN_ONPEAK_HE_HI}, all days of the week",
            AN_BLOCK_OFFPEAK: f"HE1-HE{AN_ONPEAK_HE_LO - 1} and HE{AN_ONPEAK_HE_HI + 1}-HE24, all days",
            "class": ("each hour's latest banked basis, classed against that hour's "
                      "own banked basis history; same six class names the dart rows "
                      "carry as `band`"),
            "context": (f"the words a surface may print for that class at this depth; "
                        f"percentile vocabulary is earned at n>={AN_PERCENTILE_FLOOR}"),
        },
        "window": {"start": first_d.isoformat(), "end": last_d.isoformat()},
        "per_he": per_he,
        "blocks": blocks,
        "months": months,
        "stance": _an_stance(
            [r["class"] for r in per_he],
            "no hour carries a basis class"),
    }


# ── Components ──────────────────────────────────────────────────────────────

AN_COMPONENT_COLS = ("lmp", "energy", "congestion", "loss", "ghg")


def _an_build_components(da_rows, rt_rows) -> dict:
    """Window averages of the decomposition the atlas already banks, per
    market. Each component averages over the rows where IT is non-null (a null
    ghg must not shrink the congestion sample), so every figure carries its own
    n."""
    out = {}
    for market, rows in ((AN_DA_MARKET, da_rows), (AN_RT_MARKET, rt_rows)):
        stanza = {}
        for col in AN_COMPONENT_COLS:
            vals = [_an_f(r[col]) for r in rows]
            vals = [v for v in vals if v is not None]
            stanza[col] = {"mean": _an_r(_an_mean(vals)), "n": len(vals)}
        stanza["rows"] = len(rows)
        out[market] = stanza
    return out


# ── The ledger (the table an asset manager lifts) ───────────────────────────

def _an_build_ledger(basis) -> dict:
    """The monthly table: month, node 7x16 avg, hub 7x16 avg, basis, N per row.

    Derived from the basis stanza so the ledger can NEVER disagree with the
    panel above it. Absent when there is no priceable hub. With today's 8 days
    of depth this is one row for 2026-07 with complete=false — the row states
    its own N and completeness rather than reading like a settled month.
    """
    if not basis.get("available"):
        return {"available": False, "reason": basis.get("reason"), "rows": []}
    rows = []
    for m in basis["months"]:
        cut = m.get(AN_BLOCK_ONPEAK)
        if not cut:
            continue
        rows.append({
            "month": m["month"],
            "complete": m["complete"],
            "node_7x16": cut["node"],
            "hub_7x16": cut["hub"],
            "basis_7x16": cut["basis"],
            "n_hours": cut["n_hours"],
            "n_days": cut["n_days"],
        })
    return {
        "available": True,
        "hub": basis["hub"],
        "block": f"{AN_BLOCK_ONPEAK} (HE{AN_ONPEAK_HE_LO}-HE{AN_ONPEAK_HE_HI}, all days)",
        "rows": rows,
    }


# ── SQL: the node package's four reads (all index-served, shapes stated) ─────

# 1. Existence + identity. The universe row carries th_hub (the pairing) and
#    the geo table carries coordinates; node_type/area come from the PRICED
#    universe so identity never describes a node this lane cannot price.
#    One pnode off three PK/unique indexes — milliseconds.
#    Exactly ONE row always comes back (the anchor VALUES row), so "unknown
#    node" is a field on a present row rather than an empty result — the route
#    can tell "no such id" from "DB gave me nothing". atlas_pnode_universe and
#    atlas_pnode_geo_corrected are both unique on pnode_id (verified: 14,417
#    rows / 14,417 distinct), so neither LEFT JOIN can fan the row out; the
#    crosswalk CAN have several generators per node, so its name is collapsed
#    inside a LATERAL aggregate rather than joined directly.
_AN_IDENTITY_SQL = """
    SELECT
        t.pnode_id,
        (u.pnode_id IS NOT NULL) AS in_universe,
        (p.min_date IS NOT NULL) AS priced,
        u.th_hub, u.apnode_type,
        p.node_type, p.area, p.min_date, p.max_date,
        COALESCE(g.corrected_lat, g.latitude) AS latitude,
        COALESCE(g.corrected_lon, g.longitude) AS longitude,
        g.geocode_status,
        pl.plant_name
    FROM (VALUES (%(pnode_id)s::text)) AS t(pnode_id)
    LEFT JOIN atlas_pnode_universe u ON u.pnode_id = t.pnode_id
    LEFT JOIN atlas_pnode_geo_corrected g ON g.pnode_id = t.pnode_id
    LEFT JOIN LATERAL (
        SELECT s.node_type, s.area,
               min(s.market_date) AS min_date, max(s.market_date) AS max_date
        FROM atlas_pnode_lmp_snapshot s
        WHERE s.pnode_id = t.pnode_id
          AND s.market = %(da)s
          AND s.market_date >= %(sentinel)s
        GROUP BY s.node_type, s.area
        ORDER BY max(s.market_date) DESC
        LIMIT 1
    ) p ON true
    LEFT JOIN LATERAL (
        SELECT min(a.plant_name) AS plant_name
        FROM atlas_pnode_crosswalk x
        JOIN atlas_plants a ON a.plant_code = x.plant_code
        WHERE x.pnode_id = t.pnode_id
    ) pl ON true
"""

# 2/3. The two settlement legs for ONE node over full banked depth. Index Only
#      Scan / Index Scan on atlas_pnode_lmp_snapshot_pkey (pnode_id leads the
#      PK) — ~192 rows for DAM, ~700 for RTPD. Never a universe read.
_AN_NODE_LEG_SQL = """
    SELECT market_date, market_hour, market_interval,
           lmp, energy, congestion, loss, ghg
    FROM atlas_pnode_lmp_snapshot
    WHERE pnode_id = %(pnode_id)s
      AND market = %(market)s
      AND market_date >= %(sentinel)s
    ORDER BY market_date, market_hour, market_interval
"""

# 4. The hub leg — the cross-store half of basis. idx_tsv_series_ts covers
#    (dataset, series, ts DESC) so this is an index range scan over ~200 rows
#    despite living in the 43 GB table. The ts window is bounded HARD by the
#    node's own banked span (no open-ended read, ever) and the UTC<->PT
#    conversion happens right here, once.
_AN_HUB_LEG_SQL = """
    SELECT (ts AT TIME ZONE 'America/Los_Angeles')::date AS d,
           extract(hour FROM (ts AT TIME ZONE 'America/Los_Angeles'))::int + 1 AS he,
           value
    FROM timeseries_values
    WHERE dataset = %(dataset)s
      AND series = %(series)s
      AND ts >= %(ts_lo)s
      AND ts < %(ts_hi)s
    ORDER BY ts
"""


def _an_hub_ts_bounds(first_d, last_d) -> tuple:
    """PT date span -> the half-open UTC instant window covering those PT days.

    PT midnight of the first day through PT midnight after the last. Localized
    via ZoneInfo so the offset is the one actually in force (no hardcoded -7/-8).
    """
    lo = _datetime.combine(first_d, _time(0, 0), tzinfo=AN_PT)
    hi = _datetime.combine(last_d + _timedelta(days=1), _time(0, 0), tzinfo=AN_PT)
    return lo.astimezone(_timezone.utc), hi.astimezone(_timezone.utc)


# ── SQL: node search ────────────────────────────────────────────────────────
# The searchable universe is the PRICED one (the latest DAM instant, selected
# by the same index-driven LIMIT 1 the Cockpit lane uses) so a hit is always a
# node this department can actually render a package for.
#
# NAMES, honestly: atlas_pnode_universe.description is NOT a human name — it
# is the pnode_id (21,362 of 24,098 rows) or the id minus its -APND suffix. So
# it is not searched and not returned. The only real gazetteer is
# atlas_plants.plant_name via atlas_pnode_crosswalk, which covers 376 nodes.
# `name` is therefore the plant name when one is known and NULL otherwise —
# never the id dressed up as a name.
_AN_SEARCH_SQL = """
    WITH latest AS (
        SELECT market_date, market_hour, market_interval
        FROM atlas_pnode_lmp_snapshot
        WHERE market = %(da)s
          AND market_date >= %(sentinel)s
        ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
        LIMIT 1
    ),
    universe AS (
        SELECT s.pnode_id, s.node_type, s.area
        FROM atlas_pnode_lmp_snapshot s, latest l
        WHERE s.market = %(da)s
          AND s.market_date = l.market_date
          AND s.market_hour = l.market_hour
          AND s.market_interval IS NOT DISTINCT FROM l.market_interval
    ),
    plant_match AS (
        SELECT x.pnode_id,
               min(a.plant_name) FILTER (
                   WHERE a.plant_name ILIKE %(q)s ESCAPE '\\'
               ) AS matched_plant,
               min(a.plant_name) AS any_plant
        FROM atlas_pnode_crosswalk x
        JOIN atlas_plants a ON a.plant_code = x.plant_code
        GROUP BY x.pnode_id
    )
    SELECT u.pnode_id, u.node_type, u.area,
           COALESCE(pm.matched_plant, pm.any_plant) AS name,
           (pm.matched_plant IS NOT NULL) AS matched_via_name,
           un.th_hub,
           COALESCE(g.corrected_lat, g.latitude) AS latitude,
           COALESCE(g.corrected_lon, g.longitude) AS longitude
    FROM universe u
    LEFT JOIN plant_match pm ON pm.pnode_id = u.pnode_id
    LEFT JOIN atlas_pnode_universe un ON un.pnode_id = u.pnode_id
    LEFT JOIN atlas_pnode_geo_corrected g ON g.pnode_id = u.pnode_id
    WHERE u.pnode_id ILIKE %(q)s ESCAPE '\\'
       OR pm.matched_plant IS NOT NULL
    ORDER BY (u.pnode_id ILIKE %(prefix)s ESCAPE '\\') DESC, u.pnode_id
    LIMIT %(limit)s
"""


# ── SQL: movers ─────────────────────────────────────────────────────────────
# The interval grid, read off ONE reference node's PK-index slice (~644 rows,
# ~7 ms). Valid universe-wide because RTPD interval coverage is
# node-independent (see the header note + its verification).
_AN_GRID_SQL = """
    SELECT market_date AS d, market_hour AS he, count(*)::float8 AS k
    FROM atlas_pnode_lmp_snapshot
    WHERE pnode_id = %(pnode_id)s
      AND market = %(market)s
      AND market_date >= %(lo)s
      AND market_date <= %(hi)s
    GROUP BY 1, 2
    ORDER BY 1, 2
"""

# A reference node: any priced node will do (all 14,827 are present at every
# instant). Taken off the latest instant so it is guaranteed live.
_AN_REF_NODE_SQL = """
    WITH latest AS (
        SELECT market_date, market_hour, market_interval
        FROM atlas_pnode_lmp_snapshot
        WHERE market = %(market)s
          AND market_date >= %(sentinel)s
        ORDER BY market_date DESC, market_hour DESC, market_interval DESC NULLS LAST
        LIMIT 1
    )
    SELECT s.pnode_id
    FROM atlas_pnode_lmp_snapshot s, latest l
    WHERE s.market = %(market)s
      AND s.market_date = l.market_date
      AND s.market_hour = l.market_hour
      AND s.market_interval IS NOT DISTINCT FROM l.market_interval
    ORDER BY s.pnode_id
    LIMIT 1
"""

# THE PINNED MOVERS CHUNKS. There are TWO shapes because the two markets have
# different row densities per hour (RTPD ~59k, DAM ~15k) and the planner treats
# them differently. Using one shape for both costs an order of magnitude —
# measured, not guessed.
#
# RTPD — 6-hour chunks, inline VALUES (he, 1/k) grid. Both halves of the
# predicate are load-bearing:
#   * the VALUES grid on the outer side of the nested loop -> ONE bitmap index
#     scan per hour (the plan we want),
#   * `market_hour BETWEEN` -> keeps the row estimate under the ~5% cliff so
#     the planner does not flip the whole thing to a parallel seq scan.
# Measured: 356k rows, Bitmap Index Scan on idx_lmp_snap_market_time,
# HashAggregate on pnode_id with NO sort and NO spill (the 1/k weight is
# applied inline precisely to avoid grouping by hour). 226-416 ms warm,
# ~1.6 s cold. Widening to a full day flips it to a seq scan (1.8 s+).
_AN_MOVERS_RT_CHUNK_SQL = """
    SELECT s.pnode_id,
           sum(s.lmp::float8 / g.k) AS weighted_sum,
           count(*) AS n_rows
    FROM (VALUES {values}) AS g(he, k)
    JOIN atlas_pnode_lmp_snapshot s
      ON s.market_hour = g.he
    WHERE s.market = %(market)s
      AND s.market_date = %(d)s
      AND s.market_hour BETWEEN %(he_lo)s AND %(he_hi)s
      AND s.lmp IS NOT NULL
    GROUP BY s.pnode_id
"""

# DAM — ONE chunk per date, hours as an ANY(array). DAM is hourly so every
# weight is 1 and no grid is needed. Do NOT reuse the RTPD shape here: the
# VALUES-grid nested loop degrades to a plain Index Scan on the sparser DAM
# rows (measured 2,430 ms for 89k rows), whereas this shape plans as a Parallel
# Bitmap Heap Scan and does a whole 24-hour day — 356k rows — in 1,254 ms cold.
# That is ~4x cheaper per row for 4x the coverage.
_AN_MOVERS_DA_CHUNK_SQL = """
    SELECT pnode_id,
           sum(lmp::float8) AS weighted_sum,
           count(*) AS n_rows
    FROM atlas_pnode_lmp_snapshot
    WHERE market = %(market)s
      AND market_date = %(d)s
      AND market_hour = ANY(%(hours)s)
      AND lmp IS NOT NULL
    GROUP BY pnode_id
"""

# The node -> hub pairing map for the whole priced universe, for basis movers.
# 24k-row table, PK-ordered read; the priced-universe intersection happens in
# Python against the chunk results (a pnode_id IN (...) list of 1,579 ids would
# wreck the chunk plan above).
_AN_PAIRING_MAP_SQL = """
    SELECT pnode_id, th_hub
    FROM atlas_pnode_universe
    WHERE th_hub = ANY(%(hubs)s)
"""


def _an_chunk_plan(dates, grid_by_date, chunk_hours) -> list:
    """Split a window into index-servable (date, hours) chunks.

    `chunk_hours=None` means one chunk per date (the DAM shape); an int caps a
    chunk at that many hours (the RTPD shape). Each chunk carries the (he, k)
    pairs it needs, so the SQL reads exactly the hours that are banked — never
    an assumed 24-hour day. Hours absent from the grid are in no chunk at all.
    """
    chunks = []
    for d in dates:
        hours = sorted(grid_by_date.get(d, {}))
        if not hours:
            continue
        step = chunk_hours or len(hours)
        for i in range(0, len(hours), step):
            slice_ = hours[i:i + step]
            chunks.append({
                "d": d,
                "he_lo": slice_[0],
                "he_hi": slice_[-1],
                "hours": list(slice_),
                "pairs": [(he, grid_by_date[d][he]) for he in slice_],
            })
    return chunks


def _an_movers_rt_sql(pairs) -> str:
    """Render the RTPD chunk SQL with its inline VALUES (he, 1/k) grid.

    The literals are inlined rather than bound because the planner has to SEE
    the tiny relation's size to choose the nested-loop bitmap plan; a bound
    array would leave it guessing. They are ints and floats derived from DB
    output, never from user input, and are coerced here so that stays true.
    """
    rows = [f"({int(he)}, {float(k)}::float8)" for he, k in pairs]
    return _AN_MOVERS_RT_CHUNK_SQL.format(values=", ".join(rows))


def _an_movers_rank(per_node: dict, top_n: int) -> dict:
    """Rank a {pnode_id: value} map into top-N both directions.

    `up` is most-positive-first, `down` is most-negative-first. Ties break on
    pnode_id so the screen is deterministic between requests. Nodes whose value
    could not be computed are absent, never zero-filled — a zero here would
    read as "no spread" instead of "no data".
    """
    scored = sorted(
        ((pid, v) for pid, v in per_node.items() if v is not None),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return {
        "up": [{"pnode_id": p, "value": _an_r(v)} for p, v in scored[:top_n]],
        "down": [{"pnode_id": p, "value": _an_r(v)}
                 for p, v in sorted(scored, key=lambda kv: (kv[1], kv[0]))[:top_n]],
        "ranked_nodes": len(scored),
    }


# ── Reads ───────────────────────────────────────────────────────────────────

async def _an_read(sql: str, params: dict) -> list:
    """One pooled read with the lane's retry-once-on-dead-connection behavior.
    Delegates to the Cockpit reader so there is ONE such helper in this file."""
    return await _cockpit_read(sql, params)


async def _an_read_depth() -> dict:
    return _an_depth(await _an_read(_AN_DEPTH_SQL, _an_depth_params()))


# ═══════════════════════════════════════════════════════════════════════════
# 1. GET /api/analytics/node-search
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/analytics/node-search")
async def analytics_node_search(
    q: str = Query(
        default="",
        description=(
            "Case-insensitive substring matched against pnode_id OR a plant "
            "name from the Atlas crosswalk. Empty browses the universe by "
            "pnode_id. LIKE metacharacters match literally. Prefix matches "
            "sort first."
        ),
    ),
    limit: int = Query(
        default=AN_SEARCH_MAX,
        description=f"Max matches, 1..{AN_SEARCH_MAX}. Results are always bounded.",
    ),
):
    """
    Find a node to open the package on — the search box the department leads with.

    Searches the PRICED universe (the latest DAM instant), so every hit is a
    node `/api/analytics/node/{pnode_id}` can actually render.

    `name` is the Atlas plant name when one is known and NULL otherwise. It is
    never the pnode_id restyled as a name: `atlas_pnode_universe.description`
    holds the id itself for 21,362 of 24,098 rows, so it is not a gazetteer and
    is not consulted. Only 376 priced nodes carry a plant name today — that is
    the honest coverage, and the client should render the id as the primary
    label with `name` as a subtitle when present.

    `hub` is the paired CAISO trading hub under pairing rule v0
    (atlas_pnode_universe.th_hub, verbatim — no geographic inference). It is
    null for ~89% of the universe, which is correct: CAISO attributes no
    trading hub to most load and non-CAISO WEIM nodes.

    Response:
        {
          "query": "Alamitos", "count": 3, "limit": 50,
          "as_of": "...", "depth": {...},
          "matches": [{"pnode_id": "ALAMT3G_7_B1", "name": "AES Alamitos LLC",
                       "matched_via_name": true, "node_type": "GEN",
                       "area": "CA", "latitude": 33.76, "longitude": -118.1,
                       "th_hub": "TH_SP15_GEN-APND", "hub": "SP15",
                       "hub_priceable": true, "pairing": "paired"}, ...]
        }

    No matches -> 200 with an empty list. DB unavailable -> 503.
    """
    assert _pool is not None

    if not isinstance(limit, int) or limit < 1 or limit > AN_SEARCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be an integer in 1..{AN_SEARCH_MAX}",
        )

    q_clean = q.strip()
    escaped = _cockpit_like_escape(q_clean)
    params = {
        "da": AN_DA_MARKET,
        "sentinel": AN_SENTINEL_MIN_DATE,
        "q": f"%{escaped}%",
        "prefix": f"{escaped}%",
        "limit": limit,
    }

    try:
        depth = await _an_read_depth()
        rows = await _an_read(_AN_SEARCH_SQL, params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    matches = []
    for r in rows:
        pairing = _an_pair_hub(r["th_hub"])
        matches.append({
            "pnode_id": r["pnode_id"],
            "name": r["name"],
            "matched_via_name": bool(r["matched_via_name"]),
            "node_type": r["node_type"],
            "area": r["area"],
            "latitude": _an_f(r["latitude"]),
            "longitude": _an_f(r["longitude"]),
            "th_hub": pairing["th_hub"],
            "hub": pairing["hub"],
            "hub_priceable": pairing["hub_priceable"],
            "pairing": pairing["pairing"],
        })

    return {
        "query": q_clean,
        "limit": limit,
        "count": len(matches),
        "as_of": _utcnow().isoformat(),
        "depth": depth,
        "matches": matches,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET /api/analytics/node/{pnode_id} — THE PACKAGE
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/analytics/node/{pnode_id}")
async def analytics_node_package(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. ALAMT3G_7_B1. Unknown/unpriced -> 400.",
    ),
):
    """
    The node analytics package: identity · DART envelope · basis · components · ledger.

    ALL arithmetic is over banked data — eight calendar dates as of 2026-07-30
    (see `depth`), so every distribution here rests on 6-8 samples per HE and
    says so in its own `n`. Nothing extrapolates.

    Stanzas:
      identity    name/type/coordinates + the paired TH hub under rule v0.
      dart        per-HE mean and the p5/p25/p50/p75/p95 envelope over banked
                  depth, plus the latest banked day's realized DART per HE with
                  the band it landed in. DART = FMM(RTPD) − DA, POSITIVE = RT
                  ABOVE DA. Note this is the opposite sign from the `spread`
                  field on /api/timeseries/caiso-hub-lmp — do not mix them.
      basis       node DAM − hub DAM per HE, with 7x16 (HE7-22, ALL days),
                  off-peak, and calendar-month cuts. ABSENT (available:false +
                  reason) when the node has no priceable CAISO hub — which is
                  the common case for load and non-CAISO nodes.
      components  energy/congestion/loss/ghg window means per market, each with
                  its own n.
      ledger      the monthly 7x16 table, N stated per row, derived from the
                  basis stanza so the two can never disagree.

    Errors: blank/unknown/unpriced pnode_id -> 400; DB unavailable -> 503. A
    known node with no banked rows -> 200 with honest empty stanzas.
    """
    assert _pool is not None

    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")

    ident_params = {"pnode_id": node, "da": AN_DA_MARKET,
                    "sentinel": AN_SENTINEL_MIN_DATE}
    try:
        depth = await _an_read_depth()
        ident_rows = await _an_read(_AN_IDENTITY_SQL, ident_params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    ident = ident_rows[0] if ident_rows else None
    if not ident or not ident["priced"]:
        # Conforms to /api/atlas/pnode-history: an id this lane cannot price is
        # a client input error, not an empty 200 pretending the node exists.
        raise HTTPException(
            status_code=400,
            detail=(f"unknown pnode_id '{node}': not present in the banked "
                    f"{AN_DA_MARKET} universe"),
        )

    pairing = _an_pair_hub(ident["th_hub"])

    try:
        da_rows = await _an_read(_AN_NODE_LEG_SQL, {
            "pnode_id": node, "market": AN_DA_MARKET,
            "sentinel": AN_SENTINEL_MIN_DATE})
        rt_rows = await _an_read(_AN_NODE_LEG_SQL, {
            "pnode_id": node, "market": AN_RT_MARKET,
            "sentinel": AN_SENTINEL_MIN_DATE})

        hub_rows: list = []
        if pairing["hub_priceable"] and da_rows:
            first_d = _an_as_date(da_rows[0]["market_date"])
            last_d = _an_as_date(da_rows[-1]["market_date"])
            ts_lo, ts_hi = _an_hub_ts_bounds(first_d, last_d)
            hub_rows = await _an_read(_AN_HUB_LEG_SQL, {
                "dataset": AN_HUB_DA_DATASET, "series": pairing["hub"],
                "ts_lo": ts_lo, "ts_hi": ts_hi})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    basis = _an_build_basis(da_rows, hub_rows, pairing)

    return {
        "pnode_id": node,
        "as_of": _utcnow().isoformat(),
        "depth": depth,
        "identity": {
            "pnode_id": node,
            "name": ident["plant_name"],
            "node_type": ident["node_type"],
            "apnode_type": ident["apnode_type"],
            "area": ident["area"],
            "latitude": _an_f(ident["latitude"]),
            "longitude": _an_f(ident["longitude"]),
            "geocode_status": ident["geocode_status"],
            "in_universe": bool(ident["in_universe"]),
            "banked": {"min_date": _an_iso_d(ident["min_date"]),
                       "max_date": _an_iso_d(ident["max_date"])},
            **{k: pairing[k] for k in
               ("th_hub", "hub", "hub_priceable", "pairing", "pairing_rule")},
        },
        "dart": _an_build_dart(da_rows, rt_rows),
        "basis": basis,
        "components": _an_build_components(da_rows, rt_rows),
        "ledger": _an_build_ledger(basis),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 3. GET /api/analytics/movers — the universe-wide screen
# ═══════════════════════════════════════════════════════════════════════════

async def _an_movers_grid(market: str, lo, hi, ref_node: str) -> dict:
    """{date: {he: k}} for one market over [lo, hi], from one reference node."""
    rows = await _an_read(_AN_GRID_SQL, {
        "pnode_id": ref_node, "market": market, "lo": lo, "hi": hi})
    grid: dict = defaultdict(dict)
    for r in rows:
        grid[_an_as_date(r["d"])][int(r["he"])] = float(r["k"])
    return grid


async def _an_movers_sum(market: str, chunks: list, weighted: bool) -> tuple:
    """Run the pinned chunk read over `chunks`, summing per node.

    `weighted=True` uses the RTPD shape (inline VALUES 1/k grid); False uses the
    DAM shape (per-date ANY(hours), unit weights). Both are index-served by
    construction — see the two SQL constants. Chunks run at bounded concurrency:
    enough to cut wall-clock, not enough to stampede a 5-connection pool on a
    2 CU compute.

    Returns (weighted_sum_by_node, row_count_by_node) so the caller can DROP
    nodes with incomplete coverage rather than silently averaging one node over
    a shorter window than its neighbours.
    """
    sums: dict = defaultdict(float)
    counts: dict = defaultdict(int)
    sem = asyncio.Semaphore(AN_MOVERS_CONCURRENCY)

    async def _one(chunk):
        if weighted:
            sql = _an_movers_rt_sql(chunk["pairs"])
            params = {"market": market, "d": chunk["d"],
                      "he_lo": chunk["he_lo"], "he_hi": chunk["he_hi"]}
        else:
            sql = _AN_MOVERS_DA_CHUNK_SQL
            params = {"market": market, "d": chunk["d"], "hours": chunk["hours"]}
        async with sem:
            return await _an_read(sql, params)

    for batch_rows in await asyncio.gather(*(_one(c) for c in chunks)):
        for r in batch_rows:
            sums[r["pnode_id"]] += float(r["weighted_sum"])
            counts[r["pnode_id"]] += int(r["n_rows"])
    return sums, counts


@app.get("/api/analytics/movers")
async def analytics_movers(
    response: Response,
    window: str = Query(
        default="1d",
        description=f"Look-back window. One of {', '.join(AN_MOVERS_WINDOWS)}.",
    ),
    metric: str = Query(
        default="dart",
        description=f"Ranking metric. One of {', '.join(AN_MOVERS_METRICS)}.",
    ),
    cb_only: bool = Query(
        default=False,
        description=(
            "Rank only convergence-bidding-eligible nodes, at the CURRENT "
            "vintage of caiso_cb_eligible_nodes. Default false — the public "
            "boards rank the whole priced universe, unchanged."
        ),
    ),
):
    """
    Top-50 movers in both directions, universe-wide, by DART or basis.

    `metric=dart`  mean over the window of (hourly-mean FMM − DA) per node,
                   positive = RT above DA. All 14,827 priced nodes rank.
    `metric=basis` mean over the window of (node DAM − hub DAM) per node. Only
                   the 1,579 nodes with a priceable CAISO hub can rank; the
                   count of nodes dropped for want of a pairing is reported in
                   `coverage`, never hidden.

    QUERY PLAN (stated, per post-115 board discipline). Every read is chunked
    so it stays on idx_lmp_snap_market_time; the two markets use DIFFERENT
    shapes because they have different row densities per hour and the planner
    treats them differently:

      RTPD  6-hour chunks, inline VALUES (hour, 1/k) grid + a redundant
            `market_hour BETWEEN` bound. Bitmap Index Scan, HashAggregate on
            pnode_id, no sort, no spill. 356k rows in 226-416 ms warm.
            4 chunks/day.
      DAM   one chunk per date, hours as ANY(array). Parallel Bitmap Heap Scan,
            356k rows in 1,254 ms cold. 1 chunk/day.

    So `metric=basis` is 1 read/day (DAM only) and `metric=dart` is 5/day —
    5 reads for 1d, 35 for 7d — run at concurrency AN_MOVERS_CONCURRENCY.

    MEASURED BUDGET (acceptance 4). Per-chunk server-side EXPLAIN (ANALYZE)
    execution times on the live compute, 2026-07-30. Cold means the chunk's heap
    pages were not resident; warm means a repeat read. Both are reported because
    a 0.25-2 CU autoscaling compute genuinely swings this much:

      chunk                     cold      warm
      RTPD 6h (356k rows)     ~1.3 s    0.23-0.42 s
      DAM  1 date (356k rows) ~1.25 s   ~0.3 s

      metric=dart  1d  (5 reads)   ~6.5 s cold / ~1.5 s warm sequential
                                   ~2.0 s cold / ~0.5 s warm at concurrency 4
      metric=dart  7d  (35 reads)  ~45 s  cold / ~11 s warm sequential
                                   ~12 s  cold / ~3 s  warm at concurrency 4
      metric=basis 7d  (7 reads)   ~9 s   cold / ~2 s  warm sequential
                                   ~2.5 s cold / ~0.6 s warm at concurrency 4

    These are summed server-side times, NOT an end-to-end wall clock: the lane
    was built where direct Postgres egress is blocked, so the endpoint could not
    be driven against the live pantry. `runtime_ms` in the payload is the real
    measurement and should be read off the first deploy.

    The honest read: 1d is interactive, and 7d dart is NOT on a cold cache. The
    5-minute cache below carries the repeat cost, but the FIRST 7d dart request
    after a dispatch will take double-digit seconds. The dashboard's window
    toggle should default to 1d, and if a cold 7d proves to be a problem in
    practice the fix is a warmed cache (a scheduled poke after each dispatch),
    not a wider chunk — every wider shape measured here is worse.

    What NOT to "simplify" it back to, all measured on this compute: widening
    an RTPD chunk to a full day flips it to a parallel seq scan (1.8 s and
    climbing); reusing the RTPD VALUES-grid shape for DAM degrades to a plain
    Index Scan (2,430 ms for a QUARTER of the rows); and the naive
    GROUP BY (pnode_id, date, hour) shape over a 7-day RTPD window seq-scans
    and spills 430 MB for 38 s. `runtime_ms` reports the measured cost of the
    request that actually computed the answer, so a regression is visible in
    the payload rather than only in a log.

    CB-ONLY (`cb_only=true`) narrows the RANKING UNIVERSE, not the reads. The
    sweep above is unchanged — same chunks, same plan, same cost — and the
    current-vintage eligible set (one small keyed read) is applied to the
    per-node statistic before the ranking. `cb_filter` echoes the parameter, the
    vintage date, the eligible-universe size and how many ranked nodes it
    dropped, so the board can state what it is showing. Default false leaves the
    public boards byte-for-byte as they were, and costs zero extra reads.

    Cached for 5 minutes per (metric, window, cb_only): the snapshot writer has
    no standing schedule, so the answer only changes when a dispatch lands.

    Errors: bad window/metric -> 400; DB unavailable -> 503.
    """
    assert _pool is not None

    if window not in AN_MOVERS_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"window must be one of {', '.join(AN_MOVERS_WINDOWS)}")
    if metric not in AN_MOVERS_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {', '.join(AN_MOVERS_METRICS)}")

    cache_key = (metric, window, cb_only)
    hit = _an_movers_cache.get(cache_key)
    now_mono = time.monotonic()
    if hit and (now_mono - hit[0]) < AN_MOVERS_CACHE_TTL:
        response.headers["Cache-Control"] = f"public, max-age={int(AN_MOVERS_CACHE_TTL)}"
        return {**hit[1], "cached": True}

    started = time.monotonic()
    try:
        depth = await _an_read_depth()
        markets = depth["markets"]
        da_max = markets.get(AN_DA_MARKET, {}).get("max_date")
        rt_max = markets.get(AN_RT_MARKET, {}).get("max_date")
        if not da_max or not rt_max:
            raise HTTPException(
                status_code=503,
                detail="no banked nodal data in the snapshot hot tier")

        # Anchor on the newest date BOTH legs carry (staleness by design — never
        # wall-clock now, which would blank a legitimately stale screen).
        end_d = min(_date.fromisoformat(da_max), _date.fromisoformat(rt_max))
        start_d = end_d - _timedelta(days=AN_MOVERS_WINDOWS[window] - 1)

        ref_rows = await _an_read(_AN_REF_NODE_SQL, {
            "market": AN_RT_MARKET, "sentinel": AN_SENTINEL_MIN_DATE})
        if not ref_rows:
            raise HTTPException(status_code=503,
                                detail="no priced node universe to screen")
        ref_node = ref_rows[0]["pnode_id"]

        rt_grid = await _an_movers_grid(AN_RT_MARKET, start_d, end_d, ref_node)
        da_grid = await _an_movers_grid(AN_DA_MARKET, start_d, end_d, ref_node)

        # MATCHED HOURS. An hour is comparable only when every leg the METRIC
        # needs published it. That set is metric-specific on purpose:
        #   dart  needs DA + FMM        -> DAM hours ∩ RTPD hours
        #   basis needs DA + hub DA     -> DAM hours ∩ hub hours (RTPD is
        #                                  irrelevant and must NOT narrow it)
        # Either way the set is node-independent (universe-wide dispatch), so it
        # is one scalar set shared by every ranked node — which is what makes
        # the weighted single-pass read valid.
        hub_map: dict = {}
        pair_rows: list = []
        if metric == "basis":
            ts_lo, ts_hi = _an_hub_ts_bounds(start_d, end_d)
            # One read per zone: `series` is the second column of
            # idx_tsv_series_ts, so a per-zone equality keeps the index in play
            # (a three-zone IN-list would not).
            for zone in sorted(set(AN_TH_HUB_TO_ZONE.values())):
                rows = await _an_read(_AN_HUB_LEG_SQL, {
                    "dataset": AN_HUB_DA_DATASET, "series": zone,
                    "ts_lo": ts_lo, "ts_hi": ts_hi})
                hub_map[zone] = _an_hub_map(rows)
            pair_rows = await _an_read(_AN_PAIRING_MAP_SQL, {
                "hubs": list(AN_TH_HUB_TO_ZONE)})
            # An hour counts only if EVERY zone banked it, so all three zones'
            # block averages span the identical hours and the screen ranks
            # NP15/SP15/ZP26 nodes on the same footing.
            other = set.intersection(*(set(m) for m in hub_map.values())) \
                if hub_map else set()
        else:
            other = {(d, he) for d, hours in rt_grid.items() for he in hours}

        matched: dict = {}
        for d, hours in da_grid.items():
            common = sorted(he for he in hours if (d, he) in other)
            if common:
                matched[d] = common
        total_hours = sum(len(v) for v in matched.values())
        if not total_hours:
            raise HTTPException(
                status_code=503,
                detail=("no hour in the window carries both a DA and an FMM leg"
                        if metric == "dart" else
                        "no hour in the window carries both a node DA and a hub DA leg"))

        # DA is hourly: one row per hour, so its weight is 1.
        da_pairs = {d: {he: 1.0 for he in hes} for d, hes in matched.items()}

        dates = sorted(matched)
        # DAM: one chunk per date, unit weights. RTPD: 6-hour chunks, 1/k grid.
        da_chunks = _an_chunk_plan(dates, da_pairs, None)
        da_sums, da_counts = await _an_movers_sum(
            AN_DA_MARKET, da_chunks, weighted=False)

        rt_pairs: dict = {}
        rt_sums, rt_counts = ({}, {})
        rt_chunks: list = []
        if metric == "dart":
            rt_pairs = {d: {he: rt_grid[d][he] for he in hes}
                        for d, hes in matched.items()}
            rt_chunks = _an_chunk_plan(dates, rt_pairs, AN_MOVERS_CHUNK_HOURS)
            rt_sums, rt_counts = await _an_movers_sum(
                AN_RT_MARKET, rt_chunks, weighted=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # ── Per-node statistic. A node must carry the FULL matched hour set on
    # every leg it needs; a short node is EXCLUDED and counted, not averaged
    # over a different window than its neighbours.
    expected_da = total_hours
    # Sum of the per-hour interval counts: the RTPD row count a node must carry
    # to have contributed to every matched hour. Zero when there is no RT leg.
    expected_rt = int(round(sum(
        rt_pairs[d][he] for d in rt_pairs for he in rt_pairs[d]))) if rt_pairs else 0
    per_node: dict = {}
    short = 0
    unpaired = 0

    if metric == "dart":
        # Iterate the UNION of both legs so a node missing entirely from one of
        # them is counted as excluded rather than vanishing from the tally.
        for pid in set(da_sums) | set(rt_sums):
            da_sum, rt_sum = da_sums.get(pid), rt_sums.get(pid)
            if da_sum is None or rt_sum is None:
                short += 1
                continue
            if da_counts.get(pid) != expected_da or rt_counts.get(pid) != expected_rt:
                short += 1
                continue
            per_node[pid] = (rt_sum - da_sum) / total_hours
    else:
        zone_of = {r["pnode_id"]: AN_TH_HUB_TO_ZONE[r["th_hub"]] for r in pair_rows}
        # The hub's own block average over the SAME matched hours.
        hub_mean: dict = {}
        for zone, m in hub_map.items():
            vals = [m[(d, he)] for d in matched for he in matched[d] if (d, he) in m]
            hub_mean[zone] = (sum(vals) / len(vals)) if len(vals) == total_hours else None
        for pid, da_sum in da_sums.items():
            zone = zone_of.get(pid)
            if zone is None:
                unpaired += 1
                continue
            if da_counts.get(pid) != expected_da:
                short += 1
                continue
            hm = hub_mean.get(zone)
            if hm is None:
                short += 1
                continue
            per_node[pid] = (da_sum / total_hours) - hm

    # ── CB-eligibility filter. Applied to the per-node statistic, AFTER the
    # sweep and BEFORE the ranking: the reads above are untouched, only the
    # ranking universe narrows. Off by default and then not even read.
    cb_vintage = None
    cb_universe = None
    cb_excluded = None
    if cb_only:
        try:
            cb_ids, cb_vintage = await _an_cb_eligible()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
        cb_universe = len(cb_ids)
        before = len(per_node)
        per_node = {p: v for p, v in per_node.items() if p in cb_ids}
        cb_excluded = before - len(per_node)

    ranked = _an_movers_rank(per_node, AN_MOVERS_TOP_N)
    runtime_ms = int((time.monotonic() - started) * 1000)

    payload = {
        "metric": metric,
        "window": window,
        "cb_filter": _an_cb_block(cb_only, cb_vintage, cb_universe, cb_excluded),
        "as_of": _utcnow().isoformat(),
        "depth": depth,
        "window_served": {"start": dates[0].isoformat(), "end": dates[-1].isoformat(),
                          "matched_hours": total_hours,
                          "matched_days": len(matched)},
        "definition": (
            "mean over matched hours of (hourly-mean FMM − DA); positive = RT above DA"
            if metric == "dart" else
            "mean over matched hours of (node DAM − hub DAM); positive = node above hub"
        ),
        "coverage": {
            "ranked_nodes": ranked["ranked_nodes"],
            "excluded_incomplete": short,
            "excluded_unpaired": unpaired,
            "pairing_rule": "caiso_th_hub_v0" if metric == "basis" else None,
        },
        "top_n": AN_MOVERS_TOP_N,
        "up": ranked["up"],
        "down": ranked["down"],
        "runtime_ms": runtime_ms,
        "query_plan": {
            "index": "idx_lmp_snap_market_time",
            "dam": "one chunk per date, market_hour = ANY(hours); parallel bitmap heap scan",
            "rtpd": (f"{AN_MOVERS_CHUNK_HOURS}-hour chunks, inline VALUES(he,1/k) grid + "
                     "redundant market_hour BETWEEN; bitmap index scan, "
                     "HashAggregate on pnode_id (no sort, no spill)"),
            "chunks": {"dam": len(da_chunks), "rtpd": len(rt_chunks)},
            "concurrency": AN_MOVERS_CONCURRENCY,
        },
        "cached": False,
    }
    _an_movers_cache[cache_key] = (time.monotonic(), payload)
    response.headers["Cache-Control"] = f"public, max-age={int(AN_MOVERS_CACHE_TTL)}"
    _analytics_log.info(
        "movers metric=%s window=%s cb_only=%s hours=%d ranked=%d short=%d "
        "unpaired=%d cb_excluded=%s runtime_ms=%d",
        metric, window, cb_only, total_hours, ranked["ranked_nodes"], short,
        unpaired, cb_excluded, runtime_ms)
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# THE REGIME PACKAGE v0 — ANALYTICS ROOM 1, SECOND COURSE
# concept capture 2026-07-30 (energylake-dashboard/docs/
# concept_capture_2026_07_30_regime_package.md). Sibling of the Node Analytics
# Package directly above, and it reads the SAME stores under the SAME axioms.
#
#   GET /api/analytics/node/{pnode_id}/ladder?metric=  the percentile ladder
#   GET /api/analytics/node/{pnode_id}/grid?metric=    the HE x date heat grid
#   GET /api/analytics/tb4?scope=&id=&window=          top-bottom-4 spread
#   GET /api/analytics/movers-grid?window=&metric=      top-N nodes x HE1-24
#
# plus, ABOVE, the server-side basis classing + stance verdicts folded into the
# existing package payload (`basis.per_he[].class/.context`, `dart.stance`,
# `basis.stance`) — see `_an_classing` / `_an_stance`.
#
# ── DEPTH GRAMMAR IS SERVER-SIDE LAW HERE, NOT A CLIENT COURTESY ────────────
# The ladder is the form that can lie loudest: nine percentile columns over
# seven samples is min/max wearing a costume, and a chart cannot tell. So the
# floor is enforced in the PAYLOAD, not in the caption:
#
#   n <  AN_PERCENTILE_FLOOR (15)  ->  the row serves min/mid/max and carries
#                                      NO p-keys AT ALL (absent, not nulled)
#                                      and NO chip key. vocabulary: "range".
#   n >= AN_PERCENTILE_FLOOR       ->  the full P01..P99 ladder + the regime
#                                      chip appear. vocabulary: "percentile".
#
# A nulled p95 is worse than an absent one: `payload.p95 ?? fallback` renders
# the fallback, but `"p95" in row` is a fact the client cannot misread. The
# floor is 15 because that is the dashboard's own PERCENTILE_FLOOR; the two
# constants are pinned equal by test so the room cannot end up refusing
# percentile words while the server prints them.
#
# AT TODAY'S DEPTH EVERY LADDER ROW IS A RANGE ROW. The hot tier holds eight
# dates (six complete), so n = 6..8 per hour and the full ladder is code that
# has not yet had data. That is the designed behaviour, not an unfinished
# branch — the ladder widens on its own as the pantry banks depth.
#
# ── PERCENTILE METHOD: ONE DEFINITION ACROSS ANALYTICS ──────────────────────
# Every quantile in this lane goes through `_an_percentile_cont` — the same
# function the envelope uses, which is Postgres' `percentile_cont` linear
# interpolation implemented explicitly. `_an_percentile_rank` (the regime
# chip's rank) is its exact INVERSE, and the suite pins the round trip
# `percentile_cont(rank(v)) == v` plus a column-by-column cross-check of the
# ladder against `_an_envelope` on identical rows. There is no second
# percentile definition in this department and there must never be one.
#
# ── THE REGIME CHIP ─────────────────────────────────────────────────────────
# The current value's rank within THAT HOUR'S OWN history — never cross-node,
# never cross-hour. Binned on the rank rounded to whole percent so the bin and
# the printed number can never contradict each other (a raw 0.748 would print
# "MID (P75)"): P>=75 HIGH, P<=25 LOW, else MID. Below the depth floor the chip
# is ABSENT — a rank against six samples is not a rank.
#
# ── QUERY SHAPES: TWO NEW ONES, BOTH MEASURED ───────────────────────────────
# The ladder, the heat grid and node-scope TB4 introduce NO new query shape.
# They ride `_AN_NODE_LEG_SQL` / `_AN_HUB_LEG_SQL` / `_AN_DEPTH_SQL` — the
# per-node index-only reads the Nodes lane already pinned — and do all their
# work in Python over ~192 DAM / ~700 RTPD rows. Only two shapes are new, both
# EXPLAIN (ANALYZE)'d against the live pantry on 2026-07-30:
#
#   _AN_TB4_HUB_SQL       hub TB4 over FULL depth (88,344 hourly rows, 2016-01
#                         -> 2026-07). Index Scan on idx_tsv_series_ts, ONE
#                         sort, sorted Aggregate -> 3,681 daily rows.
#                         148 ms warm / 189 ms part-cold.
#                         REJECTED ALTERNATIVE, measured: the obvious
#                         row_number() OVER (PARTITION BY day ORDER BY value)
#                         shape plans FOUR sorts (one per window frame) and
#                         costs 1,868 ms — 12.6x worse for the same answer, and
#                         it spills 2,944 kB to disk instead of 1,968 kB. The
#                         array_agg + slice shape is pinned for that reason.
#   _AN_NODE_HE_SQL       the movers-grid's per-node per-HE read, 50 pnodes.
#                         pnode_id leads the PK, so ANY(array) is a Bitmap
#                         Index Scan on atlas_pnode_lmp_snapshot_pkey feeding a
#                         single-batch HashAggregate — no sort, no spill.
#                         RTPD 7d: 31,350 rows in -> 8,000 out, 236 ms cold.
#                         DAM  7d:  8,000 rows in -> 8,000 out,  57 ms.
#
# THE MOVERS GRID DOES NOT RE-RANK. It calls `analytics_movers` in-process, so
# it shares that endpoint's pinned chunk plan AND its 5-minute cache, then
# reads per-HE cells for the 50 ranked nodes only. Ranking the universe per HE
# would be the 430 MB spill the Nodes lane measured and refused.
#
# THE 20-SECOND COLD PATH IS NOT FIXED HERE. A cold `movers-grid?window=7d&
# metric=dart` pays the movers 7d cold cost (~12 s at concurrency 4) before it
# reads a single cell. The durable answer is a PRECOMPUTED movers artifact
# written pantry-side (cron + catalog entry at birth); that is a named FOLLOW-
# ON lane and is deliberately NOT built here. This endpoint computes live and
# leans on the cache, and says so in `runtime_ms` / `cached`.
# ═══════════════════════════════════════════════════════════════════════════

# The ladder's columns, in monotone order. P05/P25/P50/P75/P95 are the same
# numbers the envelope serves under p5/p25/p50/p75/p95 (pinned by test); the
# ladder adds the tails the table form has room for.
AN_LADDER_QUANTILES = (("p01", 0.01), ("p05", 0.05), ("p10", 0.10),
                       ("p25", 0.25), ("p50", 0.50), ("p75", 0.75),
                       ("p90", 0.90), ("p95", 0.95), ("p99", 0.99))

AN_REGIME_METRICS = ("dart", "basis")

# Chip bins, on the rank ROUNDED to whole percent. See the header note.
AN_REGIME_HIGH_PCT = 75
AN_REGIME_LOW_PCT = 25
AN_REGIME_RULE = (f"current value's percentile rank within that hour's own banked "
                  f"history (percentile_cont inverse, rounded to whole percent): "
                  f"P>={AN_REGIME_HIGH_PCT} HIGH, P<={AN_REGIME_LOW_PCT} LOW, else MID")

AN_PERCENTILE_METHOD = ("percentile_cont linear interpolation between the two "
                        "closest ranks — the department's one percentile "
                        "definition, shared with the envelope")

AN_RANGE_DEFINITION = ("min/mid/max are the lowest, median and highest banked "
                       "daily value at this hour; percentile columns are "
                       "withheld below the depth floor, not nulled")

# Heat-grid colour caps. Stated in the payload; values are NEVER clipped.
AN_GRID_SCALE_CAP = {"dart": 15.0, "basis": 10.0}
AN_GRID_HOURS = tuple(range(1, 25))

# TB4.
AN_TB4_SCOPES = ("hub", "node")
AN_TB4_TOP_N = 4
# A day needs at least 2 x TOP_N priced hours or the top-4 and bottom-4 sets
# overlap and TB4 stops meaning anything.
AN_TB4_MIN_HOURS = 2 * AN_TB4_TOP_N
AN_TB4_WINDOWS = {"7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365,
                  "full": None}
AN_TB4_SHIFT_FRACTION = 0.25
AN_TB4_SHORT_AVG_DAYS = 7
AN_TB4_LONG_AVG_DAYS = 30
AN_TB4_CACHE_TTL = 300.0

# The movers grid: 25 each way (50 nodes read per request).
AN_MOVERS_GRID_TOP_N = 25
AN_MOVERS_GRID_CACHE_TTL = AN_MOVERS_CACHE_TTL

_an_tb4_cache: "dict[tuple, tuple[float, dict]]" = {}
_an_movers_grid_cache: "dict[tuple, tuple[float, dict]]" = {}


# ── The percentile rank (the exact inverse of _an_percentile_cont) ──────────

def _an_percentile_rank(sorted_vals, value) -> "float | None":
    """The q in [0,1] whose `_an_percentile_cont(sorted_vals, q)` IS `value`.

    Written as the literal inverse of the department's percentile function so
    the chip's rank and the ladder's columns cannot describe two different
    distributions. The suite pins the round trip on real and constructed rows.

    Three cases, and the tie case is the only one with a judgement call in it:
      * value strictly inside a gap -> invert the linear interpolation exactly.
      * value present               -> its own index / (n-1).
      * value present SEVERAL times -> the MIDPOINT of the tie run, so a value
        tied across k samples ranks in the middle of them rather than at the
        bottom (which is what taking the first index would do, and would report
        the maximum of an all-equal sample as P0).
    Clamped only STRICTLY outside the sample: a value below the minimum is P0
    and above the maximum is P100. A value EQUAL to an extreme falls through to
    the tie rule on purpose — clamping it first would report the maximum of an
    all-equal sample as P0, which is the one wrong answer this function must
    never give. `sorted_vals` MUST already be sorted ascending.
    """
    n = len(sorted_vals)
    if n == 0 or value is None:
        return None
    if n == 1:
        # One sample IS the distribution; it has no interior to rank against.
        return 0.5
    s = sorted_vals
    v = float(value)
    if v < s[0]:
        return 0.0
    if v > s[-1]:
        return 1.0
    lo = next(i for i in range(n) if s[i] >= v)      # first index at/above v
    hi = max(i for i in range(n) if s[i] <= v)       # last index at/below v
    if hi >= lo:                                     # v is present: lo..hi ties
        return ((lo + hi) / 2.0) / (n - 1)
    frac = (v - s[hi]) / (s[lo] - s[hi])             # lo == hi + 1
    return (hi + frac) / (n - 1)


def _an_regime_chip(sorted_vals, current) -> "dict | None":
    """The chip: where `current` sits in this hour's OWN history, as a number.

    None when there is nothing to rank against. The label carries the rank for
    every bin including MID — the rank IS the product ("where does today sit,
    stated as a number"), and a bare MID would throw away the one figure the
    reader came for.
    """
    rank = _an_percentile_rank(sorted_vals, current)
    if rank is None:
        return None
    pct = int(round(rank * 100))
    regime = ("HIGH" if pct >= AN_REGIME_HIGH_PCT
              else "LOW" if pct <= AN_REGIME_LOW_PCT else "MID")
    return {"regime": regime, "rank": pct, "label": f"{regime} (P{pct})",
            "rule": AN_REGIME_RULE}


# ── The ladder row ──────────────────────────────────────────────────────────

def _an_ladder_row(he: int, entries, value_key: str) -> dict:
    """One HE's ladder row. THE DEPTH FLOOR IS ENFORCED HERE, once.

    Below the floor the row carries min/mid/max and neither a p-key nor a chip
    key EXISTS on it. Above the floor the nine percentile columns and the chip
    appear. Both shapes always state `n`, `current` and `current_date`.

    `current` is the latest banked day THAT HOUR has — not the node's latest
    date. The banked frontier is ragged (a mid-dispatch day holds HE1..HE16),
    and pinning every hour to the global latest date would blank the evening
    ladder for most of every afternoon.
    """
    vals = sorted(float(e[value_key]) for e in entries)
    n = len(vals)
    latest = max(entries, key=lambda e: e["date"])
    current = float(latest[value_key])
    row = {
        "he": he,
        "n": n,
        "current": _an_r(current),
        "current_date": latest["date"].isoformat(),
    }
    if n < AN_PERCENTILE_FLOOR:
        row["vocabulary"] = "range"
        row["min"] = _an_r(vals[0])
        row["mid"] = _an_r(_an_percentile_cont(vals, 0.50))
        row["max"] = _an_r(vals[-1])
        return row
    row["vocabulary"] = "percentile"
    for name, q in AN_LADDER_QUANTILES:
        row[name] = _an_r(_an_percentile_cont(vals, q))
    row["chip"] = _an_regime_chip(vals, current)
    return row


def _an_by_he(series) -> dict:
    """A (date, he, value) series -> {he: [entries]}, hours ascending."""
    out: dict = defaultdict(list)
    for e in series:
        out[e["he"]].append(e)
    return out


def _an_metric_series(metric: str, da_rows, rt_rows, hub_rows) -> list:
    """The per-(date, HE) daily series the Regime forms all read."""
    if metric == "dart":
        return _an_dart_series(da_rows, rt_rows)
    return _an_basis_series(da_rows, hub_rows)


AN_METRIC_CONVENTION = {
    "dart": "dart = fmm(rtpd) - da; positive = rt above da",
    "basis": "basis = node DAM − hub DAM, per HE; positive = node above hub",
}


def _an_build_ladder(metric: str, series) -> dict:
    """The ladder stanza over an already-built daily series."""
    if not series:
        return {
            "available": False,
            "reason": ("no banked hour carries both a DA and an FMM leg"
                       if metric == "dart" else
                       "no banked hour carries both a node DAM price and a hub DAM price"),
            "per_he": [], "window": None, "n_days": 0,
        }
    key = "dart" if metric == "dart" else "basis"
    by_he = _an_by_he(series)
    per_he = [_an_ladder_row(he, by_he[he], key) for he in sorted(by_he)]
    dates = sorted({e["date"] for e in series})
    return {
        "available": True,
        "window": {"start": dates[0].isoformat(), "end": dates[-1].isoformat()},
        "n_days": len(dates),
        "n_hours": len(series),
        "per_he": per_he,
    }


# ── The heat grid ───────────────────────────────────────────────────────────

def _an_build_heat_grid(metric: str, series) -> dict:
    """HE1-24 rows x banked-date columns, with both margins.

    DATES ARE NEVER PADDED: `dates` lists exactly the days the pantry holds, so
    the grid widens on its own as depth banks and never invents a column. A
    cell with no banked hour is null — that is a hole in a real column, not an
    invented one — and every margin states the count it averaged.

    Rows are the full HE1-24 axis so the grid keeps its shape while the newest
    day fills in hour by hour; an hour with nothing banked is an all-null row
    with a null average and n=0, which reads as "not yet", not as zero.
    """
    key = "dart" if metric == "dart" else "basis"
    dates = sorted({e["date"] for e in series})
    cell: dict = {(e["date"], e["he"]): float(e[key]) for e in series}

    rows = []
    for he in AN_GRID_HOURS:
        vals = [cell.get((d, he)) for d in dates]
        present = [v for v in vals if v is not None]
        rows.append({
            "he": he,
            "cells": [_an_r(v) for v in vals],
            "row_avg": _an_r(_an_mean(present)),
            "n": len(present),
        })

    day_avg = []
    for d in dates:
        vals = [cell[(d, he)] for he in AN_GRID_HOURS if (d, he) in cell]
        day_avg.append({"date": d.isoformat(), "avg": _an_r(_an_mean(vals)),
                        "n": len(vals)})

    return {
        "available": bool(series),
        "dates": [d.isoformat() for d in dates],
        "hours": list(AN_GRID_HOURS),
        "rows": rows,
        "day_avg": day_avg,
        "n_dates": len(dates),
        "n_cells": len(cell),
        "window": ({"start": dates[0].isoformat(), "end": dates[-1].isoformat()}
                   if dates else None),
    }


# ── Shared per-node read (no new query shape — all four are the Nodes lane's) ─

async def _an_regime_reads(node: str, metric: str) -> tuple:
    """depth + identity + the legs one Regime form needs, for one node.

    Raises the lane's HTTPExceptions: 400 for an id this department cannot
    price (conforming to the package route), 503 for a DB that is not there.
    Returns (depth, ident, pairing, da_rows, rt_rows, hub_rows).
    """
    ident_params = {"pnode_id": node, "da": AN_DA_MARKET,
                    "sentinel": AN_SENTINEL_MIN_DATE}
    try:
        depth = await _an_read_depth()
        ident_rows = await _an_read(_AN_IDENTITY_SQL, ident_params)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    ident = ident_rows[0] if ident_rows else None
    if not ident or not ident["priced"]:
        raise HTTPException(
            status_code=400,
            detail=(f"unknown pnode_id '{node}': not present in the banked "
                    f"{AN_DA_MARKET} universe"))

    pairing = _an_pair_hub(ident["th_hub"])
    try:
        da_rows = await _an_read(_AN_NODE_LEG_SQL, {
            "pnode_id": node, "market": AN_DA_MARKET,
            "sentinel": AN_SENTINEL_MIN_DATE})
        rt_rows: list = []
        hub_rows: list = []
        if metric == "dart":
            rt_rows = await _an_read(_AN_NODE_LEG_SQL, {
                "pnode_id": node, "market": AN_RT_MARKET,
                "sentinel": AN_SENTINEL_MIN_DATE})
        elif pairing["hub_priceable"] and da_rows:
            first_d = _an_as_date(da_rows[0]["market_date"])
            last_d = _an_as_date(da_rows[-1]["market_date"])
            ts_lo, ts_hi = _an_hub_ts_bounds(first_d, last_d)
            hub_rows = await _an_read(_AN_HUB_LEG_SQL, {
                "dataset": AN_HUB_DA_DATASET, "series": pairing["hub"],
                "ts_lo": ts_lo, "ts_hi": ts_hi})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    return depth, ident, pairing, da_rows, rt_rows, hub_rows


def _an_regime_metric_guard(metric: str) -> None:
    if metric not in AN_REGIME_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {', '.join(AN_REGIME_METRICS)}")


def _an_unpaired_absence(pairing: dict) -> "dict | None":
    """The basis absence stanza, or None when the node IS priceable. ~89% of
    the universe takes this path — it is the common case, not an error."""
    if pairing["hub_priceable"]:
        return None
    return {
        "available": False,
        "reason": ("node has no CAISO trading hub in atlas_pnode_universe.th_hub"
                   if pairing["pairing"] == "unpaired"
                   else f"paired to {pairing['th_hub']}, which has no banked hub price series"),
        "hub": None,
        "pairing": pairing["pairing"],
        "pairing_rule": pairing["pairing_rule"],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 4. GET /api/analytics/node/{pnode_id}/ladder — THE PERCENTILE LADDER
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/analytics/node/{pnode_id}/ladder")
async def analytics_node_ladder(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. ALAMT3G_7_B1. Unknown/unpriced -> 400.",
    ),
    metric: str = Query(
        default="dart",
        description=f"One of {', '.join(AN_REGIME_METRICS)}.",
    ),
):
    """
    The percentile ladder — the envelope as a table, one row per hour-ending.

    Per HE, over the node's whole banked window: the P01..P99 columns, the
    CURRENT value (that hour's latest banked day), and the REGIME CHIP — where
    current sits inside that hour's OWN history, stated as a number.

    THE DEPTH FLOOR IS SERVED, NOT CAPTIONED. Below `depth_floor` complete
    samples a row carries `min`/`mid`/`max` and `vocabulary: "range"`, and the
    percentile keys and the chip DO NOT EXIST on that row — absent, never
    nulled, because a client can misread a null and cannot misread an absent
    key. At or above the floor the full ladder and the chip appear and
    `vocabulary` reads `"percentile"`.

    AT TODAY'S DEPTH EVERY ROW IS A RANGE ROW (the hot tier holds eight dates,
    so n = 6..8 per hour). The percentile branch is real, tested and waiting on
    the pantry — the ladder lights itself as depth banks, with no code change.

    Percentiles are `percentile_cont` linear interpolation — the department's
    ONE definition, shared with the envelope on
    `/api/analytics/node/{pnode_id}`. The chip's rank is that function's exact
    inverse. Both are pinned against the envelope's own math on identical rows.

    `metric=dart`  daily DART at each hour. DART = FMM(RTPD) − DA, POSITIVE =
                   RT ABOVE DA — the opposite sign from `spread` on
                   /api/timeseries/caiso-hub-lmp. Never mix them.
    `metric=basis` daily (node DAM − hub DAM) at each hour. Returns
                   `available: false` + a reason for the ~89% of nodes CAISO
                   attributes no trading hub to.

    Errors: unknown/unpriced pnode_id -> 400; bad metric -> 400; DB down -> 503.
    """
    assert _pool is not None

    _an_regime_metric_guard(metric)
    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")

    depth, ident, pairing, da_rows, rt_rows, hub_rows = \
        await _an_regime_reads(node, metric)

    base = {
        "pnode_id": node,
        "metric": metric,
        "as_of": _utcnow().isoformat(),
        "depth": depth,
        "convention": AN_METRIC_CONVENTION[metric],
        "percentile_method": AN_PERCENTILE_METHOD,
        "depth_floor": AN_PERCENTILE_FLOOR,
        "depth_floor_rule": (
            f"a row with fewer than {AN_PERCENTILE_FLOOR} banked samples serves "
            f"min/mid/max and carries no percentile columns and no regime chip"),
        "range_definition": AN_RANGE_DEFINITION,
        "chip_rule": AN_REGIME_RULE,
        "hub": pairing["hub"] if metric == "basis" else None,
    }

    absence = _an_unpaired_absence(pairing) if metric == "basis" else None
    if absence:
        return {**base, **absence, "per_he": [], "window": None, "n_days": 0}

    series = _an_metric_series(metric, da_rows, rt_rows, hub_rows)
    return {**base, **_an_build_ladder(metric, series)}


# ═══════════════════════════════════════════════════════════════════════════
# 5. GET /api/analytics/node/{pnode_id}/grid — THE HE x DATE HEAT GRID
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/api/analytics/node/{pnode_id}/grid")
async def analytics_node_grid(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. ALAMT3G_7_B1. Unknown/unpriced -> 400.",
    ),
    metric: str = Query(
        default="dart",
        description=f"One of {', '.join(AN_REGIME_METRICS)}.",
    ),
):
    """
    The heat grid — HE1-24 down, banked dates across, one day's value per cell.

    One glance at the node's lived regime. Row averages (per hour, across the
    window) and day averages (per date, across the hours) sit in the margins,
    each stating the count it averaged.

    NEVER PADDED. `dates` is exactly what the pantry holds — every banked date
    in the hot tier, no more — so the grid widens on its own as depth banks and
    never invents a column to square the matrix. A cell the pantry has no hour
    for is `null`: a hole in a real column, not an invented one. Rows are the
    full HE1-24 axis so the grid keeps its shape while today fills in hour by
    hour; an hour with nothing banked is an all-null row with n=0.

    `scale_cap` states the colour cap the room should render at (±$15 DART /
    ±$10 basis). VALUES ARE NEVER CLIPPED — the cap is a palette instruction,
    and a $1,019 scarcity hour arrives in the payload at $1,019.

    Errors: unknown/unpriced pnode_id -> 400; bad metric -> 400; DB down -> 503.
    """
    assert _pool is not None

    _an_regime_metric_guard(metric)
    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")

    depth, ident, pairing, da_rows, rt_rows, hub_rows = \
        await _an_regime_reads(node, metric)

    base = {
        "pnode_id": node,
        "metric": metric,
        "as_of": _utcnow().isoformat(),
        "depth": depth,
        "convention": AN_METRIC_CONVENTION[metric],
        "scale_cap": {
            "value": AN_GRID_SCALE_CAP[metric],
            "unit": "$/MWh",
            "applies_to": "cell colour only",
            "note": "values are never clipped; the cap saturates the palette",
        },
        "hub": pairing["hub"] if metric == "basis" else None,
    }

    absence = _an_unpaired_absence(pairing) if metric == "basis" else None
    if absence:
        return {**base, **absence, "dates": [], "hours": list(AN_GRID_HOURS),
                "rows": [], "day_avg": [], "n_dates": 0, "n_cells": 0,
                "window": None}

    series = _an_metric_series(metric, da_rows, rt_rows, hub_rows)
    return {**base, **_an_build_heat_grid(metric, series)}


# ═══════════════════════════════════════════════════════════════════════════
# 6. GET /api/analytics/tb4 — THE TOP-BOTTOM-4 SPREAD
# ═══════════════════════════════════════════════════════════════════════════
#
# TB4 = mean(top-4 HE LMPs) − mean(bottom-4 HE LMPs), per day, DAM. The
# battery-revenue environment in one number.
#
# A day needs at least AN_TB4_MIN_HOURS (8) priced hours or the two sets
# OVERLAP and the number stops meaning anything. Short days are DROPPED and
# COUNTED (`excluded_short_days`), never averaged over a smaller basket.

# THE PINNED HUB SHAPE. Measured 2026-07-30 on the live pantry (Neon 0.25-2 CU,
# pg17), full depth = 88,344 hourly rows per series, 2016-01 -> 2026-07:
#
#   Index Scan idx_tsv_series_ts -> ONE Sort (external merge, 1,968 kB)
#   -> sorted Aggregate (array_agg ORDER BY) -> 3,681 daily rows.
#   148 ms warm (all buffers hit) / 189 ms with 1,780 blocks read cold.
#
# WHAT NOT TO "SIMPLIFY" IT BACK TO, measured on the same rows: the obvious
# row_number() OVER (PARTITION BY day ORDER BY value DESC/ASC) + count(*) OVER
# shape needs FOUR sorts (one per window frame), spills 2,944 kB, materialises
# all 88,344 rows through three WindowAgg nodes and costs 1,868 ms — 12.6x the
# array_agg shape for an identical answer. The per-group sort inside array_agg
# is 24 elements wide and effectively free; the window frames' sorts are not.
_AN_TB4_HUB_SQL = """
    WITH day AS (
        SELECT (ts AT TIME ZONE 'America/Los_Angeles')::date AS d,
               array_agg(value::float8 ORDER BY value) AS vals
        FROM timeseries_values
        WHERE dataset = %(dataset)s
          AND series = %(series)s
          AND value IS NOT NULL
        GROUP BY 1
    )
    SELECT d.d,
           array_length(d.vals, 1) AS n_hours,
           (SELECT avg(x) FROM unnest(
                d.vals[array_length(d.vals, 1) - {top_n_1}
                       : array_length(d.vals, 1)]) AS x) AS top_mean,
           (SELECT avg(x) FROM unnest(d.vals[1:{top_n}]) AS x) AS bottom_mean
    FROM day AS d
    WHERE array_length(d.vals, 1) >= %(min_hours)s
    ORDER BY d.d
""".format(top_n=AN_TB4_TOP_N, top_n_1=AN_TB4_TOP_N - 1)


def _an_days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return (_date(y + (m == 12), (m % 12) + 1, 1) - _date(y, m, 1)).days


def _an_tb4_day(vals) -> "dict | None":
    """One day's TB4 from that day's hourly prices, or None when the day is too
    short for the top-4 and bottom-4 baskets to be disjoint."""
    s = sorted(float(v) for v in vals if v is not None)
    if len(s) < AN_TB4_MIN_HOURS:
        return None
    top_mean = _an_mean(s[-AN_TB4_TOP_N:])
    bottom_mean = _an_mean(s[:AN_TB4_TOP_N])
    return {"top_mean": top_mean, "bottom_mean": bottom_mean,
            "tb4": top_mean - bottom_mean, "hours": len(s)}


def _an_tb4_daily_from_hours(hours_by_date) -> tuple:
    """{date: [hourly lmp]} -> (daily TB4 rows ascending, short days dropped)."""
    daily, short = [], 0
    for d in sorted(hours_by_date):
        row = _an_tb4_day(hours_by_date[d])
        if row is None:
            short += 1
            continue
        daily.append({"date": d.isoformat(),
                      "tb4": _an_r(row["tb4"]),
                      "top4_mean": _an_r(row["top_mean"]),
                      "bottom4_mean": _an_r(row["bottom_mean"]),
                      "hours": row["hours"]})
    return daily, short


def _an_tb4_monthly(daily) -> list:
    """The long view: mean TB4 per calendar month. `complete` is true only when
    every day of that month produced a TB4 row — a month inside the span with a
    missing day is honestly incomplete."""
    by_month: dict = defaultdict(list)
    for r in daily:
        by_month[r["date"][:7]].append(r["tb4"])
    out = []
    for month in sorted(by_month):
        vals = by_month[month]
        out.append({"month": month, "tb4_mean": _an_r(_an_mean(vals)),
                    "n_days": len(vals),
                    "complete": len(vals) == _an_days_in_month(month)})
    return out


def _an_tb4_trailing(daily, k: int) -> dict:
    """Mean TB4 over the last k BANKED days. Honest N: when the pantry holds
    fewer than k days the average is over what exists and says so — it is never
    padded, and never refused for being thin."""
    tail = daily[-k:]
    vals = [r["tb4"] for r in tail]
    return {
        "value": _an_r(_an_mean(vals)),
        "n_days": len(vals),
        "requested_days": k,
        "complete": len(vals) == k,
        "start": tail[0]["date"] if tail else None,
        "end": tail[-1]["date"] if tail else None,
    }


def _an_tb4_shift(short_avg: dict, long_avg: dict) -> dict:
    """The regime-shift flag, with its threshold and its arithmetic ON the flag.

    Proposed threshold: |delta| > 25% of the 30d mean. It is a PROPOSAL, stated
    in the payload as a tunable number rather than buried in code, because the
    right fraction is a captain's call and the flag must be auditable without
    reading this file.

    A 30d mean of exactly zero makes any non-zero delta a shift (the threshold
    is 0). That is arithmetically correct and practically vanishing for a TB4,
    which is a spread between the day's top and bottom hours; it is called out
    here rather than special-cased into a lie.

    THE THIN-DEPTH TRAP, and why this returns an absence rather than a flag.
    When the pantry holds fewer than 30 days BOTH trailing windows resolve to
    the same days, the delta is necessarily 0.0000 and the flag is necessarily
    false. That is not "no regime shift" — it is "no comparison" — and it is
    exactly what the nodal hot tier produces today (seven banked days, both
    windows identical). Serving `flag: false` there would read as a finding, so
    the verdict is withheld with its reason instead.
    """
    sv, lv = short_avg["value"], long_avg["value"]
    definition = (f"|avg_{AN_TB4_SHORT_AVG_DAYS}d − avg_{AN_TB4_LONG_AVG_DAYS}d| "
                  f"> {AN_TB4_SHIFT_FRACTION} × |avg_{AN_TB4_LONG_AVG_DAYS}d|")
    if sv is None or lv is None:
        return {"available": False,
                "reason": "not enough banked TB4 days to average both windows",
                "threshold_definition": definition,
                "threshold_fraction": AN_TB4_SHIFT_FRACTION}
    if (short_avg["start"], short_avg["end"]) == (long_avg["start"], long_avg["end"]):
        return {
            "available": False,
            "reason": (f"both windows resolve to the same {short_avg['n_days']} "
                       f"banked days, so the comparison is not informative at "
                       f"this depth"),
            "threshold_definition": definition,
            "threshold_fraction": AN_TB4_SHIFT_FRACTION,
        }
    delta = sv - lv
    threshold = AN_TB4_SHIFT_FRACTION * abs(lv)
    flag = abs(delta) > threshold
    return {
        "available": True,
        "flag": flag,
        "delta": _an_r(delta),
        "threshold": _an_r(threshold),
        "threshold_fraction": AN_TB4_SHIFT_FRACTION,
        "threshold_definition": definition,
        "arithmetic": (f"|{sv:.4f} − {lv:.4f}| = {abs(delta):.4f} vs "
                       f"{AN_TB4_SHIFT_FRACTION} × |{lv:.4f}| = {threshold:.4f} "
                       f"→ {'regime shift' if flag else 'no regime shift'}"),
    }


def _an_tb4_payload(daily, short_days: int, window: str) -> dict:
    """Everything downstream of the daily series — identical for both scopes,
    which is what makes `scope=node` genuinely the same shape as `scope=hub`
    rather than a lookalike."""
    days = AN_TB4_WINDOWS[window]
    served = daily if days is None else daily[-days:]
    short_avg = _an_tb4_trailing(daily, AN_TB4_SHORT_AVG_DAYS)
    long_avg = _an_tb4_trailing(daily, AN_TB4_LONG_AVG_DAYS)
    return {
        "available": bool(daily),
        "window": window,
        "window_definition": ("the last N BANKED TB4 days, not N calendar days; "
                              "'full' serves every banked day"),
        "window_served": ({"start": served[0]["date"], "end": served[-1]["date"],
                           "days": len(served)} if served else None),
        "n_days_banked": len(daily),
        "excluded_short_days": short_days,
        "daily": served,
        # The long view is over FULL banked depth regardless of `window` — that
        # is the point of it.
        "monthly": _an_tb4_monthly(daily),
        f"avg_{AN_TB4_SHORT_AVG_DAYS}d": short_avg,
        f"avg_{AN_TB4_LONG_AVG_DAYS}d": long_avg,
        "regime_shift": _an_tb4_shift(short_avg, long_avg),
    }


def _an_tsv_depth(series: str, daily, short_days: int) -> dict:
    """The hub leg's depth object, in the department's ONE depth grammar
    (source / retention / markets / depth_days) so a client renders the same
    caption over the hub store as over the snapshot hot tier."""
    first = daily[0]["date"] if daily else None
    last = daily[-1]["date"] if daily else None
    days = ((_date.fromisoformat(last) - _date.fromisoformat(first)).days + 1
            if first and last else 0)
    return {
        "source": "timeseries_values",
        "retention": "full banked history; no retention window",
        "markets": {AN_DA_MARKET: {"min_date": first, "max_date": last,
                                   "days": days}},
        "depth_days": days,
        "dataset": AN_HUB_DA_DATASET,
        "series": series,
        "tb4_days": len(daily),
        "excluded_short_days": short_days,
    }


@app.get("/api/analytics/tb4")
async def analytics_tb4(
    response: Response,
    scope: str = Query(
        default="hub",
        description=f"One of {', '.join(AN_TB4_SCOPES)}.",
    ),
    id: str = Query(
        default="SP15",
        description=("hub scope: a CAISO trading hub "
                     f"({', '.join(sorted(set(AN_TH_HUB_TO_ZONE.values())))}). "
                     "node scope: a priced pnode_id."),
    ),
    window: str = Query(
        default="30d",
        description=f"One of {', '.join(AN_TB4_WINDOWS)}.",
    ),
):
    """
    TB4 — the top-bottom-4 spread, the battery-revenue environment in one number.

        TB4(day) = mean(top-4 HE LMPs) − mean(bottom-4 HE LMPs), DAM

    `scope=hub` rides the FULL banked archive: 88,344 hourly rows per zone,
    2016-01 → today, ~3,681 TB4 days. It serves the requested window's daily
    series PLUS a monthly-mean series over that whole depth — the long view a
    daily line cannot show. One index-served read, ~150-190 ms measured.

    `scope=node` rides the snapshot hot tier with honest N, in the IDENTICAL
    payload shape. Today that is eight banked dates, so the node's `monthly`
    series is one incomplete month and `avg_30d` is an average of ~7 days that
    states `n_days: 7, complete: false`. Nothing is padded to look longer.

    A day needs at least 8 priced hours or the top-4 and bottom-4 baskets
    overlap; short days are dropped and counted in `excluded_short_days`.

    `avg_7d` / `avg_30d` are over the last 7 / 30 BANKED TB4 days of full depth
    — the standing statistic, not a slice of `window`. `regime_shift` carries
    its flag, its threshold, the tunable fraction behind it, and the arithmetic
    that produced it, so the verdict can be audited off the payload alone.

    Errors: bad scope/window -> 400; unknown hub or unpriced pnode_id -> 400;
    DB unavailable -> 503. Cached 5 minutes per (scope, id, window).
    """
    assert _pool is not None

    if scope not in AN_TB4_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"scope must be one of {', '.join(AN_TB4_SCOPES)}")
    if window not in AN_TB4_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"window must be one of {', '.join(AN_TB4_WINDOWS)}")
    ident = (id or "").strip()
    if not ident:
        raise HTTPException(status_code=400, detail="id is required")

    cache_key = (scope, ident, window)
    hit = _an_tb4_cache.get(cache_key)
    if hit and (time.monotonic() - hit[0]) < AN_TB4_CACHE_TTL:
        response.headers["Cache-Control"] = f"public, max-age={int(AN_TB4_CACHE_TTL)}"
        return {**hit[1], "cached": True}

    started = time.monotonic()
    common = {
        "scope": scope,
        "id": ident,
        "market": AN_DA_MARKET,
        "as_of": _utcnow().isoformat(),
        "definition": (f"tb4 = mean(top-{AN_TB4_TOP_N} HE LMPs) − "
                       f"mean(bottom-{AN_TB4_TOP_N} HE LMPs), per day, "
                       f"{AN_DA_MARKET}"),
        "requirements": (f"a day needs at least {AN_TB4_MIN_HOURS} priced hours "
                         f"so the two baskets are disjoint; shorter days are "
                         f"dropped and counted"),
    }

    if scope == "hub":
        zones = sorted(set(AN_TH_HUB_TO_ZONE.values()))
        if ident not in zones:
            raise HTTPException(
                status_code=400,
                detail=f"unknown hub '{ident}': must be one of {', '.join(zones)}")
        try:
            rows = await _an_read(_AN_TB4_HUB_SQL, {
                "dataset": AN_HUB_DA_DATASET, "series": ident,
                "min_hours": AN_TB4_MIN_HOURS})
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
        daily = [{"date": _an_iso_d(r["d"]),
                  "tb4": _an_r(_an_f(r["top_mean"]) - _an_f(r["bottom_mean"])),
                  "top4_mean": _an_r(_an_f(r["top_mean"])),
                  "bottom4_mean": _an_r(_an_f(r["bottom_mean"])),
                  "hours": int(r["n_hours"])}
                 for r in rows]
        # Short days never come back from the SQL (they are filtered server
        # side), so the count is honest only as "excluded by the read".
        depth = _an_tsv_depth(ident, daily, 0)
        body = {**common, "hub": ident, "depth": depth,
                **_an_tb4_payload(daily, 0, window),
                "query_plan": {
                    "index": "idx_tsv_series_ts",
                    "shape": ("array_agg(value ORDER BY value) per PT day, then "
                              "slice the 4 ends; ONE sort, sorted Aggregate"),
                    "measured": "88,344 rows -> 3,681 days in 148 ms warm / 189 ms part-cold",
                    "rejected": ("row_number() window frames: 4 sorts, 2,944 kB "
                                 "spill, 1,868 ms for the same answer"),
                }}
    else:
        _depth, _ident_row, _pairing, da_rows, _rt, _hub = \
            await _an_regime_reads(ident, "basis")
        hours_by_date: dict = defaultdict(list)
        for r in da_rows:
            v = _an_f(r["lmp"])
            if v is not None:
                hours_by_date[_an_as_date(r["market_date"])].append(v)
        daily, short_days = _an_tb4_daily_from_hours(hours_by_date)
        body = {**common, "hub": None, "depth": _depth,
                **_an_tb4_payload(daily, short_days, window),
                "query_plan": {
                    "index": "atlas_pnode_lmp_snapshot_pkey",
                    "shape": ("the Nodes lane's per-node DAM leg read (~192 rows, "
                              "index-only); TB4 is computed in-process"),
                    "measured": "no new query shape — reuses _AN_NODE_LEG_SQL",
                    "rejected": None,
                }}

    body["runtime_ms"] = int((time.monotonic() - started) * 1000)
    body["cached"] = False
    _an_tb4_cache[cache_key] = (time.monotonic(), body)
    response.headers["Cache-Control"] = f"public, max-age={int(AN_TB4_CACHE_TTL)}"
    _analytics_log.info("tb4 scope=%s id=%s window=%s days=%d runtime_ms=%d",
                        scope, ident, window, body["n_days_banked"],
                        body["runtime_ms"])
    return body


# ═══════════════════════════════════════════════════════════════════════════
# 7. GET /api/analytics/movers-grid — TOP-N NODES x HE1-24
# ═══════════════════════════════════════════════════════════════════════════
#
# The portfolio layout: rows are the top-N ranked nodes in BOTH directions,
# columns are HE1-24, cells are the window-mean of the metric at that hour.
#
# TWO PHASES, and the split is the whole performance story.
#   1. RANK. Delegated to `analytics_movers` IN-PROCESS, so this endpoint
#      inherits that route's pinned chunk plan (RTPD 6-hour VALUES grid / DAM
#      ANY(hours)) AND its 5-minute cache for free, and cannot drift from the
#      numbers /api/analytics/movers reports for the same request.
#   2. FILL. Per-HE cells for the 50 ranked nodes ONLY. pnode_id leads the
#      snapshot PK, so a 50-element ANY(array) is a bitmap index scan feeding a
#      single-batch HashAggregate. Measured 2026-07-30, 7-day window, 50 nodes:
#         RTPD  31,350 rows in -> 8,000 out   236 ms cold
#         DAM    8,000 rows in -> 8,000 out    57 ms
#
# WHY NOT GROUP THE UNIVERSE BY HOUR: because the Nodes lane measured it.
# GROUP BY (pnode_id, market_date, market_hour) over a 7-day RTPD window
# seq-scans 9.5M rows and spills 430 MB for 38 seconds. Ranking first and
# reading 50 nodes second is three orders of magnitude cheaper.
#
# THE COLD PATH IS STILL THE MOVERS COLD PATH. A cold 7d dart request pays
# phase 1's ~12 s before phase 2 reads a cell. The durable fix is a PRECOMPUTED
# movers artifact written pantry-side (cron + catalog entry at birth) — a named
# FOLLOW-ON lane, explicitly NOT built here. `precompute` in the payload says
# so out loud rather than leaving the next reader to rediscover it.

# The per-node per-HE read. ONE new shape, both markets, both measured above.
_AN_NODE_HE_SQL = """
    SELECT pnode_id, market_date AS d, market_hour AS he,
           avg(lmp::float8) AS v, count(*) AS k
    FROM atlas_pnode_lmp_snapshot
    WHERE pnode_id = ANY(%(nodes)s)
      AND market = %(market)s
      AND market_date >= %(lo)s
      AND market_date <= %(hi)s
      AND lmp IS NOT NULL
    GROUP BY 1, 2, 3
"""


def _an_he_map(rows) -> dict:
    """Per-node per-hour rows -> {(pnode_id, date, he): value}."""
    out = {}
    for r in rows:
        v = _an_f(r["v"])
        if v is None:
            continue
        out[(r["pnode_id"], _an_as_date(r["d"]), int(r["he"]))] = v
    return out


def _an_grid_cells(nodes, dates, per_hour) -> dict:
    """{pnode_id: (cells, cell_n)} over HE1-24 from a per-(node, date, hour)
    value function. Absent hours are null cells with n=0 — never zeros, which
    would read as "no spread" instead of "no data"."""
    out = {}
    for pid in nodes:
        cells, counts = [], []
        for he in AN_GRID_HOURS:
            vals = [v for v in (per_hour(pid, d, he) for d in dates)
                    if v is not None]
            cells.append(_an_r(_an_mean(vals)))
            counts.append(len(vals))
        out[pid] = (cells, counts)
    return out


@app.get("/api/analytics/movers-grid")
async def analytics_movers_grid(
    response: Response,
    window: str = Query(
        default="1d",
        description=f"Look-back window. One of {', '.join(AN_MOVERS_WINDOWS)}.",
    ),
    metric: str = Query(
        default="dart",
        description=f"Ranking metric. One of {', '.join(AN_MOVERS_METRICS)}.",
    ),
    cb_only: bool = Query(
        default=False,
        description=(
            "Rank only convergence-bidding-eligible nodes, at the CURRENT "
            "vintage of caiso_cb_eligible_nodes. Passed straight through to "
            "the phase-1 ranking. Default false — the public grid is unchanged."
        ),
    ),
):
    """
    The movers grid — top-25 nodes each way as rows, HE1-24 as columns.

    Rows come from `/api/analytics/movers` (this endpoint calls it in-process,
    so the ranking, the coverage counts and the 5-minute cache are that
    endpoint's, not a second opinion). Cells are the window-mean of the metric
    at that hour for that node.

    READ `value` AND `cells` AS TWO DIFFERENT STATEMENTS. `value` is the movers
    ranking statistic, computed over the STRICT matched-hour set every ranked
    node carries in full. A cell is that hour's mean over the dates the pantry
    banked it, with its own `cell_n`. When coverage is ragged the mean of a
    row's cells need not equal its `value`, and neither figure is wrong — they
    answer different questions. `cell_n` is served so the difference is
    inspectable rather than surprising.

    `scale_cap` states the colour cap (±$15 DART / ±$10 basis). Values are
    never clipped.

    CB-ONLY (`cb_only=true`) is passed through to phase 1 and nowhere else: the
    ranking narrows to current-vintage convergence-bidding-eligible nodes, and
    phase 2 then fills cells for whichever nodes ranked. `cb_filter` is the same
    block /api/analytics/movers echoes, forwarded verbatim so the two endpoints
    cannot disagree about what was filtered. Default false.

    PERFORMANCE, honestly. Phase 1 is the movers read and carries its cost:
    ~2 s cold / ~0.5 s warm for 1d, and ~12 s cold at concurrency 4 for 7d
    dart. Phase 2 adds ~0.3 s. `runtime_ms` reports what this request actually
    cost and `cached` says whether it paid it. The durable answer to the cold
    7d path is a precomputed movers artifact written pantry-side — a named
    follow-on lane, not this endpoint; see `precompute` in the payload.

    Errors: bad window/metric -> 400; DB unavailable -> 503.
    """
    assert _pool is not None

    if window not in AN_MOVERS_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"window must be one of {', '.join(AN_MOVERS_WINDOWS)}")
    if metric not in AN_MOVERS_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {', '.join(AN_MOVERS_METRICS)}")

    cache_key = (metric, window, cb_only)
    hit = _an_movers_grid_cache.get(cache_key)
    if hit and (time.monotonic() - hit[0]) < AN_MOVERS_GRID_CACHE_TTL:
        response.headers["Cache-Control"] = \
            f"public, max-age={int(AN_MOVERS_GRID_CACHE_TTL)}"
        return {**hit[1], "cached": True}

    started = time.monotonic()

    # ── Phase 1: rank, via the movers endpoint itself. ──────────────────────
    ranking = await analytics_movers(
        Response(), window=window, metric=metric, cb_only=cb_only)
    up = ranking["up"][:AN_MOVERS_GRID_TOP_N]
    down = ranking["down"][:AN_MOVERS_GRID_TOP_N]
    served = ranking["window_served"]
    nodes = list(dict.fromkeys([e["pnode_id"] for e in up + down]))

    # ── Phase 2: per-HE cells for those nodes only. ─────────────────────────
    lo = _date.fromisoformat(served["start"])
    hi = _date.fromisoformat(served["end"])
    dates = [lo + _timedelta(days=i) for i in range((hi - lo).days + 1)]

    da_map: dict = {}
    rt_map: dict = {}
    hub_map: dict = {}
    zone_of: dict = {}
    if nodes:
        try:
            da_map = _an_he_map(await _an_read(_AN_NODE_HE_SQL, {
                "nodes": nodes, "market": AN_DA_MARKET, "lo": lo, "hi": hi}))
            if metric == "dart":
                rt_map = _an_he_map(await _an_read(_AN_NODE_HE_SQL, {
                    "nodes": nodes, "market": AN_RT_MARKET, "lo": lo, "hi": hi}))
            else:
                ts_lo, ts_hi = _an_hub_ts_bounds(lo, hi)
                for zone in sorted(set(AN_TH_HUB_TO_ZONE.values())):
                    hub_map[zone] = _an_hub_map(await _an_read(_AN_HUB_LEG_SQL, {
                        "dataset": AN_HUB_DA_DATASET, "series": zone,
                        "ts_lo": ts_lo, "ts_hi": ts_hi}))
                pair_rows = await _an_read(_AN_PAIRING_MAP_SQL,
                                           {"hubs": list(AN_TH_HUB_TO_ZONE)})
                zone_of = {r["pnode_id"]: AN_TH_HUB_TO_ZONE[r["th_hub"]]
                           for r in pair_rows if r["pnode_id"] in set(nodes)}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    if metric == "dart":
        def _cell(pid, d, he):
            da = da_map.get((pid, d, he))
            rt = rt_map.get((pid, d, he))
            return None if (da is None or rt is None) else rt - da
    else:
        def _cell(pid, d, he):
            da = da_map.get((pid, d, he))
            zone = zone_of.get(pid)
            hub = hub_map.get(zone, {}).get((d, he)) if zone else None
            return None if (da is None or hub is None) else da - hub

    filled = _an_grid_cells(nodes, dates, _cell)

    def _rows(entries, direction):
        out = []
        for i, e in enumerate(entries, start=1):
            cells, counts = filled.get(e["pnode_id"], ([None] * 24, [0] * 24))
            out.append({
                "pnode_id": e["pnode_id"],
                "direction": direction,
                "rank": i,
                "value": e["value"],
                "cells": cells,
                "cell_n": counts,
                "n_cells": sum(1 for c in cells if c is not None),
            })
        return out

    runtime_ms = int((time.monotonic() - started) * 1000)
    payload = {
        "metric": metric,
        "window": window,
        # Forwarded verbatim from phase 1 so the grid and the movers board can
        # never disagree about what was filtered.
        "cb_filter": ranking["cb_filter"],
        "as_of": _utcnow().isoformat(),
        "depth": ranking["depth"],
        "window_served": served,
        "definition": ranking["definition"],
        "cell_definition": (
            "mean over the banked dates in the window of "
            + ("(hourly-mean FMM − DA)" if metric == "dart"
               else "(node DAM − hub DAM)")
            + " at that hour-ending; null where the pantry banked no such hour"),
        "value_definition": (
            "the movers ranking statistic over the strict matched-hour set — "
            "NOT the mean of this row's cells, which ride per-hour coverage"),
        "scale_cap": {
            "value": AN_GRID_SCALE_CAP[metric],
            "unit": "$/MWh",
            "applies_to": "cell colour only",
            "note": "values are never clipped; the cap saturates the palette",
        },
        "hours": list(AN_GRID_HOURS),
        "top_n": AN_MOVERS_GRID_TOP_N,
        "up": _rows(up, "up"),
        "down": _rows(down, "down"),
        "coverage": {**ranking["coverage"], "nodes_read": len(nodes)},
        "runtime_ms": runtime_ms,
        "ranking_cached": bool(ranking.get("cached")),
        "query_plan": {
            "phase_1": {**ranking["query_plan"],
                        "note": "delegated to /api/analytics/movers in-process"},
            "phase_2": {
                "index": "atlas_pnode_lmp_snapshot_pkey",
                "shape": ("pnode_id = ANY(top-N ids) + market + date range, "
                          "GROUP BY (pnode_id, date, hour); bitmap index scan "
                          "-> single-batch HashAggregate, no sort, no spill"),
                "measured": ("50 nodes x 7 days: RTPD 31,350 rows -> 8,000 in "
                             "236 ms cold; DAM 8,000 rows in 57 ms"),
                "reads": 1 if metric == "dart" else 0,
            },
        },
        "precompute": {
            "built": False,
            "note": ("this endpoint computes live behind a 5-minute cache. The "
                     "durable answer to the cold 7d path is a precomputed movers "
                     "artifact written pantry-side (cron + catalog entry at "
                     "birth) — a named follow-on lane, not this endpoint"),
        },
        "cached": False,
    }
    _an_movers_grid_cache[cache_key] = (time.monotonic(), payload)
    response.headers["Cache-Control"] = \
        f"public, max-age={int(AN_MOVERS_GRID_CACHE_TTL)}"
    _analytics_log.info(
        "movers-grid metric=%s window=%s cb_only=%s nodes=%d runtime_ms=%d "
        "ranking_cached=%s",
        metric, window, cb_only, len(nodes), runtime_ms,
        payload["ranking_cached"])
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# THE PAPER DESK — BLOTTER / BOOK / EQUITY v0  (/api/desk/*)   2026-07-31
#
# Five read-only stanzas over ONE table, `paper_journal`, which the desk agent
# writes and this service only ever reads:
#
#   GET /api/desk/blotter   every bid, newest first, with derived status
#   GET /api/desk/book      desk totals: open exposure, settled record, P&L
#   GET /api/desk/equity    cumulative settled P&L by trade_date
#   GET /api/desk/by-node   per-pnode aggregates
#   GET /api/desk/by-play   per-screen aggregates (v0 serves play: null — §PLAY)
#
# THE ARITHMETIC IS NOT HERE. Every derivation lives in paper_desk.py, which is
# pure — no I/O, no clock, no database. This layer reads rows and hands them
# over. That split is what makes tests/test_paper_desk.py a test of the ledger
# rules rather than a test of a query. The five rails (notes are not bids;
# status is derived from two columns; a position marks only at settlement; P&L
# is read and never recomputed from prices; empty is [] and thin is null) are
# stated once, in that module's docstring, and pinned there.
#
# ZERO LLM, MECHANICALLY. Nothing in this lane calls a language service. It is
# arithmetic over 19 banked rows. tests/test_paper_desk.py greps the source of
# paper_desk.py AND of all five route functions below for the vocabulary that
# would betray one, in the house pattern
# (test_structures.py::test_no_forward_pricing_vocabulary_anywhere_in_the_module).
#
# §PLAY — THE ONE FINDING, STATED UP FRONT. The by-play stanza wants the screen
# that fired each bid. The writer does not carry one in a machine-readable slot:
# `inputs_as_of` holds map.map_id (the agent program — identical on every row in
# the table, notes included), bid.* position fields, and bid_screen.facts[]
# (free prose). The screen name appears only in the `rationale` PROSE, under
# "WHICH SCREEN FIRED". So v0 serves `play: null` with the reason attached, and
# does not regex an English sentence to key an aggregate. The near miss and the
# one-field writer fix are documented in paper_desk.py's PLAY_KEY_PATHS block.
# The lookup is live: stamp inputs_as_of.bid.screen and this lights up with no
# code change.
#
# DEPTH, MEASURED 2026-07-31. `paper_journal` holds 19 rows: 17 notes and 2
# bids, both filed 2026-07-30, both unsettled with no reason stamped (so both
# read OPEN), both frontier_ok = false. NOTHING HAS SETTLED YET. So today the
# equity curve is honestly `points: []`, the book's settled block is all zeros
# with `win_rate: null`, and by-node returns two nodes with open exposure and no
# record. The settled branches are real, tested against hand-built rows, and
# light themselves the day the settlement writer runs — no code change here.
#
# QUERY SHAPES: ONE, and it is the whole lane. See _PD_BIDS_SQL for the EXPLAIN
# receipt. No migrations, no new indexes, no existing endpoint touched.
# ═══════════════════════════════════════════════════════════════════════════

import paper_desk as _pd

DESK_PREFIX = "/api/desk"

# The lane's ONE query shape. Every stanza rides it; none of them adds a second.
#
# `inputs_as_of` is selected because the by-play key lookup needs it, and is
# NEVER put on the wire — it is a heavy jsonb blob (the full bid screen with its
# prose facts) and the blotter is a position list, not an audit log.
#
# EXPLAIN (ANALYZE, BUFFERS) receipt — Neon `Energylake`, 2026-07-31:
#
#   Sort  (cost=3.21..3.21 rows=1 width=418) (actual time=0.033..0.033 rows=2)
#     Sort Key: trade_date, entry_id
#     Sort Method: quicksort  Memory: 25kB
#     Buffers: shared hit=6
#     ->  Seq Scan on paper_journal
#           (cost=0.00..3.20 rows=1 width=418) (actual time=0.016..0.016 rows=2)
#           Filter: (kind = 'bid'::text)
#           Rows Removed by Filter: 17
#           Buffers: shared hit=3
#   Planning Time: 2.367 ms
#   Execution Time: 0.055 ms
#
# A Seq Scan is the RIGHT plan here and not a finding: the whole table is three
# pages, so any index would cost a second read to save nothing. The table does
# carry `uniq_paper_journal_bid_position` (trade_date, pnode_id, direction)
# WHERE kind = 'bid' — a write-side uniqueness guard, not a read path, and this
# lane neither needs nor uses it. The plan is worth revisiting when the journal
# reaches the low thousands of rows; at 19 it is 0.055 ms and 3 buffers.
_PD_BIDS_SQL = """
    SELECT entry_id, entry_ts, kind, trade_date, pnode_id, constraint_id,
           direction, size_mw, price_limit, hour_scope, conviction,
           filled_hours, settled, settle_da, settle_fmm, pnl_per_mwh,
           pnl_dollars, settled_at, unsettled_reason, frontier_ok,
           inputs_as_of
    FROM paper_journal
    WHERE kind = %(kind)s
    ORDER BY trade_date, entry_id
"""


async def _pd_read_bids() -> tuple:
    """The lane's single read -> (normalized bids, per-bid play triples).

    Returns them side by side and in the same order so the by-play stanza can
    group without `inputs_as_of` ever reaching the wire.

    Fail-loud: a DB failure is a 503 with a plain body, never a fabricated 200
    with an empty blotter. A 200 from any stanza in this lane asserts the
    journal was actually read — "no positions" and "could not look" are
    different answers and must never render the same.
    """
    try:
        rows = await _cockpit_read(_PD_BIDS_SQL, {"kind": _pd.BID_KIND})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
    bids = _pd.bid_rows(rows)
    by_id = {r.get("entry_id"): r.get("inputs_as_of") for r in rows}
    plays = [_pd.play_of(by_id.get(b["entry_id"])) for b in bids]
    return bids, plays


def _pd_base() -> dict:
    """The stamp every stanza carries: when it was computed, and off what."""
    return {
        "as_of": _utcnow().isoformat(),
        "source": "paper_journal (kind='bid')",
        "paper_only": (
            "these are paper positions. Nothing in this repository can submit "
            "a bid to CAISO or any external market; no such code path exists"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 1. GET /api/desk/blotter — every bid, with its derived status
# ═══════════════════════════════════════════════════════════════════════════

@app.get(f"{DESK_PREFIX}/blotter")
async def desk_blotter():
    """
    The blotter — every `kind='bid'` row in `paper_journal`, newest first.

    Per row: trade_date, pnode_id, direction, size_mw, price_limit, hour_scope,
    conviction, settled, settle_da, settle_fmm, pnl_per_mwh, pnl_dollars,
    filled_hours, settled_at, unsettled_reason — plus `entry_id` (a stable row
    key), `constraint_id`, and `frontier_ok`.

    `frontier_ok` is additive beyond the named list and is load-bearing: every
    bid banked to date carries `false`, meaning the position was taken on
    inputs the writer's own freshness check flagged. A blotter that hides that
    lies by omission.

    STATUS IS DERIVED SERVER-SIDE, never stored:
        settled                       -> SETTLED
        unsettled, no reason stamped  -> OPEN
        unsettled, reason stamped     -> PENDING

    A PENDING row's `unsettled_reason` is served VERBATIM — never mapped to a
    code, never truncated. Render the string.

    NOTES ARE NOT BIDS. `kind='note'` is the morning pre-bid read: prose, no
    position. It is filtered in the SQL and again in `paper_desk.bid_rows`, and
    can never appear here.

    Empty journal -> `rows: []`, never null. DB down -> 503.
    """
    assert _pool is not None
    bids, _plays = await _pd_read_bids()
    return {**_pd_base(), **_pd.blotter(bids)}


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET /api/desk/book — the totals
# ═══════════════════════════════════════════════════════════════════════════

@app.get(f"{DESK_PREFIX}/book")
async def desk_book():
    """
    The book — desk totals over the same bids the blotter serves.

    Open exposure is a COUNT and a GROSS MW (sum of |size_mw|, direction
    ignored) and NOTHING ELSE. There is no live intraday mark in v0: a position
    marks only at settlement, so an open position contributes no dollars to
    anything here. (A banked-hours running mark is filed as v1 debt in the
    handoff, deliberately not built.)

    Settled record: n, win / loss / flat, and `win_rate` over the classified
    rows. `win = pnl_dollars > 0`. A ZERO-FILL SETTLEMENT — a bid that never
    cleared, `filled_hours = 0` — transacts no MWh and so books no P&L: it
    counts FLAT, never a loss.

    `unclassified` counts settled rows carrying no P&L that are not zero-fills
    — real holes in the ledger. win + loss + flat + unclassified == settled.n,
    always; nothing is quietly dropped to make a rate look clean.

    `pnl_dollars` is the cumulative settled total, summed from the same
    cent-rounded figures the blotter displays, so the visible column adds up.
    P&L arrives ALREADY SIGNED from the settlement writer and is read verbatim
    — `settle_da` / `settle_fmm` are display columns and are never operands.

    `avg_pnl_per_mwh` is the UNWEIGHTED mean over settled rows carrying one,
    with `avg_pnl_per_mwh_n` beside it. Null, never 0.0, when nothing has
    settled — 0.0 would read "everything lost".

    DB down -> 503.
    """
    assert _pool is not None
    bids, _plays = await _pd_read_bids()
    return {**_pd_base(), **_pd.book(bids)}


# ═══════════════════════════════════════════════════════════════════════════
# 3. GET /api/desk/equity — the curve
# ═══════════════════════════════════════════════════════════════════════════

@app.get(f"{DESK_PREFIX}/equity")
async def desk_equity():
    """
    The equity curve — cumulative settled `pnl_dollars` by trade_date, ascending.

    SETTLED ROWS ONLY, and GAPS ARE HONEST. One point per trade_date that
    actually carries a settlement; a date with nothing settled produces NO
    POINT. Consecutive points are therefore not necessarily adjacent dates.
    There is no interpolation, no zero-fill of an empty date, and no
    carry-forward of the last value — a flat segment drawn across a gap is a
    claim that the desk was flat, which is not what a gap means.

    Each point carries the day's `pnl_dollars`, the running
    `cumulative_pnl_dollars`, and `settled_n` / `booked_n` so a client can see
    when a day's total rests on fewer rows than settled that day.

    The last point's cumulative figure is exactly the book's `pnl_dollars` —
    both sum the same cent-rounded row values.

    Nothing settled -> `points: []` and `span: null`, never a fabricated
    zero line. DB down -> 503.
    """
    assert _pool is not None
    bids, _plays = await _pd_read_bids()
    return {**_pd_base(), **_pd.equity_curve(bids)}


# ═══════════════════════════════════════════════════════════════════════════
# 4. GET /api/desk/by-node — per-pnode aggregates
# ═══════════════════════════════════════════════════════════════════════════

@app.get(f"{DESK_PREFIX}/by-node")
async def desk_by_node():
    """
    Per-pnode aggregates: n, settled n, win / loss / flat, win rate, total P&L,
    and average pnl/MWh — plus open count and open gross MW, so a node with
    live exposure and no record yet is still legible.

    Ordered by total P&L descending. A node with NOTHING SETTLED sorts last but
    STAYS VISIBLE: dropping it would make the list itself read as a ranking of
    performance, when it is a ranking of a mixed set.

    `win_rate` is null — never 0.0 — for a node with nothing classified.

    Every rule the book applies applies here: settled-only P&L, zero-fill is
    flat, P&L read never recomputed.

    Empty journal -> `nodes: []`. DB down -> 503.
    """
    assert _pool is not None
    bids, _plays = await _pd_read_bids()
    return {**_pd_base(), **_pd.by_node(bids)}


# ═══════════════════════════════════════════════════════════════════════════
# 5. GET /api/desk/by-play — per-screen aggregates (v0: play is null)
# ═══════════════════════════════════════════════════════════════════════════

@app.get(f"{DESK_PREFIX}/by-play")
async def desk_by_play():
    """
    Per-play aggregates, keyed off the screen that fired each bid — the same
    statistics `/api/desk/by-node` reports, grouped by play instead of node.

    V0 SERVES `play: null`, AND THAT IS THE FINDING. The writer carries no
    machine-readable play key. `inputs_as_of` on a bid row holds:

      * `map.map_id` = "desk_reader_prebid" — identical on EVERY row in the
        table, notes included. That is the agent program, not the screen; it
        cannot discriminate one play from another.
      * `bid.*` — the position's own fields (size, node, direction, conviction,
        hour scope, price limit, constraint, rules, fingerprint, persistence
        metrics, settle reference, conviction reasons).
      * `bid_screen.{facts[], positions[], bids_filed}` — `facts` is free prose
        ("GATE 1 OK ...", "CANDIDATE ...", "PRICED ...", "DROP ...").

    The screen name appears in exactly one place: the `rationale` PROSE, under
    the heading "WHICH SCREEN FIRED". Regexing an English sentence to key an
    aggregate would make the aggregate a property of the writer's prose style,
    so this endpoint does not do it.

    THE NEAR MISS, so nobody re-discovers it: `bid.persistence` exists as a
    metrics block, and both banked bids did fire off the persistence screen.
    Inferring the play from the PRESENCE of that key is schema-shape
    divination, not a contract — there is no phantom or surprise counterpart to
    discriminate against, and both banked rows come from one run on one trade
    date, so the guess cannot be checked against a counterexample.

    So every bid falls into ONE group with `play: null` — the counts still tie
    to the blotter — and `key` reports what was searched and why nothing was
    found. Zero bids -> `plays: []`.

    WRITER FIX, one field, no migration of existing rows: stamp
    `inputs_as_of.bid.screen` ∈ {"persistence", "phantom", "surprise"}. The
    lookup here is already live against `key.paths_searched`, so the stanza
    groups on it the day it appears, with no change to this file. If a bid can
    be corroborated by more than one screen — the rationale's "No
    phantom/surprise corroboration this run" implies it can — then
    `bid.screens: [...]` is the right shape, and v1 groups on the array; v0
    reads scalars only and reports an array as present-but-not-groupable rather
    than flattening it silently.

    DB down -> 503.
    """
    assert _pool is not None
    bids, plays = await _pd_read_bids()
    return {**_pd_base(), **_pd.by_play(bids, plays)}


# ═══════════════════════════════════════════════════════════════════════════
# THE LAB PAPER DESK v0  (/api/lab/paper-desk)                     2026-08-06
#
# ONE endpoint, ONE read, the whole desk in one payload:
#
#   GET /api/lab/paper-desk   blotter + equity + by_node + by_play + book
#
# WHY THIS EXISTS ALONGSIDE /api/desk/*, RATHER THAN REPLACING IT. Two reasons,
# and both are contract-level, not cosmetic:
#
#   1. VOIDS. `/api/desk/*` has no void concept — a voided position reads
#      PENDING there, because a voided row IS an unsettled row carrying a
#      reason. On 2026-07-31 that was invisible: nothing had been voided. It is
#      not invisible now. 8 of the 14 banked bids are voided double-books, and
#      a page that files them under "pending" tells the desk it is waiting on
#      settlements that are never coming. This lane derives a third status,
#      PUTS THE VOIDS ON THE BLOTTER, excludes them from every aggregate, and
#      counts them in `book.n_void`. Voids are the product here, not noise.
#
#   2. ONE INSTANT. The /api/desk handoff filed it as debt in its own §7: the
#      five stanzas each issue their own read, so a client rendering all five
#      can mix two instants. This lane is that debt paid — one read, one
#      `as_of`, five stanzas that cannot disagree with each other.
#
# The two lanes are therefore NOT merged and neither is deprecated. `/api/desk`
# is OPEN/PENDING/SETTLED with a trade_date curve; this is settled/pending/void
# with a settled_at curve. Both have consumers, and collapsing them would force
# one of the two to change under a client.
#
# THE ARITHMETIC IS NOT HERE. Every derivation lives in paper_lab.py, which is
# pure — no I/O, no clock, no database. This layer reads rows and hands them
# over. paper_lab.py reuses paper_desk.py's coercions and, critically, its
# `play_of` lookup: there is ONE implementation of the play key in the
# building, not two.
#
# §PLAY — THE FINDING, AND IT IS GOOD NEWS. The brief anticipated serving
# `play: "unclassified"` because on 2026-07-31 the screen that fired a bid
# lived only in the `rationale` PROSE, and `/api/desk/by-play` refused to regex
# an English sentence to key an aggregate — filing instead a one-field writer
# fix: stamp `inputs_as_of.bid.screen`. THE WRITER TOOK THE FIX. Re-measured
# 2026-08-06: the key is stamped on 12 of 14 bids ("surprise" x6,
# "persistence" x6) and this lane groups on it for real. The 2 rows without it
# (entries 18 and 19) predate the fix, are both voided, and honestly read
# "unclassified" — they are not backfilled and no play is guessed for them. The
# rationale prose is still never parsed. `derivation.play` ships the
# measurement with the payload. See paper_lab.py's PLAY block for the receipt,
# including that the prose and the key agree 12 for 12.
#
# ZERO LLM, MECHANICALLY GREPPED — the house pattern, applied to paper_lab.py
# and to the route function and reader below.
#
# QUERY SHAPES: ONE, and it carries the curve count with it. See
# _LAB_PAPER_DESK_SQL for the EXPLAIN receipt. No migrations, no new indexes,
# no writes, no existing endpoint touched.
# ═══════════════════════════════════════════════════════════════════════════

import paper_lab as _pl

LAB_PREFIX = "/api/lab"

# The lane's ONE query shape. `paper_bid_curves` is joined as a CORRELATED
# COUNT rather than a second round trip, so the endpoint keeps its one-read
# promise: a client cannot see a blotter row whose curve count came from a
# different instant than the row itself.
#
# `rationale` and `inputs_as_of` ARE on the wire in this lane — a deliberate
# difference from /api/desk/blotter, which withholds them. This page is an
# audit surface: the whole point is that a reader can see what a position was
# taken on and why. Measured 2026-08-06 that is 36 kB of jsonb and ~33 kB of
# prose across all 14 bids. Worth re-measuring, and probably worth paginating,
# if the journal reaches the low thousands of rows.
#
# EXPLAIN (ANALYZE, BUFFERS) receipt — Neon `Energylake`, 2026-08-06:
#
#   Sort  (cost=74.63..74.66 rows=14 width=1088) (actual time=0.222..0.223 rows=14)
#     Sort Key: j.trade_date DESC, j.entry_id DESC
#     Sort Method: quicksort  Memory: 27kB
#     Buffers: shared hit=34
#     ->  Seq Scan on paper_journal j
#           (cost=0.00..74.36 rows=14 width=1088) (actual time=0.035..0.208 rows=14)
#           Filter: (kind = 'bid'::text)
#           Rows Removed by Filter: 24
#           SubPlan 1
#             ->  Aggregate  (cost=4.84..4.85 rows=1) (actual time=0.013..0.013 loops=14)
#                   ->  Seq Scan on paper_bid_curves c
#                         (cost=0.00..4.80 rows=16) (actual rows=16 loops=14)
#                         Filter: (entry_id = j.entry_id)
#                         Rows Removed by Filter: 208
#   Planning Time: 0.190 ms
#   Execution Time: 0.256 ms
#
# TWO Seq Scans, AND BOTH ARE THE RIGHT PLAN — this is not a finding. Both
# tables are a handful of pages (38 journal rows, 224 curve rows), so an index
# would cost a second read to save nothing; the planner is choosing correctly.
# `paper_bid_curves` DOES carry a usable index for the day it matters —
# `paper_bid_curves_pkey (entry_id, he, segment)`, leading on entry_id — so the
# correlated subplan flips to an index scan on its own when the table grows,
# with no change here. The subplan's 14 loops are the thing to watch: it is
# O(bids) and today that is 0.256 ms total.
_LAB_PAPER_DESK_SQL = """
    SELECT j.entry_id, j.kind, j.author, j.trade_date, j.pnode_id,
           j.direction, j.size_mw, j.price_limit, j.hour_scope,
           j.settled, j.unsettled_reason,
           j.settle_da, j.settle_fmm, j.pnl_per_mwh, j.pnl_dollars,
           j.settled_at, j.settled_by_version,
           j.rationale, j.inputs_as_of,
           (SELECT count(*) FROM paper_bid_curves c
             WHERE c.entry_id = j.entry_id) AS curve_points
    FROM paper_journal j
    WHERE j.kind = %(kind)s
    ORDER BY j.trade_date DESC, j.entry_id DESC
"""


async def _lab_read_desk() -> list:
    """The lane's single read.

    Fail-loud: a DB failure is a 503 with a plain body, never a fabricated 200
    with an empty desk. A 200 from this endpoint asserts the journal was
    actually read — "no positions" and "could not look" are different answers
    and must never render the same.
    """
    try:
        return await _cockpit_read(_LAB_PAPER_DESK_SQL, {"kind": _pl.BID_KIND})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")


@app.get(f"{LAB_PREFIX}/paper-desk")
async def lab_paper_desk():
    """
    The whole paper desk in one payload, off one read of `paper_journal`.

    `blotter[]` — every `kind='bid'` row, newest first. Per row: entry_id,
    trade_date, pnode_id, author, direction, size_mw, price_limit, hour_scope,
    status, unsettled_reason, settle_da, settle_fmm, pnl_per_mwh, pnl_dollars,
    settled_at, settled_by_version, rationale, inputs_as_of, curve_points —
    plus `play`, which is additive and load-bearing (the by_play stanza groups
    on it, and a client re-deriving it from `inputs_as_of` would be a second
    implementation of the same rule, waiting to drift).

    `curve_points` is a COUNT of this bid's rows in `paper_bid_curves` — the
    shape of the bid that was filed, as a number, not the curve itself. Every
    banked bid carries 16 (HE7-22, one segment per hour). No banked curve is 0,
    not null: zero curve points is a fact, not an unknown.

    STATUS IS DERIVED SERVER-SIDE, THREE-VALUED, and lowercase:
        settled = true                     -> "settled"   (terminal, first)
        unsettled, reason starts "voided"  -> "void"
        anything else                      -> "pending"

    There is no "open" here. `/api/desk/*` splits unsettled-with-no-reason from
    unsettled-with-a-reason; this contract does not, and both read "pending".

    VOIDS RIDE THE BLOTTER. That is the point of this endpoint. A voided
    position is a thing the desk did and then withdrew, and 8 of the 14 banked
    bids are voided double-books — 7 of them every bid ever filed at
    LUNDY_7_N003. They appear in `blotter[]` with their reason verbatim, they
    are EXCLUDED from every figure in `equity`, `by_node` and `by_play`, and
    they are COUNTED in `book.n_void`. A void is never folded into pending, and
    `n_settled + n_pending + n_void == len(blotter)` always.

    A node or play whose every bid was voided STILL APPEARS in the aggregates,
    with zeroes and an `n_void` — dropping it would take its voids off the page,
    which is the failure this endpoint exists to prevent.

    `equity[]` — `{date, cumulative_pnl}` by the UTC calendar date of
    `settled_at`, i.e. WHEN THE MONEY WAS BOOKED, not when the position was
    taken. This will NOT agree with `/api/desk/equity`, which draws by
    trade_date, and is not meant to: the one settled bid was taken 2026-07-31
    and settled 2026-08-06. Dates with nothing settled produce no point — gaps
    are real and are left as gaps. A settled row with no `settled_at` is
    excluded and counted in `derivation.equity`, never parked on a made-up date.

    `by_node[]` / `by_play[]` — `n_settled`, `n_pending` (by_node; additive on
    by_play), `wins`, `total_pnl`, `avg_pnl_per_mwh` (by_node), plus additive
    `n_void`. Ranked by total_pnl, nothing-settled groups last, never dropped.

    `book` — `settled_pnl`, `n_settled`, `n_pending`, `n_void`. `settled_pnl`
    is exactly the sum of the blotter's displayed `pnl_dollars` column and
    exactly the curve's last `cumulative_pnl`; all three sum the same
    cent-rounded row values, so a desk that adds up the visible column gets the
    same answer.

    P&L IS READ, NEVER RECOMPUTED. `settle_da` and `settle_fmm` sit on the wire
    beside `pnl_dollars` for display and are never operands. The settlement
    writer owns the direction convention; do not re-derive a sign from them.

    `derivation` is additive and carries every rule this endpoint applied,
    including `derivation.play` — the measured coverage of the writer's play
    key, which is now LIVE on 12 of 14 rows (see §PLAY above).

    Empty journal -> every list `[]`, book all zeros. DB down -> 503.
    """
    assert _pool is not None
    rows = await _lab_read_desk()
    return {
        "as_of": _utcnow().isoformat(),
        **_pl.payload(rows),
        "source": "paper_journal (kind='bid') + paper_bid_curves (count)",
        "paper_only": (
            "these are paper positions. Nothing in this repository can submit "
            "a bid to CAISO or any external market; no such code path exists"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODAL DAILY SERVING v0 — atlas_node_daily_stats on the wire
# lane ruled 2026-07-31.
#
#   GET /api/analytics/node/{pnode_id}/daily?days=30      (cap 90)
#
# WHAT THIS SERVES, AND WHY IT IS DIFFERENT FROM EVERYTHING ABOVE IT. Every
# other endpoint in the Analytics + Regime rooms reads
# `atlas_pnode_lmp_snapshot` — a ~7-day rolling HOT TIER with no standing
# writer. This one reads `atlas_node_daily_stats`, the DURABLE per-node daily
# record derived FROM that snapshot nightly and written to outlive it. The
# snapshot is where the hours live; this table is where the days accumulate.
#
# THE LAWS BELOW ARE NOT THIS LANE'S OPINIONS. They are lifted from the COMMENTs
# on `atlas_node_daily_stats` itself (migration 125) and re-stated in every
# response's `rules` block, because each is a thing a client can get silently,
# catastrophically wrong:
#
#   1. THE DART SIGN IS PINNED: positive = RT above DA. This repo carries BOTH
#      conventions — fingerprints/build.py computes (dam.da - fmm.fmm), the
#      exact NEGATION — and /api/timeseries/caiso-hub-lmp ships the opposite
#      sign under the field name `spread`. Flip it and every chart inverts with
#      no error anywhere.
#
#   2. A NULL dart_onpk ON A SUNDAY OR A NERC HOLIDAY IS THE CALENDAR, NOT AN
#      ABSENCE. Those days have no on-peak block at all, so hours_onpk_matched
#      is 0 and the mean does not exist. `null_semantics` on every row says
#      which of the two a null is, so the client never has to infer it.
#
#   3. ON-PEAK HERE IS THE NERC/WECC 6x16 BLOCK: HE7-22 inclusive, Monday
#      through SATURDAY, EXCLUDING NERC holidays. This is NOT the `7x16` block
#      the Analytics room calls on-peak elsewhere in this same API (HE7-22, all
#      seven days, holiday-blind). Different blocks; the response says so.
#
#   4. tb4 IS A MEAN-OF-4 SPREAD IN $/MWh. `caiso_blocks_daily.tb4` is a SUM of
#      the extremes in $/MW-day and is therefore ~4x this number. The two are
#      not comparable without dividing.
#
#   5. is_weekend IS NOT "no on-peak block". Saturday is a weekend day AND an
#      on-peak day. A consumer that conflates them is wrong 52 days a year.
#
# GAPS ARE REAL. `days` bounds ROWS, not a calendar span: the read takes the
# newest `days` rows this node actually has. A date the nightly producer never
# banked is simply not in the list — never zero-filled, never interpolated —
# and `days_returned` beside `days_requested` is how the thinness stays
# visible. `window_served` reports the first and last market_date actually
# returned, so a client can compare the span against the count and see a hole.
#
# QUERY PLAN. One index-served read on `idx_node_daily_node_date
# (pnode_id, market_date DESC)` — equality on the leading column plus a
# backwards range scan, LIMIT-bounded at 90, so the cost is the LIMIT and not
# the table. A second tiny read reports the table's banked depth (min/max
# market_date), served by `idx_node_daily_date_dart`, so a zero-row answer is
# interpretable rather than mysterious.
#
# ERRORS. Blank pnode_id -> 400. Out-of-range `days` -> 400 naming the bound. A
# node with no banked rows -> 200 with days_returned: 0 and the depth attached,
# never a 404 and never a fabricated flat line (house standard).
# ═══════════════════════════════════════════════════════════════════════════

AN_DAILY_TABLE = "atlas_node_daily_stats"
AN_DAILY_DAYS_DEFAULT = 30
AN_DAILY_DAYS_MIN = 1
AN_DAILY_DAYS_MAX = 90

# The rules block, carried from the COMMENTs on atlas_node_daily_stats. These
# strings are PINNED BY TEST (tests/test_nodal_daily.py) — a client that keys a
# caption or a tooltip off one of them must not have it silently reworded.
AN_DAILY_RULES = {
    "dart_sign": (
        "DART = hourly-mean FMM minus DA, taken over MATCHED hours only. SIGN "
        "IS PINNED: positive = RT above DA. Do not \"fix\" it to DA - FMM; this "
        "repo carries both conventions and fingerprints/build.py's "
        "(dam.da - fmm.fmm) is the NEGATION of this column."
    ),
    "onpeak_block": (
        "On-peak is the NERC/WECC block: HE7-HE22 inclusive, Monday-Saturday, "
        "EXCLUDING NERC holidays. Off-peak is the exact complement. This is NOT "
        "the 7x16 block (HE7-22 all seven days, holiday-blind) that this API's "
        "Analytics room calls on-peak; the two must never be conflated."
    ),
    "onpeak_null_is_calendar": (
        "dart_onpk is NULL when hours_onpk_matched = 0, which is the correct "
        "and expected value on every Sunday and every NERC holiday - those days "
        "have no on-peak block at all. Never read a NULL there as a missing "
        "measurement without checking dow and is_nerc_holiday first."
    ),
    "is_weekend": (
        "is_weekend is Saturday or Sunday. It is NOT the inverse of \"has an "
        "on-peak block\": Saturday is a weekend day AND an on-peak day under "
        "the NERC 6x16 convention. A consumer that treats is_weekend as \"no "
        "on-peak block\" is wrong 52 days a year."
    ),
    "tb4_unit": (
        "tb4_da/tb4_rt are the MEAN of the 4 highest hourly prices minus the "
        "MEAN of the 4 lowest, in $/MWh. NOTE the unit difference from "
        "caiso_blocks_daily.tb4, which is a SUM of the extremes in $/MW-day and "
        "is therefore ~4x this number. The two are not comparable without "
        "dividing."
    ),
    "tb4_full_day_gate": (
        "tb4_da is NULL unless the DA leg banked a FULL Pacific civil day "
        "(23/24/25 hours depending on DST - not a literal 24). tb4_rt is gated "
        "on the FMM leg's OWN full-day coverage, independently of tb4_da."
    ),
    "basis": (
        "basis_da/basis_fmm are node mean minus the node's th_hub mean. NULL "
        "when the node has no th_hub in atlas_pnode_universe (21,821 of 24,098 "
        "nodes - honest absence and the common path) AND ALSO when the hub "
        "itself is not priced in the snapshot for that date. As of 2026-07-31 "
        "the latter makes this column NULL FOR EVERY ROW: the five th_hub nodes "
        "have zero DAM rows anywhere in the snapshot. That is an upstream "
        "ingester gap, deliberately not worked around."
    ),
    "hours_matched": (
        "hours_matched is how many hours on this date had BOTH a DAM print and "
        "at least one FMM interval for this node - the honest N behind "
        "dam_mean, fmm_mean and dart_mean. A 9 here means dart_mean is a 9-hour "
        "mean, and no consumer should present it as a day."
    ),
    "dow": (
        "dow is 0=Monday .. 6=Sunday - Python's date.weekday() convention. "
        "Deliberately NOT isoweekday (1-7) and NOT Postgres EXTRACT(DOW) "
        "(0=Sunday). Three conventions exist in this codebase; this column pins "
        "this one."
    ),
    "cb_eligible": (
        "cb_eligible is convergence-bidding eligibility joined from "
        "caiso_cb_eligible_nodes at its CURRENT max(vintage_date) as of "
        "derivation time - a point-in-time stamp, not a foreign key. It answers "
        "\"was this node CB-eligible when we last computed the row\", NOT \"was "
        "it eligible on market_date\". NULL when the node is absent from the "
        "vintage entirely."
    ),
    "gaps": (
        "`days` bounds ROWS, not a calendar span: this returns the newest "
        "`days` rows the node actually has. Dates the nightly producer never "
        "banked are absent - never zero-filled, never interpolated. Compare "
        "days_returned against days_requested, and window_served against the "
        "count, to see the thinness."
    ),
}

# Per-row null semantics. The point of this block is that a client never has to
# re-derive law #2 from dow/is_nerc_holiday itself — the server already did.
AN_DAILY_NULL_CALENDAR = "calendar: no on-peak block exists on this date"
AN_DAILY_NULL_ABSENT = "absent: no matched hours in this block on this date"

_AN_DAILY_SQL = """
    SELECT market_date, dow, is_weekend, is_nerc_holiday,
           dart_mean, dart_onpk, dart_offpk,
           tb4_da, tb4_rt,
           basis_da, basis_fmm,
           hours_matched, hours_onpk_matched, hours_offpk_matched,
           cb_eligible, computed_at
    FROM atlas_node_daily_stats
    WHERE pnode_id = %(pnode_id)s
    ORDER BY market_date DESC
    LIMIT %(days)s
"""

# Table-wide banked depth, so a zero-row node is interpretable. Index-served by
# idx_node_daily_date_dart (market_date leading).
_AN_DAILY_DEPTH_SQL = """
    SELECT min(market_date) AS min_date,
           max(market_date) AS max_date,
           count(DISTINCT market_date) AS dates
    FROM atlas_node_daily_stats
"""


def _an_daily_null_reason(value, hours: int, dow: int, holiday: bool):
    """Why a block mean is null — the calendar, or a genuine absence.

    Returns None when the value is PRESENT: there is nothing to explain about a
    number that exists. A Sunday (dow 6) or a NERC holiday has NO on-peak block,
    so a null there is the calendar answering correctly; anything else is hours
    that did not match.
    """
    if value is not None:
        return None
    if hours == 0 and (dow == 6 or holiday):
        return AN_DAILY_NULL_CALENDAR
    return AN_DAILY_NULL_ABSENT


def _an_daily_row(r: dict) -> dict:
    """One atlas_node_daily_stats row, shaped for the wire.

    Every numeric is rounded to the department's 4dp. `null_semantics` carries
    the per-row verdict for BOTH block means, and `has_onpeak_block` states the
    calendar fact directly, so the client never re-derives either.
    """
    d = _an_as_date(r["market_date"])
    dow = int(r["dow"])
    holiday = bool(r["is_nerc_holiday"])
    onpk_hours = int(r["hours_onpk_matched"])
    offpk_hours = int(r["hours_offpk_matched"])
    computed = r.get("computed_at")
    return {
        "market_date": d.isoformat() if d else None,
        "dow": dow,
        "is_weekend": bool(r["is_weekend"]),
        "is_nerc_holiday": holiday,
        "has_onpeak_block": not (dow == 6 or holiday),
        "dart_mean": _an_r(r["dart_mean"]),
        "dart_onpk": _an_r(r["dart_onpk"]),
        "dart_offpk": _an_r(r["dart_offpk"]),
        "tb4_da": _an_r(r["tb4_da"]),
        "tb4_rt": _an_r(r["tb4_rt"]),
        "basis_da": _an_r(r["basis_da"]),
        "basis_fmm": _an_r(r["basis_fmm"]),
        "hours_matched": int(r["hours_matched"]),
        "hours_onpk_matched": onpk_hours,
        "hours_offpk_matched": offpk_hours,
        "cb_eligible": (None if r["cb_eligible"] is None
                        else bool(r["cb_eligible"])),
        "computed_at": computed.isoformat() if computed else None,
        "null_semantics": {
            "dart_onpk": _an_daily_null_reason(
                r["dart_onpk"], onpk_hours, dow, holiday),
            "dart_offpk": _an_daily_null_reason(
                r["dart_offpk"], offpk_hours, dow, holiday),
        },
    }


def _an_daily_depth(rows) -> dict:
    """The table's banked depth — what exists at all, node-independent."""
    r = rows[0] if rows else {}
    mn, mx = _an_as_date(r.get("min_date")), _an_as_date(r.get("max_date"))
    return {
        "source": AN_DAILY_TABLE,
        "retention": (
            "durable; derived nightly by nodal_daily_stats.py from "
            "atlas_pnode_lmp_snapshot ONLY (never from timeseries_values), and "
            "written to outlive that ~7-day rolling hot tier"),
        "min_date": mn.isoformat() if mn else None,
        "max_date": mx.isoformat() if mx else None,
        "dates": int(r.get("dates") or 0),
    }


@app.get("/api/analytics/node/{pnode_id}/daily")
async def analytics_node_daily(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. LUNDY_7_N003. Blank -> 400.",
    ),
    days: int = Query(
        default=AN_DAILY_DAYS_DEFAULT,
        description=(
            f"How many of the node's newest daily rows to return, "
            f"{AN_DAILY_DAYS_MIN}..{AN_DAILY_DAYS_MAX}. Bounds ROWS, not a "
            f"calendar span — gaps are never filled."
        ),
    ),
):
    """
    The node's daily record — one row per banked market_date, newest first.

    Reads `atlas_node_daily_stats`, the DURABLE nightly derivation, not the
    ~7-day snapshot hot tier every other endpoint in this room reads. Per date:
    the DART means (day, on-peak, off-peak), both tb4 battery-arbitrage spreads,
    both basis legs, the matched-hour counts behind all of it, the calendar
    flags, and the node's convergence-bidding eligibility stamp.

    THE FIVE LAWS, all carried in `rules` on every response because each is
    silent on the server and catastrophic on the client:

      * DART SIGN. Positive = RT above DA. The OPPOSITE sign ships from
        /api/timeseries/caiso-hub-lmp under the field name `spread`, and
        fingerprints/build.py computes the exact negation. Never mix them.

      * A NULL dart_onpk ON A SUNDAY OR A NERC HOLIDAY IS THE CALENDAR. Those
        days have no on-peak block at all, so the mean does not exist. Every row
        carries `null_semantics` saying which of the two a null is, and
        `has_onpeak_block` saying whether the block existed — so the client
        never re-derives it from dow and is_nerc_holiday.

      * ON-PEAK IS THE NERC 6x16 BLOCK — HE7-22, Mon-SATURDAY, ex-NERC-holiday.
        Explicitly NOT the 7x16 block (HE7-22, all seven days, holiday-blind)
        that this same API's Analytics room calls on-peak. Different blocks.

      * tb4 IS $/MWh, a MEAN-of-4 spread. `caiso_blocks_daily.tb4` is a SUM in
        $/MW-day, ~4x this number. Not comparable without dividing.

      * is_weekend IS NOT "no on-peak block". Saturday is both.

    GAPS ARE REAL. `days` bounds rows, not calendar days: you get the newest
    `days` rows the node HAS. `days_returned` sits beside `days_requested` and
    `window_served` reports the actual first/last date, so a node with six
    banked days answers with six rows and says so — no zero-fill, no
    interpolation, no padded date axis.

    BASIS IS NULL ON EVERY ROW TODAY, and that is upstream, not a bug here: the
    five th_hub nodes have zero DAM rows anywhere in the snapshot the producer
    is pinned to. `rules.basis` states it rather than hiding it.

    Errors: blank pnode_id -> 400; out-of-range days -> 400 naming the bound;
    DB down -> 503. A node with no banked rows is a 200 with days_returned: 0
    and the table's depth attached — never a 404.
    """
    assert _pool is not None

    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")
    if days < AN_DAILY_DAYS_MIN or days > AN_DAILY_DAYS_MAX:
        raise HTTPException(
            status_code=400,
            detail=(f"days must be between {AN_DAILY_DAYS_MIN} and "
                    f"{AN_DAILY_DAYS_MAX}; got {days}"))

    try:
        rows = await _an_read(_AN_DAILY_SQL, {"pnode_id": node, "days": days})
        depth_rows = await _an_read(_AN_DAILY_DEPTH_SQL, {})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    served = [_an_daily_row(r) for r in rows]
    window = (
        {"start": served[-1]["market_date"], "end": served[0]["market_date"]}
        if served else None
    )

    return {
        "pnode_id": node,
        "as_of": _utcnow().isoformat(),
        "depth": _an_daily_depth(depth_rows),
        "days_requested": days,
        "days_returned": len(served),
        "window_served": window,
        "order": "market_date DESC (newest first)",
        "unit": "$/MWh",
        "rules": AN_DAILY_RULES,
        "null_semantics_vocabulary": {
            "calendar": AN_DAILY_NULL_CALENDAR,
            "absent": AN_DAILY_NULL_ABSENT,
            "note": ("null_semantics is null for a value that is PRESENT — "
                     "there is nothing to explain about a number that exists"),
        },
        "days": served,
        "query_plan": {
            "index": "idx_node_daily_node_date (pnode_id, market_date DESC)",
            "shape": ("equality on the leading column + backwards range scan, "
                      f"LIMIT-bounded at {AN_DAILY_DAYS_MAX}"),
            "reads": 2,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# NODE HOURLY REGIME v0 — atlas_node_hourly_stats on the wire
# Node Analytics Package v2, lane ruled 2026-08-01.
#
#   GET /api/analytics/nodes/{pnode_id}/hourly?days=30      (days 7..90)
#
# ONE NODE, ONE WINDOW, EVERY DERIVATION DONE HERE. The dashboard computes
# nothing off this payload: the envelope, the ladder, the regime chips, the
# stance verdict, the outlier tally and the averages all arrive finished. A
# surface that re-derives any of them is a second truth, and this room has
# spent two lanes making sure there is only one.
#
# ── TWO STORES, AND THE SEAM BETWEEN THEM IS TODAY ──────────────────────────
# HISTORY comes from `atlas_node_hourly_stats` — the DURABLE per-(node, date,
# HE) record, PK (pnode_id, trade_date, he), so the window read is an
# index-prefix range scan BY CONSTRUCTION and not by anyone's good intentions.
#
# TODAY comes from `atlas_pnode_lmp_snapshot`, this node only, today's PT date
# only, markets DAM + RTPD only — bounded at ~120 rows. NEVER timeseries_values.
# The RT leg is RTPD by the captain's ruling of 2026-08-01; rt_n = 4 is a full
# hour, rt_n = 3 happens and is served AS-IS rather than dropped or scaled.
#
# WHY TWO STORES AND NOT ONE. The durable table DOES carry today's early hours —
# the nightly producer runs ahead of the day — but it carries them as of ITS
# last run, and it is stale within the hour. Measured on 2026-08-01 at 16:46Z:
# atlas_node_hourly_stats held today HE10 at rt_n = 2, while the snapshot held
# the same hour at rt_n = 3. Reading today from the bank would serve a number
# that is quietly one dispatch behind the one the desk is looking at. So the
# window read STOPS AT YESTERDAY and today is read live.
#
# THE CONSEQUENCE, STATED BECAUSE IT IS THE WHOLE POINT: today is NOT in the
# distribution it is being placed against. The regime chip ranks today's HE7
# against the banked HE7s of the previous `days` days and against nothing else.
# A value cannot be an outlier against a sample that contains it.
#
# ── THE WINDOW IS `days` CALENDAR DAYS ENDING YESTERDAY ─────────────────────
# lo = today_PT - days, hi = today_PT - 1. days=30 on 2026-08-01 asks for
# 2026-07-02..2026-07-31. The warehouse was seeded 2026-07-25, so seven of those
# thirty days exist and TWENTY-THREE DO NOT. That is not a failure and it is not
# hidden: `window.gap_days` names every absent date and `window.coverage` puts
# the fraction on the wire. The window fills by nightly cron and is complete on
# the node's first banked date + `days` — 2026-08-24 for a 30-day ask against a
# 2026-07-25 seed. `depth.accretion` says so in the payload.
#
# ── HONESTY MACHINERY IS LOAD-BEARING AT BIRTH ──────────────────────────────
# The warehouse holds eight dates against a thirty-day ask, so every honesty
# field on this endpoint is doing real work on day one rather than waiting to:
#
#   * PER-HE `days_present` + `coverage` ("7/30") ride on EVERY envelope row and
#     EVERY ladder row. Neither form can be read without its own depth.
#   * PERCENTILES COMPUTE OVER PRESENT DAYS ONLY. A missing day is never
#     interpolated, never zero-filled, never carried forward. n IS the n.
#   * PER-HE COVERAGE IS NOT UNIFORM and the payload proves it. The 2026-07-25
#     seed day banked DAM from HE11 and RTPD from HE10, so on that date HE1-9
#     have no row at all, HE10 has an RT-only row (dart NULL, rt_n honest), and
#     HE11-24 are whole. One node, three different per-HE depths, all stated.
#
# ── THE DEPTH FLOOR IS DELIBERATELY NOT APPLIED HERE ────────────────────────
# /api/analytics/node/{pnode_id}/ladder WITHHOLDS its percentile columns below
# AN_PERCENTILE_FLOOR (15) samples and serves min/mid/max instead. THIS ENDPOINT
# DOES NOT. The captain's ruling for v2 is that a ladder over fewer than ten
# days is served WITH its coverage and never suppressed: the number is real, and
# what is owed to the reader is its depth, not its absence.
#
# The two endpoints therefore answer differently at the same depth, ON PURPOSE.
# `ladder_depth_policy` on every response says which rule this payload followed,
# so a client holding both cannot mistake one for the other. Do not "fix" this
# by importing the floor; it was ruled out by name.
#
# ── THE RAW CELLS RIDE ALONG: `<metric>.history` ────────────────────────────
# Every other block on this payload is a derivation. `history` is not: it is
# the banked {trade_date, he, value} cells THEMSELVES, over the same window the
# envelope, ladder, regime rails and outlier rails were computed from.
#
# It exists because the dashboard draws a 30-day HE x date heat grid, and a
# grid cannot be reconstructed from percentiles. Without it the surface has to
# make a second query against a table THIS REQUEST ALREADY READ — so the cells
# are emitted from the rows already in hand. Zero new queries, zero new pool
# pressure, no change to `query_plan.reads`.
#
# ONLY PRESENT CELLS ARE LISTED. A row whose metric is NULL is simply absent
# from the list, which is the same filter the distributions are built through —
# so `history.n_cells` EQUALS `n_banked_cells` by construction, and the grid
# renders honest holes rather than fabricated zeros. Nothing is padded to a
# full 24 x days rectangle; the absences are the point.
#
# TODAY IS NOT IN IT. `history` is the banked window and stops at yesterday.
# Today's live partial line stays under `today`, where it cannot leak into a
# distribution or into a grid cell that claims to be settled.
#
# NO CAP AND NO PAGINATION. ~158 cells at the warehouse's current depth, ~720
# at a full 30-day node, 2,160 at the 90-day ceiling. Small enough to ship
# whole, and a truncated grid would be a lie about the warehouse.
#
# `basis.history` is the SAME call against the dark column: an empty list and
# n_cells 0 under the existing reason, lighting itself on hub arrival with no
# shape change and no version bump.
#
# ── BASIS IS BORN COMPLETE AND SERVED DARK ──────────────────────────────────
# `basis` carries the FULL shape — envelope, ladder, regime, stance, outliers,
# averages — computed by the SAME code path as dart, off
# `atlas_node_hourly_stats.basis_sp15`. That column is NULL on all 2,510,268
# rows today (the five th_hub nodes have no DAM anywhere in the snapshot the
# producer is pinned to), so every value comes out null and `basis.reason`
# states D-07-31-01.
#
# NOTHING ABOUT THAT IS HARDCODED. There is no `if metric == "basis": return
# nulls` branch — the block is data-driven, so the day the hub lands the shelf
# LIGHTS ITSELF with no code change and NO PAYLOAD VERSION BUMP. The one part
# that will not light on its own is `basis.averages.current_day`: today is read
# from a NODE-SCOPED snapshot query that carries no hub leg, so a same-day basis
# cannot be derived here at any depth. That is stated in the block rather than
# implied away.
#
# ── THE FOUR READS ──────────────────────────────────────────────────────────
# All EXPLAIN (ANALYZE)'d against the live pantry on 2026-08-01:
#
#   1. window   atlas_node_hourly_stats, PK prefix + date range. days=30 ->
#               Bitmap Index Scan on atlas_node_hourly_stats_pkey, 159 rows,
#               2.6 ms. A full 30-day node is 720 rows; the 90-day cap is 2,160.
#   2. today    atlas_pnode_lmp_snapshot, PK prefix. market IN (DAM, RTPD)
#               folds into the index cond as market = ANY(...) -> Index Scan on
#               atlas_pnode_lmp_snapshot_pkey, 50 rows, 21 ms cold.
#   3. depth    the node's OWN banked min/max/rows over all dates -> Index Only
#               Scan, Heap Fetches: 0, 6 ms cold. Node-scoped on purpose: see
#               below.
#   4. frontier the table-wide min/max — ONLY on the zero-row path, TTL-cached.
#
# READ 4 IS NOT ON THE HOT PATH AND THAT IS A MEASUREMENT, NOT A PREFERENCE.
# atlas_node_hourly_stats has exactly one index, the PK, which leads with
# pnode_id. A table-wide `min(trade_date), max(trade_date)` therefore plans a
# Parallel Seq Scan: 30,236 buffers, 219 ms. Adding `count(DISTINCT trade_date)`
# makes it a sort-and-aggregate over all 2.5 M rows — 5,329 ms and a 29 MB
# external merge to disk. Neither belongs on a request that is otherwise 30 ms.
# So the hot path reports the NODE's depth (read 3, index-only, exact), and the
# warehouse frontier is fetched ONLY when the node returned nothing at all —
# the one case where a client cannot otherwise tell "bad id" from "empty table"
# — and is cached for ANH_FRONTIER_CACHE_TTL seconds because it changes once a
# night. A node that IS present never pays for it.
#
# ── ERRORS ──────────────────────────────────────────────────────────────────
# days outside 7..90 -> 400 naming the bound and the value. Blank pnode_id ->
# 400. An UNKNOWN pnode_id is a 200 with an empty window and a depth block that
# says the node has no rows at any date — the /daily convention, never a 404 and
# never a fabricated flat line.
# ═══════════════════════════════════════════════════════════════════════════

ANH_TABLE = "atlas_node_hourly_stats"
ANH_TODAY_TABLE = "atlas_pnode_lmp_snapshot"

ANH_DAYS_DEFAULT = 30
ANH_DAYS_MIN = 7
ANH_DAYS_MAX = 90

# The two metrics, and the column each reads. `basis` is the dark shelf: the
# column exists, is NULL everywhere today, and lights on its own.
ANH_METRIC_COLUMN = {"dart": "dart", "basis": "basis"}

# The envelope's five bands. NOTE these are p10/p25/p50/p75/p90 — deliberately
# NOT the Nodes room's AN_ENVELOPE_QUANTILES (p5/p25/p50/p75/p95). Different
# form, different tails; the payload names its own.
ANH_ENVELOPE_QUANTILES = (("p10", 0.10), ("p25", 0.25), ("p50", 0.50),
                          ("p75", 0.75), ("p90", 0.90))

# The ladder's nine columns ARE the Regime room's — same constant, so the two
# ladders in this API cannot drift into different columns.
ANH_LADDER_QUANTILES = AN_LADDER_QUANTILES

# The outlier rails.
ANH_OUTLIER_LO_Q = 0.05
ANH_OUTLIER_HI_Q = 0.95
# The cell list is capped so a 90-day window cannot ship 2,160 objects. The cap
# is STATED and the count is never truncated with it (see `_anh_outliers`).
ANH_OUTLIER_CELL_CAP = 100

# The regime chip's bins, on the rank rounded to whole percent — the same bins
# and the same rounding as the Regime room's chip, reusing its constants so the
# two chips cannot disagree about what HIGH means.
ANH_REGIME_HIGH_PCT = AN_REGIME_HIGH_PCT
ANH_REGIME_LOW_PCT = AN_REGIME_LOW_PCT
ANH_REGIME_RULE = (
    f"today's value at this HE ranked against the SAME HE's banked window "
    f"(percentile_cont inverse, rounded to whole percent); today is not in that "
    f"sample. pct >= {ANH_REGIME_HIGH_PCT} HIGH, pct <= {ANH_REGIME_LOW_PCT} "
    f"LOW, else MID"
)

# The stance thresholds. This is v2's own counted rule and it is NOT the Nodes
# room's `_an_stance` majority-of-extremes tally — different arithmetic, stated
# on the wire so the two verdicts are never read as one.
ANH_STANCE_BULL_TOP = 6
ANH_STANCE_BEAR_TOP = 2
ANH_STANCE_BEAR_BOTTOM = 6
ANH_STANCE_RULE = (
    f"BULLISH when >= {ANH_STANCE_BULL_TOP} of the day's hours place in the top "
    f"quartile (chip HIGH); BEARISH when <= {ANH_STANCE_BEAR_TOP} place top AND "
    f">= {ANH_STANCE_BEAR_BOTTOM} place bottom (chip LOW); otherwise NEUTRAL. "
    f"Counted over the hours today has actually banked, never over an assumed 24"
)

# The ladder policy divergence, on every response. See the header.
ANH_LADDER_DEPTH_POLICY = (
    "served at every depth WITH its per-row coverage; percentile columns are "
    "never withheld. This deliberately DIFFERS from "
    "/api/analytics/node/{pnode_id}/ladder, which withholds percentile columns "
    f"below {AN_PERCENTILE_FLOOR} samples and serves min/mid/max instead. Ruled "
    "for v2 on 2026-08-01: the number is real and what is owed is its depth, "
    "not its absence"
)

ANH_PARTIAL_RULE = (
    "rows under `today` are live reads of the dispatch snapshot and carry "
    "partial: true. The list ENDS at the last hour either leg has banked - there "
    "is no projection, no padding to HE24, and no carry-forward"
)

# The raw cell set. One line on the wire, because the block it labels is the
# only non-derivation on the payload and a reader needs to know why it is there
# and what its absences mean. `{metric}` is filled per block.
ANH_HISTORY_RULE = (
    "banked (trade_date, HE) {metric} cells over the SAME window every "
    "derivation on this block used - a cell absent here is a cell absent in the "
    "warehouse, never interpolated and never padded to a full grid. Today is "
    "never in this list; it lives under `today`"
)

ANH_BASIS_DARK_REASON = "TH-hub DAM ingest pending (D-07-31-01)"
ANH_BASIS_SOURCE_NOTE = (
    "basis reads atlas_node_hourly_stats.basis_sp15 through the SAME code path "
    "as dart - there is no null-returning branch. The block lights itself when "
    "the column banks, with no payload version bump"
)
ANH_BASIS_TODAY_NOTE = (
    "basis.averages.current_day and basis.regime cannot light from this "
    "endpoint at any depth: today is read from a node-scoped snapshot query "
    "that carries no hub leg. A same-day basis needs a hub read this route does "
    "not make. This is the one part of the dark shelf that hub arrival will NOT "
    "switch on by itself"
)
# The per-row version of the note above. Short enough to sit on 24 rows.
ANH_BASIS_NO_CURRENT = (
    "no same-day basis: today's read is node-scoped and carries no hub leg"
)

# dow convention, pinned here because three of them exist in this codebase.
ANH_DOW_RULE = ("dow is 0=Monday .. 6=Sunday - Python's date.weekday(). NOT "
                "isoweekday (1-7) and NOT Postgres EXTRACT(DOW) (0=Sunday)")

ANH_DART_SIGN_RULE = (
    "dart = rt_avg - dam_lmp. SIGN IS PINNED: positive = RT above DA. Banked "
    "rows carry the producer's own `dart` column verbatim; today's rows recompute "
    "it from the snapshot's two legs by the identical expression. The OPPOSITE "
    "sign ships from /api/timeseries/caiso-hub-lmp under the field name `spread`"
)

ANH_HUB_PAIRING_RULE = (
    "identity.pairing is CAISO's own node->trading-hub attribution, read "
    "verbatim from atlas_pnode_universe.th_hub - never inferred from "
    "geography, area, or node name. Four verdicts, and they are NOT "
    "interchangeable: paired (a hub with a banked price series), "
    "paired_unpriceable (CAISO pairs it, the pantry has no series), unpaired "
    "(no trading hub attributed in the universe table - the common case), and "
    "not_in_universe (no universe row at all, so nothing is claimed either "
    "way). identity.fleet carries the counts behind 'common case', measured "
    "off the same table on the same request. A payload with NO identity block "
    "is an older build of this endpoint: that is a fact about the feed, not "
    "about the node, and a client must not render it as an unpaired node"
)

ANH_HE_RULE = (
    "he is the stored hour-ending, verbatim from atlas_node_hourly_stats.he and "
    "atlas_pnode_lmp_snapshot.market_hour - never renumbered here. The per-HE "
    "axis is HE1-24 UNION every hour either store actually carries, so a "
    "DST fall-back HE25 appears instead of being dropped"
)

# The warehouse frontier changes once a night and costs a 219 ms parallel seq
# scan, so it is cached. Only the zero-row path ever asks for it.
ANH_FRONTIER_CACHE_TTL = 300.0
_anh_frontier_cache: "dict[str, tuple[float, dict]]" = {}

# The pairing census is an aggregate over the 24,098-row universe (3.8 ms
# measured). It rides on every request rather than only the empty ones, so it
# is cached for longer than the frontier — the universe table is a nightly
# artifact, not a live one, and a stale-by-a-minute fleet count would be worse
# than useless: it is the sentence a hub-less node's page is built on.
ANH_CENSUS_CACHE_TTL = 900.0
_anh_census_cache: "dict[str, tuple[float, dict]]" = {}


# ── SQL: four shapes, three of them index-prefix reads on a PK ──────────────

# 1. The window. pnode_id equality + a trade_date range against PK
#    (pnode_id, trade_date, he) — an index-prefix scan by construction. 720 rows
#    at days=30, 2,160 at the cap.
_ANH_WINDOW_SQL = """
    SELECT trade_date, he, dam_lmp, rt_avg, rt_n, dart, basis_sp15
    FROM atlas_node_hourly_stats
    WHERE pnode_id = %(pnode_id)s
      AND trade_date >= %(lo)s
      AND trade_date <= %(hi)s
    ORDER BY trade_date, he
"""

# 2. Today's partial. THIS node, TODAY's PT date, DAM + RTPD only. RTD is
#    excluded by the house axiom and timeseries_values is never touched here.
#    market IN (...) folds into the PK index cond as market = ANY(...).
_ANH_TODAY_SQL = """
    SELECT market, market_hour, market_interval, lmp
    FROM atlas_pnode_lmp_snapshot
    WHERE pnode_id = %(pnode_id)s
      AND market_date = %(today)s
      AND market IN (%(da)s, %(rt)s)
    ORDER BY market, market_hour, market_interval
"""

# 3. The node's own banked depth over ALL dates, not just the window — so a
#    30-day ask can report that the node holds eight days total. Index Only
#    Scan on the PK prefix, Heap Fetches: 0.
_ANH_NODE_DEPTH_SQL = """
    SELECT min(trade_date) AS min_date,
           max(trade_date) AS max_date,
           count(*)        AS rows
    FROM atlas_node_hourly_stats
    WHERE pnode_id = %(pnode_id)s
"""

# 4. The table-wide frontier. Parallel Seq Scan, 219 ms — read ONLY when the
#    node returned nothing at all, and cached. See the header.
_ANH_FRONTIER_SQL = """
    SELECT min(trade_date) AS min_date,
           max(trade_date) AS max_date
    FROM atlas_node_hourly_stats
"""

# 5. The node's HUB PAIRING. `atlas_node_hourly_stats` carries no hub column,
#    so this lane must read the attribution where it lives —
#    atlas_pnode_universe.th_hub, the same column and the same rule the node
#    package uses. Unique on pnode_id (24,098 rows / 24,098 distinct, verified
#    2026-08-01), so no fan-out is possible.
#
#    The VALUES anchor is load-bearing, exactly as in _AN_IDENTITY_SQL: exactly
#    one row always comes back, so "no row in the universe" arrives as
#    in_universe=false on a present row rather than as an empty result that
#    could equally mean the DB gave us nothing. This endpoint serves an unknown
#    pnode_id as a 200, so that distinction has nowhere else to be made.
_ANH_IDENT_SQL = """
    SELECT
        t.pnode_id,
        (u.pnode_id IS NOT NULL) AS in_universe,
        u.th_hub
    FROM (VALUES (%(pnode_id)s::text)) AS t(pnode_id)
    LEFT JOIN atlas_pnode_universe u ON u.pnode_id = t.pnode_id
"""

# 6. The fleet census behind the pairing sentence. Aggregate over a Seq Scan of
#    24,098 rows — 3.8 ms measured, and cached, so the common request pays
#    nothing. Grouped by th_hub rather than counted with a hardcoded hub list:
#    the priceable/unpriceable split is folded in Python against
#    AN_TH_HUB_TO_ZONE, so the numbers follow the universe table and the
#    pairing map instead of being restated in SQL.
_ANH_CENSUS_SQL = """
    SELECT th_hub, count(*) AS n
    FROM atlas_pnode_universe
    GROUP BY th_hub
"""


def _anh_coverage(days_present: int, requested: int) -> str:
    """The per-HE depth string, e.g. "7/30". Present days over days ASKED FOR —
    never over days available, which would always read 100%."""
    return f"{days_present}/{requested}"


def _anh_norm_window(rows) -> list:
    """Banked rows -> plain dicts with `he` and `rt_n` as real ints.

    The int() casts are the contract rule "dow/HE integers never cross a
    language boundary" being enforced at the seam rather than trusted: psycopg
    hands back smallint as int today, and this keeps that true if it ever
    doesn't. NULL propagates through _an_f — nothing is coalesced to 0.
    """
    out = []
    for r in rows:
        out.append({
            "date": _an_as_date(r["trade_date"]),
            "he": int(r["he"]),
            "dam_lmp": _an_f(r["dam_lmp"]),
            "rt_avg": _an_f(r["rt_avg"]),
            "rt_n": int(r["rt_n"]),
            "dart": _an_f(r["dart"]),
            "basis": _an_f(r["basis_sp15"]),
        })
    return out


def _anh_by_he(banked, key: str) -> dict:
    """{he: [(date, value)]} over rows where `key` is PRESENT, dates ascending.

    Absent values are DROPPED, not zeroed and not interpolated — so the list
    length IS days_present for that HE, and every percentile downstream is
    computed over exactly the days that exist.
    """
    out: dict = defaultdict(list)
    for r in banked:
        v = r[key]
        if v is None:
            continue
        out[r["he"]].append((r["date"], v))
    return out


def _anh_today_rows(rows) -> list:
    """Snapshot rows -> today's per-HE line, ending at the last banked hour.

    The RTPD hourly price is the straight mean of the intervals PRESENT in that
    hour and `rt_n` is how many there were — 4 is a full hour, 3 happens and is
    served as-is rather than scaled or dropped. DAM is one print per hour;
    `dam_n` rides along so a surprise would be visible rather than silent.

    dart is computed ONLY when both legs exist. An RT-only hour is an honest
    null with its rt_n attached, never a half-computed number.
    """
    dam: dict = {}
    dam_n: dict = defaultdict(int)
    rt: dict = defaultdict(list)
    for r in rows:
        v = _an_f(r["lmp"])
        if v is None:
            continue
        he = int(r["market_hour"])
        if r["market"] == AN_DA_MARKET:
            dam[he] = v
            dam_n[he] += 1
        else:
            rt[he].append(v)

    out = []
    for he in sorted(set(dam) | set(rt)):
        iv = rt.get(he, [])
        rt_avg = (sum(iv) / len(iv)) if iv else None
        da = dam.get(he)
        dart = (rt_avg - da) if (rt_avg is not None and da is not None) else None
        out.append({
            "he": he,
            "dam_lmp": _an_r(da),
            "dam_n": int(dam_n.get(he, 0)),
            "rt_avg": _an_r(rt_avg),
            "rt_n": len(iv),
            "dart": _an_r(dart),
            "basis": None,
            "partial": True,
        })
    return out


def _anh_envelope_row(he: int, vals, requested: int) -> dict:
    """One HE's envelope band. Coverage rides ON the row, never beside it."""
    row = {
        "he": he,
        "days_present": len(vals),
        "coverage": _anh_coverage(len(vals), requested),
    }
    s = sorted(vals)
    for name, q in ANH_ENVELOPE_QUANTILES:
        row[name] = _an_r(_an_percentile_cont(s, q)) if s else None
    row["mean"] = _an_r(_an_mean(s)) if s else None
    return row


def _anh_ladder_row(he: int, vals, requested: int) -> dict:
    """One HE's nine-column ladder.

    NO DEPTH FLOOR. The columns are present at every n >= 1 and absent only when
    the HE has nothing banked. `days_present` and `coverage` are on the row so a
    P99 over four days is readable AS a P99 over four days. See the header for
    why this diverges from the Regime room's ladder on purpose.
    """
    row = {
        "he": he,
        "days_present": len(vals),
        "coverage": _anh_coverage(len(vals), requested),
    }
    s = sorted(vals)
    for name, q in ANH_LADDER_QUANTILES:
        row[name] = _an_r(_an_percentile_cont(s, q)) if s else None
    return row


def _anh_regime_row(he: int, vals, current, requested: int,
                    no_current_reason: str) -> dict:
    """Where today's value at this HE sits inside that HE's OWN banked window.

    The chip is the brief's {chip, pct}; `days_present`/`coverage` ride with it
    because a P83 against six days is a different claim from a P83 against
    ninety, and the chip alone cannot say which it is.

    Two honest absences, distinguished rather than merged: today has no value at
    this hour, or the window has nothing to rank it against. `no_current_reason`
    is the caller's word for the first one, because "not yet" is true for dart
    and a FALSE PROMISE for basis — today's read is node-scoped and carries no
    hub leg, so a same-day basis is not late, it is not coming.
    """
    row = {
        "he": he,
        "current": _an_r(current),
        "days_present": len(vals),
        "coverage": _anh_coverage(len(vals), requested),
        "chip": None,
        "pct": None,
        "reason": None,
    }
    if current is None:
        row["reason"] = no_current_reason
        return row
    if not vals:
        row["reason"] = "no banked day at this hour in the window to rank against"
        return row
    rank = _an_percentile_rank(sorted(vals), current)
    pct = int(round(rank * 100))
    row["pct"] = pct
    row["chip"] = ("HIGH" if pct >= ANH_REGIME_HIGH_PCT
                   else "LOW" if pct <= ANH_REGIME_LOW_PCT else "MID")
    return row


def _anh_stance(regime_rows) -> dict:
    """v2's counted verdict. The arithmetic ships WITH the word, always.

    NOTHING PLACED IS NOT NEUTRAL. With no chips the bare rule would return
    NEUTRAL (0 top, 0 bottom), which would assert a reading nobody took — so
    that path returns verdict null with a reason instead.
    """
    top = sum(1 for r in regime_rows if r["chip"] == "HIGH")
    bottom = sum(1 for r in regime_rows if r["chip"] == "LOW")
    placed = sum(1 for r in regime_rows if r["chip"] is not None)
    if placed == 0:
        return {
            "verdict": None,
            "n_top_quartile": 0,
            "n_bottom_quartile": 0,
            "n_placed": 0,
            "available": False,
            "reason": ("no hour could be placed: today has no value, or the "
                       "window has nothing to rank it against"),
            "rule": ANH_STANCE_RULE,
            "line": None,
        }
    verdict = ("BULLISH" if top >= ANH_STANCE_BULL_TOP
               else "BEARISH" if (top <= ANH_STANCE_BEAR_TOP
                                  and bottom >= ANH_STANCE_BEAR_BOTTOM)
               else "NEUTRAL")
    return {
        "verdict": verdict,
        "n_top_quartile": top,
        "n_bottom_quartile": bottom,
        "n_placed": placed,
        "available": True,
        "reason": None,
        "rule": ANH_STANCE_RULE,
        "line": f"{top}h top quartile / {bottom}h bottom of {placed}h placed -> {verdict}",
    }


def _anh_outliers(by_he, requested: int) -> dict:
    """(day, HE) cells beyond that HE's OWN p05/p95 rails, over the window.

    THE RAW COUNT IS AN ARTIFACT AT THIN DEPTH AND THE PAYLOAD SAYS SO.
    percentile_cont interpolates, so for any HE with n >= 2 distinct values p05
    sits STRICTLY ABOVE the sample minimum and p95 strictly below the maximum —
    which makes that HE's min and max outliers BY CONSTRUCTION, not by market
    behaviour. Over 24 hours of a thin window that is ~48 cells of pure
    arithmetic.

    So two numbers ship. `n_outliers` is the count the brief asked for, computed
    exactly as specified. `n_outliers_interior` excludes cells that are their own
    HE's minimum or maximum — at thin depth it is 0, which is the honest reading,
    and it becomes the real signal as the window deepens.

    The cell list is capped at ANH_OUTLIER_CELL_CAP; the COUNTS are never capped
    and `cells_truncated` states when the list is short of them.
    """
    cells = []
    scanned = below = above = interior = 0
    for he in sorted(by_he):
        vals = [v for _d, v in by_he[he]]
        if not vals:
            continue
        s = sorted(vals)
        scanned += len(s)
        if len(s) < 2:
            continue                      # one sample has no interior to exceed
        lo = _an_percentile_cont(s, ANH_OUTLIER_LO_Q)
        hi = _an_percentile_cont(s, ANH_OUTLIER_HI_Q)
        vmin, vmax = s[0], s[-1]
        for d, v in by_he[he]:
            side = "below_p05" if v < lo else "above_p95" if v > hi else None
            if side is None:
                continue
            if side == "below_p05":
                below += 1
            else:
                above += 1
            is_extreme = (v == vmin or v == vmax)
            if not is_extreme:
                interior += 1
            if len(cells) < ANH_OUTLIER_CELL_CAP:
                cells.append({
                    "trade_date": d.isoformat(),
                    "he": he,
                    "value": _an_r(v),
                    "side": side,
                    "is_he_extreme": is_extreme,
                })
    total = below + above
    return {
        "n_outliers": total,
        "n_outliers_interior": interior,
        "below_p05": below,
        "above_p95": above,
        "cells_scanned": scanned,
        "cells": cells,
        "cells_truncated": total > len(cells),
        "cell_cap": ANH_OUTLIER_CELL_CAP,
        "requested_days": requested,
        "rule": ("a banked (day, HE) cell strictly beyond that HE's own p05 or "
                 "p95 over this window; rails are computed per HE over present "
                 "days only"),
        "interpretation": (
            "n_outliers is the literal count and at thin depth it is mostly "
            "arithmetic: percentile_cont puts p05 strictly above the sample "
            "minimum for any HE with 2+ distinct values, so that HE's min and "
            "max are beyond their own rails by construction. Read "
            "n_outliers_interior - which excludes each HE's own extremes - as "
            "the behavioural signal, and read both against days_present"),
    }


def _anh_averages(by_he, today_rows, key: str) -> dict:
    """Current-day and window means for one metric.

    The window mean is over BANKED cells only and does not include today — the
    same exclusion the regime chip rides on, so the two numbers describe the
    same two populations. Empty in, null out; never 0.
    """
    win = [v for he in by_he for _d, v in by_he[he]]
    cur = [r[key] for r in today_rows if r.get(key) is not None]
    return {
        "current_day": _an_r(_an_mean(cur)) if cur else None,
        "current_day_n": len(cur),
        "window": _an_r(_an_mean(win)) if win else None,
        "window_n": len(win),
    }


def _anh_history(banked, key: str, metric: str) -> dict:
    """The raw banked cells every derivation on this block was computed from.

    THE ONE NON-DERIVATION ON THE PAYLOAD, and it is here for exactly one
    reason: the dashboard's HE x date heat grid needs the cells themselves, and
    the alternative is a second query against a table this request has already
    read. So it is emitted from `banked` — the rows already in hand for the
    envelope, the ladder and the outliers — at ZERO additional reads and zero
    additional pool pressure.

    ONLY PRESENT CELLS ARE LISTED. A null-metric row is simply absent, so the
    grid renders an honest hole instead of a fabricated zero. That is the same
    filter `_anh_by_he` applies to build the distributions, which is why
    `n_cells` equals this block's `n_banked_cells` BY CONSTRUCTION and not by
    coincidence — the two count the same cells through the same test.

    There is no cap and no pagination. A 30-day node is ~720 cells at full
    depth and the 90-day ceiling is 2,160; the list is small enough to ship
    whole, and a truncated grid would be a lie about the warehouse.

    `banked` stops at yesterday by construction of the window read, so today
    can never appear here — it is served under `today`, live and partial.

    Ordering is the window read's own ORDER BY trade_date, he.
    """
    cells = [{"trade_date": r["date"].isoformat(),
              "he": r["he"],
              "value": _an_r(r[key])}
             for r in banked if r[key] is not None]
    return {
        "cells": cells,
        "n_cells": len(cells),
        "rule": ANH_HISTORY_RULE.format(metric=metric),
    }


def _anh_metric_block(metric: str, key: str, banked, today_rows,
                      hours, requested: int) -> dict:
    """One metric's whole shelf: envelope, ladder, regime, stance, outliers,
    averages — every one of them over the SAME per-HE window.

    THE SHAPE IS IDENTICAL FOR BOTH METRICS AND IS BUILT BY THIS FUNCTION ONLY.
    `basis` is not a special case with hardcoded nulls; it is this function
    called against a column that is currently NULL everywhere, which produces
    the complete shape with null values and `available: false`. When the column
    banks, the same call produces numbers. No branch, no version bump.
    """
    by_he = _anh_by_he(banked, key)
    today_by_he = {r["he"]: r.get(key) for r in today_rows}
    # "not yet" for dart is a wait; for basis it would be a promise this route
    # cannot keep. See ANH_BASIS_TODAY_NOTE.
    no_current = (ANH_BASIS_NO_CURRENT if metric == "basis"
                  else "today has no value at this hour yet")

    envelope, ladder, regime = [], [], []
    for he in hours:
        vals = [v for _d, v in by_he.get(he, [])]
        envelope.append(_anh_envelope_row(he, vals, requested))
        ladder.append(_anh_ladder_row(he, vals, requested))
        regime.append(_anh_regime_row(he, vals, today_by_he.get(he), requested,
                                      no_current))

    n_present = sum(len(v) for v in by_he.values())
    block = {
        "metric": metric,
        "source_column": f"{ANH_TABLE}.{'basis_sp15' if metric == 'basis' else 'dart'}",
        "available": n_present > 0,
        "reason": None,
        "n_banked_cells": n_present,
        "envelope": envelope,
        "ladder": ladder,
        "regime": regime,
        "stance": _anh_stance(regime),
        "outliers": _anh_outliers(by_he, requested),
        "averages": _anh_averages(by_he, today_rows, key),
        # The raw cells the six blocks above were derived from. Data-driven
        # like the rest of this function: for `basis` the same call returns an
        # empty list and n_cells 0 under the existing `reason`, and lights
        # itself when the column banks.
        "history": _anh_history(banked, key, metric),
    }
    if metric == "basis":
        block["source_note"] = ANH_BASIS_SOURCE_NOTE
        block["current_day_note"] = ANH_BASIS_TODAY_NOTE
        if n_present == 0:
            block["reason"] = ANH_BASIS_DARK_REASON
    elif n_present == 0:
        block["reason"] = ("no banked day in this window carries both a DAM "
                           "print and an RTPD mean for this node")
    return block


def _anh_window_block(banked, lo, hi, requested: int) -> dict:
    """The honesty block: what was asked for, what exists, and what is missing.

    `gap_days` names EVERY requested date this node has no row for — including
    the ones that predate the warehouse, because a client asking for 30 days is
    owed the whole answer, not the flattering part of it.

    `gap_days_interior` is the subset falling BETWEEN the first and last banked
    date. Those are the ones that mean something went wrong: a leading run of
    absent dates is the warehouse still accreting, an interior hole is a night
    the producer missed. Two different facts, two different lists.
    """
    dates = sorted({r["date"] for r in banked})
    present = set(dates)
    expected = [lo + _timedelta(days=i) for i in range((hi - lo).days + 1)]
    gaps = [d for d in expected if d not in present]
    interior = ([d for d in gaps if dates[0] < d < dates[-1]] if dates else [])
    return {
        "requested_days": requested,
        "days_banked": len(dates),
        "coverage": _anh_coverage(len(dates), requested),
        "requested_start": lo.isoformat(),
        "requested_end": hi.isoformat(),
        "first_trade_date": dates[0].isoformat() if dates else None,
        "last_trade_date": dates[-1].isoformat() if dates else None,
        "gap_days": [d.isoformat() for d in gaps],
        "gap_days_interior": [d.isoformat() for d in interior],
        "banked_dates": [{"trade_date": d.isoformat(), "dow": d.weekday()}
                         for d in dates],
        "dow_rule": ANH_DOW_RULE,
        "gap_rule": (
            "gap_days is every requested date with no banked row for this node. "
            "gap_days_interior is the subset between the first and last banked "
            "date - a leading run is the warehouse still accreting, an interior "
            "hole is a night the producer missed. Neither is ever filled"),
        "window_rule": (
            "the window is `days` calendar days ENDING YESTERDAY (PT). Today is "
            "served separately and live, and is deliberately not in the "
            "distribution it is placed against"),
    }


async def _anh_frontier() -> dict:
    """The table-wide banked frontier, TTL-cached.

    Called ONLY when a node returned no rows at all — the one case where a
    client cannot tell an unknown id from an empty warehouse. It is a 219 ms
    parallel seq scan (the table's only index leads with pnode_id), so it is
    cached for ANH_FRONTIER_CACHE_TTL and never touches the hot path.
    """
    hit = _anh_frontier_cache.get("all")
    now = time.monotonic()
    if hit and (now - hit[0]) < ANH_FRONTIER_CACHE_TTL:
        return {**hit[1], "cached": True}
    rows = await _an_read(_ANH_FRONTIER_SQL, {})
    r = rows[0] if rows else {}
    mn, mx = _an_as_date(r.get("min_date")), _an_as_date(r.get("max_date"))
    out = {
        "min_trade_date": mn.isoformat() if mn else None,
        "max_trade_date": mx.isoformat() if mx else None,
        "measured_cost": ("parallel seq scan, ~219 ms - the table's only index "
                          "is the PK and it leads with pnode_id, so there is no "
                          "index path to a table-wide date range"),
        "cache_ttl_seconds": ANH_FRONTIER_CACHE_TTL,
    }
    _anh_frontier_cache["all"] = (now, out)
    return {**out, "cached": False}


async def _anh_census() -> dict:
    """The universe-wide pairing census, TTL-cached.

    THE FLEET TRUTH, COUNTED RATHER THAN ASSERTED. A node page that says "no
    trading hub attributed" is stating the common case, and the page can only
    say so honestly if it knows how common. These are the counts, read from the
    table on the same request that reads the node's own row:

        24,098 nodes in atlas_pnode_universe
         2,277 carry a th_hub      (1,984 of them priceable: NP15/SP15/ZP26)
        21,821 carry none          (the answer for ~91% of the universe)

    Those figures are the measurement on 2026-08-01, written here for the
    reader only — NOTHING in this function or on the payload is hardcoded to
    them. The counts are grouped in SQL and split against AN_TH_HUB_TO_ZONE, so
    the day CAISO attributes another node or the pantry banks another hub
    series, the sentence on the page moves with it.
    """
    hit = _anh_census_cache.get("all")
    now = time.monotonic()
    if hit and (now - hit[0]) < ANH_CENSUS_CACHE_TTL:
        return {**hit[1], "cached": True}

    rows = await _an_read(_ANH_CENSUS_SQL, {})
    by_hub = {r["th_hub"]: int(r["n"] or 0) for r in rows}
    total = sum(by_hub.values())
    without = by_hub.get(None, 0)
    with_hub = total - without
    priceable = sum(n for h, n in by_hub.items()
                    if h and h in AN_TH_HUB_TO_ZONE)

    out = {
        "universe_nodes": total,
        "with_hub": with_hub,
        "without_hub": without,
        "with_hub_priceable": priceable,
        "with_hub_unpriceable": with_hub - priceable,
        "by_th_hub": {h: n for h, n in sorted(
            ((h, n) for h, n in by_hub.items() if h), key=lambda kv: -kv[1])},
        "source": "atlas_pnode_universe.th_hub",
        "cache_ttl_seconds": ANH_CENSUS_CACHE_TTL,
    }
    _anh_census_cache["all"] = (now, out)
    return {**out, "cached": False}


def _anh_identity_block(ident_rows, census) -> dict:
    """The node's hub attribution — the block this endpoint used to omit.

    Four verdicts, and the difference between them is the whole point. Read
    `pairing` and nothing else to decide what to render:

      paired              -> `th_hub` is CAISO's label, `hub` its priced zone
      paired_unpriceable  -> CAISO pairs it; the pantry banks no series
      unpaired            -> no trading hub attributed in the universe table
      not_in_universe     -> the node has no universe row; nothing is claimed

    `reason` is this lane's sentence for each, served rather than composed by
    the client, so the page never has to invent words for an absence. A client
    that finds NO identity block at all is talking to an older build and should
    say so — "this payload carries no hub" is a fact about the feed, and it is
    a different sentence from "this node has no hub", which is a fact about the
    node. Only one of them is true per node, and the block is what tells them
    apart.
    """
    r = ident_rows[0] if ident_rows else {}
    in_universe = bool(r.get("in_universe"))
    pairing = _an_pair_hub(r.get("th_hub"), in_universe=in_universe)

    if pairing["pairing"] == "paired":
        reason = (f"CAISO attributes this node to {pairing['th_hub']}, and the "
                  f"pantry banks that hub's price series")
    elif pairing["pairing"] == "paired_unpriceable":
        reason = (f"CAISO attributes this node to {pairing['th_hub']}, which "
                  f"has no banked hub price series")
    elif pairing["pairing"] == "unpaired":
        reason = ("no trading hub attributed in the universe table - "
                  f"{census['without_hub']:,} of {census['universe_nodes']:,} "
                  f"nodes are in the same position, and it is CAISO's answer "
                  f"rather than a gap")
    else:
        reason = ("this node has no row in atlas_pnode_universe, so CAISO "
                  "states no trading hub attribution for it either way")

    return {
        "in_universe": in_universe,
        **pairing,
        "reason": reason,
        "source": "atlas_pnode_universe.th_hub",
        "fleet": census,
    }


def _anh_depth_block(node_depth, requested: int, frontier) -> dict:
    """What this node holds, what the window needs, and when it will be full.

    The accretion date is the node's OWN first banked date + `days`: at that
    point the window [today-days, today-1] sits entirely inside banked territory.
    A 30-day ask against a 2026-07-25 seed completes on 2026-08-24.
    """
    r = node_depth[0] if node_depth else {}
    mn, mx = _an_as_date(r.get("min_date")), _an_as_date(r.get("max_date"))
    rows = int(r.get("rows") or 0)
    complete_on = (mn + _timedelta(days=requested)) if mn else None
    today = _utcnow().astimezone(AN_PT).date()
    block = {
        "source": ANH_TABLE,
        "today_source": ANH_TODAY_TABLE,
        "retention": (
            "durable; derived nightly from atlas_pnode_lmp_snapshot. Today's "
            "partial is read live from that ~7-day dispatch-only hot tier "
            "instead, because the nightly bank is stale within the hour"),
        "node_present": rows > 0,
        "node_rows": rows,
        "node_min_trade_date": mn.isoformat() if mn else None,
        "node_max_trade_date": mx.isoformat() if mx else None,
        "scope_note": ("min/max/rows are THIS NODE's, over all dates - an "
                       "index-only read on the PK prefix. A table-wide date "
                       "range has no index path and is fetched only when a node "
                       "returns nothing"),
        "accretion": {
            "requested_days": requested,
            "window_complete": bool(complete_on and complete_on <= today),
            "window_complete_on": complete_on.isoformat() if complete_on else None,
            "note": (
                f"the {requested}-day window fills by nightly cron. It is "
                f"complete once this node's earliest banked date is {requested} "
                f"days behind today"
                + (f" - {complete_on.isoformat()} for a first banked date of "
                   f"{mn.isoformat()}" if complete_on else
                   "; this node has banked nothing, so there is no date to give")),
        },
    }
    if rows == 0:
        block["node_absent_note"] = (
            f"pnode_id has no row in {ANH_TABLE} at ANY date. This is a 200 with "
            f"an empty window, not a 404 - the house convention. The warehouse "
            f"frontier below distinguishes an unknown id from an empty table")
        block["warehouse_frontier"] = frontier
    return block


@app.get("/api/analytics/nodes/{pnode_id}/hourly")
async def analytics_nodes_hourly(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. HPLNDJT_6_N001. Unknown -> 200 with an empty window.",
    ),
    days: int = Query(
        default=ANH_DAYS_DEFAULT,
        description=(
            f"Length of the banked window in calendar days, "
            f"{ANH_DAYS_MIN}..{ANH_DAYS_MAX}. The window ENDS YESTERDAY (PT); "
            f"today is served separately and live."
        ),
    ),
):
    """
    The node's hourly regime — today's shape placed against its own banked window.

    Per hour-ending, over the last `days` calendar days ending YESTERDAY:
    the p10-p90 envelope, the nine-column p01-p99 ladder, and the REGIME CHIP
    placing today's value at that hour inside that hour's own history. Plus the
    counted stance verdict, the outlier tally against per-HE p05/p95 rails, and
    the current-day and window averages. Nothing is left for the client to
    compute.

    TWO STORES, SEAMED AT TODAY. History is `atlas_node_hourly_stats`, the
    durable per-(node, date, HE) record. Today is read LIVE from
    `atlas_pnode_lmp_snapshot` — this node, today's PT date, DAM + RTPD only,
    ~120 rows. The durable table does carry today's early hours, but as of its
    last nightly run: on 2026-08-01 it held HE10 at rt_n=2 while the snapshot
    held rt_n=3. So the window stops at yesterday and today is read fresh.

    TODAY IS NOT IN THE DISTRIBUTION IT IS PLACED AGAINST. The chip on HE7 ranks
    today's HE7 against the banked HE7s and against nothing else. Today's rows
    carry `partial: true` and their `rt_n` (4 is a full hour; 3 happens and is
    served as-is), and the line ENDS at the last banked hour — no projection.

    HONESTY IS ON EVERY ROW, and at today's depth it is all load-bearing: the
    warehouse holds eight dates against a thirty-day ask. Every envelope and
    ladder row carries `days_present` and `coverage` ("7/30"). Percentiles
    compute over present days ONLY — never interpolated, never zero-filled.
    `window` names every absent date, separating the leading run (the warehouse
    still accreting) from interior holes (a night the producer missed), and
    `depth.accretion` says when the window fills.

    THE DEPTH FLOOR IS NOT APPLIED HERE, ON PURPOSE. A ladder over fewer than
    ten days is served WITH its coverage and never suppressed — the number is
    real and what is owed is its depth. This DIFFERS from
    /api/analytics/node/{pnode_id}/ladder, which withholds percentile columns
    below 15 samples. `ladder_depth_policy` on every response says which rule
    this payload followed.

    THE HUB IS ON THE PAYLOAD, which it was not before 2026-08-01.
    `atlas_node_hourly_stats` has no hub column, so this endpoint read four
    tables that all lack the attribution and served a package with no hub in
    it at all — and a node page reading only this endpoint had nothing to
    print. `identity` now carries CAISO's own verdict from
    `atlas_pnode_universe.th_hub`, under the SAME rule v0 the node package
    uses, plus `identity.fleet` — the counted truth that 2,277 of 24,098 nodes
    carry a hub and 21,821 do not. See `rules.hub_pairing`: "no hub attributed
    to this node" and "no hub on this payload" are different claims, and only
    one of them can be true per node.

    THE RAW CELLS RIDE ALONG. `dart.history.cells` is the banked
    {trade_date, he, value} set the derivations were computed from — the same
    window, emitted from rows already read, so the HE x date heat grid can be
    drawn from this payload without a second query. Only present cells are
    listed: `n_cells` equals `n_banked_cells`, nothing is padded to a full
    grid, and today is never in it.

    BASIS IS BORN COMPLETE AND SERVED DARK. It carries the full shape through
    the SAME code path as dart, off `basis_sp15` — a column that is NULL on
    every row today, so every value is null and `basis.reason` states
    D-07-31-01. There is no null-returning branch: the shelf lights itself when
    the hub lands, with no payload version bump.

    Errors: days outside 7..90 -> 400 naming the bound and the value; blank
    pnode_id -> 400; DB down -> 503. An unknown pnode_id is a 200 with an empty
    window and a depth block saying the node has no rows at any date.
    """
    assert _pool is not None
    started = time.monotonic()

    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")
    if days < ANH_DAYS_MIN or days > ANH_DAYS_MAX:
        raise HTTPException(
            status_code=400,
            detail=(f"days must be between {ANH_DAYS_MIN} and {ANH_DAYS_MAX}; "
                    f"got {days}"))

    # The window is `days` calendar days ending YESTERDAY, in Pacific time —
    # the market's own day boundary, and the one both stores are keyed on.
    today = _utcnow().astimezone(AN_PT).date()
    hi = today - _timedelta(days=1)
    lo = today - _timedelta(days=days)

    # The four hot reads have NO dependency on each other, so they are one
    # round trip and not four. Against a remote pantry the wall clock here is
    # RTT-dominated (each read is single-digit ms of server time), and
    # serializing them would quadruple the latency for nothing. Concurrency 4
    # sits inside the pool's max_size of 5 — which is why the pairing read
    # joined this gather and the census did NOT: five concurrent reads would
    # let ONE request hold every connection in the pool.
    #
    # The census is the cheap one to leave out. It is TTL-cached for 15 minutes
    # and identical for every node, so on all but one request in nine hundred
    # seconds it costs no round trip at all — and when it does, it pays for
    # itself alone rather than crowding the node's own reads.
    #
    # The frontier is the one dependent read: it is asked for only after the
    # depth read comes back empty.
    try:
        win_rows, today_raw, node_depth, ident_rows = await asyncio.gather(
            _an_read(_ANH_WINDOW_SQL, {"pnode_id": node, "lo": lo, "hi": hi}),
            _an_read(_ANH_TODAY_SQL, {"pnode_id": node, "today": today,
                                      "da": AN_DA_MARKET, "rt": AN_RT_MARKET}),
            _an_read(_ANH_NODE_DEPTH_SQL, {"pnode_id": node}),
            _an_read(_ANH_IDENT_SQL, {"pnode_id": node}),
        )
        census = await _anh_census()
        # Only a node with nothing banked pays for the table-wide frontier.
        frontier = None
        if not (node_depth and int(node_depth[0].get("rows") or 0)):
            frontier = await _anh_frontier()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    banked = _anh_norm_window(win_rows)
    today_rows = _anh_today_rows(today_raw)

    # HE1-24 UNION every hour either store actually carries, so the axis keeps
    # its shape while today fills in AND a DST fall-back HE25 is not dropped.
    hours = sorted(set(AN_GRID_HOURS)
                   | {r["he"] for r in banked}
                   | {r["he"] for r in today_rows})

    payload = {
        "pnode_id": node,
        "as_of": _utcnow().isoformat(),
        "as_of_pt_date": today.isoformat(),
        "unit": "$/MWh",
        "requested_days": days,
        "hours": hours,
        "identity": _anh_identity_block(ident_rows, census),
        "window": _anh_window_block(banked, lo, hi, days),
        "depth": _anh_depth_block(node_depth, days, frontier),
        "today": {
            "trade_date": today.isoformat(),
            "dow": today.weekday(),
            "partial": True,
            "hours_banked": len(today_rows),
            "last_he": today_rows[-1]["he"] if today_rows else None,
            "rows": today_rows,
            "source": ANH_TODAY_TABLE,
            "markets": [AN_DA_MARKET, AN_RT_MARKET],
            "rule": ANH_PARTIAL_RULE,
        },
        "dart": _anh_metric_block("dart", "dart", banked, today_rows, hours, days),
        "basis": _anh_metric_block("basis", "basis", banked, today_rows, hours, days),
        "rules": {
            "dart_sign": ANH_DART_SIGN_RULE,
            "he": ANH_HE_RULE,
            "dow": ANH_DOW_RULE,
            "regime": ANH_REGIME_RULE,
            "stance": ANH_STANCE_RULE,
            "today_excluded": (
                "every distribution on this payload - envelope, ladder, regime "
                "rails, outlier rails, window average - is computed over the "
                "banked window ONLY. Today is never in the sample it is placed "
                "against"),
            "no_interpolation": (
                "percentiles compute over present days only. A missing day is "
                "dropped, never interpolated, never zero-filled, never carried "
                "forward. days_present IS the n behind every column on its row"),
            "rt_leg": (
                f"the RT leg is {AN_RT_MARKET} (ruled 2026-08-01), never RTD and "
                f"never timeseries_values. rt_n is the interval count behind "
                f"rt_avg: 4 is a full hour, 3 happens and is served as-is"),
            "hub_pairing": ANH_HUB_PAIRING_RULE,
        },
        "ladder_depth_policy": ANH_LADDER_DEPTH_POLICY,
        "percentile_method": AN_PERCENTILE_METHOD,
        "query_plan": {
            "reads": ((4 if not frontier else 5)
                      + (0 if census.get("cached") else 1)),
            "window": (f"{ANH_TABLE} PK prefix (pnode_id) + trade_date range - "
                       f"index-prefix scan by construction; "
                       f"{24 * days} rows at days={days}, "
                       f"{24 * ANH_DAYS_MAX} at the cap"),
            "today": (f"{ANH_TODAY_TABLE} PK prefix (pnode_id, market, "
                      f"market_date); market IN (...) folds into the index cond; "
                      f"~120 rows"),
            "node_depth": (f"{ANH_TABLE} PK prefix, Index Only Scan, "
                           f"Heap Fetches: 0"),
            "pairing": ("atlas_pnode_universe unique-index lookup on pnode_id "
                        "behind a one-row VALUES anchor; exactly 1 row"),
            "pairing_census": (
                "atlas_pnode_universe grouped by th_hub - aggregate over a seq "
                f"scan of ~24k rows, 3.8 ms measured, cached "
                f"{ANH_CENSUS_CACHE_TTL:.0f}s and shared by every node"),
            "warehouse_frontier": (
                "read ONLY when the node has no rows at all; parallel seq scan "
                f"(~219 ms), cached {ANH_FRONTIER_CACHE_TTL:.0f}s"),
        },
        "runtime_ms": None,
    }
    payload["runtime_ms"] = round((time.monotonic() - started) * 1000, 1)
    return payload


# ═══════════════════════════════════════════════════════════════════════════
# THE ALMANAC — the publication surface (spec 2026-08-06, lane A, 2026-08-07)
#
#   GET /api/almanac                     the shelf: newest-first cards, ?series=
#   GET /api/almanac/{series}            the series page: latest full + archive
#   GET /api/almanac/{series}/{issue}    one issue, in full
#
# Three read-only routes over `publications` (migration 154, DECLARED IN
# migrations/154_publications.sql AND NOT YET APPLIED — the architect applies
# it). No writes; the daily-brief backfill ships as a separate script.
#
# THE CONTRACT IS PINNED VERBATIM and carries NO additive fields — see the
# almanac.py docstring, which transcribes the spec and states every judgement
# call. Lane B builds against these three shapes.
#
# PUBLISHED-ONLY, AND THE FILTER IS IN SQL. `status = 'published'` is a
# predicate on every one of the three queries, not a post-filter in Python.
# A draft can therefore never reach a serializer at all, and a request for a
# draft by its exact key gets the same 404 as a request for nothing — never a
# 403, which would confirm the draft exists.
#
# BEFORE THE MIGRATION IS APPLIED. `publications` does not exist yet, so every
# read below raises psycopg's UndefinedTable. That ONE error class is caught
# and served as the honest empty state (empty shelf, a series page with
# latest=null, a 404 on an issue) so Lane B can build against live URLs today.
# It is logged at WARNING every time, because "the table isn't there" is a real
# operational fact, not a normal empty page. EVERY OTHER database error is a
# 503 — "no issues" and "could not look" must never render the same.
#
# THE READS, AND WHY `body` RIDES THE SHELF. `read_minutes` is derived from the
# prose in `body` (see almanac.read_minutes), so the shelf query selects bodies
# it does not otherwise serve. Measured 2026-08-07 on a Neon branch carrying the
# full backfill, that is 14 rows totalling 28,770 bytes of jsonb, read in 3
# buffer hits at 0.060 ms — cheaper than a second round trip, and the whole
# table is three pages. Worth revisiting when `publications` reaches the low
# thousands:
# the fix then is a stored `read_minutes` (or a generated column), NOT a second
# read, because a card whose word count came from a different instant than its
# headline is a card that can contradict itself.
#
# ── A NAMESPACE COLLISION THAT IS ALREADY LIVE, AND HOW IT STAYS SAFE ────────
# `/api/almanac/lmp-shape` (M2, added 2026-05-29, registered near the TOP of
# this file) sits inside the prefix this lane just claimed, and `lmp-shape` is
# not a series. It keeps working ONLY because Starlette matches routes in
# REGISTRATION ORDER and the literal path is registered ~13,900 lines earlier
# than `/api/almanac/{series}`. Move these routes above it — or move it below
# them — and it starts serving `404 no such series: 'lmp-shape'`.
#
# The ordering is pinned by `test_the_legacy_lmp_shape_route_still_wins`, so
# the break shows up in the suite rather than in someone's chart. It is NOT
# special-cased in `_almanac_series_or_404`: adding `lmp-shape` to the series
# vocabulary to "protect" it would put a chart endpoint in the Almanac's
# cadence list, which is worse than the collision.
# ═══════════════════════════════════════════════════════════════════════════

import almanac as _alm

ALMANAC_PREFIX = "/api/almanac"

# One shared column list for the shelf and the series page. Both need the full
# body: the shelf to derive read_minutes, the series page to serve `latest` in
# full. The archive stubs are cut from the same rows in the pure layer rather
# than fetched separately, which is what keeps the series page to one read.
_ALMANAC_COLUMNS = """
        series, issue_key, headline, dek, author, issued_ts,
        verified, verifier_version, data_cutoff_ts, body, status
"""

# NEWEST-FIRST, PINNED: issued_ts DESC with NULLs LAST, tie-broken on issue_key
# DESC. The tie-break is load-bearing — a backfill writes a whole series inside
# one transaction, and `now()` is constant across it, so without it the order of
# a backfilled shelf is whatever the heap happens to hand back.
_ALMANAC_NEWEST_FIRST = """
    ORDER BY issued_ts DESC NULLS LAST, issue_key DESC
"""

# The shelf. `%(series)s::text IS NULL OR ...` is the house's optional-filter
# idiom (same shape as /api/nodes/search): one query plan, one statement, the
# filter folded in or out by a parameter rather than by string concatenation.
_ALMANAC_SHELF_SQL = f"""
    SELECT {_ALMANAC_COLUMNS}
    FROM publications
    WHERE status = 'published'
      AND (%(series)s::text IS NULL OR series = %(series)s)
    {_ALMANAC_NEWEST_FIRST}
"""

# One series' whole published run, newest first. The head is `latest`; the tail
# is the archive.
_ALMANAC_SERIES_SQL = f"""
    SELECT {_ALMANAC_COLUMNS}
    FROM publications
    WHERE status = 'published'
      AND series = %(series)s
    {_ALMANAC_NEWEST_FIRST}
"""

# One issue. Rides UNIQUE(series, issue_key) — an index lookup, at most one row.
_ALMANAC_ISSUE_SQL = f"""
    SELECT {_ALMANAC_COLUMNS}
    FROM publications
    WHERE status = 'published'
      AND series = %(series)s
      AND issue_key = %(issue_key)s
    LIMIT 1
"""

_almanac_log = logging.getLogger("almanac")


def _almanac_series_or_404(series: str) -> str:
    """Validate a path series against the migration's CHECK vocabulary.

    404, not 422: `/api/almanac/quarterly` is a page that does not exist, and
    that is what a 404 says. The detail names the whole vocabulary so a caller
    fixes it on the first try.
    """
    if series not in _alm.SERIES_VOCABULARY:
        raise HTTPException(
            status_code=404,
            detail=(f"no such series: {series!r}. "
                    f"one of {list(_alm.SERIES_VOCABULARY)}"),
        )
    return series


async def _almanac_read(sql: str, params: dict, what: str) -> list:
    """One read, with the pre-migration state handled and nothing else swallowed.

    UndefinedTable -> [] and a WARNING: migration 154 has not been applied yet,
    so the shelf is honestly empty rather than a 500. Anything else -> 503.
    """
    try:
        return await _cockpit_read(sql, params)
    except psycopg.errors.UndefinedTable:
        _almanac_log.warning(
            "publications table does not exist (migration 154 not applied) — "
            "serving %s as empty", what)
        return []
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")


@app.get(f"{ALMANAC_PREFIX}")
async def almanac_shelf(
    series: Optional[str] = Query(
        None,
        description=("Filter the shelf to one series. One of "
                     f"{list(_alm.SERIES_VOCABULARY)}. Omit for every series."),
    ),
):
    """
    The shelf — every published issue as a card, newest first, across all series.

    Serves a BARE JSON ARRAY of shelf items. Nothing wraps it: the spec pinned
    the shelf ITEM, and a `count` or an `as_of` would be an unannounced key on
    a contract another lane is building against.

        [ { series, issue_key, headline, dek, issued_ts, verified,
            read_minutes }, ... ]

    `?series=` narrows to one cadence lane; an unknown value is a 400 naming
    the vocabulary, never a silently empty shelf.

    `read_minutes` is DERIVED, the only field here with no column behind it:
    prose words / 200, rounded up, floored at 1 — and 0 for a body with no
    prose at all. Figure blocks count zero; a figure is looked at, not read.

    Newest-first is `issued_ts DESC NULLS LAST`, tie-broken on `issue_key DESC`.

    PUBLISHED ONLY. Drafts are filtered in SQL and can never reach this list.

    Nothing published -> `[]`. DB down -> 503.
    """
    assert _pool is not None
    if series is not None and series not in _alm.SERIES_VOCABULARY:
        raise HTTPException(
            status_code=400,
            detail=(f"unknown series: {series!r}. "
                    f"one of {list(_alm.SERIES_VOCABULARY)}"),
        )
    rows = await _almanac_read(
        _ALMANAC_SHELF_SQL, {"series": series}, "the shelf")
    return [_alm.shelf_item(r) for r in rows]


@app.get(f"{ALMANAC_PREFIX}/{{series}}")
async def almanac_series(series: str):
    """
    One series' page — the latest issue in full, plus the archive behind it.

        { series, intro, latest: <issue>, archive: [{issue_key, headline,
          issued_ts}] }

    `latest` is the newest published issue, in the same full shape
    `/api/almanac/{series}/{issue}` serves. `archive` is every OLDER published
    issue as a stub, newest first — THE LATEST IS NOT REPEATED THERE. A series
    with exactly one published issue serves `archive: []`, which is the spec's
    "Empty archive = []" clause doing real work.

    `intro` is the series' standing introduction. It comes from a code-side
    registry (`almanac.SERIES_INTRO`), not a column — a standing intro is not
    an issue and has no row to live on. That registry is EMPTY at v0, so every
    series serves `intro: null` today.

    A series in the vocabulary with nothing published serves
    `latest: null, archive: []` — a 200, not a 404. The page exists; it is
    empty. A series OUTSIDE the vocabulary (`/api/almanac/quarterly`) is a 404.

    PUBLISHED ONLY, filtered in SQL. DB down -> 503.
    """
    assert _pool is not None
    series = _almanac_series_or_404(series)
    rows = await _almanac_read(
        _ALMANAC_SERIES_SQL, {"series": series}, f"the {series} series page")
    return _alm.series_page(series, rows)


@app.get(f"{ALMANAC_PREFIX}/{{series}}/{{issue}}")
async def almanac_issue(series: str, issue: str):
    """
    One issue, in full.

        { series, issue_key, headline, dek, author, issued_ts, verified,
          verifier_version, data_cutoff_ts, body: [blocks as stored] }

    `body` is served EXACTLY as `publications.body` holds it — ordered blocks,
    never reshaped, never reordered, never block-validated on the way out. The
    writer owns what a block says; a serving layer that "fixes" one is a
    serving layer that can change what was published.

        {"type": "prose",  "md": "..."}
        {"type": "figure", "component": "...", "params": {...}, "as_of": "..."}

    `verified` is always a real boolean. `verifier_version` and
    `data_cutoff_ts` are null when the writer banked no such stamp — a null
    `verifier_version` does NOT mean unverified; `verified` is the field that
    answers that.

    A DRAFT IS A 404, identical to an issue that was never written. Not a 403:
    a 403 would confirm the draft exists, which is exactly what "drafts never
    serve" is there to prevent. An unknown series is a 404 too.

    DB down -> 503.
    """
    assert _pool is not None
    series = _almanac_series_or_404(series)
    rows = await _almanac_read(
        _ALMANAC_ISSUE_SQL,
        {"series": series, "issue_key": issue},
        f"issue {series}/{issue}",
    )
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"no published issue {series}/{issue}")
    return _alm.issue(rows[0])


# ═══════════════════════════════════════════════════════════════════════════
# /api/weather/dd/* — the Degree Day Ledger, Phase B (2026-08-10)
# ═══════════════════════════════════════════════════════════════════════════
#
# Phase A shipped the composite to production and nothing rendered it. This lane
# is the pipe: five ratified regions, their daily departures, their month- and
# season-to-date totals on the captain's ruled anchors, the SoCalGas arbiter
# grade, and the forecast board's run-over-run delta.
#
# Read-only. No migration is needed or declared: every object below already
# exists on production. Ledger read at build time — migrations 159/160/161/164
# are referenced by this lane, and `SELECT max(version) FROM schema_migrations`
# on production returns **164** (152 rows banked), which matches the spec's
# reading. Nothing here materialises a view, adds an index, or writes.
#
# ── THE COST, RE-MEASURED, AND WHY THE SPEC'S NUMBER IS WRONG ───────────────
#
# The lane spec states dd_region_mtd/dd_region_std cost "~1.4 s per
# region-scoped query (2,021,249 buffers)". That is not what production does
# today. Measured against production 2026-08-10 (EXPLAIN ANALYZE, and
# clock_timestamp() deltas for the wall-clock figures):
#
#   dd_region_mtd  WHERE region=... AND obs_date=...  ->  29,325 ms, 9,071,557 buffers
#   dd_region_std  WHERE obs_date=...  (all 5 regions) ->  22,418 ms
#   dd_region_daily_vs_normal  WHERE region+obs_date range (92 rows) ->      3 ms
#   dd_region_daily_vs_normal  5 regions x 102 days (490 rows)       ->      3 ms
#   dd_socalgas_grade  full table (29,956 rows)                      ->    294 ms
#   dd_region_daily  max(obs_date) WHERE obs_date >= CURRENT_DATE-60 ->      2 ms
#
# So the slow pair is ~16-21x the spec's figure, and the spec's second claim —
# that dd_region_daily "re-derives ~30,000 days on every call" — is true only
# for the views that ADD the window function. dd_region_daily and
# dd_region_daily_vs_normal keep date pushdown and answer in single-digit
# milliseconds; /daily therefore needs no cache to be fast, and gets a short one
# only for the house's own reasons.
#
# ── THE LEVER: THE REGION PREDICATE BUYS NOTHING ────────────────────────────
#
# The 164 plan shows why, and it changes the whole design. `CTE base` materialises
# ALL 172,656 composite rows before either predicate is applied; `region` and
# `obs_date` are then filtered on the CTE Scan sitting on top ("Rows Removed by
# Filter: 172,655"). One cell costs exactly what five regions cost.
#
# Therefore: **the board is built once for all five regions and the cache is
# filtered, never the query.** The spec's uncached page render — "five regions x
# two views" — would be 5 x (29 + 22) = ~260 s done naively per region. Built
# board-wide it is ONE MTD read plus ONE STD read: ~52 s, measured, cold, once.
#
# The captain's ruling stands: nothing here materialises anything. The answer to
# a 52-second view is a cached endpoint that states its own age, and that is what
# this is.
#
# ── THE CACHE, AND THE THING IT REFUSES TO DO ───────────────────────────────
#
# In-process, per resolved `as_of`, stale-while-revalidate:
#
#   fresh (age < TTL)   -> served, `cache.state = "fresh"`
#   stale (age >= TTL)  -> served IMMEDIATELY with `cache.state = "stale"` and a
#                          background refresh kicked off. A stale number wearing
#                          its age is honest; a fresh-looking stale number is the
#                          failure this desk exists to refuse.
#   miss                -> built inline, single-flight (one build at a time, so
#                          five concurrent first-hits collapse into one 52 s read
#                          rather than five, and the max_size=5 pool keeps four
#                          free connections throughout).
#
# The miss path is the expensive one, so startup warms the default as_of in the
# background: by the time a browser asks, the board is usually already fresh.
# Warming is fail-soft — it can never take the service down with it.
#
# EVERY response carries `cache: {state, built_at, age_seconds, ttl_seconds,
# build_seconds, refreshing}` plus `X-Cache` and `Age` headers. There is no path
# through this code that returns a number without saying how old it is.
#
# ── WHAT THIS LANE WILL NOT DO ──────────────────────────────────────────────
#
# It will not sum daily rows into a cumulative. dd_region_daily_vs_normal is
# right here, it is 3 ms, and adding up 31 of its rows would produce an MTD total
# in a fraction of the time the 164 view takes. It would also reproduce the
# 402.75 defect one layer deeper than any post-check can reach, and it would
# silently invent a completeness rule the views already publish. The slow view is
# the arbiter. We wait for it, once, and cache it.
# ═══════════════════════════════════════════════════════════════════════════

import degree_days as _dd

DD_PREFIX = "/api/weather/dd"

_dd_log = logging.getLogger("energylake.dd")

# Fresh windows. The composite only moves when GHCND publishes (daily, with a
# 2-4 day lag), so these are about bounding staleness, not chasing a feed.
_DD_REGIONS_TTL = 900.0        # 22 rows; ratified vectors change ~never
_DD_DAILY_TTL = 300.0          # 3 ms underneath; the TTL is for burst scans
_DD_GRADE_TTL = 300.0          # ~294 ms full-table underneath
_DD_CUMULATIVE_TTL = 1800.0    # ~52 s underneath — see the cost note above
_DD_FORECAST_TTL = 300.0

# A cold cumulative build is two slow reads back to back. The ceiling is a
# backstop against a runaway plan, not a target: 504 beats hanging forever.
_DD_CUMULATIVE_BUILD_TIMEOUT = 240.0

# Request caps. Both windows are bounded so a caller cannot ask for a payload
# nobody meant to serve; both are 400s naming the cap, never a silent truncation.
_DD_DAILY_DEFAULT_DAYS = 45
_DD_DAILY_MAX_DAYS = 1100
_DD_GRADE_DEFAULT_DAYS = 365
_DD_GRADE_MAX_DAYS = 1100
_DD_FORECAST_DEFAULT_DAYS = 15
_DD_FORECAST_MAX_DAYS = 30

# How far back to look for the composite's right edge. A bounded predicate keeps
# date pushdown alive (2 ms); the unbounded max() costs 1.6 s and is only used as
# a fallback if the composite is ever more than this stale.
_DD_OBS_THROUGH_LOOKBACK_DAYS = 60


# ── The cache ───────────────────────────────────────────────────────────────

class _DDCacheEntry:
    """One cached payload and everything needed to state its age."""

    __slots__ = ("payload", "built_at", "built_mono", "build_seconds", "refreshing")

    def __init__(self, payload, built_at, built_mono, build_seconds):
        self.payload = payload
        self.built_at = built_at            # wall clock, for the response
        self.built_mono = built_mono        # monotonic, for the age arithmetic
        self.build_seconds = build_seconds
        self.refreshing = False


class _DDCache:
    """In-process cache with single-flight builds and optional stale-serving.

    ONE lock per cache, not per key. For the cumulative board that is deliberate:
    the underlying read is a ~52 s full-view derivation, and letting two of them
    run concurrently would tie up two of the pool's five connections for a minute
    to answer questions that arrive together anyway. Serialising them costs a
    second caller some wall clock; it never costs the service its pool.
    """

    def __init__(self, name: str, ttl: float, *, max_entries: int = 16,
                 allow_stale: bool = False, build_timeout: float | None = None):
        self.name = name
        self.ttl = ttl
        self.max_entries = max_entries
        self.allow_stale = allow_stale
        self.build_timeout = build_timeout
        self._entries: dict = {}
        self._lock = asyncio.Lock()
        self._tasks: set = set()            # strong refs; asyncio only holds weak ones

    def clear(self) -> None:
        self._entries.clear()

    def _evict(self) -> None:
        while len(self._entries) > self.max_entries:
            oldest = min(self._entries, key=lambda k: self._entries[k].built_mono)
            del self._entries[oldest]

    async def _build(self, key, builder) -> _DDCacheEntry:
        """Run `builder`, stamp the result. Caller holds the lock."""
        t0 = time.monotonic()
        payload = builder()
        payload = await payload if asyncio.iscoroutine(payload) else payload
        elapsed = time.monotonic() - t0
        entry = _DDCacheEntry(payload, _utcnow(), time.monotonic(), round(elapsed, 3))
        self._entries[key] = entry
        self._evict()
        _dd_log.info("%s: built %r in %.1fs", self.name, key, elapsed)
        return entry

    async def _locked_build(self, key, builder) -> _DDCacheEntry:
        async def _run():
            async with self._lock:
                # Double-checked: a build that finished while we queued for the
                # lock is a build we do not repeat.
                entry = self._entries.get(key)
                if entry is not None and (time.monotonic() - entry.built_mono) < self.ttl:
                    return entry
                return await self._build(key, builder)

        if self.build_timeout is None:
            return await _run()
        return await asyncio.wait_for(_run(), timeout=self.build_timeout)

    async def _refresh_in_background(self, key, builder) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.refreshing = True
        try:
            await self._locked_build(key, builder)
        except Exception as e:                       # never let a refresh escape
            _dd_log.warning("%s: background refresh of %r failed: %s",
                            self.name, key, e)
        finally:
            stale = self._entries.get(key)
            if stale is not None:
                stale.refreshing = False

    def _spawn_refresh(self, key, builder) -> None:
        entry = self._entries.get(key)
        if entry is not None and entry.refreshing:
            return                                   # one refresh per key is enough
        task = asyncio.create_task(self._refresh_in_background(key, builder))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def serve(self, key, builder) -> tuple[Any, str, _DDCacheEntry]:
        """(payload, state, entry) — the whole policy in one place.

        state is "fresh" | "stale" | "miss". A stale entry is returned as-is and
        a refresh is started behind it; a miss blocks on the build.
        """
        entry = self._entries.get(key)
        if entry is not None:
            age = time.monotonic() - entry.built_mono
            if age < self.ttl:
                return entry.payload, "fresh", entry
            if self.allow_stale:
                self._spawn_refresh(key, builder)
                return entry.payload, "stale", entry
        entry = await self._locked_build(key, builder)
        return entry.payload, "miss", entry


def _dd_envelope(payload: dict, state: str, entry: _DDCacheEntry, cache: _DDCache,
                 response: Response) -> dict:
    """Attach the age to the body AND the headers. Nothing serves without it."""
    age = max(0.0, time.monotonic() - entry.built_mono)
    out = dict(payload)
    out["cache"] = {
        "state": state,
        "built_at": entry.built_at.isoformat(),
        "age_seconds": round(age, 1),
        "ttl_seconds": cache.ttl,
        "build_seconds": entry.build_seconds,
        "refreshing": entry.refreshing,
    }
    response.headers["Cache-Control"] = f"max-age={int(cache.ttl)}"
    response.headers["X-Cache"] = {"fresh": "hit", "stale": "stale", "miss": "miss"}[state]
    response.headers["Age"] = str(int(age))
    return out


_dd_regions_cache = _DDCache("dd/regions", _DD_REGIONS_TTL, max_entries=1)
_dd_daily_cache = _DDCache("dd/daily", _DD_DAILY_TTL, max_entries=32)
_dd_grade_cache = _DDCache("dd/socalgas-grade", _DD_GRADE_TTL, max_entries=16)
_dd_forecast_cache = _DDCache("dd/forecast", _DD_FORECAST_TTL, max_entries=32)
_dd_cumulative_cache = _DDCache(
    "dd/cumulative", _DD_CUMULATIVE_TTL, max_entries=8,
    allow_stale=True, build_timeout=_DD_CUMULATIVE_BUILD_TIMEOUT,
)

_DD_CACHES = (_dd_regions_cache, _dd_daily_cache, _dd_grade_cache,
              _dd_forecast_cache, _dd_cumulative_cache)


# ── SQL ─────────────────────────────────────────────────────────────────────

_DD_REGIONS_SQL = """
    SELECT region, station_id, weight::float8 AS weight, version, vintage, note
    FROM dd_region_weights_active
    ORDER BY region, station_id
"""

# Bounded predicate on purpose: dd_region_daily keeps date pushdown, so this is
# 2 ms. The unbounded max() over the same view is 1.6 s (measured) and is only
# reached when the composite has gone more than the lookback stale.
_DD_OBS_THROUGH_SQL = """
    SELECT max(obs_date) AS obs_through
    FROM dd_region_daily
    WHERE obs_date >= CURRENT_DATE - %(lookback)s::int
"""

_DD_OBS_THROUGH_FALLBACK_SQL = """
    SELECT max(obs_date) AS obs_through FROM dd_region_daily
"""

# `%(region)s::text IS NULL OR ...` is the house optional-filter idiom (see
# /api/nodes/search, /api/almanac): one plan, one statement, no concatenation.
_DD_DAILY_SQL = """
    SELECT region, version, obs_date, basis_complete, normals_complete,
           tavg_f::float8            AS tavg_f,
           hdd::float8               AS hdd,
           cdd::float8               AS cdd,
           tavg_norm_f::float8       AS tavg_norm_f,
           hdd_norm::float8          AS hdd_norm,
           cdd_norm::float8          AS cdd_norm,
           window_label,
           tavg_departure_f::float8  AS tavg_departure_f,
           hdd_departure::float8     AS hdd_departure,
           cdd_departure::float8     AS cdd_departure,
           member_stations, members_present, missing_stations,
           normals_missing_stations, min_sample_count
    FROM dd_region_daily_vs_normal
    WHERE obs_date BETWEEN %(start)s AND %(end)s
      AND (%(region)s::text IS NULL OR region = %(region)s)
    ORDER BY region, obs_date
"""

# NO region predicate, deliberately. See the cost note: the 164 views build all
# 172,656 composite rows before any filter is applied, so five regions cost what
# one costs. Narrowing here would buy nothing and would turn one 29 s build into
# five of them.
_DD_MTD_SQL = """
    SELECT region, version, obs_date, window_start, window_label,
           days_in_window, days_complete, normals_days_complete,
           complete, normals_complete,
           hdd::float8            AS hdd,
           cdd::float8            AS cdd,
           hdd_norm::float8       AS hdd_norm,
           cdd_norm::float8       AS cdd_norm,
           hdd_departure::float8  AS hdd_departure,
           cdd_departure::float8  AS cdd_departure,
           ly_obs_date, ly_days_in_window, ly_days_complete, ly_complete,
           ly_hdd::float8         AS ly_hdd,
           ly_cdd::float8         AS ly_cdd,
           hdd_vs_ly::float8      AS hdd_vs_ly,
           cdd_vs_ly::float8      AS cdd_vs_ly
    FROM dd_region_mtd
    WHERE obs_date = %(as_of)s
    ORDER BY region
"""

_DD_STD_SQL = """
    SELECT region, version, obs_date, season, season_start, window_label,
           days_in_window, days_complete, normals_days_complete,
           complete, normals_complete,
           hdd::float8            AS hdd,
           cdd::float8            AS cdd,
           hdd_norm::float8       AS hdd_norm,
           cdd_norm::float8       AS cdd_norm,
           hdd_departure::float8  AS hdd_departure,
           cdd_departure::float8  AS cdd_departure,
           ly_obs_date, ly_season_start, ly_days_in_window, ly_days_complete,
           ly_complete,
           ly_hdd::float8         AS ly_hdd,
           ly_cdd::float8         AS ly_cdd,
           hdd_vs_ly::float8      AS hdd_vs_ly,
           cdd_vs_ly::float8      AS cdd_vs_ly
    FROM dd_region_std
    WHERE obs_date = %(as_of)s
    ORDER BY region
"""

_DD_GRADE_ROWS_SQL = """
    SELECT obs_date, version,
           el_composite_temp_f::float8  AS el_composite_temp_f,
           scg_composite_temp_f::float8 AS scg_composite_temp_f,
           delta_f::float8              AS delta_f,
           basis_complete, arbiter_present,
           member_stations, members_present, missing_stations
    FROM dd_socalgas_grade
    WHERE obs_date BETWEEN %(start)s AND %(end)s
    ORDER BY obs_date
"""

# Every statistic is FILTERed to arbiter_present: a day SoCalGas never published
# is an ungraded day, not a zero-delta day. NULL start/end = all time, which is
# how the panel's long-view summary is served beside a short window of rows.
_DD_GRADE_SUMMARY_SQL = """
    SELECT count(*)                                            AS n_days,
           count(*) FILTER (WHERE arbiter_present)             AS n_graded,
           (avg(delta_f) FILTER (WHERE arbiter_present))::float8         AS mean_delta_f,
           (stddev_samp(delta_f) FILTER (WHERE arbiter_present))::float8 AS sd_delta_f,
           (avg(abs(delta_f)) FILTER (WHERE arbiter_present))::float8    AS mean_abs_delta_f,
           (min(delta_f) FILTER (WHERE arbiter_present))::float8         AS min_delta_f,
           (max(delta_f) FILTER (WHERE arbiter_present))::float8         AS max_delta_f,
           min(obs_date) FILTER (WHERE arbiter_present)        AS first_graded,
           max(obs_date) FILTER (WHERE arbiter_present)        AS last_graded
    FROM dd_socalgas_grade
    WHERE (%(start)s::date IS NULL OR obs_date >= %(start)s)
      AND (%(end)s::date IS NULL OR obs_date <= %(end)s)
"""

# rn <= 2 is the whole point: rank 1 is the newest issuance for that target date,
# rank 2 is the one before it, and their difference is the run-over-run delta.
# Migration 161 keeps every issuance and indexes (station_id, target_date,
# issued_ts DESC) for exactly this read.
_DD_FORECAST_SQL = """
    WITH scoped AS (
        SELECT station_id, target_date, issued_ts, tmax_f, tmin_f, tavg_f,
               hdd, cdd, basis_complete, hours_covered, hours_required,
               source_product, gridpoint_id, icao,
               row_number() OVER (PARTITION BY station_id, target_date
                                  ORDER BY issued_ts DESC) AS rn
        FROM station_degree_days_forecast
        WHERE target_date >= %(from_date)s
          AND target_date < %(to_date)s
          AND (%(station)s::text IS NULL OR station_id = %(station)s)
    )
    SELECT * FROM scoped
    WHERE rn <= 2
    ORDER BY station_id, target_date, rn
"""


# ── Params ──────────────────────────────────────────────────────────────────

def _dd_parse_date(raw: Optional[str], field: str) -> Optional[_date]:
    """ISO date or a 400 naming the field. Never a silent None on bad input."""
    if raw is None or raw == "":
        return None
    try:
        return _date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an ISO date (YYYY-MM-DD), got {raw!r}",
        )


def _dd_window(start: Optional[_date], end: Optional[_date], anchor: _date,
               *, default_days: int, max_days: int) -> tuple[_date, _date]:
    """Resolve (start, end) against an anchor, with both caps enforced loudly."""
    if end is None:
        end = anchor
    if start is None:
        start = end - _timedelta(days=default_days - 1)
    if start > end:
        raise HTTPException(
            status_code=400,
            detail=f"start ({start.isoformat()}) is after end ({end.isoformat()})",
        )
    span = (end - start).days + 1
    if span > max_days:
        raise HTTPException(
            status_code=400,
            detail=(f"window of {span} days exceeds the {max_days}-day cap. "
                    "Narrow start/end — this endpoint never silently truncates."),
        )
    return start, end


async def _dd_read(sql: str, params: dict) -> list:
    """One DD read, with the house's 503-on-DB-down contract."""
    try:
        return await _cockpit_read(sql, params)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")


async def _dd_obs_through() -> Optional[_date]:
    """The composite's right edge — NOT today.

    GHCND actuals lag NOAA's publication schedule by 2-4 days (production sits at
    2026-08-06 while the wall clock reads 2026-08-10). Every default window in
    this lane anchors here rather than on `now`, so the ledger's edge is the data's
    edge and the surface can draw the seam where it actually is.
    """
    rows = await _dd_read(_DD_OBS_THROUGH_SQL,
                          {"lookback": _DD_OBS_THROUGH_LOOKBACK_DAYS})
    edge = rows[0]["obs_through"] if rows else None
    if edge is None:
        rows = await _dd_read(_DD_OBS_THROUGH_FALLBACK_SQL, {})
        edge = rows[0]["obs_through"] if rows else None
    return edge


async def _dd_region_vocabulary() -> list[str]:
    """The ratified region ids, off the (cached) vectors read."""
    payload, _, _ = await _dd_regions_cache.serve(
        "vectors", lambda: _dd_read(_DD_REGIONS_SQL, {}))
    return [r["region"] for r in _dd.regions_payload(payload)["regions"]]


async def _dd_region_or_400(region: Optional[str]) -> Optional[str]:
    if region is None or region == "":
        return None
    known = await _dd_region_vocabulary()
    if region not in known:
        raise HTTPException(
            status_code=400,
            detail=f"unknown region: {region!r}. one of {known}",
        )
    return region


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get(f"{DD_PREFIX}/regions")
async def dd_regions(response: Response):
    """The five ratified composite vectors: members, weights, version, vintage.

        { vector_version, vector_versions, region_count,
          regions: [ { region, version, vintage, note, member_stations,
                       weight_sum, members: [{station_id, weight}] } ],
          cache: {...} }

    Cheap (22 rows off dd_region_weights_active), cached 15 min. `weight_sum` is
    reported rather than asserted — all five sum to 1.00 today, and if one ever
    stops the number says so instead of a normalization hiding it.

    `vector_version` is `v1`: these are ratified editorial judgments about which
    stations stand for a region, not laws of nature, and the surface is required
    to state the version beside the numbers. DB unavailable -> 503.
    """
    assert _pool is not None
    rows, state, entry = await _dd_regions_cache.serve(
        "vectors", lambda: _dd_read(_DD_REGIONS_SQL, {}))
    return _dd_envelope(_dd.regions_payload(rows), state, entry,
                        _dd_regions_cache, response)


@app.get(f"{DD_PREFIX}/daily")
async def dd_daily(
    response: Response,
    region: Optional[str] = Query(
        None, description="One ratified region id. Omit for all five."),
    start: Optional[str] = Query(None, description="ISO date, inclusive."),
    end: Optional[str] = Query(
        None, description="ISO date, inclusive. Defaults to the composite's "
                          "right edge (obs_through), NOT today."),
):
    """Daily composite degree days against normals, one row per (region, date).

        { start, end, regions_requested, regions_present, vector_version,
          row_count, obs_through, rows: [...], cache: {...} }

    Every row carries the completeness the view publishes for itself —
    `basis_complete`, `normals_complete`, `members_present`/`member_stations`,
    `missing_stations`, `normals_missing_stations`, `min_sample_count` — so a
    client can say WHY a cell is thin without a second call. None of it is
    recomputed here.

    Measured 3 ms underneath (dd_region_daily_vs_normal keeps date pushdown, so
    the window function penalty that makes /cumulative slow does not apply). The
    5-minute cache is for burst scans, not for latency.

    Default window: the last 45 days ending at `obs_through`. Cap 1100 days; a
    wider ask is a 400 naming the cap, never a silent truncation.
    DB unavailable -> 503.
    """
    assert _pool is not None
    region = await _dd_region_or_400(region)
    s, e = _dd_parse_date(start, "start"), _dd_parse_date(end, "end")
    anchor = await _dd_obs_through() or _utcnow().date()
    s, e = _dd_window(s, e, anchor,
                      default_days=_DD_DAILY_DEFAULT_DAYS,
                      max_days=_DD_DAILY_MAX_DAYS)

    key = (region, s, e)

    async def _build():
        rows = await _dd_read(_DD_DAILY_SQL, {"start": s, "end": e, "region": region})
        return _dd.daily_payload(rows, start=s, end=e,
                                 regions_requested=[region] if region else None)

    payload, state, entry = await _dd_daily_cache.serve(key, _build)
    return _dd_envelope(payload, state, entry, _dd_daily_cache, response)


@app.get(f"{DD_PREFIX}/cumulative")
async def dd_cumulative(
    response: Response,
    region: Optional[str] = Query(
        None, description="Filters the CACHED board. The underlying views cost "
                          "the same for one region as for five, so the query is "
                          "never narrowed — see the cost note in the source."),
    as_of: Optional[str] = Query(
        None, description="ISO date. Defaults to the composite's right edge."),
):
    """Month-to-date and season-to-date for every region on one date. THE SLOW ONE.

        { as_of, as_of_source, obs_through, vector_version, region_count,
          regions: [ { region,
                       mtd: { window_start, window_end, days_in_window,
                              days_complete, complete, normals_complete,
                              hdd, cdd, hdd_norm, cdd_norm,
                              hdd_departure, cdd_departure,
                              last_year: {...}, absence: {...}|null },
                       std: { ...same..., season, season_start } } ],
          cache: {...} }

    ABSENCE IS A FIRST-CLASS VALUE. A null total is not an error and is not a
    zero — it is a stated incompleteness with the view's own day counts beside
    it. `absence.reason` is one of `incomplete_obs` (the "89 of 92 days" case),
    `incomplete_norm`, `no_season` (a shoulder month has no ruled anchor, which
    is different from an incomplete season), or `no_row`. Nothing on this path
    coalesces, and nothing sums daily rows to fill a gap.

    COST AND CACHE, measured on production 2026-08-10: dd_region_mtd is 29.3 s
    and dd_region_std 22.4 s, board-wide or single-cell alike (the views build all
    172,656 composite rows before any predicate applies). So the board is built
    ONCE for all five regions — ~52 s cold — and cached 30 min with
    stale-while-revalidate: an expired board is served immediately, labelled
    `cache.state = "stale"`, while a refresh runs behind it. `cache.age_seconds`,
    the `Age` header and `X-Cache` state the age on every single response.

    Startup warms the default `as_of`, so the cold 52 s is normally paid by the
    service at boot rather than by a browser. A build that exceeds 240 s -> 504.
    DB unavailable -> 503.
    """
    assert _pool is not None
    region = await _dd_region_or_400(region)
    requested = _dd_parse_date(as_of, "as_of")
    edge = await _dd_obs_through()
    resolved = requested or edge
    if resolved is None:
        raise HTTPException(
            status_code=503,
            detail="the regional composite has no rows; no as_of can be resolved",
        )

    async def _build():
        return await _dd_build_board(resolved, requested is not None, edge)

    try:
        board, state, entry = await _dd_cumulative_cache.serve(resolved, _build)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=(f"the cumulative board did not build within "
                    f"{int(_DD_CUMULATIVE_BUILD_TIMEOUT)}s. The 164 views derive "
                    "the whole composite per call; retry once the in-flight "
                    "build lands."),
        )
    return _dd_envelope(_dd.filter_board(board, region), state, entry,
                        _dd_cumulative_cache, response)


async def _dd_build_board(as_of: _date, pinned: bool, edge: Optional[_date]) -> dict:
    """The two slow reads, board-wide, in the order the surface needs them."""
    mtd = await _dd_read(_DD_MTD_SQL, {"as_of": as_of})
    std = await _dd_read(_DD_STD_SQL, {"as_of": as_of})
    return _dd.cumulative_payload(
        mtd, std,
        as_of=as_of,
        as_of_source="requested" if pinned else "latest_obs",
        obs_through=edge,
    )


@app.get(f"{DD_PREFIX}/socalgas-grade")
async def dd_socalgas_grade(
    response: Response,
    start: Optional[str] = Query(None, description="ISO date, inclusive."),
    end: Optional[str] = Query(None, description="ISO date, inclusive."),
):
    """Our composite against SoCalGas's own published composite, and the delta.

        { start, end, row_count,
          summary: { window: {...}, all_time: {...} },
          note, rows: [ { obs_date, el_composite_temp_f, scg_composite_temp_f,
                          delta_f, abs_delta_f, basis_complete, arbiter_present,
                          member_stations, members_present, missing_stations } ],
          cache: {...} }

    `delta_f` is OURS minus THEIRS. Divergence is a finding about our station
    weights, not an error in SoCalGas's — they are the arbiter here, not the
    subject, and the surface is required to label it that way.

    Both summaries count only `arbiter_present` days: a day SoCalGas never
    published is an ungraded day, not a zero-delta day, and `n_days` vs
    `n_graded` is the honest denominator. `sd_delta_f` is a SAMPLE sd and is null
    below two graded days rather than 0.0.

    `summary.all_time` is always computed over the whole table (294 ms measured,
    29,956 rows) so the panel's long view does not depend on the row window. Row
    window defaults to the last 365 days, cap 1100. DB unavailable -> 503.
    """
    assert _pool is not None
    s, e = _dd_parse_date(start, "start"), _dd_parse_date(end, "end")
    anchor = await _dd_obs_through() or _utcnow().date()
    s, e = _dd_window(s, e, anchor,
                      default_days=_DD_GRADE_DEFAULT_DAYS,
                      max_days=_DD_GRADE_MAX_DAYS)

    async def _build():
        rows = await _dd_read(_DD_GRADE_ROWS_SQL, {"start": s, "end": e})
        win = await _dd_read(_DD_GRADE_SUMMARY_SQL, {"start": s, "end": e})
        allt = await _dd_read(_DD_GRADE_SUMMARY_SQL, {"start": None, "end": None})
        return _dd.grade_payload(rows,
                                 win[0] if win else None,
                                 allt[0] if allt else None,
                                 start=s, end=e)

    payload, state, entry = await _dd_grade_cache.serve((s, e), _build)
    return _dd_envelope(payload, state, entry, _dd_grade_cache, response)


@app.get(f"{DD_PREFIX}/forecast")
async def dd_forecast(
    response: Response,
    station: Optional[str] = Query(
        None, description="GHCN station id. Omit for every station on the board."),
    days: int = Query(_DD_FORECAST_DEFAULT_DAYS, ge=1, le=_DD_FORECAST_MAX_DAYS,
                      description="Forward horizon in days from today."),
):
    """The forecast degree-day board: newest issuance per target date, PLUS the
    run-over-run delta against the issuance before it.

        { station, from_date, days, board_state, absence, stations_present,
          row_count, rows_with_run_over_run_delta, newest_issued_ts,
          rows: [ { station_id, target_date, issued_ts, tmax_f, tmin_f, tavg_f,
                    hdd, cdd, basis_complete, hours_covered, hours_required,
                    prior_issued_ts, prior_hdd, prior_cdd, prior_tavg_f,
                    delta_hdd, delta_cdd, delta_tavg_f,
                    delta_basis, delta_absence } ],
          cache: {...} }

    THE DELTA IS THE TRADER NUMBER. The current issuance is a level; what moves
    a position is how that level CHANGED since the last run. Migration 161 keeps
    every issuance and indexes (station_id, target_date, issued_ts DESC) for
    exactly this subtraction, so the endpoint ships with it rather than adding it
    later.

    `station_degree_days_forecast` holds 0 rows today (measured 2026-08-10 — the
    builder has not had its maiden run). That is served as `board_state: "empty"`
    with an `absence` naming the reason, NOT a 404 and not a bare `[]`: an empty
    board and a filtered-to-nothing board are different answers. A target date
    seen for the first time gets null deltas with
    `delta_absence.reason = "no_prior_issuance"` — a first sighting is not a
    zero-change day. DB unavailable -> 503.
    """
    assert _pool is not None
    from_date = _utcnow().date()
    to_date = from_date + _timedelta(days=days)
    key = (station, from_date, days)

    async def _build():
        rows = await _dd_read(_DD_FORECAST_SQL, {
            "from_date": from_date, "to_date": to_date, "station": station,
        })
        return _dd.forecast_payload(rows, station=station,
                                    from_date=from_date, days=days)

    payload, state, entry = await _dd_forecast_cache.serve(key, _build)
    return _dd_envelope(payload, state, entry, _dd_forecast_cache, response)


# ── Startup warm ────────────────────────────────────────────────────────────

async def _dd_warm_cumulative() -> None:
    """Pay the ~52 s cold build at boot so a browser does not.

    Fail-soft by construction: every exception is logged and swallowed. A ledger
    that could not pre-warm still serves — the first caller just pays the build
    — and a service that refuses to start because a cache could not warm is a
    worse failure than a slow first request.
    """
    try:
        edge = await _dd_obs_through()
        if edge is None:
            _dd_log.warning("dd warm: composite has no rows; nothing to warm")
            return
        await _dd_cumulative_cache.serve(
            edge, lambda: _dd_build_board(edge, False, edge))
        _dd_log.info("dd warm: cumulative board ready for %s", edge)
    except Exception as e:
        _dd_log.warning("dd warm: skipped (%s)", e)


# ═══════════════════════════════════════════════════════════════════════════
# NODE ANALYTICS API v0 — per-node package + block pricing (2026-08-12)
# ═══════════════════════════════════════════════════════════════════════════
#
#   GET /api/nodes/{pnode_id}/package   the whole per-node package, one call
#   GET /api/hubs/{hub}/blocks          the same block set on the hub DA series
#   GET /api/nodes/top-movers           the screening-page seed
#
# READ-ONLY LANE. No writes, no DDL, no migrations anywhere in this section.
#
# ── SUBSTRATE, MEASURED LIVE 2026-08-12 (not inherited from a brief) ────────
# `atlas_node_hourly_stats`: 46.2 M rows, 2026-05-03 .. 2026-08-11, 101 distinct
# dates over a 101-day span — GAPLESS, verified by counting distinct dates
# against the span rather than by assuming it. ~19.3 k nodes x 24 HE/day.
#
# THE WINDOW IS ALWAYS COMPUTED FROM THE DATA. Every endpoint below derives its
# window from min/max(trade_date) — the NODE's own where the node is the
# subject. No date is hardcoded anywhere in this lane; the bank grows daily and
# a hardcoded edge would rot within a day of being written.
#
# COLUMN REALITY, and it is not uniform:
#   dam_lmp         ~221 k of ~464 k rows/day — only about HALF the universe
#                   carries a day-ahead price at all. A node with no DAM is not
#                   a broken node, and its dam-derived stanzas come back empty
#                   rather than zero.
#   dam_congestion  / dam_loss  NULL before 2026-05-14, populated from that date
#                   on. `components_from` is DERIVED per node (the node's own
#                   earliest non-null congestion day), never asserted from the
#                   table-wide date, because a node that started late has a
#                   later components floor than the warehouse does.
#   basis_sp15      populated but SPARSE — ~55 k of ~469 k rows/day, i.e. about
#                   2.3 k of 19.3 k nodes. main.py's older note that this column
#                   is "NULL on all 2,510,268 rows" was true when written and is
#                   now STALE: the shelf lit some time after 2026-08-01. This
#                   lane reads it live and reports what is there.
#
# ── TH HUBS ARE IN THIS TABLE, CONTRARY TO THE STANDING NOTE ───────────────
# TH_SP15_GEN-APND, TH_NP15_GEN-APND and TH_ZP26_GEN-APND all carry a full 24 HE
# of DAM and RT in atlas_node_hourly_stats today (verified 2026-08-12). The hub
# endpoint still reads `timeseries_values` instead, and that is a DEPTH choice,
# not a availability one: the nodal bank starts 2026-05-03, while the hub series
# reach back to 2016-01-01. Serving hubs from the nodal table would throw away
# ten years of history to save a join.
#
# A TRAP THIS LANE AVOIDS: the sibling ids TH_*_GEN_ONPEAK-APND and
# TH_*_GEN_OFFPEAK-APND carry 16 and 8 HE per day respectively — they are
# CAISO's own pre-blocked apnodes, not hourly nodes. Run the block calendar over
# one of those and every block silently reports the wrong hours. They are not
# hub aliases here and `/api/hubs/{hub}/blocks` will not resolve to them.

import block_pricing as _bp

NAP_TABLE = "atlas_node_hourly_stats"
NAP_WINDOWS = ("all", "30", "90")

# Hub lane. The series ARE named 'NP15'/'SP15'/'ZP26' in timeseries_values —
# recon'd, not guessed. Measured depth 2016-01-01 .. 2026-08-13 (the DA curve
# runs one day forward of today, which is why last_day can be tomorrow).
NAP_HUB_DATASET = AN_HUB_DA_DATASET          # 'caiso_lmp_da_hourly'
NAP_HUBS = ("SP15", "NP15", "ZP26")

# Movers. days is CAPPED and the cap is a measurement (see the plan note on the
# endpoint), not a preference.
NAP_MOVERS_METRICS = ("dart", "basis_sp15")
NAP_MOVERS_TOP_N = 50
NAP_MOVERS_MAX_DAYS = 7
NAP_MOVERS_TTL_S = 900


def _nap_holiday(d) -> bool:
    """The lane's calendar — main.py's own, injected into block_pricing.

    There is exactly one NERC calendar in this process and this is the seam
    where the block layer picks it up.
    """
    return is_nerc_holiday(d)


# ── Identity ────────────────────────────────────────────────────────────────
# NAME AND ZONE ARE THINLY COVERED AND THIS LANE SAYS SO RATHER THAN INVENTING.
# Measured 2026-08-12 over the 24,098-row universe:
#   * `atlas_pnode_zone` — the natural home for a LAP zone — is EMPTY (0 rows).
#     There is therefore NO load-zone attribution available at any coverage, and
#     `zone` below is the CAISO TRADING HUB (atlas_pnode_universe.th_hub,
#     verbatim, no geographic inference), which is what a power desk means by a
#     node's zone. It is null for ~90.6% of the universe (2,277 of 24,098 carry
#     one) and that is CAISO's own attribution, not a gap this lane can fill.
#   * `name` — there is no gazetteer. `description` equals the pnode_id itself
#     on 21,362 of 24,098 rows, so it is only surfaced when it actually differs;
#     otherwise the Atlas plant name (via the crosswalk) is used when one
#     exists. Both null is the common and honest answer, and the client should
#     render the id as the primary label.
#   * `kind` — atlas_pnode_geo.node_type, which IS fully covered (28,834 of
#     28,834 rows carry both node_type and area).
_NAP_IDENTITY_SQL = """
    SELECT u.pnode_id,
           u.th_hub,
           u.apnode_type,
           NULLIF(u.description, u.pnode_id)          AS description_name,
           g.node_type,
           g.area,
           (SELECT min(p.plant_name)
              FROM atlas_pnode_crosswalk x
              JOIN atlas_plants p ON p.plant_code = x.plant_code
             WHERE x.pnode_id = %(pnode_id)s)         AS plant_name
      FROM atlas_pnode_universe u
      LEFT JOIN atlas_pnode_geo g ON g.pnode_id = u.pnode_id
     WHERE u.pnode_id = %(pnode_id)s
"""

# The node's OWN banked depth, off the PK prefix (index-only). Node-scoped on
# purpose: the table-wide frontier is a seq scan and does not belong on a
# per-node request.
_NAP_DEPTH_SQL = """
    SELECT min(trade_date) AS first_day,
           max(trade_date) AS last_day,
           count(DISTINCT trade_date) AS days_banked
      FROM atlas_node_hourly_stats
     WHERE pnode_id = %(pnode_id)s
"""

# The window read. PK prefix + a date range -> Bitmap Index Scan on
# atlas_node_hourly_stats_pkey. EXPLAIN (ANALYZE) 2026-08-12, full 101-day
# window on TH_SP15_GEN-APND:
#
#   Bitmap Heap Scan on atlas_node_hourly_stats  (actual rows=2424)
#     Recheck Cond: ((pnode_id = '...') AND (trade_date >= '2026-05-03'))
#     ->  Bitmap Index Scan on atlas_node_hourly_stats_pkey (actual rows=2496)
#           Index Cond: ((pnode_id = '...') AND (trade_date >= '2026-05-03'))
#   Execution Time: 293 ms cold
#
# 2,424 rows = 101 days x 24 HE. The read touches days x 24 rows and NOTHING
# else — no seq scan, no sort spill. That is the receipt the package's
# performance claim rests on.
_NAP_ROWS_SQL = """
    SELECT trade_date, he, dam_lmp, rt_avg, rt_n, dart, basis_sp15,
           dam_congestion, dam_loss
      FROM atlas_node_hourly_stats
     WHERE pnode_id = %(pnode_id)s
       AND trade_date >= %(lo)s
     ORDER BY trade_date, he
"""


def _nap_window_bounds(first_day, last_day, window_days: str):
    """The [lo, hi] the package computes over, DERIVED from the node's bank.

    'all' is the node's whole bank. '30'/'90' count back from the node's OWN
    last banked day — not from today, and not from the warehouse's last day. A
    node that stopped updating a week ago gets its last 30 BANKED days, and the
    window it actually got is echoed on the wire so the difference is visible.
    """
    if first_day is None or last_day is None:
        return None, None
    if window_days == "all":
        return first_day, last_day
    lo = last_day - _timedelta(days=int(window_days) - 1)
    return max(lo, first_day), last_day


@app.get("/api/nodes/{pnode_id}/package")
async def nodes_package(
    pnode_id: str = Path(
        ...,
        description="CAISO pricing-node id, e.g. ALAMT3G_7_B1. Unknown -> 400.",
    ),
    window_days: str = Query(
        default="all",
        description=f"Window over the node's OWN bank. One of {', '.join(NAP_WINDOWS)}.",
    ),
):
    """
    The per-node package — identity, shape, distribution, TB and block pricing.

    ONE call returns the whole node page. Every stanza is computed over the SAME
    window, and that window is echoed in `window` so no chart can quietly be
    drawn over a different span than the one beside it.

    Stanzas:
      identity        pnode_id, name, zone, first_day, last_day, days_banked,
                      components_from (the node's own earliest day with
                      non-null dam_congestion — derived, never asserted).
      profile         per HE1-24: p25/p50/p75 (+mean, n) of dam_lmp.
      profile_rt      the same for rt_avg.
      heatmap         per (month, HE): mean dam_lmp, months in the bank only.
      distribution    dam_lmp counts on the GridStatus bin edges.
      distribution_rt the same for rt_avg.
      tb              `tb2` and `tb4` in $/kW-month, by month + a summary over
                      COMPLETE months only: {avg, median, max, min, n_months}.
      blocks          6x16 / off_peak / 7x8 / 7x16 / atc / 2x16h, monthly, each
                      with counted `hours` and a complete-months `summary` on
                      the same {avg, median, max, min, n_months} keys as `tb`.
      blocks_daily    the last 31 banked days x block, each row carrying
                      `partial` — true when the bank does not hold the whole
                      trade date yet (the frontier day).
      basis           `per_he` p25/p50/p75 of basis_sp15 + `monthly` averages.
      dart            the same for dart.
      rt_available    one bool per board that can draw a real-time series —
                      `profile`, `distribution`, `blocks` — each evaluated by
                      the coverage rule over that board's own window.
      rt_coverage_note the suppression rule, stated, with the counts behind it.
                      This stays the SINGLE explanation; `rt_available` carries
                      the per-board verdict and repeats no prose.

    ABSENCE IS STATED, NEVER ZERO-FILLED. An HE, month or day the bank does not
    carry is ABSENT from its array. The one deliberate exception is
    `distribution`, where every bin ships even at n=0 — the bins are a fixed
    axis the client draws, and a missing bar would silently rescale the chart.

    RT SUPPRESSION. When real-time coverage is thin (see `rt_coverage_note`),
    `avg_rt` on every block row is null rather than an average of whichever
    hours happened to report. The rule is on the wire, not just in this
    docstring.

    Errors: blank/unknown pnode_id -> 400; bad window_days -> 400; DB
    unavailable -> 503. A known node with nothing banked -> 200 with honest
    empty stanzas and a null window.
    """
    assert _pool is not None

    node = (pnode_id or "").strip()
    if not node:
        raise HTTPException(status_code=400, detail="pnode_id is required")
    if window_days not in NAP_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"window_days must be one of {', '.join(NAP_WINDOWS)}",
        )

    try:
        ident_rows = await _an_read(_NAP_IDENTITY_SQL, {"pnode_id": node})
        depth_rows = await _an_read(_NAP_DEPTH_SQL, {"pnode_id": node})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    depth = depth_rows[0] if depth_rows else {}
    first_day, last_day = depth.get("first_day"), depth.get("last_day")

    # A node with neither an identity row nor a banked row is not a node this
    # API knows — a client input error, not an empty 200 pretending otherwise.
    # This mirrors /api/analytics/node/{pnode_id}.
    if not ident_rows and first_day is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown pnode_id '{node}': no identity and nothing banked",
        )

    ident = ident_rows[0] if ident_rows else {}
    identity = {
        "pnode_id": node,
        # The plant name when the crosswalk knows one, else a description that
        # is genuinely a name, else null. Never the id restyled as a name.
        "name": ident.get("plant_name") or ident.get("description_name"),
        # in_universe is passed explicitly so "CAISO says this node has no hub"
        # stays distinct from "CAISO has never listed this node" — see
        # _an_pair_hub. A node can be banked in the hourly table and still have
        # no universe row.
        "zone": _an_pair_hub(ident.get("th_hub"),
                             in_universe=bool(ident_rows)).get("hub"),
        "pairing": _an_pair_hub(ident.get("th_hub"),
                                in_universe=bool(ident_rows))["pairing"],
        "th_hub": ident.get("th_hub"),
        "kind": ident.get("node_type") or ident.get("apnode_type"),
        "area": ident.get("area"),
        "first_day": first_day.isoformat() if first_day else None,
        "last_day": last_day.isoformat() if last_day else None,
        "days_banked": int(depth.get("days_banked") or 0),
        "components_from": None,   # derived from the rows below
        "coverage_note": (
            "zone is atlas_pnode_universe.th_hub verbatim (no geographic "
            "inference) and is null for ~90% of the universe; "
            "atlas_pnode_zone is empty, so no LAP-zone attribution exists. "
            "name is the Atlas plant name or a description that differs from "
            "the id — both null is common and honest."
        ),
    }

    lo, hi = _nap_window_bounds(first_day, last_day, window_days)
    empty_window = {
        "days_requested": window_days, "first_day": None, "last_day": None,
        "days_in_window": 0, "hours": 0,
    }
    if lo is None:
        # Known node, nothing banked. Honest empties, not zeros.
        return {
            "pnode_id": node, "window": empty_window, "identity": identity,
            "profile": [], "profile_rt": [], "heatmap": [],
            "distribution": _bp.distribution([], "dam_lmp"),
            "distribution_rt": _bp.distribution([], "rt_avg"),
            "tb": {}, "blocks": {}, "blocks_daily": [],
            "basis": {"column": "basis_sp15", "per_he": [], "monthly": []},
            "dart": {"column": "dart", "per_he": [], "monthly": []},
            # Nothing banked -> no board can draw RT. False here, not the
            # `suppressed: false` of a rule that never got to fire.
            "rt_available": {"profile": False, "distribution": False,
                             "blocks": False},
            "rt_coverage_note": _bp.rt_coverage([]),
            "block_definitions": _bp.BLOCK_DEFINITIONS,
        }

    try:
        rows = await _an_read(_NAP_ROWS_SQL, {"pnode_id": node, "lo": lo})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # `he` arrives as smallint and the numerics as Decimal; normalise once here
    # so every downstream function sees plain ints/floats.
    win = []
    for r in rows:
        win.append({
            "trade_date": r["trade_date"],
            "he": int(r["he"]),
            "dam_lmp": None if r["dam_lmp"] is None else float(r["dam_lmp"]),
            "rt_avg": None if r["rt_avg"] is None else float(r["rt_avg"]),
            "rt_n": int(r["rt_n"] or 0),
            "dart": None if r["dart"] is None else float(r["dart"]),
            "basis_sp15": None if r["basis_sp15"] is None else float(r["basis_sp15"]),
            "dam_congestion": (None if r["dam_congestion"] is None
                               else float(r["dam_congestion"])),
        })

    days_present = sorted({r["trade_date"] for r in win})
    w_first = days_present[0] if days_present else None
    w_last = days_present[-1] if days_present else None
    window = {
        "days_requested": window_days,
        "first_day": w_first.isoformat() if w_first else None,
        "last_day": w_last.isoformat() if w_last else None,
        "days_in_window": len(days_present),
        "hours": len(win),
    }

    # components_from — the node's OWN earliest day carrying congestion.
    comp_days = [r["trade_date"] for r in win if r["dam_congestion"] is not None]
    identity["components_from"] = min(comp_days).isoformat() if comp_days else None

    complete = _bp.month_completeness(w_first, w_last)
    coverage = _bp.rt_coverage(win)

    # PER-BOARD RT AVAILABILITY. One boolean per board that can draw a real-time
    # series, each evaluated over THAT board's own rows by the same rule the
    # package-level `rt_coverage_note` states — the note stays the single
    # explanation, and these are the three flags a board reads to decide between
    # a series and an honest blank. The three boards share this window today, so
    # they agree today; they are computed separately rather than aliased so a
    # board that narrows its window later cannot inherit another board's verdict.
    rt_available = {
        "profile": _bp.rt_available(win),
        "distribution": _bp.rt_available(win),
        "blocks": _bp.rt_available(win),
    }

    # RT SUPPRESSION, applied by NOT COMPUTING the field rather than by nulling
    # it afterwards — so there is no code path where a suppressed rt average
    # exists in memory and could leak into a summary.
    if coverage["suppressed"]:
        fields, names = ("dam_lmp",), ("avg_dam",)
    else:
        fields, names = ("dam_lmp", "rt_avg"), ("avg_dam", "avg_rt")

    blocks = _bp.blocks_monthly(win, _nap_holiday, complete, fields, names)
    if coverage["suppressed"]:
        for b in blocks.values():
            for cell in b["monthly"]:
                cell["avg_rt"] = None

    # blocks_daily is the LAST 31 BANKED DAYS — banked, not calendar. A gap in
    # the bank shortens the span rather than producing an empty row.
    last31 = set(days_present[-31:])
    daily_rows = [r for r in win if r["trade_date"] in last31]
    blocks_daily = _bp.blocks_daily(daily_rows, _nap_holiday, fields, names)
    if coverage["suppressed"]:
        for cell in blocks_daily:
            cell["avg_rt"] = None

    return {
        "pnode_id": node,
        "window": window,
        "identity": identity,
        "profile": _bp.profile(win, "dam_lmp"),
        "profile_rt": _bp.profile(win, "rt_avg"),
        "heatmap": _bp.heatmap(win, "dam_lmp"),
        "distribution": _bp.distribution(win, "dam_lmp"),
        "distribution_rt": _bp.distribution(win, "rt_avg"),
        "tb": {f"tb{n}": _bp.tb_monthly(win, "dam_lmp", n, complete)
               for n in _bp.TB_N_VALUES},
        "blocks": blocks,
        "blocks_daily": blocks_daily,
        "basis": {
            "column": "basis_sp15",
            "per_he": _bp.profile(win, "basis_sp15"),
            "monthly": _bp.monthly_avg(win, "basis_sp15", complete),
        },
        "dart": {
            "column": "dart",
            "per_he": _bp.profile(win, "dart"),
            "monthly": _bp.monthly_avg(win, "dart", complete),
        },
        "rt_available": rt_available,
        "rt_coverage_note": coverage,
        "block_definitions": _bp.BLOCK_DEFINITIONS,
    }


# ── GET /api/hubs/{hub}/blocks ──────────────────────────────────────────────
#
# MEASURED DEPTH, 2026-08-12: dataset caiso_lmp_da_hourly carries series
# 'NP15', 'SP15' and 'ZP26' with 88,656 hourly rows each, 2016-01-01 ..
# 2026-08-13. That is ~10.6 years — years deeper than the nodal bank, which is
# the whole reason this endpoint reads timeseries_values rather than the nodal
# table (where the same hubs now also live; see the section header).
#
# `first_day`/`last_day` are MEASURED per request off the series, never
# asserted from this comment. last_day routinely reads one day AHEAD of today:
# the day-ahead curve for tomorrow publishes today, and this lane renders that
# rather than trimming it to look tidy.
#
# RECONCILED. Block values computed by this query's hour sets reproduce the
# pantry's independently-built `caiso_blocks_daily` to 0.00000000 on every day
# checked (SP15, 2026-07-01..08, blocks 7x16 and 7x24), and the 6x16 day-set
# matches its exclusions exactly over the table's full 2016-2026 depth.
_NAP_HUB_SQL = """
    SELECT (v.ts AT TIME ZONE %(tz)s)::date                              AS trade_date,
           EXTRACT(HOUR FROM v.ts AT TIME ZONE %(tz)s)::int + 1          AS he,
           v.value                                                      AS dam_lmp
      FROM timeseries_values v
     WHERE v.dataset = %(dataset)s
       AND v.series  = %(series)s
       AND v.value IS NOT NULL
     ORDER BY v.ts
"""

# Daily granularity over ten years would be ~23,000 objects on the wire. It is
# BOUNDED to the most recent N banked days and the bound is STATED in the
# response — a silent truncation would read as "this is all there is".
NAP_HUB_DAILY_DAYS = 400


@app.get("/api/hubs/{hub}/blocks")
async def hubs_blocks(
    hub: str = Path(..., description=f"One of {', '.join(NAP_HUBS)}."),
    granularity: str = Query(
        default="monthly",
        description="monthly (full measured depth) or daily (bounded, see response).",
    ),
):
    """
    The block set — 6x16 / off_peak / 7x8 / 7x16 / atc / 2x16h — on a hub's DA curve.

    Same calendar, same arithmetic and same response shape as the node
    package's `blocks`, computed from the hub day-ahead series in
    `timeseries_values`. Reading the hubs from the deep series rather than from
    the nodal table is a deliberate depth choice: the nodal bank begins
    2026-05-03, these series begin 2016-01-01.

    `depth` reports the series' MEASURED first/last day. Nothing here claims
    history it did not count, and `last_day` is often tomorrow because the
    day-ahead curve publishes forward.

    `granularity=daily` is bounded to the most recent banked days and says so
    in `bound`. `monthly` runs over the full measured depth.

    Only the three CAISO trading hubs resolve. The TH_*_GEN_ONPEAK-APND /
    _OFFPEAK-APND ids are CAISO's own PRE-BLOCKED apnodes carrying 16 and 8
    hours a day; running an hourly block calendar over them would silently
    misreport every block, so they are not accepted as aliases here.

    Errors: unknown hub -> 400; bad granularity -> 400; DB unavailable -> 503.
    """
    assert _pool is not None

    h = (hub or "").strip().upper()
    if h not in NAP_HUBS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown hub '{hub}': expected one of {', '.join(NAP_HUBS)}",
        )
    if granularity not in ("monthly", "daily"):
        raise HTTPException(
            status_code=400, detail="granularity must be monthly or daily")

    try:
        rows = await _an_read(_NAP_HUB_SQL, {
            "dataset": NAP_HUB_DATASET, "series": h, "tz": MARKET_TZ})
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    win = [{"trade_date": r["trade_date"], "he": int(r["he"]),
            "dam_lmp": float(r["dam_lmp"])} for r in rows]

    days = sorted({r["trade_date"] for r in win})
    first_day = days[0] if days else None
    last_day = days[-1] if days else None
    depth = {
        "series": h,
        "dataset": NAP_HUB_DATASET,
        "first_day": first_day.isoformat() if first_day else None,
        "last_day": last_day.isoformat() if last_day else None,
        "days_banked": len(days),
        "hours": len(win),
        "measured": "first_day/last_day counted from the series on this request",
    }

    body = {
        "hub": h, "granularity": granularity, "depth": depth,
        "block_definitions": _bp.BLOCK_DEFINITIONS,
        # The hub series carries a day-ahead price only — there is no real-time
        # leg in this dataset, so avg_rt is not fabricated as null-shaped
        # padding; the block rows simply carry avg_dam.
        "fields": {"avg_dam": "day-ahead hourly LMP, $/MWh"},
    }

    if granularity == "monthly":
        complete = _bp.month_completeness(first_day, last_day)
        body["blocks"] = _bp.blocks_monthly(
            win, _nap_holiday, complete, ("dam_lmp",), ("avg_dam",))
        body["bound"] = None
        return body

    keep = set(days[-NAP_HUB_DAILY_DAYS:])
    body["blocks_daily"] = _bp.blocks_daily(
        [r for r in win if r["trade_date"] in keep],
        _nap_holiday, ("dam_lmp",), ("avg_dam",))
    body["bound"] = {
        "days_returned": len(keep),
        "days_available": len(days),
        "rule": (f"daily granularity is bounded to the most recent "
                 f"{NAP_HUB_DAILY_DAYS} banked days; the full measured depth "
                 f"is in `depth` and monthly granularity covers all of it"),
    }
    return body


# ── GET /api/nodes/top-movers ───────────────────────────────────────────────
#
# THE RISKY AGGREGATE, MEASURED RATHER THAN ASSUMED (2026-08-12, live).
#
# The obvious form — one GROUP BY over a 7-day range — is a PARALLEL SEQ SCAN
# and it is not close:
#
#   SELECT pnode_id, avg(dart), count(DISTINCT trade_date)
#     FROM atlas_node_hourly_stats
#    WHERE trade_date BETWEEN '2026-08-05' AND '2026-08-11' AND dart IS NOT NULL
#    GROUP BY pnode_id ORDER BY avg DESC LIMIT 50
#
#   -> Parallel Seq Scan, Rows Removed by Filter: 15,198,614
#      Sort Method: external merge, 21.8 MB ON DISK
#      Execution Time: 24,861 ms
#
# Dropping count(DISTINCT) to get a HashAggregate does not save it (20,111 ms
# cold / 18,988 ms warm), and neither does `trade_date = ANY(array)`
# (18,283 ms). The planner will not use idx_node_hourly_trade_date for a 7-day
# slice of a 9.9 GB table, and the table does not fit the compute's cache, so
# the scan is genuinely paid every time.
#
# ONE DAY, however, IS index-served: 525 ms warm, 845 ms cold.
#
# So this endpoint CHUNKS ONE READ PER DATE and combines them in Python —
# exactly the idiom /api/analytics/movers already uses on the snapshot table
# for the same reason. Chunks run concurrently against the pool, so the wall
# clock is roughly the slowest chunk plus contention rather than their sum.
#
# THE WINDOW IS CAPPED AT 7 DAYS AND THE CAP IS STATED on the wire. It is a
# measurement, not a preference: at 7 chunks this is already the most expensive
# read in the lane. An over-cap request is a 400, never a silent truncation.
_NAP_MOVERS_DAY_SQL = """
    SELECT pnode_id,
           avg({col})   AS v,
           count(*)     AS n_hours
      FROM atlas_node_hourly_stats
     WHERE trade_date = %(day)s
       AND {col} IS NOT NULL
     GROUP BY pnode_id
"""

# The banked frontier, index-served off idx_node_hourly_trade_date. The lane
# NEVER hardcodes an end date — the bank grows daily.
_NAP_FRONTIER_SQL = "SELECT max(trade_date) AS last_day FROM atlas_node_hourly_stats"

_nap_movers_cache: dict = {}


@app.get("/api/nodes/top-movers")
async def nodes_top_movers(
    response: Response,
    metric: str = Query(
        default="dart",
        description=f"One of {', '.join(NAP_MOVERS_METRICS)}.",
    ),
    direction: str = Query(
        default="up", description="up (highest first) or down (lowest first)."),
    days: int = Query(
        default=7,
        description=f"Window in banked days, 1..{NAP_MOVERS_MAX_DAYS} (measured cap).",
    ),
):
    """
    Top-50 nodes by window-average DART or SP15 basis — the screening-page seed.

    The window ENDS at the warehouse's last banked day (read per request, never
    hardcoded) and runs back `days` banked dates.

    `value` is HOUR-WEIGHTED across the window: the chunks are combined by
    summing (mean x hours) and dividing by total hours, so a node present for
    three days does not outweigh one present for seven on a per-day mean.
    `days_present` and `hours` ride every row, so a node ranking on thin
    coverage is visible rather than hidden behind a rank.

    PERFORMANCE IS BOUNDED AND THE BOUND IS STATED. See `plan` in the response
    and the measurement note above this endpoint: the whole-range GROUP BY is a
    24.9 s seq scan, so the read is chunked per date (525-845 ms each,
    index-served) and capped at 7 days. Results are cached 15 minutes.

    Errors: bad metric/direction/days -> 400; DB unavailable -> 503.
    """
    assert _pool is not None

    if metric not in NAP_MOVERS_METRICS:
        raise HTTPException(
            status_code=400,
            detail=f"metric must be one of {', '.join(NAP_MOVERS_METRICS)}")
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="direction must be up or down")
    if not isinstance(days, int) or days < 1 or days > NAP_MOVERS_MAX_DAYS:
        raise HTTPException(
            status_code=400,
            detail=(f"days must be an integer in 1..{NAP_MOVERS_MAX_DAYS} — the "
                    f"cap is a measured bound, see the endpoint's plan note"))

    key = (metric, direction, days)
    hit = _nap_movers_cache.get(key)
    now = time.monotonic()
    if hit and (now - hit[0]) < NAP_MOVERS_TTL_S:
        response.headers["X-Cache"] = "hit"
        return hit[1]

    try:
        frontier = await _an_read(_NAP_FRONTIER_SQL, {})
        last_day = frontier[0]["last_day"] if frontier else None
        if last_day is None:
            raise HTTPException(status_code=503, detail="no banked rows")
        window_days = [last_day - _timedelta(days=i) for i in range(days)]

        # `metric` is validated against a closed tuple above, so this format is
        # not a SQL-injection seam — it is the only way to vary a COLUMN, which
        # cannot be a bound parameter.
        sql = _NAP_MOVERS_DAY_SQL.format(col=metric)
        chunks = await asyncio.gather(
            *[_an_read(sql, {"day": d}) for d in window_days])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")

    # Hour-weighted combine. Sum(mean x hours) / sum(hours) reconstructs the
    # true window mean from per-day means without re-reading the rows.
    acc: dict = {}
    for day, chunk in zip(window_days, chunks):
        for r in chunk:
            a = acc.setdefault(r["pnode_id"], {"wsum": 0.0, "hours": 0, "days": 0})
            n = int(r["n_hours"])
            a["wsum"] += float(r["v"]) * n
            a["hours"] += n
            a["days"] += 1

    ranked = [
        {"pnode_id": pid,
         "value": round(a["wsum"] / a["hours"], 4),
         "days_present": a["days"],
         "hours": a["hours"]}
        for pid, a in acc.items() if a["hours"] > 0
    ]
    ranked.sort(key=lambda x: x["value"], reverse=(direction == "up"))
    top = ranked[:NAP_MOVERS_TOP_N]

    body = {
        "metric": metric,
        "direction": direction,
        "window": {
            "days_requested": days,
            "first_day": min(window_days).isoformat(),
            "last_day": last_day.isoformat(),
            "derived": "last_day is max(trade_date) read per request",
        },
        "universe": {
            "nodes_ranked": len(ranked),
            "note": ("nodes with no non-null value in the window do not rank "
                     "and are not counted as zero"),
        },
        "count": len(top),
        "movers": top,
        "plan": {
            "shape": "one index-served read per date, combined hour-weighted in-process",
            "measured_ms_per_chunk": {"warm": 525, "cold": 845},
            "why_not_one_query": (
                "a single GROUP BY over the range is a parallel seq scan: "
                "24,861 ms with a 21.8 MB external merge (measured 2026-08-12)"),
            "cap_days": NAP_MOVERS_MAX_DAYS,
            "cache_ttl_s": NAP_MOVERS_TTL_S,
        },
    }
    _nap_movers_cache[key] = (now, body)
    response.headers["X-Cache"] = "miss"
    return body


# ═══════════════════════════════════════════════════════════════════════════
# /api/interconnection/* — the CAISO Interconnection Queue, v0 (2026-08-12)
# ═══════════════════════════════════════════════════════════════════════════
#
# WHAT THE BANK ACTUALLY HOLDS (read live 2026-08-12, schema-first):
#
#   caiso_interconnection_queue — 2,278 rows, ALL present_in_snapshot, 492,196.8
#   MW, ONE distinct snapshot_date (2026-08-07). That last fact is the important
#   one and it shapes this whole lane: the table is UPSERT-IN-PLACE, not an
#   append-only snapshot log. There is exactly one generation of the queue in
#   the bank, `first_seen_at` is the SAME instant on all 2,278 rows
#   (2026-06-11T01:03:57Z — the bulk birth of the table) and `last_updated` is
#   likewise uniform (2026-08-07T21:21:09Z). So `first_seen_at` is SERVED on
#   every row, as the lane requires, but it cannot yet distinguish a new entrant
#   from a founding row, and this module says so in `data_note` rather than
#   letting a chart imply lineage the column does not yet carry. The lineage
#   that DOES exist today lives in tape_interconnection_events.
#
#   tape_interconnection_events — POPULATED: 8 rows, every one event_type
#   'status_change', detected 2026-06-12 .. 2026-07-31, all on Fridays. So
#   /events serves real rows; the honest-empty branch is built and tested but is
#   not the live path.
#
#   CADENCE, MEASURED NOT ASSUMED: every banked snapshot_date across both tables
#   (2026-06-12, 06-26, 07-17, 07-24, 07-31, 08-07) is a Friday, at 8 distinct
#   dates over 8 weeks with three gaps. That is a weekly Friday refresh with
#   misses — which is what `data_note` says, in those words. It does not say
#   "daily", and it does not say "weekly" without the qualifier.
#
#   3 statuses: ACTIVE (268), COMPLETED (250), WITHDRAWN (1,760). 1,721 rows
#   carry a withdrawn_date — the 39-row gap is handled explicitly, see
#   interconnection._is_withdrawn.
#
# READ-ONLY. Three SELECTs, no DDL, no writes, and no table outside the two
# named above is touched.
#
# WHY THE SUMMARY IS ONE READ AND NOT SIX GROUP BYs: the fuel rollup is a parse
# in Python (41 free-text strings -> 9 display fuels), so `by_fuel` can never be
# a GROUP BY. Once one facet is computed in process, computing the others in SQL
# would let two facets of the same response describe two different universes.
# 2,278 slim rows is a single ~1 MB read; every facet is derived from it.
# ═══════════════════════════════════════════════════════════════════════════

import interconnection as _ic

IC_PREFIX = "/api/interconnection"

_ic_log = logging.getLogger("energylake.interconnection")

# The bank moves once a week at most (see the cadence note above), so these TTLs
# are about absorbing burst scans of the table, not about chasing a feed.
_IC_SUMMARY_TTL = 900.0
_IC_PROJECTS_TTL = 300.0
_IC_EVENTS_TTL = 300.0

_IC_EVENTS_DEFAULT_LIMIT = 50
_IC_EVENTS_MAX_LIMIT = 500

# `data_note` is assembled from what the read actually found — snapshot count,
# the first_seen_at spread, the tape's depth — never from a constant describing
# a cadence nobody measured.
_IC_SOURCE_NOTE = (
    "CAISO public interconnection queue, banked by the pantry. Snapshot cadence "
    "measured from the banked snapshot_dates across caiso_interconnection_queue "
    "and tape_interconnection_events: weekly, on Fridays, with gaps (8 distinct "
    "Friday dates 2026-06-12..2026-08-07). caiso_interconnection_queue is "
    "upsert-in-place and holds ONE generation of the queue, so first_seen_at is "
    "uniform across every row today and does NOT yet distinguish new entrants; "
    "queue-change lineage lives in tape_interconnection_events (/api/"
    "interconnection/events). No coordinates exist in the bank — geography is "
    "county/state only, and nothing here infers a location beyond them."
)


# ── The read ────────────────────────────────────────────────────────────────
# The slim column set the summary needs. Deliberately NOT `SELECT *`: `raw`
# (jsonb) and `withdrawal_comment` (free text) are the two fat columns and the
# summary reads neither, which is what keeps a 2,278-row full-table read cheap.
_IC_SUMMARY_SQL = """
    SELECT generation_type,
           capacity_mw::float8 AS capacity_mw,
           status,
           county,
           state,
           transmission_owner,
           queue_date,
           proposed_completion_date,
           withdrawn_date
    FROM caiso_interconnection_queue
    WHERE present_in_snapshot
"""

_IC_SNAPSHOT_SQL = """
    SELECT max(snapshot_date) AS snapshot_date,
           count(DISTINCT snapshot_date) AS snapshot_count,
           min(first_seen_at) AS first_seen_min,
           max(first_seen_at) AS first_seen_max,
           max(last_updated) AS last_updated
    FROM caiso_interconnection_queue
"""

# Every filter is a parameter, and every one uses the house optional-filter
# idiom (`%(p)s::type IS NULL OR ...`): ONE statement, ONE plan, no string
# building anywhere. The ONLY part of this query that varies is the ORDER BY,
# and it is substituted from `_IC_SORT_SQL` below — a code-side constant table
# keyed by the request value. A caller's `sort` string selects a key or it 400s;
# it never reaches the SQL text. `fuel` is absent on purpose: the rollup is a
# Python parse and is applied after the read, in `_ic.filter_by_fuel`.
_IC_PROJECTS_SQL = """
    SELECT queue_position, project_name, generation_type,
           capacity_mw::float8 AS capacity_mw,
           status, study_process, interconnection_location,
           county, state, transmission_owner, deliverability,
           queue_date, proposed_completion_date, actual_completion_date,
           withdrawn_date, withdrawal_comment,
           first_seen_at, last_updated, snapshot_date, present_in_snapshot
    FROM caiso_interconnection_queue
    WHERE present_in_snapshot
      AND (%(status)s::text IS NULL OR upper(status) = upper(%(status)s))
      AND (%(county)s::text IS NULL OR upper(county) = upper(%(county)s))
      AND (%(to)s::text IS NULL OR upper(transmission_owner) = upper(%(to)s))
      AND (%(min_mw)s::float8 IS NULL OR capacity_mw >= %(min_mw)s)
      AND (%(max_mw)s::float8 IS NULL OR capacity_mw <= %(max_mw)s)
      AND (%(queue_year)s::int IS NULL
           OR extract(year FROM queue_date) = %(queue_year)s)
      AND (%(q)s::text IS NULL
           OR project_name ILIKE %(q)s ESCAPE '\\'
           OR queue_position ILIKE %(q)s ESCAPE '\\')
    ORDER BY {order_by}, queue_position
"""

# request `sort` value -> the SQL expression it selects. Nothing else is
# reachable. NULLS LAST on every branch: 255 rows have no proposed COD, and a
# sort that floats them to the top of a "soonest first" table is a worse answer
# than one that puts the absences at the end.
_IC_SORT_SQL = {
    "capacity_mw": "capacity_mw",
    "queue_date": "queue_date",
    "proposed_completion_date": "proposed_completion_date",
    "project_name": "project_name",
}

_IC_EVENTS_SQL = """
    SELECT id, event_type, queue_position, project_name, generation_type,
           mw::float8 AS mw, old_values, new_values, detected_at, snapshot_date
    FROM tape_interconnection_events
    ORDER BY detected_at DESC, id DESC
    LIMIT %(limit)s
"""


async def _ic_read(sql: str, params: dict) -> list:
    """One read against the shared pool, retrying once on a stale connection.

    Same shape as `_dd_read`/`_cockpit_read` — this lane opens no connection of
    its own and holds nothing across requests.
    """
    assert _pool is not None
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            async with _pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(sql, params)
                    return await cur.fetchall()
        except _POOL_DEAD_CONN_ERRORS as e:
            last_exc = e
    assert last_exc is not None
    raise last_exc


# The dd cache is a general in-process cache with single-flight builds; this
# lane reuses it rather than growing a second one.
_ic_summary_cache = _DDCache("interconnection/summary", _IC_SUMMARY_TTL, max_entries=2)
_ic_projects_cache = _DDCache("interconnection/projects", _IC_PROJECTS_TTL, max_entries=64)
_ic_events_cache = _DDCache("interconnection/events", _IC_EVENTS_TTL, max_entries=8)

_IC_CACHES = (_ic_summary_cache, _ic_projects_cache, _ic_events_cache)


async def _ic_snapshot_chip() -> dict:
    """snapshot_date + the measured lineage facts behind `data_note`."""
    rows = await _ic_read(_IC_SNAPSHOT_SQL, {})
    r = rows[0] if rows else {}
    fs_min, fs_max = r.get("first_seen_min"), r.get("first_seen_max")
    return {
        "snapshot_date": r.get("snapshot_date"),
        "snapshot_count": r.get("snapshot_count"),
        "first_seen_at_min": fs_min,
        "first_seen_at_max": fs_max,
        "first_seen_at_is_uniform": (fs_min is not None and fs_min == fs_max),
        "last_updated": r.get("last_updated"),
    }


def _ic_data_note(chip: dict) -> str:
    """The source note, plus whatever THIS read found about lineage.

    Not a constant: if the pantry ever banks a second snapshot generation, the
    'first_seen_at is uniform' clause disappears from the response on its own,
    because it is derived from the read rather than from a comment.
    """
    note = _IC_SOURCE_NOTE
    if chip.get("first_seen_at_is_uniform"):
        note += (
            f" As measured on this read: {chip.get('snapshot_count')} distinct "
            f"snapshot_date(s) banked and first_seen_at identical on every row "
            f"({_ic._iso(chip.get('first_seen_at_min'))}), so first_seen_at "
            f"carries no entrant signal yet."
        )
    else:
        note += (
            f" As measured on this read: {chip.get('snapshot_count')} distinct "
            f"snapshot_date(s) banked and first_seen_at spanning "
            f"{_ic._iso(chip.get('first_seen_at_min'))}.."
            f"{_ic._iso(chip.get('first_seen_at_max'))}, so first_seen_at now "
            f"distinguishes entrants."
        )
    return note


# ── Parameter validation ────────────────────────────────────────────────────

def _ic_fuel_or_400(fuel: Optional[str]) -> Optional[str]:
    """`fuel` filters on the ROLLUP, so its vocabulary is ours, not CAISO's.

    Case-insensitive on the way in (a URL is typed by hand), exact on the way
    out. An unknown fuel is a 400 naming every legal value — never an empty
    result set that looks like "no such projects".
    """
    if fuel is None or not fuel.strip():
        return None
    wanted = fuel.strip().lower()
    for known in _ic.FUEL_ORDER:
        if known.lower() == wanted:
            return known
    raise HTTPException(
        status_code=400,
        detail=(f"unknown fuel: {fuel!r}. `fuel` filters on the display rollup, "
                f"not the raw generation_type. One of: {list(_ic.FUEL_ORDER)}"),
    )


def _ic_sort_or_400(sort: Optional[str]) -> str:
    """THE INJECTION SEAM, closed. A `sort` value is a KEY into a code-side
    table of SQL fragments; it is never interpolated, concatenated or quoted
    into the statement. Anything outside the whitelist is a 400 naming the
    whitelist — not a silent fall back to the default, which would let a hostile
    or merely mistyped sort look like it worked."""
    if sort is None or not sort.strip():
        return _ic.DEFAULT_SORT
    key = sort.strip()
    if key not in _IC_SORT_SQL:
        raise HTTPException(
            status_code=400,
            detail=(f"unknown sort: {sort!r}. One of: "
                    f"{sorted(_IC_SORT_SQL)}"),
        )
    return key


def _ic_order_or_400(order: Optional[str], sort_key: str) -> str:
    """asc | desc. Defaults per column (MW and dates read newest/biggest first,
    a name reads A-Z), and is likewise a key, never text spliced into SQL."""
    if order is None or not order.strip():
        return _ic.SORT_WHITELIST[sort_key][1]
    o = order.strip().lower()
    if o not in ("asc", "desc"):
        raise HTTPException(
            status_code=400, detail=f"unknown order: {order!r}. One of ['asc', 'desc']")
    return o


def _ic_status_or_400(status: Optional[str]) -> Optional[str]:
    if status is None or not status.strip():
        return None
    s = status.strip().upper()
    if s not in _ic.KNOWN_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown status: {status!r}. One of: {list(_ic.KNOWN_STATUSES)}",
        )
    return s


def _ic_order_by(sort_key: str, order: str) -> str:
    """Assemble ORDER BY from two validated KEYS. Both halves are constants
    chosen by dict lookup; there is no path from a request string to this text."""
    expr = _IC_SORT_SQL[sort_key]
    direction = "ASC" if order == "asc" else "DESC"
    return f"{expr} {direction} NULLS LAST"


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get(f"{IC_PREFIX}/summary")
async def interconnection_summary(response: Response):
    """Every aggregate for the interconnection tab, in ONE call.

        { snapshot_date, data_note, generated_from_rows,
          snapshot:              { snapshot_date, project_count, total_mw,
                                   statuses: [{status, count, mw}] },
          by_fuel:               { active: {fuel -> {count, mw}},
                                   all_statuses: {...},
                                   active_statuses: ["ACTIVE"] },
          by_queue_year:         [{year, count, mw}]  — the 27-year vintage chart
          by_proposed_cod_year:  { years: [{year, count, mw}], undated: {...} },
          by_county:             { top: [{county, state, count, mw}], other,
                                   group_count, top_n },
          by_transmission_owner: [{to, count, mw}],
          attrition:             { years: [{year, enqueued_*, withdrawn_*,
                                            surviving_*, withdrawn_mw_share}],
                                   totals, no_queue_date,
                                   withdrawn_status_without_date, definition },
          unmapped_types:        [{generation_type, fuel, unmapped_components,
                                   known, count, mw}],
          cache: {...} }

    THE FUEL ROLLUP IS A PARSE AND IT IS NEVER SILENT. 41 free-text
    generation_type strings roll up to 9 display fuels; any string containing a
    component no keyword recognises is named in `unmapped_types` with a `known`
    flag against the 41 banked at recon time, and any string that recognises
    nothing lands in `Other` with its raw label in `by_fuel[*].Other.raw_types`.
    "Steam Turbine" (158 rows, 38.3 GW) is deliberately NOT mapped to Natural
    Gas — see R1 in interconnection.py. `Geothermal` matches zero rows today and
    is still emitted, at count 0, because an empty bucket is a fact about the
    queue and a missing one would be a fact about our code.

    ABSENCE IS STATED. Null proposed CODs are `undated` with their own count and
    MW — never dropped, never zero-dated. `attrition` uses the ruled definition
    (withdrawn = withdrawn_date IS NOT NULL) and reports beside it the 39 rows /
    10,857.1 MW that CAISO calls WITHDRAWN with no date, which that definition
    cannot see. `by_county` is a top-25 with the remainder aggregated as `other`
    and the full `group_count` stated — a bounded list, never a silent truncation.

    One full-table read of a slim column set (~2,278 rows), all facets derived
    in process so they cannot disagree; cached 15 minutes against a bank that
    moves weekly. DB unavailable -> 503.
    """
    assert _pool is not None

    async def _build():
        chip = await _ic_snapshot_chip()
        rows = await _ic_read(_IC_SUMMARY_SQL, {})
        return _ic.summary_payload(
            rows,
            snapshot_date=chip.get("snapshot_date"),
            data_note=_ic_data_note(chip),
        )

    payload, state, entry = await _ic_summary_cache.serve("summary", _build)
    return _dd_envelope(payload, state, entry, _ic_summary_cache, response)


@app.get(f"{IC_PREFIX}/projects")
async def interconnection_projects(
    response: Response,
    status: Optional[str] = Query(
        None, description="ACTIVE | COMPLETED | WITHDRAWN. Case-insensitive; "
                          "anything else is a 400 naming the three."),
    fuel: Optional[str] = Query(
        None, description="Filters on the display ROLLUP (Solar, Battery "
                          "Storage, Solar+Storage Hybrid, ...), not on the raw "
                          "generation_type. Unknown value -> 400."),
    county: Optional[str] = Query(None, description="Exact county, case-insensitive."),
    to: Optional[str] = Query(
        None, description="Transmission owner, exact + case-insensitive "
                          "(SCE, PGAE, SDGE, DCRT, GLW, VEA, DSLK, IID)."),
    min_mw: Optional[float] = Query(None, description="capacity_mw >= this."),
    max_mw: Optional[float] = Query(None, description="capacity_mw <= this."),
    queue_year: Optional[int] = Query(None, description="year(queue_date) == this."),
    q: Optional[str] = Query(
        None, description="Case-insensitive substring over project_name AND "
                          "queue_position. LIKE metacharacters match literally."),
    sort: Optional[str] = Query(
        None, description="capacity_mw | queue_date | proposed_completion_date "
                          "| project_name. Anything else is a 400."),
    order: Optional[str] = Query(
        None, description="asc | desc. Defaults per column: desc for MW and "
                          "dates, asc for project_name."),
    page: int = Query(1, ge=1, description="1-based."),
    page_size: int = Query(
        _ic.PAGE_SIZE_DEFAULT, ge=1, le=_ic.PAGE_SIZE_MAX,
        description=f"<= {_ic.PAGE_SIZE_MAX}."),
):
    """The faceted project table, paged.

        { snapshot_date, data_note, sort, order, filters, unmapped_types,
          page: { page, page_size, total, page_count, has_more },
          rows: [ { queue_position, project_name, generation_type, fuel,
                    unmapped_components, capacity_mw, status, study_process,
                    interconnection_location, county, state,
                    transmission_owner, deliverability, queue_date,
                    proposed_completion_date, actual_completion_date,
                    withdrawn, withdrawn_date, withdrawal_comment,
                    first_seen_at, last_updated, snapshot_date,
                    present_in_snapshot } ],
          cache: {...} }

    EVERY ROW CARRIES BOTH generation_type (raw, as CAISO wrote it) AND fuel
    (our rollup). A client that disagrees with the rollup can re-derive its own;
    neither is hidden behind the other.

    SQL INJECTION POSTURE. Every filter is a bound parameter under the house
    `%(p)s::type IS NULL OR ...` idiom — one statement, one plan, no string
    building. `sort` and `order` are KEYS into code-side constant tables of SQL
    fragments; a value outside either whitelist is a 400 naming the whitelist,
    never a silent fallback. There is no path from a request string into the
    statement text, and tests/test_interconnection.py proves a hostile `sort`
    400s rather than executing.

    `fuel` is applied AFTER the read, in process, because the rollup is a parse
    over free text and cannot be a SQL predicate. Ordering, `page.total` and the
    page slice are therefore all computed over the fuel-filtered set — a page is
    never a post-filtered fragment of some larger SQL page. `unmapped_types`
    describes the whole filtered result set, not the visible page.

    A page past the end is an empty `rows` with a truthful `total`, not a 404.
    DB unavailable -> 503.
    """
    assert _pool is not None

    status_v = _ic_status_or_400(status)
    fuel_v = _ic_fuel_or_400(fuel)
    sort_key = _ic_sort_or_400(sort)
    order_v = _ic_order_or_400(order, sort_key)

    if min_mw is not None and max_mw is not None and min_mw > max_mw:
        raise HTTPException(
            status_code=400,
            detail=f"min_mw ({min_mw}) is greater than max_mw ({max_mw})",
        )

    q_like = f"%{_cockpit_like_escape(q.strip())}%" if (q and q.strip()) else None

    params = {
        "status": status_v,
        "county": county.strip() if (county and county.strip()) else None,
        "to": to.strip() if (to and to.strip()) else None,
        "min_mw": min_mw,
        "max_mw": max_mw,
        "queue_year": queue_year,
        "q": q_like,
    }
    filters = {
        "status": status_v,
        "fuel": fuel_v,
        "county": params["county"],
        "to": params["to"],
        "min_mw": min_mw,
        "max_mw": max_mw,
        "queue_year": queue_year,
        "q": q.strip() if (q and q.strip()) else None,
    }

    key = (tuple(sorted(filters.items(), key=lambda kv: kv[0])),
           sort_key, order_v, page, page_size)

    async def _build():
        chip = await _ic_snapshot_chip()
        sql = _IC_PROJECTS_SQL.format(order_by=_ic_order_by(sort_key, order_v))
        rows = await _ic_read(sql, params)
        rows = _ic.filter_by_fuel(rows, fuel_v)
        return _ic.projects_payload(
            rows,
            page=page, page_size=page_size,
            sort=sort_key, order=order_v, filters=filters,
            snapshot_date=chip.get("snapshot_date"),
            data_note=_ic_data_note(chip),
        )

    payload, state, entry = await _ic_projects_cache.serve(key, _build)
    return _dd_envelope(payload, state, entry, _ic_projects_cache, response)


@app.get(f"{IC_PREFIX}/events")
async def interconnection_events(
    response: Response,
    limit: int = Query(
        _IC_EVENTS_DEFAULT_LIMIT, ge=1, le=_IC_EVENTS_MAX_LIMIT,
        description=f"<= {_IC_EVENTS_MAX_LIMIT}."),
):
    """The tape join: banked queue changes, newest first.

        { count, limit, event_types, note,
          rows: [ { id, event_type, queue_position, project_name,
                    generation_type, fuel, mw, changed_fields,
                    old_values, new_values, detected_at, snapshot_date } ],
          cache: {...} }

    THIS IS THE LINEAGE THE QUEUE TABLE CANNOT GIVE. caiso_interconnection_queue
    is upsert-in-place and holds one generation, so a project's history is not
    recoverable from it; tape_interconnection_events is where a change is
    actually recorded. Verified populated 2026-08-12: 8 rows, all
    event_type='status_change', detected 2026-06-12..2026-07-31, each an
    ACTIVE -> WITHDRAWN or ACTIVE -> COMPLETED transition.

    `changed_fields` is derived from the keys of old_values/new_values so a
    client can name what moved without diffing two jsonb blobs.

    HONEST EMPTY. If the ledger holds nothing the response is 200 with
    `rows: []`, `count: 0` and a `note` saying so. Nothing on this path
    reconstructs an event from the queue snapshot, infers one from a
    withdrawn_date, or invents history the bank does not hold.
    DB unavailable -> 503.
    """
    assert _pool is not None

    async def _build():
        rows = await _ic_read(_IC_EVENTS_SQL, {"limit": limit})
        return _ic.events_payload(rows, limit=limit)

    payload, state, entry = await _ic_events_cache.serve(limit, _build)
    return _dd_envelope(payload, state, entry, _ic_events_cache, response)
