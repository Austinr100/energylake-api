"""d091644 (D-09-25-167) — the vintages routes, spec §2.

  V1  arrays line up: t0 + i is the row's target_ts and lead0 + i its lead_h,
      on a 66-hour and a 240-hour issuance side by side; whole MW, half away
  V2  cal is null for an issuance its line does not cover, null at the hour
      where it covers some leads only, and equals the outlook route's
      calibrated value for the same issuance and hour
  V3  a hole inside an issuance is null and the array still lines up after it
  V4  oldest first, newest last; n trims from the old end; n above 120 refused
  V5  absence for an unknown area and for an area with no rows
  V6  the newest issuance's reg equals the outlook route's registry curve,
      hour for hour; and every banked outlook receipt's issuance inside the
      bank equals its vintage (the receipts are the vector)
  D1  degree days equal the region view's for the same (region, weighting,
      source, issued_ts, target_date); NWS issuances = folds
  D2  an incomplete day carries the view's value and its false

Three layers. Route tests run main.py against tests/test_solar_outlook.py's
fake pool. PG tests run the routes' own SQL, through main.py, against a real
Postgres (skipped where no `initdb` is installed). Bank tests run the routes
over production rows read on 2026-10-07 (tests/fixtures/vintages_d091644,
each issuance checked against an md5 Neon computed). The rehearsal is
docs/receipts/vintages-d091644/rehearse.py.
"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main
import solar_outlook as so
import vintages as vt
from test_solar_outlook import FakePool

UTC = timezone.utc
H = timedelta(hours=1)
A = datetime(2026, 10, 1, 12, tzinfo=UTC)      # a 66-hour backfill issuance
B = datetime(2026, 10, 3, 12, tzinfo=UTC)      # a 240-hour 6-hourly issuance
ROOT = pathlib.Path(__file__).resolve().parents[1]
BANK = ROOT / "tests" / "fixtures" / "vintages_d091644"
RECEIPTS = ROOT / "docs" / "receipts" / "vintages-d091644"

CACHES = ("_solar_vintages_cache", "_wind_vintages_cache", "_dd_region_vintages_cache",
          "_dd_region_vectors_cache", "_solar_outlook_cache", "_wind_outlook_cache")


@pytest.fixture(autouse=True)
def _cold_memos():
    caches = [getattr(main, c) for c in CACHES if hasattr(main, c)]
    for c in caches:
        c.clear()
    yield
    for c in caches:
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def get(client, monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    return client.get(path)


def ts(s):
    return datetime.fromisoformat(s)


# ── fixtures: a 66-hour and a 240-hour issuance side by side ────────────────

def band(lead):
    return ("h01_06" if lead <= 6 else "h07_24" if lead <= 24 else "h25_48" if lead <= 48
            else "h49_120" if lead <= 120 else "h121_240")


def gen_rows(init, last_lead, *, base, calibrated=False, drop=(), tech="solar_pv"):
    """VINTAGES_SQL's columns. Registry is x.5 on an EVEN whole part, so a
    banker's round (Python's round) lands one MW low and is caught."""
    out = []
    for lead in range(1, last_lead + 1):
        if lead in drop:
            continue
        reg = base + 2 * lead + 0.5
        cid = (10 if lead <= 6 else 11 if lead <= 24 else 12) if calibrated and lead <= 48 else None
        out.append({"init_ts": init, "target_ts": init + (lead - 1) * H, "lead_h": lead,
                    "registry_mw": reg,
                    "calibrated_mw": (reg * 0.9 if cid is not None else None),
                    "calibration_id": cid,
                    "method_version": "solar_pv_v1" if tech == "solar_pv" else "wind_v1"})
    return out


def lines():
    """10 fitted on 1-6; 11 fitted on 7-21 though its rows run to 24; 12 has
    no fitted leads (covers nothing, D-09-25-136 clause 3)."""
    base = {"area": "CISO", "intercept_mw": 0.0, "slope": 0.9, "fit_start": date(2026, 9, 4),
            "fit_end": date(2026, 10, 1), "n_hours": 300, "n_days": 28, "fitted_at": A,
            "method_version": "solar_pv_v1", "fit_rows": 300}
    return [{**base, "calibration_id": 10, "lead_band": "h01_06", "fit_lead_min": 1, "fit_lead_max": 6},
            {**base, "calibration_id": 11, "lead_band": "h07_24", "fit_lead_min": 7, "fit_lead_max": 21},
            {**base, "calibration_id": 12, "lead_band": "h25_48", "fit_lead_min": None, "fit_lead_max": None}]


def side_by_side(**kw):
    return gen_rows(A, 66, base=1000.0) + gen_rows(B, 240, base=2000.0, calibrated=True, **kw)


def vintage_pool(rows, ls=None):
    return FakePool([("SET LOCAL", []),
                     ("WITH RECURSIVE inits", rows),
                     ("FROM implied_gen_calibration", lines() if ls is None else ls)])


def half_away(x):
    """The rule, written out independently of vintages.whole_mw."""
    if x is None:
        return None
    d = Decimal(x)
    q = int(abs(d) + Decimal("0.5"))
    return q if d >= 0 else -q


def serve(client, monkeypatch, rows, qs="area_kind=ba&area=CISO", tech="solar", ls=None):
    r = get(client, monkeypatch, vintage_pool(rows, ls), f"/api/generation/{tech}/vintages?{qs}")
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# V1 — arrays line up
# ═══════════════════════════════════════════════════════════════════════════

def test_V1_66h_and_240h_issuances_line_up_hour_for_hour(client, monkeypatch):
    rows = side_by_side()
    body = serve(client, monkeypatch, rows)
    a, b = body["issuances"]
    assert (a["init"], len(a["reg"]), a["lead0"]) == (A.isoformat(), 66, 1)
    assert (b["init"], len(b["reg"]), b["lead0"]) == (B.isoformat(), 240, 1)
    by_init = {a["init"]: a, b["init"]: b}
    for r in rows:
        iss = by_init[r["init_ts"].isoformat()]
        i = r["lead_h"] - iss["lead0"]
        assert ts(iss["t0"]) + i * H == r["target_ts"]
        assert iss["reg"][i] == half_away(r["registry_mw"])


def test_V1_whole_mw_is_half_away_from_zero():
    assert [vt.whole_mw(x) for x in (2.5, 3.5, -2.5, 0.49999999999999994, 1e-9, None)] == \
        [3, 4, -3, 0, 0, None]
    assert vt.whole_mw(2000.5) == 2001          # round() would give 2000


def test_V1_body_carries_the_contract_keys_and_nothing_per_hour(client, monkeypatch):
    body = serve(client, monkeypatch, side_by_side())
    assert {k: body[k] for k in ("tech", "area_kind", "area", "model", "unit", "step_h")} == \
        {"tech": "solar_pv", "area_kind": "ba", "area": "CISO", "model": "gfs",
         "unit": "MW", "step_h": 1}
    assert set(body) == {"tech", "area_kind", "area", "model", "unit", "step_h",
                         "issuances", "absence", "cache"}
    assert set(body["issuances"][0]) == {"init", "t0", "lead0", "reg", "cal", "method_version"}
    assert all(isinstance(v, int) for v in body["issuances"][1]["reg"])


# ═══════════════════════════════════════════════════════════════════════════
# V2 — cal is the outlook's gate, per issuance and per hour
# ═══════════════════════════════════════════════════════════════════════════

def test_V2_cal_is_null_for_an_issuance_no_line_covers(client, monkeypatch):
    a, b = serve(client, monkeypatch, side_by_side())["issuances"]
    assert a["cal"] is None
    assert isinstance(b["cal"], list) and len(b["cal"]) == 240


def test_V2_cal_is_null_at_hours_its_line_was_not_fitted_on(client, monkeypatch):
    rows = side_by_side()
    b = serve(client, monkeypatch, rows)["issuances"][1]
    cal = {r["lead_h"]: r["calibrated_mw"] for r in rows if r["init_ts"] == B}
    for lead in range(1, 241):
        want = half_away(cal[lead]) if lead <= 21 else None   # 22-24 beyond fit; 25-48 no fit
        assert b["cal"][lead - 1] == want, lead


def outlook_hours(rows, prev_rows):
    prev = {r["target_ts"]: r for r in prev_rows}
    out = []
    for r in rows:
        p = prev.get(r["target_ts"])
        out.append({**r, "lead_band": band(r["lead_h"]), "weather_step_h": 1,
                    "weather_source": "hrrr_80m" if r["lead_h"] <= 48 else "gfs_100m",
                    "outage_mw_subtracted": 0.0, "cap_mw_subtracted": 0.0,
                    "scored_registry_mw": r["registry_mw"], "scored_mw_total": 1.0,
                    "ac_mw_total": 1.0, "n_sites": 1, "source_posted_ts": None,
                    "prev_registry_mw": p and p["registry_mw"],
                    "prev_calibrated_mw": p and p["calibrated_mw"],
                    "prev_calibration_id": p and p["calibration_id"],
                    "prev_lead_h": p and p["lead_h"],
                    "prev_method_version": p and p["method_version"]})
    return out


def outlook_body(client, monkeypatch, tech, init, prev, rows, prev_rows, ls):
    pool = FakePool([("SET LOCAL", []),
                     ("AS prev_init_ts", [{"init_ts": init, "prev_init_ts": prev}]),
                     ("AS prev_registry_mw", outlook_hours(rows, prev_rows)),
                     ("FROM implied_gen_scores", []), ("FROM implied_gen_calibration", ls),
                     ("FROM timeseries_values", []), ("FROM implied_gen_sites", []),
                     ("FROM implied_gen_wind_sites", [])])
    r = get(client, monkeypatch, pool,
            f"/api/generation/{tech}/outlook?area_kind=ba&area=CISO&init={init.isoformat()}"
            .replace("+", "%2B"))
    assert r.status_code == 200, r.text
    return r.json()


def assert_same_as_outlook(iss, outlook):
    """Hour for hour: reg is the outlook's registry_mw, cal its calibrated_mw."""
    assert iss["init"] == outlook["issuance"]["init_ts"]
    held = [v for v in iss["reg"] if v is not None]
    assert len(held) == len(outlook["hours"])            # the outlook serves no hole
    for h in outlook["hours"]:
        i = (ts(h["target_ts"]) - ts(iss["t0"])) // H
        assert iss["lead0"] + i == h["lead_h"]
        assert iss["reg"][i] == half_away(h["registry_mw"]), h["target_ts"]
        got = iss["cal"][i] if iss["cal"] is not None else None
        assert got == half_away(h["calibrated_mw"]), h["target_ts"]


