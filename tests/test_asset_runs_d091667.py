"""d091667 — the asset page reads a plant's run history and the filled wind
equipment (D-09-25-167, D-09-25-171, D-09-25-173, D-09-25-76, D-09-25-109).

  H1  one plant's runs, oldest first (the area vintages contract: the run
      before issuances[k] is issuances[k-1]), and the page's run-before move
      on the served arrays equals a hand difference on production's rows
      (SunZia Wind South, Solar Star 1; the 10-08 00/06/12Z runs, read
      2026-10-08 20:43Z). No change, sum or gap is in the body.
  H2  an absent run is an absence with its reason, never a hole: before the
      history began, not yet landed, not in the ledger, plant not in the run,
      evicted past eight
  H3  two weather sources are two series; runs are never joined across them
      or across model
  H4  every basis token production holds today is served verbatim and
      labelled from one table; a made-up token is served as unknown
  H5  a mixed fleet: production states none; every plant is served as the
      one machine its row states, never averaged by the API
  H6  a plant without a rotor or a speed band says it cannot animate, and why
  H7  the turbine-data attribution notice rides in every payload that carries
      a wind figure (a sweep over the app's routes)
  H8  (d091635's and d091644's tests are unchanged and pass; the two vectors
      this lane re-banks equal main's copies plus exactly the new key)
  H9  the SQL is pinned; the statements run on a real Postgres and prune to
      one partition per run
  C   D-09-25-75: timeout first, memoised per (tech, plant_code, n), 400/404/503

Route tests run main.py against test_solar_outlook's FakePool. PG tests skip
where no `initdb` is installed.
"""

import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import asset_page as ap
import main
import wind_outlook as wo
from test_solar_outlook import FakePool

UTC = timezone.utc
H = timedelta(hours=1)
HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
BANK = HERE / "fixtures" / "asset_runs_d091667" / "bank_2026_10_08.json"
RECEIPTS = ROOT / "docs" / "receipts" / "asset-runs-d091667"

_spec = importlib.util.spec_from_file_location("bank_from_neon", RECEIPTS / "bank_from_neon.py")
bn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bn)

SUNZIA, SOLAR_STAR = 66923, 58388
T = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)  # noqa: E731


# ── The bank ────────────────────────────────────────────────────────────────

def load():
    """The bank, every run and the site table held to Neon's md5 on load."""
    b = json.loads(BANK.read_text())
    for key, runs in b["runs"].items():
        tech = key.split("/")[0]
        for run in runs:
            got = hashlib.md5(bn.run_md5_text(tech, run).encode()).hexdigest()
            assert got == run["md5"], f"{key} {run['init_ts']}: the bank is not what Neon read"
    assert hashlib.md5(bn.sites_md5_text(b["wind_sites"]).encode()).hexdigest() == b["wind_sites_md5"]
    return b


BANKED = load()
DB_TECH = {"wind": "wind", "solar": "solar_pv"}
CODE = {"wind": SUNZIA, "solar": SOLAR_STAR}


def ledger(tech):
    return [{**r, "init_ts": T(r["init_ts"]), "landed_at": T(r["landed_at"])}
            for r in BANKED["ledger"] if r["tech"] == DB_TECH[tech]]


def history(tech):
    """The plant's rows, as implied_gen_site_history holds them."""
    out = []
    for run in BANKED["runs"][f"{DB_TECH[tech]}/{CODE[tech]}"]:
        i0 = T(run["init_ts"])
        for j, lead in enumerate(run["lead_h"]):
            row = {"init_ts": i0, "target_ts": i0 + (lead - 1) * H, "lead_h": lead,
                   "weather_source": run["weather_source"][j], "implied_mw": run["implied_mw"][j],
                   "outage_mw_subtracted": run["outage"][j], "cap_mw_subtracted": run["cap"][j],
                   "method_version": run["mv"]}
            for c in ap.HOUR_DRIVERS[tech]:
                row[c] = run[c][j]
            out.append(row)
    return out


def sql_rows(tech, led=None, hist=None, n=8):
    """What runs_sql returns over (ledger, history): the ledger newest first
    LIMIT 8 with k, each run LEFT JOINed to the plant's rows for k <= n."""
    led = sorted(ledger(tech) if led is None else led, key=lambda r: r["init_ts"], reverse=True)[:ap.RUNS_KEEP]
    hist = history(tech) if hist is None else hist
    out = []
    for k, r in enumerate(led, 1):
        base = {"k": k, "run_init_ts": r["init_ts"], "run_n_rows": r["n_rows"],
                "run_n_plants": r["n_plants"], "run_method_version": r["method_version"],
                "run_landed_at": r["landed_at"]}
        hs = sorted((h for h in hist if h["init_ts"] == r["init_ts"]), key=lambda h: h["target_ts"]) \
            if k <= n else []
        empty = {c: None for c in ("target_ts", "lead_h", "weather_source", "implied_mw",
                                   "outage_mw_subtracted", "cap_mw_subtracted", "method_version")
                 + ap.HOUR_DRIVERS[tech]}
        out += [{**base, **{c: h[c] for c in empty}} for h in hs] or [{**base, **empty}]
    return sorted(out, key=lambda r: (r["run_init_ts"], r["target_ts"] or r["run_init_ts"]))


PLANT_ROW = {"wind": [{"plant_code": SUNZIA, "plant_name": "SunZia Wind South"}],
             "solar": [{"plant_code": SOLAR_STAR, "plant_name": "Solar Star 1"}]}


def runs_pool(tech, led=None, hist=None, plant=None):
    return FakePool([
        ("SET LOCAL", []),
        ("FROM implied_gen_site_history_runs", lambda p: sql_rows(tech, led, hist, p["n"])),
        ("FROM implied_gen_wind_sites", PLANT_ROW["wind"] if plant is None else plant),
        ("FROM implied_gen_sites", PLANT_ROW["solar"] if plant is None else plant),
    ])


def _clear():
    for c in (getattr(main, "_asset_runs_cache", None), main._asset_cache):
        if c is not None:            # absent on main: the red run reaches the assertions
            c.clear()


@pytest.fixture(autouse=True)
def _cold_memos():
    _clear()
    yield
    _clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def get(client, monkeypatch, pool, path):
    monkeypatch.setattr(main, "_pool", pool)
    return client.get(path)


def runs(client, monkeypatch, tech="wind", qs="", **kw):
    r = get(client, monkeypatch, runs_pool(tech, **kw),
            f"/api/generation/asset/runs?plant_code={CODE[tech]}&tech={tech}{qs}")
    assert r.status_code == 200, r.text
    return r.json()


# The page's arithmetic, written out here as the dashboard lane will: the run
# before is issuances[k-1]; a series is compared only with the series of the
# same weather_source; an hour counts where both hold a value.

def series_hours(issuance, src, col="implied_mw"):
    s = next((s for s in issuance["series"] if s["weather_source"] == src), None)
    if s is None:
        return {}
    t0 = T(s["t0"])
    return {t0 + i * H: v for i, v in enumerate(s[col]) if v is not None}


