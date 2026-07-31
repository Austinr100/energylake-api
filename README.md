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

---

## Tests

Test-only deps live in `requirements-dev.txt`; no live database is needed
(an in-memory fake pool stands in for Neon):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
