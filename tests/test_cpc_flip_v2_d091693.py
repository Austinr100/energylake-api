"""d091693 — the CPC API serves cpc_curves_v2.

  V0  STOP-0, as read on Neon: every v1 issuance has v2 rows, and one v2
      backtest_version of 720 cells; the counts printed
  V1  real v2 rows beside real v1 rows (tests/load_bank_d091693.py, Neon's
      rows read 2026-10-11 ~13:47Z): every route serves v2 only, and the v1
      rows change no byte (d091691's P0-P3, restated for v2); the local bank
      answers the routes' reads as Neon does; the vectors are what the routes
      serve
  V2  the 13 places on 22 to 29 base years carry their count and the
      writer's label, Denver is a curve, the pooled bands under 30 are none;
      every count is read from the rows
  V3  reasons() clause 5 reads the cell's min_base_years, base_reason rides
      verbatim, DRAWABLE_RULE quotes 298
  V4  d091691's P4 source rule restated for v2: one constant, every reference
      to the three relations pinned to it
  V5  the Neon plans of every pinned read, beside the v1-pinned SQL; STOP-P

The bank tests run the routes' own statements on a throwaway local Postgres
(pantry 286 + 298's DDL verbatim) through load_bank_d091673.PgPool; they skip
where no initdb is installed. The rehearsal is
docs/receipts/cpc-api-flip-v2-d091693/rehearse.py.
"""

import inspect
import json
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

import cpc_outlooks as co
import load_bank_d091673 as lb
import load_bank_d091693 as bank
import main

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECEIPTS = ROOT / "docs" / "receipts" / "cpc-api-flip-v2-d091693"
PIN = ROOT / "docs" / "receipts" / "cpc-api-method-pin-d091691"
sys.path.insert(0, str(RECEIPTS))
import neon_reads  # noqa: E402

# main a10b6f2's statements: d091691's v1 pin (its pinned_sql.json)
V1_SQL = json.loads((PIN / "pinned_sql.json").read_text())
PINNED_READS = ("CURVES_SQL", "PLACES_SQL", "PLACES_NEWEST_SQL")
RELATIONS = ("cpc_outlook_curves", "cpc_curve_verdicts", "v_cpc_curves_drawable")

CACHES = ("_cpc_places_cache", "_cpc_curves_cache", "_cpc_vintages_cache",
          "_cpc_outlooks_cache")


def P(key):
    pk, pl, w = key
    return {"place_kind": pk, "place": pl, **({"weighting": w} if w else {})}