def page_day_move(body, k, src, day, col="implied_mw"):
    cur = series_hours(body["issuances"][k], src, col)
    prev = series_hours(body["issuances"][k - 1], src, col)
    shared = [t for t in cur if t in prev and t.date().isoformat() == day]
    return len(shared), sum(cur[t] for t in shared) - sum(prev[t] for t in shared)


def hand_day_move(tech, k, src, day):
    """The same move from the raw bank, not from anything the route served."""
    rs = BANKED["runs"][f"{DB_TECH[tech]}/{CODE[tech]}"]

    def hours(run):
        i0 = T(run["init_ts"])
        return {i0 + (lead - 1) * H: v for lead, s, v in zip(run["lead_h"], run["weather_source"],
                                                               run["implied_mw"]) if s == src}
    cur, prev = hours(rs[k]), hours(rs[k - 1])
    shared = [t for t in cur if t in prev and t.date().isoformat() == day]
    return len(shared), sum(cur[t] for t in shared) - sum(prev[t] for t in shared)


# ═══════════════════════════════════════════════════════════════════════════
# H1 — one plant's runs, the run before, and a hand difference
# ═══════════════════════════════════════════════════════════════════════════

def test_H1_sunzia_serves_every_held_run_oldest_first(client, monkeypatch):
    b = runs(client, monkeypatch)
    assert [i["init_ts"] for i in b["issuances"]] == \
        ["2026-10-08T00:00:00+00:00", "2026-10-08T06:00:00+00:00", "2026-10-08T12:00:00+00:00"]
    assert (b["plant_name"], b["model"], b["figure"], b["n"]) == ("SunZia Wind South", "hrrr_gfs", "registry", 8)
    i = b["issuances"][-1]
    assert (i["method_version"], i["landed_at"], i["n_plants"]) == \
        ("wind_v1", "2026-10-08T14:58:03.655000+00:00", 323)


def test_H1_every_served_value_is_the_banked_row(client, monkeypatch):
    for tech in ("wind", "solar"):
        b = runs(client, monkeypatch, tech)
        _clear()
        got = {}
        for iss in b["issuances"]:
            for s in iss["series"]:
                t0, lead0 = T(s["t0"]), s["lead0"]
                for j in range(len(s["implied_mw"])):
                    got[(T(iss["init_ts"]), t0 + j * H)] = (
                        lead0 + j, s["weather_source"], s["implied_mw"][j], s["outage_mw_subtracted"][j],
                        s["cap_mw_subtracted"][j], *[s[c][j] for c in ap.HOUR_DRIVERS[tech]])
        want = {(h["init_ts"], h["target_ts"]): (
            h["lead_h"], h["weather_source"], h["implied_mw"], h["outage_mw_subtracted"],
            h["cap_mw_subtracted"], *[h[c] for c in ap.HOUR_DRIVERS[tech]]) for h in history(tech)}
        assert got == want, tech
        assert len(want) == 720


@pytest.mark.parametrize("tech,k,src,day,n_hours,move", [
    # pinned from the raw bank by hand (docs/handback §1.4): MWh of implied
    # generation, the run against the run before, over the hours both hold
    ("wind", 2, "hrrr_80m", "2026-10-09", 24, 1129.2529),
    ("wind", 1, "hrrr_80m", "2026-10-08", 18, -854.9087),
    ("wind", 2, "gfs_100m", "2026-10-13", 24, -8137.3389),
    ("solar", 2, None, "2026-10-11", 24, 672.4618),
    ("solar", 1, None, "2026-10-10", 24, -331.953),
])
def test_H1_the_run_before_move_is_the_hand_difference(client, monkeypatch, tech, k, src, day, n_hours, move):
    b = runs(client, monkeypatch, tech)
    got = page_day_move(b, k, src, day)
    assert got[0] == n_hours and round(got[1], 4) == move
    hand = hand_day_move(tech, k, src, day)
    assert hand[0] == n_hours and abs(hand[1] - got[1]) < 1e-9


def test_H1_no_change_sum_or_gap_is_computed_here(client, monkeypatch):
    """D-09-25-167 clause 4: the page does the arithmetic."""
    b = runs(client, monkeypatch)
    keys = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk({k: v for k, v in b.items() if k != "cache"})
    banned = re.compile(r"move|change|delta|diff|gap|spacing|previous|prior|before_init|sum|total")
    assert not {k for k in keys if banned.search(k)}, keys
    assert set(b["issuances"][0]) == {"init_ts", "model", "method_version", "landed_at",
                                      "n_plants", "weather_sources", "series"}


def eight_runs(tech="wind"):
    """Eight held runs, 6 h apart, from the live rows: the three live runs and
    five older copies, each copy's values offset so the runs are distinct."""
    led0, hist0 = ledger(tech), history(tech)
    newest = max(r["init_ts"] for r in led0)
    led, hist = [], []
    base = [h for h in hist0 if h["init_ts"] == newest]
    for j in range(8):
        i = newest - j * 6 * H
        live = [r for r in led0 if r["init_ts"] == i]
        led.append(live[0] if live else {**led0[-1], "init_ts": i, "landed_at": i + 3 * H})
        if live:
            hist += [h for h in hist0 if h["init_ts"] == i]
        else:
            hist += [{**h, "init_ts": i, "target_ts": h["target_ts"] - j * 6 * H,
                      "implied_mw": h["implied_mw"] + j} for h in base]
    return led, hist


def test_H1_eight_runs_the_default_and_the_cap(client, monkeypatch):
    led, hist = eight_runs()
    b = runs(client, monkeypatch, led=led, hist=hist)
    inits = [i["init_ts"] for i in b["issuances"]]
    assert len(inits) == 8 and inits == sorted(inits)
    assert b["ledger"]["full"] is True and b["ledger"]["held"] == 8
    assert [a["reason"] for a in b["absent"]] == ["not_yet_landed"]
    assert b["before_oldest"]["reason"] == "evicted"
    for k in range(1, 8):                               # every run's run before is k-1
        assert T(b["issuances"][k]["init_ts"]) - T(b["issuances"][k - 1]["init_ts"]) == 6 * H


def test_H1_n_reaches_the_read_and_the_window(client, monkeypatch):
    led, hist = eight_runs()
    pool = runs_pool("wind", led=led, hist=hist)
    b = get(client, monkeypatch, pool, f"/api/generation/asset/runs?plant_code={SUNZIA}&tech=wind&n=3").json()
    q, p = next((q, p) for q, p in pool.calls if "implied_gen_site_history_runs" in q)
    assert (p["n"], p["keep"]) == (3, 8)
    assert [i["init_ts"][:13] for i in b["issuances"]] == ["2026-10-08T00", "2026-10-08T06", "2026-10-08T12"]
    assert b["before_oldest"] == {"reason": "outside_window", "init_ts": "2026-10-07T18:00:00+00:00",
                                  "detail": "the ledger holds an older run, outside the newest 3 cycles asked for"}


@pytest.mark.parametrize("n", ["9", "100", "0", "-1", "abc", "2.5"])
def test_H1_n_out_of_range_is_refused_not_trimmed(client, monkeypatch, n):
    pool = runs_pool("wind")
    r = get(client, monkeypatch, pool, f"/api/generation/asset/runs?plant_code={SUNZIA}&tech=wind&n={n}")
    assert r.status_code == 400 and pool.calls == []


