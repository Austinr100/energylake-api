"""The banked responses, for the dashboard lane (d091612).

main.py's three load routes, served over production's rows at
2026-10-05T03:30Z (tests/fixtures/load_outlook_d091611/, made with render.py):

    python docs/receipts/load-net-demand-api-d091611/sample.py

writes sample_outlook_{ca_iso_tac,bpat,ladwp,pge_tac}.json, sample_areas.json
and sample_net_demand_ciso.json beside it. The `cache` block is the memo's
and varies run to run; everything else is the route's answer at NOW.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = pathlib.Path(__file__).resolve().parent

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import load_bank_d091611 as lb  # noqa: E402

BODIES = {
    "sample_outlook_ca_iso_tac.json": "/api/load/outlook?area=CA%20ISO-TAC",
    "sample_outlook_bpat.json": "/api/load/outlook?area=BPAT",
    "sample_outlook_ladwp.json": "/api/load/outlook?area=LADWP",
    "sample_outlook_pge_tac.json": "/api/load/outlook?area=PGE-TAC",
    "sample_areas.json": "/api/load/areas",
    "sample_net_demand_ciso.json": "/api/load/net-demand?area=CISO",
}


def serve(path: str) -> dict:
    b = lb.load()
    main._pool = lb.pool(b)
    main._utcnow = lambda: lb.NOW
    for c in main._LOAD_CACHES:
        c.clear()
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:400])
    return r.json()


if __name__ == "__main__":
    for name, path in BODIES.items():
        body = serve(path)
        body.pop("cache", None)
        (OUT / name).write_text(json.dumps(body, indent=1) + "\n")
        print(name, len(json.dumps(body)))
