"""d091666 — the degree-day board's scores, blend, band and desk routes.

  S1  a scored cell and an unscored cell both ride through with their flags;
      nothing unscored is given a MAE
  S2  the blend is served from v_dd_blend_drawable only (pantry 282/283: "Read
      blends ONLY through v_dd_blend_drawable"); a row the view does not hold
      is never served, never given a value, and what is not served is stated
  S3  degree_days.SOURCE_LABELS is the only place a label is made (a sweep
      over every dd module and main.py's dd section)
  S4  the band is a stated absence: forecasts_gefs holds no member values at
      our places (STOP-B), so no band, no percentile, no read
  S5  the desk's change is the delta view's row, never a difference made
      here; its values and period sums are the region board's
  S6  every new read's SQL is pinned, and is the SQL the plan receipts ran
  S7  the existing dd routes are unchanged (their own test files run as they
      are beside this one)

Bank tests run the routes over production rows read on 2026-10-08
(tests/fixtures/dd_board_d091666, each file checked against an md5 Neon
computed). Route tests use tests/test_solar_outlook.py's fake pool. The
rehearsal is docs/receipts/dd-board-d091666/rehearse.py.
"""

import hashlib
import json
import pathlib
import re
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import dd_board as db
import degree_days as dd
import main
from test_solar_outlook import FakePool

ROOT = pathlib.Path(__file__).resolve().parents[1]
BANK = ROOT / "tests" / "fixtures" / "dd_board_d091666"
RECEIPTS = ROOT / "docs" / "receipts" / "dd-board-d091666"
TODAY = date(2026, 10, 8)

CACHES = ("_dd_scores_cache", "_dd_blend_cache", "_dd_desk_cache",
          "_dd_region_fc_cache", "_dd_region_vectors_cache")


@pytest.fixture(autouse=True)
def _cold_memos(monkeypatch):
    monkeypatch.setattr(main, "_dd_today_pt", lambda: TODAY, raising=False)
    caches = [getattr(main, c) for c in CACHES if hasattr(main, c)]
    for c in caches:
        c.clear()
    yield
    for c in caches:
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


# ── the bank ────────────────────────────────────────────────────────────────

def banked(name):
    """A banked file's text, checked against the md5 Neon computed."""
    for line in (BANK / "manifest.psv").read_text().splitlines():
        f, n, md5, ln = line.split("|")
        if f == name:
            t = (BANK / f).read_text().rstrip("\n")
            assert hashlib.md5(t.encode()).hexdigest() == md5 and len(t) == int(ln), f
            rows = json.loads(t)
            assert len(rows) == int(n)
            return rows
    raise KeyError(name)


def D(s):
    return date.fromisoformat(s)


def TS(s):
    return None if s is None else datetime.fromisoformat(s)


SCORE_COLS = ("member", "lead_day", "place", "n_target_days", "n_pairs",
              "n_provisional_days", "scored", "bias_tavg_f", "mae_tavg_f",
              "rmse_tavg_f", "mae_hdd", "mae_cdd", "excluded")


def score_rows(place_kind, weighting=None):
    """v_dd_member_scores_current rows as the route selects them."""
    v = json.loads((BANK / "vintage.json").read_text())
    shared = {"as_of_date": D(v["as_of_date"]), "ghcn_frontier": D(v["ghcn_frontier"]),
              "window_start": D(v["window_start"]), "window_end": D(v["window_end"]),
              "min_target_days": v["min_target_days"], "scorer_version": v["scorer_version"],
              "scorer_hash": v["scorer_hash"],
              "blend_method_version": v["blend_method_version"],
              "created_at": TS(v["created_at"])}
    if place_kind == "station":
        files = [("scores_station.json", "")]
    else:
        files = [(f"scores_region_{w}.json", w) for w in ("load_share_365d", "population")
                 if weighting in (None, w)]
    out = []
    for f, w in files:
        for a in banked(f):
            out.append({**shared, **dict(zip(SCORE_COLS, a)),
                        "place_kind": place_kind, "weighting": w})
    return sorted(out, key=lambda r: (r["place"], r["weighting"], r["member"], r["lead_day"]))


BLEND_COLS = ("station_id", "target_date", "issue_date", "lead_day", "method_version",
              "method_hash", "tavg_f", "hdd", "cdd", "n_members_used", "blend_mae",
              "best_member", "best_member_mae", "blend_n_target_days", "computed_ts")


