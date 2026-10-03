"""
Tests for GET /api/weather/dd/forecast/regions (lane d091576, D-09-25-120).

Fixture rows carry the column sets of pantry's three views as the route
selects them (v_degree_days_region_forecast, v_degree_days_model_delta,
v_degree_days_model_spread), measured on Neon 2026-10-03. The pure layer
(degree_days.region_forecast_payload) is exercised directly; route tests use
a fake pool that also records the statements and the transaction around them.

  R1  a change is only ever between two issuances of one source_product
  R2  spacing_comparable false reaches the payload; the change is still served
  R3  a period with a missing or incomplete day is complete: false, no sum
  R4  gridpoints_raw is served as label "NWS"; nothing else maps labels
  R5  a row with no normal serves normal null and null departures, and the value
  R6  bad region / weighting / from / days are each a 400 naming the field
  R7  the per-station route's tests (test_weather_dd_ledger.py) run unchanged
"""

import datetime
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import degree_days as dd
import main


D = datetime.date
UTC = datetime.timezone.utc


def T(day, hour, minute=0):
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=UTC)


PNW = ["USW00024131", "USW00024157", "USW00024229", "USW00024233"]


def _fc(target, src, issued, *, hdd=5.0, cdd=0.5, tavg=60.0, hdd_norm=7.0,
        cdd_norm=0.3, complete=True, spacing=6, missing=None, region="pnw"):
    """A v_degree_days_region_forecast row as the route selects it.

    The view nulls every value column when basis_complete is false; pass
    complete=False to get that shape.
    """
    if not complete:
        hdd = cdd = tavg = hdd_norm = cdd_norm = None
        spacing = None
    return {
        "region": region, "weighting": "population",
        "target_date": target, "issued_ts": issued, "source_product": src,
        "basis_complete": complete,
        "hdd": hdd, "cdd": cdd, "tavg_f": tavg,
        "hdd_norm": hdd_norm, "cdd_norm": cdd_norm,
        "hdd_vs_norm": None if hdd is None or hdd_norm is None else hdd - hdd_norm,
        "cdd_vs_norm": None if cdd is None or cdd_norm is None else cdd - cdd_norm,
        "sample_spacing_hours": spacing,
        "member_stations": 4,
        "members_present": 4 - len(missing or []) if complete else 0,
        "missing_stations": missing if complete else (missing or PNW),
        "rn": 1,
    }


def _delta(target, src, issued, prior, *, hdd=1.0, cdd=-0.5, spacing=6,
           prior_spacing=6, comparable=True, complete=True, prior_complete=True,
           region="pnw"):
    return {
        "region": region, "weighting": "population",
        "target_date": target, "source_product": src,
        "issued_ts": issued, "prior_issued_ts": prior,
        "hdd_delta": hdd, "cdd_delta": cdd,
        "basis_complete": complete, "prior_basis_complete": prior_complete,
        "sample_spacing_hours": spacing,
        "prior_sample_spacing_hours": prior_spacing,
        "spacing_comparable": comparable, "rn": 1,
    }


def _spread(target, sources, *, hdd=2.0, cdd=0.4, complete=None,
            comparable=True, region="pnw"):
    return {
        "region": region, "weighting": "population", "target_date": target,
        "source_products": sources, "sources_present": len(sources),
        "sources_complete": len(sources) if complete is None else complete,
        "hdd_max_minus_min": hdd, "cdd_max_minus_min": cdd,
        "spacing_comparable": comparable,
    }


def _board(fc, delta=(), spread=(), *, from_date=D(2026, 10, 3), days=16):
    return dd.region_forecast_payload(
        list(fc), list(delta), list(spread), regions=["pnw"],
        weighting="population", from_date=from_date, days=days,
        members={"pnw": PNW})


def _day(board, iso):
    return next(d for d in board["regions"][0]["days"] if d["target_date"] == iso)


def _period(board, window):
    return next(p for p in board["regions"][0]["periods"] if p["window"] == window)


# ═══════════════════════════════════════════════════════════════════════════
# R1 — a change is one source against its own previous issuance
# ═══════════════════════════════════════════════════════════════════════════

