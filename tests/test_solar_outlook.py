"""d091568 (D-09-25-114) — the solar outlook API, spec §3.4 G1–G5.

  G1  a band with no score row, or a row with scored=false, is "not yet
      scored" — never another band's score, never an older window's
  G2  caiso_dam appears only for hub areas (hub, hub_sum)
  G3  previous_mw is the same target hour from the previous issuance of the
      SAME MODEL (the degree-day forecast's ranking mistake, pantry d091557)
  G4  an area with no calibration returns calibrated_mw null, every band's
      line null, and `unscaled` true
  G5  the label is spec §3.3 verbatim, on both routes

G6 (phone width, touch hover) is the page's, not this repo's.

Two layers. The route tests run main.py against a fake pool that answers by
substring (as tests/test_polled_routes_d091551.py does), so they pin the
Python. The PG tests run solar_outlook.py's SQL against a real Postgres built
from migration 264's tables, so they pin the SQL; they skip where no
`initdb` is installed. Reds: docs/receipts/solar-outlook-api-d091568/reds.txt.
"""

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import solar_outlook as so

UTC = timezone.utc
INIT = datetime(2026, 10, 1, 12, tzinfo=UTC)
PREV = datetime(2026, 9, 30, 12, tzinfo=UTC)
OLDER = datetime(2026, 9, 29, 12, tzinfo=UTC)

SPEC = pathlib.Path(__file__).resolve().parent / "fixtures" / "solar_outlook_d091568" / "spec_3_3_label.txt"


@pytest.fixture(autouse=True)
def _cold_solar_memos():
    main._solar_outlook_cache.clear()
    main._solar_sites_cache.clear()
    yield
    main._solar_outlook_cache.clear()
    main._solar_sites_cache.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


# ── the fake pool ───────────────────────────────────────────────────────────

