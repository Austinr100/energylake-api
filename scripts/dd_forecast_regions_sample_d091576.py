"""d091576: the pnw sample payload, built by the real route from Neon rows.

The three .psv files under docs/receipts/dd-forecast-regions-d091576/ are the
route's three reads for region=pnw, weighting=population, target dates
2026-10-03..2026-10-18, exported from Neon on 2026-10-03 ~17:05Z (values
rounded to 6 dp in the export). This script serves them through
GET /api/weather/dd/forecast/regions with a fake pool and prints the body:

    python scripts/dd_forecast_regions_sample_d091576.py > docs/receipts/dd-forecast-regions-d091576/sample_pnw.json

The forecast export used concat_ws, which drops NULLs, so an incomplete row
carries only date|issued|source|f|spacing|members_present|missing.
"""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from fastapi.testclient import TestClient
import main
H = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "receipts", "dd-forecast-regions-d091576")
def ts(s): return dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc) if s else None
def fl(s): return float(s) if s not in ("", None) else None
def b(s): return {"t": True, "true": True, "f": False, "false": False}.get(s)
fc = []
for line in open(f"{H}/neon_pnw_population_region_forecast.psv").read().split("\n"):
    if not line: continue
    p = line.split("|")
    base = {"region": "pnw", "weighting": "population", "target_date": dt.date.fromisoformat(p[0]),
            "issued_ts": ts(p[1]), "source_product": p[2], "basis_complete": b(p[3]), "rn": 1, "member_stations": 4}
    if p[3] == "t":
        base.update(hdd=fl(p[4]), cdd=fl(p[5]), tavg_f=fl(p[6]), hdd_norm=fl(p[7]), cdd_norm=fl(p[8]),
                    hdd_vs_norm=fl(p[9]), cdd_vs_norm=fl(p[10]), sample_spacing_hours=int(p[11]),
                    members_present=int(p[12]), missing_stations=None)
    else:  # concat_ws dropped the NULL value columns
        base.update(hdd=None, cdd=None, tavg_f=None, hdd_norm=None, cdd_norm=None, hdd_vs_norm=None,
                    cdd_vs_norm=None, sample_spacing_hours=None, members_present=int(p[5]),
                    missing_stations=p[6].split(","))
    fc.append(base)
delta = []
for line in open(f"{H}/neon_pnw_population_model_delta.psv").read().split("\n"):
    if not line: continue
    p = line.split("|")
    delta.append({"region": "pnw", "weighting": "population", "target_date": dt.date.fromisoformat(p[0]),
                  "issued_ts": ts(p[1]), "source_product": p[2], "prior_issued_ts": ts(p[3]),
                  "hdd_delta": fl(p[4]), "cdd_delta": fl(p[5]), "basis_complete": b(p[6]),
                  "prior_basis_complete": b(p[7]), "sample_spacing_hours": int(p[8]) if p[8] else None,
                  "prior_sample_spacing_hours": int(p[9]) if p[9] else None,
                  "spacing_comparable": b(p[10]), "rn": 1})
spread = []
for line in open(f"{H}/neon_pnw_population_model_spread.psv").read().split("\n"):
    if not line: continue
    p = line.split("|")
    spread.append({"region": "pnw", "weighting": "population", "target_date": dt.date.fromisoformat(p[0]),
                   "source_products": p[1].split(","), "sources_present": int(p[2]),
                   "sources_complete": int(p[3]), "hdd_max_minus_min": fl(p[4]),
                   "cdd_max_minus_min": fl(p[5]), "spacing_comparable": b(p[6])})
vectors = [{"region": "pnw", "weighting": w, "station_id": s}
           for w in ("population", "load_share_365d")
           for s in ("USW00024131", "USW00024157", "USW00024229", "USW00024233")]
script = [("FROM degree_day_region_weights", vectors), ("FROM v_degree_days_model_delta", delta),
          ("FROM v_degree_days_model_spread", spread), ("FROM v_degree_days_region_forecast", fc)]
class Cur:
    def __init__(s): s.rows = []
    async def __aenter__(s): return s
    async def __aexit__(s, *e): return False
    async def execute(s, q, p=None):
        s.rows = next((r for n, r in script if n in q), [])
    async def fetchall(s): return list(s.rows)
class Tx:
    async def __aenter__(s): return s
    async def __aexit__(s, *e): return False
class Conn:
    async def __aenter__(s): return s
    async def __aexit__(s, *e): return False
    def cursor(s): return Cur()
    def transaction(s): return Tx()
class Pool:
    def connection(s): return Conn()
main._pool = Pool()
r = TestClient(main.app).get("/api/weather/dd/forecast/regions?region=pnw&from=2026-10-03")
print(r.status_code, file=sys.stderr)
print(json.dumps(r.json(), indent=1))