def blend_rows():
    out = []
    for a in banked("blend_drawable.json"):
        r = dict(zip(BLEND_COLS, a))
        r.update(target_date=D(r["target_date"]), issue_date=D(r["issue_date"]),
                 computed_ts=TS(r["computed_ts"]))
        out.append(r)
    return out


def board_rows():
    fc = [{**r, "target_date": D(r["target_date"]), "issued_ts": TS(r["issued_ts"])}
          for r in banked("board_fc_pnw_population.json")]
    dl = [{**r, "target_date": D(r["target_date"]), "issued_ts": TS(r["issued_ts"]),
           "prior_issued_ts": TS(r["prior_issued_ts"])}
          for r in banked("board_delta_pnw_population.json")]
    sp = [{**r, "target_date": D(r["target_date"])}
          for r in banked("board_spread_pnw_population.json")]
    return fc, dl, sp


def pool(*, scores=None, blend=None, board=None):
    """The fake pool: each read answers from the bank by its own predicate."""
    by_kind = scores if scores is not None else {
        "station": score_rows("station"), "region": score_rows("region")}
    fc, dl, sp = board if board is not None else board_rows()
    return FakePool([
        ("SET LOCAL", []),
        ("FROM v_dd_member_scores_current", lambda p: list(by_kind.get(p["place_kind"], []))),
        ("FROM v_dd_blend_drawable",
         lambda p: [r for r in (blend if blend is not None else blend_rows())
                    if p["from_date"] <= r["target_date"] < p["to_date"]
                    and p["station"] in (None, r["station_id"])]),
        ("FROM degree_day_region_weights", banked("vectors.json")),
        ("FROM v_degree_days_model_delta", dl),
        ("FROM v_degree_days_model_spread", sp),
        ("FROM v_degree_days_region_forecast", fc),
    ])


def get(client, monkeypatch, p, path, params=None):
    monkeypatch.setattr(main, "_pool", p)
    return client.get(path, params=params)


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# S1 — scored and unscored cells ride through; nothing unscored has a MAE
# ═══════════════════════════════════════════════════════════════════════════

def cell(body, place_kind, place, weighting, member, lead):
    p = next(x for x in body["places"] if (x["place_kind"], x["place"], x["weighting"])
             == (place_kind, place, weighting))
    return next(c for c in p["cells"] if (c["member"], c["lead_day"]) == (member, lead))


