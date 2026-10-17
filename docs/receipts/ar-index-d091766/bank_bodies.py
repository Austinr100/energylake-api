"""The bodies d091766 hands back (bodies/), served by the routes over the banked AR bank in the
test cluster (tests/load_bank_d091766.py): the dashboard lane d091767's vectors.

    python3 -I docs/receipts/ar-index-d091766/bank_bodies.py

  index.json                     GET /api/weather/ar/index        on the bank (ar_scores empty)
  landfall.json.gz               GET /api/weather/ar/landfall     on the bank: every p/n withheld
  index_a3_fixture.json          /index    with A3's CONSTRUCTED ar_scores row (or_wa x d1-2 x 250 shown)
  landfall_a3_fixture.json.gz    /landfall with the same row: that cell's p250/n250 served

`cache` is popped (the memo's age). The suite's R1 serves each again and compares it whole.
"""
import gzip
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent / "bodies"
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import main  # noqa: E402
import load_bank_d091766 as lb  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BODIES = [("index.json", "/api/weather/ar/index", False),
          ("landfall.json.gz", "/api/weather/ar/landfall", False),
          ("index_a3_fixture.json", "/api/weather/ar/index", True),
          ("landfall_a3_fixture.json.gz", "/api/weather/ar/landfall", True)]


def main_():
    main._utcnow = lambda: lb.NOW
    out = {}
    with lb.cluster() as conn:
        main._pool = lb.PgPool(conn)
        client = TestClient(main.app)
        for name, path, fixture in BODIES:
            main._ar_index_cache.clear(), main._ar_landfall_cache.clear()
            with conn.transaction():
                if fixture:
                    conn.execute("INSERT INTO ar_scores SELECT * FROM json_populate_record(NULL::ar_scores, %s::json)",
                                 (json.dumps(lb.SCORES_FIXTURE),))
                r = client.get(path)
                assert r.status_code == 200, r.text[:300]
                body = r.json()
                conn.execute("DELETE FROM ar_scores WHERE scorer_hash = 'fixture_d091766'")
            body.pop("cache")
            raw = (json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode()
            if name.endswith(".gz"):
                raw = gzip.compress(raw, 9, mtime=0)
            (OUT / name).write_bytes(raw)
            out[name] = {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                         "scores_fixture": fixture}
    (OUT / "manifest.json").write_text(json.dumps({
        "lane": "d091766", "served_over": "tests/load_bank_d091766.py (Neon rows banked 2026-10-17 12:51-12:52Z)",
        "scores_fixture": lb.SCORES_FIXTURE, "bodies": out}, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main_()
