"""d091741: the example bodies in the handback, served from the test fixtures.

    python3 -I docs/receipts/tropics-api-index-d091741/write_example_bodies.py

Base layer (the ledger slice + the constructed display rows) for most; the second
layer (constructed_rows.json) for I2 and the published manifest; /ground from this
lane's last z0 recording. The `cache` block is dropped. Not run by the suite.
"""
import asyncio
import json
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import load_bank_d091741 as lb  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "bodies"
P = "/api/weather/tropics"
BASE = {"products_ep202026.json": f"{P}/products?storm_id=ep202026",
        "products_ep182026.json": f"{P}/products?storm_id=ep182026",
        "products_al092026_014A.json": f"{P}/products?storm_id=al092026&advisory=014A",
        "products_al092026.json": f"{P}/products?storm_id=al092026",
        "text_ep202026.json": f"{P}/text?storm_id=ep202026",
        "text_ep182026_057A.json": f"{P}/text?storm_id=ep182026&advisory=057A",
        "members_ep212026.json": f"{P}/members?storm_id=ep212026"}
CONSTRUCTED = {"products_ep202026_032_corrected.json": f"{P}/products?storm_id=ep202026&advisory=032",
               "members_ep202026_2026101112_published.json":
                   f"{P}/members?storm_id=ep202026&cycle=2026101112"}


def write(name, body):
    body.pop("cache", None)
    (OUT / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print(name)


def main_():
    main._utcnow = lambda: lb.NOW
    c = TestClient(main.app)
    for layer, paths in ((dict(), BASE), (dict(constructed=True), CONSTRUCTED)):
        with lb.cluster(**layer) as conn:
            main._pool = lb.PgPool(conn)
            for name, path in paths.items():
                for k in ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache"):
                    getattr(main, k).clear()
                r = c.get(path)
                assert r.status_code == 200, (path, r.text)
                write(name, r.json())
    runs = [json.loads(l) for l in (lb.FIX / "gibs" / "d091741_z0_runs.jsonl").read_text().splitlines()]
    run = [r for r in runs if r["layer"].startswith("GOES-East")][-1]
    ans = {s["slot_utc"]: (s["status"], s["content_type"], s.get("layer_time_actual")) for s in run["slots"]}

    async def fetch(url):
        t = url.split("/default/")[1].split("/")[0]
        if t not in ans:
            raise LookupError(f"unrecorded slot {t}")
        return ans[t]
    main._tropics_gibs_fetch = fetch
    main._utcnow = lambda: datetime.fromisoformat(run["probed_utc"])
    main._tropics_ground_cache.clear()
    write("ground_al.json", c.get(f"{P}/ground?basin=al").json())


if __name__ == "__main__":
    main_()
