"""d091673 — the Tropics API: storms, tracks by model run, and place odds,
read from the tropical bank (pantry d091664, migration 291).

  T0  the bank is Neon's: every file's sha-256 is the one Neon computed, cut
      at one instant
  T1  /storms active: three storms, each newest position the hand-read row
  T2  an empty bank (no storm in scope) is a 200 with `absence` and the
      newest heartbeat's time
  T3  a stale heartbeat reads STALE in `poll`, by ingestion_freshness's rule
  T4  /storm: the observed track in time order; the official forecast is the
      newest advisory by init_ts, with '008a', '008A' and '8' present; two
      storms sharing a name stay two storms
  T5  /tracks: oldest first, one series per source, n above 8 is 400, a
      source missing from a cycle is in absent[], a member row is refused by
      name
  T6  /odds: below_1pct carried and never 0; thresholds and windows as
      stored; STOP-P (no coordinates anywhere) stated and tested
  T7  an unknown source gets the unknown label, and SOURCES is the only
      place a label is made
  T8  404, 400 and 503
  T9  longitude is served signed for a constructed east-positive row
  T10 plans: every read on the points, odds and heartbeat tables uses an
      index, at the bank's size and at 31x; body bytes at n = 4 and 8
  C   the memo, the timeout, the cache block; R the rulings across bodies
  V   the banked bodies (the dashboard lane's vectors) are served byte for byte

Two layers. Route tests on the real bank run main.py's statements unchanged
on a local Postgres built from migration 291's tables with every row Neon
holds (load_bank_d091673); they skip by name where no initdb is installed.
Constructed cases run on test_solar_outlook's FakePool. Reds:
docs/receipts/tropics-api-d091673/red_on_main.txt and reds.txt.
"""

import ast
import asyncio
import gzip
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import tropics as tr
import load_bank_d091673 as lb
from test_solar_outlook import FakePool

UTC = timezone.utc
ROOT = pathlib.Path(__file__).resolve().parent.parent
BODIES = lb.FIX / "bodies"
P = "/api/weather/tropics"
STORMS = ("al092026", "ep182026", "ep202026")
CACHES = ("_tropics_storms_cache", "_tropics_storm_cache", "_tropics_tracks_cache",
          "_tropics_odds_cache")


@pytest.fixture(autouse=True)
def _cold_memos(monkeypatch):
    caches = [getattr(main, c) for c in CACHES if hasattr(main, c)]
    for c in caches:
        c.clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lb.NOW)
    yield
    for c in caches:
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def pg():
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")
    with lb.cluster() as conn:
        yield conn


def ts(s):
    return datetime.fromisoformat(s)


def pg_get(client, monkeypatch, pg, path, status=200):
    pool = lb.PgPool(pg)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, r.text
    return r.json()


def fake_get(client, monkeypatch, routes, path):
    pool = FakePool(routes)
    monkeypatch.setattr(main, "_pool", pool)
    return client.get(path), pool


def strip_cache(body):
    return {k: v for k, v in body.items() if k != "cache"}


# ── the bank, read in Python (no route code) ────────────────────────────────

_ROWS = {}


def bank(kind):
    if kind not in _ROWS:
        _ROWS[kind] = lb.rows(kind)
    return _ROWS[kind]


def bank_points(storm_id, source=None, init=None):
    return [r for r in bank("point") if r["storm_id"] == storm_id
            and (source is None or r["source"] == source)
            and (init is None or ts(r["init_ts"]) == init)]


def model_inits(storm_id):
    return sorted({ts(r["init_ts"]) for r in bank_points(storm_id)
                   if r["source"] not in ("best_track", "tcvitals", "nhc_official")})


# ═══════════════════════════════════════════════════════════════════════════
# T0 — the bank is Neon's
# ═══════════════════════════════════════════════════════════════════════════

def test_T0_every_bank_file_carries_the_sha_neon_computed():
    for kind, f in lb.MANIFEST["files"].items():
        assert len(lb.lines(kind)) == f["rows"], kind      # lb.text() raises on a sha mismatch


def test_T0_a_hand_edit_to_the_bank_is_red(monkeypatch, tmp_path):
    f = lb.MANIFEST["files"]["storm"]
    raw = gzip.decompress((lb.FIX / f["path"]).read_bytes()).decode().replace("Rachel", "Rachael")
    (tmp_path / "bank").mkdir()
    (tmp_path / f["path"]).write_bytes(gzip.compress(raw.encode()))
    monkeypatch.setattr(lb, "FIX", tmp_path)
    with pytest.raises(AssertionError, match="is not the one Neon computed"):
        lb.text("storm")


def test_T0_one_cut_for_every_table():
    cut = lb.CUT
    assert all(ts(r["inserted_ts"]) <= cut for r in bank("point"))
    assert all(ts(r["inserted_ts"]) <= cut for r in bank("odds"))
    assert all(ts(r["fetch_ts"]) <= cut for r in bank("heartbeat"))
    assert max(ts(s["updated_ts"]) for s in bank("storm")) == cut
    assert len(bank("point")) == 34518 and len(bank("odds")) == 2674


# ═══════════════════════════════════════════════════════════════════════════
# T1 — /storms active
# ═══════════════════════════════════════════════════════════════════════════

# Read by hand on Neon, 2026-10-09 22:1xZ, with the cut (handback §1.6).
HAND_READ = {
    "al092026": {"name": "Isaias", "newest": ("nhc_official", "013", "2026-10-09T21:00:00+00:00", 29.2, -87.0, 100.0, 959.0, "MH"),
                 "best_track": ("2026-10-09T18:00:00+00:00", 28.5, -87.1, 105.0, 959.0, "HU"),
                 "tcvitals": ("2026-10-09T18:00:00+00:00", 28.5, -87.1, 105.0, 959.0, "ISAIAS")},
    "ep182026": {"name": "Rachel", "newest": ("nhc_official", "051", "2026-10-09T21:00:00+00:00", 23.3, -124.0, 40.0, 995.0, "TS"),
                 "best_track": ("2026-10-09T18:00:00+00:00", 23.0, -124.5, 45.0, 995.0, "TS"),
                 "tcvitals": ("2026-10-09T18:00:00+00:00", 23.0, -124.5, 44.7, 995.0, "RACHEL")},
    "ep202026": {"name": "Simon", "newest": ("nhc_official", "010", "2026-10-09T21:00:00+00:00", 16.2, -104.7, 90.0, 966.0, "HU"),
                 "best_track": ("2026-10-09T18:00:00+00:00", 16.0, -104.7, 90.0, 966.0, "HU"),
                 "tcvitals": ("2026-10-09T18:00:00+00:00", 16.0, -104.7, 89.4, 966.0, "SIMON")},
}