class FakePool:
    """(needle, rows) routes, first match wins; records (sql, params)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def connection(self):
        pool = self

        class _Cur:
            rows = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query, params=None):
                pool.calls.append((query, params))
                self.rows = []
                for needle, rows in pool.routes:
                    if needle in query:
                        self.rows = rows(params) if callable(rows) else rows
                        break

            async def fetchall(self):
                return list(self.rows)

            async def fetchone(self):
                return self.rows[0] if self.rows else None

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

    def sql_run(self):
        return [q for q, _p in self.calls]


def hour_rows(n=30, *, calibrated=False, prev=True):
    out = []
    for k in range(1, n + 1):
        lead = k
        band = so.LEAD_BANDS[0] if lead <= 6 else so.LEAD_BANDS[1] if lead <= 24 else so.LEAD_BANDS[2]
        reg = 1000.0 + k
        out.append({
            "target_ts": INIT + timedelta(hours=k - 1), "lead_h": lead, "lead_band": band,
            "weather_step_h": 1, "registry_mw": reg,
            "calibrated_mw": round(reg * 0.8, 3) if calibrated else None,
            "calibration_id": (10 + so.LEAD_BANDS.index(band)) if calibrated else None,
            "outage_mw_subtracted": 5.0, "ac_mw_total": 24218.0, "n_sites": 1029,
            "source_posted_ts": INIT + timedelta(hours=4), "method_version": "solar_pv_v1",
            "prev_registry_mw": (reg - 100.0) if prev else None,
            "prev_calibrated_mw": (round((reg - 100.0) * 0.8, 3) if calibrated else None) if prev else None,
            "prev_method_version": "solar_pv_v1" if prev else None,
        })
    return out


def score_row(band, who, *, scored=True, n_days=20, mae=123.0, area_kind="hub"):
    return {"lead_band": band, "who": who, "area_kind": area_kind,
            "window_start": date(2026, 9, 4), "window_end": date(2026, 10, 1),
            "n_hours": n_days * 12, "n_days": n_days, "scored": scored,
            "bias_mw": 10.0, "mae_mw": mae, "mae_pct_installed": 2.5, "rmse_mw": 150.0,
            "r": 0.98, "scored_at": datetime(2026, 10, 2, 18, 53, tzinfo=UTC)}


def outlook_pool(*, hours=None, scores=(), lines=(), actuals=(), fleet=(), issuance=None):
    hours = hour_rows() if hours is None else hours
    iss = [issuance or {"init_ts": INIT, "prev_init_ts": PREV}] if hours else []
    return FakePool([
        ("SET LOCAL", []),
        ("AS prev_init_ts", iss),
        ("AS prev_registry_mw", hours),
        ("FROM implied_gen_scores", list(scores)),
        ("FROM implied_gen_calibration", list(lines)),
        ("FROM timeseries_values", list(actuals)),
        ("FROM implied_gen_sites", list(fleet)),
    ])


def get(client, monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    return r


# ═══════════════════════════════════════════════════════════════════════════
# G1 — "not yet scored", never another band's score
# ═══════════════════════════════════════════════════════════════════════════

def test_G1_band_with_no_row_is_not_yet_scored_and_borrows_nothing(client, monkeypatch):
    # Only h07_24 has a (scored) row. Every other band says so, in words.
    pool = outlook_pool(scores=[score_row("h07_24", "registry", mae=111.0)])
    r = get(client, monkeypatch, pool, "/api/generation/solar/outlook?area_kind=hub&area=SP15")
    assert r.status_code == 200, r.text
    s = r.json()["scores"]
    assert set(s) == set(so.SCORE_BANDS)
    assert s["h07_24"]["registry"]["mae_mw"] == 111.0
    for band in so.SCORE_BANDS:
        for who, v in s[band].items():
            if (band, who) != ("h07_24", "registry"):
                assert v == "not yet scored", (band, who, v)
    # and no number of h07_24's appears under any other band
    assert "111.0" not in str({b: v for b, v in s.items() if b != "h07_24"})


def test_G1_scored_false_is_not_yet_scored_with_its_counts(client, monkeypatch):
    pool = outlook_pool(scores=[
        score_row("h01_06", "registry", scored=False, n_days=9, mae=77.0),
        score_row("h07_24", "registry", scored=True, mae=111.0),
    ])
    r = get(client, monkeypatch, pool, "/api/generation/solar/outlook?area_kind=hub&area=SP15")
    body = r.json()
    assert body["scores"]["h01_06"]["registry"] == "not yet scored"
    assert body["score_progress"]["h01_06"]["registry"] == {
        "n_days": 9, "n_hours": 108, "window_end": "2026-10-01", "min_days": 14}
    assert body["score_progress"]["h25_48"]["registry"] is None      # no row at all
    assert "77.0" not in r.text                                       # its MAE is not served


def test_G1_the_score_read_is_one_limit_1_lateral_per_band_and_who():
    for kind in so.AREA_KINDS:
        sql = so.SCORES_SQL[kind]
        assert not re.search(r"DISTINCT\s+ON", sql, re.I)
        pairs = re.findall(r"\('(\w+)', '(\w+)'\)", sql)
        assert pairs == so.score_pairs(kind)
        lateral = re.search(r"LATERAL\s*\((.*)\)\s*AS s\s*$", sql, re.S).group(1)
        assert "s.lead_band = v.lead_band AND s.who = v.who" in lateral
        assert "s.area = %(area)s" in lateral and "s.area_kind = %(area_kind)s" in lateral
        assert "s.method_version = %(method_version)s" in lateral
        assert re.search(r"ORDER BY s\.window_end DESC\s+LIMIT 1\s*$", lateral.strip())


# ═══════════════════════════════════════════════════════════════════════════
# G2 — caiso_dam only for hub areas
# ═══════════════════════════════════════════════════════════════════════════

def _actual_rows(series_list, dataset, n=30, base=900.0):
    return [{"dataset": dataset, "series": s, "ts": INIT + timedelta(hours=k), "value": base + k}
            for s in series_list for k in range(n)]


@pytest.mark.parametrize("kind,area", [("hub", "SP15"), ("hub_sum", "HUBSUM")])
def test_G2_hub_areas_carry_caiso_dam(client, monkeypatch, kind, area):
    series = ["SP15:Solar"] if kind == "hub" else [f"{h}:Solar" for h in so.HUBS]
    actuals = (_actual_rows(series, "caiso_renewables_hourly")
               + _actual_rows(series, "caiso_renewables_fcst_dam", base=950.0))
    pool = outlook_pool(actuals=actuals, scores=[
        score_row(so.DAM, "caiso_dam", area_kind=kind, mae=200.0)])
    r = get(client, monkeypatch, pool,
            f"/api/generation/solar/outlook?area_kind={kind}&area={area}")
    body = r.json()
    assert len(body["caiso_dam"]) == 30 and len(body["actuals"]) == 30
    k = len(series)
    assert body["caiso_dam"][0]["mw"] == 950.0 * k and body["actuals"][0]["mw"] == 900.0 * k
    assert body["scores"][so.DAM]["caiso_dam"]["mae_mw"] == 200.0
    for band in so.LEAD_BANDS:                      # beside dam_comparable only
        assert "caiso_dam" not in body["scores"][band]


def test_G2_hub_sum_counts_only_hours_all_three_hubs_have(client, monkeypatch):
    rows = _actual_rows(["NP15:Solar", "ZP26:Solar", "SP15:Solar"], "caiso_renewables_hourly", n=5)
    rows = [r for r in rows if not (r["series"] == "ZP26:Solar" and r["ts"] == INIT)]
    pool = outlook_pool(actuals=rows)
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=hub_sum").json()
    assert [a["target_ts"] for a in body["actuals"]][0] == (INIT + timedelta(hours=1)).isoformat()


@pytest.mark.parametrize("kind,area", [("ba", "CISO"), ("ba", "PACE"), ("state", "CA")])
def test_G2_no_caiso_dam_outside_hub_areas_even_if_the_db_offers_one(client, monkeypatch, kind, area):
    # A hostile fake: it answers CAISO's forecast and a caiso_dam score row to
    # any read. Neither may reach the body of a BA or a state.
    pool = outlook_pool(
        actuals=_actual_rows(["SP15:Solar"], "caiso_renewables_fcst_dam"),
        scores=[score_row(so.DAM, "caiso_dam", area_kind=kind, mae=200.0)])
    r = get(client, monkeypatch, pool,
            f"/api/generation/solar/outlook?area_kind={kind}&area={area}")
    body = r.json()
    assert "caiso_dam" not in body
    assert all("caiso_dam" not in v for v in body["scores"].values())
    assert "caiso_dam" not in r.text
    # and the reads never ask for it
    for q in pool.sql_run():
        assert "caiso_renewables_fcst_dam" not in q and "'caiso_dam'" not in q


def test_G2_ciso_actual_is_the_fuel_mix_total_and_nothing_else():
    assert so.actual_pairs("ba", "CISO") == [("actual", "caiso_fuel_mix_hourly", "solar")]
    assert so.actual_pairs("ba", "PACE") == [] and so.actual_pairs("state", "CA") == []
    for (kind, _area), sql in so.ACTUALS_SQL.items():
        assert ("caiso_renewables_fcst_dam" in sql) == (kind in ("hub", "hub_sum"))
        assert "t.dataset = d.dataset AND t.series = d.series" in sql


# ═══════════════════════════════════════════════════════════════════════════
# G3 — previous_mw from the previous issuance of the same model
# ═══════════════════════════════════════════════════════════════════════════

def test_G3_previous_issuance_is_ranked_within_the_model():
    for sql in (so.ISSUANCE_NEWEST_SQL, so.ISSUANCE_AT_SQL):
        prev = re.search(r"\(SELECT p\.init_ts(.*?)LIMIT 1\)", sql, re.S).group(1)
        assert "p.model = %(model)s" in prev
        assert "p.area_kind = %(area_kind)s" in prev and "p.area = %(area)s" in prev
        assert "p.init_ts < cur.init_ts" in prev
        assert re.search(r"ORDER BY p\.init_ts DESC\s*$", prev.strip())
        assert not re.search(r"DISTINCT\s+ON|max\(", sql, re.I)
    join = re.search(r"LEFT JOIN implied_gen_area_hourly p(.*?)WHERE", so.HOURS_SQL, re.S).group(1)
    for col in ("tech", "area_kind", "area", "model", "target_ts"):
        assert f"p.{col} = c.{col}" in join
    assert "p.init_ts = %(prev_init)s" in join


def test_G3_route_serves_the_previous_hour_like_for_like(client, monkeypatch):
    pool = outlook_pool()
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()
    assert body["issuance"]["previous_init_ts"] == PREV.isoformat()
    h = body["hours"][0]
    assert h["previous_mw"] == h["registry_mw"] - 100.0 == h["previous_registry_mw"]
    # the hours read was pinned to the issuance and its predecessor, same model
    (_q, params), = [(q, p) for q, p in pool.calls if "AS prev_registry_mw" in q]
    assert params["init"] == INIT and params["prev_init"] == PREV and params["model"] == "gfs"


def test_G3_calibrated_hours_compare_calibrated_to_calibrated(client, monkeypatch):
    pool = outlook_pool(hours=hour_rows(calibrated=True))
    h = get(client, monkeypatch, pool,
            "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()["hours"][0]
    assert h["previous_mw"] == h["previous_calibrated_mw"] != h["previous_registry_mw"]


def test_G3_first_issuance_has_no_previous(client, monkeypatch):
    pool = outlook_pool(hours=hour_rows(prev=False),
                        issuance={"init_ts": INIT, "prev_init_ts": None})
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()
    assert body["issuance"]["previous_init_ts"] is None
    assert all(h["previous_mw"] is None for h in body["hours"])


# ═══════════════════════════════════════════════════════════════════════════
# G4 — no calibration: calibrated_mw null, the registry figure, "unscaled"
# ═══════════════════════════════════════════════════════════════════════════

def test_G4_no_calibration_is_null_and_unscaled(client, monkeypatch):
    pool = outlook_pool(hours=hour_rows(calibrated=False))
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=ba&area=PACE").json()
    assert body["unscaled"] is True
    assert all(h["calibrated_mw"] is None and h["registry_mw"] > 0 for h in body["hours"])
    assert body["calibration"] == {b: None for b in so.LEAD_BANDS}
    assert not any("implied_gen_calibration" in q for q in pool.sql_run())


def test_G4_a_line_the_rows_do_not_carry_is_not_reported(client, monkeypatch):
    # A line exists for h01_06 but no row carries its id: not this issuance's scaling.
    line = {"calibration_id": 10, "area": "SP15", "lead_band": "h01_06", "intercept_mw": 1.0,
            "slope": 0.85, "fit_start": date(2026, 9, 4), "fit_end": date(2026, 9, 30),
            "n_hours": 300, "n_days": 27, "fitted_at": INIT, "method_version": "solar_pv_v1"}
    pool = outlook_pool(hours=hour_rows(calibrated=False), lines=[line])
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()
    assert body["calibration"]["h01_06"] is None and body["unscaled"] is True
    # the route did not read it; and had it been read, the builder drops it
    assert so.build_calibration(hour_rows(calibrated=False), [line])["h01_06"] is None
    other = hour_rows(calibrated=True)
    assert so.build_calibration(other, [{**line, "calibration_id": 99}])["h01_06"] is None


def test_G4_calibrated_rows_name_their_line(client, monkeypatch):
    lines = [{"calibration_id": 10 + i, "area": "SP15", "lead_band": b, "intercept_mw": 1.0,
              "slope": 0.85, "fit_start": date(2026, 9, 4), "fit_end": date(2026, 9, 30),
              "n_hours": 300, "n_days": 27, "fitted_at": INIT, "method_version": "solar_pv_v1"}
             for i, b in enumerate(so.LEAD_BANDS)]
    pool = outlook_pool(hours=hour_rows(calibrated=True), lines=lines)
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()
    assert body["unscaled"] is False
    assert body["calibration"]["h07_24"]["slope"] == 0.85
    assert body["calibration"]["h07_24"]["calibration_id"] == 11
    assert body["calibration"]["h49_120"] is None          # no row in that band
    assert so.DAM not in body["calibration"]


# ═══════════════════════════════════════════════════════════════════════════
# G5 — the label, verbatim, on both routes
# ═══════════════════════════════════════════════════════════════════════════

def test_G5_label_is_the_spec_text_verbatim():
    assert so.LABEL == SPEC.read_text(encoding="utf-8").strip()


def test_G5_label_on_the_outlook(client, monkeypatch):
    body = get(client, monkeypatch, outlook_pool(),
               "/api/generation/solar/outlook?area_kind=hub&area=SP15").json()
    assert body["label"] == SPEC.read_text(encoding="utf-8").strip()


def test_G5_label_on_the_outlook_when_empty(client, monkeypatch):
    body = get(client, monkeypatch, outlook_pool(hours=[]),
               "/api/generation/solar/outlook?area_kind=state&area=CA").json()
    assert body["label"] == so.LABEL and body["absence"]["reason"] == "no_issuance"


def test_G5_label_on_the_sites(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("implied_gen_site_latest", []),
                     ("FROM implied_gen_sites", [])])
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/sites?day=2026-10-02").json()
    assert body["label"] == so.LABEL and body["absence"]["reason"] == "no_site_rows"


# ═══════════════════════════════════════════════════════════════════════════
# D-09-25-75 — memo, timeout, and the route's contract
# ═══════════════════════════════════════════════════════════════════════════

def test_reads_run_after_the_statement_timeout_and_are_memoised(client, monkeypatch):
    pool = outlook_pool()
    monkeypatch.setattr(main, "_pool", pool)
    a = client.get("/api/generation/solar/outlook?area_kind=hub&area=SP15")
    n = len(pool.calls)
    b = client.get("/api/generation/solar/outlook?area_kind=hub&area=SP15")
    assert a.headers["X-Cache"] == "miss" and b.headers["X-Cache"] == "hit"
    assert len(pool.calls) == n                                      # one build
    assert pool.calls[0][0] == f"SET LOCAL statement_timeout = '{main.SOLAR_STATEMENT_TIMEOUT}'"


@pytest.mark.parametrize("qs", [
    "area_kind=zone&area=X", "area_kind=hub&area=CISO", "area_kind=hub",
    "area_kind=ba&area=ciso", "area_kind=hub_sum&area=SP15",
    "area_kind=hub&area=SP15&model=ifs", "area_kind=hub&area=SP15&init=yesterday"])
def test_bad_params_are_400(client, monkeypatch, qs):
    monkeypatch.setattr(main, "_pool", outlook_pool())
    assert client.get(f"/api/generation/solar/outlook?{qs}").status_code == 400


def test_unbanked_init_is_404(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("AS prev_init_ts", [])])
    r = get(client, monkeypatch, pool,
            "/api/generation/solar/outlook?area_kind=hub&area=SP15&init=2026-01-01T00:00Z")
    assert r.status_code == 404


def test_db_down_is_503(client, monkeypatch):
    class Down:
        def connection(self):
            raise RuntimeError("connection refused")
    monkeypatch.setattr(main, "_pool", Down())
    assert client.get("/api/generation/solar/outlook?area_kind=hub&area=SP15").status_code == 503


@pytest.mark.parametrize("qs", ["", "target=2026-10-02T19:00Z&day=2026-10-02",
                                "target=2026-10-02T19:30Z", "day=10/02/2026"])
def test_sites_bad_params_are_400(client, monkeypatch, qs):
    monkeypatch.setattr(main, "_pool", FakePool([]))
    assert client.get(f"/api/generation/solar/sites?{qs}").status_code == 400


def _unit(code, gen, ac, hub="SP15", ba="CISO", state="CA", mount_basis="stated"):
    return {"plant_code": code, "generator_id": gen, "plant_name": f"Plant {code}",
            "latitude": 35.0, "longitude": -118.0, "ac_mw": ac, "dc_mw": ac * 1.26,
            "dc_basis": "class_default", "mount": "single_axis", "mount_basis": mount_basis,
            "tilt_deg": None, "tilt_basis": None, "azimuth_deg": 180.0,
            "azimuth_basis": "class_default", "bifacial": False,
            "hub": hub, "ba_code": ba, "state": state}


def test_sites_day_energy_per_plant_with_units(client, monkeypatch):
    lo, hi = so.pacific_day_bounds(date(2026, 10, 2))
    latest = [{"plant_code": 1, "init_ts": INIT, "target_ts": lo + timedelta(hours=k),
               "lead_h": 12 + k, "implied_mw": 10.0, "outage_mw_subtracted": 1.0,
               "method_version": "solar_pv_v1"} for k in range(24)]
    latest += [{**r, "plant_code": 2} for r in latest]
    units = [_unit(1, "A", 50.0), _unit(1, "B", 30.0), _unit(2, "A", 20.0, hub=None, ba="PACE", state="UT")]
    pool = FakePool([("SET LOCAL", []), ("implied_gen_site_latest", latest),
                     ("FROM implied_gen_sites", units)])
    body = get(client, monkeypatch, pool,
               "/api/generation/solar/sites?day=2026-10-02&area_kind=hub_sum").json()
    assert body["hours_expected"] == 24 and body["plant_count"] == 1
    p = body["plants"][0]
    assert p["ac_mw"] == 80.0 and p["implied_mwh"] == 240.0 and p["hours_covered"] == 24
    assert [u["generator_id"] for u in p["units"]] == ["A", "B"]
    assert p["units"][0]["dc_basis"] == "class_default"
    (_q, params), = [(q, pr) for q, pr in pool.calls if "implied_gen_site_latest" in q]
    assert params["lo"] == lo and params["hi"] == hi


# ═══════════════════════════════════════════════════════════════════════════
# The SQL itself, on a real Postgres (migration 264's tables)
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
    written_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tech, area_kind, area, model, init_ts, target_ts));
CREATE TABLE implied_gen_scores (
    tech text NOT NULL, area_kind text NOT NULL, area text NOT NULL, lead_band text NOT NULL,
    who text NOT NULL, window_start date, window_end date NOT NULL, n_hours integer NOT NULL,
    n_days integer NOT NULL, scored boolean NOT NULL, bias_mw double precision,
    mae_mw double precision, mae_pct_installed double precision, rmse_mw double precision,
    r double precision, method_version text NOT NULL, scored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tech, area, lead_band, who, window_end, method_version));
CREATE TABLE timeseries_values (
    ts timestamptz NOT NULL, dataset text NOT NULL, series text NOT NULL, value numeric,
    PRIMARY KEY (ts, dataset, series));
"""


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_solar_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data, sock = os.path.join(d, "data"), d
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    # -l, and no captured pipe: the postmaster inherits pg_ctl's stdout, and a
    # captured pipe it holds open never reaches EOF.
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
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(d, ignore_errors=True)