def test_V2_cal_equals_the_outlook_routes_calibrated_value(client, monkeypatch):
    rows = side_by_side()
    b = serve(client, monkeypatch, rows)["issuances"][1]
    ob = outlook_body(client, monkeypatch, "solar", B, A,
                      [r for r in rows if r["init_ts"] == B],
                      [r for r in rows if r["init_ts"] == A], lines())
    assert any(h["calibrated_mw"] is not None for h in ob["hours"])
    assert_same_as_outlook(b, ob)


# ═══════════════════════════════════════════════════════════════════════════
# V3 — a hole is null, and the array lines up after it
# ═══════════════════════════════════════════════════════════════════════════

def test_V3_a_hole_is_null_and_the_array_lines_up_after_it(client, monkeypatch):
    rows = side_by_side(drop=(5, 30))
    b = serve(client, monkeypatch, rows)["issuances"][1]
    assert len(b["reg"]) == 240
    assert b["reg"][4] is None and b["cal"][4] is None
    assert b["reg"][29] is None and b["cal"][29] is None
    held = {r["lead_h"]: r for r in rows if r["init_ts"] == B}
    for lead in (4, 6, 29, 31, 240):
        assert b["reg"][lead - b["lead0"]] == half_away(held[lead]["registry_mw"])
    assert sum(v is None for v in b["reg"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# V4 — order, n
# ═══════════════════════════════════════════════════════════════════════════

def test_V4_oldest_first_newest_last(client, monkeypatch):
    rows = sorted(side_by_side(), key=lambda r: (r["init_ts"], r["target_ts"]))
    inits = [i["init"] for i in serve(client, monkeypatch, rows)["issuances"]]
    assert inits == [A.isoformat(), B.isoformat()]


def test_V4_n_reaches_the_read_default_28(client, monkeypatch):
    for qs, want in (("", 28), ("&n=4", 4), ("&n=120", 120)):
        main._solar_vintages_cache.clear()
        pool = vintage_pool(side_by_side())
        get(client, monkeypatch, pool, f"/api/generation/solar/vintages?area_kind=ba&area=CISO{qs}")
        reads = [p for q, p in pool.calls if "WITH RECURSIVE inits" in q]
        assert len(reads) == 1 and reads[0]["n"] == want


@pytest.mark.parametrize("n", ["121", "1000", "0", "-1", "abc", "2.5"])
def test_V4_n_out_of_range_is_refused(client, monkeypatch, n):
    for tech in ("solar", "wind"):
        pool = vintage_pool(side_by_side())
        r = get(client, monkeypatch, pool,
                f"/api/generation/{tech}/vintages?area_kind=ba&area=CISO&n={n}")
        assert r.status_code == 400, (tech, n, r.text)
        assert "n must be" in r.json()["detail"]
        assert not any("WITH RECURSIVE" in q for q, _p in pool.calls)


# ═══════════════════════════════════════════════════════════════════════════
# V5 — absence
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tech,qs", [("solar", "area_kind=ba&area=ZZZZ"),
                                     ("wind", "area_kind=ba&area=ZZZZ"),
                                     ("solar", "area_kind=state&area=WY")])
