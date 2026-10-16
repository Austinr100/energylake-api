"""d091757 — the API serves the 7-day outlook (/outlook) and the arrival-time objects (toa on
/products), on banked vectors (D-09-25-234, D-09-25-236; D-09-05-T; D-09-25-75 / -76).

  B   the bank: every display:two_* and display:toa_* row and NHC's toa_*_kmz rows of the
      four storms, Neon's verbatim (re-read 2026-10-16 13:09:21Z after D-09-25-236's
      withdrawal, manifest.json "d091757"), held in the test cluster to 314's and 316's
      CHECKs; no row constructed. Every TOA row is the corrected parser's (labels.by_source
      on every one) and names, in its withdrawal note, the vintage_id it replaced
  S   the stamp is the issuance: 12 digits, never an hour and never r<n>; two outlooks of
      one hour are two issuances (10-14 05:17Z / 05:22Z, 10-15 23:11Z / 23:47Z); the
      default is the newest stamp; every banked graphics stamp is served at itself
  P   the text is paired by its own stamp: the basin's newest at or before the graphics'
      stamp, every banked stamp, every basin (cp is served ep's text)
  O   the outlook body: the publisher's meta verbatim (the disturbances as banked, no
      field derived, D-09-25-76), the rules, the not_published absences, 404 / 400
  T   TOA on /products: every one of the 56 banked identities served as its copy in
      force (the 8 corrections as r1, original stated), the label table the row's own
      keys under the row's own names, verbatim (D-09-25-242 §1(d): labels, labels_null,
      timezone, withdrawal; never a key the publisher does not write, so no label_rule);
      the four contour-only objects with parsed 0 as banked; not_issued on NHC's refs
  W   the wire: the statement timeout first, the memo, each stamp read names one
      product and reads a key, 294's view carries no display product
  X1  the Neon receipts of the new reads: no seq scan; the stamp read is one backward
      tfv_identity probe
  R1  the bodies handed back (docs/receipts/tropics-api-outlook-toa-d091757/bodies/) are
      what the routes serve over the bank

The rehearsal is tests/rehearse_tropics_outlook_toa_d091757.py; its named reds are
test_S_an_hour_is_not_a_stamp, test_S_two_outlooks_of_one_hour_are_two_issuances,
test_P_the_text_is_paired_by_its_own_stamp (the stamp mistaken for an hour) and
test_S_r_n_is_not_an_issuance, test_T_each_correction_is_served_as_its_r1 (r<n>
mistaken for an issuance).
"""

import hashlib
import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import main
import tropics_index as ti
import load_bank_d091741 as lb

ROOT = pathlib.Path(__file__).resolve().parent.parent
P = "/api/weather/tropics"
RECEIPTS = ROOT / "docs" / "receipts" / "tropics-api-outlook-toa-d091757"
CACHES = ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache",
          "_tropics_outlook_cache")
DISPLAY = "https://display.energylake.io/"
BASE = re.compile(r"r[1-9][0-9]*$")


def _clear():
    for c in CACHES:
        if hasattr(main, c):            # main before this lane has no outlook memo: red by name, not at setup
            getattr(main, c).clear()


@pytest.fixture(autouse=True)
def _cold(monkeypatch):
    _clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lb.NOW)
    yield
    _clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def pg():
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")
    with lb.cluster(padded=True) as conn:
        yield conn


def get(client, monkeypatch, conn, path, status=200):
    _clear()
    pool = lb.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, (path, r.text[:500])
    return r.json(), pool


def base(key: str) -> str:
    """The issuance a key is of. Python's re takes the first alternative of 316's base
    pattern, so the mirror strips r<n> (d091746 §7.12)."""
    return BASE.sub("", key)


TWO = lb.rows("two") if (lb.FIX / "bank" / "two.jsonl.gz").exists() else []
TOA = lb.rows("toa") if (lb.FIX / "bank" / "toa.jsonl.gz").exists() else []


def in_force(rows, product, storm_id, issuance):
    """The copy in force as 314's view names it: the highest revision, then the newest
    publish, then the newest vintage_id."""
    c = [r for r in rows if (r["product"], r["storm_id"], base(r["vintage_key"])) ==
         (product, storm_id, issuance) and r["status"] == "banked"]
    if not c:
        return None
    rev = lambda r: int(r["vintage_key"].rsplit("r", 1)[1]) if BASE.search(r["vintage_key"]) else 0
    return max(c, key=lambda r: (rev(r), r["fetch_ts"], r["vintage_id"]))


def stamps(product):
    return sorted({base(r["vintage_key"]) for r in TWO if r["product"] == f"display:{product}"})


AREAS = stamps("two_areas")


def paired(text_product, stamp):
    """The bank's own answer: the text's newest stamp at or before the graphics'."""
    c = [s for s in stamps(text_product) if s <= stamp]
    return c[-1] if c else None


def by_product(items):
    return {o["product"]: o for o in items}


