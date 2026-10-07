"""d091608 (D-09-25-136 route half, D-09-25-138) — the outlook routes read the
stored fit, gate solar, and stop serving yesterday.

  A1  solar: a lit hour beyond its line's fitted leads is served
      calibrated_mw null with "beyond_fitted_leads"; inside, the stored figure
  A2  a dark hour beyond the fit opens no gap and moves no seam; a lit run
      beyond the fit is one gap, with its band and the line's fitted leads
  A3  wind's body equals main's over the same production rows, except the
      fitted-lead receipts (fit_rows, the basis) and the fields the rulings add
  A4  a line with null fitted leads covers nothing (both techs, one gate)
  A5  each score carries lead_min/lead_max (null where the row has none) and
      passes the hours rule through when present
  A6  _DDCache: fresh inside ttl; stale with one refresh inside ttl +
      max_stale_s; beyond it the request builds (miss), single-flight
  A7  the degree-day cumulative cache keeps its unbounded stale serve; the
      four outlook/sites caches are the only ones bounded

Route tests run main.py on test_solar_outlook's FakePool; the PG tests run the
SQL on a real Postgres (skipped where no initdb). Reds:
docs/receipts/outlook-fit-d091608/reds.txt.
"""

import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import solar_outlook as so
import wind_outlook as wo
from test_solar_outlook import FakePool

UTC = timezone.utc
INIT = datetime(2026, 10, 4, 18, tzinfo=UTC)
PREV = datetime(2026, 10, 4, 12, tzinfo=UTC)
HERE = pathlib.Path(__file__).resolve().parent
WIND_FIX = HERE / "fixtures" / "wind_outlook_d091590"
RECEIPTS = HERE.parent / "docs" / "receipts"


@pytest.fixture(autouse=True)
def _cold_memos():
    for c in (main._solar_outlook_cache, main._wind_outlook_cache):
        c.clear()
    yield
    for c in (main._solar_outlook_cache, main._wind_outlook_cache):
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def band_of(lead):
    for name, lo, hi in (("h01_06", 1, 6), ("h07_24", 7, 24), ("h25_48", 25, 48),
                         ("h49_120", 49, 120), ("h121_240", 121, 240)):
        if lo <= lead <= hi:
            return name


LINE_IDS = {"h01_06": 11, "h07_24": 12, "h25_48": 13, "h49_120": 14}


def solar_hours(n=72, *, dark=(), calibrated_through=120):
    """A solar issuance with every band's line applied to every hour of the
    band (as the ungated solar writer stores it). `dark` leads have
    registry_mw 0 and, as the writer stores them, calibrated 0."""
    out = []
    for lead in range(1, n + 1):
        band = band_of(lead)
        reg = 0.0 if lead in dark else 1000.0 + lead
        cal = (round(reg * 0.8, 3) if lead <= calibrated_through else None)
        out.append({
            "target_ts": INIT + timedelta(hours=lead - 1), "lead_h": lead, "lead_band": band,
            "weather_step_h": 1, "registry_mw": reg, "calibrated_mw": cal,
            "calibration_id": LINE_IDS.get(band) if cal is not None else None,
            "outage_mw_subtracted": 0.0, "ac_mw_total": 24218.0, "n_sites": 1029,
            "source_posted_ts": INIT + timedelta(hours=4), "method_version": "solar_pv_v1",
            "prev_registry_mw": reg, "prev_calibrated_mw": cal,
            "prev_calibration_id": LINE_IDS.get(band) if cal is not None else None,
            "prev_lead_h": lead, "prev_method_version": "solar_pv_v1",
        })
    return out


def line(band, lo, hi, *, area="HUBSUM"):
    return {"calibration_id": LINE_IDS[band], "area": area, "lead_band": band,
            "intercept_mw": 0.0, "slope": 0.8, "fit_start": date(2026, 9, 6),
            "fit_end": date(2026, 10, 3), "n_hours": 300, "n_days": 28,
            "fitted_at": datetime(2026, 10, 4, 20, tzinfo=UTC), "method_version": "solar_pv_v1",
            "fit_lead_min": lo, "fit_lead_max": hi,
            "fit_rows": None if lo is None else 300}


def solar_lines(h07=(7, 21), h25=(26, 45), h49=(50, 66)):
    return [line("h01_06", 1, 6), line("h07_24", *h07), line("h25_48", *h25), line("h49_120", *h49)]