def _pos(p):
    return (p["valid_ts"], p["lat_deg"], p["lon_deg"], p["vmax_kt"], p["mslp_hpa"], p["stage"])


def test_T1_storms_active_is_the_three_storms_with_the_hand_read_positions(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/storms")
    assert b["scope"] == "active" and b["count"] == 3 and b["absence"] is None
    assert [s["storm_id"] for s in b["storms"]] == list(STORMS)
    for s in b["storms"]:
        h = HAND_READ[s["storm_id"]]
        assert s["name"] == h["name"] and s["status"] == "active"
        src, adv, *pos = h["newest"]
        np_ = s["newest_position"]
        assert (np_["source"], np_["advisory"]) == (src, adv)
        assert _pos(np_) == tuple(pos)
        assert s["newest_advisory"]["advisory"] == adv
        by = {p["source"]: p for p in s["positions"]}
        assert _pos(by["best_track"]) == h["best_track"]
        assert _pos(by["tcvitals"]) == h["tcvitals"]
        assert [p["source"] for p in s["positions"]] == ["best_track", "nhc_official", "tcvitals"]


def test_T1_identity_fields_are_the_bank_row(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/storms")
    rows = {r["storm_id"]: r for r in bank("storm")}
    for s in b["storms"]:
        r = rows[s["storm_id"]]
        assert (s["basin"], s["number"], s["season"], s["status"]) == (
            r["basin"], r["number"], r["season"], r["status"])
        assert s["names"] == r["names"] and s["aliases"] == r["aliases"]
        assert s["first_seen_ts"] == r["first_seen"] and s["last_seen_ts"] == r["last_seen"]
        assert s["book_position"]["lat_deg"] == r["last_lat"]


def test_T1_scopes_recent_and_all_and_a_bad_scope(client, monkeypatch, pg):
    for scope in ("recent", "all"):
        b = pg_get(client, monkeypatch, pg, f"{P}/storms?scope={scope}")
        assert [s["storm_id"] for s in b["storms"]] == list(STORMS) and b["scope"] == scope
    r, pool = fake_get(client, monkeypatch, [], f"{P}/storms?scope=current")
    assert r.status_code == 400 and pool.calls == []


# ═══════════════════════════════════════════════════════════════════════════
# T2 / T3 — absence and the poll
# ═══════════════════════════════════════════════════════════════════════════

HB = datetime(2026, 10, 9, 21, 54, 46, 943472, tzinfo=UTC)


def test_T2_no_storm_in_scope_is_a_200_with_absence_and_the_heartbeat(client, monkeypatch, pg):
    with pg.transaction(force_rollback=True):
        pg.execute("UPDATE tropical_storms SET status = 'inactive'")
        b = pg_get(client, monkeypatch, pg, f"{P}/storms")
    assert b["storms"] == [] and b["count"] == 0
    assert b["absence"]["reason"] == "no_storms_in_scope"
    assert b["poll"]["newest_heartbeat_ts"] == HB.isoformat()
    assert HB.isoformat() in b["absence"]["detail"]
    assert b["poll"]["freshness"]["status"] == "FRESH"


def test_T2_an_empty_bank_answers_200_never_404(client, monkeypatch):
    routes = [(tr.POLL_SQL, [{"newest_heartbeat_ts": HB, "stale_after_override": timedelta(hours=2),
                              "lifecycle_status": "active"}]),
              (tr.STORMS_SQL, [])]
    r, pool = fake_get(client, monkeypatch, routes, f"{P}/storms")
    assert r.status_code == 200
    b = r.json()
    assert b["absence"]["reason"] == "no_storms_in_scope" and b["storms"] == []
    assert not any(tr.OFFICIAL_NEWEST_SQL in q for q in pool.sql_run())


def test_T2_no_heartbeat_at_all_is_missing(client, monkeypatch):
    routes = [(tr.POLL_SQL, [{"newest_heartbeat_ts": None, "stale_after_override": timedelta(hours=2),
                              "lifecycle_status": "active"}]), (tr.STORMS_SQL, [])]
    b = fake_get(client, monkeypatch, routes, f"{P}/storms")[0].json()
    assert b["poll"]["freshness"]["status"] == "MISSING"
    assert "no successful poll" in b["absence"]["detail"]


def test_T3_a_stale_heartbeat_reads_stale(client, monkeypatch, pg):
    monkeypatch.setattr(main, "_utcnow", lambda: HB + timedelta(hours=2, seconds=1))
    b = pg_get(client, monkeypatch, pg, f"{P}/storms")
    f = b["poll"]["freshness"]
    assert f["status"] == "STALE" and f["stale_after_h"] == 2.0 and f["age_s"] == 7201.0
    assert b["count"] == 3                                   # stale is stated, not a blank page


def test_T3_the_grade_is_the_views_case_at_its_edges():
    poll = {"newest_heartbeat_ts": HB, "stale_after_override": timedelta(hours=2)}
    assert tr.grade_poll(poll, HB + timedelta(hours=2))["freshness"]["status"] == "FRESH"
    assert tr.grade_poll(poll, HB + timedelta(hours=2, microseconds=1))["freshness"]["status"] == "STALE"
    assert tr.grade_poll({"newest_heartbeat_ts": None, "stale_after_override": None},
                         HB)["freshness"]["status"] == "MISSING"


def test_T3_the_grade_equals_the_views_own_at_the_witnessed_instant():
    w = json.loads((lb.FIX / "freshness_witness.json").read_text())
    row = next(r for r in w["rows"] if r["dataset_code"] == "nhc_storms_current")
    assert w["newest_heartbeat_by_index"] == row["frontier_ts"]     # the index read is the view's frontier
    stale_after = next(json.loads(l)["stale_after_override"] for l in lb.lines("dataset")
                       if '"nhc_storms_current"' in l)
    assert stale_after == "02:00:00" and row["stale_after_h"] == 2
    g = tr.grade_poll({"newest_heartbeat_ts": ts(row["frontier_ts"]),
                       "stale_after_override": timedelta(hours=2)}, ts(w["read_ts"]))
    assert g["freshness"]["status"] == row["status"] == "FRESH"
    assert "> stale_after THEN 'STALE'" in w["view_case"]


# ═══════════════════════════════════════════════════════════════════════════
# T4 — /storm
# ═══════════════════════════════════════════════════════════════════════════

def test_T4_the_observed_track_is_in_time_order_and_is_the_bank(client, monkeypatch, pg):
    for sid in STORMS:
        b = pg_get(client, monkeypatch, pg, f"{P}/storm?storm_id={sid}")
        for src in ("best_track", "tcvitals"):
            o = b["observed"][src]
            got = [p["valid_ts"] for p in o["points"]]
            assert got == sorted(got) and len(set(got)) == len(got)
            rows = sorted(bank_points(sid, src), key=lambda r: r["valid_ts"])
            assert [(p["valid_ts"], p["lat_deg"], p["lon_deg"], p["vmax_kt"], p["radii_nm"])
                    for p in o["points"]] == [
                (r["valid_ts"], r["lat"], float(r["lon"]), r["vmax_kt"] and float(r["vmax_kt"]),
                 r["radii"]) for r in rows]
            assert o["operational"] is (src == "tcvitals")


def test_T4_the_official_is_the_newest_advisory_by_init_ts_on_the_bank(client, monkeypatch, pg):
    want = {"al092026": "013", "ep182026": "051", "ep202026": "010"}
    for sid, adv in want.items():
        b = pg_get(client, monkeypatch, pg, f"{P}/storm?storm_id={sid}")
        o = b["official"]
        assert o["advisory"] == adv and o["init_ts"] == "2026-10-09T18:00:00+00:00"
        rows = [r for r in bank_points(sid, "nhc_official") if r["advisory"] == adv]
        assert [p["tau_h"] for p in o["points"]] == sorted(r["tau"] for r in rows)
        assert all("radii_nm" in p for p in o["points"])


def official_rows(spec):
    """(advisory, init hour on 10-09, taus) -> official rows for al092026."""
    out = []
    for adv, hh, taus in spec:
        init = datetime(2026, 10, 9, hh, tzinfo=UTC)
        for k, tau in enumerate(taus):
            out.append({"storm_id": "al092026", "advisory": adv, "init_ts": init, "tau": tau,
                        "valid_ts": init + timedelta(hours=tau), "lat": 20.0 + k, "lon": -80.0 - k,
                        "vmax_kt": 50.0, "mslp_hpa": None, "radii": {}, "stage": "TS"})
    return out


STORM_ROW = next(dict(r, names=r["names"]) for r in bank("storm") if r["storm_id"] == "al092026")


def _storm_row(sid="al092026", name="Isaias"):
    r = dict(STORM_ROW, storm_id=sid, basin=sid[:2], number=int(sid[2:4]))
    r["names"] = [{"name": name, "from": "2026-10-09T12:00:00+00:00", "to": "2026-10-09T21:00:00+00:00"}]
    for k in ("first_seen", "last_seen", "updated_ts"):
        r[k] = ts(r[k])
    return r


def test_T4_008a_008A_and_8_order_by_init_ts_never_by_the_string(client, monkeypatch):
    # '8' and '008A' share the 12Z synoptic time ('008A' is its intermediate,
    # own position at tau 6); '008a' is the 06Z advisory. Any string sort puts
    # '8' (or '008a') last; by init_ts and own position, '008A' is newest.
    rows = official_rows([("008a", 6, (6, 12, 24)), ("8", 12, (3, 12, 24)), ("008A", 12, (6, 12, 24))])
    assert max(r["advisory"] for r in rows) == "8"
    assert sorted({r["advisory"] for r in rows}, key=str.lower)[-1] == "8"
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.OBSERVED_SQL, []),
              (tr.OFFICIAL_NEWEST_SQL, rows), (tr.CYCLES_SQL, [])]
    r, _ = fake_get(client, monkeypatch, routes, f"{P}/storm?storm_id=al092026")
    o = r.json()["official"]
    assert o["advisory"] == "008A" and o["position_tau_h"] == 6
    assert o["position_valid_ts"] == "2026-10-09T18:00:00+00:00"
    assert o["same_init_advisories"] == ["8", "008A"]          # verbatim, in issue order