def _seed(conn):
    ins = ("INSERT INTO implied_gen_area_hourly (tech, area_kind, area, model, init_ts, target_ts, "
           "lead_h, lead_band, weather_step_h, registry_mw, outage_mw_subtracted, ac_mw_total, "
           "n_sites, method_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,0,1000,10,'solar_pv_v1')")
    rows = []
    # GFS issued at OLDER, PREV, INIT; a second model ("ifs") issued BETWEEN
    # PREV and INIT. Ranked without the model, IFS would be "previous".
    between = PREV + timedelta(hours=12)
    for model, init, base in (("gfs", OLDER, 100.0), ("gfs", PREV, 200.0),
                              ("ifs", between, 900.0), ("gfs", INIT, 300.0)):
        for k in range(1, 49):
            t = init + timedelta(hours=k - 1)
            band = "h01_06" if k <= 6 else "h07_24" if k <= 24 else "h25_48"
            for kind, area in (("hub", "SP15"), ("ba", "CISO")):
                rows.append(("solar_pv", kind, area, model, init, t, k, band, base + k))
    with conn.cursor() as cur:
        cur.executemany(ins, rows)
        sc = ("INSERT INTO implied_gen_scores (tech, area_kind, area, lead_band, who, window_start, "
              "window_end, n_hours, n_days, scored, mae_mw, method_version) "
              "VALUES ('solar_pv',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        cur.executemany(sc, [
            # h07_24 registry: an older SCORED window, then the newest UNSCORED one
            ("hub", "SP15", "h07_24", "registry", date(2026, 9, 1), date(2026, 9, 29), 300, 20, True, 50.0, "solar_pv_v1"),
            ("hub", "SP15", "h07_24", "registry", date(2026, 9, 25), date(2026, 10, 1), 60, 5, False, 99.0, "solar_pv_v1"),
            # h01_06 registry scored; h25_48 only under another method_version
            ("hub", "SP15", "h01_06", "registry", date(2026, 9, 4), date(2026, 10, 1), 300, 20, True, 40.0, "solar_pv_v1"),
            ("hub", "SP15", "h25_48", "registry", date(2026, 9, 4), date(2026, 10, 1), 300, 20, True, 41.0, "solar_pv_v0"),
            ("hub", "SP15", "dam_comparable", "caiso_dam", date(2026, 9, 4), date(2026, 10, 1), 300, 20, True, 60.0, "solar_pv_v1"),
        ])
        cur.executemany("INSERT INTO timeseries_values VALUES (%s,%s,%s,%s)", [
            (INIT + timedelta(hours=k), ds, se, 500 + k)
            for k in range(-5, 60)
            for ds, se in (("caiso_renewables_hourly", "SP15:Solar"),
                           ("caiso_renewables_fcst_dam", "SP15:Solar"),
                           ("caiso_renewables_hourly", "NP15:Solar"),
                           ("caiso_fuel_mix_hourly", "solar"))])