def solar_pool(*, hours, lines, scores=()):
    return FakePool([
        ("SET LOCAL", []),
        ("AS prev_init_ts", [{"init_ts": INIT, "prev_init_ts": PREV}] if hours else []),
        ("AS prev_registry_mw", hours),
        ("FROM implied_gen_scores", list(scores)),
        ("FROM implied_gen_calibration", list(lines)),
        ("FROM timeseries_values", []),
        ("FROM implied_gen_sites", []),
    ])


def solar_body(client, monkeypatch, **kw):
    monkeypatch.setattr(main, "_pool", solar_pool(**kw))
    r = client.get("/api/generation/solar/outlook?area_kind=hub_sum")
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# A1 — solar is gated: a lit hour beyond the fit is null, with its reason
# ═══════════════════════════════════════════════════════════════════════════

def test_A1_solar_lit_hour_beyond_the_fit_is_null_inside_is_the_stored_figure(client, monkeypatch):
    body = solar_body(client, monkeypatch, hours=solar_hours(), lines=solar_lines())
    h = {x["lead_h"]: x for x in body["hours"]}
    for lead in (1, 6, 7, 21, 26, 45, 50, 66):                  # inside: the stored figure
        assert h[lead]["calibrated_mw"] == round((1000.0 + lead) * 0.8, 3), lead
        assert h[lead]["calibrated_absent_reason"] is None
    for lead in (22, 24, 25, 46, 48, 49, 67, 72):               # beyond: null, and why
        assert h[lead]["calibrated_mw"] is None, lead
        assert h[lead]["calibrated_absent_reason"] == "beyond_fitted_leads"
        assert h[lead]["registry_mw"] == 1000.0 + lead
        assert h[lead]["previous_mw"] == h[lead]["previous_registry_mw"]   # like for like
    # nothing of a withheld figure survives anywhere in the body
    assert str(round((1000.0 + 22) * 0.8, 3)) not in json.dumps(body)
    assert body["unscaled"] is False
    assert body["calibration"]["h07_24"]["fit_lead_max"] == 21
    assert body["calibration"]["h07_24"]["applied_lead_max"] == 24
    assert body["calibration_fit_leads_basis"] == "stored by the writer (pantry migration 272)"


def test_A1_solar_previous_figure_is_gated_by_its_own_line_at_its_own_lead(client, monkeypatch):
    hours = solar_hours()
    for x in hours:                       # the previous run reached this hour 6 h further out
        x["prev_lead_h"] = x["lead_h"] + 6
        x["prev_calibration_id"] = LINE_IDS.get(band_of(x["prev_lead_h"]))
    body = solar_body(client, monkeypatch, hours=hours, lines=solar_lines())
    h = {x["lead_h"]: x for x in body["hours"]}
    assert h[10]["calibrated_mw"] is not None and h[10]["previous_mw"] == h[10]["previous_calibrated_mw"]
    # lead 16 is fitted; the previous run's lead 22 is not: no like for like
    assert h[16]["calibrated_mw"] is not None
    assert h[16]["previous_calibrated_mw"] is None and h[16]["previous_mw"] is None


# ═══════════════════════════════════════════════════════════════════════════
# A2 — gaps and the seam over LIT hours only
# ═══════════════════════════════════════════════════════════════════════════

def test_A2_a_dark_hour_beyond_the_fit_opens_no_gap_and_moves_no_seam(client, monkeypatch):
    # h07_24 fitted 7-24 except that its hours 22-24 are dark: nothing lit is withheld there
    dark = (22, 23, 24)
    body = solar_body(client, monkeypatch, hours=solar_hours(n=24, dark=dark),
                      lines=solar_lines(h07=(7, 21)))
    h = {x["lead_h"]: x for x in body["hours"]}
    assert all(h[l]["calibrated_absent_reason"] == "beyond_fitted_leads" for l in dark)
    assert body["calibration_gaps"] == []
    assert body["seams"]["calibration"] is None              # every lit hour is calibrated


