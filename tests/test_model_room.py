"""
Tests for the Model Room R2 archive proxy (D-07-22):
  GET /api/model-room/cycles?model=gfs&days=7
  GET /api/model-room/frame/{key}

R2 is never hit for real here — a FakeR2 client (an in-memory key/object store
that mimics the S3 list_objects_v2 / get_object surface the endpoints use)
stands in, injected by monkeypatching main._get_r2_client. The four R2_* config
globals are set so main._model_room_configured() reports provisioned. The clock
is frozen via main._utcnow so the `days` window is deterministic.

The path-traversal + allowlist rejections are proven two ways: directly against
the validator (main._validate_frame_key), and end-to-end through the frame route
(where a rejected key must NEVER reach the fake bucket).

NOTE (STOP-GATE): the LIVE receipts — a real listing showing the published
gfs/20260721/18Z cycle, and the frame route streaming the real PNG bytes — need
the captain-provisioned read-only R2 token and are run post-provisioning. These
fake-client tests pin the SHAPE and the security contract that don't need it.
"""

import datetime

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


# Three seed artifacts under a cycle = "published" (v0 availability).
def _cycle_keys(model, date, cycle, n=3):
    names = ["500mb.png", "mslp.png", "precip.png", "meta.json"][:n]
    return [f"d2/synoptic/{model}/{date}/{cycle}Z/{a}" for a in names]


# ═══════════════════════════════════════════════════════════════════════════
# cycles
# ═══════════════════════════════════════════════════════════════════════════

def test_cycles_lists_published_cycles_newest_first(client, monkeypatch):
    """The pinned-shape receipt: gfs/20260721/18Z (and 12Z) present, newest
    first, each row {model, date, cycle}. Mirrors the live receipt the captain
    runs once the token is provisioned."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    keys = (
        _cycle_keys("gfs", "20260721", "18")
        + _cycle_keys("gfs", "20260721", "12")
        + _cycle_keys("gfs", "20260720", "00")
    )
    fake = FakeR2(keys=keys)
    configure(monkeypatch, fake)
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    # Platform envelope: {"cycles": [...]} (no bare array).
    assert d == {"cycles": [
        {"model": "gfs", "date": "20260721", "cycle": "18"},
        {"model": "gfs", "date": "20260721", "cycle": "12"},
        {"model": "gfs", "date": "20260720", "cycle": "00"},
    ]}
    print("ok cycles_lists_published_cycles_newest_first")


def test_cycles_excludes_incomplete_cycle_seam(client, monkeypatch):
    """A cycle with fewer than the three seed artifacts is NOT yet published —
    the v0 availability seam. 18Z has 3 artifacts (published); 06Z has 2 (not)."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    keys = (
        _cycle_keys("gfs", "20260721", "18", n=3)
        + _cycle_keys("gfs", "20260721", "06", n=2)   # incomplete -> excluded
    )
    fake = FakeR2(keys=keys)
    configure(monkeypatch, fake)
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    assert d == {"cycles": [{"model": "gfs", "date": "20260721", "cycle": "18"}]}
    print("ok cycles_excludes_incomplete_cycle_seam")


def test_cycles_respects_days_window(client, monkeypatch):
    """`days` is an inclusive UTC-date window ending today. With today=07-22 and
    days=7 the cutoff is 07-16, so a 07-10 cycle is out of window."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    keys = (
        _cycle_keys("gfs", "20260721", "18")
        + _cycle_keys("gfs", "20260710", "00")   # older than the 7-day window
    )
    fake = FakeR2(keys=keys)
    configure(monkeypatch, fake)
    d = client.get("/api/model-room/cycles?model=gfs&days=7").json()
    assert d == {"cycles": [{"model": "gfs", "date": "20260721", "cycle": "18"}]}
    # Widen the window and the older cycle reappears.
    d2 = client.get("/api/model-room/cycles?model=gfs&days=20").json()
    dates = {row["date"] for row in d2["cycles"]}
    assert dates == {"20260721", "20260710"}
    print("ok cycles_respects_days_window")


def test_cycles_invalid_model_rejected_before_r2(client, monkeypatch):
    """A hostile model slug is a 400 and never reaches R2 (no listing call)."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    fake = FakeR2(keys=[])
    configure(monkeypatch, fake)
    r = client.get("/api/model-room/cycles?model=../etc")
    assert r.status_code == 400
    assert fake.listed() == []
    print("ok cycles_invalid_model_rejected_before_r2")


def test_cycles_503_when_unprovisioned(client, monkeypatch):
    """No token yet -> 503, the STOP-GATE's honest answer."""
    freeze_now(monkeypatch, _dt(2026, 7, 22, 15))
    monkeypatch.setattr(main, "R2_ARCHIVE_BUCKET", "")
    monkeypatch.setattr(main, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(main, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(main, "R2_ENDPOINT", "")
    r = client.get("/api/model-room/cycles?model=gfs&days=7")
    assert r.status_code == 503
    print("ok cycles_503_when_unprovisioned")


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


if __name__ == "__main__":
    print("Run via pytest for full fixtures.")