def test_r1_change_is_only_ever_within_one_source_product():
    """Interleaved runs: GFS 10-02 12Z, IFS 10-02 18Z, IFS 10-03 00Z, GFS 10-03
    06Z. GFS's change must be against GFS 12Z, never against the IFS run that
    sits between them in time."""
    day = D(2026, 10, 5)
    fc = [_fc(day, "GFS", T(3, 6), hdd=4.0), _fc(day, "IFS", T(3, 0), hdd=9.0)]
    delta = [
        _delta(day, "GFS", T(3, 6), T(2, 12), hdd=0.25, cdd=0.0),
        _delta(day, "IFS", T(3, 0), datetime.datetime(2026, 10, 2, 18, tzinfo=UTC),
               hdd=-1.5, cdd=0.1),
    ]
    by = _day(_board(fc, delta), "2026-10-05")["by_source"]

    assert by["GFS"]["change"]["prior_issued_ts"] == T(2, 12).isoformat()
    assert by["GFS"]["change"]["hdd"] == 0.25          # the view's GFS-vs-GFS delta
    assert by["GFS"]["change"]["hdd"] != 4.0 - 9.0     # never GFS minus IFS
    assert by["IFS"]["change"]["prior_issued_ts"] == "2026-10-02T18:00:00+00:00"
    assert by["IFS"]["change"]["hdd"] == -1.5


def test_r1_a_delta_row_never_attaches_to_another_source_or_issuance():
    day = D(2026, 10, 5)
    fc = [_fc(day, "GFS", T(3, 6))]
    # Only an IFS delta, and a GFS delta for an OLDER issuance than the cell's.
    delta = [_delta(day, "IFS", T(3, 0), T(2, 12)),
             _delta(day, "GFS", T(2, 12), T(2, 6))]
    cell = _day(_board(fc, delta), "2026-10-05")["by_source"]["GFS"]
    assert cell["change"] is None
    assert cell["change_absence"]["reason"] == "no_prior_issuance"


def test_r1_all_three_reads_rank_per_source_product():
    for sql in (main._DD_REGION_FC_SQL, main._DD_REGION_DELTA_SQL):
        flat = " ".join(sql.split())
        assert ("PARTITION BY region, weighting, source_product, target_date "
                "ORDER BY issued_ts DESC") in flat
        assert "WHERE rn = 1" in flat
    # The delta itself is the view's lag() over the same per-source partition.
    assert "v_degree_days_model_delta" in main._DD_REGION_DELTA_SQL


def test_first_sighting_is_an_absence_not_a_zero_change():
    day = D(2026, 10, 17)
    fc = [_fc(day, "GFS", T(3, 6))]
    delta = [_delta(day, "GFS", T(3, 6), None, hdd=None, cdd=None, comparable=None)]
    cell = _day(_board(fc, delta), "2026-10-17")["by_source"]["GFS"]
    assert cell["change"] is None
    assert cell["change_absence"]["reason"] == "no_prior_issuance"


def test_change_against_an_incomplete_prior_names_why_it_is_absent():
    """Measured: GFS 06Z 2026-10-10 is complete but its 12Z prior is the 3h->6h
    seam day (7 of 8 samples), so the view carries no delta."""
    day = D(2026, 10, 10)
    fc = [_fc(day, "GFS", T(3, 6))]
    delta = [_delta(day, "GFS", T(3, 6), T(2, 12), hdd=None, cdd=None,
                    prior_spacing=None, comparable=False, prior_complete=False)]
    cell = _day(_board(fc, delta), "2026-10-10")["by_source"]["GFS"]
    assert cell["change"] is None
    assert cell["change_absence"]["reason"] == "incomplete_basis"
    assert cell["change_absence"]["prior_issued_ts"] == T(2, 12).isoformat()
    assert cell["change_absence"]["prior_basis_complete"] is False


# ═══════════════════════════════════════════════════════════════════════════
# R2 — spacing_comparable false is served, and so is the change
# ═══════════════════════════════════════════════════════════════════════════

def test_r2_spacing_not_comparable_is_served_with_the_change():
    day = D(2026, 10, 11)
    fc = [_fc(day, "GFS", T(3, 6), spacing=3)]
    delta = [_delta(day, "GFS", T(3, 6), T(2, 12), hdd=7.741293, cdd=0.0,
                    spacing=3, prior_spacing=6, comparable=False)]
    spread = [_spread(day, ["AIFS", "GFS", "IFS"], comparable=False)]
    d = _day(_board(fc, delta, spread), "2026-10-11")
    change = d["by_source"]["GFS"]["change"]
    assert change["hdd"] == 7.741293
    assert change["spacing_comparable"] is False
    assert change["prior_sample_spacing_hours"] == 6
    assert d["by_source"]["GFS"]["sample_spacing_hours"] == 3
    assert d["spread"]["spacing_comparable"] is False
    assert d["spread"]["hdd"] == 2.0