def test_T4_the_intermediate_on_the_bank_shares_its_parents_init_and_forecast():
    for sid, parent in (("al092026", "012"), ("ep182026", "050"), ("ep202026", "009")):
        p = {r["tau"]: r for r in bank_points(sid, "nhc_official") if r["advisory"] == parent}
        c = {r["tau"]: r for r in bank_points(sid, "nhc_official") if r["advisory"] == parent + "A"}
        assert {r["init_ts"] for r in p.values()} == {r["init_ts"] for r in c.values()}
        assert min(p) == 3 and min(c) == 6
        for tau in set(p) & set(c):
            assert (p[tau]["lat"], p[tau]["lon"], p[tau]["vmax_kt"], p[tau]["radii"]) == (
                c[tau]["lat"], c[tau]["lon"], c[tau]["vmax_kt"], c[tau]["radii"])
        assert set(c) - set(p) == {6} and set(p) - set(c) == {3}


def test_T4_two_storms_sharing_a_name_stay_two_storms(client, monkeypatch):
    a, b_ = _storm_row("al092026", "Isaias"), _storm_row("ep092026", "Isaias")
    obs = {"al092026": [{"source": "best_track", "init_ts": HB, "advisory": "", "tau": 0,
                         "valid_ts": HB, "lat": 28.5, "lon": -87.1, "vmax_kt": 105.0,
                         "mslp_hpa": 959.0, "radii": {}, "stage": "HU"}],
           "ep092026": [{"source": "best_track", "init_ts": HB, "advisory": "", "tau": 0,
                         "valid_ts": HB, "lat": 14.0, "lon": -110.0, "vmax_kt": 30.0,
                         "mslp_hpa": 1006.0, "radii": {}, "stage": "TD"}]}
    routes = [(tr.POLL_SQL, [{"newest_heartbeat_ts": HB, "stale_after_override": timedelta(hours=2),
                              "lifecycle_status": "active"}]),
              (tr.STORMS_SQL, [dict(a, bt_valid_ts=None, tv_valid_ts=None),
                               dict(b_, bt_valid_ts=None, tv_valid_ts=None)]),
              (tr.OFFICIAL_NEWEST_SQL, []),
              (tr.STORM_SQL, lambda p: [{"al092026": a, "ep092026": b_}[p["storm_id"]]]),
              (tr.OBSERVED_SQL, lambda p: obs[p["storm_id"]]),
              (tr.CYCLES_SQL, [])]
    s = fake_get(client, monkeypatch, routes, f"{P}/storms")[0].json()
    assert [(x["storm_id"], x["name"]) for x in s["storms"]] == [("al092026", "Isaias"),
                                                                 ("ep092026", "Isaias")]
    one = fake_get(client, monkeypatch, routes, f"{P}/storm?storm_id=ep092026")[0].json()
    assert one["storm"]["storm_id"] == "ep092026"
    assert [p["lat_deg"] for p in one["observed"]["best_track"]["points"]] == [14.0]