# ═══════════════════════════════════════════════════════════════════════════
# B — the bank
# ═══════════════════════════════════════════════════════════════════════════

def test_B_the_fixture_carries_the_neon_read_stamp_and_counts():
    d = lb.MANIFEST["d091757"]
    assert d["count"]["ro"] == "on" and d["count"]["read_at"] == "2026-10-16T13:09:21.898Z"
    for kind, n in (("two", 136), ("toa", 120), ("nhc_toa", 122)):
        assert d["bank"][kind]["rows"] == n and d["bank"][kind]["read_at"] == d["count"]["read_at"]
        assert len(lb.rows(kind)) == n, kind                     # rows() checks Neon's sha first
    assert (d["count"]["n_two"], d["count"]["n_toa"]) == ("136", "120")
    # 317 (d091761, cwms_*_irregular datasets) landed since 316; the view is still 316's
    assert d["count"]["max_migration"] == 317
    assert d["count"]["view_md5"] == "977c97d66d70ff8a8ed7e6992545f6c7"    # 316's view


def test_B_what_the_rows_are():
    assert {r["source"] for r in TWO + TOA} == {"display"}
    assert {r["storm_id"] for r in TWO} == {""}
    assert all(re.fullmatch(r"[0-9]{12}", r["vintage_key"]) for r in TWO)   # 316: the stamp, no r<n>
    assert sorted({r["product"] for r in TWO}) == ["display:two_areas", "display:two_points",
                                                   "display:two_text_at", "display:two_text_ep"]
    assert [len(stamps(p)) for p in ("two_areas", "two_points", "two_text_at", "two_text_ep")] == \
        [39, 39, 29, 29]
    # every row's own meta.stamp is its key (the publisher's), and each object's key carries it
    assert all(r["meta"]["stamp"] == r["vintage_key"] and f"/{r['vintage_key']}/" in r["r2_key"] for r in TWO)
    r1 = sorted((r["storm_id"], r["vintage_key"], r["product"]) for r in TOA if BASE.search(r["vintage_key"]))
    assert r1 == sorted((s, a + "r1", f"display:toa_{k}") for s, a in (
        ("ep182026", "054"), ("ep182026", "056"), ("ep182026", "057"), ("ep212026", "001"))
        for k in ("earliest", "mostlikely"))
    # D-09-25-236's withdrawal (run 38888308463): every TOA row is its republish, one fetch_ts
    d = lb.MANIFEST["d091757"]["count"]
    assert (d["n_toa_label_rule"], d["n_toa_withdrawn"], d["n_toa_first_parser"]) == ("0", "120", "0")
    assert {r["fetch_ts"] for r in TOA} == {"2026-10-16T10:57:17.013635+00:00"}
    assert (min(r["vintage_id"] for r in TOA), max(r["vintage_id"] for r in TOA)) == (8603, 8722)


def test_B_no_first_parser_row_is_banked():
    """The withdrawal's own selector (pantry scripts/withdraw_toa_d091756.py) is "no
    labels.by_source": no banked row meets it, and no row carries label_rule, a key the
    publisher does not write."""
    assert all("by_source" in r["meta"]["labels"] for r in TOA)
    assert {tuple(sorted(r["meta"]["labels"])) for r in TOA} == {
        ("backwards", "by_source", "icon_bands", "n", "not_a_time", "null", "parsed")}
    assert not any("label_rule" in r["meta"] for r in TOA)
    n = sum(r["meta"]["labels"]["n"] for r in TOA)
    parsed = sum(r["meta"]["labels"]["parsed"] for r in TOA)
    assert (n, parsed, n - parsed) == (2792, 2672, 120)
    # the 120 nulls are the 5 % WSP contours, one per object, said not_a_time
    assert all(r["meta"]["labels"]["null"] == r["meta"]["labels"]["not_a_time"] == 1 for r in TOA)
    assert {json.dumps(r["meta"]["labels_null"]) for r in TOA} == {
        json.dumps([{"why": "not_a_time", "label": "Wind Speed Probability 5% contour"}])}
    assert all(isinstance(r["meta"]["advisory_in_object"], str) for r in TOA)
    assert not any("advisory_check" in r["meta"] for r in TOA)        # the KML carries every advisory
    assert sum("correction" in r["meta"] for r in TOA) == 8


def test_B_every_withdrawal_names_the_ruling_and_the_row_it_replaced():
    ws = [r["meta"]["withdrawal"] for r in TOA]
    assert {w["ruling"] for w in ws} == {"D-09-25-236"}
    assert {w["note"] for w in ws} == {"withdrawn and republished under D-09-25-236, no reader existed"}
    replaced = sorted(w["withdrawn_vintage_id"] for w in ws)
    # one row replaced per row: the 112 the first bank held (8139-8250), and the 8 the
    # scheduled publish wrote after it on the first parser (02:54Z and 08:58Z)
    assert replaced == list(range(8139, 8251)) + [8440, 8441, 8442, 8443, 8578, 8579, 8580, 8581]
    assert all(r["meta"]["withdrawal"]["withdrawn_vintage_id"] < r["vintage_id"] for r in TOA)
    assert all(re.fullmatch(r"[0-9a-f]{64}", w["withdrawn_sha256"]) for w in ws)