# ═══════════════════════════════════════════════════════════════════════════
# H2 — an absent run is an absence with its reason
# ═══════════════════════════════════════════════════════════════════════════

def _window_is_accounted_for(b):
    """Every cycle of the window is served or absent, once; the next is pending."""
    served = {i["init_ts"] for i in b["issuances"]}
    absent = [a["init_ts"] for a in b["absent"] if a["reason"] != "not_yet_landed"]
    assert not served & set(absent) and len(absent) == len(set(absent))
    assert len(served) + len(absent) == b["n"]
    assert all(a["detail"] for a in b["absent"])


def test_H2_today_five_cycles_before_the_history_and_18z_not_yet_landed(client, monkeypatch):
    for tech in ("wind", "solar"):
        b = runs(client, monkeypatch, tech)
        _clear()
        assert b["ledger"] == {"table": "implied_gen_site_history_runs", "held": 3, "keep": 8, "full": False,
                               "oldest_init_ts": "2026-10-08T00:00:00+00:00",
                               "newest_init_ts": "2026-10-08T12:00:00+00:00"}
        assert [(a["init_ts"][:13], a["reason"]) for a in b["absent"]] == [
            ("2026-10-06T18", "before_history"), ("2026-10-07T00", "before_history"),
            ("2026-10-07T06", "before_history"), ("2026-10-07T12", "before_history"),
            ("2026-10-07T18", "before_history"), ("2026-10-08T18", "not_yet_landed")]
        assert b["before_oldest"]["reason"] == "before_history" and b["absence"] is None
        _window_is_accounted_for(b)


def test_H2_evicted_past_eight_is_named_once_the_ledger_is_full(client, monkeypatch):
    led, hist = eight_runs()
    b = runs(client, monkeypatch, led=led, hist=hist)
    assert b["before_oldest"] == {"reason": "evicted", "init_ts": None, "detail": ap.ABSENT_REASONS["evicted"]}
    assert "D-09-25-171" in b["before_oldest"]["detail"]
    _window_is_accounted_for(b)


def test_H2_a_skipped_cycle_is_not_in_ledger_and_the_run_before_steps_over_it(client, monkeypatch):
    led, hist = eight_runs()
    gone = T("2026-10-08T00:00:00Z")
    led = [r for r in led if r["init_ts"] != gone]
    hist = [h for h in hist if h["init_ts"] != gone]
    b = runs(client, monkeypatch, led=led, hist=hist)
    assert {"init_ts": "2026-10-08T00:00:00+00:00", "reason": "not_in_ledger",
            "detail": ap.ABSENT_REASONS["not_in_ledger"]} in b["absent"]
    inits = [i["init_ts"][:13] for i in b["issuances"]]
    assert "2026-10-08T00" not in inits and inits[-2:] == ["2026-10-08T06", "2026-10-08T12"]
    assert inits[-3] == "2026-10-07T18"                  # issuances[k-1] is the next older held run
    _window_is_accounted_for(b)


def test_H2_a_run_without_the_plant_is_plant_not_in_run_never_a_hole(client, monkeypatch):
    gone = T("2026-10-08T06:00:00Z")
    hist = [h for h in history("wind") if h["init_ts"] != gone]     # the ledger still names the run
    b = runs(client, monkeypatch, hist=hist)
    a = next(a for a in b["absent"] if a["init_ts"] == "2026-10-08T06:00:00+00:00")
    assert a["reason"] == "plant_not_in_run" and "323 plants" in a["detail"]
    assert [i["init_ts"][:13] for i in b["issuances"]] == ["2026-10-08T00", "2026-10-08T12"]
    _window_is_accounted_for(b)


def test_H2_nothing_held_and_plant_in_no_run(client, monkeypatch):
    b = runs(client, monkeypatch, led=[], hist=[])
    assert b["absence"]["reason"] == "no_run_held" and b["issuances"] == [] and b["absent"] == []
    _clear()
    b = runs(client, monkeypatch, hist=[])
    assert b["absence"]["reason"] == "plant_in_no_held_run"
    assert {a["reason"] for a in b["absent"]} == {"plant_not_in_run", "before_history", "not_yet_landed"}


def test_H2_a_hole_inside_a_series_is_null_never_zero(client, monkeypatch):
    hole = T("2026-10-08T12:00:00Z") + 9 * H                 # lead 10 of the newest run
    hist = [h for h in history("wind") if not (h["init_ts"] == T("2026-10-08T12:00:00Z") and h["target_ts"] == hole)]
    b = runs(client, monkeypatch, hist=hist)
    s = b["issuances"][-1]["series"][0]
    assert s["weather_source"] == "hrrr_80m" and len(s["implied_mw"]) == 48 and s["n_hours"] == 47
    assert s["implied_mw"][9] is None and s["hub_ws_ms"][9] is None and s["implied_mw"][10] is not None


# ═══════════════════════════════════════════════════════════════════════════
# H3 — two weather sources are two series
# ═══════════════════════════════════════════════════════════════════════════

def test_H3_a_wind_run_is_two_series_split_at_the_seam(client, monkeypatch):
    b = runs(client, monkeypatch)
    for iss in b["issuances"]:
        assert iss["weather_sources"] == ["hrrr_80m", "gfs_100m"]
        hrrr, gfs = iss["series"]
        assert (hrrr["lead0"], len(hrrr["implied_mw"]), gfs["lead0"], len(gfs["implied_mw"])) == (1, 48, 49, 192)
        assert T(gfs["t0"]) == T(hrrr["t0"]) + 48 * H
        assert all(v is None for v in hrrr["gust_ms"]) and all(v is not None for v in gfs["gust_ms"])


def test_H3_hours_across_the_seam_never_pair(client, monkeypatch):
    """10-10 00-05Z: GFS in the 00Z run, HRRR in the 06Z run. Same hours, two
    sources: no series pairs them, so no move is drawn there."""
    b = runs(client, monkeypatch)
    seam = [T("2026-10-10T00:00:00Z") + j * H for j in range(6)]
    cur_h, prev_g = series_hours(b["issuances"][1], "hrrr_80m"), series_hours(b["issuances"][0], "gfs_100m")
    assert all(t in cur_h and t in prev_g for t in seam)
    assert not any(t in series_hours(b["issuances"][0], "hrrr_80m") for t in seam)
    assert not any(t in series_hours(b["issuances"][1], "gfs_100m") for t in seam)
    n_h, _ = page_day_move(b, 1, "hrrr_80m", "2026-10-10")
    n_g, _ = page_day_move(b, 1, "gfs_100m", "2026-10-10")
    assert (n_h, n_g) == (0, 18)                        # 24 hours of the day; 6 have no like-for-like pair


def test_H3_solar_is_one_series_with_the_banks_null_source(client, monkeypatch):
    b = runs(client, monkeypatch, "solar")
    for iss in b["issuances"]:
        assert iss["weather_sources"] == [None] and len(iss["series"]) == 1
        assert len(iss["series"][0]["implied_mw"]) == 240 and "hub_ws_ms" not in iss["series"][0]
    assert "attribution" not in b