def test_V5_unknown_area_or_no_rows_is_an_absence_and_an_empty_list(client, monkeypatch, tech, qs):
    body = serve(client, monkeypatch, [], qs=qs, tech=tech)
    assert body["issuances"] == []
    assert body["absence"]["reason"] == "no_issuance"
    assert body["absence"]["detail"]


@pytest.mark.parametrize("qs", ["area_kind=zone&area=CISO", "area_kind=hub&area=XX",
                                "area_kind=ba", "area_kind=ba&area=ciso",
                                "area_kind=ba&area=CISO&model=ecmwf"])
def test_V5_bad_params_are_400(client, monkeypatch, qs):
    assert get(client, monkeypatch, vintage_pool([]),
               f"/api/generation/solar/vintages?{qs}").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# D-09-25-75 — statement timeout, one read, the memo
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tech,name", [("solar", "solar_pv"), ("wind", "wind")])
def test_one_read_after_the_statement_timeout_naming_one_area(client, monkeypatch, tech, name):
    pool = vintage_pool(side_by_side())
    get(client, monkeypatch, pool, f"/api/generation/{tech}/vintages?area_kind=ba&area=CISO")
    sql = pool.sql_run()
    assert sql[0].strip() == f"SET LOCAL statement_timeout = '{main.SOLAR_STATEMENT_TIMEOUT}'"
    assert main.SOLAR_STATEMENT_TIMEOUT == "5s"
    assert [q for q in sql if "implied_gen_area_hourly" in q] == [vt.VINTAGES_SQL]
    p = next(p for q, p in pool.calls if q == vt.VINTAGES_SQL)
    assert (p["tech"], p["area_kind"], p["area"]) == (name, "ba", "CISO")
    assert p["model"] == {"solar": "gfs", "wind": "hrrr_gfs"}[tech]
    # the gate's lines: the outlook's own statement, by id
    assert sql[-1] == so.CALIBRATION_SQL


