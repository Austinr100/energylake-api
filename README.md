# energylake-api

Read-only FastAPI window onto the EnergyLake Neon pantry.
Milestone 1 of the May 20 roadmap — the pipe that the Vercel frontend
needs before anything renders.

```
pantry (Neon)  ->  this API (Railway)  ->  Vercel frontend
```

This service never writes to the pantry. Ingestion stays in
`energylake-pantry`. This repo only serves what already exists.

---

## 1. Schema is CONFIRMED (no edits needed)

The fuel-mix query was written against the real schema, confirmed via
the Neon SQL Editor on 2026-05-22:

```
timeseries_values(ts timestamptz, dataset text, series text,
                  value numeric, meta jsonb, ingested_ts timestamptz)
dataset = 'caiso_fuel_mix_hourly'  -- 758k rows, 2020-01 -> current, hourly
series  = batteries, biogas, biomass, coal, geothermal, imports,
          large_hydro, natural_gas, nuclear, other, renewables,
          small_hydro, solar, wind   (values in MW)
```

Two data facts the chart side must respect (see comments in `main.py`):
- `renewables` is a roll-up overlapping the granular renewable fuels —
  chart the granular fuels OR `renewables`, never both (double-count).
- Negative values are REAL (batteries charging, net exports) — let the
  stacked area go below zero; do not clamp.

## 2. Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env with the real NEON_DATABASE_URL
# load .env into the shell (or use a tool like python-dotenv / direnv):
export $(grep -v '^#' .env | xargs)   # Windows PowerShell: see note below
uvicorn main:app --reload
```

Windows PowerShell env load:
```powershell
Get-Content .env | Where-Object {$_ -notmatch '^#' -and $_ -match '='} | ForEach-Object {
    $k,$v = $_ -split '=',2
    [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim().Trim('"'))
}
```

Verify:
```bash
curl http://localhost:8000/health
# {"status":"ok","db":"ok"}

curl "http://localhost:8000/api/timeseries/caiso-fuel-mix?limit=24"
# [{"ts":"...","solar":18550.0,"wind":4696.0,"batteries":-1845.0, ...}, ...]

curl "http://localhost:8000/api/timeseries/caiso-fuel-mix?limit=24&shape=long"
# [{"ts":"...","series":"solar","value":18550.0}, ...]
```

If `/health` is ok but fuel-mix 404s, the dataset name is wrong — but it
was confirmed on 2026-05-22, so a 404 more likely means you're pointed at
a different database than the pantry.

## 3. Deploy to Railway

1. Push this repo to GitHub (`energylake-api`).
2. In Railway: New Project -> Deploy from GitHub repo -> pick `energylake-api`.
3. Railway auto-detects Python via Nixpacks; `railway.json` / `Procfile`
   give it the start command and healthcheck.
4. In the service's **Variables** tab, set:
   - `NEON_DATABASE_URL` (same string the pantry uses)
   - `ALLOWED_ORIGINS` (your Vercel domains, comma-separated)
5. Railway assigns a public URL. Hit `https://<url>/health` to confirm.

## 4. Wire the frontend (Milestone 2)

In Vercel project env vars, add:
```
NEXT_PUBLIC_API_BASE=https://<your-railway-url>
```
Then fetch `${process.env.NEXT_PUBLIC_API_BASE}/api/timeseries/caiso-fuel-mix`
from a server component or route handler and render the first chart
(stacked area; allow the y-axis below zero for batteries/imports).

---

## Endpoints

- `GET /health` -> `{"status":"ok","db":"ok"}`
- `GET /api/timeseries/caiso-fuel-mix?limit=24&shape=pivot`
  -> newest-first hours, one object per hour with a key per fuel (MW)
- `GET /api/timeseries/caiso-fuel-mix?limit=24&shape=long`
  -> newest-first, one object per fuel per hour
- `GET /api/timeseries/caiso-hub-lmp`
  -> CAISO trading-hub LMP (NP15/SP15/ZP26) for the previous + current PT trade
  date: per hub `da`/`rtpd`/`rtd` arrays plus server-derived `dart`
  (DA − avg RTPD per hour, + = DA over RT), a `latest` ticker block, and
  on/off-peak DA `peak` averages. Fixed window, read-only, no parameters
- `GET /api/market-clock`
  -> the single deterministic CAISO market state for the Ticker chip and every
  downstream surface (briefs, email arc, Paper Desk) — one state, computed once,
  zero LLM. `state` ∈ `DA_BIDDING` → `DA_MARKET_RUNNING` (10:00 PT bid close) →
  `DA_PUBLISHED` (awards detected in the lake) → `RT_LIVE` (live FMM tape is the
  overnight headline); target trade date = tomorrow PT. **Publication detection
  ("the lake holds the rows") is the sole authority for DA_PUBLISHED/RT_LIVE** —
  the clock can never claim awards the lake lacks. Payload: `state`/`label`/
  `detail`/`trade_date`/`next_expected{event,at}`/`prints{sp15_da?,latest_fmm?}`/
  `as_of`/`degraded`/`degraded_feeds`/`sources[]`. The NERC/weekend calendar is
  block-vocabulary context only (the DAM clears a full 24h every calendar day, so
  a cycle is never suppressed — Sunday/holiday just reads "off-peak all hours" in
  `detail`). A stale FMM feed (>60m) or an overdue DAM sets `degraded` naming the
  feed; DB unavailable -> 503. Read-only, no parameters