def test_H3_every_read_names_one_model_and_one_plant(client, monkeypatch):
    for tech in ("wind", "solar"):
        pool = runs_pool(tech)
        get(client, monkeypatch, pool, f"/api/generation/asset/runs?plant_code={CODE[tech]}&tech={tech}")
        q, p = next((q, p) for q, p in pool.calls if "implied_gen_site_history" in q)
        assert (p["tech"], p["model"], p["plant_code"]) == (DB_TECH[tech], ap.MODELS[tech], CODE[tech])
        flat = " ".join(q.split())
        assert "WHERE tech = %(tech)s AND model = %(model)s" in flat
        assert ("x.tech = %(tech)s AND x.plant_code = %(plant_code)s AND x.model = %(model)s "
                "AND x.init_ts = r.init_ts") in flat
        _clear()


def test_H3_a_second_weather_source_in_one_run_is_its_own_series_wherever_it_sits(client, monkeypatch):
    hist = [{**h, "weather_source": "gfs_100m"} if h["init_ts"] == T("2026-10-08T12:00:00Z") and
            h["lead_h"] in (10, 11) else h for h in history("wind")]
    b = runs(client, monkeypatch, hist=hist)
    srcs = [(s["weather_source"], s["lead0"], s["n_hours"]) for s in b["issuances"][-1]["series"]]
    assert srcs == [("hrrr_80m", 1, 46), ("gfs_100m", 10, 194)]   # never one array across two sources
    assert b["issuances"][-1]["series"][0]["implied_mw"][9] is None


# ═══════════════════════════════════════════════════════════════════════════
# H4 — every basis token verbatim, labelled in one place
# ═══════════════════════════════════════════════════════════════════════════

BASIS_COLS = ("turbine_model_basis", "n_turbines_basis", "rotor_basis", "hub_height_basis", "curve_basis")

# Every distinct value production held, 2026-10-08 20:3xZ (the bank, and the
# connector's GROUP BY in the handback §1.2).
PRODUCTION_TOKENS = {
    "turbine_model_basis": {"eia860_sch3": 318, "public_record": 2, "uswtdb": 2, "none": 1},
    "n_turbines_basis": {"eia860_sch3": 318, "public_record": 2, "uswtdb": 2, "none": 1},
    "rotor_basis": {"uswtdb": 288, "same_model_uswtdb": 16, "none": 12, "model_designation": 5,
                    "public_record": 2},
    "hub_height_basis": {"eia860_sch3": 318, "none": 3, "uswtdb": 2},
    "curve_basis": {"vintage_band": 323},
}

FIELD = {"turbine_model_basis": "turbine_model", "n_turbines_basis": "n_turbines",
         "rotor_basis": "rotor_m", "hub_height_basis": "hub_height_m"}

CATALOG_NOW = ([{"table_name": "implied_gen_site_latest", "column_name": c} for c in ap.HOUR_DRIVERS["wind"]]
               + [{"table_name": "implied_gen_wind_sites", "column_name": c}
                  for c in ap.CURVE_DRIVERS + (ap.EQUIPMENT_SOURCE,)])


def equipment_of(site, catalog=CATALOG_NOW):
    found = ap.detect("wind", catalog)
    body = ap.build_asset(tech="wind", plant_code=site["plant_code"], found=found, site_rows=[site],
                          hour_rows=[], score_rows=[])
    return body["plant"]["equipment"]


SWEEP = {s["plant_code"]: equipment_of(s) for s in BANKED["wind_sites"]}


def served_basis(eq, col):
    return eq["curve"]["basis"] if col == "curve_basis" else eq[FIELD[col]]["basis"]


def test_H4_the_bank_holds_exactly_the_measured_tokens():
    for col, want in PRODUCTION_TOKENS.items():
        got = {}
        for s in BANKED["wind_sites"]:
            got[s[col]] = got.get(s[col], 0) + 1
        assert got == want, col


def test_H4_every_production_token_is_served_verbatim_and_labelled():
    sites = {s["plant_code"]: s for s in BANKED["wind_sites"]}
    for code, eq in SWEEP.items():
        for col in BASIS_COLS:
            b = served_basis(eq, col)
            assert b["token"] == sites[code][col], (code, col)
            assert b["status"] == "known" and b["label"] == ap.BASIS_LABELS[col][b["token"]], (code, col)


def test_H4_the_flat_bases_are_unchanged_beside_the_block(client, monkeypatch):
    s = next(s for s in BANKED["wind_sites"] if s["plant_code"] == SUNZIA)
    b = ap.build_asset(tech="wind", plant_code=SUNZIA, found=ap.detect("wind", CATALOG_NOW),
                       site_rows=[s], hour_rows=[], score_rows=[])
    p = b["plant"]
    assert (p["turbine_model_basis"], p["n_turbines_basis"], p["rotor_basis"], p["hub_height_basis"]) == \
        ("public_record", "public_record", "public_record", "none")


@pytest.mark.parametrize("col", BASIS_COLS)
def test_H4_a_made_up_token_is_unknown_never_the_nearest(col):
    s = {**BANKED["wind_sites"][0], col: "uswtdb_v2"}
    b = served_basis(equipment_of(s), col)
    assert b == {"token": "uswtdb_v2", "status": "unknown", "label": ap.UNKNOWN_BASIS_LABEL}
    s = {**BANKED["wind_sites"][0], col: None}
    assert served_basis(equipment_of(s), col)["status"] == "absent"


# What each token means, in the writer's own terms (pantry implied_gen/wind.py
# header, implied_gen/wind_equipment.py rules 1-3, d091647 handback §1.3): the
# words every label must carry, and the words it must not.
MEANS = {
    ("turbine_model_basis", "eia860_sch3"): (("EIA-860", "predominant", "largest generator"), ("USWTDB",)),
    ("turbine_model_basis", "uswtdb"): (("USWTDB",), ("EIA-860",)),
    ("n_turbines_basis", "eia860_sch3"): (("EIA-860", "summed"), ("USWTDB",)),
    ("rotor_basis", "uswtdb"): (("USWTDB", "capacity-weighted", "blend"), ("stated model",)),
    ("rotor_basis", "same_model_uswtdb"): (("USWTDB", "stated model", "any plant"), ("blend",)),
    ("rotor_basis", "model_designation"): (("stated model's name", "naming convention"), ("USWTDB",)),
    ("rotor_basis", "public_record"): (("public document", "equipment_source"), ("USWTDB",)),
    ("rotor_basis", "none"): (("no source", "equipment_source"), ()),
    ("hub_height_basis", "eia860_sch3"): (("EIA-860", "MW-weighted"), ("USWTDB",)),
    ("curve_basis", "vintage_band"): (("vintage band", "converted"), ()),
    ("curve_basis", "vintage_band_default_2010"): (("2010", "unknown"), ()),
}


@pytest.mark.parametrize("col,token", sorted(MEANS))
def test_H4_each_label_says_what_the_writer_does(col, token):
    must, must_not = MEANS[(col, token)]
    label = ap.BASIS_LABELS[col][token]
    assert all(w in label for w in must) and not any(w in label for w in must_not), label


