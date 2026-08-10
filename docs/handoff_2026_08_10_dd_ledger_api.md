# Handoff — Degree Day Ledger, Phase B (`energylake-api`) — 2026-08-10

Lane spec: *CC SPEC — Degree Day Ledger, Phases B + C, 2026-08-09*.
Branch: `claude/new-session-eb290z` (the session's designated branch; the spec
suggested `claude/dd-ledger-api` — flagging the divergence rather than pushing
to a branch nobody assigned me).

Phase A shipped the composite and nothing rendered it. This is the pipe.

---

## What shipped

| endpoint | source | cost | cache |
|---|---|---|---|
| `GET /api/weather/dd/regions` | `dd_region_weights_active` | trivial (22 rows) | 15 min |
| `GET /api/weather/dd/daily` | `dd_region_daily_vs_normal` | **3 ms** | 5 min |
| `GET /api/weather/dd/cumulative` | `dd_region_mtd` + `dd_region_std` | **~52 s** | 30 min + stale-while-revalidate + startup warm |
| `GET /api/weather/dd/socalgas-grade` | `dd_socalgas_grade` | 294 ms | 5 min |
| `GET /api/weather/dd/forecast` | `station_degree_days_forecast` | index-served (0 rows today) | 5 min |

Files: `degree_days.py` (new, pure shaping layer), `main.py` (SQL + routes +
cache), `tests/test_weather_dd_ledger.py` (new, 37 tests).

**No migration is declared or needed** — every object already exists on
production. Ledger swept both ways: this lane references migrations 159 / 160 /
161 / 164, and `SELECT max(version) FROM schema_migrations` on production
returns **164** (152 rows banked). That matches the spec's reading; no drift.

No `dd_*` view was materialised. No index was added. Nothing under
`.github/workflows/` was touched. No PR opened.

---

## R1 — recon answers

**1. How weather endpoints are shaped, authenticated, cached.**
There is no auth: the API is a read-only window and CORS is the only gate
(`ALLOWED_ORIGINS` plus a Vercel-preview origin regex). The house pattern for a
weather read is: module-level SQL constant → thin `async def` route taking
`response: Response` → in-process dict cache `{key: (monotonic_stamp, payload)}`
checked at the top of the handler → `response.headers["Cache-Control"]` mirroring
the TTL → `except Exception` on the DB read raising **503** (`db unavailable:`).
The pure composition lives in a sidecar module (`almanac.py`, `structures.py`,
`paper_desk.py`) and is tested directly; the route gets one or two tests through
a `_FakePool`. This lane follows that pattern; it does not invent one.

**2. Existing caching layer + the house convention for a slow upstream.**
No Redis, no HTTP cache middleware. Every cache in this repo is in-process and
per-endpoint, and the TTL is chosen from the upstream's cadence, not from
latency:

- `_DELTA_BOARD_CACHE_TTL = 60` and `_METEOGRAM_CACHE_TTL = 60` (NWS issuance cadence)
- `_ATLAS_CONSTRAINTS_CACHE_TTL = 300`, `_ATLAS_FP_CACHE_TTL = 300`
- `_STRUCT_CATALOG_CACHE_TTL = 900` — the closest precedent to a genuinely
  expensive read, and the one endpoint that already emits `X-Cache: hit|miss`
  instead of `Cache-Control`.

The house convention for a slow upstream is therefore: cache it in-process,
state the TTL on the response, and 503 when the DB is gone. What the house did
**not** already have is a stale-serving path or a self-reported age. This lane
adds both (see below) because a 52-second upstream needs them and the spec
requires them; both are additive and no existing endpoint changed.

**3. How the Kelvin Tier-1 tiles read `forecasts_nws`.**
Three read shapes, all over the `series / issued_ts / target_ts / value / meta`
column set:

- `/api/weather/temp-matrix` — a `latest` CTE (`MAX(issued_ts) GROUP BY series`)
  joined back to the table, so only the newest issuance per station series is
  read; hourly targets are bucketed into the station's **local** calendar date
  via its IANA tz, and each cell carries `hours_covered` + `partial`.
- `/api/weather/delta-board` — a lookback window (`issued_ts >= now - N days`)
  pulled raw, then bucketed in Python (`_compute_delta_board`) into synoptic
  issuances; the board's number is the run-over-run change between the two most
  recent buckets over **shared hours only**.
- `/api/weather/meteogram` — per-series, current issuance solid plus up to two
  ghosted priors from the same synoptic bucketing.