- `GET /api/newswire/recent?limit=20&since_days=30`
  -> Joule-rewritten Newswire headlines + captions, newest first
- `GET /api/tape/recent?limit=20&since_days=1500` **(DEPRECATED — use `/api/wire/recent`)**
  -> Joule Tape headlines for power-sector SEC filings, newest first
- `GET /api/briefs/daily/latest`
  -> most recent `brief_type='daily'` Joule brief (404 if none)
- `GET /api/briefs/daily/{date}`
  -> daily Joule brief for an ISO date `YYYY-MM-DD` (422 on bad date, 404 if none)
- `GET /api/wire/recent?stream=&limit=50`
  -> power-signal tape filings (same shape as `/api/tape/recent` plus
  `is_power_signal`); optional `stream` = `sec_filing` | `ir_press`;
  `limit` default 50, hard-capped at 200
- `GET /api/regulatory/board?include_resolved=&body=`
  -> the `regulatory_board` view as JSON (`{as_of, count, items[]}`), newest by
  `salience DESC, importance DESC, body ASC`. Default = on-board items only;
  `include_resolved=true` returns all. Optional general `body` filter: one or
  comma-separated (e.g. `?body=CAISO`, `?body=FERC,CPUC`) over FERC, CPUC,
  CAISO, NERC, CARB, NRC, CA_LEG, REGIONAL, BPA; unknown bodies -> 400
- `GET /api/atlas/pnode-lmp?market=RTD`
  -> latest CAISO pnode-LMP snapshot for one market — prices only, no geometry
  (the map joins client-side on `pnode_id` against the pnode geometry). Columnar
  payload: top-level `market`/`snapshot_vintage`/`market_date`/`market_hour`/
  `market_interval`/`feed_generated_at`/`pnode_count`/`expected_pnode_count`/
  `complete`/`fell_back` plus six order-aligned arrays sorted by `pnode_id`
  (`pnode_id`, `lmp`, `energy`, `congestion`, `loss`, `ghg`; NULL components pass
  through as JSON null). `market` ∈ `RTD` (default) / `RTPD` / `DAM`. Selects the
  newest market instant via a **two-step index-only query** (latest instant, then
  its rows — replacing the old table-wide `COUNT(DISTINCT)` scan-and-sort that ran
  ~46 s; now sub-second). A ≥90%-of-expected-universe row-count floor guards
  against a partial mid-dispatch write: a thin latest instant falls back one
  interval and serves the fuller of the two, flagged `fell_back:true`; a still-
  thin serve carries `complete:false` (**never silently thin**). Staleness is
  expected (dispatch-only feed) and surfaced via the timestamp fields, not an
  error. Unknown market -> 400; **no instant at all** (empty/expired table) -> 503
  (**note the deliberate 503-not-404 convention fork** — valid market + zero rows
  is a data-availability condition, not a missing resource; a present-but-thin
  instant is a `complete:false` 200, not a 503). `Cache-Control: max-age=60`

- `GET /api/atlas/pnode-history?pnode_id=KRNCNYN_6_N001&market=RTD&hours=24`
  -> raw 7-day price-component **history** for one CAISO pnode from the same
  hot tier — prices only, no geometry. A pure range scan (no completeness
  gating; partial trailing instants are honest data). Columnar payload:
  top-level `pnode_id`/`market`/`hours`/`known`/`row_count` plus order-aligned
  arrays sorted ascending by instant — three market-coordinate arrays
  (`market_date`, `market_hour`, `market_interval`; `market_interval` is null
  for hourly DAM) and five component arrays (`lmp`, `energy`, `congestion`,
  `loss`, `ghg`; NULL passes through as JSON null). **No server-derived
  timestamp**: the hot tier carries no market-instant `timestamptz`, so the
  client builds the ISO time axis from the three coordinate arrays using its
  HE/interval conventions (and computes DART = FMM − DAM client-side). `market`
  ∈ `RTD` (default) / `RTPD` / `DAM` — `FMM` is a display name only and is
  rejected 400 (send `RTPD`). `hours` ∈ `24` (default) / `168`, coarsened to a
  whole-day filter server-side. Blank/unknown `pnode_id`, bad `market`, or
  `hours`∉{24,168} -> 400; a **known** pnode with no rows in the window -> 200
  with empty arrays and `known:true`; DB unavailable -> 503. DAM all-zero
  sentinel rows pass through as-is (no-price is a client render concern).
  `Cache-Control: max-age=60`

- `GET /api/weather/temp-matrix`
  -> the 17-station WECC basket (ordered N→S from `station_metadata.json`), each
  with a D1–D7 forecast hi/lo grid. Per station we use ONLY the latest issuance
  of its `{station_id}_temperature` NWS series (hourly, Celsius), bucket each
  hourly target into the station's **local** calendar date via its IANA tz
  (`D1` = that station's local date at request time), and take max/min per day
  (converted to °F). Each cell carries `hours_covered` + `partial` (< 18h) so
  D1's afternoon partial is surfaced, not hidden; anomalies (`anom_hi/anom_lo`,
  one decimal) are vs `station_normals_daily` (`window_label` carried in the
  envelope — never hardcode a year range). A station with no issuance in the
  last 24h is `degraded:true` with null cells and its last `issued_ts` — always
  17 rows, never dropped. DB unavailable -> 503
