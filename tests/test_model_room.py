"""
Tests for the Model Room R2 archive proxy (D-07-22):
  GET /api/model-room/cycles?model=gfs&days=7
  GET /api/model-room/frame/{key}

THE STORE UNDER /cycles CHANGED (2026-09-06). The cycle index is no longer
rebuilt per request by walking R2; it is one indexed read of the render ledger
d2_render_runs (the O(1) re-base — the endpoint was answering 499 at 14-16 s).
So the /cycles tests below drive a FakeDB swapped into `main._pool`, in the same
monkeypatch style as the sibling suites, and the FakeR2 stands beside it purely
as the FALSIFIER: the defect class was "this handler lists R2", and
test_cycles_makes_zero_r2_calls proves it no longer does — zero list calls, with
the R2 config left fully provisioned so nothing but the code path explains it.

The /frame tests are untouched: that route is still an R2 proxy.

R2 is never hit for real here — a FakeR2 client (an in-memory key/object store
that mimics the S3 list_objects_v2 / get_object surface the endpoints use)
stands in, injected by monkeypatching main._get_r2_client. The four R2_* config
globals are set so main._model_room_configured() reports provisioned. The clock
is frozen via main._utcnow so the `days` window is deterministic.

The path-traversal + allowlist rejections are proven two ways: directly against
the validator (main._validate_frame_key), and end-to-end through the frame route
(where a rejected key must NEVER reach the fake bucket).

LIVE RECEIPTS (captured against production 2026-07-23, R2 token provisioned):
  * /cycles?model=gfs&days=7 -> {"cycles":[{gfs,20260723,00}]}
    (pinned byte-for-byte in test_cycles_live_payload_pin_manifest_era — the
    SAME payload, now proven out of the ledger rather than the walk). The
    first machine-made render manifest is live: d2/renders/gfs/20260723/00Z/
    manifest.json (etag b7ab5acd268fde7267cebe6d912372ea, WECC 41/41, NA 33/41).
    The seed-era loose cycles at d2/synoptic/ (20260721 18Z/12Z) carry NO
    manifest under d2/renders/, so the manifest-presence seam drops them off the
    listing — ruled-expected.
  * /frame/weather/tiles/... -> 200 (Atlas tile bank, admitted 2026-09-04);
    /frame/weather/values/... -> 403 (server-side only, never proxied)
  * /frame/d2/renders/gfs/20260723/00Z/manifest.json -> 200, application/json,
    etag "b7ab5acd268fde7267cebe6d912372ea", and
    Cache-Control: public, max-age=31536000, immutable.
These fake-client tests reproduce that live layout so the shape + security
contract regress-guard without a live bucket in CI.
"""

import datetime

import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


UTC = datetime.timezone.utc