def test_B_the_cluster_holds_every_banked_row_under_316s_checks(pg):
    n = pg.execute("SELECT count(*) AS n FROM tropical_file_vintage WHERE source = 'display' "
                   "AND (product LIKE 'display:two\\_%' OR product LIKE 'display:toa\\_%')").fetchone()["n"]
    assert n == len(TWO) + len(TOA)
    assert pg.execute("SELECT version FROM schema_migrations WHERE version = 316").fetchone()
    view = pg.execute("SELECT md5(pg_get_viewdef('tropical_display_in_force'::regclass)) AS m").fetchone()["m"]
    assert view == "977c97d66d70ff8a8ed7e6992545f6c7"
    # 316's view reads each stamp whole: one advisory_or_cycle per stamp, the stamp itself
    got = pg.execute("SELECT advisory_or_cycle AS s FROM tropical_display_in_force "
                     "WHERE product = 'display:two_areas' ORDER BY 1").fetchall()
    assert [r["s"] for r in got] == AREAS


# ═══════════════════════════════════════════════════════════════════════════
# S — the stamp is the issuance
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("raw", ["2026101405", "2026101523"])
def test_S_an_hour_is_not_a_stamp(client, monkeypatch, pg, raw):
    with pytest.raises(ValueError, match="an hour"):
        ti.parse_stamp(raw)
    b, pool = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep&stamp={raw}", status=400)
    assert "hour" in json.dumps(b) and pool.calls == []


@pytest.mark.parametrize("raw", ["202610140517r1", "202610152347r2"])
def test_S_r_n_is_not_an_issuance(client, monkeypatch, pg, raw):
    with pytest.raises(ValueError, match="correction"):
        ti.parse_stamp(raw)
    b, pool = get(client, monkeypatch, pg, f"{P}/outlook?basin=al&stamp={raw}", status=400)
    assert "correction" in json.dumps(b) and pool.calls == []
    # the stamp read cuts the base off the key: a correction's key is never a stamp
    assert "SELECT substring(a.vintage_key FROM '^[0-9]{12}') AS stamp" in ti.OUTLOOK_STAMP_SQL


@pytest.mark.parametrize("raw", ["20261015234", "2026101523470", "202613152347", "202610152360",
                                 "202602301200", "ABNT20_KNHC_152319", "2026-10-15T23:47"])
def test_S_a_malformed_stamp_is_a_400(client, monkeypatch, pg, raw):
    with pytest.raises(ValueError):
        ti.parse_stamp(raw)
    get(client, monkeypatch, pg, f"{P}/outlook?basin=al&stamp={raw}", status=400)


@pytest.mark.parametrize("pair", [("202610140517", "202610140522"), ("202610152311", "202610152347")],
                         ids=["10-14T05", "10-15T23"])
def test_S_two_outlooks_of_one_hour_are_two_issuances(client, monkeypatch, pg, pair):
    a, b = pair
    assert a[:10] == b[:10] and a in AREAS and b in AREAS
    ba, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep&stamp={a}")
    bb, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep&stamp={b}")
    for body, st in ((ba, a), (bb, b)):
        assert body["stamp"] == body["graphics"]["stamp"] == st and body["stamp_requested"]
        objs = by_product(body["graphics"]["objects"])
        for name in ti.OUTLOOK_GRAPHICS:
            row = in_force(TWO, f"display:{name}", "", st)
            assert (objs[name]["url"], objs[name]["sha256"], objs[name]["stamp"]) == \
                (DISPLAY + row["r2_key"], row["sha256"], st)
    assert by_product(ba["graphics"]["objects"])["two_areas"]["sha256"] != \
        by_product(bb["graphics"]["objects"])["two_areas"]["sha256"]