# ═══════════════════════════════════════════════════════════════════════════
# R3 — a period sum only when every day is present and complete
# ═══════════════════════════════════════════════════════════════════════════

def _run(src, issued, start, n, **kw):
    return [_fc(start + datetime.timedelta(days=i), src, issued, **kw) for i in range(n)]


def test_r3_full_period_sums_and_normal_sums_over_the_same_days():
    # Newest issuance 10-03 06Z = 10-02 23:00 PDT -> day 1 is 10-03.
    fc = _run("GFS", T(3, 6), D(2026, 10, 3), 15, hdd=2.0, cdd=0.5,
              hdd_norm=7.0, cdd_norm=0.25)
    b = _board(fc)
    p = _period(b, "d01_05")
    assert (p["from"], p["to"]) == ("2026-10-03", "2026-10-07")
    cell = p["by_source"]["GFS"]
    assert cell["complete"] is True
    assert (cell["days_present"], cell["days_required"]) == (5, 5)
    assert cell["hdd"] == 10.0 and cell["cdd"] == 2.5
    assert cell["hdd_normal"] == 35.0 and cell["cdd_normal"] == 1.25
    assert "period_rule" in b and "Pacific" in b["period_rule"]


def test_r3_incomplete_last_day_leaves_the_period_unsummed_with_its_count():
    fc = (_run("IFS", T(3, 0), D(2026, 10, 3), 14)
          + [_fc(D(2026, 10, 17), "IFS", T(3, 0), complete=False)])
    cell = _period(_board(fc), "d11_15")["by_source"]["IFS"]
    assert cell["complete"] is False
    assert cell["days_present"] == 4 and cell["days_required"] == 5
    assert cell["hdd"] is None and cell["cdd"] is None
    assert cell["hdd_normal"] is None and cell["cdd_normal"] is None


def test_r3_missing_day_leaves_the_period_unsummed():
    fc = [r for r in _run("AIFS", T(3, 0), D(2026, 10, 3), 15)
          if r["target_date"] != D(2026, 10, 9)]
    cell = _period(_board(fc), "d06_10")["by_source"]["AIFS"]
    assert cell["complete"] is False and cell["days_present"] == 4
    assert cell["hdd"] is None


def test_r3_a_day_from_an_older_issuance_does_not_count_toward_the_period():
    fc = _run("GFS", T(3, 6), D(2026, 10, 3), 4) + [
        _fc(D(2026, 10, 7), "GFS", T(2, 12))]
    cell = _period(_board(fc), "d01_05")["by_source"]["GFS"]
    assert cell["complete"] is False and cell["days_present"] == 4
    assert cell["issued_ts"] == T(3, 6).isoformat()


def test_r3_day_one_is_the_pacific_day_after_the_newest_issuance():
    # 10-03 18Z = 10-03 11:00 PDT -> day 1 is 10-04.
    fc = _run("GFS", T(3, 18), D(2026, 10, 3), 16)
    p = _period(_board(fc), "d01_05")
    assert (p["from"], p["to"]) == ("2026-10-04", "2026-10-08")


# ═══════════════════════════════════════════════════════════════════════════
# R4 — gridpoints_raw is "NWS", in one place
# ═══════════════════════════════════════════════════════════════════════════

def test_r4_gridpoints_raw_is_served_as_nws_and_keeps_its_stored_name():
    day = D(2026, 10, 4)
    fc = [_fc(day, "gridpoints_raw", datetime.datetime(2026, 10, 2, 18, 41, 9, tzinfo=UTC),
              complete=False, missing=PNW[:2] + PNW[3:]),
          _fc(day, "GFS", T(3, 6))]
    b = _board(fc)
    src = {s["source_product"]: s for s in b["regions"][0]["sources"]}
    assert src["gridpoints_raw"]["label"] == "NWS"
    assert src["GFS"]["label"] == "GFS"
    assert "gridpoints_raw" in _day(b, "2026-10-04")["by_source"]
    assert b["source_labels"] == {"gridpoints_raw": "NWS"}


def test_r4_no_other_code_path_maps_labels():
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = []
    for path in root.glob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"""(["'])NWS\1""", line):      # a "NWS" string literal
                hits.append((path.name, n))
    assert len(hits) == 1 and hits[0][0] == "degree_days.py", hits
    assert dd.SOURCE_LABELS == {"gridpoints_raw": "NWS"}