The pattern this lane inherits from those three is the **run-over-run delta**:
the current issuance is a level, and the number that moves a position is how the
level changed. `/api/weather/dd/forecast` ships that subtraction from day one
rather than adding it later.

---

## THE COST — the spec's central number is wrong, with receipts

The spec states, as the constraint that shapes both phases:

> `dd_region_mtd` and `dd_region_std` cost ~1.4 s per region-scoped query …
> Measured, not estimated (2,021,249 buffers).

**That is not what production does.** Measured 2026-08-10 against
`fancy-block-96153928` (`EXPLAIN (ANALYZE, BUFFERS)` for buffers, and
`clock_timestamp() - statement_timestamp()` for wall clock):

| query | rows | measured | spec's claim |
|---|---|---|---|
| `dd_region_mtd WHERE region=… AND obs_date=…` | 1 | **29,325 ms**, **9,071,557 buffers** | 1.4 s, 2,021,249 buffers |
| `dd_region_std WHERE obs_date=…` (all 5) | 5 | **22,418 ms** | — |
| `dd_region_daily_vs_normal` 1 region × 92 days | 92 | **3 ms** | implied slow |
| `dd_region_daily_vs_normal` 5 regions × 102 days | 490 | **3 ms** | implied slow |
| `dd_socalgas_grade` full table | 29,956 | **294 ms** | — |
| `dd_region_daily` `max(obs_date)` bounded 60 d | 1 | **2 ms** | — |
| `dd_region_daily` `max(obs_date)` unbounded | 1 | **1,600 ms** | — |

So the slow pair is **16–21× the spec's figure**, and 4.5× its buffer count.

**The spec's stated reason is also only half right.** It says
`dd_region_daily` "re-derives ~30,000 days from `station_degree_days_daily` ×
the weight map on every call". The re-derivation is real, but it only costs when
the *window function* blocks pushdown. `dd_region_daily` and
`dd_region_daily_vs_normal` keep date pushdown and answer in single-digit
milliseconds; only `dd_region_mtd` / `dd_region_std`, which add the
`WindowAgg`, lose it. **`/daily` therefore needs no cache to be fast** — it got a
short one for burst scans, not for latency.

### The lever the plan hands you: the region predicate buys nothing

From the 164 plan:

```
CTE base
  ->  Merge Left Join (actual rows=172656 loops=1)
        Rows Removed by Join Filter: 63019440
  ->  CTE Scan on base b (actual rows=1 loops=1)
        Filter: ((region = 'socalgas_territory') AND (obs_date = '2026-07-31'))
        Rows Removed by Filter: 172655
```

All 172,656 composite rows are materialised **before** either predicate applies.
One cell costs exactly what five regions cost.

**Consequence for the spec's third number.** It says an uncached page render of
"five regions × two views is ~14 s". Done the obvious way — one call per region —
it is **5 × (29 + 22) ≈ 260 s**. Built board-wide it is ONE MTD read plus ONE STD
read: **~52 s**, once.

---

## What I cached, and why

`/cumulative` builds **the whole board — all five regions, both views — in one
pair of reads**, and the `?region=` parameter filters the *cached board*, never
the query. That is the entire design consequence of the plan above: narrowing
the SQL would buy nothing and would turn one 29 s build into five.

Cache policy, in `_DDCache`:

- **fresh** (age < TTL) → served, `cache.state = "fresh"`, `X-Cache: hit`.
- **stale** (age ≥ TTL) → served **immediately** with `cache.state = "stale"` and
  a background refresh started behind it. A stale number wearing its age is
  honest; a fresh-looking stale number is the failure this desk refuses.
- **miss** → built inline, **single-flight**. One lock per cache, so five
  concurrent first-hits collapse into one 52 s read rather than five, and the
  `max_size=5` pool keeps four connections free throughout.
- **startup warm** — `lifespan` kicks off a detached, fail-soft build of the
  default `as_of`, so the cold 52 s is normally paid by the service at boot
  rather than by a browser. Opt out with `DD_WARM_ON_STARTUP=0`.
- **240 s build ceiling** → 504 rather than hanging forever.

TTL 1800 s. At two builds an hour that is ~104 s of DB work per hour (~2.9% duty
cycle on the slow pair) — stated rather than assumed.

**Every response states its own age**, on every one of the five endpoints:

```json
"cache": { "state": "stale", "built_at": "2026-08-10T…Z", "age_seconds": 1841.2,
           "ttl_seconds": 1800.0, "build_seconds": 51.9, "refreshing": true }
```

