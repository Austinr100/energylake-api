"""d091614 (D-09-25-141 clause 3) — GET /api/mjo/status.

  T1  `today` is the newest banked day
  T2  the walk is 40 days in order, each with its source; the OMI→ROMI seam is named
  T2r rmm1/rmm2 follow the banked convention, pinned against banked days
  T3  `active` equals amplitude >= the threshold on every day; the constant is pantry's
  T4  no day is invented: a missing day is absent and counted
  P1  plans: one (dataset, series) per index scan, no sequential scan
  plus the D-09-25-138 memo, the statement timeout and the 503s.

Route tests run main.py over a fake pool that answers each of mjo_status.py's
statements by its own parameters from a bank of days, the way Postgres would.
The production bank is tests/fixtures/mjo_status_d091614 (Neon, 2026-10-05,
read-only). Reds: docs/receipts/mjo-status-api-d091614/reds.txt.
"""

import json
import pathlib
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import mjo_status as ms
from test_solar_outlook import FakePool

UTC = timezone.utc
HERE = pathlib.Path(__file__).resolve().parent
FIX = json.loads((HERE / "fixtures" / "mjo_status_d091614" / "production_2026_10_05.json").read_text())
NOW = datetime.fromisoformat(FIX["now"])                     # 2026-10-05T18:00Z
PLANS = (HERE.parent / "docs" / "receipts" / "mjo-status-api-d091614" / "plans.txt").read_text()
PANTRY_BUILDER = HERE.parent.parent / "energylake-pantry" / "scripts" / "build_mjo_index.py"


# ── a bank of days, answered the way Postgres would ─────────────────────────

def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def production_bank() -> dict:
    """date -> {series: value, "_source": index_source}"""
    bank = {}
    for d, pc1, pc2, amp, ph, act, src in FIX["walk"]:
        bank[date.fromisoformat(d)] = {"pc1": pc1, "pc2": pc2, "amplitude": amp,
                                       "phase": float(ph), "active": float(act), "_source": src}
    for d, pc1, pc2, amp, ph, src in FIX["banked_days"]:
        bank[date.fromisoformat(d)] = {"pc1": pc1, "pc2": pc2, "amplitude": amp,
                                       "phase": float(ph), "active": float(amp >= 1.0),
                                       "_source": src}
    return bank


def synthetic_bank(last: date, n: int, *, seam: date | None = None) -> dict:
    """n consecutive days ending on `last`; OMI before `seam`, ROMI from it."""
    bank = {}
    for i in range(n):
        d = last - timedelta(days=n - 1 - i)
        pc1, pc2 = 0.9 * ((i % 7) - 3) / 3, 0.8 * ((i % 5) - 2) / 2
        amp = round((pc1 ** 2 + pc2 ** 2) ** 0.5, 5)
        r1, r2 = ms.rmm(pc1, pc2)
        bank[d] = {"pc1": pc1, "pc2": pc2, "amplitude": amp,
                   "phase": float(ms.octant(r1, r2)), "active": float(amp >= 1.0),
                   "_source": "romi" if seam is not None and d >= seam else "omi"}
    return bank


def bank_pool(bank: dict) -> FakePool:
    def rows(series, lo, hi):
        return sorted((d, v) for d, v in bank.items()
                      if series in v and lo <= _ts(d) < hi)

    def newest(p):
        out = []
        for s in ms.SERIES:
            r = rows(s, p["lo"], p["hi"])
            assert p["dataset"] == ms.DATASET
            out.append({"series": s, "ts": _ts(r[-1][0]) if r else None})
        return out

    def walk(p):
        assert p["dataset"] == ms.DATASET
        return [{"series": s, "ts": _ts(d), "value": v[s], "source": v["_source"]}
                for s in ms.SERIES for d, v in rows(s, p["lo"], p["hi"])]

    def seam(p):
        r = [(d, v) for d, v in rows(p["series"], p["lo"], p["hi"]) if v["_source"] == "omi"]
        if not r:
            return []
        o = r[-1][0]
        after = [(d, v) for d, v in rows(p["series"], _ts(o + timedelta(days=1)), p["hi"])]
        nd, nv = after[0] if after else (None, None)
        return [{"omi_last": _ts(o), "omi_last_source": "omi",
                 "next_day": _ts(nd) if nd else None,
                 "next_source": nv["_source"] if nv else None}]

    return FakePool([("SELECT v.series, n.ts", newest),
                     ("SELECT v.series, w.ts", walk),
                     ("AS omi_last", seam)])


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    main._mjo_status_cache.clear()
    yield
    main._mjo_status_cache.clear()


def get(monkeypatch, bank):
    pool = bank_pool(bank)
    monkeypatch.setattr(main, "_pool", pool)
    r = TestClient(main.app).get("/api/mjo/status")
    return r, pool


