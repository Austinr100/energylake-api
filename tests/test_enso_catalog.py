"""
Tests for GET /api/enso/catalog (d091476) — the ENSO catalog, read-only, from
the bank. No database: the route runs against `_BankPool`, an in-memory stand-in
for migration 240's three tables that reads the SQL it is handed the way
Postgres + psycopg would, in the three respects this route depends on:

  * a `numeric` column comes back as `Decimal` unless the SELECT casts it
    `::float8` (so dropping a cast is visible here — spec R1);
  * `catalog_version = %(v)s` filters only if the WHERE clause says so (so
    dropping the version scope is visible here — spec R2);
  * `ORDER BY start_year, start_season` sorts the season TEXT, i.e.
    alphabetically (so the chronological re-sort in build_payload is tested).

T1..T7 are the spec's §3 table; each test's name carries its number.
"""

import datetime
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import enso_catalog
import main

UTC = datetime.timezone.utc
ROOT = Path(__file__).resolve().parent.parent

CUR = "62f522e5aa11"     # the current version (both classifiers, as in the bank)
OLD = "0badc0de0001"     # an older version still present in the fixture
COMPUTED = datetime.datetime(2026, 9, 24, 17, 16, 15, tzinfo=UTC)
EARLIER = datetime.datetime(2026, 8, 24, 17, 0, 0, tzinfo=UTC)

SOURCE = {"oni": {"dataset": "cpc_oni_monthly", "max_ts": "2026-07-01T00:00:00Z"},
          "nino_long": None}
DEVELOPING = {"kind": "nino", "n_seasons": 4, "last_oni": 1.8,
              "from": "MAM 2026", "to": "JJA 2026"}


# ---------------------------------------------------------------------------
# Fixture rows shaped like spec §0 / migration 240 (numeric columns as Decimal,
# exactly what psycopg returns for an uncast `numeric`).
# ---------------------------------------------------------------------------

def _run(classifier, version, computed_at, n_episodes, n_year_bins, developing):
    return {"classifier": classifier, "developing": developing, "source": SOURCE,
            "n_episodes": n_episodes, "n_year_bins": n_year_bins,
            "catalog_version": version, "computed_at": computed_at}


def _ep(classifier, version, episode_id, kind, label, start, end, n, peak,
        peak_at, strength, very_strong, years):
    return {"classifier": classifier, "catalog_version": version,
            "computed_at": COMPUTED, "episode_id": episode_id, "kind": kind,
            "label": label, "start_year": start[0], "start_season": start[1],
            "end_year": end[0] if end else None,
            "end_season": end[1] if end else None, "open": end is None,
            "n_seasons": n, "peak_oni": Decimal(peak), "peak_year": peak_at[0],
            "peak_season": peak_at[1], "strength": strength,
            "very_strong": very_strong, "enso_years": years}


def _bin(classifier, version, year, label, kind, strength, pwo, episode_id,
         n34=None, notes=None, concurrent=("neutral",) * 4):
    djf, mam, jja, son = concurrent
    return {"classifier": classifier, "catalog_version": version,
            "computed_at": COMPUTED, "enso_year": year, "label": label,
            "kind": kind, "strength": strength,
            "peak_window_oni": Decimal(pwo) if pwo is not None else None,
            "flavor": None, "n3_minus_n4": Decimal(n34) if n34 is not None else None,
            "concurrent_djf": djf, "concurrent_mam": mam, "concurrent_jja": jja,
            "concurrent_son": son, "episode_id": episode_id, "notes": notes}