plus `X-Cache: hit|miss|stale`, `Age: <seconds>`, and `Cache-Control: max-age=<ttl>`.
There is no path through this code that returns a number without saying how old
it is.

### What I did *not* do

I did not sum daily rows into a cumulative. `dd_region_daily_vs_normal` is right
there and it is 3 ms — adding up 31 of its rows would produce an MTD total in a
fraction of the 164 view's time. It would also reproduce the 402.75 defect one
layer *deeper* than any post-check can reach, and it would silently invent a
completeness rule the views already publish. The slow view is the arbiter; we
wait for it once and cache it.

---

## Absence is a first-class response value

Every cumulative block carries `absence` — `null` when the totals are real, and
otherwise one of four reasons, each decided by a boolean the **view** already
published. Nothing in it is computed here.

| reason | meaning | counts carried |
|---|---|---|
| `incomplete_obs` | `complete = false` — the "89 of 92 days" case | `days_complete`, `days_in_window` |
| `incomplete_norm` | `normals_complete = false` — total may be whole, departure has no baseline | + `normals_days_complete` |
| `no_season` | STD, `season = null` — a shoulder month has no ruled anchor, which is *different from* an incomplete season | nulls |
| `no_row` | the view returned nothing for this (region, as_of) | nulls |

A missing row returns the **same key set** as a present one, so the client has
one branch, not two. `/daily` carries `basis_complete`, `normals_complete`,
`members_present`/`member_stations`, `missing_stations`,
`normals_missing_stations` and `min_sample_count` on **every** row.

---

## FINDINGS FOR PHASE C — read these before building the surface

**1. The seasonal departure is NULL for all five regions right now.**
At the current right edge (`as_of = 2026-08-06`), every `dd_region_std` row has
`normals_complete = false`, so `hdd_norm`, `cdd_norm`, `hdd_departure` and
`cdd_departure` are all NULL fleet-wide:

| region | days | complete | cdd | cdd_departure |
|---|---|---|---|---|
| desert_sw | 98 of 98 | true | 2381.59 | **null** |
| pnw | 98 of 98 | true | 342.18 | **null** |
| caiso_np15_proxy | 95 of 98 | false | null | null |
| caiso_sp15_proxy | 96 of 98 | false | null | null |
| socalgas_territory | 96 of 98 | false | null | null |

Phase C's part 2 — "cumulative departure over the active season per region" —
**cannot be drawn from `/cumulative` today.** Daily departures
(`dd_region_daily_vs_normal.cdd_departure`) *are* available and complete, but
accumulating them client-side is precisely the fill the spec's R3 forbids. Either
the chart plots *daily* departure (honest, and available now), or it waits for
the STD normals basis. That is a captain's call, not mine.

**2. `dd_region_std.normals_days_complete` looks wrong.** It reports **219**
against `days_in_window = 98` on the same row, for all five regions. On
`dd_region_mtd` the same pair is consistent (6 and 6 on 2026-08-06). 219 > 98 is
not a coverage count of this window. I did not touch the view (no migrations in
this lane); the API stops short of formatting a sentence around it —
`incomplete_norm` renders `"normals basis incomplete for this window"` rather
than `"normals cover 219 of 98 days"`, and both raw counts ride as fields. Worth
a look before Phase C renders that number.

**3. The SoCalGas arbiter numbers moved.** Spec says "mean +0.75 °F, sd 2.46,
mean|delta| 1.78 over **5,637** graded days". Measured 2026-08-10:

| statistic | measured | spec |
|---|---|---|
| graded days | **6,069** | 5,637 |
| rows in scope | 29,956 | — |
| mean delta | +0.7546 | +0.75 ✓ |
| sd (sample) | 2.4551 | 2.46 ✓ |
| mean abs delta | 1.7787 | 1.78 ✓ |
| range | −10.25 … +20.10 | — |
| graded span | 2009-12-15 … 2026-08-03 | — |

The three statistics round exactly to the spec's; only the day count is stale.
Phase C should render `summary.all_time.n_graded` rather than hard-coding 5,637.

**4. `station_degree_days_forecast` holds 0 rows** (measured — the builder has
not had its maiden run). The endpoint ships the run-over-run delta anyway, as
instructed, and serves `board_state: "empty"` with an `absence` naming the
reason rather than a 404 or a bare `[]`. A target date seen for the first time
gets `delta_absence.reason = "no_prior_issuance"` — a first sighting is not a
zero-change day.

