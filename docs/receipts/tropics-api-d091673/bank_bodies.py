"""Bank each Tropics route's body as served from the bank, for the dashboard lane (d091673).

    python3 docs/receipts/tropics-api-d091673/bank_bodies.py

Starts a throwaway Postgres with the bank (tests/load_bank_d091673.py), serves
every vector path through main.app at load_bank_d091673.NOW, and writes:

  tests/fixtures/tropics_d091673/bodies/<name>.json   the body without its
      `cache` block (its age differs per request), compact, key order as served
  docs/receipts/tropics-api-d091673/sizes.json        bytes raw and gzip -6 of
      the full response body, per storm, /tracks at n = 1..8 and the other routes

test_V_* holds the routes to the bodies byte for byte; test_T10 to sizes.json.
"""

import gzip
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import load_bank_d091673 as lb  # noqa: E402
from test_tropics_d091673 import CACHES, STORMS, VECTORS, P  # noqa: E402

OUT = lb.FIX / "bodies"


def clear():
    for c in CACHES:
        getattr(main, c).clear()


def main_():
    OUT.mkdir(exist_ok=True)
    main._utcnow = lambda: lb.NOW
    with lb.cluster() as conn:
        main._pool = lb.PgPool(conn)
        c = TestClient(main.app)
        for fname, path in VECTORS:
            clear()
            r = c.get(path)
            assert r.status_code == 200, (path, r.text)
            body = {k: v for k, v in r.json().items() if k != "cache"}
            (OUT / fname).write_text(json.dumps(body, separators=(",", ":"), ensure_ascii=False))
            print(fname, len(r.content))
        sizes = {"what": "bytes of the full response body (compact JSON, with its cache block) "
                         "and of gzip -6 of it, served from the bank at " + lb.NOW.isoformat(),
                 "stop_z_gzip_bytes": 500_000}
        for sid in STORMS:
            s = {}
            for n in range(1, 9):
                clear()
                t0 = time.perf_counter()
                raw = c.get(f"{P}/tracks?storm_id={sid}&n={n}").content
                ms = (time.perf_counter() - t0) * 1000
                s[f"n{n}"] = {"raw": len(raw), "gzip6": len(gzip.compress(raw, 6)),
                              "local_build_ms": round(ms, 1)}
            for route in ("storm", "odds"):
                clear()
                raw = c.get(f"{P}/{route}?storm_id={sid}").content
                s[route] = {"raw": len(raw), "gzip6": len(gzip.compress(raw, 6))}
            sizes[sid] = s
        clear()
        raw = c.get(f"{P}/storms").content
        sizes["storms"] = {"raw": len(raw), "gzip6": len(gzip.compress(raw, 6))}
    (ROOT / "docs/receipts/tropics-api-d091673/sizes.json").write_text(json.dumps(sizes, indent=1) + "\n")
    print(json.dumps(sizes["ep182026"], indent=1))


if __name__ == "__main__":
    main_()