def _dt(y, mo, d, h=0, mi=0):
    return datetime.datetime(y, mo, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------------------
# FakeR2: an in-memory S3-ish store over the subset of the API we call.
# ---------------------------------------------------------------------------

class FakeClientError(Exception):
    """Mimics botocore.exceptions.ClientError enough for _r2_error_code/status:
    it carries a .response dict with Error.Code."""
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data


class FakeR2:
    def __init__(self, keys=None, objects=None):
        # keys: iterable of object key strings (the listing universe).
        # objects: {key: (bytes, content_type_or_None)} for get_object.
        self.objects = dict(objects or {})
        self.keys = sorted(set(keys or []) | set(self.objects))
        self.calls = []

    def list_objects_v2(self, **kw):
        self.calls.append(("list", kw))
        prefix = kw.get("Prefix", "")
        delim = kw.get("Delimiter")
        matched = [k for k in self.keys if k.startswith(prefix)]
        if delim:
            common, contents = set(), []
            for k in matched:
                rest = k[len(prefix):]
                i = rest.find(delim)
                if i == -1:
                    contents.append({"Key": k})
                else:
                    common.add(prefix + rest[:i + 1])
            return {
                "CommonPrefixes": [{"Prefix": p} for p in sorted(common)],
                "Contents": contents,
                "IsTruncated": False,
            }
        return {"Contents": [{"Key": k} for k in matched], "IsTruncated": False}

    def get_object(self, Bucket, Key):
        self.calls.append(("get", Key))
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey")
        data, ctype = self.objects[Key]
        out = {"Body": _FakeBody(data), "ETag": '"deadbeef"'}
        if ctype is not None:
            out["ContentType"] = ctype
        return out

    # test helpers
    def listed(self):
        return [c for c in self.calls if c[0] == "list"]

    def got(self):
        return [c[1] for c in self.calls if c[0] == "get"]


def configure(monkeypatch, fake):
    monkeypatch.setattr(main, "R2_ARCHIVE_BUCKET", "archive-bucket")
    monkeypatch.setattr(main, "R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setattr(main, "R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setattr(main, "R2_ENDPOINT", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(main, "_get_r2_client", lambda: fake)


def freeze_now(monkeypatch, when):
    monkeypatch.setattr(main, "_utcnow", lambda: when)


@pytest.fixture
def client():
    return TestClient(main.app)


# The seed-era loose objects still live under the DIFFERENT d2/synoptic/ prefix.
# They are servable through /frame but carry no manifest under d2/renders/ and no
# row in the render ledger, so /cycles never lists them (ruled-expected).
_SEED_ARTIFACTS = ["anomaly_map.png", "synoptic.json", "receipt.md"]


def _seed_cycle_keys(model, date, cycle):
    return [f"d2/synoptic/{model}/{date}/{cycle}Z/{a}" for a in _SEED_ARTIFACTS]


# A render cycle's objects under d2/renders/ (the extended FHR-sequence layout
# the pantry d2.sequence CLI writes). These keys no longer decide the /cycles
# answer — the ledger does — but they are exactly what the OLD walk would have
# listed, which is what makes them the right bait for the zero-R2-calls
# falsifier: a handler that still walks would find them.
def _render_cycle_keys(model, date, cycle, manifest=True, frames=3,
                       region="north_america", param="z500_anom"):
    base = f"d2/renders/{model}/{date}/{cycle}Z"
    keys = [f"{base}/{param}/{region}/f{f * 6:03d}.png" for f in range(frames)]
    if manifest:
        keys.append(f"{base}/manifest.json")
    return keys


# ---------------------------------------------------------------------------
# FakeDB: the render ledger d2_render_runs, in memory, behind the app's pool
# shape. It does not parse SQL — it applies the ONE read the endpoint issues
# (model =, run_date >=, manifest_sha IS NOT NULL, DISTINCT (run_date, cycle),
# newest first) to its rows, and captures the query + params so the structural
# rails can ALSO be asserted at the source-of-truth level (test_cycles_sql_shape,
# test_cycles_cutoff_param_is_inclusive_window).
#
# Rows are faked as psycopg's dict_row yields them from the SELECT: run_date a
# datetime.date, cycle a smallint (int). The zero-padding and the YYYYMMDD token
# are the ENDPOINT's job, which is precisely what these tests pin.
# ---------------------------------------------------------------------------

def banked(model, date_token, cycle, param="z500_anom", region="north_america",
           manifest_sha="e3a32a60effa"):
    """One ledger row. A real cycle banks SEVERAL — one per param x region — all
    carrying the same per-cycle manifest_sha; the endpoint's DISTINCT is what
    collapses that fan-out back to one cycle."""
    return {
        "model": model,
        "run_date": datetime.date(int(date_token[:4]), int(date_token[4:6]),
                                  int(date_token[6:8])),
        "cycle": int(cycle),
        "param": param,
        "region": region,
        "manifest_sha": manifest_sha,
    }


class _FakeDBCursor:
    def __init__(self, ledger, sink, fail):
        self._ledger = ledger
        self._sink = sink
        self._fail = fail
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["query"] = query
        self._sink["params"] = params
        self._sink["calls"] = self._sink.get("calls", 0) + 1
        if self._fail is not None:
            raise self._fail
        hits = {
            (r["run_date"], r["cycle"])
            for r in self._ledger
            if r["model"] == params["model"]
            and r["run_date"] >= params["cutoff"]
            and r["manifest_sha"] is not None      # the availability seam
        }
        self._rows = [{"run_date": d, "cycle": c}
                      for d, c in sorted(hits, reverse=True)]

    async def fetchall(self):
        return list(self._rows)


class _FakeDBConn:
    def __init__(self, ledger, sink, fail):
        self._args = (ledger, sink, fail)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeDBCursor(*self._args)


class FakeDB:
    def __init__(self, rows=None, fail=None):
        self.ledger = list(rows or [])
        self.sink = {}
        self.fail = fail

    def connection(self):
        return _FakeDBConn(self.ledger, self.sink, self.fail)


def use_ledger(monkeypatch, rows=None, fail=None):
    db = FakeDB(rows=rows, fail=fail)
    monkeypatch.setattr(main, "_pool", db)
    return db


# ═══════════════════════════════════════════════════════════════════════════
# cycles — served from the ledger (the O(1) re-base, 2026-09-06)
# ═══════════════════════════════════════════════════════════════════════════

def test_cycles_lists_published_cycles_newest_first(client, monkeypatch):
    """The shape receipt: three banked cycles, newest first, each row
    {model, date, cycle}. The (param, region) fan-out is seeded deliberately —
    20260722/00 banks four rows — so the DISTINCT collapse is proven, not
    assumed."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [
        banked("gfs", "20260722", 0, param="z500_anom", region="north_america"),
        banked("gfs", "20260722", 0, param="z500_anom", region="wecc"),
        banked("gfs", "20260722", 0, param="mslp", region="north_america"),
        banked("gfs", "20260722", 0, param="mslp", region="wecc"),
        banked("gfs", "20260721", 18),
        banked("gfs", "20260721", 12),
        banked("ifs", "20260722", 12),   # another model — never in gfs's answer
    ])
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    # Platform envelope: {"cycles": [...]} (no bare array).
    assert d == {"cycles": [
        {"model": "gfs", "date": "20260722", "cycle": "00"},
        {"model": "gfs", "date": "20260721", "cycle": "18"},
        {"model": "gfs", "date": "20260721", "cycle": "12"},
    ]}
    print("ok cycles_lists_published_cycles_newest_first")


def test_cycles_live_payload_pin_manifest_era(client, monkeypatch):
    """PINNED LIVE PAYLOAD (D-07-23, verified against production web-production-
    497cb 2026-07-23), now served from the LEDGER: /cycles?model=gfs&days=7
    returns exactly the one render cycle gfs/20260723/00.

    THE POINT OF THIS TEST AFTER THE RE-BASE: the payload did not move when the
    store did. The seed-era loose cycles gfs/20260721/18Z and /12Z live under
    d2/synoptic/ with no manifest and no ledger row, so they are absent for the
    same reason as before — asserted here by seeding them into the R2 store
    alongside and proving they do NOT appear.
    """
    # Freeze to the live server date so days=7 spans 20260723 (and 20260721).
    freeze_now(monkeypatch, _dt(2026, 7, 23, 15, 0))
    fake = FakeR2(keys=(_seed_cycle_keys("gfs", "20260721", "18")
                        + _seed_cycle_keys("gfs", "20260721", "12")))
    configure(monkeypatch, fake)
    use_ledger(monkeypatch, [
        banked("gfs", "20260723", 0,
               manifest_sha="b7ab5acd268fde7267cebe6d912372ea"),
    ])
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    # The exact live payload, verbatim: the manifest-era truth, seeds gone.
    assert r.json() == {"cycles": [
        {"model": "gfs", "date": "20260723", "cycle": "00"},
    ]}
    print("ok cycles_live_payload_pin_manifest_era")


def test_cycles_makes_zero_r2_calls(client, monkeypatch):
    """THE FALSIFIER, and the whole reason this lane exists. The defect class was
    "the cycle index is rebuilt per request by walking list_objects_v2" — cost
    scaling with the archive, measured at 14,061-15,938 ms and four parallel
    499s on 2026-09-06.

    So: stand a FULLY PROVISIONED FakeR2 in the path, stocked with exactly the
    render keys the old walk would have found, and assert the handler makes ZERO
    R2 calls. Not "fewer" — zero. A regression that reintroduces any listing,
    even a single MaxKeys=1 probe per cycle, fails here rather than in a Railway
    log a week later.
    """
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    fake = FakeR2(keys=(_render_cycle_keys("gfs", "20260722", "00")
                        + _render_cycle_keys("gfs", "20260721", "18")))
    configure(monkeypatch, fake)          # R2 provisioned and reachable...
    use_ledger(monkeypatch, [banked("gfs", "20260722", 0)])
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 200
    assert r.json() == {"cycles": [
        {"model": "gfs", "date": "20260722", "cycle": "00"}]}
    assert fake.calls == []               # ...and never touched. Zero. Any verb.
    print("ok cycles_makes_zero_r2_calls")


def test_cycles_served_with_r2_entirely_unprovisioned(client, monkeypatch):
    """THE DECOUPLING RECEIPT. Before the re-base this was a 503: no R2 token, no
    cycle index. The route no longer reads R2 at all, so an R2 outage — or a
    never-provisioned token — can no longer dark the Viewer's cycle index. The
    R2 config gate went with the R2 dependency; keeping it would have been a
    guard that cannot fire, wired to a store this route does not use."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    monkeypatch.setattr(main, "R2_ARCHIVE_BUCKET", "")
    monkeypatch.setattr(main, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(main, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(main, "R2_ENDPOINT", "")
    assert not main._model_room_configured()
    use_ledger(monkeypatch, [banked("gfs", "20260722", 6)])
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 200
    assert r.json() == {"cycles": [
        {"model": "gfs", "date": "20260722", "cycle": "06"}]}
    print("ok cycles_served_with_r2_entirely_unprovisioned")


def test_cycles_excludes_unmanifested_row_seam(client, monkeypatch):
    """THE AVAILABILITY SEAM, carried across the store change. D-07-23-01 made
    manifest-presence the publication signal; in the ledger's vocabulary that is
    manifest_sha. A row banked without one is NOT published and does not list.

    The live column is NOT NULL and the 2026-09-06 census measured 0 nulls and 0
    empty strings across all 1,079 rows — so this guard is a no-op against
    today's table, and it is tested anyway: the seam is what the SQL asserts, and
    a schema that later admits a null must not silently widen the answer.
    """
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [
        banked("gfs", "20260721", 18),
        banked("gfs", "20260721", 6, manifest_sha=None),   # banked, unmanifested
    ])
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    assert d == {"cycles": [{"model": "gfs", "date": "20260721", "cycle": "18"}]}
    print("ok cycles_excludes_unmanifested_row_seam")


def test_cycles_respects_days_window(client, monkeypatch):
    """`days` is an inclusive UTC-date window ending today. With today=07-22 and
    days=7 the cutoff is 07-16, so a 07-10 cycle is out of window."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [
        banked("gfs", "20260721", 18),
        banked("gfs", "20260710", 0),     # older than the 7-day window
    ])
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    assert d == {"cycles": [{"model": "gfs", "date": "20260721", "cycle": "18"}]}
    # Widen the window and the older cycle reappears.
    d2 = client.get("/api/model-room/cycles?model=gfs&days=20").json()
    dates = {row["date"] for row in d2["cycles"]}
    assert dates == {"20260721", "20260710"}
    print("ok cycles_respects_days_window")


def test_cycles_cutoff_param_is_inclusive_window(client, monkeypatch):
    """THE OFF-BY-ONE GUARD, at the source of truth. The window is `days` dates
    INCLUDING today, so the cutoff is today-(days-1) — not today-days, which the
    obvious `current_date - days` in SQL would have made a 15-date window at
    days=14 and a silent parity failure at the boundary. The cutoff is also a
    real date OBJECT, bound as a parameter: the window rides the app's frozen
    _utcnow clock, never the database session's."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    db = use_ledger(monkeypatch, [])
    client.get("/api/model-room/cycles?model=gfs&days=14")
    assert db.sink["params"]["cutoff"] == datetime.date(2026, 7, 9)   # 22 - 13
    assert db.sink["params"]["model"] == "gfs"
    # days=1 is today alone.
    client.get("/api/model-room/cycles?model=gfs&days=1")
    assert db.sink["params"]["cutoff"] == datetime.date(2026, 7, 22)
    print("ok cycles_cutoff_param_is_inclusive_window")


def test_cycles_sql_shape(client, monkeypatch):
    """The structural rails, asserted against the SQL the handler actually ran:
    ONE query, over the ledger, DISTINCT to cycle granularity, carrying the
    availability seam, ordered newest-first in the database rather than in
    Python."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    db = use_ledger(monkeypatch, [banked("gfs", "20260722", 0)])
    client.get("/api/model-room/cycles?model=gfs&days=7")
    q = " ".join(db.sink["query"].split())
    assert "FROM d2_render_runs" in q
    assert "SELECT DISTINCT run_date, cycle" in q
    assert "manifest_sha IS NOT NULL" in q
    assert "ORDER BY run_date DESC, cycle DESC" in q
    assert db.sink["calls"] == 1          # O(1): one read, whatever the bank size
    print("ok cycles_sql_shape")


def test_cycles_cache_headers_on_200(client, monkeypatch):
    """Repeat loads should never pay origin at all — secondary to the O(1)
    origin, but the shared cache is what absorbs the dashboard's parallel fan-out
    until it learns to single-flight."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [banked("gfs", "20260722", 0)])
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 200
    assert r.headers["cache-control"] == (
        "public, s-maxage=60, stale-while-revalidate=300")
    print("ok cycles_cache_headers_on_200")


def test_cycles_no_cache_header_on_error(client, monkeypatch):
    """A failure is never cached — the directive is set on the 200 path only, so
    an outage cannot be pinned into a CDN for five minutes."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [], fail=psycopg.OperationalError("connection closed"))
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 503
    assert "cache-control" not in {k.lower() for k in r.headers}
    print("ok cycles_no_cache_header_on_error")


def test_cycles_invalid_model_rejected_before_db(client, monkeypatch):
    """A hostile model slug is a 400 and never reaches the database (no query),
    exactly as it never reached R2 before."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    db = use_ledger(monkeypatch, [])
    r = client.get("/api/model-room/cycles?model=../etc")
    assert r.status_code == 400
    assert db.sink == {}
    print("ok cycles_invalid_model_rejected_before_db")


def test_cycles_503_when_db_unavailable(client, monkeypatch):
    """DB unavailable -> 503, the platform's standard posture. NO silent fallback
    to the R2 walk: a fallback that restores the 15-second path on a Neon blip is
    a guard that cannot fire. The R2 client is provisioned and stocked here
    precisely so the absence of that fallback is visible — the handler could have
    walked, and does not."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    fake = FakeR2(keys=_render_cycle_keys("gfs", "20260722", "00"))
    configure(monkeypatch, fake)
    use_ledger(monkeypatch, [],
               fail=psycopg.OperationalError("SSL connection has been closed"))
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]
    assert fake.calls == []
    print("ok cycles_503_when_db_unavailable")


def test_cycles_empty_ledger_is_empty_envelope(client, monkeypatch):
    """Nothing banked in the window is an empty list, not an error — the Viewer
    renders honest absence, and the STILL LOADING banner never fires."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    use_ledger(monkeypatch, [])
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 200
    assert r.json() == {"cycles": []}
    print("ok cycles_empty_ledger_is_empty_envelope")


# ═══════════════════════════════════════════════════════════════════════════
# frame
# ═══════════════════════════════════════════════════════════════════════════

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"fake-frame-bytes"


def test_frame_streams_png_with_immutable_cache(client, monkeypatch):
    key = "d2/synoptic/gfs/20260721/18Z/500mb.png"
    fake = FakeR2(objects={key: (PNG_MAGIC, "image/png")})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{key}")
    assert r.status_code == 200
    assert r.content == PNG_MAGIC
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert r.headers.get("etag") == '"deadbeef"'
    print("ok frame_streams_png_with_immutable_cache")


def test_frame_serves_json_object(client, monkeypatch):
    key = "d2/synoptic/gfs/20260721/18Z/meta.json"
    fake = FakeR2(objects={key: (b'{"cycle":"18Z"}', "application/json")})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{key}")
    assert r.status_code == 200
    assert r.json() == {"cycle": "18Z"}
    assert r.headers["content-type"] == "application/json"
    print("ok frame_serves_json_object")


def test_frame_content_type_inferred_when_object_has_none(client, monkeypatch):
    """When R2 stored no ContentType, fall back to the extension map."""
    key = "d2/synoptic/gfs/20260721/18Z/500mb.png"
    fake = FakeR2(objects={key: (PNG_MAGIC, None)})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{key}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    print("ok frame_content_type_inferred_when_object_has_none")


def test_frame_path_traversal_rejected_at_validator(monkeypatch):
    """THE RECEIPT (unit): traversal / dot / absolute / non-d2 keys are refused
    by the validator, deterministically (no URL-normalization in the way)."""
    for bad in ("d2/synoptic/../../etc/passwd", "d2/..", "../secret",
                "/d2/x", "d2//x", "d2/synoptic/./x", "d2/x\\y", "", "d2"):
        with pytest.raises(HTTPException) as ei:
            main._validate_frame_key(bad)
        assert ei.value.status_code in (400, 403)
    # A clean d2/ key survives untouched.
    ok = "d2/synoptic/gfs/20260721/18Z/500mb.png"
    assert main._validate_frame_key(ok) == ok
    print("ok frame_path_traversal_rejected_at_validator")


def test_frame_non_d2_prefix_rejected_before_r2(client, monkeypatch):
    """THE RECEIPT (end-to-end): a key outside d2/ is 403 and the bucket is
    NEVER touched (get_object not called)."""
    fake = FakeR2(objects={"secret/passwd": (b"x", "text/plain")})
    configure(monkeypatch, fake)
    r = client.get("/api/model-room/frame/secret/passwd")
    assert r.status_code == 403
    assert fake.got() == []          # validation blocked it before any R2 call
    print("ok frame_non_d2_prefix_rejected_before_r2")


def test_frame_404_on_missing_object(client, monkeypatch):
    key = "d2/synoptic/gfs/20260721/18Z/absent.png"
    fake = FakeR2(objects={})        # nothing stored -> NoSuchKey
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{key}")
    assert r.status_code == 404
    print("ok frame_404_on_missing_object")


def test_frame_503_when_unprovisioned(client, monkeypatch):
    """Valid key, but no token yet -> 503 (validated first, then config)."""
    monkeypatch.setattr(main, "R2_ARCHIVE_BUCKET", "")
    monkeypatch.setattr(main, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(main, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(main, "R2_ENDPOINT", "")
    r = client.get("/api/model-room/frame/d2/synoptic/gfs/20260721/18Z/500mb.png")
    assert r.status_code == 503
    print("ok frame_503_when_unprovisioned")


# ═══════════════════════════════════════════════════════════════════════════
# frame — weather/tiles/ (Atlas tile bank, admitted 2026-09-04)
# ═══════════════════════════════════════════════════════════════════════════
#
# THE DEFECT THESE CLOSE: the frame route answered 403 in 2 ms for live Atlas
# tile keys (Railway log, 2026-09-04) — the validator refusing the key before
# R2, because the allowlist predated the tile bank. The bank lives in the SAME
# archive bucket as d2/, so this is an allowlist admission, not a bucket switch.

TILE_MANIFEST_KEY = "weather/tiles/gfs/na3/20260903/06Z/mslp/f006/manifest.json"
TILE_PNG_KEY = "weather/tiles/gfs/na3/20260903/00Z/z500_anom/f072/3/2/1.png"


def test_frame_serves_tile_manifest_json(client, monkeypatch):
    """THE RECEIPT: the exact key from the 2026-09-04 403 log line now returns
    200, application/json, immutable cache."""
    fake = FakeR2(objects={
        TILE_MANIFEST_KEY: (b'{"param":"mslp","fhr":6}', "application/json")})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{TILE_MANIFEST_KEY}")
    assert r.status_code == 200
    assert r.json() == {"param": "mslp", "fhr": 6}
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert fake.got() == [TILE_MANIFEST_KEY]
    print("ok frame_serves_tile_manifest_json")


def test_frame_serves_tile_png(client, monkeypatch):
    """A tile PNG under weather/tiles/ streams as image/png — including when R2
    stored no ContentType, proving the suffix map (not a d2/-shaped branch) is
    what decides the type."""
    fake = FakeR2(objects={TILE_PNG_KEY: (PNG_MAGIC, None)})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{TILE_PNG_KEY}")
    assert r.status_code == 200
    assert r.content == PNG_MAGIC
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    print("ok frame_serves_tile_png")


def test_frame_weather_values_prefix_rejected_before_r2(client, monkeypatch):
    """LEAST SURFACE: weather/values/ sidecars are read SERVER-SIDE by the point
    API and are NOT admitted to the public proxy — 403, bucket never touched."""
    key = "weather/values/gfs/na3/20260903/06Z/mslp/f006.f32"
    fake = FakeR2(objects={key: (b"\x00\x00\x00\x00", "application/octet-stream")})
    configure(monkeypatch, fake)
    r = client.get(f"/api/model-room/frame/{key}")
    assert r.status_code == 403
    assert fake.got() == []          # validation blocked it before any R2 call
    print("ok frame_weather_values_prefix_rejected_before_r2")


def test_frame_tiles_traversal_rejected_at_validator(monkeypatch):
    """The pre-existing traversal guards apply to the NEW prefix unchanged —
    one code path, not a second. Escaping weather/tiles/ into d2/ still fails,
    even though d2/ is itself allowlisted."""
    for bad in ("weather/tiles/../d2/renders/x.png", "weather/tiles//x.png",
                "weather/tiles/./x.png", "/weather/tiles/x.png",
                "weather/tiles/", "weather/tiles", "weather/tiles/x\\y",
                "weather", "weather/other/x.png"):
        with pytest.raises(HTTPException) as ei:
            main._validate_frame_key(bad)
        assert ei.value.status_code in (400, 403), bad
    # Clean tile keys survive untouched.
    for ok in (TILE_MANIFEST_KEY, TILE_PNG_KEY):
        assert main._validate_frame_key(ok) == ok
    print("ok frame_tiles_traversal_rejected_at_validator")


if __name__ == "__main__":
    print("Run via pytest for full fixtures.")