def test_H4_no_two_tokens_of_a_column_share_a_label():
    for col, labels in ap.BASIS_LABELS.items():
        assert len(set(labels.values())) == len(labels), col
    for col in BASIS_COLS:                               # and every token the writer can write is labelled
        assert set(PRODUCTION_TOKENS[col]) <= set(ap.BASIS_LABELS[col])


def test_H4_labels_are_worded_in_one_place():
    """Every label the payload can print is a value of BASIS_LABELS (or the
    unknown/absent wording), and no other module words a basis token."""
    labels = {v for col in ap.BASIS_LABELS.values() for v in col.values()}
    for eq in SWEEP.values():
        for col in BASIS_COLS:
            assert served_basis(eq, col)["label"] in labels
    for f in ROOT.glob("*.py"):
        if f.name == "asset_page.py":
            continue
        text = f.read_text()
        for lab in labels:
            assert lab not in text, (f.name, lab)


def test_H4_equipment_source_is_verbatim_and_its_absence_named():
    sites = {s["plant_code"]: s for s in BANKED["wind_sites"]}
    for code, eq in SWEEP.items():
        assert eq["equipment_source"] == sites[code]["equipment_source"]
        assert eq["equipment_source_absence"] is None
    assert sum(eq["equipment_source"] is not None for eq in SWEEP.values()) == 35
    pre284 = equipment_of({k: v for k, v in sites[SUNZIA].items() if k != "equipment_source"},
                          catalog=[r for r in CATALOG_NOW if r["column_name"] != "equipment_source"])
    assert pre284["equipment_source"] is None and pre284["equipment_source_absence"]["reason"] == "column_absent"


def test_H4_the_site_read_selects_equipment_source_only_where_the_catalog_has_it():
    assert "s.equipment_source" in ap.wind_site_sql(ap.CURVE_DRIVERS, True)
    assert "equipment_source" not in ap.wind_site_sql(ap.CURVE_DRIVERS, False)
    assert ap.detect_equipment_source(CATALOG_NOW) is True
    assert ap.detect_equipment_source([]) is False
    assert "equipment_source" in ap.columns_params("wind")["columns"]
    assert "equipment_source" not in ap.columns_params("solar")["columns"]


def test_H4_the_asset_route_reads_the_catalog_and_serves_the_block(client, monkeypatch):
    s = next(s for s in BANKED["wind_sites"] if s["plant_code"] == SUNZIA)
    pool = FakePool([("SET LOCAL", []), ("information_schema.columns", CATALOG_NOW),
                     ("FROM implied_gen_scores", []), ("FROM implied_gen_site_latest", []),
                     ("FROM implied_gen_wind_sites", [s])])
    b = get(client, monkeypatch, pool, f"/api/generation/asset?plant_code={SUNZIA}&tech=wind").json()
    site_sql = next(q for q, _ in pool.calls if "FROM implied_gen_wind_sites" in q)
    assert "s.equipment_source" in site_sql
    eq = b["plant"]["equipment"]
    assert eq["rotor_m"] == {"value": 154.0, "unit": "m", "absence": None,
                             "basis": {"token": "public_record", "status": "known",
                                       "label": ap.BASIS_LABELS["rotor_basis"]["public_record"]}}
    assert eq["hub_height_m"]["value"] is None and eq["hub_height_m"]["absence"]["reason"] == "not_stated"
    assert eq["equipment_source"].startswith("turbine_model: GE Vernova press release")
    assert eq["animate"]["can"] is True


# ═══════════════════════════════════════════════════════════════════════════
# H5 — a mixed fleet is served as parts; production states none
# ═══════════════════════════════════════════════════════════════════════════

def test_H5_every_plant_is_the_one_machine_its_row_states_never_averaged():
    sites = {s["plant_code"]: s for s in BANKED["wind_sites"]}
    for code, eq in SWEEP.items():
        s = sites[code]
        f = eq["fleet"]
        assert f["stated_as"] == "one_machine" and f["detail"] == ap.FLEET_DETAIL
        assert f["parts"] == [{"turbine_model": s["turbine_model"], "n_turbines": s["n_turbines"],
                               "rotor_m": None if s["rotor_m"] is None else float(s["rotor_m"]),
                               "hub_height_m": None if s["hub_height_m"] is None else float(s["hub_height_m"])}]


def test_H5_two_names_for_one_model_are_not_a_mix():
    """USWTDB lists GE1.7-100 under two maker names; equipment_source says so
    verbatim. That is one machine, and the text is never split into parts."""
    plants = [c for c, eq in SWEEP.items() if (eq["equipment_source"] or "").count(" + ")]
    assert len(plants) == 7            # GE 1.5-77 x2, 1.7-100 x2, 2.82-127 x2, 2.3-116
    for c in plants:
        assert len(SWEEP[c]["fleet"]["parts"]) == 1


def test_H5_sunzia_south_and_north_are_two_plants_each_one_machine():
    south, north = SWEEP[66923]["fleet"]["parts"], SWEEP[66924]["fleet"]["parts"]
    assert south == [{"turbine_model": "GE Vernova 3.6-154", "n_turbines": 674, "rotor_m": 154.0,
                      "hub_height_m": None}]
    assert north == [{"turbine_model": "Vestas V163-4.5", "n_turbines": 242, "rotor_m": 163.0,
                      "hub_height_m": None}]


def test_H5_a_uswtdb_rotor_says_it_may_be_a_blend():
    assert "blend" in ap.BASIS_LABELS["rotor_basis"]["uswtdb"] and "blend" in ap.FLEET_DETAIL


# ═══════════════════════════════════════════════════════════════════════════
# H6 — whether the rotor can turn, and why not
# ═══════════════════════════════════════════════════════════════════════════

CANNOT_ANIMATE = {10597: "Ridgetop Energy LLC", 50532: "Victory Garden (Tehachapi)",
                  50820: "East Winds Project", 50821: "Mojave 16/17/18", 52142: "Mojave 3/4/5",
                  54647: "TPC Windfarms LLC", 54686: "Difwind Farms Ltd VI",
                  55339: "Phoenix Wind Power LLC", 56276: "ZCO", 57792: "Foundation IE",
                  67160: "Springfield Wind", 69451: "West Camp Wind Farm"}


def test_H6_exactly_twelve_plants_cannot_animate_and_each_says_why():
    names = {s["plant_code"]: s["plant_name"] for s in BANKED["wind_sites"]}
    no = {c: eq for c, eq in SWEEP.items() if not eq["animate"]["can"]}
    assert {c: names[c] for c in no} == CANNOT_ANIMATE
    for c, eq in no.items():
        a = eq["animate"]
        assert a["missing"] == ["rotor_m"] and a["why_not"].startswith("no rotor diameter (")
        assert ap.BASIS_LABELS["rotor_basis"]["none"] in a["why_not"]
        assert eq["rotor_m"]["absence"]["reason"] == "not_stated"