- `GET /api/weather/regime`
  -> the driver/regime panel: five teleconnection chips (`oni`, `roni`, `pdo`,
  `qbo`, `iod_dmi`) with latest value, `as_of` month, and `staleness_days` vs
  today (never filtered — staleness is displayed); ONLY ONI carries a
  deterministic `warm`/`cool`/`neutral` band. Plus CPC outlook vintages (6-10 &
  8-14 temp/precip) as metadata (issued/valid window, format) with `lean:null,
  lean_status:"pending render leg"` — parsed probabilities are the deferred
  render leg, never faked from R2. Archive `depth` (min issued_date) once in the
  envelope. DB unavailable -> 503
- `GET /api/weather/station-walk`
  -> per station (N→S), the latest daily observation vs normal: each station's
  OWN latest `obs_date` (as-of grammar — a station lagging the shared latest
  still appears at its own date), `tmax/tmin` (°C→°F), `hdd/cdd`,
  `basis_complete`, the (month, day) normals (`hdd_norm/cdd_norm/tavg_norm_f`),
  obs−norm anomalies (one decimal), and `days_behind`. Always 17 rows;
  `window_label` + per-row `sample_count` carried through. DB unavailable -> 503

- `GET /api/model-room/cycles?model=gfs&days=7`
  -> the published D2 synoptic cycles for `model` over the last `days` UTC dates,
  newest first, wrapped in the platform envelope:
  `{"cycles": [{model, date, cycle}]}` (date = `YYYYMMDD`, cycle = two-digit
  UTC hour). A cycle is "published" when its seed artifacts are present under
  `d2/synoptic/{model}/{date}/{cycle}Z/` in the R2 archive (v0: asserted by
  object count; manifest-presence is a marked seam). Bad model slug -> 400;
  R2 token unprovisioned -> 503

- `GET /api/model-room/frame/{key}`
  -> streams one archived D2 frame object (PNG or JSON) straight from R2 with a
  long immutable cache (`Cache-Control: public, max-age=31536000, immutable`).
  The key is validated against the `d2/` prefix allowlist BEFORE it touches R2
  (no traversal, no other prefixes) — a bad key is rejected 400/403 and never
  reaches the bucket. Missing object -> 404; R2 token unprovisioned -> 503

  Both Model Room routes read from a **read-only** R2 token scoped to `d2/` on
  the archive bucket (see `R2_*` in `.env.example`). This service never writes
  to the bucket; `boto3` is imported lazily, only when a Model Room route is hit.

### Weather Atlas B — the point API (`/api/weather/point*`)

The tiles carry the hover; **this carries the click.** Atlas A writes a *value
sidecar* beside every rendered frame — a raw little-endian float32 array
`{param}_f{fhr}.f32` plus a `.json` header describing its geometry — and these
two routes turn a (lat, lon) into a byte offset in that array and read
**exactly four bytes** out of it over HTTP `Range`.

**The array never enters the container.** The `na3` crop is 222x583 float32 =
517,704 bytes per frame, and a 41-frame ladder is 21 MB. `bytes_read` rides on
every response as the standing assertion that none of it was fetched: 4 for a
click, 164 for the whole ladder. Geometry, decode, key scheme and the range
store live in `weather_point.py`; the store takes an injectable transport, so
the whole suite runs against a synthetic sidecar with no network.

- `GET /api/weather/point?lat=&lon=&param=&run=&fhr=&model=gfs&crop=na3&chain=0`
  -> one cell: `{value, units, display, cell:{lat,lon,j,i,distance_km},
  source:{key, sha256, offset, range, bytes_read: 4}, header:{...axes},
  verified: false}`. `?chain=1` adds the charter §7 rung stub
  (`temperature|degree_day|load|lmp`), first rung filled and the rest dark with
  a stated reason apiece.

- `GET /api/weather/point/ladder?lat=&lon=&param=&run=&fhr_step=6&fhr_max=240`
  -> the same cell across the forecast ladder: **41 four-byte range GETs, one
  header, at most 8 in flight**, `full_object_reads: 0`. A frame the writer has
  not reached yet fails on its own row (`available: false` + its own error) —
  the other forty still carry values.

  Four behaviours are load-bearing and each is pinned in
  `tests/test_weather_point.py`:
  * **NaN is an answer, not an error.** The crop's off-domain corners and the
    antimeridian strip hold NaN; they come back `200` with `value: null` and
    `reason: "nodata"` — never `0.0`.
  * **Outside the crop is a 404 that states the bounds**, never a nearest-edge
    value: an edge cell handed back silently is indistinguishable from a real one.
  * **`verified` is always `false`, and says why.** The header's sha256 is over
    the whole object and a four-byte read cannot check it. The chain is proven
    in Atlas A's write-time manifest, not per click.
  * **A store that ignores `Range`** (200 + the whole object) is refused with a
    502 rather than absorbed — honouring it would make `bytes_read: 4` a lie on
    the very response reporting it.

  Longitudes are normalised onto the sidecar's own axis: `na3` runs
  -186.75..-41.25 under `west_negative_monotonic`, a monotonic run that walks
  past -180 so the western Aleutians have columns without a seam. A click there
  arrives as +173.50 and is moved onto the axis by a single 360-degree shift.

  Config: `WEATHER_VALUES_BASE_URL` (a public/CDN base — no credentials used at
  all) **or** the `WEATHER_VALUES_*` R2 vars (see `.env.example`). Note the
  Model Room's `R2_*` token is scoped to `d2/` and generally cannot read
  `weather/values/`, hence the separate set. Unconfigured -> 503, and the rest
  of the API is unaffected: `httpx` is imported inside the store, never at
  module import. **No new dependencies** — the R2 request signer is ~40 lines of
  stdlib `hmac`/`hashlib` rather than a boto3 import.