def _key(kind="hub", area="SP15", model="gfs"):
    return {"tech": "solar_pv", "area_kind": kind, "area": area, "model": model}


def test_PG_G3_previous_issuance_is_the_same_models(pg):
    iss = pg.execute(so.ISSUANCE_NEWEST_SQL, _key()).fetchone()
    assert iss == {"init_ts": INIT, "prev_init_ts": PREV}       # not the IFS run between
    at = pg.execute(so.ISSUANCE_AT_SQL, {**_key(), "init": PREV}).fetchone()
    assert at == {"init_ts": PREV, "prev_init_ts": OLDER}
    hours = pg.execute(so.HOURS_SQL, {**_key(), "init": INIT, "prev_init": PREV}).fetchall()
    assert len(hours) == 48
    for h in hours:
        lead_prev = h["lead_h"] + 24                              # PREV is 24 h earlier
        if lead_prev <= 48:
            assert h["prev_registry_mw"] == 200.0 + lead_prev
        else:
            assert h["prev_registry_mw"] is None                  # beyond PREV's horizon
    ifs = pg.execute(so.ISSUANCE_NEWEST_SQL, _key(model="ifs")).fetchone()
    assert ifs["prev_init_ts"] is None                           # IFS has no earlier run


def test_PG_G3_unbanked_init_returns_no_row(pg):
    assert pg.execute(so.ISSUANCE_AT_SQL, {**_key(), "init": INIT + timedelta(hours=6)}).fetchone() is None