def test_T4_no_statement_finds_a_storm_by_name():
    for name in dir(tr):
        if name.endswith("_SQL"):
            sql = getattr(tr, name)
            where = sql.split("WHERE", 1)[1] if "WHERE" in sql else ""
            assert "name" not in where.lower(), name
            assert "->>" not in sql and "@>" not in sql, name


# ═══════════════════════════════════════════════════════════════════════════
# T5 — /tracks
# ═══════════════════════════════════════════════════════════════════════════

def test_T5_oldest_first_one_series_per_source_every_point_the_banks(client, monkeypatch, pg):
    for sid in STORMS:
        b = pg_get(client, monkeypatch, pg, f"{P}/tracks?storm_id={sid}")
        assert b["n"] == 4
        inits = [ts(c["init_ts"]) for c in b["cycles"]]
        assert inits == model_inits(sid)[-4:]                       # the newest 4, oldest first
        for c in b["cycles"]:
            srcs = [s["source"] for s in c["series"]]
            assert len(srcs) == len(set(srcs)) and srcs == sorted(srcs)
            for s in c["series"]:
                rows = sorted(bank_points(sid, s["source"], ts(c["init_ts"])), key=lambda r: r["tau"])
                assert [(p["tau_h"], p["valid_ts"], p["lat_deg"], p["lon_deg"], p["vmax_kt"],
                         p["mslp_hpa"]) for p in s["points"]] == [
                    (r["tau"], r["valid_ts"], r["lat"], float(r["lon"]),
                     None if r["vmax_kt"] is None else float(r["vmax_kt"]),
                     None if r["mslp_hpa"] is None else float(r["mslp_hpa"])) for r in rows]
                assert set(s["points"][0]) == {"tau_h", "valid_ts", "lat_deg", "lon_deg",
                                               "vmax_kt", "mslp_hpa"}


def test_T5_n_8_and_the_official_rides_with_its_cycle(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/tracks?storm_id=al092026&n=8")
    assert [ts(c["init_ts"]) for c in b["cycles"]] == model_inits("al092026")[-8:]
    by = {c["init_ts"]: c for c in b["cycles"]}
    assert [a["advisory"] for a in by["2026-10-09T12:00:00+00:00"]["official"]] == ["012", "012A"]
    assert [a["advisory"] for a in by["2026-10-09T18:00:00+00:00"]["official"]] == ["013"]
    assert by["2026-10-08T12:00:00+00:00"]["official"] == []
    assert by["2026-10-08T12:00:00+00:00"]["official_absence"]["reason"] == "no_official_at_this_init"


@pytest.mark.parametrize("n", ["9", "100", "0", "-1", "abc", "2.5"])
def test_T5_n_out_of_range_is_400_with_no_read(client, monkeypatch, n):
    r, pool = fake_get(client, monkeypatch, [], f"{P}/tracks?storm_id=ep182026&n={n}")
    assert r.status_code == 400 and pool.calls == []


def test_T5_a_source_missing_from_a_cycle_is_in_absent(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/tracks?storm_id=al092026&n=2")
    absent = {a["source"]: a for a in b["absent"]}
    avno = absent["atcf:AVNO"]
    assert avno["present_in"] == ["2026-10-09T12:00:00+00:00"]
    assert avno["missing_from"] == ["2026-10-09T18:00:00+00:00"] and avno["adeck"] == "late"
    newest = {s["source"] for s in b["cycles"][-1]["series"]}
    assert newest == {"atcf:CLP5", "atcf:HCCA", "atcf:IVCN", "atcf:OCD5", "atcf:RVCN",
                      "atcf:TCLP", "atcf:TVCN"}                       # the early aids only
    assert all(tr.source_meta(s)["adeck"] == "early" for s in newest)
    every = {s["source"] for c in b["cycles"] for s in c["series"]}
    assert set(absent) == every - newest


def _track_rows(sources, inits=(datetime(2026, 10, 9, 6, tzinfo=UTC), datetime(2026, 10, 9, 12, tzinfo=UTC))):
    out = []
    for init in inits:
        for src in sources:
            for tau in (0, 12):
                out.append({"source": src, "init_ts": init, "advisory": "", "tau": tau,
                            "valid_ts": init + timedelta(hours=tau), "lat": 20.0, "lon": -90.0,
                            "vmax_kt": 50.0, "mslp_hpa": 990.0, "stage": None})
    return out


def test_T5_a_member_row_is_refused_by_name(client, monkeypatch):
    rows = _track_rows(["atcf:AVNO", "ecmwf_ens", "atcf:AP01", "ecmwf_aifs_ens", "atcf:AC00"])
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.TRACKS_SQL, rows)]
    b = fake_get(client, monkeypatch, routes, f"{P}/tracks?storm_id=al092026")[0].json()
    served = {s["source"] for c in b["cycles"] for s in c["series"]}
    assert served == {"atcf:AVNO"}
    assert {x["source"] for x in b["refused"]} == {"ecmwf_ens", "atcf:AP01", "ecmwf_aifs_ens", "atcf:AC00"}
    assert all(x["reason"] == "ensemble_member" for x in b["refused"])
    assert "ensemble_members" in b["not_served"]