### The Structures room (`/api/analytics/structures/*`)

Room 2 of the Analytics Department: swaps, monthly-average (Asian) options and
HRCOs. **A calculator and a replay engine — not a pricing desk and not advice.**
There is no option premium, no volatility, no greeks and no forward curve
anywhere in this lane; the payoff arithmetic lives in `structures.py` as a pure
module. Three rails ride in every payload (`posture.rails`), because a consumer
that can drop them will:

1. **No forward pricing.** Payoff is computed *given* your inputs and *realized*
   history. An HRCO's payoff diagram is struck off the most recent **banked**
   gas print, labelled with its date, and withheld entirely when none is banked.
2. **Depth-honest.** A month is banked **whole** or it is not replayed — no
   interpolation, no partial-month averages — and every gated month comes back
   with a receipt saying why. The expected block-hour count is derived per date
   from the tz database (`7x24` is 23/24/25 across the DST switch, `7x8` is
   7/8/9), never a hardcoded calendar.
3. **Rankings are descriptive sorts** of historical arithmetic. Legs that cannot
   be replayed are returned with their reason rather than dropped.

- `GET /api/analytics/structures/catalog`
  -> the banked-reality menu the builder panel reads, and the only menu it may
  offer: structures, blocks, replay-capable legs with **measured** depth per
  block, and every banked gas index by name with its cadence, depth and
  staleness in days. Also carries the gas-leg STOP-gate verdict and the filed
  data-roadmap gaps. Cached 15 min in-process (`X-Cache`). DB unavailable -> 503

  Measured depth as shipped: hubs **SP15/NP15/ZP26 carry 118 banked complete
  7x16 months** (2016-01..2026-06) — the completeness gate reproduces this
  repo's documented coverage holes (2016-02, 2016-08, 2019-01) with no
  special-casing. **Nodal legs carry zero**: 2,376 series begin 2026-06-07, so
  June is short its first six days and July is mid-publication. Nodal replay is
  honest absence, *computed* rather than hardcoded, so it lights up on its own.

- `POST /api/analytics/structures/evaluate`
  -> structure definition in; payoff diagram + month-by-month replay out.
  Stateless (saved structures are Phase 2). Body: `structure`, `leg`, `block`,
  `size_mw`, plus the structure's own fields (`fixed_price` / `strike` /
  `payout` / `heat_rate` + `vom_adder` + `gas_index`) and `window_months`
  (null = all banked months). The diagram is a pure function of the inputs, so
  it renders even when the replay is empty. A bad field is a 400 that **names**
  it; DB unavailable -> 503