**5. The ledger's right edge is 2026-08-06, not today (2026-08-10).** Every
default window in this lane anchors on the composite's measured edge
(`obs_through`), never on `now`, and `/daily` and `/cumulative` both report it.
GHCND's 2–4 day publication lag is visible in the payload, so Phase C can draw
the obs/forecast seam where it actually is.

**6. The spec's own header for Phase C.** `vector_version` is on every payload
(`"v1"`), and `/regions` carries each vector's members, weights, `vintage` and
per-region `note`. `weight_sum` is **reported, not asserted** — all five sum to
1.00 today, and if one stops the number says so instead of a normalization
hiding it.

---

## R3 — tests and mutation receipts

`tests/test_weather_dd_ledger.py`, **37 tests**. Full suite: **1,268 passed**.

Contract tests over the real view shapes (fixture columns verified against
production `information_schema` plus live reads, not invented):

- a complete region-month returns numbers — socalgas July 2026 = 421.25 CDD vs
  372.755 normal, +48.495, `absence: null`;
- an incomplete one returns nulls **with counts** — np15 at 30 of 31 days →
  `cdd: null` and `absence.message: "30 of 31 days"`;
- an incomplete STD likewise — socalgas at 90 of 92;
- the shoulder months (Apr 15, Oct 20) return a **row** per region with
  `season: null` and every total null → `absence.reason: "no_season"`;
- the live 2026-08-06 STD shape: observed total serves (2381.59) while the
  departure does not, `reason: "incomplete_norm"`.

### Mutation receipts

**Mutation 1 — coalesce a NULL total to 0** (the one the spec names). In
`degree_days._f`, `return None` → `return 0.0`:

```
9 failed, 27 passed
FAILED test_incomplete_region_month_returns_nulls_with_counts   assert 0.0 is None
FAILED test_incomplete_season_to_date_returns_nulls_with_counts
FAILED test_shoulder_months_return_season_null_and_no_std[shoulder0]
FAILED test_shoulder_months_return_season_null_and_no_std[shoulder1]
FAILED test_normals_incomplete_is_its_own_reason
FAILED test_last_year_deltas_are_the_views_arithmetic_not_ours
FAILED test_ungraded_day_is_not_a_zero_delta_day
FAILED test_grade_summary_sd_is_null_below_two_graded_days
FAILED test_cumulative_route_serves_the_board_and_names_its_absences
```

Reverted; 1,268 pass. The assertions are `is None`, not falsiness — `assert not
x` would pass for `0.0` and is exactly the check that lets the defect through.

**Mutation 2 — a stale number wearing a fresh face.** In `main._dd_envelope`,
`age = max(0.0, time.monotonic() - entry.built_mono)` → `age = 0.0`:

```
1 failed, 35 passed
FAILED test_stale_board_is_served_labelled_rather_than_rebuilt_on_the_request
   assert 0.0 > 1800.0
```

Reverted.

---

## Verification performed, and its one limit

Every SQL statement in this lane was executed against production
(`fancy-block-96153928`, branch `br-dark-morning-ajosxafs`) with parameters
substituted, and returned the expected rows: `_DD_REGIONS_SQL`,
`_DD_OBS_THROUGH_SQL`, `_DD_DAILY_SQL`, `_DD_MTD_SQL`, `_DD_STD_SQL`,
`_DD_GRADE_ROWS_SQL`, `_DD_GRADE_SUMMARY_SQL`, `_DD_FORECAST_SQL`. All reads,
no writes.

**The limit, stated plainly:** I could not boot the ASGI app end to end against
Neon from this session. Outbound TCP :5432 is blocked by the environment's
network policy (only HTTPS through the agent proxy is permitted), so
`AsyncConnectionPool` times out. The SQL is verified; the *handler wiring* is
verified by the route tests through a fake pool, not by a live HTTP round trip.
First deploy should curl all five endpoints — in particular `/cumulative` cold,
to confirm the ~52 s build and the startup warm behave on Railway's timeouts.

---

## Handback checklist

- **Compare link:** `https://github.com/Austinr100/energylake-api/compare/main...claude/new-session-eb290z`
- **No PR opened** (lane rule).
- **Migration:** none needed; ledger read **164** by both sweep and `max(version)`.
- **Screenshot of the ledger section showing a complete and an incomplete
  region:** Phase C, `energylake-dashboard` — not this repo. The API side of
  that receipt is the `/cumulative` payload above, where desert_sw and pnw carry
  numbers and the other three carry `absence` with their day counts.
