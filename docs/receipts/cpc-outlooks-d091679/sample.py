"""Bank each route's body as the real route serves it over the fixtures
(tests/fixtures/cpc_outlooks_d091679), byte for byte (the cache block
included), and its size raw and gzip -6. Run from the repo root:

    python docs/receipts/cpc-outlooks-d091679/sample.py
"""
import gzip, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
HERE = pathlib.Path(__file__).resolve().parent

from fastapi.testclient import TestClient   # noqa: E402
import main                                  # noqa: E402
import test_cpc_outlooks_d091679 as t        # noqa: E402

client = TestClient(main.app)
rows = []
for name, path, params in t.BODIES:
    for c in t.CACHES:
        getattr(main, c).clear()
    main._pool = t.pool()
    r = client.get(path, params=params)
    assert r.status_code == 200, (name, r.text)
    (HERE / name).write_bytes(r.content)
    gz = len(gzip.compress(r.content, 6, mtime=0))
    q = "&".join(f"{k}={v}" for k, v in params.items())
    rows.append(f"{name}|{path}{'?' + q if q else ''}|{len(r.content)}|{gz}")
    print(f"{name:45s} {len(r.content):>9,d} {gz:>8,d}")
(HERE / "bytes.psv").write_text("\n".join(rows) + "\n")

# Measured, not banked (it would repeat its 1.5 MB fixture): the geometry body.
for c in t.CACHES:
    getattr(main, c).clear()
main._pool = t.pool()
r = client.get("/api/weather/cpc/outlooks", params={"product": "wk34", "geometry": "true"})
print("outlooks?product=wk34&geometry=true", len(r.content), len(gzip.compress(r.content, 6, mtime=0)))
