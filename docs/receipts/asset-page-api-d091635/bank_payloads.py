"""Write the route's answers for the banked production reads (d091635).

    python docs/receipts/asset-page-api-d091635/bank_payloads.py

The reads are tests/fixtures/asset_page_d091635/bank_2026_10_07.json: every
statement the two routes run, rendered with its parameters and run as ONE
statement through the Neon connector (bank.sql beside it), 2026-10-07 01:17Z.
The payloads written here are what the routes answer for those reads; the
dashboard banks them as its test vector (D-09-05-T), and
tests/test_asset_page_d091635.py V holds the route to them.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import test_asset_page_d091635 as t  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent


def write(name, pool, path):
    main._asset_cache.clear()
    main._assets_search_cache.clear()
    main._pool = pool
    body = TestClient(main.app).get(path).json()
    body.pop("cache")
    (OUT / f"{name}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print(name, len(json.dumps(body)))


b = t.bank()
write("asset_solar_58388", t.solar_pool(b), t.SOLAR)
write("asset_wind_57514", t.wind_pool(b), t.WIND)
write("assets_solar_star", t.search_pool(b["search_solar_solar_star"], b["search_wind_solar_star"]),
      "/api/generation/assets?q=solar%20star")
write("assets_ocotillo", t.search_pool(b["search_solar_ocotillo"], b["search_wind_ocotillo"]),
      "/api/generation/assets?q=ocotillo")