def test_the_read_has_no_max_and_no_distinct_on():
    s = vt.VINTAGES_SQL.lower()
    assert "max(" not in s and "distinct" not in s
    assert s.count("order by i.init_ts desc") == 1 and s.count("order by p.init_ts desc") == 1


def test_memo_is_keyed_on_n(client, monkeypatch):
    pool = vintage_pool(side_by_side())
    monkeypatch.setattr(main, "_pool", pool)
    r1 = client.get("/api/generation/solar/vintages?area_kind=ba&area=CISO&n=4")
    r2 = client.get("/api/generation/solar/vintages?area_kind=ba&area=CISO&n=4")
    r3 = client.get("/api/generation/solar/vintages?area_kind=ba&area=CISO&n=12")
    assert [r.headers["X-Cache"] for r in (r1, r2, r3)] == ["miss", "hit", "miss"]
    assert sum("WITH RECURSIVE" in q for q in pool.sql_run()) == 2
    assert r1.json()["cache"]["state"] == "miss"


def test_db_down_is_503(client, monkeypatch):
    class Down:
        def connection(self):
            raise RuntimeError("connection refused")
    assert get(client, monkeypatch, Down(),
               "/api/generation/wind/vintages?area_kind=ba&area=CISO").status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# PG — the routes' own SQL, through main.py, against a real Postgres
# ═══════════════════════════════════════════════════════════════════════════

_DDL = """
CREATE TABLE implied_gen_area_hourly (
    tech text NOT NULL, area_kind text NOT NULL, area text NOT NULL, model text NOT NULL,
    init_ts timestamptz NOT NULL, target_ts timestamptz NOT NULL, lead_h smallint NOT NULL,
    lead_band text NOT NULL, weather_step_h smallint NOT NULL,
    registry_mw double precision NOT NULL, calibrated_mw double precision,
    calibration_id bigint, outage_mw_subtracted double precision NOT NULL,
    ac_mw_total double precision NOT NULL, n_sites integer NOT NULL,
    source_posted_ts timestamptz, method_version text NOT NULL,
    PRIMARY KEY (tech, area_kind, area, model, init_ts, target_ts));
CREATE TABLE implied_gen_calibration (
    calibration_id bigint PRIMARY KEY, tech text NOT NULL, area text NOT NULL,
    lead_band text NOT NULL, intercept_mw double precision, slope double precision,
    fit_start date, fit_end date, n_hours integer, n_days integer, fitted_at timestamptz,
    method_version text, fit_lead_min smallint, fit_lead_max smallint, fit_rows integer);
CREATE TABLE implied_gen_scores (
    tech text, area_kind text, area text, lead_band text, who text, window_start date,
    window_end date, n_hours integer, n_days integer, scored boolean, bias_mw float8,
    mae_mw float8, mae_pct_installed float8, rmse_mw float8, r float8, method_version text,
    scored_at timestamptz, lead_min smallint, lead_max smallint);
CREATE TABLE timeseries_values (ts timestamptz, dataset text, series text, value numeric);
CREATE TABLE implied_gen_sites (
    tech text, mount_basis text, dc_basis text, ac_mw numeric, hub text, ba_code text, state text);
"""