def test_A2_a_lit_run_beyond_the_fit_is_one_gap_with_its_band_and_fitted_leads(client, monkeypatch):
    # lit 22-24 beyond h07_24's fit, a dark hour inside the run does not split it
    hours = solar_hours(n=30, dark=(23,))
    body = solar_body(client, monkeypatch, hours=hours, lines=solar_lines(h07=(7, 21), h25=(25, 48)))
    assert body["calibration_gaps"] == [
        {"first_lead": 22, "last_lead": 24, "band": "h07_24", "reason": "beyond_fitted_leads",
         "fit_lead_min": 7, "fit_lead_max": 21}]
    seam = body["seams"]["calibration"]
    assert (seam["last_calibrated_lead"], seam["first_registry_lead"]) == (21, 22)
    assert seam["reason"] == "beyond_fitted_leads"


def test_A2_dark_hours_between_calibrated_and_withheld_do_not_move_the_seam(client, monkeypatch):
    # leads 20-21 dark (calibrated 0), 22 lit and beyond: the seam is at the last LIT calibrated hour
    body = solar_body(client, monkeypatch, hours=solar_hours(n=24, dark=(20, 21)),
                      lines=solar_lines(h07=(7, 21)))
    seam = body["seams"]["calibration"]
    assert (seam["last_calibrated_lead"], seam["first_registry_lead"]) == (19, 22)


def test_A2_a_change_of_line_closes_a_gap(client, monkeypatch):
    # 22-24 beyond h07_24 and 25 beyond h25_48: two lines, two gaps (production's shape)
    body = solar_body(client, monkeypatch, hours=solar_hours(n=30), lines=solar_lines())
    gaps = body["calibration_gaps"]
    assert [(g["first_lead"], g["last_lead"], g["band"], g["fit_lead_min"], g["fit_lead_max"])
            for g in gaps] == [(22, 24, "h07_24", 7, 21), (25, 25, "h25_48", 26, 45)]


def test_A2_no_line_hours_are_not_gaps_and_close_a_run(client, monkeypatch):
    hours = solar_hours(n=30)
    for x in hours:
        if x["lead_h"] == 23:                        # the writer stored nothing here
            x["calibrated_mw"], x["calibration_id"] = None, None
    body = solar_body(client, monkeypatch, hours=hours, lines=solar_lines(h25=(25, 48)))
    assert [(g["first_lead"], g["last_lead"]) for g in body["calibration_gaps"]] == [(22, 22), (24, 24)]


def test_A2_a_writer_gated_hour_that_keeps_its_line_id_reads_beyond_the_fit(client, monkeypatch):
    # what a gated writer stores if it keeps calibration_id (handback §7.2):
    # no figure, the line named. The route says why, and the run is a gap.
    hours = solar_hours(n=30)
    for x in hours:
        if 22 <= x["lead_h"] <= 24:
            x["calibrated_mw"] = None
    body = solar_body(client, monkeypatch, hours=hours, lines=solar_lines(h25=(25, 48)))
    h = {x["lead_h"]: x for x in body["hours"]}
    assert [h[l]["calibrated_absent_reason"] for l in (22, 23, 24)] == ["beyond_fitted_leads"] * 3
    assert [(g["first_lead"], g["last_lead"]) for g in body["calibration_gaps"]] == [(22, 24)]


def test_A2_wind_carries_calibration_gaps_from_the_same_function(client, monkeypatch):
    from test_wind_outlook import outlook_pool, lines_today
    monkeypatch.setattr(main, "_pool", outlook_pool(lines=lines_today(h49_max=66)))
    body = client.get("/api/generation/wind/outlook?area_kind=hub_sum").json()
    assert body["calibration_gaps"] == [
        {"first_lead": 67, "last_lead": 120, "band": "h49_120", "reason": "beyond_fitted_leads",
         "fit_lead_min": 49, "fit_lead_max": 66}]
    assert wo.gate is so.gate and wo.calibration_gaps is so.calibration_gaps
    assert wo.CALIBRATION_SQL is so.CALIBRATION_SQL


# ═══════════════════════════════════════════════════════════════════════════
# A3 — wind's body is main's, over the same production rows
# ═══════════════════════════════════════════════════════════════════════════

