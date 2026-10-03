"""The sample response: /api/generation/solar/outlook?area_kind=hub_sum, served
by main.py over production's rows as banked 2026-10-03 17:20 UTC (read-only,
through the Neon connector, by the route's own statements) in
tests/fixtures/solar_outlook_d091568/production_hubsum_2026_10_03.json.

    python docs/receipts/solar-outlook-api-d091568/sample.py > .../sample_outlook_hubsum.json
"""
import json, pathlib, sys
from datetime import date, datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures" / "solar_outlook_d091568" / "production_hubsum_2026_10_03.json"

TS = {"init_ts", "prev_init_ts", "target_ts", "source_posted_ts", "scored_at", "ts", "fitted_at"}
DATES = {"window_start", "window_end", "fit_start", "fit_end"}


def typed(row):
    out = {}
    for k, v in row.items():
        if v is not None and k in TS:
            v = datetime.fromisoformat(v)
        elif v is not None and k in DATES:
            v = date.fromisoformat(v)
        out[k] = v
    return out


def load():
    bank = json.loads(FIX.read_text())
    return {
        "issuance": typed(bank["issuance"]),
        "hours": [typed(r) for r in bank["hours"]],
        "scores": [typed(r) for r in bank["scores"]],
        "actuals": [typed(r) for r in bank["actuals"]],
        "fleet": bank["fleet"],
        "lines_existing": bank["lines_existing_hubsum"],
    }


def pool(b):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_solar_outlook import FakePool
    return FakePool([("SET LOCAL", []), ("AS prev_init_ts", [b["issuance"]]),
                     ("AS prev_registry_mw", b["hours"]), ("FROM implied_gen_scores", b["scores"]),
                     ("FROM implied_gen_calibration", []), ("FROM timeseries_values", b["actuals"]),
                     ("FROM implied_gen_sites", b["fleet"])])


if __name__ == "__main__":
    import main
    from fastapi.testclient import TestClient
    main._pool = pool(load())
    r = TestClient(main.app).get("/api/generation/solar/outlook?area_kind=hub_sum")
    assert r.status_code == 200, r.text
    body = r.json()
    body["cache"] = {k: "<per request>" for k in body["cache"]}
    print(json.dumps(body, indent=1))
