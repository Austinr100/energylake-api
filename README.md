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