@pytest.fixture
def prod(monkeypatch):
    r, pool = get(monkeypatch, production_bank())
    assert r.status_code == 200, r.text
    return r.json(), pool


# ═══ T1 today is the newest banked day ══════════════════════════════════════

def test_T1_today_is_the_newest_banked_day(prod):
    b, _ = prod
    assert b["today"] == {"date": "2026-09-30", "phase": 2, "amplitude": 1.16974,
                          "active": True, "index_source": "romi"}
    assert b["frontier"] == {"date": "2026-09-30", "age_days": 5, "as_of": "2026-10-05"}
    assert b["walk"][-1]["date"] == b["today"]["date"]
    assert all(FIX["newest"][s] == "2026-09-30" for s in ms.SERIES)


def test_T1_a_newer_bank_moves_today(monkeypatch):
    bank = synthetic_bank(date(2026, 10, 4), 60)
    r, _ = get(monkeypatch, bank)
    b = r.json()
    assert b["today"]["date"] == "2026-10-04" and b["frontier"]["age_days"] == 1
    last = bank[date(2026, 10, 4)]
    assert b["today"]["amplitude"] == last["amplitude"]
    assert b["today"]["phase"] == int(last["phase"])


def test_T1_one_series_ahead_is_the_frontier_and_the_day_says_what_it_lacks(monkeypatch):
    bank = synthetic_bank(date(2026, 9, 30), 60)
    bank[date(2026, 10, 1)] = {"pc1": 0.1, "_source": "romi"}
    b = get(monkeypatch, bank)[0].json()
    assert b["today"]["date"] == "2026-10-01"
    assert b["today"]["amplitude"] is None and b["today"]["phase"] is None
    assert b["walk_window"]["incomplete"] == [
        {"date": "2026-10-01", "lacking": ["pc2", "amplitude", "phase", "active"]}]


# ═══ T2 the walk: 40 days, in order, each with its source; the seam ════════

def test_T2_walk_is_40_days_in_order_with_sources(prod):
    b, _ = prod
    w = b["walk"]
    assert len(w) == ms.WALK_DAYS == 40
    days = [date.fromisoformat(x["date"]) for x in w]
    assert days == [date(2026, 8, 22) + timedelta(days=i) for i in range(40)]
    assert all(x["source"] == "romi" for x in w)
    assert set(w[0]) == {"date", "pc1", "pc2", "rmm1", "rmm2", "phase",
                         "amplitude", "active", "source"}
    assert b["walk_window"] == {"first": "2026-08-22", "last": "2026-09-30", "days": 40,
                                "served": 40, "missing": 0, "missing_dates": [],
                                "incomplete": []}


def test_T2_the_seam_is_named(prod):
    s = prod[0]["sources"]
    assert s["omi_last"] == "2026-06-24" and s["romi_first"] == "2026-06-25"
    assert s["seam"] == "OMI through 2026-06-24; ROMI from 2026-06-25"
    assert s["note"] is None
    assert FIX["whole_bank_audit"]["omi_last"] == s["omi_last"]
    assert FIX["whole_bank_audit"]["romi_first"] == s["romi_first"]


def test_T2_a_walk_across_the_seam_carries_each_days_source(monkeypatch):
    seam = date(2026, 9, 20)
    b = get(monkeypatch, synthetic_bank(date(2026, 9, 30), 80, seam=seam))[0].json()
    src = {x["date"]: x["source"] for x in b["walk"]}
    assert src["2026-09-19"] == "omi" and src["2026-09-20"] == "romi"
    assert [x["source"] for x in b["walk"]] == ["omi"] * 29 + ["romi"] * 11
    assert b["sources"]["omi_last"] == "2026-09-19"
    assert b["sources"]["romi_first"] == "2026-09-20"


def test_T2_no_romi_yet_is_said_not_guessed(monkeypatch):
    b = get(monkeypatch, synthetic_bank(date(2026, 9, 30), 60))[0].json()
    s = b["sources"]
    assert s["omi_last"] == "2026-09-30" and s["romi_first"] is None and s["seam"] is None
    assert "ROMI has not begun" in s["note"]


# ═══ T2r the RMM rotation against banked days ══════════════════════════════

def test_T2r_rmm_follows_the_banked_convention_on_banked_days():
    # one day from each source; the banked phase is the octant of (PC2, -PC1)
    assert len(FIX["banked_days"]) >= 3
    assert {r[5] for r in FIX["banked_days"]} == {"omi_orig", "omi", "romi"}
    for d, pc1, pc2, amp, phase, src in FIX["banked_days"]:
        r1, r2 = ms.rmm(pc1, pc2)
        assert (r1, r2) == (pc2, -pc1), d
        assert ms.octant(r1, r2) == phase, d
        # and NOT the unrotated pair: the convention is load-bearing
        assert ms.octant(pc1, pc2) != phase or ms.octant(-pc1, pc2) != phase, d