# the vectors (docs/receipts/cpc-api-flip-v2-d091693/body_*.json, sample.py)
BODIES = [
    ("body_curves_ksan.json", "/api/weather/cpc/curves", P(bank.KSAN)),
    ("body_curves_kden.json", "/api/weather/cpc/curves", P(bank.KDEN)),
    ("body_curves_san_jose.json", "/api/weather/cpc/curves", P(bank.SJC)),
    ("body_curves_pnw_population.json", "/api/weather/cpc/curves", P(bank.PNW_POP)),
    ("body_curves_sp15_load_share_365d.json", "/api/weather/cpc/curves", P(bank.SP15_LOAD)),
    ("body_vintages_814temp_ksan_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **P(bank.KSAN), "n": 14}),
    ("body_vintages_610temp_pnw_population.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **P(bank.PNW_POP)}),
    ("body_vintages_610temp_kden_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **P(bank.KDEN), "n": 14}),
    ("body_places.json", "/api/weather/cpc/places", {}),
]

# words only a v1 row, cell or version carries
V1_WORDS = (bank.V1, bank.V1_HASH, bank.V1_BACKTEST, "cpc_curves_writer_v1",
            "cpc_curve_verdicts_v1", "under_30_base_years")


def flat(s):
    return " ".join(s.split())


# ── the bank ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def banks():
    if lb.initdb_path() is None:
        pytest.skip("no initdb: the bank tests need a local Postgres")
    with bank.banks() as conns:
        yield conns


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _cold_memos():
    for c in CACHES:
        getattr(main, c).clear()
    yield
    for c in CACHES:
        getattr(main, c).clear()


def serve(client, monkeypatch, conn, path, params):
    for c in CACHES:
        getattr(main, c).clear()
    monkeypatch.setattr(main, "_pool", lb.PgPool(conn))
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r


_CLOCK = re.compile(rb'"built_at":"[^"]*"|"build_seconds":[0-9.e-]+')


def masked(b: bytes) -> bytes:
    """The body with the memo's clock (built_at, build_seconds) masked; the
    rest of the cache block is fixed on a cold memo (miss, 0.0, 900.0, false)."""
    return _CLOCK.sub(b"<clock>", b)


def vector(name):
    return (RECEIPTS / name).read_bytes()


def product(body, prod):
    return next(p for p in body["products"] if p["product"] == prod)


def cells(body):
    return [c for p in body["places"] for pr in p["products"]
            for s in pr["seasons"] for c in s["cells"]]


def values_of(obj, key):
    """Every value of `key` anywhere in a body."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from values_of(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from values_of(v, key)


def places_of(conn):
    return [r["k"] for r in conn.execute(
        "SELECT DISTINCT ARRAY[place_kind, place, weighting] AS k FROM cpc_curve_verdicts "
        "WHERE method_version = %s ORDER BY 1", (bank.V2,))]


# ═══════════════════════════════════════════════════════════════════════════
# V0 — STOP-0's counts, as read on Neon
# ═══════════════════════════════════════════════════════════════════════════

def test_V0_stop0_counts_both_conditions():
    s = json.loads((RECEIPTS / "stop0.json").read_text())
    a, b = s["a"], s["b"]
    print(f"\nSTOP-0 (a) at {a['read_at']}: v1 issuances {a['v1_issuances']}, "
          f"v2 issuances {a['v2_issuances']}, v1 issuances lacking v2 {a['v1_lacking_v2']}; "
          f"v2 only: {[(r['product'], r['issued_date']) for r in s['a_v2_only']['rows']]}")
    print(f"STOP-0 (b) at {b['read_at']}: " + "; ".join(
        f"{r['method_version']} {r['backtest_version']} {r['cells']} cells" for r in b["rows"]))
    # (a) every v1 issuance has v2 rows; v2 may hold more
    assert a["v1_lacking_v2"] == 0
    assert a["v2_issuances"] - a["v1_issuances"] == len(s["a_v2_only"]["rows"]) == 2
    # (b) one cpc_curves_v2 backtest_version, of 720 cells
    v2 = [r for r in b["rows"] if r["method_version"] == bank.V2]
    assert len(v2) == 1 and v2[0]["cells"] == 720 and v2[0]["backtest_version"] == bank.V2_BACKTEST


def test_V0_the_banked_rows_hold_the_same_rule():
    """The rows read from Neon obey both conditions too: at the six places
    banked on every issuance, every v1 window row has its v2 row, and the
    verdicts are one v2 version of 720 cells."""
    for p in bank.PRODUCTS:
        win = {v: {(r["issued_date"], r["place_kind"], r["place"], r["weighting"])
                   for r in bank.curves(v, p) if r["day_index"] is None
                   and (r["place_kind"], r["place"], r["weighting"]) in bank.SIX}
               for v in (bank.V1, bank.V2)}
        assert win[bank.V1] and win[bank.V1] <= win[bank.V2], p
    vv = bank.verdicts(bank.V2)
    assert len(vv) == 720 and {r["backtest_version"] for r in vv} == {bank.V2_BACKTEST}


# ═══════════════════════════════════════════════════════════════════════════
# V1 — v2 only, and the v1 rows beside change nothing (d091691 P0-P3, for v2)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("tag,name,side,params", neon_reads.READS)
def test_V1_the_local_bank_answers_as_neon(banks, tag, name, side, params):
    """Each read, run on Neon and here over `both`, hashes the same: the
    rows the bodies are served from are Neon's, read for read."""
    want = json.loads((RECEIPTS / "neon_reads.json").read_text())[tag]
    got = banks["both"].execute(neon_reads.wrapped(name, side, params)).fetchone()
    assert (got["len"], got["sha"]) == (want["len"], want["sha"])


def test_V1_the_view_is_neons(banks):
    """pantry 298's view, as created here, deparses to Neon's: the md5 d091691
    read on 2026-10-10 and this lane read again on 2026-10-11."""
    m = banks["both"].execute("SELECT md5(pg_get_viewdef('v_cpc_curves_drawable'::regclass)) "
                              "AS m").fetchone()["m"]
    s = json.loads((RECEIPTS / "stop0.json").read_text())
    assert m == s["b_detail"]["viewmd5"] == "66966f907581836370275d209d564a67"


@pytest.mark.parametrize("name,path,params", BODIES)
def test_V1_v1_rows_beside_change_no_byte(banks, client, monkeypatch, name, path, params):
    """Over v2 alone and over v2 with every v1 row beside it: the same bytes."""
    alone = masked(serve(client, monkeypatch, banks["v2"], path, params).content)
    beside = masked(serve(client, monkeypatch, banks["both"], path, params).content)
    assert beside == alone


@pytest.mark.parametrize("name,path,params", BODIES)
def test_V1_every_route_serves_v2_only(banks, client, monkeypatch, name, path, params):
    r = serve(client, monkeypatch, banks["both"], path, params)
    body = r.json()
    assert set(values_of(body, "method_version")) == {bank.V2}, name
    assert set(values_of(body, "method_hash")) == {bank.V2_HASH}, name
    assert set(values_of(body, "backtest_version")) == {bank.V2_BACKTEST}, name
    assert set(values_of(body, "writer_version")) <= {"cpc_curves_writer_v2"}, name
    assert [w for w in V1_WORDS if w in r.text] == [], name   # a short diff on a 770 KB body


@pytest.mark.parametrize("name,path,params", BODIES)
def test_V1_the_vectors_are_what_the_routes_serve(banks, client, monkeypatch, name, path,
                                                   params):
    """Byte for byte, the memo's clock masked: the dashboard lane's vectors."""
    r = serve(client, monkeypatch, banks["both"], path, params)
    assert masked(r.content) == masked(vector(name))


def test_V1_the_v2_only_issuance_is_the_newest(banks, client, monkeypatch):
    """2026-10-10 exists under v2 alone (the writer stamps v2 only since): it is
    every route's newest, at every place."""
    body = serve(client, monkeypatch, banks["both"], "/api/weather/cpc/places", {}).json()
    assert body["newest_written_issued_date"] == {"610temp": "2026-10-10",
                                                  "814temp": "2026-10-10"}
    newest = [pr["newest_curve"] for p in body["places"] for pr in p["products"]]
    assert len(newest) == 60 and {n["issued_date"] for n in newest} == {"2026-10-10"}
    for key in bank.SIX:
        cur = serve(client, monkeypatch, banks["both"], "/api/weather/cpc/curves", P(key)).json()
        assert [p["newest_written_issued_date"] for p in cur["products"]] == [
            "2026-10-10", "2026-10-10"], key
    v = serve(client, monkeypatch, banks["both"], "/api/weather/cpc/curves/vintages",
              {"product": "814temp", **P(bank.KSAN), "n": 14}).json()
    assert v["issued_dates"][-1] == "2026-10-10" and v["issued_dates"][0] == "2026-09-27"


@pytest.mark.parametrize("name,path,params", BODIES)
def test_V1_the_pin_decides_not_the_bank(banks, client, monkeypatch, name, path, params):
    """The same statements with the pin's literal set back to v1: over the
    same bank they serve v1's rows alone, exactly as over the v1 rows by
    themselves, and not the v2 body. What reaches a body is the constant's
    version, whatever else stands in the tables."""
    for n in PINNED_READS:
        monkeypatch.setattr(co, n, getattr(co, n).replace(f"'{bank.V2}'", f"'{bank.V1}'"))
    r = serve(client, monkeypatch, banks["both"], path, params)
    both = masked(r.content)
    v1 = masked(serve(client, monkeypatch, banks["v1"], path, params).content)
    assert both == v1 and both != masked(vector(name))
    assert set(values_of(r.json(), "method_version")) <= {bank.V1}


# ═══════════════════════════════════════════════════════════════════════════
# V2 — the 13 short places carry their count; Denver is a curve
# ═══════════════════════════════════════════════════════════════════════════

def short_places(conn):
    """(place_kind, place, weighting) -> base years, under 30, from the rows."""
    return {(r["place_kind"], r["place"], r["weighting"]): r["h"] for r in conn.execute(
        "SELECT place_kind, place, weighting, min(history_years_in_base) AS h, "
        "max(history_years_in_base) AS hx FROM cpc_curve_verdicts WHERE method_version = %s "
        "GROUP BY 1, 2, 3 HAVING min(history_years_in_base) < 30", (bank.V2,))}


def test_V2_the_13_short_places_are_the_rows(banks):
    s = short_places(banks["both"])
    assert len(s) == 13
    assert sorted(s.values()) == [22] * 6 + [26] * 3 + [28] * 3 + [29]
    assert s[bank.KDEN] == 29 and s[bank.SJC] == 28 and s[bank.SP15_LOAD] == 22


def test_V2_each_short_place_states_its_count_with_the_writers_label(banks, client,
                                                                     monkeypatch):
    """At every short place and product, the newest frame either draws a
    curve whose count, members, fewest and threads are the window row's and
    whose label is the view's own text ("..., on N base years, not CPC's 30"),
    or is not drawable with the cell's count beside it. 17 of the 26 draw."""
    c = banks["both"]
    drawn = 0
    for key, h in short_places(c).items():
        body = serve(client, monkeypatch, c, "/api/weather/cpc/curves", P(key)).json()
        for pr in body["products"]:
            v = pr["vintage"]
            row = c.execute(
                "SELECT base_years, base_fewest_stations, base_by_station, threaded_base "
                "FROM cpc_outlook_curves WHERE method_version = %s AND product = %s "
                "AND issued_date = %s AND place_kind = %s AND place = %s AND weighting = %s "
                "AND day_index IS NULL", (bank.V2, pr["product"], v["issued_date"], *key)
            ).fetchone()
            assert row["base_years"] == h, (key, pr["product"])
            assert v["verdict"]["history_years_in_base"] == h
            assert v["verdict"]["min_base_years"] == 22
            assert v["verdict"]["base_by_station"] == row["base_by_station"]
            assert v["verdict"]["base_fewest_stations"] == row["base_fewest_stations"]
            assert v["verdict"]["threaded_base"] == row["threaded_base"]
            cv = v["curve"]
            if cv is None:
                assert pr["absence"]["reason"] == "not_drawable"
                assert [r["code"] for r in pr["absence"]["reasons"]] == ["no_band_claim"]
                assert v["verdict"]["band_basis"] == "none"
                continue
            drawn += 1
            labels = {r["label"] for r in c.execute(
                "SELECT label FROM v_cpc_curves_drawable WHERE method_version = %s "
                "AND product = %s AND issued_date = %s AND place_kind = %s AND place = %s "
                "AND weighting = %s", (bank.V2, pr["product"], v["issued_date"], *key))}
            assert labels == {cv["label"]}
            assert cv["label"].endswith(f", on {h} base years, not CPC's 30")
            assert (cv["base_years"], cv["base_fewest_stations"], cv["base_by_station"],
                    cv["threaded_base"], cv["min_base_years"]) == (
                h, row["base_fewest_stations"], row["base_by_station"], row["threaded_base"], 22)
            if cv["band_sentence"] is not None:
                assert f"on {h} base years, not CPC's 30" in cv["band_sentence"]
    assert drawn == 17


def test_V2_denver_is_a_curve(banks, client, monkeypatch):
    """KDEN: not_written under v1 (production today); under v2 both products
    draw, on 29 base years, five of them threaded from USW00023062."""
    c = banks["both"]
    prod = json.loads((RECEIPTS / "prod" / "body_curves_kden.json").read_text())
    assert [p["absence"]["reason"] for p in prod["products"]] == ["not_written", "not_written"]
    body = serve(client, monkeypatch, c, "/api/weather/cpc/curves", P(bank.KDEN)).json()
    assert body["products_drawn"] == ["610temp", "814temp"] and body["absence"] is None
    thread = c.execute("SELECT DISTINCT threaded_base FROM cpc_outlook_curves "
                       "WHERE method_version = %s AND place = %s", (bank.V2, bank.KDEN[1])
                       ).fetchall()
    assert len(thread) == 1
    for pr in body["products"]:
        cv = pr["vintage"]["curve"]
        assert cv["base_years"] == 29 and cv["base_by_station"] == [
            {"station": "USW00003017", "base_years": 29}]
        assert cv["threaded_base"] == thread[0]["threaded_base"]
        assert [t["predecessor"] for t in cv["threaded_base"]] == ["USW00023062"]
        assert cv["label"] == ("History at USW00003017, 1991-2025, reweighted by NOAA CPC's "
                               f"published {'6-10' if pr['product'] == '610temp' else '8-14'} "
                               "day odds, on 29 base years, not CPC's 30")


def test_V2_thirty_year_places_keep_cpc_s_words(banks, client, monkeypatch):
    """At 30 base years the label is 286's, unchanged; base_years says 30."""
    body = serve(client, monkeypatch, banks["both"], "/api/weather/cpc/curves",
                 P(bank.PNW_POP)).json()
    for pr in body["products"]:
        cv = pr["vintage"]["curve"]
        assert cv["base_years"] == 30 and "base years" not in cv["label"]
        assert cv["label"].endswith(" day odds")


def test_V2_pooled_bands_under_30_become_none(banks, client, monkeypatch):
    """The 66 v1 cells at short places on the pooled stations' band are, under
    v2, band_basis none, no claim, not drawable (nothing borrowed from a
    30-year neighbour, pantry 298 ccv_base_ck)."""
    c = banks["both"]
    pooled = c.execute(
        "SELECT v2.band_basis, v2.band_claim IS NULL AS no_claim, v2.drawable, count(*) AS n "
        "FROM cpc_curve_verdicts v1 JOIN cpc_curve_verdicts v2 USING (product, place_kind, "
        "place, weighting, season, strength) WHERE v1.method_version = %s "
        "AND v2.method_version = %s AND v1.band_basis = 'pooled_stations' "
        "AND v2.history_years_in_base < 30 GROUP BY 1, 2, 3", (bank.V1, bank.V2)).fetchall()
    assert pooled == [{"band_basis": "none", "no_claim": True, "drawable": False, "n": 66}]
    body = serve(client, monkeypatch, c, "/api/weather/cpc/places", {}).json()
    short = [x for x in cells(body) if x["history_years_in_base"] < 30]
    assert len(short) == 312
    assert sum(1 for x in short if x["band_basis"] == "pooled_stations") == 0
    assert sum(1 for x in short if x["drawable"]) == 245


def test_V2_places_counts(banks, client, monkeypatch):
    body = serve(client, monkeypatch, banks["both"], "/api/weather/cpc/places", {}).json()
    n = banks["both"].execute(
        "SELECT count(*) AS n, count(*) FILTER (WHERE drawable) AS d FROM cpc_curve_verdicts "
        "WHERE method_version = %s", (bank.V2,)).fetchone()
    assert (body["place_count"], body["cell_count"], body["drawable_count"]) == (30, 720, 641)
    assert (body["cell_count"], body["drawable_count"]) == (n["n"], n["d"])


# ═══════════════════════════════════════════════════════════════════════════
# V3 — clause 5 at the cell's floor; base_reason verbatim
# ═══════════════════════════════════════════════════════════════════════════

# pantry's own example of the sentence (handback 2026-10-10 short base, section 2)
BASE_REASON = ("on 21 base years, under the 22 of the shortest base measured "
               "(D-09-25-177 (5), not a ruled floor): no curve is written")


@pytest.fixture(scope="module")
def ontario_cell(banks):
    """A real v2 cell: USW00003102 (Ontario, 22 base years), 610temp SON strong."""
    r = banks["both"].execute(
        "SELECT * FROM cpc_curve_verdicts WHERE method_version = %s AND place = 'USW00003102' "
        "AND product = '610temp' AND season = 'SON' AND strength = 'strong'",
        (bank.V2,)).fetchone()
    return {"season": r["season"], "strength": r["strength"], "drawable": r["drawable"],
            **{k: r[k] for k in co.CELL_KEYS}, "backtest_version": r["backtest_version"]}


def test_V3_clause_5_reads_the_cells_floor(ontario_cell):
    c = dict(ontario_cell)
    assert (c["history_years_in_base"], c["min_base_years"], c["drawable"]) == (22, 22, True)
    assert co.reasons(c) == []
    # at its floor and not drawable for another term: no clause 5
    c.update(drawable=False, band_claim=None, band_basis="none")
    assert [r["code"] for r in co.reasons(c)] == ["no_band_claim"]
    # under its floor: clause 5, with the floor and the bank's sentence, verbatim
    c.update(history_years_in_base=21, base_reason=BASE_REASON)
    assert co.reasons(c)[0] == {"code": "under_min_base_years", "clause": 5,
                                "history_years_in_base": 21, "min_base_years": 22,
                                "base_reason": BASE_REASON}
    # the floor is the cell's: 29 years is under a v1 cell's 30 and not under v2's 22
    v1_cell = dict(c, history_years_in_base=29, min_base_years=30, base_reason=None)
    assert [r["code"] for r in co.reasons(v1_cell)] == ["under_min_base_years", "no_band_claim"]
    assert co.reasons(v1_cell)[0]["min_base_years"] == 30
    assert [r["code"] for r in co.reasons(dict(v1_cell, min_base_years=22))] == ["no_band_claim"]


def test_V3_nothing_is_derived_from_base_reason():
    """reasons() hands base_reason on as it is and reads no number into or out
    of it; no 30 is left in it."""
    src = inspect.getsource(co.reasons)
    code = [ln.strip() for ln in src.splitlines()
            if "base_reason" in ln and not ln.strip().startswith("#")]
    assert code == ['"base_reason": cell["base_reason"]})']
    assert not re.search(r"\b30\b", src)
    c = {"drawable": False, "verdict": "beats", "history_years_in_base": 21,
         "min_base_years": 22, "base_reason": "any words at all, 7 of them", "band_claim": "x",
         "band_basis": "cell", "season_t": None, "season_skill": None, "season_n_eff": None}
    assert co.reasons(c)[0]["base_reason"] == "any words at all, 7 of them"


def test_V3_base_reason_rides_verbatim_on_every_cell(banks, client, monkeypatch):
    """Every served cell's five count fields are its row's; base_reason is null
    on all 720 today (no v2 place is under 22), so no clause 5 is served."""
    c = banks["both"]
    rows = {(r["place_kind"], r["place"], r["weighting"], r["product"], r["season"],
             r["strength"]): r for r in c.execute(
        "SELECT * FROM cpc_curve_verdicts WHERE method_version = %s", (bank.V2,))}
    body = serve(client, monkeypatch, c, "/api/weather/cpc/places", {}).json()
    n = 0
    for p in body["places"]:
        for pr in p["products"]:
            for s in pr["seasons"]:
                for x in s["cells"]:
                    r = rows[(p["place_kind"], p["place"], p["weighting"], pr["product"],
                              s["season"], x["strength"])]
                    for k in ("min_base_years", "base_reason", "base_by_station",
                              "base_fewest_stations", "threaded_base"):
                        assert x[k] == r[k], k
                    n += 1
    assert n == 720 and {r["base_reason"] for r in rows.values()} == {None}
    codes = {r["code"] for x in cells(body) for r in x["reasons"]}
    assert codes == {"season_does_not_beat_equal_odds", "no_band_claim"}


def test_V3_drawable_rule_quotes_298():
    ddl = flat((ROOT / "tests/fixtures/cpc_method_pin_d091691/ddl_286_298.sql").read_text())
    quoted = co.DRAWABLE_RULE.split(":")[0]
    assert quoted == ("drawable = (verdict = 'beats' AND history_years_in_base >= "
                      "min_base_years AND band_claim IS NOT NULL)")
    assert ("drawable = (verdict = 'beats' AND history_years_in_base >= min_base_years "
            "AND band_claim IS NOT NULL)") in ddl
    assert "migration 298" in co.DRAWABLE_RULE and ">= 30" not in co.DRAWABLE_RULE


# ═══════════════════════════════════════════════════════════════════════════
# V4 — d091691's P4 source rule, for v2
# ═══════════════════════════════════════════════════════════════════════════

_REF = re.compile(r"\b(FROM|JOIN)\s+(" + "|".join(RELATIONS) + r")\b(?:\s+(\w+))?", re.I)
_NOT_ALIAS = {"where", "on", "join", "left", "cross", "order", "group", "limit", "offset"}


def unpinned_refs(sql: str, version: str) -> list[str]:
    """Each FROM/JOIN of the three relations whose alias does not carry
    `<alias>.method_version = '<version>'` in the statement (an unaliased
    reference cannot carry it, so it counts). d091691's rule, unchanged."""
    bad = []
    for m in _REF.finditer(sql):
        alias = m.group(3)
        if not alias or alias.lower() in _NOT_ALIAS:
            bad.append(f"{m.group(2)} (no alias)")
        elif not re.search(rf"(?<![\w.]){alias}\.method_version = '{re.escape(version)}'", sql):
            bad.append(f"{m.group(2)} {alias}")
    return bad


def _statements(mod):
    return {n: flat(getattr(mod, n)) for n in dir(mod) if n.endswith("_SQL")}


def test_V4_one_constant():
    assert co.CPC_METHOD_VERSION == "cpc_curves_v2"
    src = (ROOT / "cpc_outlooks.py").read_text()
    assert src.count('"cpc_curves_v2"') == 1 and src.count("'cpc_curves_v2'") == 0
    assert src.count('"cpc_curves_v1"') == 0 and src.count("'cpc_curves_v1'") == 0
    assert "CPC_METHOD_VERSION" in src.split("_PIN =")[1].splitlines()[0]


def test_V4_every_statement_naming_the_relations_carries_the_pin():
    named = {n: s for n, s in _statements(co).items()
             if any(re.search(rf"\b{r}\b", s) for r in RELATIONS)}
    assert set(named) == set(PINNED_READS)
    for n, s in named.items():
        assert _REF.search(s), n
        assert unpinned_refs(s, co.CPC_METHOD_VERSION) == [], n
        assert "cpc_curves_v1" not in s, n
    for n in set(_statements(co)) - set(named):
        assert not any(r in getattr(co, n) for r in RELATIONS), n


def test_V4_every_reference_is_counted():
    counts = {n: len(_REF.findall(flat(getattr(co, n)))) for n in PINNED_READS}
    assert counts == {"CURVES_SQL": 5, "PLACES_SQL": 3, "PLACES_NEWEST_SQL": 2}


def test_V4_the_v1_pin_fails_the_rule_for_v2():
    """main a10b6f2's statements (d091691's pin): pinned, but to v1, so every
    reference fails the rule read for v2, and passes it read for v1."""
    for n in PINNED_READS:
        assert len(unpinned_refs(V1_SQL[n], bank.V2)) == len(_REF.findall(V1_SQL[n])), n
        assert unpinned_refs(V1_SQL[n], bank.V1) == [], n


# ═══════════════════════════════════════════════════════════════════════════
# V5 — the plans on Neon, and STOP-P
# ═══════════════════════════════════════════════════════════════════════════

def _lit(v):
    if isinstance(v, list):
        return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
    return str(v) if isinstance(v, int) else f"'{v}'"


def _plans(path):
    out = {}
    for seg in path.read_text().split("== ")[1:]:
        head, _, body = seg.partition("\n")
        out.setdefault(head.split()[0], []).append(body)
    return out


def _ms(plan):
    return float(re.search(r"Execution Time: ([0-9.]+) ms", plan).group(1))


EXPLAIN = re.compile(r"^-- (\w+) (\w+_SQL) (\{.*?\})\n(.*?);\s*$", re.M | re.S)


def test_V5_the_receipts_ran_the_pinned_and_the_v1_sql():
    blocks = EXPLAIN.findall((RECEIPTS / "explains.sql").read_text())
    assert {t for t, *_ in blocks} == {f"{s}0{i}" for s in "AB" for i in range(1, 7)}
    plans = _plans(RECEIPTS / "plans_raw.txt")
    for tag, name, params, stmt in blocks:
        p = json.loads(params)
        sql = flat(getattr(co, name)) if tag[0] == "A" else V1_SQL[name]
        want = re.sub(r"%\((\w+)\)s", lambda m: _lit(p[m.group(1)]), sql)
        assert flat(stmt) == "EXPLAIN (ANALYZE, BUFFERS) " + want, tag
        assert plans[tag] and all("Execution Time" in x for x in plans[tag]), tag
    assert json.loads((RECEIPTS / "pinned_sql.json").read_text()) == _statements(co)


READS = ("01", "02", "03", "04", "05", "06")
# STOP-P (d091691's): no whole-table scan that was not one, and no read more
# than 3x its d091691 timing. Read 04 is over: KDEN n=14 drew nothing under v1
# (no window row, so the view was never read: 1.33 ms) and draws 14 frames
# under v2. This test records that, it does not excuse it: the handback's
# first section puts it to the architect.
STOP_P_OVER = {"04"}


def test_V5_stop_p():
    ours = _plans(RECEIPTS / "plans_raw.txt")
    theirs = _plans(PIN / "plans_raw.txt")
    over = set()
    for n in READS:
        a, b, d = ours[f"A{n}"][-1], ours[f"B{n}"][-1], theirs[f"A{n}"][-1]
        if _ms(a) > 3 * _ms(d):
            over.add(n)
        assert "Seq Scan on cpc_outlook_curves" not in a, n
        seq = [len(re.findall(r"Seq Scan on cpc_curve_verdicts", x)) for x in (a, b, d)]
        assert seq[0] == seq[1] <= seq[2], (n, seq)     # no whole-table scan is new
        if "cpc_outlook_curves" in a:
            assert "uniq_coc_row" in a, n
    assert over == STOP_P_OVER
    # 04 under v1: the view lateral never ran; under v2 it ran once per day row
    assert "(never executed)" in theirs["A04"][-1]
    assert "rows=84 loops=1" in ours["A04"][-1]


def test_V5_the_flip_is_not_slower_than_production_today():
    """Against the v1-pinned SQL run in the same session (production today,
    with v2's 720 verdicts already in the table): every v2 read but 04, which
    now draws, is faster or level (within 1.25x, noise)."""
    ours = _plans(RECEIPTS / "plans_raw.txt")
    for n in READS:
        a, b = _ms(ours[f"A{n}"][-1]), _ms(ours[f"B{n}"][-1])
        if n == "04":
            assert a > b
        else:
            assert a <= 1.25 * b, (n, a, b)