def test_S1_a_scored_and_an_unscored_cell_ride_through_with_their_flags(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/dd/scores"))
    view = {(r["place_kind"], r["place"], r["weighting"], r["member"], r["lead_day"]): r
            for k in ("station", "region") for r in score_rows(k)}
    s = cell(body, "region", "pnw", "population", "EL_BLEND", 0)
    u = cell(body, "region", "pnw", "population", "EL_BLEND", 6)
    assert s["scored"] is True and u["scored"] is False
    for c, lead in ((s, 0), (u, 6)):
        v = view[("region", "pnw", "population", "EL_BLEND", lead)]
        for k in ("n_target_days", "n_pairs", "n_provisional_days", "scored", "bias_tavg_f",
                  "mae_tavg_f", "rmse_tavg_f", "mae_hdd", "mae_cdd", "excluded"):
            assert c[k] == v[k], (lead, k)
    assert s["mae_tavg_f"] == pytest.approx(0.649, abs=5e-4)   # measured 2026-10-08
    assert (u["n_target_days"], u["n_pairs"]) == (11, 11)        # 11 < 14: not yet scored


def test_S1_nothing_unscored_is_given_a_mae(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/dd/scores"))
    cells = [c for p in body["places"] for c in p["cells"]]
    assert (body["cell_count"], body["scored_count"]) == (2880, 2038) == \
        (len(cells), sum(c["scored"] for c in cells))
    for c in cells:
        if not c["scored"]:
            assert all(c[k] is None for k in db.SCORE_METRICS), c
    # the view's counts per kind, as measured
    kinds = {}
    for p in body["places"]:
        kinds[p["place_kind"]] = kinds.get(p["place_kind"], 0) + len(p["cells"])
    assert kinds == {"station": 2016, "region": 864}


def test_S1_excluded_and_the_vintage_ride_through(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/dd/scores"))
    v = json.loads((BANK / "vintage.json").read_text())
    (vin,) = body["vintages"]
    assert vin == {"vintage": 0, **v}
    assert all(c["vintage"] == 0 for p in body["places"] for c in p["cells"])
    with_excluded = [c for p in body["places"] if p["place_kind"] == "station"
                     for c in p["cells"] if c["excluded"]]
    assert with_excluded and all(isinstance(c["excluded"], dict) for c in with_excluded)
    assert body["sources"]["tables"] == ["v_dd_member_scores_current"]
    assert body["sources"]["scorer_versions"] == ["dd_member_scores_v2"]
    assert body["sources"]["blend_method_versions"] == ["el_blend_v2"]
    assert body["lead_rule"] == db.LEAD_RULE


def test_S1_one_read_per_place_kind_each_under_the_statement_timeout(client, monkeypatch):
    p = pool()
    ok(get(client, monkeypatch, p, "/api/weather/dd/scores"))
    reads = [(q, a) for q, a in p.calls if "v_dd_member_scores_current" in q]
    assert sorted(a["place_kind"] for _q, a in reads) == ["region", "station"]
    assert all(q == db.SCORES_SQL for q, _a in reads)
    sets = [q for q, _a in p.calls if "SET LOCAL statement_timeout" in q]
    assert len(sets) == len(reads)
    assert all(f"'{main._DD_REGION_FC_STATEMENT_TIMEOUT}'" in q for q in sets)


def test_S1_place_kind_names_the_one_read(client, monkeypatch):
    p = pool()
    body = ok(get(client, monkeypatch, p, "/api/weather/dd/scores", {"place_kind": "region"}))
    assert {x["place_kind"] for x in body["places"]} == {"region"}
    assert [a["place_kind"] for q, a in p.calls if "v_dd_member_scores_current" in q] == ["region"]
    r = get(client, monkeypatch, p, "/api/weather/dd/scores", {"place_kind": "county"})
    assert r.status_code == 400 and r.json()["detail"].startswith("place_kind:")


def test_S1_scores_memo_ttl_and_no_scores_is_an_absence(client, monkeypatch):
    p = pool(scores={})
    r1 = get(client, monkeypatch, p, "/api/weather/dd/scores")
    r2 = get(client, monkeypatch, p, "/api/weather/dd/scores")
    assert [r.headers["X-Cache"] for r in (r1, r2)] == ["miss", "hit"]
    body = r1.json()
    assert body["cache"]["ttl_seconds"] == main._DD_SCORES_TTL == 900.0
    assert body["places"] == [] and body["absence"]["reason"] == "no_scores"


# ═══════════════════════════════════════════════════════════════════════════
# S2 — the blend: the drawable view only, and what is not served is stated
# ═══════════════════════════════════════════════════════════════════════════

def blend(client, monkeypatch, p=None, **params):
    params.setdefault("from", "2026-10-08")
    return ok(get(client, monkeypatch, p or pool(), "/api/weather/dd/blend", params))


def test_S2_served_rows_are_the_drawable_views_rows_as_written(client, monkeypatch):
    body = blend(client, monkeypatch)
    want = [r for r in blend_rows() if D("2026-10-08") <= r["target_date"] < D("2026-10-24")]
    assert len(want) == 154 == body["row_count"] == len(body["rows"])   # measured
    for got, r in zip(body["rows"], want):
        assert got["station_id"] == r["station_id"]
        assert got["target_date"] == r["target_date"].isoformat()
        assert got["issue_date"] == r["issue_date"].isoformat()
        for k in ("lead_day", "tavg_f", "hdd", "cdd", "n_members_used", "blend_mae",
                  "best_member", "best_member_mae", "blend_n_target_days",
                  "method_version", "method_hash"):
            assert got[k] == r[k], k
        assert got["computed_ts"] == r["computed_ts"].isoformat()


def test_S2_the_blend_never_reads_dd_blend_forecast(client, monkeypatch):
    p = pool()
    blend(client, monkeypatch, p)
    reads = [q for q, _a in p.calls if "SET LOCAL" not in q]
    assert reads == [db.BLEND_SQL]
    for name in dir(db):
        if name.endswith("_SQL"):
            assert "dd_blend_forecast" not in getattr(db, name), name


def test_S2_a_row_the_view_does_not_hold_is_never_served(client, monkeypatch):
    """Issue 2026-10-08 holds leads 0-14 for 21 stations in dd_blend_forecast
    (315 rows, measured); the drawable view holds 66 of them. The other 249,
    scored or not, with a value or without, are not in the payload at all."""
    body = blend(client, monkeypatch)
    held = {(r["station_id"], r["target_date"].isoformat(), r["lead_day"])
            for r in blend_rows()}
    served = {(r["station_id"], r["target_date"], r["lead_day"]) for r in body["rows"]}
    assert served <= held
    issued_today = [r for r in body["rows"] if r["issue_date"] == "2026-10-08"]
    assert len(issued_today) == 66
    assert max(r["lead_day"] for r in body["rows"]) == 5        # leads 6+ are unscored
    ns = body["not_served"]
    for k in ("no_blend_reason", "members", "scored", "beats_best_member", "drawable"):
        assert k in ns["fields"]
    assert "v_dd_blend_drawable" in ns["why"] and ns["pantry_proposal"]
    assert set(body["rows"][0]) == set(db.BLEND_ROW_KEYS)


def test_S2_no_drawable_blend_is_an_absence(client, monkeypatch):
    body = blend(client, monkeypatch, pool(blend=[]))
    assert body["rows"] == [] and body["absence"]["reason"] == "no_drawable_blend"
    assert body["region_blend"]["banked"] is False


def test_S2_one_station_names_the_read(client, monkeypatch):
    p = pool()
    body = blend(client, monkeypatch, p, station="USW00024233")
    assert {r["station_id"] for r in body["rows"]} == {"USW00024233"}
    (args,) = [a for q, a in p.calls if q == db.BLEND_SQL]
    assert args == {"station": "USW00024233", "from_date": D("2026-10-08"),
                    "to_date": D("2026-10-24")}


@pytest.mark.parametrize("params,field", [({"station": "pnw"}, "station"),
                                          ({"days": "31"}, "days"),
                                          ({"days": "0"}, "days"),
                                          ({"from": "2026-13-01"}, "from"),
                                          ({"from": "2025-01-01"}, "from")])
def test_S2_bad_params_are_400s_naming_the_field(client, monkeypatch, params, field):
    r = get(client, monkeypatch, pool(), "/api/weather/dd/blend", params)
    assert r.status_code == 400 and r.json()["detail"].startswith(f"{field}:")


# ═══════════════════════════════════════════════════════════════════════════
# S3 — SOURCE_LABELS is the only place a label is made
# ═══════════════════════════════════════════════════════════════════════════

def dd_sources():
    """Every dd module, and main.py's dd section, as (name, lines)."""
    out = [(p.name, p.read_text().splitlines())
           for p in (ROOT / "degree_days.py", ROOT / "vintages.py", ROOT / "dd_board.py")]
    text = (ROOT / "main.py").read_text()
    start = text.index("# /api/weather/dd/* — the Degree Day Ledger")
    end = text.index("# NODE ANALYTICS API v0")
    out.append(("main.py[dd]", text[start:end].splitlines()))
    return out


def test_S3_every_label_in_the_dd_section_is_made_by_source_label():
    hits = 0
    for name, lines in dd_sources():
        for n, line in enumerate(lines, 1):
            # a source's or member's printed label: "label", "best_member_label",
            # ... (not normals_window_label, which names a normals window)
            if re.search(r"""["'](\w*(member|source)\w*_)?label["']\s*:""", line):
                hits += 1
                assert "source_label(" in line, (name, n, line)
    assert hits >= 4        # forecast/regions, vintages, scores members, blend best_member


def test_S3_labels_reach_the_payloads_through_source_labels(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/dd/scores"))
    labels = {m["member"]: m["label"] for m in body["members"]}
    assert labels == {"AIFS": "AIFS", "EL_BLEND": "EL_BLEND", "GFS": "GFS", "IFS": "IFS",
                      "NBM": "NBM", "gridpoints_raw": "NWS"}
    assert body["source_labels"] == dd.SOURCE_LABELS == {"gridpoints_raw": "NWS"}
    rows = blend(client, monkeypatch)["rows"]
    nws = [r for r in rows if r["best_member"] == "gridpoints_raw"]
    assert nws and all(r["best_member_label"] == "NWS" for r in nws)


# ═══════════════════════════════════════════════════════════════════════════
# S4 — the band: STOP-B, a stated absence
# ═══════════════════════════════════════════════════════════════════════════

def numbers(x):
    if isinstance(x, bool):
        return []
    if isinstance(x, (int, float)):
        return [x]
    if isinstance(x, dict):
        return [n for v in x.values() for n in numbers(v)]
    if isinstance(x, list):
        return [n for v in x for n in numbers(v)]
    return []


def test_S4_the_band_is_an_absence_and_reads_nothing(client, monkeypatch):
    p = pool()
    body = ok(get(client, monkeypatch, p, "/api/weather/dd/band"))
    assert body["band"] is None
    assert body["absence"]["reason"] == "not_banked"
    assert p.calls == []                       # nothing read, so nothing guessed
    assert numbers({k: v for k, v in body.items() if k != "banked"}) == []
    assert "P5" in body["absence"]["detail"] and "member" in body["absence"]["detail"]


def test_S4_the_band_never_relabels_the_banked_p10_p90():
    body = db.band_payload()
    assert body["percentile_rule"] is None and body["band"] is None

    def keys(x):
        if isinstance(x, dict):
            return set(x) | {k for v in x.values() for k in keys(v)}
        if isinstance(x, list):
            return {k for v in x for k in keys(v)}
        return set()
    assert not {k for k in keys(body) if re.fullmatch(r"p\d+", k.lower())}
    assert body["banked"]["statistics"] == ["mean", "p10", "p50", "p90"]
    assert body["requires"]


# ═══════════════════════════════════════════════════════════════════════════
# S5 — the desk: the delta view's change, the board's values, the score cell
# ═══════════════════════════════════════════════════════════════════════════

def desk(client, monkeypatch, p=None, **params):
    params.setdefault("region", "pnw")
    params.setdefault("from", "2026-10-08")
    return ok(get(client, monkeypatch, p or pool(), "/api/weather/dd/desk", params))


@pytest.mark.parametrize("src", db.DESK_SOURCES)
def test_S5_the_desks_change_is_the_delta_views_row(client, monkeypatch, src):
    body = desk(client, monkeypatch, source=src)
    (reg,) = body["regions"]
    fc, dl, _sp = board_rows()
    fc_by = {(r["target_date"].isoformat(), r["source_product"]): r for r in fc}
    dl_by = {(r["target_date"].isoformat(), r["source_product"]): r for r in dl}
    n = 0
    for d in reg["days"]:
        f = fc_by.get((d["target_date"], src))
        if f is None:
            assert d["hdd"] is None and d["absence"]["reason"] == "no_issuance"
            continue
        row = dl_by[(d["target_date"], src)]
        assert d["issued_ts"] == f["issued_ts"].isoformat()
        if row["prior_issued_ts"] is not None and row["hdd_delta"] is not None:
            assert d["change"]["hdd"] == row["hdd_delta"]
            assert d["change"]["cdd"] == row["cdd_delta"]
            assert d["change"]["prior_issued_ts"] == row["prior_issued_ts"].isoformat()
            n += 1
        else:
            assert d["change"] is None and d["change_absence"]["reason"]
    assert n > 0


def test_S5_the_change_is_never_a_difference_made_here(client, monkeypatch):
    """Put a delta the view could never compute beside the levels: the desk
    serves it. A desk that subtracted would serve hdd - prior instead."""
    fc, dl, sp = board_rows()
    dl = [{**r, "hdd_delta": 123.25, "cdd_delta": -7.5} if r["source_product"] == "GFS" else r
          for r in dl]
    body = desk(client, monkeypatch, pool(board=(fc, dl, sp)), source="GFS")
    changes = [d["change"] for d in body["regions"][0]["days"] if d["change"]]
    assert changes and all((c["hdd"], c["cdd"]) == (123.25, -7.5) for c in changes)


def test_S5_values_and_periods_are_the_region_boards(client, monkeypatch):
    p = pool()
    board = ok(get(client, monkeypatch, p, "/api/weather/dd/forecast/regions",
                   {"region": "pnw", "from": "2026-10-08"}))
    body = desk(client, monkeypatch, p, source="NBM")
    (b,), (reg,) = board["regions"], body["regions"]
    for bd, d in zip(b["days"], reg["days"]):
        c = bd["by_source"].get("NBM")
        assert d["target_date"] == bd["target_date"]
        assert (d["hdd"], d["cdd"]) == ((c["hdd"], c["cdd"]) if c else (None, None))
    for bp, dp in zip(b["periods"], reg["periods"]):
        assert dp == {"window": bp["window"], "from": bp["from"], "to": bp["to"],
                      **bp["by_source"]["NBM"]}
    assert body["period_rule"] == dd.PERIOD_RULE


def test_S5_the_desk_and_the_board_share_one_build(client, monkeypatch):
    p = pool()
    desk(client, monkeypatch, p, source="GFS")
    r = get(client, monkeypatch, p, "/api/weather/dd/forecast/regions",
            {"region": "pnw", "from": "2026-10-08"})
    assert r.headers["X-Cache"] == "hit"
    assert sum("FROM v_degree_days_region_forecast" in q for q, _a in p.calls) == 1


def test_S5_the_score_cell_is_the_views_cell_for_the_scorers_lead(client, monkeypatch):
    body = desk(client, monkeypatch, source="GFS")
    view = {(r["member"], r["lead_day"]): r for r in score_rows("region", "population")
            if r["place"] == "pnw"}
    states = set()
    for d in body["regions"][0]["days"]:
        if d["issued_ts"] is None:
            continue
        lead = (D(d["target_date"]) - TS(d["issued_ts"]).date()).days   # UTC issuance date
        assert d["lead_day"] == lead
        s = d["score"]
        states.add(s["state"])
        if lead > 15:
            assert s["state"] == "no_score_cell"
            continue
        v = view[("GFS", lead)]
        assert s["state"] == ("scored" if v["scored"] else "not_yet_scored")
        for k in ("n_target_days", "mae_tavg_f", "mae_hdd", "mae_cdd", "bias_tavg_f"):
            assert s[k] == v[k]
    assert "scored" in states


def test_S5_not_yet_scored_has_no_metric(client, monkeypatch):
    body = desk(client, monkeypatch, source="gridpoints_raw")
    nys = [d["score"] for d in body["regions"][0]["days"]
           if d["score"] and d["score"]["state"] == "not_yet_scored"]
    assert nys
    for s in nys:
        assert all(s[k] is None for k in db.SCORE_METRICS)


def test_S5_the_state_is_the_views_flag_not_a_count(client, monkeypatch):
    """A cell the view marks unscored is not yet scored whatever its counts
    say: GFS pnw lead 0 has 28 target days; flip only its flag (and its
    metrics, as pantry's CHECK would) and the desk must follow the flag."""
    region = [{**r, "scored": False, **{k: None for k in db.SCORE_METRICS}}
              if (r["place"], r["weighting"], r["member"], r["lead_day"])
              == ("pnw", "population", "GFS", 0) else r
              for r in score_rows("region")]
    p = pool(scores={"station": score_rows("station"), "region": region})
    d0 = desk(client, monkeypatch, p, source="GFS")["regions"][0]["days"][0]
    assert (d0["lead_day"], d0["score"]["n_target_days"]) == (0, 28)
    assert d0["score"]["state"] == "not_yet_scored"
    assert all(d0["score"][k] is None for k in db.SCORE_METRICS)


@pytest.mark.parametrize("params,field,needle", [
    ({}, "source", "required"),
    ({"source": "EL_BLEND"}, "source", "per station"),
    ({"source": "HRRR"}, "source", "one of"),
    ({"source": "GFS", "region": "atlantis"}, "region", "pnw"),
    ({"source": "GFS", "days": "31"}, "days", "cap 30")])
def test_S5_desk_params_are_400s_naming_the_field(client, monkeypatch, params, field, needle):
    params = {"region": "pnw", **params}
    r = get(client, monkeypatch, pool(), "/api/weather/dd/desk", params)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith(f"{field}:") and needle in r.json()["detail"]


def test_S5_desk_names_its_sources_and_versions(client, monkeypatch):
    body = desk(client, monkeypatch, source="IFS")
    assert body["sources"]["tables"] == [
        "v_degree_days_region_forecast", "v_degree_days_model_delta",
        "v_degree_days_model_spread", "v_dd_member_scores_current"]
    assert body["sources"]["scorer_versions"] == ["dd_member_scores_v2"]
    assert body["source_product"] == "IFS" and body["label"] == "IFS"
    assert body["inputs"]["board"]["built_at"] and body["inputs"]["scores"]["built_at"]


# ═══════════════════════════════════════════════════════════════════════════
# S6 — the new reads are pinned, and are the SQL the plans ran
# ═══════════════════════════════════════════════════════════════════════════

PINNED = {
    "SCORES_SQL": (
        "SELECT as_of_date, ghcn_frontier, member, lead_day, place_kind, place, weighting, "
        "window_start, window_end, min_target_days, n_target_days, n_pairs, "
        "n_provisional_days, scored, bias_tavg_f, mae_tavg_f, rmse_tavg_f, mae_hdd, "
        "mae_cdd, excluded, scorer_version, scorer_hash, blend_method_version, created_at "
        "FROM v_dd_member_scores_current WHERE place_kind = %(place_kind)s "
        "ORDER BY place, weighting, member, lead_day"),
    "BLEND_SQL": (
        "SELECT station_id, target_date, issue_date, lead_day, method_version, method_hash, "
        "tavg_f, hdd, cdd, n_members_used, blend_mae, best_member, best_member_mae, "
        "blend_n_target_days, computed_ts FROM v_dd_blend_drawable "
        "WHERE target_date >= %(from_date)s AND target_date < %(to_date)s "
        "AND (%(station)s::text IS NULL OR station_id = %(station)s) "
        "ORDER BY station_id, target_date, lead_day"),
}


def flat(s):
    return " ".join(s.split())


def test_S6_every_new_read_is_pinned():
    got = {n: flat(getattr(db, n)) for n in dir(db) if n.endswith("_SQL")}
    assert got == PINNED


def test_S6_the_plan_receipts_ran_the_pinned_sql():
    """explains.sql: each statement follows `-- <tag> <NAME> <json params>`.
    The pinned SQL with those literals substituted must be the statement
    that EXPLAIN (ANALYZE, BUFFERS) ran, and every read must have a plan."""
    text = (RECEIPTS / "explains.sql").read_text()
    blocks = re.findall(r"^-- (\w+) (\w+_SQL) (\{.*?\})\n(.*?);\s*$", text, re.M | re.S)
    assert blocks and {name for _t, name, _p, _s in blocks} == set(PINNED)
    for tag, name, params, stmt in blocks:
        want = re.sub(r"%\((\w+)\)s", lambda m: json.loads(params)[m.group(1)], PINNED[name])
        assert flat(stmt) == "EXPLAIN (ANALYZE, BUFFERS) " + want, tag
    plans = (RECEIPTS / "plans_raw.txt").read_text()
    for tag, *_ in blocks:
        assert f"== {tag} " in plans and "Execution Time" in plans, tag


# ═══════════════════════════════════════════════════════════════════════════
# S7 — the existing routes are unchanged
# ═══════════════════════════════════════════════════════════════════════════

def test_S7_forecast_regions_gains_no_blend_source(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/dd/forecast/regions",
                  {"region": "pnw", "from": "2026-10-08"}))
    srcs = [s["source_product"] for s in body["regions"][0]["sources"]]
    assert srcs == ["AIFS", "GFS", "IFS", "NBM", "gridpoints_raw"]
    assert body["source_labels"] == {"gridpoints_raw": "NWS"}
    paths = {r.path for r in main.app.routes}
    for path in ("/api/weather/dd/forecast", "/api/weather/dd/forecast/regions",
                 "/api/weather/dd/forecast/regions/vintages", "/api/weather/dd/scores",
                 "/api/weather/dd/blend", "/api/weather/dd/band", "/api/weather/dd/desk"):
        assert path in paths


# ═══════════════════════════════════════════════════════════════════════════
# The banked bodies (the dashboard lane's vectors)
# ═══════════════════════════════════════════════════════════════════════════

BODIES = [("body_scores.json", "/api/weather/dd/scores", {}),
          ("body_blend_from_2026-10-08.json", "/api/weather/dd/blend", {"from": "2026-10-08"}),
          ("body_band.json", "/api/weather/dd/band", {}),
          ("body_desk_pnw_gfs.json", "/api/weather/dd/desk",
           {"region": "pnw", "source": "GFS", "from": "2026-10-08"})]


@pytest.mark.parametrize("name,path,params", BODIES)
def test_banked_body_is_what_the_route_serves(client, monkeypatch, name, path, params):
    want = json.loads((RECEIPTS / name).read_text())
    body = ok(get(client, monkeypatch, pool(), path, params))
    strip = lambda b: {k: v for k, v in b.items() if k not in ("cache", "inputs")}
    assert strip(body) == strip(want)