# Five 6-hourly gfs issuances (240 h) after one 66-hour one, and an ifs
# issuance between the two newest gfs ones. The newest gfs run has a hole.
PG_GFS = [A + timedelta(hours=6 * k) for k in range(6)]      # A is the 66-hour one
PG_IFS = PG_GFS[-1] - timedelta(hours=3)


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_vintages_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data, sock = os.path.join(d, "data"), d
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-w",
                            "-l", os.path.join(d, "pg.log"), "-o",
                            f"-k {sock} -c listen_addresses=''", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=60)
    conn = psycopg.connect(f"host={sock} user=postgres dbname=postgres", autocommit=True,
                           row_factory=psycopg.rows.dict_row)
    try:
        conn.execute(_DDL)
        _seed(conn)
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-m", "immediate",
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60)
        shutil.rmtree(d, ignore_errors=True)


def pg_rows():
    out = [("gfs", r) for r in gen_rows(A, 66, base=1000.0)]
    for k, init in enumerate(PG_GFS[1:], 1):
        drop = (30,) if init == PG_GFS[-1] else ()
        out += [("gfs", r) for r in gen_rows(init, 240, base=1000.0 * (k + 1),
                                             calibrated=True, drop=drop)]
    out += [("ifs", r) for r in gen_rows(PG_IFS, 240, base=9000.0)]
    return out


def _seed(conn):
    ins = ("INSERT INTO implied_gen_area_hourly (tech, area_kind, area, model, init_ts, "
           "target_ts, lead_h, lead_band, weather_step_h, registry_mw, calibrated_mw, "
           "calibration_id, outage_mw_subtracted, ac_mw_total, n_sites, method_version) "
           "VALUES ('solar_pv','ba','CISO',%s,%s,%s,%s,%s,1,%s,%s,%s,0,1000,10,%s)")
    with conn.cursor() as cur:
        cur.executemany(ins, [(m, r["init_ts"], r["target_ts"], r["lead_h"], band(r["lead_h"]),
                               r["registry_mw"], r["calibrated_mw"], r["calibration_id"],
                               r["method_version"]) for m, r in pg_rows()])
        cols = ("calibration_id", "lead_band", "intercept_mw", "slope", "fit_start", "fit_end",
                "n_hours", "n_days", "fitted_at", "method_version", "fit_lead_min",
                "fit_lead_max", "fit_rows")
        cur.executemany(
            f"INSERT INTO implied_gen_calibration (tech, area, {', '.join(cols)}) "
            f"VALUES ('solar_pv', 'CISO', {', '.join(['%s'] * len(cols))})",
            [tuple(l[c] for c in cols) for l in lines()])


class PgPool:
    """main._pool over a sync psycopg connection: the routes' own statements
    reach a real Postgres. SET LOCAL outside a transaction only warns."""

    def __init__(self, conn):
        self.conn, self.calls = conn, []

    def connection(self):
        pool = self

        class _Cur:
            async def __aenter__(self):
                self.c = pool.conn.cursor()
                return self

            async def __aexit__(self, *exc):
                self.c.close()
                return False

            async def execute(self, query, params=None):
                pool.calls.append((query, params))
                self.c.execute(query, params)

            async def fetchall(self):
                return self.c.fetchall()

            async def fetchone(self):
                return self.c.fetchone()

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

            def transaction(self):
                return _Ctx()

        return _Ctx()


def pg_get(client, monkeypatch, pg, path):
    r = get(client, monkeypatch, PgPool(pg), path)
    assert r.status_code == 200, r.text
    return r.json()


def test_PG_V4_n_trims_from_the_old_end_within_the_model(client, monkeypatch, pg):
    body = pg_get(client, monkeypatch, pg, "/api/generation/solar/vintages?area_kind=ba&area=CISO&n=3")
    assert [i["init"] for i in body["issuances"]] == [t.isoformat() for t in PG_GFS[-3:]]
    body = pg_get(client, monkeypatch, pg, "/api/generation/solar/vintages?area_kind=ba&area=CISO&n=120")
    inits = [i["init"] for i in body["issuances"]]
    assert inits == [t.isoformat() for t in PG_GFS]           # every gfs run, never the ifs one
    assert PG_IFS.isoformat() not in inits


def test_PG_V1_V3_arrays_line_up_over_the_real_read(client, monkeypatch, pg):
    body = pg_get(client, monkeypatch, pg, "/api/generation/solar/vintages?area_kind=ba&area=CISO")
    by_init = {i["init"]: i for i in body["issuances"]}
    assert [len(i["reg"]) for i in body["issuances"]] == [66, 240, 240, 240, 240, 240]
    for m, r in pg_rows():
        if m != "gfs":
            continue
        iss = by_init[r["init_ts"].isoformat()]
        i = r["lead_h"] - iss["lead0"]
        assert ts(iss["t0"]) + i * H == r["target_ts"]
        assert iss["reg"][i] == half_away(r["registry_mw"])
    assert by_init[PG_GFS[-1].isoformat()]["reg"][29] is None


