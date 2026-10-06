"""d091623 — net demand says why it is not yet scored, and says it truly.

Books no new ruling: D-09-25-140 (net demand names the part that stops it),
D-09-25-127 (a calibrated figure only where its line was fitted on the
hour's lead).

  R1  on the 10-06 replay, every absence whose row carries a line and no
      figure reads beyond_fitted_leads: the 10-05 day's solar stop, the stop
      sentence and the gap
  R2  the gate's table on synthetic rows, and test_N1_gate's assertions
  R3  scores.ours.reason: pinned on the replay, its numbers the served fields,
      absent from a scored cell, naming a 13-day world's one unscored day
  R5  the mutations: docs/receipts/net-demand-reason-d091623/rehearse.py

The route tests run main.py over load_bank_d091623's pool: production's rows
at 2026-10-06T12:40Z, after the gated writer (pantry d091607). R4 is
tests/test_load_outlook.py and tests/test_outlook_fit_d091608.py, unchanged,
over the 10-05 replay.
"""

import json
import pathlib
import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import load_bank_d091623 as lb
import load_outlook as lo
import main
from test_load_outlook import _bt_world

UTC = timezone.utc
NOW = lb.NOW                                   # 2026-10-06T12:40Z; Pacific today 10-06
HERE = pathlib.Path(__file__).resolve().parent
RECEIPTS = HERE.parent / "docs" / "receipts" / "net-demand-reason-d091623"

REASON_10_06 = (
    "0 of 14 days scored; the newest unscored day, 2026-10-05, stopped on solar: "
    "the hour's lead is outside the leads its line was fitted on "
    "(4 h from 02:00 PT, lead 22 h); most often wind: no 12Z issuance of D-1 is banked, "
    "on 27 of 28 unscored days")


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


@pytest.fixture
def body(monkeypatch, bank):
    monkeypatch.setattr(main, "_pool", lb.pool(bank))
    r = TestClient(main.app).get("/api/load/net-demand?area=CISO")
    assert r.status_code == 200
    return r.json()


def _lines(bank):
    return {int(l["calibration_id"]): l for l in bank["lines"]}


def _inside(line, lead):
    return line["fit_lead_min"] is not None and line["fit_lead_min"] <= lead <= line["fit_lead_max"]


# ═══ R1 ═════════════════════════════════════════════════════════════════════

def test_R1_the_bank_is_the_gated_writer_s(bank):
    """What the brief measured over CISO holds on this replay: a row that keeps
    its line and has no figure is outside that line's fitted leads, and no
    figure is outside its line's fitted leads."""
    lines = _lines(bank)
    rows = [r for k in ("gen", "bt") for p in ("solar", "wind") for r in bank[k][p]]
    kept = [r for r in rows if r["calibration_id"] is not None and r["calibrated_mw"] is None]
    assert kept, "the replay carries writer-gated rows"
    assert not [r for r in kept if _inside(lines[r["calibration_id"]], r["lead_h"])]
    assert not [r for r in rows if r["calibrated_mw"] is not None
                and not _inside(lines[r["calibration_id"]], r["lead_h"])]


def test_R1_every_line_carrying_absence_reads_beyond_the_fit(bank):
    lines = _lines(bank)
    n = 0
    for k in ("gen", "bt"):
        for p in ("solar", "wind"):
            for r in bank[k][p]:
                if r["calibration_id"] is not None and r["calibrated_mw"] is None:
                    assert lo.gate(r, lines) == (None, "beyond_fitted_leads"), r
                    n += 1
    assert n > 0


def test_R1_the_hours_name_it_too(body, bank):
    rows = {p: {r["target_ts"].isoformat(): r for r in bank["gen"][p]} for p in ("solar", "wind")}
    for h in body["hours"]:
        for part in ("solar", "wind"):
            r = rows[part].get(h["target_ts"])
            if r and r["calibration_id"] is not None and r["calibrated_mw"] is None:
                assert h[f"{part}_mw"] is None
                assert h[f"{part}_absent_reason"] == "beyond_fitted_leads", (part, h["target_ts"])


def test_R1_the_10_05_day_stops_on_solar_beyond_the_fit(body):
    u = body["unscored_days"][-1]
    assert u["day"] == "2026-10-05"
    assert u["stopped_by"] == [{
        "part": "solar", "reason": "beyond_fitted_leads",
        "init_ts": "2026-10-04T12:00:00+00:00", "first_ts": "2026-10-05T09:00:00+00:00",
        "lead_h": 22, "hours_absent": 4,
        "detail": "the hour's lead is outside the leads its line was fitted on"}]


