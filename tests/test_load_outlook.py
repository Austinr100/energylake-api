"""d091611 (D-09-25-139, D-09-25-140) — the load outlook and CAISO net demand.

  L1  each day carries its product, issue date and lead (and the hours too)
  L2  a BA failing the usability rule has no score and says why
  L3  the BA's DF appears only where EIA has the BA
  L4  no hour beyond D+7
  N1  net demand is null wherever any part is registry or null, naming the part
  N2  the backtest pairs D's DAM load with D-1 12Z's calibrated parts
  N3  CAISO's DA net demand is DAM load less the three hubs' DAM solar and wind
  N4  scores need 14 days
  P1  plans: no sequential scan of timeseries_values
  plus the D-09-25-138 stale cap, the statement timeout, the 400s, and
  production's rows (tests/fixtures/load_outlook_d091611) through the routes.

Route tests run main.py over load_bank_d091611's pool, which answers each
statement from production's rows by its own parameters. Reds:
docs/receipts/load-net-demand-api-d091611/reds.txt.
"""

import asyncio
import pathlib
import re
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import load_bank_d091611 as lb
import load_outlook as lo
import main

UTC = timezone.utc
NOW = lb.NOW                                   # 2026-10-05T03:30Z; Pacific today 10-04
TODAY = date(2026, 10, 4)
HERE = pathlib.Path(__file__).resolve().parent
RECEIPTS = HERE.parent / "docs" / "receipts" / "load-net-demand-api-d091611"


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr(main, "_utcnow", lambda: NOW)
    for c in main._LOAD_CACHES:
        c.clear()
    yield
    for c in main._LOAD_CACHES:
        c.clear()


@pytest.fixture(scope="module")
def bank():
    return lb.load()


