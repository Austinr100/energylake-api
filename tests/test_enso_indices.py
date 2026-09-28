"""
Tests for GET /api/enso/indices (d091493) — ONI and RONI monthly, read-only,
from timeseries_values. No database: the route runs against `_TsPool`, an
in-memory stand-in for timeseries_values that reads the SQL it is handed the way
Postgres + psycopg would, in the respects this route depends on:

  * `value` is `numeric`, so it comes back as `Decimal` (the explicit
    Decimal -> float conversion in enso_indices is what the route relies on);
  * `dataset = %(d)s AND series = %(s)s` filters only if the WHERE says so;
  * `ts` is a timezone-aware datetime, and rows are stored OUT of time order,
    so `ORDER BY ts` (or the payload's own sort) is what makes them ascend.

I1..I6 are the lane's test table; each test's name carries its number.
"""

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import enso_indices
import main

UTC = datetime.timezone.utc
ROOT = Path(__file__).resolve().parent.parent
INGESTED = datetime.datetime(2026, 9, 27, 18, 40, 9, 322000, tzinfo=UTC)

# The production allowlist (Railway's ALLOWED_ORIGINS) and one preview origin
# that matches main.VERCEL_PREVIEW_ORIGIN_REGEX.
APEX = "https://energylake.io"
WWW = "https://www.energylake.io"
PREVIEW = "https://energylake-git-enso-indices-austinrodriguez221-6328s-projects.vercel.app"
FOREIGN = "https://anyone.else"


def _row(dataset, series, y, m, value, ingested=INGESTED):
    return {"ts": datetime.datetime(y, m, 1, tzinfo=UTC), "dataset": dataset,
            "series": series,
            "value": Decimal(value) if value is not None else None,
            "meta": {"source": "noaa_cpc"}, "ingested_ts": ingested}


def _table():
    oni = [("2026", 7, "1.800000"), ("1950", 1, "-1.530000"), ("2026", 6, "1.500000"),
           ("1950", 2, "-1.340000")]
    roni = [("1950", 1, "-1.490000"), ("2026", 7, "1.400000"), ("1950", 2, "-1.300000")]
    rows = [_row("cpc_oni_monthly", "oni", int(y), m, v) for y, m, v in oni]
    rows += [_row("cpc_roni_monthly", "roni", int(y), m, v) for y, m, v in roni]
    # a neighbour in the same table that must never leak in
    rows.append(_row("cpc_oni_monthly", "oni_total", 2026, 7, "29.090000"))
    return rows


class _TsCursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._pool.statements.append((query, params))
        assert re.search(r"FROM\s+timeseries_values", query)
        rows = [dict(r) for r in self._pool.rows]
        if re.search(r"dataset\s*=\s*%\(d\)s", query):
            rows = [r for r in rows if r["dataset"] == params["d"]]
        if re.search(r"series\s*=\s*%\(s\)s", query):
            rows = [r for r in rows if r["series"] == params["s"]]
        if re.search(r"ORDER BY\s+ts\s*$", query.strip()):
            rows.sort(key=lambda r: r["ts"])
        cols = [c.strip() for c in
                re.search(r"SELECT\s+(.+?)\s+FROM", query, re.S).group(1).split(",")]
        self._rows = [{c: r[c] for c in cols} for r in rows]

    async def fetchall(self):
        return list(self._rows)


class _TsConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        if self._pool.fail:
            raise RuntimeError("connection refused")
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _TsCursor(self._pool)


class _TsPool:
    def __init__(self, rows=None, fail=False):
        self.rows = rows if rows is not None else _table()
        self.fail = fail
        self.checkouts = 0
        self.statements = []

    def connection(self):
        self.checkouts += 1
        return _TsConn(self)


@pytest.fixture(autouse=True)
def _clear_memo():
    main._enso_indices_cache.clear()
    yield
    main._enso_indices_cache.clear()


@pytest.fixture
def pool(monkeypatch):
    p = _TsPool()
    monkeypatch.setattr(main, "_pool", p)
    return p


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => the real pool is never opened.
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# I1 — shape, ascending order, month strings
# ---------------------------------------------------------------------------

