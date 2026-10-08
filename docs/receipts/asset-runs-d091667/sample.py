"""d091667: the real responses, served by this branch's routes over the bank
(tests/fixtures/asset_runs_d091667, production read 2026-10-08 20:43Z), and
their bytes as served (compact JSON, as FastAPI writes it; gzip -6).

    sample_runs_wind_66923.json      /asset/runs, SunZia Wind South, n=8
    sample_runs_solar_58388.json     /asset/runs, Solar Star 1, n=8
    sample_plant_wind_66923.json     /asset's `plant` block and attribution for
                                     SunZia Wind South (the site row as banked,
                                     production's catalog); hours and trust
                                     left out: their rows are not in this bank

    python docs/receipts/asset-runs-d091667/sample.py
"""

from __future__ import annotations

import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import test_asset_runs_d091667 as t  # noqa: E402
from test_solar_outlook import FakePool  # noqa: E402


def served(pool, path) -> dict:
    main._asset_runs_cache.clear()
    main._asset_cache.clear()
    main._pool = pool
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, r.text
    return r.json()


def sizes(body: dict) -> tuple[int, int]:
    raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    return len(raw), len(gzip.compress(raw, 6))


def main_() -> None:
    rows = []
    for tech in ("wind", "solar"):
        b = served(t.runs_pool(tech), f"/api/generation/asset/runs?plant_code={t.CODE[tech]}&tech={tech}")
        (OUT / f"sample_runs_{tech}_{t.CODE[tech]}.json").write_text(
            json.dumps(b, indent=1, ensure_ascii=False) + "\n")
        rows.append((f"runs {tech} {t.CODE[tech]}, today's 3 runs", *sizes(b)))
        led, hist = t.eight_runs(tech)
        b8 = served(t.runs_pool(tech, led=led, hist=hist),
                    f"/api/generation/asset/runs?plant_code={t.CODE[tech]}&tech={tech}")
        rows.append((f"runs {tech} {t.CODE[tech]}, 8 runs (5 synthesised from the live ones)", *sizes(b8)))
    s = next(s for s in t.BANKED["wind_sites"] if s["plant_code"] == t.SUNZIA)
    pool = FakePool([("SET LOCAL", []), ("information_schema.columns", t.CATALOG_NOW),
                     ("FROM implied_gen_scores", []), ("FROM implied_gen_site_latest", []),
                     ("FROM implied_gen_wind_sites", [s])])
    a = served(pool, f"/api/generation/asset?plant_code={t.SUNZIA}&tech=wind")
    (OUT / "sample_plant_wind_66923.json").write_text(
        json.dumps({"attribution": a["attribution"], "plant": a["plant"]}, indent=1, ensure_ascii=False) + "\n")
    rows.append(("asset plant block, SunZia Wind South", *sizes({"plant": a["plant"]})))
    for name, raw, gz in rows:
        print(f"| {name} | {raw:,} | {gz:,} |")


if __name__ == "__main__":
    main_()