def test_PG_G1_newest_row_per_band_and_who_only(pg):
    rows = pg.execute(so.SCORES_SQL["hub"], {"tech": "solar_pv", "area_kind": "hub",
                                             "area": "SP15", "method_version": "solar_pv_v1"}).fetchall()
    scores, progress = so.build_scores("hub", rows)
    assert scores["h01_06"]["registry"]["mae_mw"] == 40.0
    # newest h07_24 window is unscored: not yet scored, NOT the older 50.0
    assert scores["h07_24"]["registry"] == "not yet scored"
    assert progress["h07_24"]["registry"]["n_days"] == 5
    # h25_48's only row is another method_version's: not this issuance's score
    assert scores["h25_48"]["registry"] == "not yet scored"
    assert scores[so.DAM]["caiso_dam"]["mae_mw"] == 60.0


def test_PG_G2_actual_reads_name_their_series_and_window(pg):
    lo, hi = INIT, INIT + timedelta(hours=47)
    hub = pg.execute(so.ACTUALS_SQL[("hub", "SP15")], {"lo": lo, "hi": hi}).fetchall()
    assert {(r["dataset"], r["series"]) for r in hub} == {
        ("caiso_renewables_hourly", "SP15:Solar"), ("caiso_renewables_fcst_dam", "SP15:Solar")}
    assert min(r["ts"] for r in hub) == lo and max(r["ts"] for r in hub) == hi
    ciso = pg.execute(so.ACTUALS_SQL[("ba", "CISO")], {"lo": lo, "hi": hi}).fetchall()
    assert {(r["dataset"], r["series"]) for r in ciso} == {("caiso_fuel_mix_hourly", "solar")}
    actuals, dam = so.build_actuals("ba", "CISO", ciso)
    assert dam is None and len(actuals) == 48


