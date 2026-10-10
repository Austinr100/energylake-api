"""d091679 — CPC outlooks API: curves, vintages, places, outlooks.

  T1  /curves for a drawable station equals the hand-read rows, both products
  T2  a place the bank does not draw is 200 with the verdict's reasons, no curve
  T3  strong odds carry the bank's band claim and sentence (ruling 172), verbatim
  T4  /curves/vintages is oldest first; n above the cap is 400, never trimmed
  T5  /places lists every place with its verdicts and matches a count query
  T6  /outlooks states an absence for an unbanked product
  T7  404, 400, 503
  T8  plan receipts: every read pinned, planned with timings; body bytes raw/gzip
  S   the rules: values only from the view, drawable only from the bank, one
      statement per read under the statement timeout, single-flight memo,
      cache block and headers, existing routes unchanged, banked bodies

Bank tests run the routes over production rows read on 2026-10-10 00:18Z
(tests/fixtures/cpc_outlooks_d091679, each file checked against the sha-256
Neon computed over the same text). Route tests use tests/test_solar_outlook.py's
fake pool. The rehearsal is docs/receipts/cpc-outlooks-d091679/rehearse.py.
"""

import asyncio
import gzip
import hashlib
import json
import pathlib
import re
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

import cpc_outlooks as co
import main
from test_solar_outlook import FakePool

ROOT = pathlib.Path(__file__).resolve().parents[1]
BANK = ROOT / "tests" / "fixtures" / "cpc_outlooks_d091679"
RECEIPTS = ROOT / "docs" / "receipts" / "cpc-outlooks-d091679"
# d091691 pinned the curve reads to one method version: their SQL pin and plans
# are re-taken there. The bodies here (the dashboard lane's vectors) are not.
PIN_RECEIPTS = ROOT / "docs" / "receipts" / "cpc-api-method-pin-d091691"

KSAN = ("station", "USW00023188", "")
KDEN = ("station", "USW00003017", "")
PNW_POP = ("region", "pnw", "population")
PNW_LOAD = ("region", "pnw", "load_share_365d")
FILES = {KSAN: "ksan", KDEN: "kden", PNW_POP: "pnw_population", PNW_LOAD: "pnw_load_share_365d"}

CACHES = ("_cpc_places_cache", "_cpc_curves_cache", "_cpc_vintages_cache",
          "_cpc_outlooks_cache")


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


# ── the bank ────────────────────────────────────────────────────────────────

DATE_KEYS = {"issuance", "w_valid_start", "w_valid_end", "target_date", "v_truth_frontier",
             "truth_frontier", "issued_date", "valid_start", "valid_end"}
TS_KEYS = {"w_written_at", "v_scored_at", "scored_at", "parsed_at"}


def _typed(r):
    """A banked row as psycopg hands it over: dates and timestamps typed."""
    out = {}
    for k, v in r.items():
        if v is not None and k in DATE_KEYS:
            v = date.fromisoformat(v)
        elif v is not None and k in TS_KEYS:
            v = datetime.fromisoformat(v)
        out[k] = v
    return out


_CACHE = {}


def banked(name, typed=True):
    """A banked file's rows, the text checked against the sha-256 Neon computed."""
    key = (name, typed)
    if key in _CACHE:
        return [dict(r) for r in _CACHE[key]]
    for line in (BANK / "manifest.psv").read_text().splitlines():
        f, n, sha, ln = line.split("|")
        if f == name:
            t = gzip.decompress((BANK / (f + ".gz")).read_bytes()).decode().rstrip("\n")
            assert hashlib.sha256(t.encode()).hexdigest() == sha and len(t) == int(ln), f
            rows = json.loads(t)
            assert len(rows) == int(n), f
            rows = [_typed(r) for r in rows] if typed else rows
            _CACHE[key] = rows
            return [dict(r) for r in rows]
    raise KeyError(name)


def curve_rows(p):
    """CURVES_SQL as the bank answers it: the place's banked 14, sliced to n."""
    key = (p["place_kind"], p["place"], p["weighting"])
    if key not in FILES:
        return [{"issuance": d, **{k: None for k in _NULL_KEYS}}
                for d in _issuances(p["product"])[-p["n"]:]]
    rows = banked(f"curves_{p['product']}_{FILES[key]}.json")
    keep = sorted({r["issuance"] for r in rows})[-p["n"]:]
    return [r for r in rows if r["issuance"] in keep]


def _issuances(product):
    return sorted({r["issuance"] for r in banked(f"curves_{product}_ksan.json")})


_NULL_KEYS = [k for k in banked("curves_610temp_kden.json", typed=False)[0] if k != "issuance"]


