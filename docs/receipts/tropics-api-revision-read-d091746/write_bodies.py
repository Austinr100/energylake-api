"""d091746: the bodies the local routes serve over the banked rows, for the handback.

    python3 -I docs/receipts/tropics-api-revision-read-d091746/write_bodies.py

Serves each path through main.app (TestClient) over the test cluster (the
13:50Z slice, Simon 033's rows and the display rows banked 22:00:14Z), drops the
`cache` block (it is the memo's state at serve time) and writes bodies/<name>.json,
plus bodies/served_from.json: per body, the banked ledger rows each served object
and its original were read from (vintage_id, vintage_key, sha256). test R1 in
tests/test_tropics_revision_read_d091746.py re-serves each body and compares.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import load_bank_d091741 as lb  # noqa: E402
from tropics_index import DISPLAY_BASE  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent / "bodies"
P = "/api/weather/tropics"
BODIES = {
    "products_ep182026_054.json": f"{P}/products?storm_id=ep182026&advisory=054",
    "text_al092026_016.json": f"{P}/text?storm_id=al092026&advisory=016",
    "text_ep202026_033.json": f"{P}/text?storm_id=ep202026&advisory=033",
    "products_ep202026_033.json": f"{P}/products?storm_id=ep202026&advisory=033",
}


def serve(conn, path):
    for c in ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache"):
        getattr(main, c).clear()
    main._pool = lb.PgPool(conn)
    main._utcnow = lambda: lb.NOW
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    body.pop("cache")
    return body


def served_from(body):
    by_key = {DISPLAY_BASE + r["r2_key"]: r for r in lb.display_rows()}
    objs = body.get("objects", []) + (body.get("wsp") or {}).get("objects", []) \
        + [o for o in body.get("products", {}).values() if o.get("url")]
    out = []
    for o in objs:
        row = by_key[o["url"]]
        orig = None
        if o["original"]:
            r0 = by_key[o["original"]["url"]]
            orig = {"vintage_id": r0["vintage_id"], "vintage_key": r0["vintage_key"], "sha256": r0["sha256"]}
        out.append({"product": o["product"], "vintage_id": row["vintage_id"], "vintage_key": row["vintage_key"],
                    "sha256": row["sha256"], "original": orig})
    return out


def main_():
    OUT.mkdir(exist_ok=True)
    rows = {}
    with lb.cluster(padded=True) as conn:
        for name, path in BODIES.items():
            body = serve(conn, path)
            (OUT / name).write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
            rows[name] = {"path": path, "objects": served_from(body)}
    (OUT / "served_from.json").write_text(json.dumps(rows, indent=1) + "\n")


if __name__ == "__main__":
    main_()