def test_H6_the_23_d091647_fills_can_animate():
    filled = {c for c, s in ((s["plant_code"], s) for s in BANKED["wind_sites"])
              if s["rotor_basis"] in ("same_model_uswtdb", "model_designation", "public_record")}
    assert len(filled) == 23
    assert all(SWEEP[c]["animate"] == {"can": True, "needs": list(ap.ANIMATE_NEEDS), "missing": [],
                                       "why_not": None, "speed_band_of": ap.SPEED_BAND_OF} for c in filled)
    assert sum(eq["animate"]["can"] for eq in SWEEP.values()) == 311


def test_H6_west_camp_states_everything_it_lacks():
    eq = SWEEP[69451]
    assert [eq[k]["value"] for k in ("turbine_model", "n_turbines", "rotor_m", "hub_height_m")] == [None] * 4
    assert all(eq[k]["basis"]["token"] == "none" for k in ("turbine_model", "n_turbines", "rotor_m", "hub_height_m"))
    assert "STOP-M" in eq["equipment_source"] and eq["animate"]["can"] is False


def test_H6_no_speed_band_says_so_with_its_reason():
    s = next(s for s in BANKED["wind_sites"] if s["plant_code"] == SUNZIA)
    pre280 = equipment_of({k: v for k, v in s.items() if k not in ap.CURVE_DRIVERS},
                          catalog=[r for r in CATALOG_NOW if r["column_name"] not in ap.CURVE_DRIVERS])
    assert pre280["animate"]["can"] is False
    assert pre280["animate"]["missing"] == ["cut_in_ms", "rated_ms", "cut_out_ms"]
    assert "column_absent" in pre280["animate"]["why_not"]
    nulls = equipment_of({**s, "rated_ms": None})
    assert nulls["animate"]["missing"] == ["rated_ms"] and "all_null" in nulls["animate"]["why_not"]


def test_H6_the_speed_band_is_the_converting_curves_and_says_so():
    eq = SWEEP[SUNZIA]
    c = eq["curve"]
    assert (c["turbine_type"], c["hub_height_m"], c["cut_in_ms"], c["rated_ms"], c["cut_out_ms"]) == \
        ("GE120/2750", 95.0, 3.0, 12.0, 25.0)
    assert c["speed_band_of"] == ap.SPEED_BAND_OF and c["cut_out_note"] is None
    v90 = next(eq for c_, eq in SWEEP.items() if eq["curve"]["turbine_type"] == "V90/2000")
    assert v90["curve"]["cut_out_ms"] == 16.5 and "not the maker's cut-out" in v90["curve"]["cut_out_note"]


# ═══════════════════════════════════════════════════════════════════════════
# H7 — the attribution notice on every wind payload (a sweep)
# ═══════════════════════════════════════════════════════════════════════════

def _wind_outlook(client, monkeypatch):
    import test_wind_outlook as tw
    main._wind_outlook_cache.clear()
    return get(client, monkeypatch, tw.outlook_pool(hours=[]),
               "/api/generation/wind/outlook?area_kind=state&area=CA").json()


def _wind_sites(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("FROM implied_gen_wind_sites", [])])
    return get(client, monkeypatch, pool, "/api/generation/wind/sites?target=2026-10-04T20:00:00Z").json()


def _wind_vintages(client, monkeypatch):
    pool = FakePool([("SET LOCAL", []), ("WITH RECURSIVE inits", []), ("FROM implied_gen_calibration", [])])
    return get(client, monkeypatch, pool, "/api/generation/wind/vintages?area_kind=ba&area=CISO").json()


def _asset_wind(client, monkeypatch):
    s = next(s for s in BANKED["wind_sites"] if s["plant_code"] == SUNZIA)
    pool = FakePool([("SET LOCAL", []), ("information_schema.columns", CATALOG_NOW),
                     ("FROM implied_gen_scores", []), ("FROM implied_gen_site_latest", []),
                     ("FROM implied_gen_wind_sites", [s])])
    return get(client, monkeypatch, pool, f"/api/generation/asset?plant_code={SUNZIA}&tech=wind").json()


def _asset_runs_wind(client, monkeypatch):
    return runs(client, monkeypatch)


def _asset_runs_wind_empty(client, monkeypatch):
    return runs(client, monkeypatch, led=[], hist=[])


def _net_demand(client, monkeypatch):
    import load_bank_d091611 as lb
    import test_load_outlook as tl
    monkeypatch.setattr(main, "_utcnow", lambda: tl.NOW)
    for c in main._LOAD_CACHES:
        c.clear()
    return get(client, monkeypatch, lb.pool(lb.load()), "/api/load/net-demand?area=CISO").json()


# Every route whose payload carries a wind figure, with how to serve it. A
# route is in WIND_ROUTES or in NOT_A_WIND_FIGURE, with the reason: the sweep
# fails on any generation, asset or wind route that is in neither.
WIND_ROUTES = {
    "/api/generation/wind/outlook": [_wind_outlook],
    "/api/generation/wind/sites": [_wind_sites],
    "/api/generation/wind/vintages": [_wind_vintages],
    "/api/generation/asset": [_asset_wind],
    "/api/generation/asset/runs": [_asset_runs_wind, _asset_runs_wind_empty],
    "/api/load/net-demand": [_net_demand],
}
NOT_A_WIND_FIGURE = {
    "/api/generation/solar/outlook": "solar only",
    "/api/generation/solar/sites": "solar only",
    "/api/generation/solar/vintages": "solar only",
    "/api/generation/assets": "the search's MW is EIA nameplate, not a figure derived from the curves",
}


def test_H7_the_sweep_covers_every_generation_asset_and_wind_route():
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    candidates = {p for p in paths if re.search(r"/api/generation/|wind|net-demand", p)}
    assert candidates == set(WIND_ROUTES) | set(NOT_A_WIND_FIGURE), candidates ^ (set(WIND_ROUTES) | set(NOT_A_WIND_FIGURE))


@pytest.mark.parametrize("path,serve", [(p, f) for p, fs in WIND_ROUTES.items() for f in fs],
                         ids=lambda x: getattr(x, "__name__", x))
def test_H7_every_wind_payload_carries_the_notice(client, monkeypatch, path, serve):
    b = serve(client, monkeypatch)
    notices = [b.get("attribution")] + list(b.get("attributions") or [])
    assert wo.ATTRIBUTION in notices, (path, list(b)[:12])


def test_H7_the_notice_is_one_text_everywhere():
    import load_outlook as lo
    import vintages as vt
    assert lo.WIND_ATTRIBUTION == wo.ATTRIBUTION
    assert "wo.ATTRIBUTION" in pathlib.Path(vt.__file__).read_text()
    assert "Open Database License (ODbL-1.0)" in wo.ATTRIBUTION


# ═══════════════════════════════════════════════════════════════════════════
# H8 — the re-banked vectors are main's plus exactly the new key
# ═══════════════════════════════════════════════════════════════════════════

def _main_copy(rel):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"main:{rel}"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"no main branch to read {rel} from")
    return json.loads(r.stdout)