# ═══════════════════════════════════════════════════════════════════════════
# R5 — no normal is null, never a fill; the value still serves
# ═══════════════════════════════════════════════════════════════════════════

def test_r5_row_without_normal_serves_its_value_and_null_departures():
    fc = [_fc(D(2026, 10, 5), "GFS", T(3, 6), hdd=4.5, cdd=0.2,
              hdd_norm=None, cdd_norm=None)]
    d = _day(_board(fc), "2026-10-05")
    cell = d["by_source"]["GFS"]
    assert d["normal"] is None
    assert cell["hdd"] == 4.5 and cell["cdd"] == 0.2
    assert cell["hdd_normal"] is None and cell["cdd_normal"] is None
    assert cell["hdd_vs_norm"] is None and cell["cdd_vs_norm"] is None


def test_r5_view_shaped_incomplete_row_is_all_null_not_zero():
    fc = [_fc(D(2026, 10, 5), "IFS", T(3, 0), complete=False)]
    d = _day(_board(fc), "2026-10-05")
    cell = d["by_source"]["IFS"]
    assert d["normal"] is None
    for k in ("hdd", "cdd", "tavg_f", "hdd_vs_norm", "cdd_vs_norm"):
        assert cell[k] is None
    assert cell["basis_complete"] is False


def test_day_without_any_row_is_served_empty():
    d = _day(_board([]), "2026-10-05")
    assert d == {"target_date": "2026-10-05", "normal": None,
                 "by_source": {}, "spread": None}


def test_members_missing_is_who_no_cell_had():
    """A partial day lists every member missing in its own cell; that is not a
    region with no members. The NWS leg's newest issuance carries one."""
    fc = (_run("GFS", T(3, 6), D(2026, 10, 3), 3)
          + [_fc(D(2026, 10, 6), "GFS", T(3, 6), complete=False)]
          + [_fc(D(2026, 10, 3), "gridpoints_raw", T(2, 18), complete=False,
                 missing=["USW00024131", "USW00024157", "USW00024233"])])
    m = _board(fc)["regions"][0]["members"]
    assert m["expected"] == 4 and m["present"] == 4 and m["missing"] == []
    assert m["missing_by_source"]["GFS"] == []
    assert m["missing_by_source"]["gridpoints_raw"] == [
        "USW00024131", "USW00024157", "USW00024233"]


# ═══════════════════════════════════════════════════════════════════════════
# Route: fake pool that records statements and transactions
# ═══════════════════════════════════════════════════════════════════════════

_VECTORS = (
    [{"region": "pnw", "weighting": w, "station_id": s}
     for w in ("population", "load_share_365d") for s in PNW]
    + [{"region": "desert_sw", "weighting": w, "station_id": "USW00023183"}
       for w in ("population", "load_share_365d")]
    + [{"region": "socalgas_territory", "weighting": "population",
        "station_id": "USW00023174"}]
)


class _Cur:
    def __init__(self, pool):
        self._pool, self._rows = pool, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        if self._pool.fail:
            raise RuntimeError("boom")
        self._pool.statements.append((query, params, self._pool.in_tx))
        for needle, rows in self._pool.script:
            if needle in query:
                self._rows = rows
                return
        self._rows = []

    async def fetchall(self):
        return list(self._rows)


class _Tx:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        self._pool.in_tx = True
        return self

    async def __aexit__(self, *exc):
        self._pool.in_tx = False
        return False


class _Conn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _Cur(self._pool)

    def transaction(self):
        return _Tx(self._pool)


class _Pool:
    def __init__(self, script=(), fail=False):
        self.script, self.fail = list(script), fail
        self.statements, self.in_tx = [], False

    def connection(self):
        return _Conn(self)


def _script(fc=(), delta=(), spread=()):
    return [("FROM degree_day_region_weights", _VECTORS),
            ("FROM v_degree_days_model_delta", list(delta)),
            ("FROM v_degree_days_model_spread", list(spread)),
            ("FROM v_degree_days_region_forecast", list(fc))]


@pytest.fixture(autouse=True)
def _clear_caches():
    for c in main._DD_CACHES:
        c.clear()
    yield
    for c in main._DD_CACHES:
        c.clear()


URL = "/api/weather/dd/forecast/regions"


