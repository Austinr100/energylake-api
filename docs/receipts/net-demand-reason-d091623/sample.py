"""The banked responses at this lane's NOW.

main.py's three load routes, served over production's rows at
2026-10-06T12:40Z (tests/fixtures/load_outlook_d091623/, made with render.py
and bank.py):

    python docs/receipts/net-demand-reason-d091623/sample.py

writes the same six bodies d091611's sample.py writes, beside this file. The
`cache` block is the memo's and varies run to run; everything else is the
route's answer at NOW.
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
OUT = pathlib.Path(__file__).resolve().parent

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import load_bank_d091623 as lb  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sample_d091611", OUT.parent / "load-net-demand-api-d091611" / "sample.py")
_old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_old)
BODIES = _old.BODIES


def serve(path: str) -> dict:
    main._pool = lb.pool(lb.load())
    main._utcnow = lambda: lb.NOW
    for c in main._LOAD_CACHES:
        c.clear()
    r = TestClient(main.app).get(path)
    assert r.status_code == 200, (path, r.status_code, r.text[:400])
    return r.json()


if __name__ == "__main__":
    for name, path in BODIES.items():
        body = serve(path)
        body.pop("cache", None)
        (OUT / name).write_text(json.dumps(body, indent=1) + "\n")
        print(name, len(json.dumps(body)))