def test_i1_shape_ascending_month_strings(pool, client):
    resp = client.get("/api/enso/indices")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body) == ["indices"]
    assert list(body["indices"]) == ["oni", "roni"]
    oni = body["indices"]["oni"]
    assert list(oni) == ["dataset", "series", "n", "n_null", "first", "last",
                         "last_ingested", "values"]
    assert (oni["dataset"], oni["series"]) == ("cpc_oni_monthly", "oni")
    assert oni["n"] == 4 and oni["n_null"] == 0
    assert (oni["first"], oni["last"]) == ("1950-01", "2026-07")
    assert oni["last_ingested"] == "2026-09-27T18:40:09.322000Z"
    assert oni["values"] == [["1950-01", -1.53], ["1950-02", -1.34],
                             ["2026-06", 1.5], ["2026-07", 1.8]]
    roni = body["indices"]["roni"]
    assert (roni["dataset"], roni["series"]) == ("cpc_roni_monthly", "roni")
    assert [m for m, _ in roni["values"]] == ["1950-01", "1950-02", "2026-07"]
    for block in body["indices"].values():
        months = [m for m, _ in block["values"]]
        assert months == sorted(months)
        assert all(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", m) for m in months)
        assert all(type(v) is float for _, v in block["values"])
    # the neighbouring series (oni_total) never leaks in
    assert 29.09 not in [v for _, v in oni["values"]]


def test_i1_ascends_even_if_the_sql_order_is_dropped():
    rows = [r for r in _table() if r["series"] == "oni"]      # stored out of order
    block = enso_indices.build_series("oni", rows)
    assert [m for m, _ in block["values"]] == ["1950-01", "1950-02", "2026-06", "2026-07"]


def test_i1_season_centre_month_is_utc_and_must_be_a_month_start():
    # 2026-07-01T00:00Z read back in a -04:00 session is still 2026-07.
    ts = datetime.datetime(2026, 7, 1, tzinfo=UTC).astimezone(
        datetime.timezone(datetime.timedelta(hours=-4)))
    block = enso_indices.build_series(
        "oni", [{"ts": ts, "value": Decimal("1.8"), "ingested_ts": INGESTED}])
    assert block["values"] == [["2026-07", 1.8]]
    with pytest.raises(ValueError, match="not a month start"):
        enso_indices.build_series("oni", [{"ts": datetime.datetime(2026, 7, 15, tzinfo=UTC),
                                           "value": Decimal("1.8"), "ingested_ts": INGESTED}])


# ---------------------------------------------------------------------------
# I2 — ETag: content-addressed, stable, moves with one value, 304
# ---------------------------------------------------------------------------

def test_i2_etag_stable_and_content_addressed(pool, client):
    a = client.get("/api/enso/indices")
    tag = a.headers["ETag"]
    assert re.fullmatch(r'W/"[0-9a-f]{16}"', tag)
    assert a.headers["Cache-Control"] == "max-age=3600"

    main._enso_indices_cache.clear()
    pool.rows = [dict(r, ingested_ts=INGESTED + datetime.timedelta(days=1)) for r in pool.rows]
    b = client.get("/api/enso/indices")
    assert b.headers["ETag"] == tag             # re-ingest, same values: same tag
    assert b.json()["indices"]["oni"]["last_ingested"] != a.json()["indices"]["oni"]["last_ingested"]


def test_i2_etag_changes_with_one_value(pool, client):
    tag = client.get("/api/enso/indices").headers["ETag"]
    main._enso_indices_cache.clear()
    for r in pool.rows:
        if r["series"] == "roni" and r["ts"].year == 2026:
            r["value"] = Decimal("1.410000")
    assert client.get("/api/enso/indices").headers["ETag"] != tag


def test_i2_etag_differs_per_index_set(pool, client):
    both = client.get("/api/enso/indices").headers["ETag"]
    oni = client.get("/api/enso/indices?index=oni").headers["ETag"]
    roni = client.get("/api/enso/indices?index=roni").headers["ETag"]
    assert len({both, oni, roni}) == 3


def test_i2_304_on_match_with_headers(pool, client):
    tag = client.get("/api/enso/indices").headers["ETag"]
    again = client.get("/api/enso/indices", headers={"If-None-Match": tag})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == tag
    assert again.headers["Cache-Control"] == "max-age=3600"
    # weak compare: the strong form and a comma list both match
    strong = tag.removeprefix("W/")
    assert client.get("/api/enso/indices",
                      headers={"If-None-Match": f'"x", {strong}'}).status_code == 304
    assert client.get("/api/enso/indices",
                      headers={"If-None-Match": 'W/"0000000000000000"'}).status_code == 200


def test_i2_etag_is_sha256_of_canonical_values():
    import hashlib
    payload = {"indices": {"oni": {"values": [["1950-01", -1.53]]}}}
    canon = '{"oni":[["1950-01",-1.53]]}'
    assert enso_indices.etag(payload) == \
        f'W/"{hashlib.sha256(canon.encode()).hexdigest()[:16]}"'


# ---------------------------------------------------------------------------
# I3 — 400 and single-index
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("q", ["nino34", "oni,nino34", "", "oni,", "ONI"])
def test_i3_unknown_index_400_names_the_allowed_set(pool, client, q):
    resp = client.get(f"/api/enso/indices?index={q}")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "allowed: oni, roni" in detail
    assert pool.checkouts == 0
    assert "ETag" not in resp.headers and "Cache-Control" not in resp.headers


@pytest.mark.parametrize("q,want", [("oni", ["oni"]), ("roni", ["roni"]),
                                    ("roni,oni", ["oni", "roni"]),
                                    ("oni,oni", ["oni"]), (" roni ", ["roni"])])
def test_i3_single_and_reordered_index(pool, client, q, want):
    body = client.get(f"/api/enso/indices?index={q}").json()
    assert list(body["indices"]) == want
    assert len(pool.statements) == len(want)     # only the asked-for series is read


# ---------------------------------------------------------------------------
# I4 — Decimal -> float, None dropped and counted, a stray Decimal fails loud
# ---------------------------------------------------------------------------

def test_i4_decimal_to_float_and_nulls_counted(pool, client):
    pool.rows.append(_row("cpc_oni_monthly", "oni", 1950, 3, None))
    pool.rows.append(_row("cpc_oni_monthly", "oni", 2026, 8, None))   # trailing null
    body = client.get("/api/enso/indices?index=oni").json()
    oni = body["indices"]["oni"]
    assert oni["n"] == 6 and oni["n_null"] == 2
    assert len(oni["values"]) == oni["n"] - oni["n_null"]
    assert oni["last"] == "2026-07"                   # a null month is not `last`
    assert "1950-03" not in [m for m, _ in oni["values"]]
    assert all(v is not None and type(v) is float for _, v in oni["values"])


def test_i4_build_series_types():
    rows = [{"ts": datetime.datetime(1950, 1, 1, tzinfo=UTC), "value": Decimal("-1.530000"),
             "ingested_ts": INGESTED}]
    block = enso_indices.build_series("oni", rows)
    assert block["values"] == [["1950-01", -1.53]]
    assert type(block["values"][0][1]) is float
    json.dumps(block, allow_nan=False)            # plain json: a Decimal would raise
    with pytest.raises(TypeError, match="oni value is str"):
        enso_indices.build_series("oni", [dict(rows[0], value="1.2")])
    with pytest.raises(ValueError, match="not finite"):
        enso_indices.build_series("oni", [dict(rows[0], value=Decimal("NaN"))])


def test_i4_stray_decimal_fails_loud_not_coerced(pool, client, monkeypatch):
    """If a Decimal ever reached the body, the route's plain JSONResponse must
    raise rather than quietly coerce it the way jsonable_encoder would."""
    real = enso_indices.build_payload

    def leaky(rows_by_index):
        p = real(rows_by_index)
        p["indices"]["oni"]["values"][0][1] = Decimal("-1.53")
        return p

    monkeypatch.setattr(main._enso_idx, "build_payload", leaky)
    monkeypatch.setattr(main._enso_idx, "etag", lambda p: 'W/"leak"')
    with pytest.raises(TypeError, match="Decimal"):
        TestClient(main.app, raise_server_exceptions=True).get("/api/enso/indices?index=oni")


# ---------------------------------------------------------------------------
# I5 — zero rows for a requested index -> 503 naming it, nothing memoised
# ---------------------------------------------------------------------------

def test_i5_zero_rows_503_names_it_and_is_not_memoised(pool, client):
    pool.rows = [r for r in pool.rows if r["dataset"] != "cpc_roni_monthly"]
    resp = client.get("/api/enso/indices")
    assert resp.status_code == 503
    assert resp.json()["detail"] == "no rows banked for roni (cpc_roni_monthly/roni)"
    assert "ETag" not in resp.headers and "Cache-Control" not in resp.headers
    assert main._enso_indices_cache == {}
    # oni alone is still served
    assert client.get("/api/enso/indices?index=oni").status_code == 200
    # and once roni lands, the very next request reads it (nothing stale cached)
    pool.rows = _table()
    assert client.get("/api/enso/indices").status_code == 200


def test_i5_db_unavailable_503(monkeypatch, client):
    monkeypatch.setattr(main, "_pool", _TsPool(fail=True))
    resp = client.get("/api/enso/indices")
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("db unavailable")
    assert main._enso_indices_cache == {}


def test_i5_memo_keyed_on_index_set_and_expires(pool, client):
    client.get("/api/enso/indices")
    client.get("/api/enso/indices?index=roni,oni")     # same set, canonicalised
    assert pool.checkouts == 1
    hit = client.get("/api/enso/indices")
    assert hit.headers["Cache-Control"] == "max-age=3600" and "ETag" in hit.headers
    client.get("/api/enso/indices?index=oni")
    assert pool.checkouts == 2
    ts, payload, tag = main._enso_indices_cache[("oni", "roni")]
    main._enso_indices_cache[("oni", "roni")] = (ts - 61.0, payload, tag)
    client.get("/api/enso/indices")
    assert pool.checkouts == 3


# ---------------------------------------------------------------------------
# I6 — the existing CORS middleware covers the route (www, apex, preview)
# ---------------------------------------------------------------------------

@pytest.fixture
def prod_origins():
    """Put the production allowlist into the list object the REAL app's CORS
    middleware holds (asserted to be the same object), then restore it."""
    mw = next(m for m in main.app.user_middleware
              if m.cls is main.SkyExemptCORSMiddleware)
    assert mw.kwargs["allow_origins"] is main.ALLOWED_ORIGINS
    assert mw.kwargs["allow_origin_regex"] == main.VERCEL_PREVIEW_ORIGIN_REGEX
    assert mw.kwargs["allow_credentials"] is True
    assert "GET" in mw.kwargs["allow_methods"]
    saved = list(main.ALLOWED_ORIGINS)
    main.ALLOWED_ORIGINS[:] = [APEX, WWW]
    yield
    main.ALLOWED_ORIGINS[:] = saved


def test_i6_route_is_not_cors_exempt():
    assert not "/api/enso/indices".startswith(main.SKY_CORS_EXEMPT_PREFIXES)


@pytest.mark.parametrize("origin", [APEX, WWW, PREVIEW])
def test_i6_cors_echoes_trusted_origins(pool, client, prod_origins, origin):
    resp = client.get("/api/enso/indices", headers={"Origin": origin})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == origin
    assert resp.headers["access-control-allow-credentials"] == "true"

    tag = resp.headers["ETag"]
    nm = client.get("/api/enso/indices", headers={"Origin": origin, "If-None-Match": tag})
    assert nm.status_code == 304
    assert nm.headers["access-control-allow-origin"] == origin

    pre = client.options("/api/enso/indices",
                         headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                  "Access-Control-Request-Headers": "if-none-match"})
    assert pre.status_code == 200
    assert pre.headers["access-control-allow-origin"] == origin


def test_i6_foreign_origin_gets_no_acao(pool, client, prod_origins):
    resp = client.get("/api/enso/indices", headers={"Origin": FOREIGN})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


# ---------------------------------------------------------------------------
# wiring: route registered once, beside the catalog; docs rows present
# ---------------------------------------------------------------------------

def test_route_placement_and_docs():
    paths = [getattr(r, "path", None) for r in main.app.routes]
    assert paths.count("/api/enso/indices") == 1
    assert paths.index("/api/enso/indices") == paths.index("/api/enso/catalog") + 1
    assert "GET /api/enso/indices?index=oni,roni" in (ROOT / "README.md").read_text()
    assert "GET /api/enso/indices " in main.__doc__
