"""Serve the API branch's REAL app on the banked production reads, for the
dashboard's probe (d091635).

    python docs/receipts/asset-page-api-d091635/serve_bank.py 8765

main.app as merged on this branch, with ONE substitution: `_pool` is the test
suite's FakePool answering each statement with the rows production returned for
it on 2026-10-07 01:17Z (tests/fixtures/asset_page_d091635/bank_2026_10_07.json).
Every route's own code runs: parsing, the memo, the catalog detection, the
shaping. Routes that need no pool (/api/local/forecast: NWS and GFS over the
network) run as they do in production. The lifespan is skipped: there is no
database to open, and nothing is written anywhere.
"""

import pathlib
import sys
from contextlib import asynccontextmanager

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import uvicorn  # noqa: E402

import main  # noqa: E402
import test_asset_page_d091635 as t  # noqa: E402
from test_solar_outlook import FakePool  # noqa: E402

b = t.bank()
SEARCH = {"%solar star%": "solar_star", "%kern%": "kern", "%57514%": "57514", "%ocotillo%": "ocotillo"}


def by_plant(solar_key, wind_key):
    def rows(p):
        if p.get("tech") == "solar_pv" and p.get("plant_code") == 58388:
            return b[solar_key]
        if p.get("tech") == "wind" and p.get("plant_code") == 57514:
            return b[wind_key]
        if "pat" in p:                                   # the search
            k = SEARCH.get(p["pat"].lower())
            return b[f"search_{'solar' if p.get('tech') == 'solar_pv' else 'wind'}_{k}"] if k else []
        return []
    return rows


def wind_site(p):
    if "pat" in p:
        k = SEARCH.get(p["pat"].lower())
        return b[f"search_wind_{k}"] if k else []
    return b["site_wind_57514"] if p.get("plant_code") == 57514 else []


main._pool = FakePool([
    ("SET LOCAL", []),
    ("information_schema.columns", b["columns"]),
    ("FROM implied_gen_scores", lambda p: b["scores_solar_SP15"] if p["tech"] == "solar_pv" else b["scores_wind_SP15"]),
    ("FROM implied_gen_site_latest", by_plant("hours_solar_58388", "hours_wind_57514")),
    ("FROM implied_gen_wind_sites", wind_site),
    ("FROM implied_gen_sites", by_plant("units_solar_58388", None)),
])


@asynccontextmanager
async def _no_lifespan(app):
    yield

main.app.router.lifespan_context = _no_lifespan

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[1] if len(sys.argv) > 1 else 8765), log_level="warning")