def test_PG_V2_cal_equals_the_outlook_route_over_the_same_database(client, monkeypatch, pg):
    vint = pg_get(client, monkeypatch, pg, "/api/generation/solar/vintages?area_kind=ba&area=CISO")
    assert vint["issuances"][0]["cal"] is None                 # the 66-hour run has no line
    for iss in vint["issuances"][1:]:
        main._solar_outlook_cache.clear()
        ob = pg_get(client, monkeypatch, pg,
                    "/api/generation/solar/outlook?area_kind=ba&area=CISO&init="
                    + iss["init"].replace("+", "%2B"))
        assert sum(h["calibrated_mw"] is not None for h in ob["hours"]) == 21
        assert_same_as_outlook(iss, ob)


def test_PG_V5_an_area_with_no_rows_is_an_absence(client, monkeypatch, pg):
    body = pg_get(client, monkeypatch, pg, "/api/generation/solar/vintages?area_kind=ba&area=ZZZZ")
    assert body["issuances"] == [] and body["absence"]["reason"] == "no_issuance"


# ═══════════════════════════════════════════════════════════════════════════
# V6 — production (Neon, read 2026-10-07): the bank and the banked receipts
# ═══════════════════════════════════════════════════════════════════════════

MODELS = {"solar_pv": "gfs", "wind": "hrrr_gfs"}
ROUTE = {"solar_pv": "solar", "wind": "wind"}
OUTLOOK_RECEIPTS = {"solar_pv": ROOT / "docs/receipts/solar-outlook-api-d091568/sample_outlook_ciso.json",
                    "wind": ROOT / "docs/receipts/wind-outlook-api-d091590/sample_outlook_ciso.json"}


def load_bank(tech):
    """VINTAGES_SQL's rows for ba/CISO at n = 28, oldest first. The bank
    holds (lead_h, registry_mw, calibrated_mw, calibration_id) per hour, as
    Postgres printed them; target_ts = init_ts + (lead_h - 1) h, which holds
    for every CISO row on Neon (checked in SQL, docs/receipts/vintages-d091644)."""
    rows = []
    for line in reversed((BANK / f"manifest_{tech}.psv").read_text().splitlines()):
        k, init, n, mv, _nmv, md5, _len = line.split("|")
        text = (BANK / "raw" / tech / f"{int(k):02d}.json").read_text()
        assert hashlib.md5(text.encode()).hexdigest() == md5, (tech, k)
        init = ts(init)
        got = json.loads(text)
        assert len(got) == int(n)
        rows += [{"init_ts": init, "target_ts": init + (lead - 1) * H, "lead_h": lead,
                  "registry_mw": reg, "calibrated_mw": cal, "calibration_id": cid,
                  "method_version": mv} for lead, reg, cal, cid in got]
    return rows


def bank_lines(tech):
    out = []
    for l in json.loads((BANK / "lines_ciso.json").read_text()):
        if l["tech"] == tech:
            out.append({**l, "fit_start": date.fromisoformat(l["fit_start"]),
                        "fit_end": date.fromisoformat(l["fit_end"]),
                        "fitted_at": ts(l["fitted_at"])})
    return out


def bank_body(client, monkeypatch, tech):
    return serve(client, monkeypatch, load_bank(tech), tech=ROUTE[tech], ls=bank_lines(tech))


@pytest.mark.parametrize("tech", ["solar_pv", "wind"])
def test_V6_newest_reg_equals_the_outlook_routes_registry_curve(client, monkeypatch, tech):
    rows = load_bank(tech)
    body = bank_body(client, monkeypatch, tech)
    assert len(body["issuances"]) == 28
    newest, before = body["issuances"][-1], body["issuances"][-2]
    pick = lambda i: [r for r in rows if r["init_ts"].isoformat() == i["init"]]
    ob = outlook_body(client, monkeypatch, ROUTE[tech], ts(newest["init"]), ts(before["init"]),
                      pick(newest), pick(before), bank_lines(tech))
    assert len(ob["hours"]) == 240
    assert_same_as_outlook(newest, ob)


@pytest.mark.parametrize("tech", ["solar_pv", "wind"])
def test_V6_the_banked_outlook_receipt_is_its_vintage_hour_for_hour(client, monkeypatch, tech):
    """The outlook receipts (d091568 / d091590, re-banked by d091608) were
    served by the outlook route on their own day. Their issuance is inside
    this bank, so its vintage must be their curve, registry and calibrated."""
    receipt = json.loads(OUTLOOK_RECEIPTS[tech].read_text())
    body = bank_body(client, monkeypatch, tech)
    iss = next(i for i in body["issuances"] if i["init"] == receipt["issuance"]["init_ts"])
    assert sum(h["calibrated_mw"] is not None for h in receipt["hours"]) > 0
    assert_same_as_outlook(iss, receipt)