@pytest.mark.parametrize("rel,key", [
    ("docs/receipts/asset-page-api-d091635/asset_wind_57514.json", ("plant", "equipment")),
    ("docs/receipts/vintages-d091644/body_wind_ciso_n28.json", ("attribution",)),
])
def test_H8_a_rebanked_vector_is_mains_plus_the_one_key(rel, key):
    new, old = json.loads((ROOT / rel).read_text()), _main_copy(rel)
    d = new
    for k in key[:-1]:
        d = d[k]
    assert key[-1] in d
    d.pop(key[-1])
    assert new == old


def test_H8_the_solar_vectors_are_untouched():
    for rel in ("docs/receipts/asset-page-api-d091635/asset_solar_58388.json",
                "docs/receipts/vintages-d091644/body_solar_ciso_n28.json"):
        assert json.loads((ROOT / rel).read_text()) == _main_copy(rel)


# ═══════════════════════════════════════════════════════════════════════════
# C — D-09-25-75 and the contract
# ═══════════════════════════════════════════════════════════════════════════

def test_C_timeout_first_then_memoised_per_n(client, monkeypatch):
    pool = runs_pool("wind")
    path = f"/api/generation/asset/runs?plant_code={SUNZIA}&tech=wind"
    get(client, monkeypatch, pool, path)
    assert pool.sql_run()[0].startswith("SET LOCAL statement_timeout = '5s'")
    n = len(pool.calls)
    r = get(client, monkeypatch, pool, path)
    assert len(pool.calls) == n and r.headers["X-Cache"] == "hit" and r.json()["cache"]["ttl_seconds"] == 300
    get(client, monkeypatch, pool, path + "&n=8")              # the default is the same key
    assert len(pool.calls) == n
    get(client, monkeypatch, pool, path + "&n=4")
    assert len(pool.calls) > n


def test_C_the_memo_is_bounded_like_the_asset_route():
    c = main._asset_runs_cache
    assert (c.ttl, c.allow_stale, c.max_stale_s) == (main._asset_cache.ttl, True, main._asset_cache.max_stale_s)


def test_C_not_in_the_registry_is_404_before_the_history_is_read(client, monkeypatch):
    pool = runs_pool("wind", plant=[])
    r = get(client, monkeypatch, pool, "/api/generation/asset/runs?plant_code=1&tech=wind")
    assert r.status_code == 404 and "plant_code=1" in r.json()["detail"]
    assert not any("implied_gen_site_history" in q for q, _ in pool.calls)


@pytest.mark.parametrize("qs", ["plant_code=66923", "tech=wind", "plant_code=66923&tech=hydro",
                                "plant_code=abc&tech=wind", "plant_code=0&tech=wind"])
def test_C_bad_params_are_400(client, monkeypatch, qs):
    assert get(client, monkeypatch, runs_pool("wind"), f"/api/generation/asset/runs?{qs}").status_code == 400


def test_C_db_down_is_503(client, monkeypatch):
    class Down:
        def connection(self):
            raise RuntimeError("no route to host")
    r = get(client, monkeypatch, Down(), f"/api/generation/asset/runs?plant_code={SUNZIA}&tech=wind")
    assert r.status_code == 503


def test_C_the_read_has_no_max_and_no_distinct_on():
    for tech in ("wind", "solar"):
        q = ap.runs_sql(tech).lower()
        assert "max(" not in q and "distinct on" not in q


# ═══════════════════════════════════════════════════════════════════════════
# H9 — the SQL pinned, and on a real Postgres
# ═══════════════════════════════════════════════════════════════════════════

PINNED_WIND_SQL = (
    "WITH r AS MATERIALIZED ( SELECT init_ts, n_rows, n_plants, method_version, landed_at, "
    "row_number() OVER (ORDER BY init_ts DESC) AS k FROM implied_gen_site_history_runs "
    "WHERE tech = %(tech)s AND model = %(model)s ORDER BY init_ts DESC LIMIT %(keep)s ) "
    "SELECT r.k, r.init_ts AS run_init_ts, r.n_rows AS run_n_rows, r.n_plants AS run_n_plants, "
    "r.method_version AS run_method_version, r.landed_at AS run_landed_at, h.target_ts, h.lead_h, "
    "h.weather_source, h.implied_mw, h.outage_mw_subtracted, h.cap_mw_subtracted, h.method_version, "
    "h.hub_ws_ms, h.gust_ms FROM r LEFT JOIN LATERAL ( SELECT x.target_ts, x.lead_h, x.weather_source, "
    "x.implied_mw, x.outage_mw_subtracted, x.cap_mw_subtracted, x.method_version, x.hub_ws_ms, x.gust_ms "
    "FROM implied_gen_site_history x WHERE x.tech = %(tech)s AND x.plant_code = %(plant_code)s "
    "AND x.model = %(model)s AND x.init_ts = r.init_ts AND r.k <= %(n)s OFFSET 0 ) h ON true "
    "ORDER BY r.init_ts, h.target_ts")


def test_H9_the_runs_sql_is_pinned():
    """The statement the plan receipts measured (docs/receipts/asset-runs-d091667/plans.md)."""
    assert " ".join(ap.runs_sql("wind").split()) == PINNED_WIND_SQL
    solar = " ".join(ap.runs_sql("solar").split())
    for c in ap.HOUR_DRIVERS["solar"]:
        assert f"h.{c}" in solar and f"x.{c}" in solar
    assert "OFFSET 0" in solar


_DDL = """
CREATE TABLE implied_gen_site_history_runs (
    tech text NOT NULL, model text NOT NULL, init_ts timestamptz NOT NULL, n_rows integer NOT NULL,
    n_plants integer NOT NULL, method_version text NOT NULL, landed_at timestamptz NOT NULL,
    PRIMARY KEY (tech, model, init_ts));
CREATE TABLE implied_gen_site_history (
    tech text NOT NULL, plant_code integer NOT NULL, model text NOT NULL,
    target_ts timestamptz NOT NULL, init_ts timestamptz NOT NULL, lead_h smallint NOT NULL,
    implied_mw double precision NOT NULL, outage_mw_subtracted double precision NOT NULL,
    method_version text NOT NULL, weather_source text, cap_mw_subtracted double precision,
    ghi_wm2 double precision, clearsky_ghi_wm2 double precision, clearsky_mw double precision,
    tcc_pct double precision, precip_mm double precision, hub_ws_ms double precision,
    gust_ms double precision,
    PRIMARY KEY (tech, plant_code, model, init_ts, target_ts)) PARTITION BY RANGE (tech, model, init_ts);
"""


def _partition(conn, tech, model, init):
    name = f"implied_gen_site_history_{tech}_{model}_{init:%Y%m%d%H}"
    conn.execute(f"CREATE TABLE {name} PARTITION OF implied_gen_site_history FOR VALUES "
                 f"FROM ('{tech}', '{model}', '{init.isoformat()}') "
                 f"TO ('{tech}', '{model}', '{(init + timedelta(microseconds=1)).isoformat()}')")