- `GET /api/analytics/structures/screener?structure=&strike=&window_months=&legs=`
  -> one structure swept across legs, ranked by realized payoff both directions.
  Bounds are explicit and stated in the payload: the three hubs by default,
  nodal legs opt-in via `legs` and capped at 40. An over-cap request is a 400 —
  never a silent truncation. Runtime is stamped on every response
  (`runtime_ms`, `X-Query-Ms`).

  Measured: **75 ms** for 3 hubs at 24 months (the default shape), 907 ms at
  full 118-month depth, 1,470 ms for 40 nodal legs. Every read is index-served
  by `idx_tsv_series_ts (dataset, series, ts DESC)`.

  Two deviations from the room's spec, both stated in the payload rather than
  papered over: the leg picker **cannot** be pointed at `/api/nodes/search`
  (that lane browses an 8-day hot tier whose 14,827 `pnode_id`s do not
  intersect the replay-capable series at all — the component is reused, its
  universe comes from `/catalog`); and **`6x16` is absent** from the block menu,
  because the NERC on-peak block needs a holiday calendar and this codebase
  holds two that disagree (`main._NERC_HOLIDAYS` uses *observed* dates,
  the pantry's `caiso_blocks_daily` 6x16 lane uses *actual* dates). The three
  blocks shipped are holiday-independent and reconcile with that lane to the
  last decimal.

### The Nodes room (`/api/analytics/node*`, `/api/analytics/movers`)

Room 1 of the Analytics Department: search a node, get its package. Three routes
serving the Node Analytics Package. All arithmetic is over banked data; every
response carries `depth` and `as_of`.

- `GET /api/analytics/node-search?q=&limit=`
  -> up to 50 nodes from the PRICED universe (the latest DAM instant) whose
  `pnode_id` OR Atlas plant name contains `q`; prefix matches sort first. Each
  match carries `node_type`, `area`, coordinates, and the paired hub. `name` is
  the Atlas plant name or **null** — `atlas_pnode_universe.description` is the
  pnode_id for 21,362 of 24,098 rows, so it is not a gazetteer and is not used;
  only 376 priced nodes have a real name. Bad `limit` -> 400; no matches -> 200
  with `[]`; DB unavailable -> 503

- `GET /api/analytics/node/{pnode_id}`
  -> the package, in five stanzas: `identity` (name/type/coordinates + paired
  hub), `dart` (per-HE mean + the p5/p25/p50/p75/p95 envelope over banked depth,
  plus the latest banked day's realized DART per HE and the band it fell in),
  `basis` (node DAM − hub DAM per HE, with 7x16 / off-peak / calendar-month
  cuts), `components` (energy/congestion/loss/ghg window means per market, each
  with its own `n`), and `ledger` (the monthly 7x16 table, N per row, derived
  from `basis` so the two cannot disagree).
  Unknown/unpriced pnode_id -> 400; a known node with nothing banked -> 200 with
  honest empty stanzas; DB unavailable -> 503

- `GET /api/analytics/movers?window=1d|7d&metric=dart|basis`
  -> top-50 both directions, universe-wide, with `coverage` (ranked / excluded
  incomplete / excluded unpaired), the measured `runtime_ms`, and the
  `query_plan` it actually rode. Cached 5 minutes per (metric, window). Bad
  window/metric -> 400; DB unavailable -> 503

Three facts worth knowing before you consume these:

- **`DART = FMM − DA`, positive = RT above DA.** This is the ratified nodal
  convention (same as `/api/atlas/pnode-history`). The OLDER `spread` field on
  `/api/timeseries/caiso-hub-lmp` and the Watchboard `dart` tile use the
  **opposite** sign (DA − RT). The field here is always named `dart`, never
  `spread`, so the two never get mixed. Mixing them draws an inverted chart with
  no error anywhere.
- **Depth is eight calendar dates**, not weeks. `atlas_pnode_lmp_snapshot` is a
  ~7-day hot tier: DAM and RTPD both span 2026-07-22..2026-07-29 with six
  complete days. So every per-HE band rests on 6–8 samples (each states its own
  `n`), and the monthly ledger is ONE row for 2026-07 with `complete: false`.
- **Basis needs a CAISO trading hub, and most nodes do not have one.** Pairing
  rule v0 reads `atlas_pnode_universe.th_hub` verbatim — no geographic
  inference. Coverage of the 14,827-node priced universe: 1,579 overall (10.7%),
  but 1,487 of 1,860 CA-area GEN nodes (79.9%). For an unpaired node the basis
  and ledger stanzas are `available: false` plus a reason — never zeros.

---

### Node Analytics v0 — package + block pricing (`/api/nodes/*`, `/api/hubs/*`)

Room 2 of the Analytics Department, built on the **durable** hourly bank
(`atlas_node_hourly_stats`) rather than the ~7-day snapshot the Nodes room
reads. Measured live 2026-08-12: 46.2 M rows, **2026-05-03 .. 2026-08-11, 101
distinct dates over a 101-day span — gapless** (counted, not assumed), ~19.3 k
nodes x 24 HE.

- `GET /api/nodes/{pnode_id}/package?window_days=all|30|90`
  -> the whole node page in one call: `identity`, `profile` / `profile_rt`
  (per-HE p25/p50/p75), `heatmap` (month x HE), `distribution` /
  `distribution_rt` (GridStatus bin edges), `tb` (`tb2` + `tb4` in
  **$/kW-month**), `blocks` (monthly, all six), `blocks_daily` (last 31 banked
  days), `basis`, `dart`, `rt_available`, and `rt_coverage_note`. Unknown id ->
  400; known id with nothing banked -> 200 with honest empties; DB unavailable
  -> 503
  - `tb.tb2` / `tb.tb4` and every `blocks.<block>` carry the same
    `summary: {avg, median, max, min, n_months}`, over **complete months only**
    — a half month still ships in `months`, flagged `partial`, and is simply
    kept out of the summary
  - `basis` / `dart` are `{column, per_he: [{he, p25, p50, p75, ...}], monthly:
    [{month, avg, ...}]}` — `per_he` is the same stanza key the Analytics room's
    ladders use, so the two rooms' ladders read alike
  - every `blocks_daily` row carries `partial`: **true** when the bank does not
    hold the whole trade date yet (the frontier day — the request landed
    mid-afternoon and the day's later hours have not published). The averages on
    a flagged row are over the hours that landed, which is why the flag travels
    with them. Completeness is 23/24/25 clock hours, counted — never a flat 24,
    or every spring-forward day would read partial forever
  - `rt_available: {profile, distribution, blocks}` — one bool per board that
    can draw a real-time series, each evaluated by the coverage rule over that
    board's own window. `rt_coverage_note` stays the single prose explanation;
    these three are the verdict a board reads. Nothing banked -> `false` on all
    three (`suppressed: false` there means the rule never fired, not that RT is
    drawable)

- `GET /api/hubs/{hub}/blocks?granularity=monthly|daily` — `SP15|NP15|ZP26`
  -> the same six blocks on the hub day-ahead curve from `timeseries_values`.
  **Measured depth 2016-01-01 .. 2026-08-13** (88,656 hourly rows per series) —
  years deeper than the nodal bank, which is why the hubs are read here and not
  from the nodal table. `depth` carries `days_banked` — a **counted** distinct
  day count, beside `first_day`/`last_day`: spanned is not banked, and a series
  with a hole says so rather than implying the span. `daily` is bounded to the
  most recent 400 days and says so in `bound`, and its rows carry the same
  `partial` flag as the node lane's

- `GET /api/nodes/top-movers?metric=dart|basis_sp15&direction=up|down&days=7`
  -> top-50 by hour-weighted window average, `days_present` and `hours` on every
  row. Chunked one index-served read per date; `days` capped at 7

**The block definitions (WECC), and they are the whole point:**

| block | definition | nominal h/day |
|---|---|---|
| `6x16` | HE7–22, Mon–**Sat**, excluding NERC holidays (On-Peak/HLH) | 16 |
| `off_peak` | every hour not in 6x16 (Wrap/LLH) | 8 |
| `7x8` | HE1–6, HE23, HE24, all seven days | 8 |
| `7x16` | HE7–22, all seven days (the PPA settlement cut) | 16 |
| `atc` | all hours (7x24) | 23/24/25 |
| `2x16h` | HE7–22 on Sundays + NERC holidays | — |

Four things worth knowing before you consume these:

- **`6x16` ships here, and it is reconciled.** The Structures room withheld 6x16
  because no lane had checked the block *arithmetic* against the pantry's own
  `caiso_blocks_daily`. That check is now done: over 2016-01-01..2026-08-10 the
  only days that table excludes from 6x16 while keeping 7x16 are Sundays (528)
  plus exactly the six observed NERC holidays a year — including **2026-07-04,
  a Saturday, which stays excluded** and so confirms the no-shift rule. Values
  match to `0.00000000`.
- **`hours` is a counted receipt, never the nominal 16/8/24.** If the calendar
  silently stopped excluding holidays, `2x16h` would report zero hours and
  `6x16` too many — visible on the wire without reading any code. `atc` reports
  23 and 25 on the DST switch days for the same reason.
- **Absence is stated, never zero-filled.** An HE, month or day the bank does
  not carry is *absent* from its array. The one deliberate exception is
  `distribution`, where every bin ships even at `n: 0` — the bins are a fixed
  axis the client draws, and a missing bar would silently rescale the chart.
- **`name` and `zone` are thinly covered and say so.** `atlas_pnode_zone` is
  **empty (0 rows)**, so no LAP-zone attribution exists at any coverage; `zone`
  is `atlas_pnode_universe.th_hub` verbatim, null for ~90% of the universe.
  `name` is the Atlas plant name or a `description` that genuinely differs from
  the id — both null is common and honest.

---

### The Paper Desk (`/api/desk/*`)

Five read-only stanzas over `paper_journal`, the table the desk agent writes
and this service only ever reads. The arithmetic lives in `paper_desk.py`,
which is pure — no I/O, no clock, no database; the routes read rows and hand
them over. Zero LLM: this lane is arithmetic over 19 banked rows, and
`tests/test_paper_desk.py` greps both the pure layer and every route function
for language-service vocabulary.

- `GET /api/desk/blotter`
  -> every `kind='bid'` row, newest first, each carrying `trade_date`,
  `pnode_id`, `direction`, `size_mw`, `price_limit`, `hour_scope`,
  `conviction`, `settled`, `settle_da`, `settle_fmm`, `pnl_per_mwh`,
  `pnl_dollars`, `filled_hours`, `settled_at`, `unsettled_reason` — plus
  `entry_id`, `constraint_id` and `frontier_ok`. **Status is derived
  server-side** and never stored: settled → `SETTLED`; unsettled with no reason
  → `OPEN`; unsettled with a reason → `PENDING`, whose `unsettled_reason` is
  served **verbatim** (render the string; it is never mapped to a code)

- `GET /api/desk/book`
  -> open count + gross MW (sum of `|size_mw|`), pending the same, the settled
  record (`win`/`loss`/`flat`/`unclassified` + `win_rate`), cumulative
  `pnl_dollars`, and the unweighted `avg_pnl_per_mwh` with its `n`

- `GET /api/desk/equity`
  -> cumulative settled `pnl_dollars` by `trade_date`, ascending, **settled rows
  only**. One point per date that actually carries a settlement — no
  interpolation, no zero-fill, no carry-forward

- `GET /api/desk/by-node` / `GET /api/desk/by-play`
  -> per-pnode and per-play aggregates: `n`, win rate, total P&L, avg
  pnl/MWh, plus open count and open gross MW. Ordered by P&L descending; a
  group with nothing settled sorts last but is **never dropped**

Five things worth knowing before you consume these:

- **Notes are not bids.** `paper_journal` holds two species keyed by `kind`:
  `note` (the Morning Pre-Bid Read — prose, no position) and `bid`. The filter
  is in the SQL *and* re-asserted in `paper_desk.bid_rows`, so prose can never
  inflate a position count.
- **A position marks only at settlement.** There is no live intraday mark in
  v0. `settled` is the sole gate on every P&L figure: an OPEN or PENDING row
  contributes nothing anywhere, even if a P&L column has been stamped on it. A
  banked-hours running mark is filed as v1 debt, deliberately not built.
- **P&L is read, never recomputed.** `pnl_dollars` / `pnl_per_mwh` arrive
  already signed from the settlement writer, which owns the direction
  convention. `settle_da` / `settle_fmm` are carried for display and are never
  operands — re-deriving a sign here would put a second, competing settlement
  convention in the building.
- **A zero-fill is FLAT, never a loss.** A settled bid that never cleared
  (`filled_hours = 0`) transacts no MWh, so it books no P&L. A settled row with
  a real fill and no P&L is `unclassified` — a hole in the ledger, counted and
  surfaced rather than laundered into flat. `win + loss + flat + unclassified
  == settled.n`, always.
- **`play` is `null`, and that is the finding.** The writer carries no
  machine-readable play key: `inputs_as_of` holds `map.map_id` (the agent
  program — identical on every row, so it cannot discriminate a screen),
  `bid.*` position fields, and `bid_screen.facts[]` (free prose). The screen
  name appears only in the `rationale` PROSE under "WHICH SCREEN FIRED", and
  regexing an English sentence to key an aggregate would make the aggregate a
  property of the writer's prose style. All bids fall into one `play: null`
  group (so the counts still tie to the blotter) and `key` reports what was
  searched. **Writer fix, one field:** stamp `inputs_as_of.bid.screen` ∈
  `persistence | phantom | surprise` — the lookup is already live and the
  stanza groups on it with no API change.

Depth, measured 2026-07-31: 19 rows — 17 notes and 2 bids, both filed
2026-07-30, both `OPEN`, both `frontier_ok = false`. **Nothing has settled
yet**, so the equity curve is honestly `points: []` and the settled record is
zeros with `win_rate: null`. The settled branches are tested against hand-built
rows and light themselves when the settlement writer runs. Empty is `[]` and
thin is `null`, never `0.0`. DB unavailable -> 503, never a fabricated empty
desk.

> Two of the notes above have since been overtaken by the table itself, and the
> Lab Paper Desk below is where that is reflected. **Voids now exist** (8 of 14
> bids), and `/api/desk/*` reads every one of them as `PENDING` — correct for
> its own vocabulary, wrong for a page. And **`play` is no longer `null`**: the
> writer took the one-field fix. `/api/desk/by-play` still serves `null`
> because its `key.available` contract distinguishes full coverage from
> partial, and 12 of 14 is partial; the Lab lane groups on the real key.

---

### The Lab Paper Desk (`GET /api/lab/paper-desk`)

**The whole desk in one payload, off one read** — `blotter` + `equity` +
`by_node` + `by_play` + `book` under a single `as_of`. Same table as
`/api/desk/*`, deliberately **not** the same contract; the arithmetic is in
`paper_lab.py`, pure, which reuses `paper_desk.py`'s coercions and its
`play_of` lookup so there is one implementation of the play key in the
building. No new tables, no writes, no migrations, one query shape.

It exists alongside `/api/desk/*` for two contract-level reasons:

- **Voids are the product, not noise.** `/api/desk/*` has no void concept — a
  voided position *is* an unsettled row carrying a reason, so it reads
  `PENDING` there. That was invisible on 2026-07-31 (nothing had been voided)
  and is not invisible now: **8 of the 14 banked bids are voided double-books**,
  and a page that files them under "pending" tells the desk it is waiting on
  settlements that are never coming. Here `status` is three-valued —
  `settled` / `pending` / `void` — **voids ride the blotter**, every aggregate
  excludes them, and `book.n_void` counts them.
- **One instant.** The `/api/desk` handoff filed it as debt: five stanzas, five
  reads, so a client rendering all five could mix two instants. This is that
  debt paid.

```
status:   settled = true               -> "settled"   (terminal, checked first)
          reason starts "voided"       -> "void"
          anything else                -> "pending"   (there is no "open" here)
```

Per blotter row: `entry_id`, `trade_date`, `pnode_id`, `author`, `direction`,
`size_mw`, `price_limit`, `hour_scope`, `status`, `unsettled_reason`,
`settle_da`, `settle_fmm`, `pnl_per_mwh`, `pnl_dollars`, `settled_at`,
`settled_by_version`, `rationale`, `inputs_as_of`, `curve_points` — plus
additive `play`. `curve_points` is a **count** of the bid's rows in
`paper_bid_curves` (every banked bid carries 16: HE7-22, one segment per hour),
not the curve itself. `rationale` and `inputs_as_of` **are** on the wire here,
unlike `/api/desk/blotter` — this page is an audit surface, and the point is
that a reader can see what a position was taken on.

Four things worth knowing before you consume it:

- **The equity curve is drawn by `settled_at`, not `trade_date`** — when the
  money was booked, not when the position was taken. It will **not** agree with
  `/api/desk/equity` and is not meant to: the one settled bid was taken
  2026-07-31 and settled 2026-08-06. `settled_at` is normalized to **UTC** on
  the wire, because the curve buckets on that string's calendar date. Dates
  with nothing settled produce no point — draw the gap.
- **A void-only group still appears**, with zeroes and its `n_void`. Today
  that is `LUNDY_7_N003`: 7 bids, **every one of them voided**. Dropping it
  would take the desk's whole void history off the page, which is the failure
  this endpoint exists to prevent. `n_void` is additive on `by_node` and
  `by_play` for exactly this reason — without it a void-only row is
  indistinguishable from a node that never traded.
- **The play key is LIVE.** The `/api/desk/by-play` handoff filed a one-field
  writer fix — stamp `inputs_as_of.bid.screen` — and **the writer took it**.
  Measured 2026-08-06 it is stamped on 12 of 14 bids (`surprise` ×6,
  `persistence` ×6) and this lane groups on it for real. The 2 rows without it
  predate the fix, are both voided, and honestly read `"unclassified"` — they
  are not backfilled and no play is guessed for them. The rationale prose is
  still never parsed. `derivation.play` ships the measurement.
- **P&L is read, never recomputed.** `settle_da` / `settle_fmm` sit on the wire
  right beside `pnl_dollars`; they are display columns and never operands.
  `book.settled_pnl` is exactly the sum of the blotter's visible `pnl_dollars`
  column and exactly the curve's last `cumulative_pnl`.

Depth, measured 2026-08-06: 38 rows — 24 notes and 14 bids spanning
2026-07-30..2026-08-05. **1 settled** (−$918.93), **5 pending**, **8 void**;
`n_settled + n_pending + n_void == len(blotter)`, always. Empty journal ->
every list `[]` and a zeroed book. DB unavailable -> 503, never a fabricated
empty desk.

---

## The Almanac (2026-08-07)

The publication surface. Three read-only endpoints over `publications`
(migration 154), published-only — **drafts never serve**.

- `GET /api/almanac?series=daily`
  -> the shelf: **a bare JSON array** of newest-first cards across every series
  (or one, with `?series=`). Per card:
  `{series, issue_key, headline, dek, issued_ts, verified, read_minutes}`
- `GET /api/almanac/{series}`
  -> `{series, intro, latest: <issue>, archive: [{issue_key, headline,
  issued_ts}]}` — the newest issue in full, plus every **older** issue as a
  stub. The latest is never repeated in the archive, so a one-issue series
  serves `archive: []`.
- `GET /api/almanac/{series}/{issue}`
  -> one issue: `{series, issue_key, headline, dek, author, issued_ts,
  verified, verifier_version, data_cutoff_ts, read_minutes, body}`

`series` is one of `daily | weekly | monthly | article`. An unknown series is a
`404` on a path and a `400` on `?series=` — never a silently empty shelf.

**The contract is pinned verbatim and carries no unannounced fields.** Lane B
builds against these three shapes; every one is asserted as an exact key set,
in order. All dates and timestamps are ISO strings. One announced amendment
(2026-08-07): the **issue** shape carries `read_minutes` — same derivation as
the shelf card, placed before `body` — so THE DESK'S READ can render the
affordance off `latest` without inventing it.

- **`body` is served exactly as stored** — an ordered list of render blocks,
  `{"type":"prose","md":…}` or
  `{"type":"figure","component":…,"params":{…},"as_of":…}`. Never reshaped,
  never reordered, never block-validated on the way out; a block type the API
  has never heard of still serves. The writer owns what a block says.
- **`read_minutes` is the one derived field**: prose words / 200, rounded up,
  floored at 1 — and **0** for a body with no prose. Figure blocks count zero;
  a figure is looked at, not read.
- **A draft is a 404, byte-identical to an issue that was never written.** Not
  a 403 — a 403 would confirm the draft exists.
- **A `verifier_version` of `null` does not mean unverified.** `verified` is
  the field that answers that, and it is never null on the wire.
- A series in the vocabulary with nothing published is a **200** with
  `latest: null, archive: []` — the page exists, it is empty. DB unavailable
  -> 503, never a fabricated empty shelf.

`intro` comes from a code-side registry (`almanac.SERIES_INTRO`), not a column
— a standing series introduction is not an issue. That registry is empty at
v0, so every series serves `intro: null`.

**Status: migration 154 is DECLARED, NOT APPLIED.**
`migrations/154_publications.sql` is reviewed and applied by the architect; the
ledger row is still `reserved`. Until then the three endpoints serve their
honest empty states and log a warning naming the migration. The daily backfill
(`scripts/backfill_almanac_daily.py` — Kelvin's 14 banked Weather Desk dailies,
2026-07-20..2026-08-06) is dry-run by default and has not been run. Full
findings, EXPLAIN receipts and the apply order are in
`docs/handoff_2026_08_07_almanac_data_layer.md`.

Note: `GET /api/almanac/lmp-shape` (M2) is a literal path inside this prefix
and is **not** a series. It keeps working only because it is registered before
`/api/almanac/{series}`; a test pins that ordering.

---

## Tests

Test-only deps live in `requirements-dev.txt`; no live database is needed
(an in-memory fake pool stands in for Neon):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