# ═══════════════════════════════════════════════════════════════════════════
# P1 — production's rows (banked 2026-10-03), through the route
# ═══════════════════════════════════════════════════════════════════════════

def test_P1_production_hubsum_bank(client, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "solar_sample", pathlib.Path(__file__).resolve().parents[1]
        / "docs" / "receipts" / "solar-outlook-api-d091568" / "sample.py")
    sample = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sample)
    b = sample.load()
    body = get(client, monkeypatch, sample.pool(b),
               "/api/generation/solar/outlook?area_kind=hub_sum").json()
    assert body["issuance"]["init_ts"] == "2026-10-01T12:00:00+00:00"
    assert body["issuance"]["previous_init_ts"] == "2026-09-30T06:00:00+00:00"
    assert len(body["hours"]) == 66 and body["hours"][-1]["lead_h"] == 66
    # the backfill applied no line: every hour is the registry figure, unscaled,
    # although lines exist for HUBSUM (finding: handback §5.1)
    assert body["unscaled"] is True and len(b["lines_existing"]) == 4
    assert body["calibration"] == {band: None for band in so.LEAD_BANDS}
    # the previous issuance (06Z, 30 h earlier) reaches target hours up to lead 36 only
    assert [h["previous_mw"] is None for h in body["hours"]].index(True) == 36
    assert body["scores"]["h121_240"] == {"registry": "not yet scored",
                                          "calibrated": "not yet scored"}
    assert body["scores"][so.DAM]["caiso_dam"]["n_days"] == 22
    assert len(body["caiso_dam"]) == 66 and len(body["actuals"]) == 39
    assert body["fleet"]["registry_units"] == 0           # implied_gen_sites is empty