def test_T5_the_bank_itself_refuses_a_member_row(pg):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation, match="ttp_official_or_deterministic"):
        with pg.transaction(force_rollback=True):
            pg.execute("INSERT INTO tropical_track_points (storm_id, source, init_ts, tau, valid_ts, "
                       "lat, lon) VALUES ('ep182026', 'ecmwf_ens', '2026-10-09 12:00+00', 0, "
                       "'2026-10-09 12:00+00', 23, -124)")


def test_T5_a_storm_with_no_model_cycle_is_a_200_with_absence(client, monkeypatch):
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.TRACKS_SQL, [])]
    r, _ = fake_get(client, monkeypatch, routes, f"{P}/tracks?storm_id=al092026")
    assert r.status_code == 200
    assert r.json()["absence"]["reason"] == "no_model_cycle" and r.json()["cycles"] == []


# ═══════════════════════════════════════════════════════════════════════════
# T6 — /odds
# ═══════════════════════════════════════════════════════════════════════════

def newest_odds(sid):
    rows = [r for r in bank("odds") if r["storm_id"] == sid and r["source"] == "nhc_pws"]
    newest = max(ts(r["issued_ts"]) for r in rows)
    return sorted((r for r in rows if ts(r["issued_ts"]) == newest), key=lambda r: r["odds_id"])


def test_T6_below_1pct_is_carried_and_never_zero(client, monkeypatch, pg):
    for sid in STORMS:
        b = pg_get(client, monkeypatch, pg, f"{P}/odds?storm_id={sid}")
        rows = newest_odds(sid)
        cells = {}
        for p in b["places"]:
            for t in p["thresholds"]:
                for w in t["windows"]:
                    for kind in ("cumulative", "onset"):
                        cells[(p["place_id"], t["threshold_kt"], w["window_h"], kind)] = w[kind]
        assert len(cells) == len(rows)
        for r in rows:
            c = cells[(r["place_id"], r["threshold_kt"], r["window_h"], r["kind"])]
            assert c["below_1pct"] is r["below_1pct"] and c["scored"] is r["scored"] is False
            if r["below_1pct"]:
                assert r["value"] == 0 and c["value_pct"] is None      # the bank's 0 is a placeholder
            else:
                assert c["value_pct"] == r["value"]
        assert sum(c["below_1pct"] for c in cells.values()) == sum(r["below_1pct"] for r in rows) > 0
        assert b["issuance"]["advisory"] == rows[0]["advisory"]
        assert b["issuance"]["issued_ts"] == rows[0]["issued_ts"]


def test_T6_thresholds_windows_and_place_order_as_stored(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/odds?storm_id=al092026")
    rows = newest_odds("al092026")
    order = list(dict.fromkeys(r["place_id"] for r in rows))
    assert [p["place_id"] for p in b["places"]] == order                 # NHC's printed order
    for p in b["places"]:
        mine = [r for r in rows if r["place_id"] == p["place_id"]]
        assert [t["threshold_kt"] for t in p["thresholds"]] == sorted({r["threshold_kt"] for r in mine})
        for t in p["thresholds"]:
            assert [w["window_h"] for w in t["windows"]] == sorted(
                {r["window_h"] for r in mine if r["threshold_kt"] == t["threshold_kt"]})
            assert t["radius_km"] is None
        held = {t["threshold_kt"] for t in p["thresholds"]}
        assert {a["threshold_kt"] for a in p["thresholds_absent"]} == {34, 50, 64} - held


def test_T6_stop_p_no_place_has_coordinates_and_the_payload_says_so(client, monkeypatch, pg):
    b = pg_get(client, monkeypatch, pg, f"{P}/odds?storm_id=ep182026")
    assert b["places"] and all(p["coordinates"] is None for p in b["places"])
    pc = b["place_coordinates"]
    assert pc["served"] is False and "no gazetteer" in pc["stop_p"]
    assert "tropical_pws_places" in pc["pantry_would_bank"]
    assert "place_coordinates" in b["not_served"]
    assert "YUMA AZ" in {p["place_id"] for p in b["places"]}              # NHC's name, as stored
    places = {r["place_id"] for r in bank("odds")}
    src = (ROOT / "tropics.py").read_text()                             # no embedded gazetteer:
    assert {x for x in places if x in src} <= {"PANAMA CITY FL", "GFAM 290N 850W"}   # prose examples only
    assert not {x for x in places if x in (ROOT / "main.py").read_text()}


def test_T6_a_storm_with_no_odds_is_a_200_with_absence(client, monkeypatch):
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.ODDS_SQL, [])]
    r, _ = fake_get(client, monkeypatch, routes, f"{P}/odds?storm_id=al092026")
    assert r.status_code == 200 and r.json()["absence"]["reason"] == "no_place_odds"


# ═══════════════════════════════════════════════════════════════════════════
# T7 — one label map
# ═══════════════════════════════════════════════════════════════════════════

