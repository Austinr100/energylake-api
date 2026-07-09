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
  -> latest **complete** CAISO pnode-LMP snapshot for one market — prices only,
  no geometry (the map joins client-side on `pnode_id` against the pnode
  geometry). Columnar payload: top-level `market`/`snapshot_vintage`/
  `market_date`/`market_hour`/`market_interval`/`feed_generated_at`/`pnode_count`
  plus six order-aligned arrays sorted by `pnode_id` (`pnode_id`, `lmp`,
  `energy`, `congestion`, `loss`, `ghg`; NULL components pass through as JSON
  null). `market` ∈ `RTD` (default) / `RTPD` / `DAM`. Selects the newest market
  instant clearing a ≥90% pnode-coverage floor; staleness is expected (dispatch-
  only feed) and surfaced via the timestamp fields, not an error. Unknown market
  -> 400; no complete instant (empty/expired table) -> 503 (**note the
  deliberate 503-not-404 convention fork** — valid market + zero rows is a
  data-availability condition, not a missing resource). `Cache-Control: max-age=60`

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

---

## Tests

Test-only deps live in `requirements-dev.txt`; no live database is needed
(an in-memory fake pool stands in for Neon):

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
