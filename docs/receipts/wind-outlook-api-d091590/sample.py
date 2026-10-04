"""The banked responses: /api/generation/wind/outlook and /sites, served by
main.py over production's rows of the live cycle init 2026-10-04 12Z (read-only,
through the Neon connector, by the route's own statements) in
tests/fixtures/wind_outlook_d091590/production_2026_10_04_12z.json.

    python docs/receipts/wind-outlook-api-d091590/sample.py
writes sample_outlook_{hubsum,ciso,zp26}.json and sample_sites_*.json beside it.
"""
import json, pathlib, sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "wind_outlook_d091590" / "production_2026_10_04_12z.json"
OUT = pathlib.Path(__file__).resolve().parent

TS = {"init_ts", "prev_init_ts", "target_ts", "source_posted_ts", "scored_at", "ts",
      "fitted_at", "init_ts_max"}
DATES = {"window_start", "window_end", "fit_start", "fit_end"}


def typed(row):
    out = {}
    for k, v in row.items():
        if v is not None and k in TS:
            v = datetime.fromisoformat(v)
        elif v is not None and k in DATES:
            v = date.fromisoformat(v[:10])
        out[k] = v
    return out


def load():
    bank = json.loads(FIX.read_text())
    areas = {a: {"issuance": typed(v["issuance"]),
                 "hours": [typed(r) for r in v["hours"]],
                 "scores": [typed(r) for r in v["scores"]],
                 "lines": [typed(r) for r in v["lines"]],
                 "actuals": [typed(r) for r in v["actuals"]]}
             for a, v in bank["areas"].items()}
    facts = {s["plant_code"]: s for s in bank["sites"]}
    latest = {k: [typed({**facts[r["plant_code"]], **r}) for r in v]
              for k, v in bank["site_latest"].items()}
    return {"areas": areas, "sites": bank["sites"], "site_latest": latest}


def outlook_pool(b, area):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_solar_outlook import FakePool
    a = b["areas"][area]
    return FakePool([("SET LOCAL", []), ("AS prev_init_ts", [a["issuance"]]),
                     ("AS prev_calibration_id", a["hours"]),
                     ("FROM implied_gen_scores", a["scores"]),
                     ("FROM implied_gen_calibration", a["lines"]),
                     ("FROM timeseries_values", a["actuals"]),
                     ("FROM implied_gen_wind_sites", b["sites"])])


def sites_pool(b, key):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_solar_outlook import FakePool
    return FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites", b["site_latest"][key])])


def strip_cache(body):
    body["cache"] = {k: "<per request>" for k in body["cache"]}
    return body


if __name__ == "__main__":
    import main
    from fastapi.testclient import TestClient
    b = load()
    c = TestClient(main.app)
    for area, q in (("HUBSUM", "area_kind=hub_sum"), ("CISO", "area_kind=ba&area=CISO"),
                    ("ZP26", "area_kind=hub&area=ZP26")):
        main._wind_outlook_cache.clear()
        main._pool = outlook_pool(b, area)
        r = c.get(f"/api/generation/wind/outlook?{q}")
        assert r.status_code == 200, r.text
        (OUT / f"sample_outlook_{area.lower()}.json").write_text(json.dumps(strip_cache(r.json()), indent=1))
    for key, q in (("target_2026_10_04T20Z", "target=2026-10-04T20:00:00Z"),
                   ("day_2026_10_05", "day=2026-10-05")):
        main._wind_sites_cache.clear()
        main._pool = sites_pool(b, key)
        r = c.get(f"/api/generation/wind/sites?{q}")
        assert r.status_code == 200, r.text
        (OUT / f"sample_sites_{key}.json").write_text(json.dumps(strip_cache(r.json()), indent=1))
    print("written")
