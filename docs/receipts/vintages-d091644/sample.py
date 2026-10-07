"""d091644 receipts: the banked bodies and their bytes.

The bodies are served by main.py (the real routes, through FastAPI's
JSONResponse) over production rows read from Neon on 2026-10-07
(tests/fixtures/vintages_d091644, each issuance checked against an md5 Neon
computed). n = 4 and 12 are the newest 4 and 12 issuances of the n = 28 bank,
which is what VINTAGES_SQL returns at those n (V4, PG test).

    python docs/receipts/vintages-d091644/sample.py

writes body_{solar,wind}_ciso_n28.json and body_dd_pnw_population.json
(indented; the dashboard lane's vectors) and prints bytes.md's table.
"""
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import test_vintages_d091644 as t  # noqa: E402


def served(path, pool):
    for c in t.CACHES:
        getattr(main, c).clear()
    main._pool = pool
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, r.text
    return r.content


def sizes(raw: bytes) -> tuple[int, int]:
    return len(raw), len(gzip.compress(raw, compresslevel=6))


def main_():
    rows_out = []
    for tech, route in (("solar_pv", "solar"), ("wind", "wind")):
        rows = t.load_bank(tech)
        inits = sorted({r["init_ts"] for r in rows})
        for n in (4, 12, 28):
            keep = set(inits[-n:])
            pool = t.vintage_pool([r for r in rows if r["init_ts"] in keep], t.bank_lines(tech))
            raw = served(f"/api/generation/{route}/vintages?area_kind=ba&area=CISO&n={n}", pool)
            b = json.loads(raw)
            assert len(b["issuances"]) == n
            hours = sum(len(i["reg"]) for i in b["issuances"])
            rows_out.append((f"{route} ba/CISO", n, hours, *sizes(raw)))
            if n == 28:
                (OUT / f"body_{route}_ciso_n28.json").write_text(json.dumps(b, indent=1) + "\n")
    raw = served("/api/weather/dd/forecast/regions/vintages?region=pnw&weighting=population",
                 t.dd_pool(t.load_dd_bank()))
    b = json.loads(raw)
    n = sum(len(s["issuances"]) for s in b["sources"])
    days = sum(len(i["hdd"]) for s in b["sources"] for i in s["issuances"])
    rows_out.append(("dd pnw/population, every source", f"all ({n})", days, *sizes(raw)))
    (OUT / "body_dd_pnw_population.json").write_text(json.dumps(b, indent=1) + "\n")
    print("| route | n | array entries | raw bytes | gzip -6 bytes |")
    print("|---|---:|---:|---:|---:|")
    for name, n, h, r, g in rows_out:
        print(f"| {name} | {n} | {h:,} | {r:,} | {g:,} |")


if __name__ == "__main__":
    main_()