# What may differ, and why: the fitted leads' receipts now come from the line
# (D-09-25-136 clause 1: fit_rows is the writer's, fit_issuances had no stored
# counterpart, the source and the basis say so), and the two fields the
# rulings add (clause 4's calibration_gaps, clause 5's lead_min/lead_max).
A3_ALLOWED = ("calibration.{band}.fit_rows", "calibration.{band}.fit_issuances",
              "calibration.{band}.fit_leads_source", "calibration_fit_leads_basis",
              "calibration_gaps", "scores.{band}.{who}.lead_min", "scores.{band}.{who}.lead_max")


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else k
            if k not in a or k not in b:
                out.append(p)
            else:
                out += _diff(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [path]


def _allowed(p):
    import re
    pat = "|".join(re.escape(a).replace(r"\{band\}", r"\w+").replace(r"\{who\}", r"\w+")
                   for a in A3_ALLOWED)
    return re.fullmatch(pat, p) is not None


@pytest.mark.parametrize("area,q", [("NP15", "area_kind=hub&area=NP15"), ("ZP26", "area_kind=hub&area=ZP26"),
                                    ("SP15", "area_kind=hub&area=SP15"), ("HUBSUM", "area_kind=hub_sum"),
                                    ("CISO", "area_kind=ba&area=CISO")])
def test_A3_wind_body_is_mains_but_for_the_fit_receipts(client, monkeypatch, area, q):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wind_sample", RECEIPTS / "wind-outlook-api-d091590" / "sample.py")
    s = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(s)
    today = json.loads((WIND_FIX / "main_body_2026_10_05_00z.json").read_text())[area]
    monkeypatch.setattr(main, "_pool", s.outlook_pool(s.load(), area))
    body = client.get(f"/api/generation/wind/outlook?{q}").json()
    body.pop("cache")
    diffs = _diff(today, body)
    assert [d for d in diffs if not _allowed(d)] == []
    assert body["hours"] == today["hours"]                     # hour by hour, identical
    assert body["seams"] == today["seams"] and body["days"] == today["days"]
    for band, c in body["calibration"].items():
        if c is None:
            continue
        t = today["calibration"][band]
        assert (c["fit_lead_min"], c["fit_lead_max"]) == (t["fit_lead_min"], t["fit_lead_max"])
        assert c["fit_rows"] == c["n_hours"]                   # the writer's count is n_hours
        assert t["fit_rows"] >= c["fit_rows"]                  # main's could not see "an actual present"


# ═══════════════════════════════════════════════════════════════════════════
# A4 — a line with null fitted leads covers nothing
# ═══════════════════════════════════════════════════════════════════════════

def test_A4_null_fitted_leads_cover_nothing_on_solar(client, monkeypatch):
    lines = solar_lines()
    lines[1] = line("h07_24", None, None)
    body = solar_body(client, monkeypatch, hours=solar_hours(n=30), lines=lines)
    h07 = [x for x in body["hours"] if x["lead_band"] == "h07_24"]
    assert all(x["calibrated_mw"] is None and x["calibrated_absent_reason"] == "beyond_fitted_leads"
               for x in h07)
    assert body["calibration"]["h07_24"]["fit_lead_min"] is None
    assert body["calibration_gaps"][0] == {"first_lead": 7, "last_lead": 24, "band": "h07_24",
                                          "reason": "beyond_fitted_leads",
                                          "fit_lead_min": None, "fit_lead_max": None}


@pytest.mark.parametrize("lo,hi", [(None, None), (49, None), (None, 66)])
def test_A4_the_one_gate_refuses_a_half_known_fit(lo, hi):
    ln = {"calibration_id": 1, "fit_lead_min": lo, "fit_lead_max": hi}
    assert not any(so._fitted(ln, lead) for lead in range(1, 241))
    assert so._fitted({"calibration_id": 1, "fit_lead_min": 49, "fit_lead_max": 66}, 66)
    assert not so._fitted(None, 10) and not so._fitted({"fit_lead_min": 1, "fit_lead_max": 6}, None)


# ═══════════════════════════════════════════════════════════════════════════
# A5 — scores carry their leads and the hours rule
# ═══════════════════════════════════════════════════════════════════════════

def _score(band, who, **kw):
    return {"lead_band": band, "who": who, "area_kind": "hub_sum", "window_start": date(2026, 9, 7),
            "window_end": date(2026, 10, 3), "n_hours": 300, "n_days": 27, "scored": True,
            "bias_mw": 1.0, "mae_mw": 2.0, "mae_pct_installed": 3.0, "rmse_mw": 4.0, "r": 0.9,
            "scored_at": datetime(2026, 10, 4, 20, tzinfo=UTC), **kw}


def test_A5_scores_carry_their_leads_and_the_hours_rule_untouched(client, monkeypatch):
    rule = {"hours": "registry_mw > 0", "basis": "d091607", "n": [1, 2]}
    scores = [_score("h49_120", "calibrated", lead_min=50, lead_max=66, hours_rule=rule),
              _score("h49_120", "registry", lead_min=49, lead_max=66, hours_rule=None),
              _score("dam_comparable", "caiso_dam", lead_min=None, lead_max=None)]
    body = solar_body(client, monkeypatch, hours=solar_hours(), lines=solar_lines(), scores=scores)
    s = body["scores"]
    assert (s["h49_120"]["calibrated"]["lead_min"], s["h49_120"]["calibrated"]["lead_max"]) == (50, 66)
    assert s["h49_120"]["calibrated"]["hours_rule"] == rule              # untouched
    assert "hours_rule" not in s["h49_120"]["registry"]                  # none on the row: none here
    assert s["dam_comparable"]["caiso_dam"]["lead_min"] is None          # the scorer has not run on it
    assert s["dam_comparable"]["caiso_dam"]["lead_max"] is None


def test_A5_wind_scores_carry_them_beside_wind_s_columns(client, monkeypatch):
    from test_wind_outlook import outlook_pool, score_row
    monkeypatch.setattr(main, "_pool", outlook_pool(
        scores=[{**score_row("h49_120", "calibrated"), "lead_min": 49, "lead_max": 66,
                 "hours_rule": "lit"}]))
    s = client.get("/api/generation/wind/outlook?area_kind=hub_sum").json()["scores"]["h49_120"]["calibrated"]
    assert (s["lead_min"], s["lead_max"], s["hours_rule"], s["mw_yes"]) == (49, 66, "lit", 7183.3)


def test_A5_the_score_read_names_the_columns_and_reads_the_rule_through_jsonb():
    for m in (so, wo):
        for kind in so.AREA_KINDS:
            sql = " ".join(m.SCORES_SQL[kind].split())
            assert "s.lead_min, s.lead_max" in sql
            assert "to_jsonb(s) -> 'hours_rule' AS hours_rule" in sql


# ═══════════════════════════════════════════════════════════════════════════
# A6 — D-09-25-138: never more than ttl + max_stale_s old
# ═══════════════════════════════════════════════════════════════════════════

class _Builder:
    def __init__(self, delay=0.05):
        self.n, self.delay = 0, delay

    async def __call__(self):
        self.n += 1
        await asyncio.sleep(self.delay)
        return {"build": self.n}


def _age(cache, key, seconds):
    cache._entries[key].built_mono = time.monotonic() - seconds


def test_A6_fresh_inside_ttl():
    async def run():
        c, b = main._DDCache("t", 300.0, allow_stale=True, max_stale_s=900.0), _Builder()
        p1, s1, _ = await c.serve("k", b)
        _age(c, "k", 299)
        p2, s2, _ = await c.serve("k", b)
        return (s1, s2, p2, b.n)
    assert asyncio.run(run()) == ("miss", "fresh", {"build": 1}, 1)


def test_A6_stale_inside_the_window_with_one_refresh_behind_it():
    async def run():
        c, b = main._DDCache("t", 300.0, allow_stale=True, max_stale_s=900.0), _Builder()
        await c.serve("k", b)
        _age(c, "k", 300 + 899)
        got = await asyncio.gather(*(c.serve("k", b) for _ in range(5)))
        states = {s for _p, s, _e in got}
        payloads = {json.dumps(p) for p, _s, _e in got}
        await asyncio.gather(*list(c._tasks))
        p, s, _ = await c.serve("k", b)
        return states, payloads, b.n, s, p
    states, payloads, n, s, p = asyncio.run(run())
    assert states == {"stale"} and payloads == {'{"build": 1}'}     # the old payload, at once
    assert n == 2                                                     # one refresh, not five
    assert (s, p) == ("fresh", {"build": 2})


def test_A6_beyond_the_window_the_request_builds_single_flight():
    async def run():
        c, b = main._DDCache("t", 300.0, allow_stale=True, max_stale_s=900.0), _Builder()
        await c.serve("k", b)
        _age(c, "k", 300 + 900 + 1)
        got = await asyncio.gather(*(c.serve("k", b) for _ in range(8)))
        return [(s, p) for p, s, _e in got], b.n
    got, n = asyncio.run(run())
    assert got == [("miss", {"build": 2})] * 8          # nobody is served the 20-minute-old entry
    assert n == 2                                         # one build for eight callers


def test_A6_the_desk_case_a_7h_old_outlook_is_rebuilt_not_served():
    # 02:35Z on 10-05: the payload was built 19:27:35Z on 10-04 (7 h 7 min)
    async def run():
        c, b = main._wind_outlook_cache, _Builder()
        c.clear()
        await c.serve("desk", b)
        _age(c, "desk", 7 * 3600 + 7 * 60)
        p, s, _ = await c.serve("desk", b)
        c.clear()
        return p, s
    assert asyncio.run(run()) == ({"build": 2}, "miss")


def test_A6_the_four_outlook_caches_are_bounded_at_900_s():
    for c in (main._solar_outlook_cache, main._solar_sites_cache,
              main._wind_outlook_cache, main._wind_sites_cache):
        assert c.allow_stale is True and c.max_stale_s == 900.0, c.name
    assert main.OUTLOOK_MAX_STALE_S == 900.0


# ═══════════════════════════════════════════════════════════════════════════
# A7 — the one deliberate exception, and no other cache changed
# ═══════════════════════════════════════════════════════════════════════════

def test_A7_dd_cumulative_keeps_unbounded_stale():
    c = main._dd_cumulative_cache
    assert c.allow_stale is True and c.max_stale_s is None

    async def run():
        b = _Builder()
        await c.serve("k", b)
        _age(c, "k", 30 * 86400)                             # a month old: still served
        p, s, _ = await c.serve("k", b)
        await asyncio.gather(*list(c._tasks))
        c._entries.pop("k", None)
        return p, s
    assert asyncio.run(run()) == ({"build": 1}, "stale")


def test_A7_no_other_cache_is_bounded():
    bounded = {v.name for v in vars(main).values()
               if isinstance(v, main._DDCache) and v.max_stale_s is not None}
    # The three load memos are bounded too, on purpose: d091611 (merged before
    # this lane) put them under the same D-09-25-138 cap. So is the MJO status
    # memo: d091614 (merged before this lane) serves it at 300 s fresh, stale
    # <= 900 s. And the asset page's two memos (d091635), which reuse the
    # outlook section's plumbing and its D-09-25-138 cap. Still an exact set.
    assert bounded == {"generation/solar/outlook", "generation/solar/sites",
                       "generation/wind/outlook", "generation/wind/sites",
                       "load/outlook", "load/areas", "load/net-demand",
                       "mjo/status", "generation/asset", "generation/assets"}


# ═══════════════════════════════════════════════════════════════════════════
# PG — the replacement reads on a real Postgres (migration 272's columns)
# ═══════════════════════════════════════════════════════════════════════════

_DDL = """
CREATE TABLE implied_gen_calibration (
    calibration_id bigint PRIMARY KEY, tech text NOT NULL, area text NOT NULL,
    lead_band text NOT NULL, intercept_mw double precision NOT NULL, slope double precision NOT NULL,
    fit_start date NOT NULL, fit_end date NOT NULL, n_hours integer NOT NULL, n_days integer NOT NULL,
    fitted_at timestamptz NOT NULL DEFAULT now(), method_version text NOT NULL,
    fit_lead_min smallint, fit_lead_max smallint, fit_rows integer);
CREATE TABLE implied_gen_scores (
    tech text NOT NULL, area_kind text NOT NULL, area text NOT NULL, lead_band text NOT NULL,
    who text NOT NULL, window_start date, window_end date NOT NULL, n_hours integer NOT NULL,
    n_days integer NOT NULL, scored boolean NOT NULL, bias_mw double precision,
    mae_mw double precision, mae_pct_installed double precision, rmse_mw double precision,
    r double precision, method_version text NOT NULL, scored_at timestamptz NOT NULL DEFAULT now(),
    actual_source text, mw_yes double precision, mw_unknown double precision, mw_no double precision,
    lead_min smallint, lead_max smallint,
    PRIMARY KEY (tech, area, lead_band, who, window_end, method_version));
"""


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_fit_")
    as_pg = ["runuser", "-u", "postgres", "--"] if os.geteuid() == 0 else []
    if as_pg:
        shutil.chown(d, "postgres")
    data = os.path.join(d, "data")
    subprocess.run(as_pg + [initdb, "-D", data, "-A", "trust", "-U", "postgres"],
                   check=True, capture_output=True)
    subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-w",
                            "-l", os.path.join(d, "pg.log"), "-o",
                            f"-k {d} -c listen_addresses='' -c timezone=UTC", "start"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    conn = psycopg.connect(f"host={d} user=postgres dbname=postgres", autocommit=True,
                           row_factory=psycopg.rows.dict_row)
    try:
        conn.execute(_DDL)
        conn.execute(
            "INSERT INTO implied_gen_calibration VALUES "
            "(696,'wind','HUBSUM','h49_120',1483.7487,0.699056,'2026-09-06','2026-10-03',499,28,now(),'wind_v1',49,66,499),"
            "(715,'solar_pv','HUBSUM','h25_48',1.0,0.8,'2026-09-06','2026-10-03',356,28,now(),'solar_pv_v1',26,45,356),"
            "(900,'wind','HUBSUM','h121_240',1.0,1.0,'2026-09-06','2026-10-03',0,0,now(),'wind_v1',NULL,NULL,NULL),"
            "(901,'wind','NP15','h49_120',1.0,1.0,'2026-09-06','2026-10-03',9,1,now(),'wind_v1',49,66,9)")
        sc = ("INSERT INTO implied_gen_scores (tech, area_kind, area, lead_band, who, window_end, "
              "n_hours, n_days, scored, mae_mw, method_version, lead_min, lead_max) "
              "VALUES ('wind','hub_sum','HUBSUM',%s,%s,%s,%s,28,true,1.0,'wind_v1',%s,%s)")
        with conn.cursor() as cur:
            cur.executemany(sc, [("h49_120", "calibrated", date(2026, 10, 3), 300, 49, 66),
                                 ("dam_comparable", "caiso_dam", date(2026, 10, 3), 300, None, None)])
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-m", "immediate",
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(d, ignore_errors=True)


def test_PG_calibration_read_is_the_stored_columns_named_by_tech_and_area(pg):
    rows = pg.execute(so.CALIBRATION_SQL, {"tech": "wind", "area": "HUBSUM",
                                           "ids": [696, 715, 900, 901]}).fetchall()
    by = {r["calibration_id"]: r for r in rows}
    assert set(by) == {696, 900}                  # another tech's or area's id is not read
    assert (by[696]["fit_lead_min"], by[696]["fit_lead_max"], by[696]["fit_rows"]) == (49, 66, 499)
    assert by[900]["fit_lead_min"] is None and not so._fitted(by[900], 130)
    assert "implied_gen_area_hourly" not in so.CALIBRATION_SQL and "LATERAL" not in so.CALIBRATION_SQL


def test_PG_score_read_before_and_after_the_hours_rule_column(pg):
    params = {"tech": "wind", "area_kind": "hub_sum", "area": "HUBSUM", "method_version": "wind_v1"}
    before = {(r["lead_band"], r["who"]): r for r in pg.execute(wo.SCORES_SQL["hub_sum"], params)}
    assert (before[("h49_120", "calibrated")]["lead_min"], before[("h49_120", "calibrated")]["lead_max"]) == (49, 66)
    assert before[("h49_120", "calibrated")]["hours_rule"] is None      # no column yet: valid, null
    assert before[("dam_comparable", "caiso_dam")]["lead_min"] is None
    pg.execute("ALTER TABLE implied_gen_scores ADD COLUMN hours_rule text")
    try:
        pg.execute("UPDATE implied_gen_scores SET hours_rule = 'registry_mw > 0' WHERE who = 'calibrated'")
        after = {(r["lead_band"], r["who"]): r for r in pg.execute(wo.SCORES_SQL["hub_sum"], params)}
        assert after[("h49_120", "calibrated")]["hours_rule"] == "registry_mw > 0"   # through, untouched
        scores, _p = so.build_scores("hub_sum", list(after.values()), wo.SCORE_EXTRA)
        assert scores["h49_120"]["calibrated"]["hours_rule"] == "registry_mw > 0"
    finally:
        pg.execute("ALTER TABLE implied_gen_scores DROP COLUMN hours_rule")