def _bank():
    runs = [
        _run("cpc_oni", CUR, COMPUTED, 4, 3, DEVELOPING),
        _run("cpc_oni", OLD, EARLIER, 1, 1, None),
        _run("roni", CUR, COMPUTED, 2, 2, {"kind": "nino", "n_seasons": 3, "last_oni": 1.4}),
    ]
    episodes = [
        _ep("cpc_oni", CUR, "nino-1997-AMJ", "nino", "1997-98", (1997, "AMJ"),
            (1998, "AMJ"), 13, "2.4", (1997, "NDJ"), "strong", True, [1997]),
        # Two synthetic episodes starting in the same year, where the season TEXT
        # order (JAS < MAM) is the reverse of time order (MAM before JAS).
        _ep("cpc_oni", CUR, "nina-2011-JAS", "nina", "2011-12", (2011, "JAS"),
            (2012, "FMA"), 8, "-1.0", (2011, "NDJ"), "moderate", False, [2011]),
        _ep("cpc_oni", CUR, "nino-2011-MAM", "nino", "2011", (2011, "MAM"),
            (2011, "JJA"), 5, "0.5", (2011, "MAM"), "weak", False, [2010]),
        _ep("cpc_oni", CUR, "nino-2025-OND", "nino", "2025-27", (2025, "OND"),
            None, 9, "1.8", (2026, "JJA"), "moderate", False, [2025, 2026]),
        # the OLD version: same classifier, must never be served
        _ep("cpc_oni", OLD, "nino-1982-AMJ", "nino", "1982-83", (1982, "AMJ"),
            (1983, "MJJ"), 15, "2.2", (1982, "NDJ"), "strong", True, [1982]),
        _ep("roni", CUR, "nino-1997-AMJ", "nino", "1997-98", (1997, "AMJ"),
            (1998, "MAM"), 12, "2.2", (1997, "NDJ"), "strong", True, [1997]),
        _ep("roni", CUR, "nina-2020-JJA", "nina", "2020-23", (2020, "JJA"),
            (2023, "JFM"), 33, "-1.3", (2020, "OND"), "moderate", False, [2020, 2021, 2022]),
    ]
    bins = [
        _bin("cpc_oni", CUR, 2026, "2026-27", "nino", "moderate", "1.8", "nino-2025-OND",
             concurrent=("nino", "nino", "nino", "nino")),
        _bin("cpc_oni", CUR, 1997, "1997-98", "nino", "strong", "2.4", "nino-1997-AMJ",
             notes="very strong"),
        _bin("cpc_oni", CUR, 2013, "2013-14", "neutral", None, "-0.3", None),
        _bin("cpc_oni", OLD, 1982, "1982-83", "nino", "strong", "2.2", "nino-1982-AMJ"),
        _bin("roni", CUR, 1997, "1997-98", "nino", "strong", "2.2", "nino-1997-AMJ"),
        _bin("roni", CUR, 2020, "2020-21", "nina", "moderate", "-1.3", "nina-2020-JJA"),
    ]
    return {"enso_catalog_runs": runs, "enso_episodes": episodes, "enso_year_bins": bins}


_NUMERIC = {"enso_episodes": ("peak_oni",),
            "enso_year_bins": ("peak_window_oni", "n3_minus_n4")}


class _BankCursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._pool.statements.append(query)
        table = re.search(r"FROM\s+(\w+)", query).group(1)
        rows = [dict(r) for r in self._pool.tables[table]
                if r["classifier"] == params["c"]]
        if re.search(r"catalog_version\s*=\s*%\(v\)s", query):
            rows = [r for r in rows if r["catalog_version"] == params["v"]]
        for col in _NUMERIC.get(table, ()):
            if f"{col}::float8" in query:
                for r in rows:
                    r[col] = float(r[col]) if r[col] is not None else None
        order = re.search(r"ORDER BY\s+(.+?)(?:\s+LIMIT|\s*$)", query.strip(), re.S)
        if order:
            for term in reversed([t.strip() for t in order.group(1).split(",")]):
                col, *direction = term.split()
                rows.sort(key=lambda r: r[col], reverse=direction == ["DESC"])
        if re.search(r"LIMIT 1\s*$", query.strip()):
            rows = rows[:1]
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _BankConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        if self._pool.fail:
            raise RuntimeError("connection refused")
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _BankCursor(self._pool)


class _BankPool:
    def __init__(self, tables=None, fail=False):
        self.tables = tables if tables is not None else _bank()
        self.fail = fail
        self.checkouts = 0
        self.statements = []

    def connection(self):
        self.checkouts += 1
        return _BankConn(self)


@pytest.fixture(autouse=True)
def _clear_memo():
    main._enso_catalog_cache.clear()
    yield
    main._enso_catalog_cache.clear()


@pytest.fixture
def pool(monkeypatch):
    p = _BankPool()
    monkeypatch.setattr(main, "_pool", p)
    return p


@pytest.fixture
def client():
    # No `with` block => lifespan does not run => the real pool is never opened.
    return TestClient(main.app)


def _float_rows(rows, cols):
    return [{k: (float(v) if k in cols and v is not None else v) for k, v in r.items()}
            for r in rows]