@pytest.mark.parametrize("tech,n240,n66", [("solar_pv", 16, 12), ("wind", 13, 15)])
def test_V6_bank_the_two_kinds_show_as_array_length(client, monkeypatch, tech, n240, n66):
    body = bank_body(client, monkeypatch, tech)
    lens = [len(i["reg"]) for i in body["issuances"]]
    assert lens == [66] * n66 + [240] * n240        # oldest first: the backfill, then the 6-hourly
    assert all(i["lead0"] == 1 for i in body["issuances"])
    assert all(i["cal"] is None or len(i["cal"]) == len(i["reg"]) for i in body["issuances"])


@pytest.mark.parametrize("tech", ["solar_pv", "wind"])
def test_V6_the_banked_body_is_what_the_route_serves(client, monkeypatch, tech):
    """The dashboard lane's vector (D-09-05-T): the banked body, cache block
    aside, is exactly what this route serves over the banked rows."""
    banked = json.loads((RECEIPTS / f"body_{ROUTE[tech]}_ciso_n28.json").read_text())
    body = bank_body(client, monkeypatch, tech)
    assert {k: v for k, v in body.items() if k != "cache"} == \
        {k: v for k, v in banked.items() if k != "cache"}


# ═══════════════════════════════════════════════════════════════════════════
# Degree days — D1, D2 and the route
# ═══════════════════════════════════════════════════════════════════════════

D = date


def dd_row(issued, target, hdd, cdd, *, complete=True, spacing=6):
    if not complete:                       # the view nulls the values (pantry 202)
        hdd = cdd = None
    return {"issued_ts": issued, "target_date": target, "hdd": hdd, "cdd": cdd,
            "basis_complete": complete, "sample_spacing_hours": spacing}


def dd_pool(by_source, vectors=None):
    vectors = vectors if vectors is not None else [
        {"region": "pnw", "weighting": "population", "station_id": "USW00024233"},
        {"region": "pnw", "weighting": "load_share_365d", "station_id": "USW00024233"},
        {"region": "socal", "weighting": "population", "station_id": "USW00023174"}]
    return FakePool([("SET LOCAL", []),
                     ("FROM degree_day_region_weights", vectors),
                     ("FROM v_degree_days_region_forecast",
                      lambda p: list(by_source.get(p["source"], [])))])


def dd_get(client, monkeypatch, pool, qs="region=pnw&weighting=population"):
    return get(client, monkeypatch, pool, f"/api/weather/dd/forecast/regions/vintages?{qs}")


def load_dd_bank():
    """DD_VINTAGES_SQL's rows for pnw / population, per source, as Neon
    printed them (numerics exact)."""
    out: dict = {}
    for i, line in enumerate((BANK / "manifest_dd_pnw_population.psv").read_text().splitlines(), 1):
        src, issued, n, md5, _len = line.split("|")
        text = (BANK / "raw" / "dd" / f"{i:02d}.json").read_text()
        assert hashlib.md5(text.encode()).hexdigest() == md5, (src, issued)
        got = json.loads(text, parse_float=Decimal)
        assert len(got) == int(n)
        out.setdefault(src, []).extend(
            dd_row(ts(issued), D.fromisoformat(t), hdd, cdd, complete=bc, spacing=sp)
            | {"hdd": hdd, "cdd": cdd}
            for t, hdd, cdd, bc, sp in got)
    return out


def dd01_ref(v):
    """0.01, half away from zero, written out independently."""
    if v is None:
        return None
    d = Decimal(v) * 100
    q = int(abs(d) + Decimal("0.5"))
    return float(Decimal(q if d >= 0 else -q) / 100)


def test_D1_values_equal_the_region_views_for_the_same_key(client, monkeypatch):
    bank = load_dd_bank()
    body = dd_get(client, monkeypatch, dd_pool(bank)).json()
    assert body["absence"] is None
    served = {s["source_product"]: s for s in body["sources"]}
    n = 0
    for src, rows in bank.items():
        iss = {i["issued_ts"]: i for i in served[src]["issuances"]}
        for r in rows:
            i = iss[r["issued_ts"].isoformat()]
            k = (r["target_date"] - D.fromisoformat(i["d0"])).days
            assert i["hdd"][k] == dd01_ref(r["hdd"]), (src, r)
            assert i["cdd"][k] == dd01_ref(r["cdd"]), (src, r)
            assert i["basis_complete"][k] is r["basis_complete"]
            assert i["sample_step_h"][k] == r["sample_spacing_hours"]
            n += 1
    assert n == sum(len(v) for v in bank.values())
    assert sum(len(i["hdd"]) for s in body["sources"] for i in s["issuances"]) == n   # no gaps held