def test_T2r_three_banked_days_by_value():
    assert ms.rmm(-0.73054, -1.66995) == (-1.66995, 0.73054)       # 1985-01-15 omi_orig, phase 8
    assert ms.octant(-1.66995, 0.73054) == 8
    assert ms.rmm(-0.24596, -0.31677) == (-0.31677, 0.24596)       # 2026-06-24 omi, phase 8
    assert ms.octant(-0.31677, 0.24596) == 8
    assert ms.rmm(1.06382, -0.48639) == (-0.48639, -1.06382)       # 2026-09-30 romi, phase 2
    assert ms.octant(-0.48639, -1.06382) == 2


def test_T2r_every_walk_day_served_rotates_to_its_banked_phase(prod):
    for x in prod[0]["walk"]:
        assert x["rmm1"] == x["pc2"] and x["rmm2"] == -x["pc1"], x["date"]
        assert ms.octant(x["rmm1"], x["rmm2"]) == x["phase"], x["date"]


def test_T2r_octant_boundaries_match_pantry():
    assert [ms.octant(-1, -1e-9), ms.octant(-1, -1.0001), ms.octant(0, -1),
            ms.octant(1, -1e-9), ms.octant(1, 0), ms.octant(0.0001, 1),
            ms.octant(-1, 1.0001), ms.octant(-1, 0)] == [1, 2, 3, 4, 5, 6, 7, 8]


PANTRY_ROMI = HERE.parent.parent / "energylake-pantry" / "ingesters" / "psl_romi.py"


@pytest.mark.skipif(not PANTRY_ROMI.exists(), reason="no energylake-pantry checkout beside this repo")
def test_T2r_octant_is_pantrys_rmm_phase_on_a_grid():
    import math
    src = re.search(r"^def rmm_phase\(.*?(?=^def )", PANTRY_ROMI.read_text(), re.M | re.S).group(0)
    ns = {"math": math}
    exec(src, ns)
    for i in range(720):
        a = math.radians(i / 2)
        r1, r2 = math.cos(a), math.sin(a)
        assert ms.octant(r1, r2) == ns["rmm_phase"](r1, r2), i


# ═══ T3 active equals amplitude >= the threshold; the constant is pantry's ═

def test_T3_active_equals_amplitude_at_or_over_the_threshold_on_every_day(prod):
    w = prod[0]["walk"]
    for x in w:
        assert x["active"] is (x["amplitude"] >= prod[0]["active_threshold"]), x["date"]
    assert sum(x["active"] for x in w) == 7
    assert FIX["whole_bank_audit"]["active_disagree"] == 0


def test_T3_the_threshold_is_pantrys():
    # pantry scripts/build_mjo_index.py, main @ c901636: ACTIVE_THRESHOLD = 1.0
    assert ms.ACTIVE_THRESHOLD == 1.0
    assert ms.is_active(1.0) and not ms.is_active(0.99999)


@pytest.mark.skipif(not PANTRY_BUILDER.exists(), reason="no energylake-pantry checkout beside this repo")
def test_T3_the_threshold_is_pantrys_from_its_source():
    m = re.search(r"^ACTIVE_THRESHOLD = ([0-9.]+)$", PANTRY_BUILDER.read_text(), re.M)
    assert m and float(m.group(1)) == ms.ACTIVE_THRESHOLD


def test_T3_the_body_carries_the_threshold(prod):
    assert prod[0]["active_threshold"] == 1.0
    assert "1.0" in prod[0]["active_rule"]


# ═══ T4 no day is invented ═════════════════════════════════════════════════

def test_T4_a_missing_day_is_absent_and_counted(monkeypatch):
    bank = synthetic_bank(date(2026, 9, 30), 60)
    gone = [date(2026, 9, 10), date(2026, 9, 11), date(2026, 8, 22)]
    for d in gone:
        del bank[d]
    b = get(monkeypatch, bank)[0].json()
    served = [x["date"] for x in b["walk"]]
    assert len(served) == 37
    assert not set(served) & {d.isoformat() for d in gone}
    assert served == sorted(served)
    ww = b["walk_window"]
    assert ww["missing"] == 3 and ww["served"] == 37 and ww["days"] == 40
    assert ww["missing_dates"] == ["2026-08-22", "2026-09-10", "2026-09-11"]
    assert ww["first"] == "2026-08-22"                    # the window, not the first served day