def _current_rows(classifier="cpc_oni"):
    b = _bank()
    run = next(r for r in b["enso_catalog_runs"]
               if r["classifier"] == classifier and r["catalog_version"] == CUR)
    eps = [r for r in b["enso_episodes"]
           if r["classifier"] == classifier and r["catalog_version"] == CUR]
    bins = [r for r in b["enso_year_bins"]
            if r["classifier"] == classifier and r["catalog_version"] == CUR]
    return (run, _float_rows(eps, ("peak_oni",)),
            _float_rows(bins, ("peak_window_oni", "n3_minus_n4")))


# ---------------------------------------------------------------------------
# T1 — build_payload: shape, types, nulls, timestamps, ordering, version scope
# ---------------------------------------------------------------------------

def test_t1_build_payload_key_order_types_and_nulls():
    run, eps, bins = _current_rows()
    p = enso_catalog.build_payload(run, eps, bins)
    assert list(p) == ["classifier", "catalog_version", "computed_at", "source",
                       "developing", "counts", "episodes", "year_bins"]
    assert list(p["episodes"][0]) == list(enso_catalog.EPISODE_KEYS)
    assert list(p["year_bins"][0]) == list(enso_catalog.YEAR_BIN_KEYS)
    assert p["computed_at"] == "2026-09-24T17:16:15Z"
    assert p["source"] == SOURCE and p["developing"] == DEVELOPING
    assert p["counts"] == {"episodes": 4, "year_bins": 3}
    for e in p["episodes"]:
        assert type(e["peak_oni"]) is float
        assert isinstance(e["enso_years"], list)
        assert all(type(y) is int for y in e["enso_years"])
    open_ep = next(e for e in p["episodes"] if e["open"])
    assert open_ep["end_year"] is None and open_ep["end_season"] is None

    text = json.dumps(p)                      # plain json: a Decimal would raise here
    back = json.loads(text)
    assert all(b["flavor"] is None for b in back["year_bins"])
    assert '"flavor": null' in text and '"flavor": ""' not in text
    neutral = next(b for b in back["year_bins"] if b["kind"] == "neutral")
    assert neutral["n3_minus_n4"] is None and neutral["notes"] is None
    assert neutral["episode_id"] is None and neutral["strength"] is None
    assert type(neutral["peak_window_oni"]) is float


def test_t1_computed_at_normalised_to_utc_z():
    run, eps, bins = _current_rows()
    run = dict(run, computed_at=COMPUTED.astimezone(
        datetime.timezone(datetime.timedelta(hours=-4))))
    assert enso_catalog.build_payload(run, eps, bins)["computed_at"] == "2026-09-24T17:16:15Z"


def test_t1_build_payload_refuses_decimal_by_name():
    run, eps, bins = _current_rows()
    eps[0]["peak_oni"] = Decimal("2.4")
    with pytest.raises(TypeError, match="peak_oni.*::float8"):
        enso_catalog.build_payload(run, eps, bins)


def test_t1_episodes_sorted_chronologically_not_by_season_text():
    run, eps, bins = _current_rows()
    ids = [e["episode_id"] for e in enso_catalog.build_payload(run, eps, bins)["episodes"]]
    assert ids == ["nino-1997-AMJ", "nino-2011-MAM", "nina-2011-JAS", "nino-2025-OND"]