def test_T7_an_unknown_source_is_served_with_the_unknown_label(client, monkeypatch):
    rows = _track_rows(["atcf:AVNO", "atcf:ZZZ9"])
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.TRACKS_SQL, rows)]
    b = fake_get(client, monkeypatch, routes, f"{P}/tracks?storm_id=al092026")[0].json()
    s = {x["source"]: x for x in b["cycles"][0]["series"]}
    assert s["atcf:ZZZ9"]["label"] == tr.UNKNOWN_SOURCE_LABEL and s["atcf:ZZZ9"]["status"] == "unknown"
    assert s["atcf:ZZZ9"]["role"] == "unknown" and s["atcf:ZZZ9"]["points"]
    assert s["atcf:AVNO"]["label"] == "GFS (A-deck AVNO)" and s["atcf:AVNO"]["status"] == "known"


def test_T7_every_bank_source_is_known_and_labels_are_distinct():
    held = {r["source"] for r in bank("point")}
    assert held <= set(tr.SOURCES)
    labels = [m["label"] for m in tr.SOURCES.values()]
    assert len(labels) == len(set(labels))
    assert len(tr.SOURCES) == 28                                         # migration 291's CHECK list


def test_T7_sources_is_read_only_inside_source_meta():
    tree = ast.parse((ROOT / "tropics.py").read_text())
    users = set()
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            if any(isinstance(n, ast.Name) and n.id == "SOURCES" for n in ast.walk(fn)):
                users.add(fn.name)
    assert users == {"source_meta"}
    main_src = (ROOT / "main.py").read_text()
    assert "_tr.SOURCES" not in main_src
    for m in tr.SOURCES.values():                                        # no label worded twice
        assert main_src.count(m["label"]) == 0
        assert (ROOT / "tropics.py").read_text().count(f'"{m["label"]}"') == 1


# ═══════════════════════════════════════════════════════════════════════════
# T8 — 404, 400, 503
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("route", ["storm", "tracks", "odds"])
def test_T8_an_unknown_storm_is_404(client, monkeypatch, pg, route):
    b = pg_get(client, monkeypatch, pg, f"{P}/{route}?storm_id=al012026", status=404)
    assert b["detail"] == "no storm with storm_id=al012026 in the tropical bank"


@pytest.mark.parametrize("q", ["", "?storm_id=", "?storm_id=AL092026", "?storm_id=al09", "?storm_id=wp012026",
                               "?storm_id=al092026%27--"])
@pytest.mark.parametrize("route", ["storm", "tracks", "odds"])
def test_T8_a_bad_storm_id_is_400_with_no_read(client, monkeypatch, route, q):
    r, pool = fake_get(client, monkeypatch, [], f"{P}/{route}{q}")
    assert r.status_code == 400 and pool.calls == []


class _DeadPool:
    def connection(self):
        raise OSError("connection refused")


@pytest.mark.parametrize("path", [f"{P}/storms", f"{P}/storm?storm_id=al092026",
                                  f"{P}/tracks?storm_id=al092026", f"{P}/odds?storm_id=al092026"])
def test_T8_db_down_is_503(client, monkeypatch, path):
    monkeypatch.setattr(main, "_pool", _DeadPool())
    r = client.get(path)
    assert r.status_code == 503 and r.json()["detail"].startswith("db unavailable")


def test_T8_a_statement_timeout_is_503_and_not_memoised(client, monkeypatch):
    import psycopg

    def cancel(_p):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.TRACKS_SQL, cancel)]
    r, _ = fake_get(client, monkeypatch, routes, f"{P}/tracks?storm_id=al092026")
    assert r.status_code == 503 and "statement timeout" in r.json()["detail"]
    assert main._tropics_tracks_cache._entries == {}


# ═══════════════════════════════════════════════════════════════════════════
# T9 — longitude
# ═══════════════════════════════════════════════════════════════════════════

def test_T9_signed_lon_rewrites_only_east_positive_values():
    assert tr.signed_lon(200.5) == -159.5 and tr.signed_lon(360) == 0.0
    assert tr.signed_lon(190.3) == -169.7                               # no float residue
    assert tr.signed_lon(180) == 180.0 and tr.signed_lon(-180) == -180.0
    assert tr.signed_lon(-87.1) == -87.1 and tr.signed_lon(3.2) == 3.2 and tr.signed_lon(None) is None


def test_T9_a_constructed_east_positive_row_is_served_signed(client, monkeypatch, pg):
    with pg.transaction(force_rollback=True):
        pg.execute("UPDATE tropical_track_points SET lon = lon + 360 WHERE storm_id = 'ep182026' "
                   "AND source = 'best_track' AND init_ts = '2026-10-09 18:00+00'")
        pg.execute("UPDATE tropical_storms SET last_lon = last_lon + 360 WHERE storm_id = 'ep182026'")
        assert pg.execute("SELECT lon FROM tropical_track_points WHERE storm_id = 'ep182026' AND "
                          "source = 'best_track' AND init_ts = '2026-10-09 18:00+00'").fetchone()["lon"] == 235.5
        b = pg_get(client, monkeypatch, pg, f"{P}/storm?storm_id=ep182026")
    last = b["observed"]["best_track"]["points"][-1]
    assert last["valid_ts"] == "2026-10-09T18:00:00+00:00" and last["lon_deg"] == -124.5
    assert b["storm"]["book_position"]["lon_deg"] == -124.0
    assert "-180..180" in b["longitude"]["served"] and "negative west" in b["longitude"]["bank_stores"]


def test_T9_the_bank_stores_signed_lon_today():
    lons = [r["lon"] for r in bank("point")]
    assert min(lons) == -143.1 and max(lons) == 3.2 and not [x for x in lons if x > 180]


# ═══════════════════════════════════════════════════════════════════════════
# T10 — plans and bytes
# ═══════════════════════════════════════════════════════════════════════════

