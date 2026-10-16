"""d091757: the bodies the local routes serve over the banked rows, for the handback.

    python3 -I docs/receipts/tropics-api-outlook-toa-d091757/write_bodies.py

Serves each path through main.app (TestClient) over the test cluster (d091741's slice,
d091746's display rows and this lane's TWO / TOA rows, banked 2026-10-16 02:49:59Z),
drops the `cache` block (the memo's state at serve time) and writes bodies/<name>.json,
plus bodies/served_from.json: per body, the banked ledger rows each served object and its
original were read from (vintage_id, vintage_key, sha256). Test R1 in
tests/test_tropics_outlook_toa_d091757.py re-serves each body and compares.
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
    "outlook_al_newest.json": f"{P}/outlook?basin=al",
    "outlook_ep_202610140517.json": f"{P}/outlook?basin=ep&stamp=202610140517",
    "outlook_ep_202610140522.json": f"{P}/outlook?basin=ep&stamp=202610140522",
    "outlook_cp_202610152311.json": f"{P}/outlook?basin=cp&stamp=202610152311",
    "products_ep182026_054.json": f"{P}/products?storm_id=ep182026&advisory=054",
}
CACHES = ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache",
          "_tropics_outlook_cache")


def serve(conn, path):
    for c in CACHES:
        getattr(main, c).clear()
    main._pool = lb.PgPool(conn)
    main._utcnow = lambda: lb.NOW
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, r.text
    body = r.json()
    body.pop("cache")
    return body


def objects_of(body):
    if "graphics" in body:
        return body["graphics"]["objects"] + ([body["text"]] if body["text"]["url"] else [])
    return body["objects"] + body["toa"]["objects"] + (body.get("wsp") or {}).get("objects", [])


def served_from(body):
    rows = lb.display_rows() + lb.rows("two") + lb.rows("toa")
    by_key = {DISPLAY_BASE + r["r2_key"]: r for r in rows}
    out = []
    for o in objects_of(body):
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