def test_D1_nws_issuances_are_the_folds(client, monkeypatch):
    body = dd_get(client, monkeypatch, dd_pool(load_dd_bank())).json()
    nws = next(s for s in body["sources"] if s["source_product"] == "gridpoints_raw")
    assert nws["label"] == "NWS"
    folds = json.loads((BANK / "dd_folds_pnw_population.json").read_text())
    assert len(nws["issuances"]) == folds["fold_count"] == 5
    assert [i["issued_ts"] for i in nws["issuances"]] == folds["fold_issued_ts"]


def test_D1_oldest_first_per_source(client, monkeypatch):
    body = dd_get(client, monkeypatch, dd_pool(load_dd_bank())).json()
    assert [s["source_product"] for s in body["sources"]] == list(vt.DD_SOURCES)
    for s in body["sources"]:
        stamps = [i["issued_ts"] for i in s["issuances"]]
        assert stamps == sorted(stamps, key=ts) and len(stamps) >= 5


def test_D2_an_incomplete_day_keeps_the_views_value_and_its_false(client, monkeypatch):
    t0 = datetime(2026, 10, 6, 12, tzinfo=UTC)
    rows = [dd_row(t0, D(2026, 10, 6), Decimal("4.005"), Decimal("0.125"), complete=False),
            dd_row(t0, D(2026, 10, 7), Decimal("5.125"), Decimal("-0.125")),
            # 10-08 not held
            dd_row(t0, D(2026, 10, 9), Decimal("6.994999"), Decimal("0"), spacing=None),
            dd_row(t0, D(2026, 10, 10), None, None, complete=False)]
    body = dd_get(client, monkeypatch, dd_pool({"GFS": rows})).json()
    gfs = next(s for s in body["sources"] if s["source_product"] == "GFS")
    (i,) = gfs["issuances"]
    assert i["d0"] == "2026-10-06" and i["issued_ts"] == t0.isoformat()
    assert i["basis_complete"] == [False, True, None, True, False]
    assert i["hdd"] == [None, 5.13, None, 6.99, None]
    assert i["cdd"] == [None, -0.13, None, 0.0, None]
    assert i["sample_step_h"] == [6, 6, None, None, 6]      # the view's, complete or not


def test_dd_one_read_per_source_each_after_the_statement_timeout(client, monkeypatch):
    pool = dd_pool({})
    dd_get(client, monkeypatch, pool)
    reads = [(q, p) for q, p in pool.calls if "v_degree_days_region_forecast" in q]
    assert sorted(p["source"] for _q, p in reads) == sorted(vt.DD_SOURCES)
    assert all(p["region"] == "pnw" and p["weighting"] == "population" for _q, p in reads)
    assert all("source_product = %(source)s" in q and "region = %(region)s" in q for q, _p in reads)
    sets = [q for q, _p in pool.calls if "SET LOCAL statement_timeout" in q]
    assert len(sets) == len(reads) + 1                    # + the vectors read


def test_dd_nothing_held_is_an_absence(client, monkeypatch):
    body = dd_get(client, monkeypatch, dd_pool({})).json()
    assert body["absence"]["reason"] == "no_issuance"
    assert all(s["issuances"] == [] for s in body["sources"])
    assert body["cache"]["state"] == "miss"


@pytest.mark.parametrize("qs,field", [("weighting=population", "region"),
                                      ("region=atlantis", "region"),
                                      ("region=pnw&weighting=hdd_share", "weighting")])
def test_dd_bad_params_are_400s_naming_the_field(client, monkeypatch, qs, field):
    r = dd_get(client, monkeypatch, dd_pool({}), qs)
    assert r.status_code == 400 and r.json()["detail"].startswith(f"{field}:")


def test_dd_weighting_defaults_to_population_and_memo_keys_on_it(client, monkeypatch):
    pool = dd_pool({})
    monkeypatch.setattr(main, "_pool", pool)
    a = client.get("/api/weather/dd/forecast/regions/vintages?region=pnw")
    b = client.get("/api/weather/dd/forecast/regions/vintages?region=pnw&weighting=population")
    c = client.get("/api/weather/dd/forecast/regions/vintages?region=pnw&weighting=load_share_365d")
    assert a.json()["weighting"] == "population"
    assert [r.headers["X-Cache"] for r in (a, b, c)] == ["miss", "hit", "miss"]


def test_dd_banked_body_is_what_the_route_serves(client, monkeypatch):
    banked = json.loads((RECEIPTS / "body_dd_pnw_population.json").read_text())
    body = dd_get(client, monkeypatch, dd_pool(load_dd_bank())).json()
    assert {k: v for k, v in body.items() if k != "cache"} == \
        {k: v for k, v in banked.items() if k != "cache"}