def test_route_serves_the_board_with_its_age_and_reads_each_view_once(monkeypatch):
    day = D(2026, 10, 5)
    pool = _Pool(_script([_fc(day, "GFS", T(3, 6))],
                         [_delta(day, "GFS", T(3, 6), T(2, 12))],
                         [_spread(day, ["GFS", "IFS"])]))
    monkeypatch.setattr(main, "_pool", pool)
    r = TestClient(main.app).get(URL, params={"region": "pnw", "from": "2026-10-03"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["weighting"] == "population" and body["days"] == 16
    assert [x["region"] for x in body["regions"]] == ["pnw"]
    assert body["cache"]["state"] == "miss" and body["cache"]["ttl_seconds"] == 300.0
    assert r.headers["Cache-Control"] == "max-age=300"
    assert r.headers["X-Cache"] == "miss"

    reads = [q for q, _, _ in pool.statements if "SET LOCAL" not in q]
    for view in ("v_degree_days_region_forecast", "v_degree_days_model_delta",
                 "v_degree_days_model_spread"):
        assert sum(f"FROM {view}" in q for q in reads) == 1, view
    # D-09-25-75: every read under a statement timeout, inside a transaction.
    timeouts = [q for q, _, tx in pool.statements if "statement_timeout" in q]
    assert len(timeouts) == len(reads) and all(
        tx for q, _, tx in pool.statements)

    r2 = TestClient(main.app).get(URL, params={"region": "pnw", "from": "2026-10-03"})
    assert r2.headers["X-Cache"] == "hit"


def test_route_defaults_to_every_region_the_weighting_has(monkeypatch):
    monkeypatch.setattr(main, "_pool", _Pool(_script()))
    c = TestClient(main.app)
    pop = c.get(URL).json()
    assert [x["region"] for x in pop["regions"]] == [
        "desert_sw", "pnw", "socalgas_territory"]
    load = c.get(URL, params={"weighting": "load_share_365d"}).json()
    assert [x["region"] for x in load["regions"]] == ["desert_sw", "pnw"]


def test_route_passes_the_window_to_every_read(monkeypatch):
    pool = _Pool(_script())
    monkeypatch.setattr(main, "_pool", pool)
    TestClient(main.app).get(URL, params={"from": "2026-10-03", "days": "5",
                                          "region": "pnw"})
    view_params = [p for q, p, _ in pool.statements if "FROM v_degree_days" in q]
    assert len(view_params) == 3
    for p in view_params:
        assert p == {"weighting": "population", "region": "pnw",
                     "from_date": D(2026, 10, 3), "to_date": D(2026, 10, 8)}


# ── R6 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("params, field", [
    ({"region": "atlantis"}, "region"),
    ({"region": "socalgas_territory", "weighting": "load_share_365d"}, "region"),
    ({"weighting": "gdp"}, "weighting"),
    ({"from": "2026-13-01"}, "from"),
    ({"from": "yesterday"}, "from"),
    ({"from": "2025-01-01"}, "from"),
    ({"days": "0"}, "days"),
    ({"days": "31"}, "days"),
    ({"days": "ten"}, "days"),
])
def test_r6_bad_params_are_400s_naming_the_field(monkeypatch, params, field):
    monkeypatch.setattr(main, "_pool", _Pool(_script()))
    r = TestClient(main.app).get(URL, params=params)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith(f"{field}:"), r.json()


def test_r6_caps_are_stated_in_the_400(monkeypatch):
    monkeypatch.setattr(main, "_pool", _Pool(_script()))
    c = TestClient(main.app)
    assert "cap 30" in c.get(URL, params={"days": "31"}).json()["detail"]
    assert "within 60 days" in c.get(URL, params={"from": "2025-01-01"}).json()["detail"]
    detail = c.get(URL, params={"region": "atlantis"}).json()["detail"]
    assert "pnw" in detail                       # names the vocabulary


def test_route_503s_when_the_db_is_down(monkeypatch):
    monkeypatch.setattr(main, "_pool", _Pool(fail=True))
    r = TestClient(main.app).get(URL)
    assert r.status_code == 503
    assert r.json()["detail"].startswith("db unavailable")


# ── R7 ──────────────────────────────────────────────────────────────────────

def test_r7_per_station_route_is_untouched():
    """The per-station SQL and handler are the same objects PR #96 shipped;
    test_weather_dd_ledger.py runs unchanged beside this file."""
    flat = " ".join(main._DD_FORECAST_SQL.split())
    assert ("PARTITION BY station_id, target_date, source_product "
            "ORDER BY issued_ts DESC") in flat
    paths = {r.path for r in main.app.routes}
    assert "/api/weather/dd/forecast" in paths
    assert "/api/weather/dd/forecast/regions" in paths