def route_statements(sid="ep182026", n=8):
    return [("POLL_SQL", tr.POLL_SQL, None),
            ("STORMS_SQL", tr.STORMS_SQL, {"scope": "active", "recent_days": 7}),
            ("OFFICIAL_NEWEST_SQL", tr.OFFICIAL_NEWEST_SQL, {"storm_ids": list(STORMS)}),
            ("STORM_SQL", tr.STORM_SQL, {"storm_id": sid}),
            ("OBSERVED_SQL", tr.OBSERVED_SQL, {"storm_id": sid}),
            ("CYCLES_SQL", tr.CYCLES_SQL, {"storm_id": sid}),
            ("TRACKS_SQL", tr.TRACKS_SQL, {"storm_id": sid, "n": n}),
            ("ODDS_SQL", tr.ODDS_SQL, {"storm_id": "al092026"})]


def plan_nodes(conn, sql, params):
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql, params).fetchone()["QUERY PLAN"][0]["Plan"]
    out = []

    def walk(n):
        out.append((n["Node Type"], n.get("Relation Name"), n.get("Index Name")))
        for c in n.get("Plans", []):
            walk(c)
    walk(plan)
    return out


BIG = ("tropical_track_points", "tropical_place_odds", "tropical_file_vintage")


def assert_indexed(conn):
    for name, sql, params in route_statements():
        nodes = plan_nodes(conn, sql, params)
        seq = [rel for typ, rel, _ in nodes if typ == "Seq Scan" and rel in BIG]
        assert not seq, (name, nodes)
        assert any(idx for _t, rel, idx in nodes if rel in BIG) or name in ("STORM_SQL",), (name, nodes)


def test_T10_every_read_uses_an_index_at_the_banks_size(pg):
    assert_indexed(pg)


@pytest.fixture(scope="module")
def pg_scaled():
    """The bank and 30 copies of it under other seasons' ids (1.07 M points):
    the size the d091671 backfill heads for."""
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    with lb.cluster() as conn:
        for k in range(1, 31):
            season = 1995 + k
            conn.execute(
                "INSERT INTO tropical_storms SELECT substr(storm_id, 1, 4) || %(s)s, basin, number, "
                "%(s)s::smallint, 'inactive', names, aliases, first_seen - %(d)s, last_seen - %(d)s, "
                "last_lat, last_lon, created_ts, updated_ts FROM tropical_storms WHERE season = 2026",
                {"s": str(season), "d": timedelta(days=364 * k)})
            conn.execute(
                "INSERT INTO tropical_track_points (storm_id, source, init_ts, advisory, tau, valid_ts, "
                "lat, lon, vmax_kt, mslp_hpa, radii, stage) SELECT substr(storm_id, 1, 4) || %(s)s, "
                "source, init_ts - %(d)s, advisory, tau, valid_ts - %(d)s, lat, lon, vmax_kt, mslp_hpa, "
                "radii, stage FROM tropical_track_points WHERE storm_id LIKE '%%2026'",
                {"s": str(season), "d": timedelta(days=364 * k)})
        conn.execute("ANALYZE")
        yield conn


def test_T10_every_read_uses_an_index_at_31x(pg_scaled):
    n = pg_scaled.execute("SELECT count(*) AS n FROM tropical_track_points").fetchone()["n"]
    assert n == 34518 * 31
    assert_indexed(pg_scaled)
    # tropical_storms is one row per storm (93 here, ~100 a season live): a few
    # pages, which Postgres reads whole rather than through the PK. Every read
    # of the three big tables above is indexed.
    pages = pg_scaled.execute("SELECT relpages FROM pg_class WHERE relname = 'tropical_storms'").fetchone()
    assert pages["relpages"] <= 16


def test_T10_body_bytes_are_under_stop_z(client, monkeypatch, pg):
    sizes = {}
    for sid in STORMS:
        for n in (4, 8):
            monkeypatch.setattr(main, "_pool", lb.PgPool(pg))
            raw = client.get(f"{P}/tracks?storm_id={sid}&n={n}").content
            sizes[(sid, n)] = (len(raw), len(gzip.compress(raw, 6)))
    assert all(gz < 500_000 for _raw, gz in sizes.values())              # STOP-Z
    rec = json.loads((ROOT / "docs/receipts/tropics-api-d091673/sizes.json").read_text())
    for sid in STORMS:                     # the cache block's age digits move a byte or two
        for n in (4, 8):
            assert abs(rec[sid][f"n{n}"]["raw"] - sizes[(sid, n)][0]) <= 16
            assert abs(rec[sid][f"n{n}"]["gzip6"] - sizes[(sid, n)][1]) <= 16


# ═══════════════════════════════════════════════════════════════════════════
# C — memo, timeout, cache block
# ═══════════════════════════════════════════════════════════════════════════

def test_C_each_build_sets_the_statement_timeout_first(client, monkeypatch):
    routes = [(tr.POLL_SQL, [{"newest_heartbeat_ts": HB, "stale_after_override": timedelta(hours=2),
                              "lifecycle_status": "active"}]),
              (tr.STORMS_SQL, []), (tr.STORM_SQL, [_storm_row()])]
    for path in (f"{P}/storms", f"{P}/storm?storm_id=al092026", f"{P}/tracks?storm_id=al092026",
                 f"{P}/odds?storm_id=al092026"):
        r, pool = fake_get(client, monkeypatch, routes, path)
        assert r.status_code == 200, (path, r.text)
        assert pool.sql_run()[0] == f"SET LOCAL statement_timeout = '{main.TROPICS_STATEMENT_TIMEOUT}'"
    assert main.TROPICS_STATEMENT_TIMEOUT == "5s"


def test_C_memoised_per_key_300s_never_stale_with_the_cache_block(client, monkeypatch):
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.TRACKS_SQL, _track_rows(["atcf:AVNO"]))]
    pool = FakePool(routes)
    monkeypatch.setattr(main, "_pool", pool)
    r1 = client.get(f"{P}/tracks?storm_id=al092026")
    r2 = client.get(f"{P}/tracks?storm_id=al092026&n=4")                # n=4 is the default's key
    r3 = client.get(f"{P}/tracks?storm_id=al092026&n=3")
    assert [r.headers["x-cache"] for r in (r1, r2, r3)] == ["miss", "hit", "miss"]
    assert r1.headers["cache-control"] == "max-age=300"
    assert set(r1.json()["cache"]) == {"state", "built_at", "age_seconds", "ttl_seconds",
                                       "build_seconds", "refreshing"}
    for c in CACHES:
        cache = getattr(main, c)
        assert cache.ttl == 300.0 and cache.allow_stale is False and cache.max_stale_s is None
    e = main._tropics_tracks_cache._entries[("al092026", 4)]
    e.built_mono -= 301
    r4 = client.get(f"{P}/tracks?storm_id=al092026")
    assert r4.headers["x-cache"] == "miss"                               # rebuilt, never served stale