def pool(*, curves=curve_rows, verdicts=None, newest=None, features=None, vintage=None,
         geometry=None):
    def feats(p):
        rows = features if features is not None else banked("outlook_features.json")
        return [r for r in rows if r["product"] in p["products"]]

    def vint(p):
        rows = vintage if vintage is not None else banked("outlook_vintage.json")
        return [r for r in rows if r["product"] in p["products"]]

    def geo(p):
        rows = geometry if geometry is not None else banked("outlook_geometry_wk34.json")
        return [r for r in rows if r["product"] in p["products"]]

    return FakePool([
        ("SET LOCAL", []),
        ("FROM v_cpc_curves_drawable dv", curves),
        ("WITH hashes AS", verdicts if verdicts is not None else banked("places_verdicts.json")),
        ("AND c.day_index IS NULL", newest if newest is not None else banked("places_newest.json")),
        ("ST_AsGeoJSON", geo),
        ("FROM cpc_outlook_features f", feats),
        ("FROM cpc_outlook_vintage v", vint),
    ])


def get(client, monkeypatch, p, path, params=None):
    monkeypatch.setattr(main, "_pool", p)
    return client.get(path, params=params)


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def place_params(key):
    pk, pl, w = key
    return {"place_kind": pk, "place": pl, **({"weighting": w} if w else {})}


def product(body, prod):
    return next(p for p in body["products"] if p["product"] == prod)


# ═══════════════════════════════════════════════════════════════════════════
# T1 — /curves at a drawable station equals the hand-read rows
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("prod", co.CURVE_PRODUCTS)
def test_T1_curves_equal_the_hand_read_view_rows(client, monkeypatch, prod):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves", place_params(KSAN)))
    hand = banked(f"handread_view_{prod}_ksan.json", typed=False)
    win = banked(f"handread_window_{prod}_ksan.json", typed=False)[0]
    p = product(body, prod)
    v, c = p["vintage"], p["vintage"]["curve"]
    assert p["newest_written_issued_date"] == v["issued_date"] == hand[0]["issued_date"]
    assert v["issued_date"] == win["issued_date"]
    assert (v["valid_start"], v["valid_end"]) == (win["valid_start"], win["valid_end"])
    assert (v["reading"], v["history"], v["notes"]) == (win["reading"], win["history"],
                                                       win["notes"])
    assert (v["season"], v["strength"]) == (win["season"], win["strength"])
    # every row the view holds, and only those, value for value
    w_hand = [h for h in hand if h["day_index"] is None]
    d_hand = [h for h in hand if h["day_index"] is not None]
    assert len(w_hand) == 1 and len(c["days"]) == len(d_hand) == (5 if prod == "610temp" else 7)
    for k in co.VALUE_COLS + ("empty_class",):
        assert c["window"][k] == w_hand[0][k], k
        assert [d[k] for d in c["days"]] == [h[k] for h in d_hand], k
    assert [d["target_date"] for d in c["days"]] == [h["target_date"] for h in d_hand]
    assert [d["day_index"] for d in c["days"]] == [h["day_index"] for h in d_hand]
    for k in ("label", "band_claim", "band_share", "band_sentence", "band_basis",
              "backtest_version", "n_history_years", "history_first", "history_last",
              "member_odds", "method_version", "method_hash", "source_content_sha256",
              "season", "strength"):
        assert c[k] == hand[0][k], k
    assert v["drawable"] is True and v["absence"] is None
    assert v["verdict"]["band_claim"] == c["band_claim"]


def test_T1_both_products_in_one_body_each_its_own_newest(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves", place_params(KSAN)))
    assert [p["product"] for p in body["products"]] == ["610temp", "814temp"]
    assert body["products_drawn"] == ["610temp", "814temp"]
    # the bank as read: 610temp's newest written is 10-09, 814temp's 10-08
    assert product(body, "610temp")["newest_written_issued_date"] == "2026-10-09"
    assert product(body, "814temp")["newest_written_issued_date"] == "2026-10-08"
    assert body["absence"] is None


@pytest.mark.parametrize("key", [PNW_POP, PNW_LOAD])
def test_T1_the_pnw_region_draws_at_both_weightings(client, monkeypatch, key):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves", place_params(key)))
    assert (body["place_kind"], body["place"], body["weighting"]) == key
    for prod in co.CURVE_PRODUCTS:
        c = product(body, prod)["vintage"]["curve"]
        assert c["label"].startswith(f"History at pnw ({key[2]}), ")
        assert len(c["days"]) == (5 if prod == "610temp" else 7)


def test_T1_region_weighting_defaults_to_population(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves",
                  {"place_kind": "region", "place": "pnw"}))
    assert body["weighting"] == "population"


# ═══════════════════════════════════════════════════════════════════════════
# T2 — not drawable: 200, the verdict's reasons, no curve
# ═══════════════════════════════════════════════════════════════════════════