def test_S_the_default_is_the_newest_stamp(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al")
    assert b["stamp"] == AREAS[-1] == "202610161136" and not b["stamp_requested"]
    named, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al&stamp={AREAS[-1]}")
    for k in ("graphics", "text", "revision", "corrected", "issued_ts"):
        assert b[k] == named[k], k


@pytest.mark.parametrize("stamp", AREAS)
def test_S_every_banked_stamp_is_served_at_itself(client, monkeypatch, pg, stamp):
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al&stamp={stamp}")
    objs = by_product(b["graphics"]["objects"])
    assert b["graphics"]["absent"] == [] and sorted(objs) == ["two_areas", "two_points"]
    for name in ti.OUTLOOK_GRAPHICS:
        row = in_force(TWO, f"display:{name}", "", stamp)
        o = objs[name]
        assert (o["url"], o["sha256"], o["bytes"]) == (DISPLAY + row["r2_key"], row["sha256"], row["byte_size"])
        assert (o["revision"], o["corrected"], o["original"]) == (0, False, None)
    assert b["issued_ts"] == objs["two_areas"]["issued_utc"]


@pytest.mark.parametrize("stamp", ["202610152319", "202610152310", "202610152348", "202501010000"])
def test_S_a_stamp_with_no_graphics_is_a_404(client, monkeypatch, pg, stamp):
    """A text's own stamp is not a graphics issuance (23:19Z is two_text_at's, 23:10Z
    two_text_ep's): the stamp names the gtwo zip."""
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al&stamp={stamp}", status=404)
    assert stamp in b["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# P — the text is paired by its own stamp
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("stamp", AREAS)
@pytest.mark.parametrize("basin", ["al", "ep", "cp"])
def test_P_the_text_is_paired_by_its_own_stamp(client, monkeypatch, pg, basin, stamp):
    name = {"al": "two_text_at", "ep": "two_text_ep", "cp": "two_text_ep"}[basin]
    want = paired(name, stamp)
    assert want is not None                    # every banked graphics stamp has a text at or before it
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin={basin}&stamp={stamp}")
    t = b["text"]
    row = in_force(TWO, f"display:{name}", "", want)
    assert (t["product"], t["stamp"], t["url"], t["sha256"]) == \
        (name, want, DISPLAY + row["r2_key"], row["sha256"])
    assert t["absence"] is None and t["stamp"] <= b["stamp"]


def test_P_the_pairs_the_brief_names():
    """The bank's own vectors where the stamp and the hour disagree: the text in force at
    05:17Z for ep is the 10-13 23:16Z one (ep's 05:22Z came after the zip); at 23:11Z for
    al, the 17:51Z one (al's 23:19Z came after)."""
    assert paired("two_text_ep", "202610140517") == "202610132316"
    assert paired("two_text_ep", "202610140522") == "202610140522"
    assert paired("two_text_at", "202610152311") == "202610151751"
    assert paired("two_text_at", "202610152347") == "202610152319"
    assert paired("two_text_at", "202610151751") == "202610151751"     # one stamp, both objects


def test_P_cp_is_served_eps_text_and_says_so(client, monkeypatch, pg):
    cp, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=cp")
    ep, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep")
    assert cp["basin"] == "cp" and cp["text"] == ep["text"] and cp["text"]["text_basin"] == "ep"
    assert cp["text"]["basin_heading"] == "For the eastern and central North Pacific east of 180 longitude:"
    assert "no CPHC outlook text" in cp["basin_rule"]


# ═══════════════════════════════════════════════════════════════════════════
# O — the outlook body
# ═══════════════════════════════════════════════════════════════════════════

def text_keys():
    return ["product", "text_basin", "url", "sha256", "bytes", "basin_heading", *ti.META_TWO_TEXT,
            "revision", "corrected", "original", "absence"]


def test_O_the_text_is_the_publishers_meta_verbatim(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep&stamp=202610152347")
    t = b["text"]
    row = in_force(TWO, "display:two_text_ep", "", "202610152310")
    assert list(t) == text_keys()
    for k in ti.META_TWO_TEXT:
        assert t[k] == row["meta"][k], k
    assert t["basin_heading"] == row["meta"]["basin"]
    # D-09-25-76: the disturbances as banked; no area number, headline or wording derived
    assert t["disturbances"] == [{"title": "South of Southern Mexico", "number": None, "prob_2day": 0,
                                  "prob_7day": 50, "risk_2day": "low", "risk_7day": "medium"}]
    assert (t["issued_utc"], t["transmitted_utc"]) == ("2026-10-16T00:00:00+00:00", "2026-10-15T23:10:00+00:00")


def test_O_the_graphics_carry_the_publishers_meta(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al")
    for o in b["graphics"]["objects"]:
        row = in_force(TWO, f"display:{o['product']}", "", b["stamp"])
        for k in ti.META_TWO_GRAPHICS:
            assert o[k] == row["meta"][k], k
        assert "source_in_force" not in o           # the outlook reads no NHC row


def test_O_the_rules_and_the_shape(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/outlook?basin=al")
    assert list(b) == ["basin", "stamp", "stamp_requested", "issued_ts", "revision", "corrected",
                       "graphics", "text", "absence", "stamp_rule", "pairing_rule", "basin_rule",
                       "revision_rule", "index_rule", "reasons", "timestamps", "not_served",
                       "nhc_terms", "cache"]
    assert b["absence"] is None and (b["revision"], b["corrected"]) == (0, False)
    assert "12-digit stamp" in b["stamp_rule"] and "never an issuance" in b["stamp_rule"]
    assert "at or before" in b["pairing_rule"] and b["reasons"] == ti.OUTLOOK_REASONS


def test_O_basin_is_required_and_named(client, monkeypatch, pg):
    get(client, monkeypatch, pg, f"{P}/outlook", status=400)
    get(client, monkeypatch, pg, f"{P}/outlook?basin=at", status=400)
    get(client, monkeypatch, pg, f"{P}/outlook?basin=wp", status=400)


def test_O_the_absences_state_their_reason():
    """The banked rows hold no stamp without a text at or before it, and no stamp without
    its points: the absences are read off the builder with the rows the read would return
    (none), not off a constructed row."""
    areas = [{"source": "display", "product": "display:two_areas", "r2_key": "tropics/two/202610152347/areas.geojson",
              **{k: v for k, v in in_force(TWO, "display:two_areas", "", "202610152347").items()
                 if k in ("sha256", "byte_size", "issued_ts", "meta")},
              "revision": 0, "corrected": False}]
    b = ti.build_outlook(basin="al", stamp="202610152347", requested=None, rows=areas,
                         text_name="two_text_at", text_stamp=None, text_rows=[])
    assert b["graphics"]["absent"] == [{"product": "two_points", "reason": "not_published",
                                        "detail": "the ledger holds no published display:two_points at "
                                                  "stamp 202610152347"}]
    assert b["text"]["absence"]["reason"] == "not_published" and b["text"]["url"] is None
    assert list(b["text"]) == text_keys()
    none = ti.build_outlook(basin="al", stamp=None, requested=None, rows=[], text_name="two_text_at",
                            text_stamp=None, text_rows=[])
    assert none["absence"]["reason"] == "not_published" and none["graphics"]["objects"] == []


# ═══════════════════════════════════════════════════════════════════════════
# T — the arrival-time objects on /products
# ═══════════════════════════════════════════════════════════════════════════

TOA_IDS = sorted({(r["storm_id"], base(r["vintage_key"])) for r in TOA})


def test_T_the_toa_pairs_and_their_refs():
    assert ti.TOA_PRODUCTS == (("toa_earliest", "toa_earliest_kmz"), ("toa_mostlikely", "toa_mostlikely_kmz"))
    # 60 rows per kind: 56 advisories, 4 of them corrected (r1)
    assert len(TOA_IDS) == 56
    assert all(sum(r["product"] == f"display:{k}" for r in TOA) == 60 for k in ("toa_earliest", "toa_mostlikely"))


@pytest.mark.parametrize("sid,adv", TOA_IDS)
def test_T_every_banked_identity_is_served_as_its_copy_in_force(client, monkeypatch, pg, sid, adv):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id={sid}&advisory={adv}")
    objs = by_product(b["toa"]["objects"])
    assert sorted(objs) == ["toa_earliest", "toa_mostlikely"] and b["toa"]["absent"] == []
    for name in ("toa_earliest", "toa_mostlikely"):
        row = in_force(TOA, f"display:{name}", sid, adv)
        o, meta = objs[name], row["meta"]
        assert (o["url"], o["sha256"], o["bytes"]) == (DISPLAY + row["r2_key"], row["sha256"], row["byte_size"])
        rev = 1 if row["vintage_key"].endswith("r1") else 0
        assert (o["revision"], o["corrected"]) == (rev, rev > 0)
        # the label table is the row's keys under the row's names, verbatim: nothing parsed,
        # renamed or filled (D-09-25-242 §1(d))
        assert o["labels"] == {"labels": meta["labels"], "labels_null": meta["labels_null"],
                               "timezone": meta["timezone"], "withdrawal": meta["withdrawal"]}
        assert "by_source" in o["labels"]["labels"] and "label_rule" not in json.dumps(o)
        assert o["advisory_in_object"] == meta["advisory_in_object"] and "advisory_check" not in o
    assert b["revision"] >= max(o["revision"] for o in objs.values())


@pytest.mark.parametrize("sid,adv", [("ep182026", "054"), ("ep182026", "056"), ("ep182026", "057"),
                                     ("ep212026", "001")])
def test_T_each_correction_is_served_as_its_r1(client, monkeypatch, pg, sid, adv):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id={sid}&advisory={adv}")
    for name in ("toa_earliest", "toa_mostlikely"):
        o = by_product(b["toa"]["objects"])[name]
        r1 = in_force(TOA, f"display:{name}", sid, adv)
        r0 = [r for r in TOA if (r["product"], r["storm_id"], r["vintage_key"]) == (f"display:{name}", sid, adv)][0]
        assert r1["vintage_key"] == adv + "r1"
        assert o["url"] == f"{DISPLAY}tropics/{sid}/adv{adv}r1/{name}.geojson" and o["sha256"] == r1["sha256"]
        assert (o["revision"], o["corrected"]) == (1, True)
        assert o["original"] == {"url": DISPLAY + r0["r2_key"], "sha256": r0["sha256"],
                                 "bytes": r0["byte_size"], "absence": None}
        assert o["source_sha256"] == r1["meta"]["source_sha256"] != r0["meta"]["source_sha256"]
    assert (b["revision"], b["corrected"]) == (1, True)


def test_T_the_054_pair_names_the_corrected_nhc_file_in_force(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026&advisory=054")
    e = by_product(b["toa"]["objects"])["toa_earliest"]
    assert e["source_r2_key"] == "tropical/nhc/2026/ep182026/adv054/toa_earliest_kmz__c10b68e0.kmz"
    assert e["source_in_force"] is True
    assert e["advisory_in_object"] == "54"
    assert e["labels"] == {
        "labels": {"n": 13, "null": 1, "parsed": 12, "backwards": [], "by_source": {"icon": 9, "description": 4},
                   "icon_bands": 3, "not_a_time": 1},
        "labels_null": [{"why": "not_a_time", "label": "Wind Speed Probability 5% contour"}],
        "timezone": "PDT",
        "withdrawal": {"note": "withdrawn and republished under D-09-25-236, no reader existed",
                       "ruling": "D-09-25-236",
                       "withdrawn_sha256": "f06dffbd6e44b32491b217ce39460de5a5a8cbb9fec48c0e8721d760ada9053e",
                       "withdrawn_fetch_ts": "2026-10-15T21:17:51.515087+00:00", "withdrawn_vintage_id": 8150}}


@pytest.mark.parametrize("sid,adv", [("ep182026", "058"), ("ep202026", "036")])
def test_T_a_contour_only_object_is_served_with_parsed_0_as_banked(client, monkeypatch, pg, sid, adv):
    """Four objects carry only the 5 % WSP contour (vintage_ids 8620, 8649, 8680, 8709):
    one label, not a time. Served as the row says it, parsed 0 with its null; no label
    made up for it."""
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id={sid}&advisory={adv}")
    for name in ("toa_earliest", "toa_mostlikely"):
        o = by_product(b["toa"]["objects"])[name]
        row = in_force(TOA, f"display:{name}", sid, adv)
        assert row["vintage_id"] in (8620, 8649, 8680, 8709) and o["sha256"] == row["sha256"]
        assert o["labels"]["labels"] == {"n": 1, "null": 1, "parsed": 0, "backwards": [],
                                         "by_source": {"description": 1}, "icon_bands": 0, "not_a_time": 1}
        assert o["labels"]["labels_null"] == [{"why": "not_a_time", "label": "Wind Speed Probability 5% contour"}]


def test_T_simon_033_mostlikely_as_the_architect_measured(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=033")
    o = by_product(b["toa"]["objects"])["toa_mostlikely"]
    assert o["sha256"] == "bda19f72ea85b4efedc792badbf4c7e19fabaa53d1f69c7fb6f598dc53bf6f78"
    lab = o["labels"]["labels"]
    assert (lab["n"], lab["parsed"], lab["by_source"], lab["icon_bands"]) == (25, 24, {"icon": 18, "description": 7}, 6)
    assert (o["labels"]["timezone"], o["advisory_in_object"]) == ("MST", "33")
    assert o["labels"]["withdrawal"]["withdrawn_vintage_id"] == 8238


@pytest.mark.parametrize("vid", [r["vintage_id"] for r in TOA])
def test_T_the_label_block_serves_only_keys_the_row_carries(vid):
    """D-09-25-242 §1(d), on every banked row (the 112 in force and the 8 originals the
    corrections replaced): the keys the label block serves are a subset of the row's meta
    keys, each served verbatim, and so is every TOA key beside the cone's. The view's
    two columns are read off the key, as 314's view reads them."""
    row = [r for r in TOA if r["vintage_id"] == vid][0]
    rev = 1 if BASE.search(row["vintage_key"]) else 0
    o = ti._toa_object(row["product"].split(":", 1)[1], {**row, "revision": rev, "corrected": False}, None)
    assert set(o["labels"]) <= set(row["meta"])
    assert all(o["labels"][k] == row["meta"][k] for k in o["labels"])
    extra = set(o) - set(ti._object("x", {**row, "revision": rev, "corrected": False}, None)) - {"labels"}
    assert extra <= set(row["meta"]) and extra == {"advisory_in_object"}


@pytest.mark.parametrize("sid,adv,linked", [("ep182026", "054A", "054"), ("al092026", "016", None)])
def test_T_not_issued_on_nhcs_own_refs(client, monkeypatch, pg, sid, adv, linked):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id={sid}&advisory={adv}")
    assert b["toa"]["objects"] == []
    for a in b["toa"]["absent"]:
        assert a["reason"] == "not_issued"
        assert (f"links advisory {linked}'s" in a["detail"]) if linked else ("links no toa_" in a["detail"])


def test_T_the_existing_objects_are_untouched(client, monkeypatch, pg):
    """toa is its own block: /products' objects and absent name cone, ww and surge_ww only."""
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026&advisory=054")
    assert [o["product"] for o in b["objects"]] == ["cone", "ww"]
    assert [a["product"] for a in b["absent"]] == ["surge_ww"]
    assert "D-09-25-236" in b["toa_rule"] and "never parsed, inferred, renamed or filled" in b["toa_rule"]
    assert "D-09-25-242" in b["toa_rule"] and "label_rule" not in b["toa_rule"]


def test_T_no_label_is_read_off_anything_but_the_rows_label_table():
    src = (ROOT / "tropics_index.py").read_text()
    block = src[src.index("def _toa_object"):src.index("def _entry_ref")]
    # the label block is the row's keys, by the publisher's names, where the row has them
    assert ti.TOA_LABEL_KEYS == ("labels", "labels_null", "timezone", "withdrawal")
    assert '"labels": {k: meta[k] for k in TOA_LABEL_KEYS if k in meta}' in block
    assert set(re.findall(r'meta\.get\("([a-z_]+)"\)', block)) == {"advisory_in_object"}
    assert "label_rule" not in block and "counts" not in block
    for word in ("valid_utc", "label_source", "description", "parse"):
        assert word not in block.split('"""')[2], word


def test_T_no_advisory_no_toa(client, monkeypatch, pg):
    b = ti.build_products(storm={"storm_id": "ep992026", "names": [], "basin": "ep"}, advisory=None,
                          adv=None, requested=None, entry=None, rows=[], wsp_rows=[])
    assert b["toa"] is None and b["absence"]["reason"] == "no_advisory"


# ═══════════════════════════════════════════════════════════════════════════
# W — the wire
# ═══════════════════════════════════════════════════════════════════════════

def test_W_the_outlook_reads(client, monkeypatch, pg):
    _, pool = get(client, monkeypatch, pg, f"{P}/outlook?basin=al")
    calls = pool.calls
    assert calls[0][0] == f"SET LOCAL statement_timeout = '{main.TROPICS_STATEMENT_TIMEOUT}'"
    assert [q for q, _ in calls[1:]] == [ti.OUTLOOK_STAMP_SQL, ti.DISPLAY_FILES_SQL, ti.OUTLOOK_STAMP_SQL,
                                         ti.DISPLAY_FILES_SQL]
    assert [p for _, p in calls[1:]] == [
        {"product": "display:two_areas", "key_below": "999999999999s"},
        {"products": ["display:two_areas", "display:two_points"], "storm_id": "",
         "advisory_or_cycle": "202610161136"},
        {"product": "display:two_text_at", "key_below": "202610161136s"},
        {"products": ["display:two_text_at"], "storm_id": "", "advisory_or_cycle": "202610161136"}]
    # a named stamp skips the newest read
    _, pool = get(client, monkeypatch, pg, f"{P}/outlook?basin=ep&stamp=202610140517")
    assert [q for q, _ in pool.calls[1:]] == [ti.DISPLAY_FILES_SQL, ti.OUTLOOK_STAMP_SQL, ti.DISPLAY_FILES_SQL]


def test_W_the_stamp_read_names_one_product_and_reads_a_key():
    sql = " ".join(ti.OUTLOOK_STAMP_SQL.split())
    assert "a.product = %(product)s" in sql and "ANY" not in sql
    assert "ORDER BY a.vintage_key DESC LIMIT 1" in sql and "DISTINCT" not in sql
    assert "a.source = 'display'" in sql and "a.storm_id = ''" in sql and "a.status = 'banked'" in sql
    assert ti.outlook_stamp_params("two_text_ep", "202610140517") == {
        "product": "display:two_text_ep", "key_below": "202610140517s"}


def test_W_the_key_bound_admits_the_stamp_and_its_corrections_and_nothing_later():
    """C collation, as Neon's ledger (datcollate C.UTF-8) and the test cluster: the bound
    '<stamp>s' sits above <stamp> and <stamp>r<n> and below the next minute."""
    k = ti.outlook_stamp_params("two_areas", "202610140517")["key_below"]
    assert sorted(["202610140517", "202610140517r1", "202610140517r12", k, "202610140518",
                   "202610140516r3"]) == ["202610140516r3", "202610140517", "202610140517r1",
                                          "202610140517r12", k, "202610140518"]


def test_W_the_outlook_is_memoised_and_the_products_read_names_toa(client, monkeypatch, pg):
    _clear()
    pool = lb.PgPool(pg)
    monkeypatch.setattr(main, "_pool", pool)
    r1 = client.get(f"{P}/outlook?basin=ep")
    n = len(pool.calls)
    r2 = client.get(f"{P}/outlook?basin=ep")
    assert r1.status_code == r2.status_code == 200 and len(pool.calls) == n
    assert r2.json()["cache"]["state"] == "fresh" and r2.headers["X-Cache"] == "hit"
    _, pool = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026&advisory=054")
    disp = [p for q, p in pool.calls if q == ti.DISPLAY_FILES_SQL and p["storm_id"] == "ep182026"]
    files = [p for q, p in pool.calls if q == ti.FILES_SQL and p["storm_id"] == "ep182026"]
    assert disp[0]["products"][-2:] == ["display:toa_earliest", "display:toa_mostlikely"]
    assert files[0]["products"][-2:] == ["toa_earliest_kmz", "toa_mostlikely_kmz"]
    for q, p in pool.calls:
        if "tropical_file_vintage_in_force" in q:
            assert "display" not in json.dumps(p, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# X1 — the Neon receipts
# ═══════════════════════════════════════════════════════════════════════════

EXPLAINS = json.loads((RECEIPTS / "explains.json").read_text())
PLANS = (RECEIPTS / "neon_plans.txt").read_text()


def _plan(name):
    return PLANS.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]


def test_X1_the_receipts_are_the_routes_statements():
    import psycopg
    for e in EXPLAINS:
        assert f"## {e['name']}\n" in PLANS
    names = [e["name"] for e in EXPLAINS]
    assert names == ["OUTLOOK_STAMP_SQL two_areas newest", "OUTLOOK_STAMP_SQL two_text_at <= 202610152347",
                     "OUTLOOK_STAMP_SQL two_text_ep <= 202610140517",
                     "DISPLAY_FILES_SQL outlook graphics 202610152347",
                     "DISPLAY_FILES_SQL outlook text_at 202610152319",
                     "DISPLAY_FILES_SQL products+toa ep182026 054", "FILES_SQL products+toa ep182026 054"]
    # the statement text is the module's own, the literals its parameter builders'
    assert EXPLAINS[0]["sql"] == " ".join(ti.OUTLOOK_STAMP_SQL.split()).replace(
        "%(product)s", "'display:two_areas'").replace("%(key_below)s", "'999999999999s'")
    assert "'{display:cone,display:ww,display:surge_ww,display:toa_earliest,display:toa_mostlikely}'" \
        in EXPLAINS[5]["sql"]


def test_X1_no_seq_scan_anywhere():
    assert "Seq Scan" not in PLANS
    assert PLANS.count("Execution Time") == len(EXPLAINS)


@pytest.mark.parametrize("name", ["OUTLOOK_STAMP_SQL two_areas newest",
                                  "OUTLOOK_STAMP_SQL two_text_at <= 202610152347",
                                  "OUTLOOK_STAMP_SQL two_text_ep <= 202610140517"])
def test_X1_the_stamp_read_is_one_backward_probe(name):
    p = _plan(name)
    assert "Index Scan Backward using tfv_identity on tropical_file_vintage a" in p
    assert re.search(r"Index Cond: \(.*\(vintage_key < '[0-9]{12}s'::text\)\)", p)
    assert "Buffers: shared hit=3\n" in p and "rows=1 loops=1" in p
    assert float(re.search(r"Execution Time: ([0-9.]+) ms", p).group(1)) < 1.0


@pytest.mark.parametrize("name,walked", [("DISPLAY_FILES_SQL outlook graphics 202610152347", 72),
                                         ("DISPLAY_FILES_SQL outlook text_at 202610152319", 26),
                                         ("DISPLAY_FILES_SQL products+toa ep182026 054", 61)])
def test_X1_the_view_read_is_an_index_range_and_the_stamp_a_filter(name, walked):
    """The view's advisory_or_cycle is substring(vintage_key …): matched as a Filter after
    a (product, storm_id) range, as d091746 measured for WSP; the expression index that
    would make it an Index Cond is pantry's (d091746 §4), not this lane's."""
    p = _plan(name)
    assert re.search(r"Index Scan using (tfv_identity|tfv_display_immutable) on tropical_file_vintage v ", p)
    assert f"Rows Removed by Filter: {walked}\n" in p
    assert "Index Scan using tfv_display_immutable on tropical_file_vintage a" in p     # the original's probe
    assert float(re.search(r"Execution Time: ([0-9.]+) ms", p).group(1)) < 5.0


# ═══════════════════════════════════════════════════════════════════════════
# R1 — the handed-back bodies
# ═══════════════════════════════════════════════════════════════════════════

SERVED = json.loads((RECEIPTS / "bodies" / "served_from.json").read_text())


@pytest.mark.parametrize("name", sorted(SERVED))
def test_R1_the_handed_back_body_is_what_the_route_serves(client, monkeypatch, pg, name):
    b, _ = get(client, monkeypatch, pg, SERVED[name]["path"])
    b.pop("cache")
    assert b == json.loads((RECEIPTS / "bodies" / name).read_text())
    rows = {r["vintage_id"]: r for r in lb.display_rows() + TWO + TOA}
    for o in SERVED[name]["objects"]:
        row = rows[o["vintage_id"]]
        assert (row["vintage_key"], row["sha256"]) == (o["vintage_key"], o["sha256"])


def test_R1_the_bodies_hashes():
    got = {n: hashlib.sha256(json.dumps(json.loads((RECEIPTS / "bodies" / n).read_text()),
                                        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
           for n in sorted(SERVED)}
    assert got == json.loads((RECEIPTS / "bodies_sha256.json").read_text())