def test_R1_the_stop_sentence_and_the_gap(body):
    nd = body["net_demand_stop"]
    assert nd["sentence"] == (
        "Net demand stops at 2026-10-09T00:00:00+00:00: "
        "solar (the hour's lead is outside the leads its line was fitted on, lead 67 h); "
        "wind (the hour's lead is outside the leads its line was fitted on, lead 67 h).")
    assert "registry only" not in nd["sentence"]
    assert len(nd["gaps"]) == 1
    g = nd["gaps"][0]
    assert (g["first_absent_ts"], g["hours"]) == ("2026-10-08T03:00:00+00:00", 4)
    assert g["parts"] == [{"part": "solar", "reason": "beyond_fitted_leads",
                           "detail": "the hour's lead is outside the leads its line was fitted on"}]
    for p in ("solar", "wind"):
        assert body["stops"][p]["stop"]["reason"] == "beyond_fitted_leads"
        assert body["stops"][p]["stop"]["first_absent_lead_h"] == 67
    assert [(x["first_absent_lead_h"], x["hours"], x["reason"])
            for x in body["stops"]["solar"]["gaps"]] == [(46, 4, "beyond_fitted_leads")]


def test_R1_the_tally_moves_two_days_from_registry_to_the_fit(body):
    tally: dict = {}
    for u in body["unscored_days"]:
        for s in u["stopped_by"]:
            tally[(s["part"], s["reason"])] = tally.get((s["part"], s["reason"]), 0) + 1
    assert tally == {("wind", "no_12z_issuance"): 27, ("solar", "registry_only"): 19,
                     ("solar", "beyond_fitted_leads"): 2, ("solar", "no_12z_issuance"): 7}


# ═══ R2 ═════════════════════════════════════════════════════════════════════

def _row(lead, reg, cal, cid):
    return {"lead_h": lead, "registry_mw": reg, "calibrated_mw": cal, "calibration_id": cid}


LINES = {1: {"fit_lead_min": 7, "fit_lead_max": 21}, 2: {"fit_lead_min": None, "fit_lead_max": None}}


@pytest.mark.parametrize("row, want", [
    (_row(10, 500, None, None), (None, "registry_only")),        # no id
    (_row(10, 500, None, 9), (None, "no_line")),                 # an id with no banked line
    (_row(23, 500, None, 1), (None, "beyond_fitted_leads")),     # no figure, lead after the fit
    (_row(6, 500, None, 1), (None, "beyond_fitted_leads")),      # no figure, lead before the fit
    (_row(10, 500, None, 1), (None, "registry_only")),           # no figure, lead inside
    (_row(10, 0, None, 1), (None, "registry_only")),             # dark, no figure, lead inside
    (_row(10, 500, None, 2), (None, "beyond_fitted_leads")),     # a line fitted on no lead
])
def test_R2_the_gate_reads_the_line_before_the_figure(row, want):
    assert lo.gate(row, LINES) == want


def test_R2_test_N1_gate_still_holds():
    """Every assertion test_N1_gate makes on main."""
    lines = LINES
    assert lo.gate(_row(10, 500, 450, 1), lines) == (450.0, None)
    assert lo.gate(_row(10, 500, None, None), lines) == (None, "registry_only")
    assert lo.gate(_row(23, 500, 450, 1), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(6, 0, 0, 1), lines) == (0.0, None)              # night, inside the line's reach
    assert lo.gate(_row(23, 0, 0, 1), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(10, 500, 450, 2), lines) == (None, "beyond_fitted_leads")
    assert lo.gate(_row(10, 500, 450, 9), lines) == (None, "no_line")


def test_R2_a_figure_without_an_id_is_still_registry_only():
    assert lo.gate(_row(10, 500, 450, None), LINES) == (None, "registry_only")


# ═══ R3 ═════════════════════════════════════════════════════════════════════

def test_R3_the_reason_on_the_replay(body):
    ours = body["scores"]["ours"]
    assert ours["status"] == "not_yet_scored" and ours["text"] == "not yet scored"
    assert ours["reason"] == REASON_10_06
    assert not ours["reason"].startswith("not yet scored")
    assert "reason" not in body["scores"]["caiso_da"]


def test_R3_every_number_in_it_is_a_served_field(body):
    ours, unscored = body["scores"]["ours"], body["unscored_days"]
    reason = ours["reason"]
    assert reason.startswith(f"{ours['n_days']} of {ours['min_days']} days scored; ")
    newest = unscored[-1]
    s = newest["stopped_by"][0]
    first_pt = datetime.fromisoformat(s["first_ts"]).astimezone(lo.PT).strftime("%H:%M")
    assert (f"the newest unscored day, {newest['day']}, stopped on {s['part']}: {s['detail']} "
            f"({s['hours_absent']} h from {first_pt} PT, lead {s['lead_h']} h)") in reason
    k = sum(1 for u in unscored if any((x["part"], x["reason"]) == ("wind", "no_12z_issuance")
                                       for x in u["stopped_by"]))
    assert reason.endswith(f"on {k} of {len(unscored)} unscored days")
    served = {str(ours["n_days"]), str(ours["min_days"]), str(s["hours_absent"]), str(s["lead_h"]),
              str(k), str(len(unscored))}
    nums = set(re.findall(r"(?<![\d-])\d+(?![\d-])", reason.replace(first_pt, "")))
    assert nums <= served | {"12"}                                   # "12Z" is the detail's word