def test_T2_a_place_with_no_curve_states_why(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves", place_params(KDEN)))
    assert body["products_drawn"] == [] and body["absence"]["reason"] == "no_curve"
    for prod in co.CURVE_PRODUCTS:
        p = product(body, prod)
        assert p["vintage"]["curve"] is None and p["vintage"]["written"] is False
        assert p["absence"]["reason"] == "not_written"
        assert p["absence"]["place_reason_codes"] == ["under_30_base_years"]
        pv = p["place_verdict"]
        assert pv == {"drawable_cells": 0, "cell_count": 12,
                      "reason_codes": ["under_30_base_years"],
                      "history_years_in_base": [24], "verdicts": ["beats"]}


def _flip(rows, issuance, **cell):
    """The bank with one issuance's verdict cell restated (as a new version
    would): the view then holds no row for it, so d.* is null."""
    out = []
    seen = set()
    for r in rows:
        if r["issuance"] != issuance:
            out.append(r)
            continue
        if issuance in seen:
            continue
        seen.add(issuance)
        r = dict(r)
        for k in [k for k in r if not (k == "issuance" or k.startswith("w_") or k.startswith("v_"))]:
            r[k] = None
        r.update({"v_" + k: v for k, v in cell.items()})
        out.append(r)
    return out


def test_T2_a_written_curve_whose_cell_is_not_drawable(client, monkeypatch):
    """KLAX/KSFO in JJA are the bank's written-but-undrawable cells; the banked
    SON window holds none, so the same shape is made by restating one cell as
    a new verdict version would: does_not_beat, as JJA at KLAX states it."""
    def curves(p):
        rows = curve_rows(p)
        if p["product"] == "814temp" and p["place"] == "USW00023188":
            last = max(r["issuance"] for r in rows)
            rows = _flip(rows, last, drawable=False, verdict="does_not_beat",
                         season_t=1.12, season_skill=4.0)
        return rows
    body = ok(get(client, monkeypatch, pool(curves=curves), "/api/weather/cpc/curves",
                  place_params(KSAN)))
    p = product(body, "814temp")
    assert p["vintage"]["written"] is True and p["vintage"]["curve"] is None
    assert p["vintage"]["drawable"] is False
    assert p["absence"]["reason"] == "not_drawable"
    assert p["absence"]["reasons"] == [{"code": "season_does_not_beat_equal_odds",
                                        "clause": 3, "verdict": "does_not_beat",
                                        "season_t": 1.12, "season_skill": 4.0}]
    # reading, history and notes still ride (they are the window row's)
    assert p["vintage"]["notes"] and p["vintage"]["reading"] == "midpoint"
    assert body["products_drawn"] == ["610temp"]


def test_T2_the_places_reasons_are_the_banks_columns(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    cells = {(p["place"], p["weighting"], pr["product"], c["season"], c["strength"]): c
             for p in body["places"] for pr in p["products"]
             for s in pr["seasons"] for c in s["cells"]}
    klax = cells[("USW00023174", "", "610temp", "JJA", "moderate")]
    assert klax["drawable"] is False and klax["verdict"] == "does_not_beat"
    assert [r["code"] for r in klax["reasons"]] == ["season_does_not_beat_equal_odds"]
    kden = cells[("USW00003017", "", "814temp", "SON", "strong")]
    assert kden["drawable"] is False and kden["verdict"] == "beats"
    assert kden["reasons"] == [{"code": "under_30_base_years", "clause": 5,
                                "history_years_in_base": 24}]
    for c in cells.values():
        assert (c["reasons"] == []) == c["drawable"]


def test_T2_every_undrawable_cell_names_its_term(client, monkeypatch):
    """Measured 2026-10-09: of 324 undrawable cells, 312 are at the 13 places
    under 30 base years (22, 24 and 26 years) and 12 are KLAX/KSFO in JJA."""
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    codes = {}
    for p in body["places"]:
        for pr in p["products"]:
            for s in pr["seasons"]:
                for c in s["cells"]:
                    for r in c["reasons"]:
                        codes.setdefault(r["code"], []).append(c["history_years_in_base"])
    assert sorted(codes) == ["season_does_not_beat_equal_odds", "under_30_base_years"]
    assert len(codes["under_30_base_years"]) == 312
    assert sorted(set(codes["under_30_base_years"])) == [22, 24, 26]
    assert len(codes["season_does_not_beat_equal_odds"]) == 12


# ═══════════════════════════════════════════════════════════════════════════
# T3 — strong odds: the bank's band claim and sentence ride through
# ═══════════════════════════════════════════════════════════════════════════

def test_T3_strong_odds_carry_the_banks_label_verbatim(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves", place_params(KSAN)))
    hand = banked("handread_view_814temp_ksan.json", typed=False)[0]
    c = product(body, "814temp")["vintage"]["curve"]
    assert c["strength"] == "strong" == hand["strength"]
    assert c["band_claim"] == "measured" and c["band_share"] == hand["band_share"] < 0.85
    assert c["band_sentence"] == hand["band_sentence"]
    assert "about 7 in 10" in c["band_sentence"] and "9 in 10" not in c["band_sentence"]
    assert c["band_basis"] == "pooled_stations"


def test_T3_no_word_of_the_apis_own(client, monkeypatch):
    """The page prints the bank's words; nothing here calls a band overconfident."""
    for path, params in [("/api/weather/cpc/curves", place_params(KSAN)),
                         ("/api/weather/cpc/curves/vintages",
                          {"product": "814temp", **place_params(KSAN), "n": 14}),
                         ("/api/weather/cpc/places", {})]:
        main._cpc_places_cache.clear()
        text = get(client, monkeypatch, pool(), path, params).text.lower()
        assert "overconfiden" not in text and "underconfiden" not in text


def test_T3_every_strong_frame_carries_its_view_rows_claim(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves/vintages",
                  {"product": "814temp", **place_params(PNW_POP), "n": 14}))
    view = {r["issuance"]: r for r in banked("curves_814temp_pnw_population.json", typed=False)
            if r["day_index"] is None and r["label"] is not None}
    for v in body["vintages"]:
        c, r = v["curve"], view[v["issued_date"]]
        assert (c["band_claim"], c["band_share"], c["band_sentence"]) == (
            r["band_claim"], r["band_share"], r["band_sentence"])


# ═══════════════════════════════════════════════════════════════════════════
# T4 — vintages: oldest first; above the cap refused
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("n", [1, 7, 14])
def test_T4_vintages_are_the_n_newest_oldest_first(client, monkeypatch, n):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves/vintages",
                  {"product": "610temp", **place_params(KSAN), "n": n}))
    want = [d.isoformat() for d in _issuances("610temp")[-n:]]
    assert body["issued_dates"] == want == sorted(want)
    assert [v["issued_date"] for v in body["vintages"]] == want
    assert body["n"] == n and body["n_cap"] == 14 and body["order"] == "oldest first"
    assert body["drawn_count"] == n