def test_T4_a_missing_frontier_day_moves_the_frontier_not_invents_it(monkeypatch):
    bank = synthetic_bank(date(2026, 9, 30), 60)
    del bank[date(2026, 9, 30)]
    b = get(monkeypatch, bank)[0].json()
    assert b["today"]["date"] == "2026-09-29" and b["frontier"]["age_days"] == 6


def test_T4_a_missing_series_is_null_never_filled(monkeypatch):
    bank = synthetic_bank(date(2026, 9, 30), 60)
    del bank[date(2026, 9, 15)]["pc1"]
    b = get(monkeypatch, bank)[0].json()
    day = next(x for x in b["walk"] if x["date"] == "2026-09-15")
    assert day["pc1"] is None and day["rmm2"] is None and day["rmm1"] is not None
    assert b["walk_window"]["incomplete"] == [{"date": "2026-09-15", "lacking": ["pc1"]}]


def test_T4_production_has_no_missing_day(prod):
    assert prod[0]["walk_window"]["missing"] == 0
    assert FIX["whole_bank_audit"]["days"] == (date(2026, 9, 30) - date(1979, 1, 1)).days + 1


# ═══ P1 plans ══════════════════════════════════════════════════════════════

def test_P1_every_statement_has_a_plan():
    for name in ("NEWEST_SQL (lo", "NEWEST_SQL (the CLAUDE.md trap", "WALK_SQL", "SEAM_SQL"):
        assert f"== {name}" in PLANS, name


def test_P1_no_sequential_scan():
    assert "Seq Scan" not in PLANS


def test_P1_every_index_scan_names_one_dataset_and_series():
    conds = re.findall(r"on timeseries_values \w+ .*\n\s+Index Cond: (.*)", PLANS)
    assert len(conds) == 5
    for c in conds:
        assert "dataset = 'mjo_index_daily'" in c and "(series = " in c and "ts " in c, c
        assert c.count("dataset =") == 1 and c.count("series =") == 1, c


def test_P1_the_sql_names_dataset_series_and_a_ts_range_per_read():
    for sql in (ms.NEWEST_SQL, ms.WALK_SQL, ms.SEAM_SQL):
        assert "DISTINCT ON" not in sql and "max(" not in sql.lower()
        for block in sql.split("FROM timeseries_values t")[1:]:
            assert "t.dataset = %(dataset)s" in block and "t.series =" in block
            assert "t.ts >" in block and "t.ts <" in block


def test_P1_the_route_runs_exactly_the_three_reads_under_a_timeout(prod):
    sql = prod[1].sql_run()
    assert sql[0] == f"SET LOCAL statement_timeout = '{main.MJO_STATEMENT_TIMEOUT}'"
    assert sql[1:] == [ms.NEWEST_SQL, ms.WALK_SQL, ms.SEAM_SQL]
    params = [p for _q, p in prod[1].calls[1:]]
    assert params[1]["lo"] == _ts(date(2026, 8, 22)) and params[1]["hi"] == _ts(date(2026, 10, 1))
    assert params[2]["series"] == "amplitude"


# ═══ the memo (D-09-25-138), the 503s ══════════════════════════════════════

def test_memo_is_300s_fresh_and_900s_stale():
    c = main._mjo_status_cache
    assert c.ttl == 300.0 and c.allow_stale and c.max_stale_s == 900.0


def test_memo_serves_the_second_call_without_sql(monkeypatch):
    r1, pool = get(monkeypatch, production_bank())
    n = len(pool.calls)
    r2 = TestClient(main.app).get("/api/mjo/status")
    assert len(pool.calls) == n
    assert r1.json()["cache"]["state"] == "miss" and r2.json()["cache"]["state"] == "fresh"
    assert r2.headers["Cache-Control"] == "max-age=300"


def test_an_empty_bank_is_503_and_not_memoised(monkeypatch):
    r, pool = get(monkeypatch, {})
    assert r.status_code == 503 and "no rows banked for mjo_index_daily" in r.json()["detail"]
    assert len(pool.calls) == 2                          # the timeout and NEWEST only
    assert main._mjo_status_cache._entries == {}


def test_a_db_error_is_503(monkeypatch):
    class Broken:
        def connection(self):
            raise RuntimeError("pool closed")
    monkeypatch.setattr(main, "_pool", Broken())
    r = TestClient(main.app).get("/api/mjo/status")
    assert r.status_code == 503 and "db unavailable" in r.json()["detail"]


def test_nothing_statistical_is_served(prod):
    b = prod[0]
    assert set(b) == {"label", "dataset", "today", "frontier", "walk", "walk_window",
                      "active_threshold", "active_rule", "phase_convention", "sources",
                      "attribution", "cache"}
    assert "PSL" in b["attribution"]["text"] and "OMI" in b["attribution"]["text"]
    assert "ROMI" in b["attribution"]["text"]