def test_t1_route_serves_floats_through_the_real_sql(pool, client):
    """The route end to end: the fake returns Decimal for any numeric the SQL
    does not cast, so this is where a dropped ::float8 shows up (R1)."""
    resp = client.get("/api/enso/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert [type(e["peak_oni"]) for e in body["episodes"]] == [float] * 4
    assert body["year_bins"][0]["peak_window_oni"] == 2.4
    assert body["computed_at"] == "2026-09-24T17:16:15Z"


def test_t1_only_the_current_version_is_served(pool, client):
    """Two versions of cpc_oni sit in the fixture; only the newest run's rows
    come back, and the old version's episode and bin never appear (R2)."""
    body = client.get("/api/enso/catalog?classifier=cpc_oni").json()
    assert body["catalog_version"] == CUR
    assert body["counts"] == {"episodes": 4, "year_bins": 3}
    assert "nino-1982-AMJ" not in {e["episode_id"] for e in body["episodes"]}
    assert 1982 not in {b["enso_year"] for b in body["year_bins"]}
    assert [b["enso_year"] for b in body["year_bins"]] == [1997, 2013, 2026]


def test_t1_short_read_is_503_and_not_memoised(pool, client):
    """A bank landing between query 1 and queries 2/3 leaves them short of the
    run's own counts; that is refused, not served or cached."""
    pool.tables["enso_episodes"] = [e for e in pool.tables["enso_episodes"]
                                    if e["episode_id"] != "nino-2025-OND"]
    resp = client.get("/api/enso/catalog")
    assert resp.status_code == 503
    assert "read short" in resp.json()["detail"]
    assert "ETag" not in resp.headers
    assert main._enso_catalog_cache == {}


# ---------------------------------------------------------------------------
# T2..T4 — the error paths (no cache headers on any of them)
# ---------------------------------------------------------------------------

def _no_cache_headers(resp):
    assert "ETag" not in resp.headers
    assert "Cache-Control" not in resp.headers


def test_t2_unknown_classifier_400_names_the_two(pool, client):
    resp = client.get("/api/enso/catalog?classifier=nino9")
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "cpc_oni" in detail and "roni" in detail
    assert pool.checkouts == 0
    _no_cache_headers(resp)


def test_t3_no_run_banked_404(monkeypatch, client):
    tables = _bank()
    tables["enso_catalog_runs"] = [r for r in tables["enso_catalog_runs"]
                                   if r["classifier"] != "roni"]
    monkeypatch.setattr(main, "_pool", _BankPool(tables))
    resp = client.get("/api/enso/catalog?classifier=roni")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "no catalog banked for roni"}
    _no_cache_headers(resp)


def test_t4_db_unavailable_503(monkeypatch, client):
    monkeypatch.setattr(main, "_pool", _BankPool(fail=True))
    resp = client.get("/api/enso/catalog")
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("db unavailable")
    _no_cache_headers(resp)


# ---------------------------------------------------------------------------
# T5 — headers, ETag, 304
# ---------------------------------------------------------------------------

def test_t5_cache_control_etag_and_304(pool, client):
    resp = client.get("/api/enso/catalog")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "max-age=3600"
    assert resp.headers["ETag"] == f'W/"{CUR}"'

    again = client.get("/api/enso/catalog", headers={"If-None-Match": resp.headers["ETag"]})
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == f'W/"{CUR}"'
    assert again.headers["Cache-Control"] == "max-age=3600"


def test_t5_304_on_fresh_read_and_listed_or_strong_tags(pool, client):
    # no memo yet: the 304 still comes from a real read
    resp = client.get("/api/enso/catalog", headers={"If-None-Match": f'"x", "{CUR}"'})
    assert resp.status_code == 304
    stale = client.get("/api/enso/catalog", headers={"If-None-Match": 'W/"not-it"'})
    assert stale.status_code == 200 and stale.json()["catalog_version"] == CUR


# ---------------------------------------------------------------------------
# T6 — the 60 s memo, keyed by classifier
# ---------------------------------------------------------------------------

def test_t6_memo_one_checkout_per_classifier(pool, client):
    a = client.get("/api/enso/catalog?classifier=cpc_oni")
    b = client.get("/api/enso/catalog?classifier=cpc_oni")
    assert a.status_code == b.status_code == 200
    assert pool.checkouts == 1
    assert b.json() == a.json()
    assert b.headers["ETag"] == f'W/"{CUR}"'           # a memo hit still sets both headers
    assert b.headers["Cache-Control"] == "max-age=3600"

    r = client.get("/api/enso/catalog?classifier=roni")
    assert pool.checkouts == 2
    body = r.json()
    assert body["classifier"] == "roni"
    assert body["counts"] == {"episodes": 2, "year_bins": 2}


def test_t6_memo_expires_after_60s(pool, client):
    client.get("/api/enso/catalog")
    ts, payload, version = main._enso_catalog_cache["cpc_oni"]
    main._enso_catalog_cache["cpc_oni"] = (ts - 61.0, payload, version)
    client.get("/api/enso/catalog")
    assert pool.checkouts == 2


# ---------------------------------------------------------------------------
# T7 — wiring: route registered once, after the ladder; docs rows present
# ---------------------------------------------------------------------------

def test_t7_route_placement_and_docs():
    paths = [getattr(r, "path", None) for r in main.app.routes]
    assert paths.count("/api/enso/catalog") == 1
    assert paths.index("/api/enso/catalog") > paths.index("/api/weather/point/ladder")
    assert "GET /api/enso/catalog?classifier=cpc_oni|roni" in (ROOT / "README.md").read_text()
    assert "GET /api/enso/catalog " in main.__doc__