def test_R3_a_scored_cell_has_no_reason():
    days, dam, truth, gen, lines = _bt_world(14)
    bt = lo.backtest(days=days, dam_load=dam, caiso_net={}, truth=truth, gen_rows=gen, lines=lines)
    assert bt["ours"]["status"] == "scored"
    assert "reason" not in bt["ours"]


def test_R3_a_13_day_world_names_its_one_unscored_day_and_part():
    days, dam, truth, gen, lines = _bt_world(14)
    d = days[-1]
    init12 = lo.backtest_inits([d])[0]
    gen["wind"] = [r for r in gen["wind"] if r["init_ts"] != init12]
    bt = lo.backtest(days=days, dam_load=dam, caiso_net={}, truth=truth, gen_rows=gen, lines=lines)
    assert bt["ours"]["n_days"] == 13 and bt["ours"]["status"] == "not_yet_scored"
    assert bt["ours"]["reason"] == (
        f"13 of 14 days scored; the newest unscored day, {d.isoformat()}, stopped on wind: "
        "no 12Z issuance of D-1 is banked; most often wind: no 12Z issuance of D-1 is banked, "
        "on 1 of 1 unscored days")


def test_R3_every_part_that_stopped_the_day_is_named():
    days, dam, truth, gen, lines = _bt_world(14)
    d = days[-1]
    init12 = lo.backtest_inits([d])[0]
    gen["wind"] = [r for r in gen["wind"] if r["init_ts"] != init12]
    first = lo.day_hours(d)[3]
    for r in gen["solar"]:
        if r["init_ts"] == init12 and first <= r["target_ts"] < first + timedelta(hours=2):
            r["calibrated_mw"] = None
    bt = lo.backtest(days=days, dam_load=dam, caiso_net={}, truth=truth, gen_rows=gen, lines=lines)
    lead = int((first - init12).total_seconds() // 3600) + 1
    assert bt["ours"]["reason"] == (
        f"13 of 14 days scored; the newest unscored day, {d.isoformat()}, stopped on "
        f"solar: the hour carries no calibrated figure (registry only) (2 h from 03:00 PT, "
        f"lead {lead} h) and wind: no 12Z issuance of D-1 is banked; most often solar: the hour "
        "carries no calibrated figure (registry only), on 1 of 1 unscored days")


def test_R3_ties_go_by_part_order_then_reason():
    unscored = [
        {"day": "2026-10-01", "stopped_by": [{"part": "wind", "reason": "no_12z_issuance",
                                              "detail": lo.ABSENT["no_12z_issuance"]}]},
        {"day": "2026-10-02", "stopped_by": [{"part": "truth", "reason": "no_truth",
                                              "detail": lo.ABSENT["no_truth"]}]},
        {"day": "2026-10-03", "stopped_by": [{"part": "solar", "reason": "no_line",
                                              "detail": lo.ABSENT["no_line"]}]},
        {"day": "2026-10-04", "stopped_by": [{"part": "solar", "reason": "beyond_fitted_leads",
                                              "detail": lo.ABSENT["beyond_fitted_leads"]}]},
    ]
    r = lo.not_scored_reason({"n_days": 0, "min_days": 14}, unscored)
    assert r.endswith("most often solar: the hour's lead is outside the leads its line was "
                      "fitted on, on 1 of 4 unscored days")
    unscored.append({"day": "2026-10-05", "stopped_by": [{"part": "load", "reason": "no_dam_load",
                                                          "detail": lo.ABSENT["no_dam_load"]}]})
    r = lo.not_scored_reason({"n_days": 0, "min_days": 14}, unscored)
    assert "the newest unscored day, 2026-10-05, stopped on load: CAISO's DAM load is not " \
           "banked for every hour; " in r
    assert r.endswith("most often load: CAISO's DAM load is not banked for every hour, "
                      "on 1 of 5 unscored days")


def test_R3_the_route_docstring_names_reason():
    doc = main.load_net_demand.__doc__
    assert "reason" in doc and "not yet scored: " in doc


# ═══ samples ════════════════════════════════════════════════════════════════

def test_samples_are_the_routes_answer(monkeypatch, bank):
    """The bodies banked at the 10-06 NOW are what the routes serve over the bank."""
    monkeypatch.setattr(main, "_pool", lb.pool(bank))
    for name, path in (("sample_net_demand_ciso.json", "/api/load/net-demand?area=CISO"),
                       ("sample_outlook_ca_iso_tac.json", "/api/load/outlook?area=CA%20ISO-TAC"),
                       ("sample_areas.json", "/api/load/areas")):
        for c in main._LOAD_CACHES:
            c.clear()
        got = TestClient(main.app).get(path).json()
        got.pop("cache")
        assert got == json.loads((RECEIPTS / name).read_text()), name