def serve(monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    r = TestClient(main.app).get(path)
    return r


@pytest.fixture
def get(monkeypatch, bank):
    def _get(path, pool=None):
        p = pool or lb.pool(bank)
        r = serve(monkeypatch, p, path)
        return r, p
    return _get


# ── synthetic rows ──────────────────────────────────────────────────────────

def issued(day: date, product: str) -> str:
    d = day - timedelta(days=lo.PRODUCT_HORIZON_DAYS[product])
    return datetime(d.year, d.month, d.day, 16, 10, tzinfo=UTC).isoformat()


def fc_rows(product, days, value=lambda h: 1000.0):
    out = []
    for d in days:
        for h in lo.day_hours(d):
            out.append({"product": product, "ts": h, "value": value(h),
                        "publish_time": issued(d, product)})
    return out


def span(a: date, b: date):
    return [a + timedelta(days=k) for k in range((b - a).days + 1)]


def synthetic_products(extra_7da_days=0):
    """CAISO's publication as of the evening of 10-04 PT: DAM to 10-05, 2DA to
    10-06, 7DA to 10-11 (+ extra days to test L4)."""
    return (fc_rows("DAM", span(date(2026, 9, 1), date(2026, 10, 5)), lambda h: 30000.0)
            + fc_rows("2DA", span(date(2026, 9, 1), date(2026, 10, 6)), lambda h: 31000.0)
            + fc_rows("7DA", span(date(2026, 9, 1), date(2026, 10, 11 + extra_7da_days)),
                      lambda h: 32000.0))


# ═══ L1 ═════════════════════════════════════════════════════════════════════

def test_L1_production_each_day_carries_product_issue_and_lead(get):
    r, _ = get("/api/load/outlook?area=CA%20ISO-TAC")
    assert r.status_code == 200
    b = r.json()
    assert [d["day"] for d in b["days"]] == [(TODAY + timedelta(days=k)).isoformat() for k in range(8)]
    assert [d["product"] for d in b["days"]] == ["DAM", "DAM", "2DA", "7DA", "7DA", "7DA", "7DA", "7DA"]
    for d in b["days"]:
        assert d["product"] in lo.PRODUCTS
        assert d["issued_at"] and d["issue_date"]
        assert d["lead_days"] == lo.PRODUCT_HORIZON_DAYS[d["product"]]
        issue = date.fromisoformat(d["issue_date"])
        assert date.fromisoformat(d["day"]) - issue == timedelta(days=d["lead_days"])
        assert d["age_sentence"].startswith(lo.PRODUCT_NAME[d["product"]])
        assert f"{issue.strftime('%b')} {issue.day}" in d["age_sentence"]
    by_day = {d["day"]: d for d in b["days"]}
    for h in b["hours"]:
        day = datetime.fromisoformat(h["target_ts"]).astimezone(lo.PT).date().isoformat()
        assert h["product"] == by_day[day]["product"]
        assert h["issued_at"] == by_day[day]["issued_at"]
        assert h["lead_days"] == by_day[day]["lead_days"]


def test_L1_D3_to_D6_say_their_age(get):
    """D-09-25-139 clause 3: D+3..D+6 are CAISO's issued 4 to 1 days ago."""
    b = get("/api/load/outlook?area=CA%20ISO-TAC")[0].json()
    ages = {d["days_ahead"]: d for d in b["days"]}
    for k, ago in ((3, 4), (4, 3), (5, 2), (6, 1)):
        assert ages[k]["product"] == "7DA"
        assert ages[k]["issued_days_ago"] == ago
        assert f"{ago} days ago" in ages[k]["age_sentence"] or (ago == 1 and "yesterday" in ages[k]["age_sentence"])
        assert "does not re-issue" in ages[k]["age_sentence"]
    assert "7DA" in lo.ISSUE_SENTENCE and "never revised" in lo.ISSUE_SENTENCE["7DA"]


def test_L1_freshest_product_wins_and_marks_the_change():
    days = lo.choose_days(lo.split_products(synthetic_products()), TODAY)
    assert [c["product"] for c in days] == ["DAM", "DAM", "2DA", "7DA", "7DA", "7DA", "7DA", "7DA"]
    _hours, out = lo.build_hours_and_days(lo.split_products(synthetic_products()), {}, TODAY)
    assert [d["product_changes"] for d in out] == [False, False, True, True, False, False, False, False]


def test_L1_a_partial_dam_does_not_hide_a_complete_2da():
    rows = [r for r in synthetic_products()
            if not (r["product"] == "DAM" and r["ts"] >= datetime(2026, 10, 5, 19, tzinfo=UTC))]
    days = lo.choose_days(lo.split_products(rows), TODAY)
    assert days[1]["product"] == "2DA"      # 10-05: DAM is short, 2DA covers it whole


# ═══ L2 ═════════════════════════════════════════════════════════════════════

def test_L2_production_ladwp_fails_and_is_not_scored(get):
    b = get("/api/load/outlook?area=LADWP")[0].json()
    assert b["actual_basis"]["usable"] is False
    assert set(b["actual_basis"]["verdict"]["failed"]) == {"dropouts", "agreement"}
    assert b["actuals"] == []
    for k in ("DAM", "2DA", "7DA", "EIA_DF"):
        s = b["scores"][k]
        assert s["status"] == "not_scored"
        assert s["text"].startswith("not scored: EIA-930 D for LDWP fails the usability rule")
        for key in ("mape_pct", "mae_mw", "bias_mw"):
            assert key not in s                      # never a zero
    assert b["days"] and b["hours"]                  # the forecast is still drawn


def test_L2_a_failing_rule_takes_the_score_away():
    good = {"n_hours": 672, "d_median": 6660, "n_dropout": 0, "dam_median": 6500,
            "r_m1": 0.88, "r_0": 0.93, "r_p1": 0.87}
    assert lo.usability(good, 672)["usable"]
    for change, test in (({"n_hours": 600}, "coverage"), ({"n_dropout": 5}, "dropouts"),
                         ({"r_0": 0.85, "r_m1": 0.80, "r_p1": 0.80}, "agreement"),
                         ({"r_p1": 0.95}, "clock"), ({"dam_median": 5000}, "scale")):
        v = lo.usability({**good, **change}, 672)
        assert v["failed"] == [test], (change, v["failed"])
        basis = lo.actual_basis("BPAT", v)
        assert basis["usable"] is False and basis["reason"].startswith("EIA-930 D for BPAT")
        days = lo.score_days(NOW)
        scores = lo.build_scores("BPAT", lo.split_products([]), {}, {}, basis, days)
        assert all(s["status"] == "not_scored" for s in scores.values())


def test_L2_usability_verdicts_on_production(bank):
    passed = sorted(r["area"] for r in bank["usability"] if lo.usability(r, 672)["usable"])
    assert passed == ["BANC", "BPAT", "EPE", "IPCO", "NWMT", "PACW", "PGE", "PNM",
                      "PSEI", "SCL", "TPWR"]


def test_L2_tacs_and_sub_areas_say_why(get):
    for area, why in (("PGE-TAC", lo.NO_ACTUAL_REASON["tac"]),
                      ("BANCSMUD", lo.NO_ACTUAL_REASON["sub_area"]),
                      ("AVRN", lo.NO_ACTUAL_REASON["weim_ba"])):
        b = get(f"/api/load/outlook?area={area}")[0].json()
        assert all(s["text"] == f"not scored: {why}" for s in b["scores"].values()), area


def test_L2_production_bpat_is_scored(get):
    b = get("/api/load/outlook?area=BPAT")[0].json()
    assert b["actual_basis"]["usable"] is True
    s = b["scores"]["DAM"]
    assert s["status"] == "scored" and s["n_days"] == 28
    assert s["mape_pct"] == pytest.approx(3.58, abs=0.01)     # Step 1, by SQL
    assert b["scores"]["EIA_DF"]["mape_pct"] == pytest.approx(1.33, abs=0.01)


def test_L2_production_system_scores_match_step_1(get):
    s = get("/api/load/outlook?area=CA%20ISO-TAC")[0].json()["scores"]
    assert s["DAM"]["mape_pct"] == pytest.approx(2.40, abs=0.01)
    assert s["DAM"]["mae_mw"] == pytest.approx(693, abs=1)
    assert s["7DA"]["mae_mw"] == pytest.approx(2168, abs=1)
    assert s["2DA"]["peak_hour_hit_pct"] == pytest.approx(85.71, abs=0.01)


# ═══ L3 ═════════════════════════════════════════════════════════════════════

def test_L3_df_only_where_eia_has_the_ba(get):
    for area, has in (("BPAT", True), ("LADWP", True), ("CA ISO-TAC", False),
                      ("PGE-TAC", False), ("BANCSMUD", False), ("AVRN", False), ("WALCDSW", False)):
        r, pool = get(f"/api/load/outlook?area={area.replace(' ', '%20')}")
        b = r.json()
        assert (b["df"] is not None) == has, area
        assert ("EIA_DF" in b["scores"]) == has, area
        assert any(h["df_mw"] is not None for h in b["hours"]) == has, area
        read_df = any(p and p.get("dataset") == lo.DF_DATASET for _q, p in pool.calls)
        assert read_df == has, area
        if not has:
            assert b["df_absent_reason"]
    b = get("/api/load/outlook?area=LADWP")[0].json()
    assert b["df"]["respondent"] == "LDWP"
    assert b["df"]["label"].startswith("Los Angeles Department of Water and Power's own")


def test_L3_system_df_is_not_drawn_and_says_why(get):
    b = get("/api/load/outlook?area=CA%20ISO-TAC")[0].json()
    assert b["df"] is None and "CAISO's own DAM" in b["df_absent_reason"]


def test_L3_areas_route(get):
    b = get("/api/load/areas")[0].json()
    assert b["count"] == 36 and len(b["areas"]) == 36
    by = {a["code"]: a for a in b["areas"]}
    assert set(by) == set(lo.AREAS)
    assert by["LADWP"]["eia"] == "LDWP" and by["PGE"]["eia_name"] == "Portland General Electric Company"
    assert by["CA ISO-TAC"]["kind"] == "system" and by["CA ISO-TAC"]["scored"] is True
    assert by["SCE-TAC"]["kind"] == "tac" and by["SCE-TAC"]["scored"] is False
    assert by["BANCRDNG"]["kind"] == "sub_area" and by["BANCRDNG"]["parent"] == "BANC"
    assert [a["code"] for a in b["areas"] if a["df_available"]] == [c for c, _e in lo.BA_ACTUALS]
    assert sorted(c for c, a in by.items() if a["kind"] == "weim_ba" and a["scored"]) == [
        "BANC", "BPAT", "EPE", "IPCO", "NWMT", "PACW", "PGE", "PNM", "PSEI", "SCL", "TPWR"]
    assert by["NEVP"]["not_scored_reason"].endswith("shifted by an hour")


# ═══ L4 ═════════════════════════════════════════════════════════════════════

def test_L4_no_hour_beyond_d_plus_7_production(get):
    b = get("/api/load/outlook?area=CA%20ISO-TAC")[0].json()
    end = lo.pacific_day_bounds(TODAY + timedelta(days=7))[1]
    assert max(datetime.fromisoformat(h["target_ts"]) for h in b["hours"]) < end
    assert max(d["days_ahead"] for d in b["days"]) <= 7


def test_L4_rows_past_d_plus_7_are_dropped():
    b = lo.build_outlook(area="PGE-TAC", now=NOW, fcst_rows=synthetic_products(extra_7da_days=3),
                         df_rows=[], actual_rows=[], usability_row=None)
    end = lo.pacific_day_bounds(TODAY + timedelta(days=7))[1]
    assert all(datetime.fromisoformat(h["target_ts"]) < end for h in b["hours"])
    assert [d["days_ahead"] for d in b["days"]] == list(range(8))


def test_L4_the_read_stops_at_d_plus_7(get):
    _r, pool = get("/api/load/outlook?area=BPAT")
    p = next(p for q, p in pool.calls if "AS p(product, dataset)" in q)
    assert p["hi"] == lo.pacific_day_bounds(TODAY + timedelta(days=7))[1]


# ═══ N1 ═════════════════════════════════════════════════════════════════════

def test_N1_production_net_demand_null_wherever_a_part_is(get):
    b = get("/api/load/net-demand?area=CISO")[0].json()
    assert any(h["net_demand_mw"] is not None for h in b["hours"])
    for h in b["hours"]:
        missing = {p for p in ("load", "solar", "wind") if h[f"{p}_mw"] is None}
        if missing:
            assert h["net_demand_mw"] is None
            assert {a["part"] for a in h["absent_part"]} == missing
            assert all(a["reason"] for a in h["absent_part"])
        else:
            assert h["absent_part"] is None
            assert h["net_demand_mw"] == pytest.approx(h["load_mw"] - h["solar_mw"] - h["wind_mw"], abs=0.01)
    assert "registry_mw" not in str(b["hours"])          # never a registry part


def test_N1_production_stops_name_part_and_lead(get):
    b = get("/api/load/net-demand?area=CISO")[0].json()
    assert b["stops"]["wind"]["last_lead_h"] == 66
    assert b["stops"]["wind"]["stop"]["first_absent_lead_h"] == 67
    assert b["stops"]["solar"]["stop"]["reason"] == "beyond_fitted_leads"
    assert b["stops"]["solar"]["last_lead_h"] == 66
    assert [g["first_absent_lead_h"] for g in b["stops"]["solar"]["gaps"]] == [22, 46]
    nd = b["net_demand_stop"]
    assert nd["stop"]["first_absent_ts"] == "2026-10-07T12:00:00+00:00"
    assert "solar" in nd["sentence"] and "lead 67 h" in nd["sentence"]
    assert len(nd["gaps"]) == 2 and nd["gaps"][0]["parts"][0]["part"] == "solar"


def _row(lead, reg, cal, cid):
    return {"lead_h": lead, "registry_mw": reg, "calibrated_mw": cal, "calibration_id": cid}


def test_N1_gate():
    lines = {1: {"fit_lead_min": 7, "fit_lead_max": 21}, 2: {"fit_lead_min": None, "fit_lead_max": None}}
    assert lo.gate(_row(10, 500, 450, 1), lines) == (450.0, None)
    assert lo.gate(_row(10, 500, None, None), lines) == (None, "registry_only")
    assert lo.gate(_row(23, 500, 450, 1), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(6, 0, 0, 1), lines) == (0.0, None)              # night, inside the line's reach
    assert lo.gate(_row(23, 0, 0, 1), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(10, 500, 450, 2), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(10, 500, 450, 9), lines) == (None, "no_line")


def test_N1_a_registry_part_stops_net_demand(bank):
    gen = {p: [dict(r) for r in bank["gen"][p]] for p in ("solar", "wind")}
    target = gen["wind"][3]["target_ts"]
    gen["wind"][3]["calibrated_mw"] = None
    gen["wind"][3]["calibration_id"] = None
    b = lo.build_net_demand(area="CISO", now=NOW, fcst_rows=bank["fcst"]["CA ISO-TAC"],
                            hub_rows=bank["hub"], truth_rows=bank["truth"], newest=bank["newest"],
                            gen_rows=gen, bt_gen_rows=bank["bt"], line_rows=bank["lines"])
    h = next(h for h in b["hours"] if h["target_ts"] == target.isoformat())
    assert h["net_demand_mw"] is None and h["wind_mw"] is None
    assert h["absent_part"] == [{"part": "wind", "reason": "registry_only"}]


# ═══ N2 ═════════════════════════════════════════════════════════════════════

def _bt_world(n_days):
    days = lo.score_days(NOW)[-n_days:]
    dam, truth, gen = {}, {}, {"solar": [], "wind": []}
    lines = {1: {"calibration_id": 1, "fit_lead_min": 1, "fit_lead_max": 66}}
    for d in days:
        for h in lo.day_hours(d):
            dam[h] = 30000.0
            truth[h] = 25000.0
        for hour, solar, wind in ((6, 9999.0, 9999.0), (12, 3000.0, 1000.0), (18, 7777.0, 7777.0)):
            init = datetime.combine(d - timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=hour)
            for h in lo.day_hours(d):
                lead = int((h - init).total_seconds() // 3600) + 1
                for part, v in (("solar", solar), ("wind", wind)):
                    gen[part].append({"init_ts": init, "target_ts": h, "lead_h": lead,
                                      "lead_band": "x", "registry_mw": v, "calibrated_mw": v,
                                      "calibration_id": 1})
    return days, dam, truth, gen, lines


def test_N2_pairs_dam_load_with_d_minus_1_12z():
    days, dam, truth, gen, lines = _bt_world(14)
    bt = lo.backtest(days=days, dam_load=dam, caiso_net={}, truth=truth, gen_rows=gen, lines=lines)
    ours = bt["ours"]
    assert ours["status"] == "scored" and ours["n_days"] == 14
    # 30000 - 3000 - 1000 = 26000 against 25000: the 12Z figures, not the 06Z or 18Z ones
    assert ours["bias_mw"] == pytest.approx(1000.0)
    assert ours["mae_mw"] == pytest.approx(1000.0)


def test_N2_the_inits_are_12z_of_d_minus_1(get):
    days = lo.score_days(NOW)
    assert lo.backtest_inits(days)[0] == datetime(2026, 9, 5, 12, tzinfo=UTC)
    assert all(i.hour == 12 and (d - i.date()).days == 1 for d, i in zip(days, lo.backtest_inits(days)))
    _r, pool = get("/api/load/net-demand?area=CISO")
    bt_calls = [p for q, p in pool.calls if "ANY(%(inits)s)" in q and len(p["inits"]) > 1]
    assert len(bt_calls) == 2 and all(p["inits"] == lo.backtest_inits(days) for p in bt_calls)


def test_N2_a_day_without_12z_or_calibration_is_not_scored_and_says_which_part():
    days, dam, truth, gen, lines = _bt_world(14)
    d0 = days[0]
    init12 = lo.backtest_inits([d0])[0]
    gen["wind"] = [r for r in gen["wind"] if r["init_ts"] != init12]
    for r in gen["solar"]:
        if r["init_ts"] == init12:
            r["calibrated_mw"] = None
    bt = lo.backtest(days=days, dam_load=dam, caiso_net={}, truth=truth, gen_rows=gen, lines=lines)
    assert bt["ours"]["n_days"] == 13 and bt["ours"]["status"] == "not_yet_scored"
    u = bt["unscored_days"][0]
    assert u["day"] == d0.isoformat()
    assert {(s["part"], s["reason"]) for s in u["stopped_by"]} == {
        ("solar", "registry_only"), ("wind", "no_12z_issuance")}


def test_N2_production_scores_no_day_and_says_why(get):
    b = get("/api/load/net-demand?area=CISO")[0].json()
    assert b["scores"]["ours"]["status"] == "not_yet_scored"
    assert b["scores"]["ours"]["n_days"] == 0
    assert len(b["unscored_days"]) == 28
    reasons = {(s["part"], s["reason"]) for u in b["unscored_days"] for s in u["stopped_by"]}
    assert reasons == {("solar", "no_12z_issuance"), ("solar", "registry_only"),
                       ("wind", "no_12z_issuance")}
    assert b["pairing_rule"] == lo.PAIRING_RULE and "12Z issuance of D-1" in b["pairing_rule"]


# ═══ N3 ═════════════════════════════════════════════════════════════════════

def test_N3_caiso_da_net_demand():
    ts = datetime(2026, 10, 5, 20, tzinfo=UTC)
    hub = {ts: {"NP15:Solar": 100.0, "ZP26:Solar": 200.0, "SP15:Solar": 300.0,
                "NP15:Wind": 10.0, "ZP26:Wind": 20.0, "SP15:Wind": 30.0, "AVA_:Solar": 5000.0}}
    assert lo.caiso_da_net({ts: 30000.0}, hub) == {ts: 30000.0 - 660.0}
    del hub[ts]["ZP26:Wind"]
    assert lo.caiso_da_net({ts: 30000.0}, hub) == {}


def test_N3_hub_read_is_the_six_series():
    got = set(re.findall(r"\('([A-Z0-9]+:(?:Solar|Wind))'\)", lo.HUB_DAM_SQL))
    assert got == {f"{h}:{t}" for h in ("NP15", "ZP26", "SP15") for t in ("Solar", "Wind")}


def test_N3_production_reference_and_scope(get):
    b = get("/api/load/net-demand?area=CISO")[0].json()
    s = b["scores"]["caiso_da"]
    assert s["status"] == "scored" and s["n_days"] == 24          # hubs banked from 09-10
    assert s["bias_mw"] == pytest.approx(1833, abs=1)              # Step 1, by SQL
    assert s["scope_note"] == lo.REFERENCE_SCOPE_NOTE
    h = next(h for h in b["hours"] if h["target_ts"] == "2026-10-05T20:00:00+00:00")
    assert h["caiso_da_net_demand_mw"] is not None


# ═══ N4 ═════════════════════════════════════════════════════════════════════

def _series(days, f=1000.0, a=990.0):
    fc, act = {}, {}
    for d in days:
        for h in lo.day_hours(d):
            fc[h], act[h] = f, a
    return fc, act


def test_N4_load_scores_need_14_days():
    days = lo.score_days(NOW)
    fc, act = _series(days[-13:])
    s = lo.score_series(fc, act, days)
    assert s["status"] == "not_yet_scored" and s["n_days"] == 13 and s["text"] == "not yet scored"
    assert "mae_mw" not in s
    fc, act = _series(days[-14:])
    s = lo.score_series(fc, act, days)
    assert s["status"] == "scored" and s["n_days"] == 14 and s["mae_mw"] == pytest.approx(10.0)


def test_N4_a_day_with_a_missing_hour_is_not_a_scored_day():
    days = lo.score_days(NOW)
    fc, act = _series(days[-14:])
    del act[lo.day_hours(days[-1])[5]]
    assert lo.score_series(fc, act, days)["n_days"] == 13


def test_N4_net_demand_scores_need_14_days():
    for n, status in ((13, "not_yet_scored"), (14, "scored")):
        days, dam, truth, gen, lines = _bt_world(n)
        bt = lo.backtest(days=lo.score_days(NOW), dam_load=dam, caiso_net={}, truth=truth,
                         gen_rows=gen, lines=lines)
        assert bt["ours"]["status"] == status and bt["ours"]["n_days"] == n


# ═══ P1 ═════════════════════════════════════════════════════════════════════

PLANS = (RECEIPTS / "plans.txt").read_text()


def test_P1_no_sequential_scan_of_timeseries_values():
    assert "Seq Scan on timeseries_values" not in PLANS
    assert "Seq Scan" not in PLANS


def test_P1_every_statement_has_a_plan():
    for name in ("FCST_SQL", "SERIES_SQL (EIA-930 DF", "SERIES_SQL (native", "SERIES_SQL (EIA-930 D,",
                 "USABILITY_SQL", "HUB_DAM_SQL", "TRUTH_SQL", "GEN_NEWEST_SQL", "GEN_ROWS_SQL (solar, the newest",
                 "GEN_ROWS_SQL (solar, the backtest", "LINES_SQL"):
        assert f"== {name}" in PLANS, name


def test_P1_every_timeseries_read_names_dataset_and_series():
    for cond in re.findall(r"on timeseries_values \w+ .*\n\s+Index Cond: (.*)", PLANS):
        assert "dataset =" in cond and "series =" in cond, cond
    for sql in (lo.FCST_SQL, lo.SERIES_SQL, lo.USABILITY_SQL, lo.HUB_DAM_SQL, lo.TRUTH_SQL):
        for block in sql.split("FROM timeseries_values t")[1:]:
            assert "t.dataset =" in block and "t.series =" in block


def test_P1_the_correlation_join_hashes_on_area_and_ts():
    assert "Hash Cond: ((ds.area = f_1.area) AND (ds.fts = f_1.ts))" in PLANS
    assert "ds AS MATERIALIZED" in lo.USABILITY_SQL


# ═══ the memo (D-09-25-138), the timeout, the 400s ══════════════════════════

def test_stale_cap_blocks_past_max_stale():
    builds = []

    async def build():
        builds.append(1)
        return {"n": len(builds)}

    c = main._DDCache("t", 10.0, allow_stale=True, max_stale_s=60.0)

    async def run():
        p, state, e = await c.serve("k", build)
        assert state == "miss"
        e.built_mono = time.monotonic() - 30          # past the ttl, inside the cap
        p, state, _e = await c.serve("k", build)
        assert state == "stale" and p == {"n": 1}
        await asyncio.sleep(0)
        await asyncio.gather(*list(c._tasks))
        c._entries["k"].built_mono = time.monotonic() - 120   # past the cap
        p, state, _e = await c.serve("k", build)
        assert state == "miss" and p == {"n": 3}

    asyncio.run(run())


def test_the_load_memos_carry_the_cap():
    for c in main._LOAD_CACHES:
        assert c.max_stale_s == 900.0 and c.allow_stale and c.ttl == 300.0


def test_reads_run_after_the_statement_timeout(get):
    for path in ("/api/load/outlook?area=BPAT", "/api/load/areas", "/api/load/net-demand"):
        _r, pool = get(path)
        qs = pool.sql_run()
        assert qs[0].startswith("SET LOCAL statement_timeout = '5s'"), path
        assert len(qs) > 1


def test_400s(get):
    assert get("/api/load/outlook")[0].status_code == 400
    assert get("/api/load/outlook?area=PGE-TACX")[0].status_code == 400
    r = get("/api/load/net-demand?area=PACE")[0]
    assert r.status_code == 400 and "D-09-25-140 clause 5" in r.text


def test_label_and_attributions_verbatim(get):
    b = get("/api/load/net-demand?area=CISO")[0].json()
    assert b["label"] == lo.NET_DEMAND_LABEL
    assert b["attributions"] == [lo.ATTRIBUTION_CAISO, lo.WIND_ATTRIBUTION]
    import wind_outlook
    assert lo.WIND_ATTRIBUTION == wind_outlook.ATTRIBUTION
    o = get("/api/load/outlook?area=BPAT")[0].json()
    assert o["label"] == lo.LABEL and o["attributions"] == [lo.ATTRIBUTION_CAISO, lo.ATTRIBUTION_EIA]


def test_samples_are_the_routes_answer(get):
    """The bodies banked for d091612 are what the routes serve over the bank."""
    import json
    for name, path in (("sample_outlook_ca_iso_tac.json", "/api/load/outlook?area=CA%20ISO-TAC"),
                       ("sample_net_demand_ciso.json", "/api/load/net-demand?area=CISO"),
                       ("sample_areas.json", "/api/load/areas")):
        body = get(path)[0].json()
        body.pop("cache")
        assert body == json.loads((RECEIPTS / name).read_text()), name