def test_C_twenty_concurrent_cold_requests_make_one_build(monkeypatch):
    routes = [(tr.STORM_SQL, [_storm_row()]), (tr.ODDS_SQL, [])]
    pool = FakePool(routes)
    monkeypatch.setattr(main, "_pool", pool)
    import httpx

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                     base_url="http://t") as c:
            return await asyncio.gather(*(c.get(f"{P}/odds?storm_id=al092026") for _ in range(20)))
    rs = asyncio.run(run())
    assert all(r.status_code == 200 for r in rs)
    assert sum(tr.ODDS_SQL in q for q in pool.sql_run()) == 1


# ═══════════════════════════════════════════════════════════════════════════
# R — the rulings across every body
# ═══════════════════════════════════════════════════════════════════════════

def all_bodies(client, monkeypatch, pg):
    out = {"storms": pg_get(client, monkeypatch, pg, f"{P}/storms")}
    for sid in STORMS:
        out[f"storm {sid}"] = pg_get(client, monkeypatch, pg, f"{P}/storm?storm_id={sid}")
        out[f"tracks {sid}"] = pg_get(client, monkeypatch, pg, f"{P}/tracks?storm_id={sid}&n=8")
        out[f"odds {sid}"] = pg_get(client, monkeypatch, pg, f"{P}/odds?storm_id={sid}")
    return out


def test_R_every_body_carries_not_served_and_the_shape_statements(client, monkeypatch, pg):
    for name, b in all_bodies(client, monkeypatch, pg).items():
        assert set(b["not_served"]) == {"ensemble_members", "cone_and_warnings",
                                        "storms_before_2026", "model_scores", "place_coordinates"}, name
        assert "R2 parquet" in b["not_served"]["ensemble_members"]
        assert "d091671" in b["not_served"]["storms_before_2026"]
        assert b["timestamps"] == tr.TIMESTAMPS and b["longitude"] == tr.LONGITUDE


def test_R_every_ecmwf_derived_series_carries_its_cc_by_notice(client, monkeypatch, pg):
    seen = set()
    for sid in STORMS:
        b = pg_get(client, monkeypatch, pg, f"{P}/tracks?storm_id={sid}&n=8")
        for c in b["cycles"]:
            for s in c["series"]:
                if s["source"] in ("ecmwf_ifs", "ecmwf_aifs"):
                    seen.add(s["source"])
                    assert "CC BY 4.0" in s["notice"] and "(c) 2026 European Centre" in s["notice"]
                    assert "Modified" in s["notice"]
                elif tr.source_meta(s["source"])["ecmwf"]:
                    assert "CC BY 4.0" in s["notice"]
                else:
                    assert s["notice"] is None
        assert set(b["attribution"]["ecmwf"]["applies_to"]) == {"ecmwf_ifs", "ecmwf_aifs"}
    assert seen == {"ecmwf_ifs", "ecmwf_aifs"}


def test_R_served_values_are_the_banks_and_valid_is_init_plus_tau(client, monkeypatch, pg):
    advs = {r["advisory"] for r in bank("point")} | {r["advisory"] for r in bank("odds")}
    for name, b in all_bodies(client, monkeypatch, pg).items():
        for c in b.get("cycles", []) if "tracks" in name else []:
            for s in c["series"]:
                for p in s["points"]:
                    assert ts(p["valid_ts"]) == ts(c["init_ts"]) + timedelta(hours=p["tau_h"])
            for a in c["official"]:
                assert a["advisory"] in advs
        text = json.dumps(b)
        assert not re.search(r'"(delta|change|diff|smoothed|interp\w*|mean_\w+)"\s*:', text), name
        assert not re.search(r'"source": "(ecmwf_ens|ecmwf_aifs_ens|atcf:AP\d\d)"', text), name


def _base():
    import subprocess
    for ref in ("origin/main", "main"):
        r = subprocess.run(["git", "-C", str(ROOT), "merge-base", "HEAD", ref],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    pytest.skip("no main branch to diff against")


def test_R_no_existing_route_or_memo_changed():
    import subprocess
    base = _base()
    diff = subprocess.run(["git", "-C", str(ROOT), "diff", base, "--", "main.py"],
                          capture_output=True, text=True)
    removed = [l for l in diff.stdout.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert removed == []                                                  # main.py only gains lines
    assert "/api/weather/tropics" not in subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{base}:main.py"], capture_output=True, text=True).stdout


# ═══════════════════════════════════════════════════════════════════════════
# V — the banked bodies (the dashboard lane's vectors)
# ═══════════════════════════════════════════════════════════════════════════

VECTORS = ([("storms_active.json", f"{P}/storms")]
           + [(f"storm_{s}.json", f"{P}/storm?storm_id={s}") for s in STORMS]
           + [(f"tracks_{s}_n4.json", f"{P}/tracks?storm_id={s}&n=4") for s in STORMS]
           + [("tracks_ep182026_n8.json", f"{P}/tracks?storm_id=ep182026&n=8")]
           + [(f"odds_{s}.json", f"{P}/odds?storm_id={s}") for s in STORMS])


@pytest.mark.parametrize("fname,path", VECTORS, ids=[v[0] for v in VECTORS])
def test_V_the_banked_body_is_served_byte_for_byte(client, monkeypatch, pg, fname, path):
    body = strip_cache(pg_get(client, monkeypatch, pg, path))
    banked = (BODIES / fname).read_text()
    assert json.dumps(body, separators=(",", ":"), ensure_ascii=False) == banked