def test_T4_default_n_is_7(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/curves/vintages",
                  {"product": "814temp", **place_params(PNW_LOAD)}))
    assert body["n"] == 7 and len(body["vintages"]) == 7


def test_T4_one_read_carries_every_frame(client, monkeypatch):
    p = pool()
    ok(get(client, monkeypatch, p, "/api/weather/cpc/curves/vintages",
           {"product": "814temp", **place_params(KSAN), "n": 14}))
    reads = [(q, pr) for q, pr in p.calls if "v_cpc_curves_drawable dv" in q]
    assert len(reads) == 1 and reads[0][1]["n"] == 14


@pytest.mark.parametrize("n", ["15", "0", "-1", "100", "seven", "7.5"])
def test_T4_n_outside_1_14_is_400_never_trimmed(client, monkeypatch, n):
    r = get(client, monkeypatch, pool(), "/api/weather/cpc/curves/vintages",
            {"product": "610temp", **place_params(KSAN), "n": n})
    assert r.status_code == 400 and r.json()["detail"].startswith("n:")


# ═══════════════════════════════════════════════════════════════════════════
# T5 — /places: every place, its verdicts, matching a count query
# ═══════════════════════════════════════════════════════════════════════════

def test_T5_places_match_the_count_query(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    counts = banked("count_verdicts.json", typed=False)
    got = {(p["place_kind"], p["place"], p["weighting"], pr["product"]):
           (pr["cell_count"], pr["drawable_cells"])
           for p in body["places"] for pr in p["products"]}
    want = {(c["place_kind"], c["place"], c["weighting"], c["product"]):
            (c["cells"], c["drawable"]) for c in counts}
    assert got == want
    assert body["place_count"] == 30 and body["cell_count"] == 720
    assert body["drawable_count"] == 396 == sum(c["drawable"] for c in counts)


def test_T5_every_cell_is_the_stored_row(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    stored = {(r["place_kind"], r["place"], r["weighting"], r["product"], r["season"],
               r["strength"]): r for r in banked("places_verdicts.json", typed=False)}
    n = 0
    for p in body["places"]:
        for pr in p["products"]:
            for s in pr["seasons"]:
                for c in s["cells"]:
                    r = stored[(p["place_kind"], p["place"], p["weighting"], pr["product"],
                                s["season"], c["strength"])]
                    for k in co.CELL_KEYS + ("drawable", "backtest_version"):
                        assert c[k] == r[k], k
                    n += 1
    assert n == 720
    assert body["verdict_versions"] == [{
        "backtest_version": "cpcv_2026-10-03_d0a93d5811",
        "scored_at": "2026-10-08T21:51:37.361650+00:00",
        "truth_frontier": "2026-10-03", "method_version": "cpc_curves_v1",
        "method_hash": "e99c71234013", "rules_version": "cpc_curve_verdicts_v1",
        "rules_hash": "5ffe9e61df72", "min_n_eff": 30}]


def test_T5_newest_curve_per_place(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    by = {(p["place"], p["weighting"]): p for p in body["places"]}
    assert body["newest_written_issued_date"] == {"610temp": "2026-10-09",
                                                  "814temp": "2026-10-08"}
    ksan = product(by[("USW00023188", "")], "814temp")["newest_curve"]
    assert (ksan["issued_date"], ksan["season"], ksan["strength"], ksan["drawable"]) == (
        "2026-10-08", "SON", "strong", True)
    kden = product(by[("USW00003017", "")], "610temp")
    assert kden["newest_curve"] is None
    assert kden["newest_curve_absence"]["reason"] == "not_written"
    written = sum(1 for p in body["places"] for pr in p["products"] if pr["newest_curve"])
    assert written == 34 == len(banked("places_newest.json"))


# ═══════════════════════════════════════════════════════════════════════════
# T6 — /outlooks: banked features, and absence for what is not banked
# ═══════════════════════════════════════════════════════════════════════════

def fam(body, f):
    return next(x for x in body["families"] if x["family"] == f)


def test_T6_unbanked_families_are_a_stated_absence(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/outlooks"))
    assert [f["family"] for f in body["families"]] == ["610", "814", "wk34", "monthly",
                                                       "seasonal"]
    for f in ("monthly", "seasonal"):
        x = fam(body, f)
        assert x["absence"]["reason"] == "nothing_banked"
        for p in x["products"]:
            assert p["features"] == [] and p["absence"]["reason"] == "not_banked"
            assert p["bytes_newest_issued_date"] is None


def test_T6_banked_families_serve_the_stored_features(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/outlooks"))
    stored = banked("outlook_features.json", typed=False)
    for f, prods in (("610", ("610temp", "610prcp")), ("814", ("814temp", "814prcp")),
                     ("wk34", ("wk34temp", "wk34prcp"))):
        x = fam(body, f)
        assert x["absence"] is None
        for p in x["products"]:
            rows = [r for r in stored if r["product"] == p["product"]]
            assert p["product"] in prods and p["feature_count"] == len(rows) > 0
            assert p["issued_date"] == rows[0]["issued_date"]
            for got, r in zip(p["features"], rows):
                for k in ("layer", "feature_index", "category", "category_source", "prob",
                          "valid_start", "valid_end", "intersects_west",
                          "west_area_fraction", "regions"):
                    assert got[k] == r[k], k
                assert got["centroid"] == (None if r["centroid_lon"] is None
                                           else [r["centroid_lon"], r["centroid_lat"]])
    wk = fam(body, "wk34")["products"][0]
    assert {f["category"] for f in wk["features"]} >= {"EC"}       # EC as EC
    assert all(f["prob"] == 33 for f in wk["features"] if f["category"] == "EC")


def test_T6_bytes_banked_but_not_parsed_say_so(client, monkeypatch):
    feats = [r for r in banked("outlook_features.json") if r["product"] != "610prcp"]
    body = ok(get(client, monkeypatch, pool(features=feats), "/api/weather/cpc/outlooks",
                  {"product": "610"}))
    p = next(x for x in fam(body, "610")["products"] if x["product"] == "610prcp")
    assert p["absence"]["reason"] == "not_parsed"
    assert p["bytes_newest_issued_date"] == "2026-10-09"


def test_T6_parsed_trails_banked_by_a_day_and_says_both(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/outlooks", {"product": "814"}))
    for p in fam(body, "814")["products"]:
        assert (p["issued_date"], p["bytes_newest_issued_date"]) == ("2026-10-08", "2026-10-09")


def test_T6_geometry_on_request_one_family(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/outlooks",
                  {"product": "wk34", "geometry": "true"}))
    assert body["geometry"] is True
    geo = {(g["product"], g["feature_index"]): g["west_geojson"]
           for g in banked("outlook_geometry_wk34.json", typed=False)}
    for p in fam(body, "wk34")["products"]:
        for f in p["features"]:
            assert f["west_geojson"] == geo[(p["product"], f["feature_index"])]
            assert (f["west_geojson"] is None) == (f["centroid"] is None)
    plain = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/outlooks", {"product": "wk34"}))
    assert "west_geojson" not in json.dumps(plain["families"])


# ═══════════════════════════════════════════════════════════════════════════
# T7 — 404, 400, 503
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("params", [
    {"place_kind": "station", "place": "USW00099999"},
    {"place_kind": "region", "place": "atlantis"},
    {"place_kind": "region", "place": "socalgas_territory", "weighting": "load_share_365d"},
])
@pytest.mark.parametrize("path", ["/api/weather/cpc/curves", "/api/weather/cpc/curves/vintages"])
def test_T7_unknown_place_is_404(client, monkeypatch, params, path):
    if path.endswith("vintages"):
        params = {**params, "product": "610temp"}
    p = pool()
    r = get(client, monkeypatch, p, path, params)
    assert r.status_code == 404 and "unknown place" in r.json()["detail"]
    assert not [q for q, _ in p.calls if "v_cpc_curves_drawable dv" in q]


@pytest.mark.parametrize("path,params,field", [
    ("/api/weather/cpc/curves", {"place": "USW00023188"}, "place_kind"),
    ("/api/weather/cpc/curves", {"place_kind": "county", "place": "x"}, "place_kind"),
    ("/api/weather/cpc/curves", {"place_kind": "station"}, "place"),
    ("/api/weather/cpc/curves", {"place_kind": "station", "place": "KSAN"}, "place"),
    ("/api/weather/cpc/curves", {"place_kind": "region", "place": "PNW!"}, "place"),
    ("/api/weather/cpc/curves", {"place_kind": "station", "place": "USW00023188",
                                 "weighting": "population"}, "weighting"),
    ("/api/weather/cpc/curves", {"place_kind": "region", "place": "pnw",
                                 "weighting": "acreage"}, "weighting"),
    ("/api/weather/cpc/curves/vintages", {"place_kind": "station", "place": "USW00023188"},
     "product"),
    ("/api/weather/cpc/curves/vintages", {"product": "610prcp", "place_kind": "station",
                                          "place": "USW00023188"}, "product"),
    ("/api/weather/cpc/outlooks", {"product": "610temp"}, "product"),
    ("/api/weather/cpc/outlooks", {"product": "wk34", "geometry": "maybe"}, "geometry"),
    ("/api/weather/cpc/outlooks", {"geometry": "true"}, "geometry"),
])
def test_T7_bad_parameters_are_400_naming_the_field(client, monkeypatch, path, params, field):
    p = pool()
    r = get(client, monkeypatch, p, path, params)
    assert r.status_code == 400 and r.json()["detail"].startswith(field + ":"), r.text
    assert p.calls == []                                 # refused before any read


class _DownPool:
    def connection(self):
        raise OSError("connection refused")


class _CanceledPool(FakePool):
    """The statement timeout firing: the read raises as psycopg raises it."""
    def connection(self):
        ctx = super().connection()
        cur_factory = ctx.cursor

        def cursor():
            cur = cur_factory()
            run = cur.execute

            async def execute(q, params=None):
                await run(q, params)
                if "SET LOCAL" not in q:
                    raise main.psycopg.errors.QueryCanceled(
                        "canceling statement due to statement timeout")
            cur.execute = execute
            return cur
        ctx.cursor = cursor
        return ctx


@pytest.mark.parametrize("path,params", [
    ("/api/weather/cpc/places", {}),
    ("/api/weather/cpc/curves", place_params(KSAN)),
    ("/api/weather/cpc/curves/vintages", {"product": "610temp", **place_params(KSAN)}),
    ("/api/weather/cpc/outlooks", {}),
])
@pytest.mark.parametrize("bad", ["down", "timeout"])
def test_T7_database_down_or_timed_out_is_503(client, monkeypatch, path, params, bad):
    p = _DownPool() if bad == "down" else _CanceledPool(pool().routes)
    r = get(client, monkeypatch, p, path, params)
    assert r.status_code == 503 and "db unavailable" in r.json()["detail"]


def test_T7_a_build_past_its_ceiling_is_503(client, monkeypatch):
    class _Slow(FakePool):
        def connection(self):
            ctx = super().connection()
            cur_factory = ctx.cursor

            def cursor():
                cur = cur_factory()
                run = cur.execute

                async def execute(q, params=None):
                    await asyncio.sleep(0.2)
                    await run(q, params)
                cur.execute = execute
                return cur
            ctx.cursor = cursor
            return ctx
    monkeypatch.setattr(main._cpc_outlooks_cache, "build_timeout", 0.05)
    r = get(client, monkeypatch, _Slow(pool().routes), "/api/weather/cpc/outlooks")
    assert r.status_code == 503 and "did not build within" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# S — the rules
# ═══════════════════════════════════════════════════════════════════════════

def test_S_no_value_is_read_from_a_base_table():
    """Every percentile, equal-odds value and member_odds comes from the view
    (`d.`), never from cpc_outlook_curves (`w.`) or anything else."""
    sql = co.CURVES_SQL
    for k in co.VALUE_COLS + ("member_odds", "empty_class", "label", "band_claim",
                              "band_share", "band_sentence"):
        refs = set(re.findall(r"(\w+)\." + k + r"\b", sql))
        assert refs <= {"d", "vv"}, (k, refs)
        assert not ({"w", "c"} & refs)
    for name in ("PLACES_SQL", "PLACES_NEWEST_SQL"):
        s = getattr(co, name)
        assert not re.search(r"\b(eq_)?(tavg|hdd|cdd)_p\d\d\b|member_odds", s), name


def test_S_a_curve_is_the_views_rows_or_nothing(client, monkeypatch):
    """Drop the view's rows but keep the verdict saying drawable: no curve is
    made from anything else, and the absence names the verdict's state."""
    def curves(p):
        rows = curve_rows(p)
        if p["product"] == "610temp":
            last = max(r["issuance"] for r in rows)
            rows = _flip(rows, last, drawable=True)
        return rows
    body = ok(get(client, monkeypatch, pool(curves=curves), "/api/weather/cpc/curves",
                  place_params(KSAN)))
    p = product(body, "610temp")
    assert p["vintage"]["curve"] is None and p["vintage"]["drawable"] is True
    assert p["absence"]["reason"] == "not_drawable" and p["absence"]["reasons"] == []


def test_S_drawable_is_the_banks_column_not_a_derivation(client, monkeypatch):
    """A cell whose fields would pass ccv_drawable_ck's terms but whose column
    says false is not drawable here, and the reverse."""
    rows = banked("places_verdicts.json")
    a = next(r for r in rows if r["drawable"])
    b = next(r for r in rows if not r["drawable"])
    a["drawable"], b["drawable"] = False, True
    body = ok(get(client, monkeypatch, pool(verdicts=rows), "/api/weather/cpc/places"))
    cells = {(p["place"], p["weighting"], pr["product"], c["season"], c["strength"]): c
             for p in body["places"] for pr in p["products"]
             for s in pr["seasons"] for c in s["cells"]}
    ca = cells[(a["place"], a["weighting"], a["product"], a["season"], a["strength"])]
    cb = cells[(b["place"], b["weighting"], b["product"], b["season"], b["strength"])]
    assert ca["drawable"] is False and [r["code"] for r in ca["reasons"]] == ["unstated"]
    assert cb["drawable"] is True and cb["reasons"] == []
    assert body["drawable_count"] == 396


@pytest.mark.parametrize("path,params,n_reads", [
    ("/api/weather/cpc/places", {}, 2),
    ("/api/weather/cpc/curves", place_params(KSAN), 4),       # places memo + one per product
    ("/api/weather/cpc/curves/vintages", {"product": "814temp", **place_params(KSAN)}, 3),
    ("/api/weather/cpc/outlooks", {}, 2),
    ("/api/weather/cpc/outlooks", {"product": "wk34", "geometry": "1"}, 3),
])
def test_S_every_read_is_one_statement_under_the_statement_timeout(client, monkeypatch, path,
                                                                    params, n_reads):
    p = pool()
    ok(get(client, monkeypatch, p, path, params))
    sets = [q for q, _ in p.calls if q.startswith("SET LOCAL")]
    reads = [q for q, _ in p.calls if not q.startswith("SET LOCAL")]
    assert len(reads) == n_reads and len(sets) == n_reads
    assert set(sets) == {f"SET LOCAL statement_timeout = "
                         f"'{main._DD_REGION_FC_STATEMENT_TIMEOUT}'"}
    pinned = {flat(getattr(co, n)) for n in PINNED}
    assert all(flat(q) in pinned for q in reads)


def test_S_memo_ttl_cache_block_and_headers(client, monkeypatch):
    p = pool()
    r1 = get(client, monkeypatch, p, "/api/weather/cpc/curves", place_params(KSAN))
    r2 = get(client, monkeypatch, p, "/api/weather/cpc/curves", place_params(KSAN))
    b1, b2 = r1.json(), r2.json()
    assert b1["cache"]["state"] == "miss" and b2["cache"]["state"] == "fresh"
    assert b1["cache"]["ttl_seconds"] == 900.0
    assert set(b1["cache"]) == {"state", "built_at", "age_seconds", "ttl_seconds",
                                "build_seconds", "refreshing"}
    assert r1.headers["X-Cache"] == "miss" and r2.headers["X-Cache"] == "hit"
    assert r1.headers["Cache-Control"] == "max-age=900" and "Age" in r2.headers
    assert len([q for q, _ in p.calls if "v_cpc_curves_drawable dv" in q]) == 2
    for c in CACHES:
        assert getattr(main, c).ttl == 900.0


def test_S_single_flight(monkeypatch):
    """Ten callers at once on a cold key: one build."""
    p = pool()
    monkeypatch.setattr(main, "_pool", p)

    async def go():
        return await asyncio.gather(*(main._cpc_places() for _ in range(10)))
    out = asyncio.run(go())
    assert {s for _p, s, _e in out} == {"miss"}
    assert len([q for q, _ in p.calls if "WITH hashes AS" in q]) == 1


def test_S_places_never_serves_its_vocabulary_key(client, monkeypatch):
    body = ok(get(client, monkeypatch, pool(), "/api/weather/cpc/places"))
    assert not [k for k in body if k.startswith("_")]


def test_S_existing_routes_unchanged():
    paths = {r.path for r in main.app.routes}
    for path in ("/api/weather/outlooks", "/api/weather/regime", "/api/weather/dd/forecast",
                 "/api/weather/dd/forecast/regions", "/api/weather/dd/scores",
                 "/api/weather/dd/blend", "/api/weather/dd/band", "/api/weather/dd/desk"):
        assert path in paths
    assert {p for p in paths if p.startswith("/api/weather/cpc")} == {
        "/api/weather/cpc/curves", "/api/weather/cpc/curves/vintages",
        "/api/weather/cpc/places", "/api/weather/cpc/outlooks"}


# ═══════════════════════════════════════════════════════════════════════════
# T8 — plan receipts, pinned SQL, body bytes
# ═══════════════════════════════════════════════════════════════════════════

PINNED = {
    "CURVES_SQL": None, "PLACES_SQL": None, "PLACES_NEWEST_SQL": None,
    "OUTLOOK_FEATURES_SQL": None, "OUTLOOK_GEOMETRY_SQL": None, "OUTLOOK_VINTAGE_SQL": None,
}


def flat(s):
    return " ".join(s.split())


def test_T8_every_read_is_pinned():
    want = json.loads((PIN_RECEIPTS / "pinned_sql.json").read_text())
    got = {n: flat(getattr(co, n)) for n in dir(co) if n.endswith("_SQL")}
    assert set(got) == set(PINNED) and got == want


def test_T8_the_plan_receipts_ran_the_pinned_sql():
    """explains.sql: each statement follows `-- <tag> <NAME> <json params>`.
    The pinned SQL with those literals substituted must be the statement that
    EXPLAIN (ANALYZE, BUFFERS) ran, and every read must have a plan with its
    execution time."""
    def lit(v):
        if isinstance(v, list):
            return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
        return str(v) if isinstance(v, int) else f"'{v}'"
    covered = set()
    # d091691: the reads it pinned are planned there (tags Axx); the rest here
    for where, keep in ((RECEIPTS, lambda t: True), (PIN_RECEIPTS, lambda t: t[0] == "A")):
        text = (where / "explains.sql").read_text()
        blocks = re.findall(r"^-- (\w+) (\w+_SQL) (\{.*?\})\n(.*?);\s*$", text, re.M | re.S)
        plans = (where / "plans_raw.txt").read_text()
        for tag, name, params, stmt in blocks:
            p = json.loads(params)
            want = re.sub(r"%\((\w+)\)s", lambda m: lit(p[m.group(1)]), flat(getattr(co, name)))
            if not keep(tag) or flat(stmt) != "EXPLAIN (ANALYZE, BUFFERS) " + want:
                continue
            seg = plans.split(f"== {tag} ")[1].split("\n== ")[0]
            assert "Execution Time" in seg, tag
            covered.add(name)
    assert covered == set(PINNED)


def test_T8_body_bytes_are_the_banked_bodies():
    rows = [l.split("|") for l in (RECEIPTS / "bytes.psv").read_text().splitlines()]
    assert rows
    for name, _path, raw, gz in rows:
        b = (RECEIPTS / name).read_bytes()
        assert len(b) == int(raw) and len(gzip.compress(b, 6, mtime=0)) == int(gz), name


# ═══════════════════════════════════════════════════════════════════════════
# The banked bodies (the dashboard lane's vectors)
# ═══════════════════════════════════════════════════════════════════════════

BODIES = [
    ("body_curves_ksan.json", "/api/weather/cpc/curves", place_params(KSAN)),
    ("body_curves_pnw_population.json", "/api/weather/cpc/curves", place_params(PNW_POP)),
    ("body_curves_kden.json", "/api/weather/cpc/curves", place_params(KDEN)),
    ("body_vintages_814temp_ksan_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **place_params(KSAN), "n": 14}),
    ("body_vintages_610temp_pnw_population.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **place_params(PNW_POP)}),
    ("body_places.json", "/api/weather/cpc/places", {}),
    ("body_outlooks.json", "/api/weather/cpc/outlooks", {}),
]


@pytest.mark.parametrize("name,path,params", BODIES)
def test_banked_body_is_what_the_route_serves(client, monkeypatch, name, path, params):
    want = json.loads((RECEIPTS / name).read_text())
    body = ok(get(client, monkeypatch, pool(), path, params))
    strip = lambda b: {k: v for k, v in b.items() if k != "cache"}
    assert strip(body) == strip(want)