@pytest.fixture(scope="module")
def pg():
    initdb = shutil.which("initdb") or "/usr/lib/postgresql/16/bin/initdb"
    if not os.path.exists(initdb):
        pytest.skip("no local Postgres (initdb) on this box")
    psycopg = pytest.importorskip("psycopg")
    bindir = os.path.dirname(initdb)
    d = tempfile.mkdtemp(prefix="pg_runs_")
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
        _seed(conn)
        yield conn
    finally:
        conn.close()
        subprocess.run(as_pg + [os.path.join(bindir, "pg_ctl"), "-D", data, "-m", "immediate",
                                "stop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        shutil.rmtree(d, ignore_errors=True)


_COLS = ("tech", "plant_code", "model", "target_ts", "init_ts", "lead_h", "implied_mw",
         "outage_mw_subtracted", "method_version", "weather_source", "cap_mw_subtracted",
         "ghi_wm2", "clearsky_ghi_wm2", "clearsky_mw", "tcc_pct", "precip_mm", "hub_ws_ms", "gust_ms")


def _seed(conn):
    with conn.cursor() as cur:
        for tech in ("wind", "solar"):
            for r in ledger(tech):
                _partition(conn, r["tech"], r["model"], r["init_ts"])
                cur.execute("INSERT INTO implied_gen_site_history_runs VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (r["tech"], r["model"], r["init_ts"], r["n_rows"], r["n_plants"],
                             r["method_version"], r["landed_at"]))
            rows = [{c: None for c in _COLS} | h | {"tech": DB_TECH[tech], "plant_code": CODE[tech],
                                                     "model": ap.MODELS[tech]} for h in history(tech)]
            # a neighbour plant on the same partitions, which the read must not see
            rows += [{**r, "plant_code": CODE[tech] + 1, "implied_mw": -1.0} for r in rows]
            cur.executemany(f"INSERT INTO implied_gen_site_history ({', '.join(_COLS)}) VALUES "
                            f"({', '.join('%(' + c + ')s' for c in _COLS)})", rows)
        # another model's run of SunZia, with its own ledger row: never a vintage of hrrr_gfs (D-09-25-173)
        i = T("2026-10-08T09:00:00Z")
        _partition(conn, "wind", "gfs", i)
        cur.execute("INSERT INTO implied_gen_site_history_runs VALUES ('wind','gfs',%s,1,1,'wind_v1',%s)", (i, i))
        cur.execute("INSERT INTO implied_gen_site_history (tech, plant_code, model, target_ts, init_ts, lead_h, "
                    "implied_mw, outage_mw_subtracted, method_version, weather_source) VALUES "
                    "('wind', %s, 'gfs', %s, %s, 1, 9999, 0, 'wind_v1', 'gfs_100m')", (SUNZIA, i, i))
        conn.execute("ANALYZE")


@pytest.mark.parametrize("tech", ["wind", "solar"])
def test_PG_H9_the_statement_returns_what_the_shaper_was_tested_on(pg, tech):
    rows = pg.execute(ap.runs_sql(tech), ap.runs_params(tech, CODE[tech], 8)).fetchall()
    assert len(rows) == 720
    assert {r["run_init_ts"] for r in rows} == {r["init_ts"] for r in ledger(tech)}
    assert all(r["implied_mw"] != -1.0 and r["implied_mw"] != 9999 for r in rows)
    want = sql_rows(tech)
    assert [{k: r[k] for k in want[0]} for r in rows] == want
    assert ap.build_runs(tech=tech, plant_code=CODE[tech], plant_name="x", n=8, rows=rows) == \
        ap.build_runs(tech=tech, plant_code=CODE[tech], plant_name="x", n=8, rows=want)


def test_PG_H9_n_limits_the_runs_read_not_the_ledger(pg):
    rows = pg.execute(ap.runs_sql("wind"), ap.runs_params("wind", SUNZIA, 1)).fetchall()
    assert len({r["run_init_ts"] for r in rows}) == 3
    assert len([r for r in rows if r["target_ts"] is not None]) == 240
    assert {r["run_init_ts"] for r in rows if r["target_ts"] is not None} == {T("2026-10-08T12:00:00Z")}


def _plan(pg, sql, n=8):
    return "\n".join(next(iter(r.values())) for r in pg.execute(
        "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) " + sql,
        ap.runs_params("wind", SUNZIA, n)).fetchall())


_SCAN = re.compile(r"Scan (?:using \w+ )?on (implied_gen_site_history_\w+) x_\d+ \(actual rows=(\d+)(?:\.\d+)? loops=(\d+)\)")


def test_PG_H9_each_run_prunes_to_its_own_partition(pg):
    """Each run's loop runs one partition: every partition scan runs once
    while the Append runs once per run, and each carries init_ts = r.init_ts.
    (Neon's plan, receipts/plans.md, is the same shape on its index scans.)"""
    plan = _plan(pg, ap.runs_sql("wind"))
    scans = _SCAN.findall(plan)
    assert {s[0] for s in scans} == {f"implied_gen_site_history_wind_hrrr_gfs_2026100{h}" for h in ("800", "806", "812")}
    assert all(loops == "1" and rows == "240" for _, rows, loops in scans), plan
    assert re.search(r"Append \(actual rows=240(?:\.0+)? loops=3\)", plan), plan
    assert plan.count("init_ts = r.init_ts") == 3
    assert "solar_pv" not in plan and "wind_gfs" not in plan            # pruned on (tech, model)


def test_PG_H9_n_keeps_older_runs_off_their_partitions(pg):
    plan = _plan(pg, ap.runs_sql("wind"), n=1)
    assert plan.count("(never executed)") == 2, plan


def test_PG_H9_unfenced_no_run_prunes(pg):
    """The fence is load-bearing: without OFFSET 0 the LATERAL is pulled up,
    init_ts leaves every partition's condition for the join, and no run is
    pruned to its partition (on Neon: each run walked every partition)."""
    plan = _plan(pg, ap.runs_sql("wind").replace("OFFSET 0", ""))
    assert "init_ts = r.init_ts" not in plan.split("Append")[1].split("Hash (")[0], plan
    assert "(x.init_ts = r.init_ts)" in plan or "Join Filter: (x.init_ts = r.init_ts)" in plan, plan


def test_PG_H9_the_registry_reads(pg):
    pg.execute("CREATE TABLE IF NOT EXISTS implied_gen_wind_sites (plant_code integer PRIMARY KEY, plant_name text)")
    pg.execute("CREATE TABLE IF NOT EXISTS implied_gen_sites (tech text, plant_code integer, generator_id text, "
               "plant_name text, PRIMARY KEY (tech, plant_code, generator_id))")
    pg.execute("INSERT INTO implied_gen_wind_sites VALUES (66923, 'SunZia Wind South') ON CONFLICT DO NOTHING")
    pg.execute("INSERT INTO implied_gen_sites VALUES ('solar_pv', 58388, 'SS11', 'Solar Star 1'), "
               "('solar_pv', 58388, 'SS12', 'Solar Star 1') ON CONFLICT DO NOTHING")
    assert pg.execute(ap.RUNS_PLANT_SQL["wind"], {"plant_code": 66923}).fetchall() == \
        [{"plant_code": 66923, "plant_name": "SunZia Wind South"}]
    assert pg.execute(ap.RUNS_PLANT_SQL["solar"], {"tech": "solar_pv", "plant_code": 58388}).fetchall() == \
        [{"plant_code": 58388, "plant_name": "Solar Star 1"}]
