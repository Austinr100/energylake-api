"""The sample responses: /api/generation/solar/outlook for the five calibrated
areas, served by main.py over production's rows of the issuance init
2026-10-04 18Z (read-only, through the Neon connector, by the route's own
statements) in tests/fixtures/solar_outlook_d091568/production_2026_10_04_18z.json.
Re-banked by d091608 through the merged code (it was HUBSUM 2026-10-01 12Z,
banked 2026-10-03 by d091568).

    python docs/receipts/solar-outlook-api-d091568/sample.py
writes sample_outlook_{np15,zp26,sp15,hubsum,ciso}.json beside it.
"""
import json, pathlib, sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "solar_outlook_d091568" / "production_2026_10_04_18z.json"
OUT = pathlib.Path(__file__).resolve().parent
QUERY = {"NP15": "area_kind=hub&area=NP15", "ZP26": "area_kind=hub&area=ZP26",
         "SP15": "area_kind=hub&area=SP15", "HUBSUM": "area_kind=hub_sum",
         "CISO": "area_kind=ba&area=CISO"}

TS = {"init_ts", "prev_init_ts", "target_ts", "source_posted_ts", "scored_at", "ts", "fitted_at"}
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
    return {"areas": {a: {"issuance": typed(v["issuance"]),
                          "hours": [typed(r) for r in v["hours"]],
                          "scores": [typed(r) for r in v["scores"] or []],
                          "lines": [typed(r) for r in v["lines"] or []],
                          "actuals": [typed(r) for r in v["actuals"] or []],
                          "fleet": v["fleet"] or []}
                      for a, v in bank["areas"].items()}}


def pool(b, area="HUBSUM"):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_solar_outlook import FakePool
    a = b["areas"][area]
    return FakePool([("SET LOCAL", []), ("AS prev_init_ts", [a["issuance"]]),
                     ("AS prev_registry_mw", a["hours"]), ("FROM implied_gen_scores", a["scores"]),
                     ("FROM implied_gen_calibration", a["lines"]),
                     ("FROM timeseries_values", a["actuals"]),
                     ("FROM implied_gen_sites", a["fleet"])])


if __name__ == "__main__":
    import main
    from fastapi.testclient import TestClient
    b = load()
    c = TestClient(main.app)
    for area, q in QUERY.items():
        main._solar_outlook_cache.clear()
        main._pool = pool(b, area)
        r = c.get(f"/api/generation/solar/outlook?{q}")
        assert r.status_code == 200, r.text
        body = r.json()
        body["cache"] = {k: "<per request>" for k in body["cache"]}
        (OUT / f"sample_outlook_{area.lower()}.json").write_text(json.dumps(body, indent=1))
    print("written")
