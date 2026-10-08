"""d091666 receipts: the banked bodies and their bytes.

Each body is served by main.py (the real routes, through FastAPI) over the
production rows read from Neon on 2026-10-08 (tests/fixtures/dd_board_d091666,
each file checked against an md5 Neon computed), with today pinned to
2026-10-08 (Pacific).

    python docs/receipts/dd-board-d091666/sample.py

writes body_*.json (indented; the dashboard lane's vectors) and prints the
bytes table.
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
import test_dd_board_d091666 as t  # noqa: E402


def served(path, params):
    for c in t.CACHES:
        getattr(main, c).clear()
    main._pool = t.pool()
    main._dd_today_pt = lambda: t.TODAY
    r = TestClient(main.app).get(path, params=params)
    assert r.status_code == 200, r.text
    return r.content


def main_():
    print("| body | route | raw bytes | gzip -6 bytes |")
    print("|---|---|---:|---:|")
    for name, path, params in t.BODIES:
        raw = served(path, params)
        (OUT / name).write_text(json.dumps(json.loads(raw), indent=1) + "\n")
        q = "&".join(f"{k}={v}" for k, v in params.items())
        print(f"| {name} | {path}{'?' + q if q else ''} | {len(raw):,} | "
              f"{len(gzip.compress(raw, compresslevel=6)):,} |")


if __name__ == "__main__":
    main_()
