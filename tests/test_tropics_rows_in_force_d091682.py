"""d091682 — the Tropics API reads the rows in force (pantry 294, 297) and says
when an advisory was corrected (ruling D-09-25-174).

  R1  the official forecast of an advisory corrected with different rows is its
      revision 1 only: the changed tau carries revision 1's numbers, and the tau
      that exists only in revision 0 is absent
  R2  /tracks never mixes two revisions of one advisory, and no view row does
  R3  /odds serves the corrected issuance only, below_1pct still carried as itself
  R4  `corrected` and `revision` are true to the ledger for every advisory and
      issuance served: false at revision 0 for every live advisory at the cut,
      true where the ledger says so
  R5  an advisory corrected with identical rows reads corrected true at revision 0,
      every figure unchanged (the live case: Rachel 052A on Neon, 2026-10-10)
  R6  d091673's vectors were re-banked with exactly the two new fields: each
      re-banked body minus them is d091673's body byte for byte
  R7  no page route names the bare tables
  R8  plan receipts: every read through the views is an index read, here and on
      Neon (docs/receipts/tropics-api-d091682/neon_plans_297.json)

The bank is d091673's with migrations 294 and 297 and the ledger at the same
cut laid over it (load_bank_d091682). The constructed corrections
(fixtures/tropics_d091682/corrected.sql) run on a second cluster, so the live
bank's bodies stay the vectors. Reds: docs/receipts/tropics-api-d091682/.
"""

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import main
import tropics as tr
import load_bank_d091673 as lb
import load_bank_d091682 as lb2

ROOT = pathlib.Path(__file__).resolve().parent.parent
P = "/api/weather/tropics"
STORMS = ("al092026", "ep182026", "ep202026")
CACHES = ("_tropics_storms_cache", "_tropics_storm_cache", "_tropics_tracks_cache",
          "_tropics_odds_cache")


@pytest.fixture(autouse=True)
def _cold_memos(monkeypatch):
    for c in CACHES:
        getattr(main, c).clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lb.NOW)
    yield
    for c in CACHES:
        getattr(main, c).clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _need_postgres():
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def pg():
    _need_postgres()
    with lb.cluster() as conn:
        yield conn


@pytest.fixture(scope="module")
def pg_c():
    """The bank with the constructed corrections C1-C3 applied."""
    _need_postgres()
    with lb.cluster() as conn:
        lb2.construct(conn)
        yield conn


def get(client, monkeypatch, conn, path, status=200):
    for c in CACHES:
        getattr(main, c).clear()
    monkeypatch.setattr(main, "_pool", lb.PgPool(conn))
    r = client.get(path)
    assert r.status_code == status, r.text
    return r.json()


def all_bodies(client, monkeypatch, conn, n=8):
    out = {"storms": get(client, monkeypatch, conn, f"{P}/storms")}
    for sid in STORMS:
        out[f"storm {sid}"] = get(client, monkeypatch, conn, f"{P}/storm?storm_id={sid}")
        out[f"tracks {sid}"] = get(client, monkeypatch, conn, f"{P}/tracks?storm_id={sid}&n={n}")
        out[f"odds {sid}"] = get(client, monkeypatch, conn, f"{P}/odds?storm_id={sid}")
    return out


def view_rows(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


SIMON_010 = {"storm_id": "ep202026", "advisory": "010", "init": "2026-10-09T18:00:00+00:00"}


def bank_official(conn, sid, adv, revision):
    return {r["tau"]: r for r in view_rows(
        conn, "SELECT tau, lat, lon, vmax_kt, mslp_hpa FROM tropical_track_points WHERE storm_id = %s "
              "AND source = 'nhc_official' AND advisory = %s AND revision = %s", (sid, adv, revision))}


# ═══════════════════════════════════════════════════════════════════════════
# R1 — the official forecast is the corrected revision, whole
# ═══════════════════════════════════════════════════════════════════════════

def test_R1_the_constructed_case_is_what_it_says(pg_c):
    r0 = bank_official(pg_c, "ep202026", "010", 0)
    r1 = bank_official(pg_c, "ep202026", "010", 1)
    assert set(r0) - set(r1) == {96} and set(r1) < set(r0)                 # tau 96: revision 0 only
    differ = [t for t in r1 if (r1[t]["lat"], r1[t]["vmax_kt"]) != (r0[t]["lat"], r0[t]["vmax_kt"])]
    assert differ == [24]
    assert (r0[24]["lat"], r0[24]["vmax_kt"]) == (17.5, 125.0) and (r1[24]["lat"], r1[24]["vmax_kt"]) == (17.8, 130.0)


def test_R1_storm_serves_revision_1_only_and_the_revision_0_tau_is_absent(client, monkeypatch, pg_c):
    b = get(client, monkeypatch, pg_c, f"{P}/storm?storm_id=ep202026")
    o = b["official"]
    assert (o["advisory"], o["init_ts"], o["revision"], o["corrected"]) == ("010", SIMON_010["init"], 1, True)
    assert [p["tau_h"] for p in o["points"]] == [3, 12, 24, 36, 48, 60, 72]   # no 96
    r1 = bank_official(pg_c, "ep202026", "010", 1)
    for p in o["points"]:
        assert (p["lat_deg"], p["vmax_kt"]) == (r1[p["tau_h"]]["lat"], r1[p["tau_h"]]["vmax_kt"])
    assert {p["tau_h"]: p["lat_deg"] for p in o["points"]}[24] == 17.8
    assert o["same_init_advisories"] == ["010"]


def test_R1_storms_names_the_corrected_advisory(client, monkeypatch, pg_c):
    b = get(client, monkeypatch, pg_c, f"{P}/storms")
    s = {x["storm_id"]: x for x in b["storms"]}["ep202026"]
    assert s["newest_advisory"]["advisory"] == "010"
    assert (s["newest_advisory"]["corrected"], s["newest_advisory"]["revision"]) == (True, 1)
    off = {p["source"]: p for p in s["positions"]}["nhc_official"]
    assert (off["lat_deg"], off["vmax_kt"]) == (16.2, 90.0)                  # tau 3, unchanged by C1


# ═══════════════════════════════════════════════════════════════════════════
# R2 — two revisions of one advisory never mix
# ═══════════════════════════════════════════════════════════════════════════

def test_R2_tracks_carries_one_revision_of_each_advisory(client, monkeypatch, pg_c):
    b = get(client, monkeypatch, pg_c, f"{P}/tracks?storm_id=ep202026&n=8")
    seen = False
    for c in b["cycles"]:
        for a in c["official"]:
            taus = [p["tau_h"] for p in a["points"]]
            assert len(taus) == len(set(taus)), (c["init_ts"], a["advisory"])
            if a["advisory"] == "010":
                seen = True
                assert c["init_ts"] == SIMON_010["init"] and a["revision"] == 1 and a["corrected"] is True
                assert taus == [3, 12, 24, 36, 48, 60, 72]
                assert {p["tau_h"]: p["lat_deg"] for p in a["points"]}[24] == 17.8
        for s in c["series"]:
            taus = [p["tau_h"] for p in s["points"]]
            assert len(taus) == len(set(taus)), (c["init_ts"], s["source"])
    assert seen


def test_R2_the_bare_table_mixes_and_the_view_never_does(pg_c):
    mixed = """SELECT storm_id, source, init_ts, advisory FROM {t}
                GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT revision) > 1"""
    assert len(view_rows(pg_c, mixed.format(t="tropical_track_points"))) == 1          # C1, as banked
    assert view_rows(pg_c, mixed.format(t="tropical_track_points_in_force")) == []
    mixed_o = """SELECT storm_id, source, issued_ts, advisory FROM {t}
                  GROUP BY 1, 2, 3, 4 HAVING count(DISTINCT revision) > 1"""
    assert len(view_rows(pg_c, mixed_o.format(t="tropical_place_odds"))) == 1          # C2
    assert view_rows(pg_c, mixed_o.format(t="tropical_place_odds_in_force")) == []


# ═══════════════════════════════════════════════════════════════════════════
# R3 — the corrected issuance only, below_1pct as itself
# ═══════════════════════════════════════════════════════════════════════════

def cells(body):
    out = {}
    for p in body["places"]:
        for t in p["thresholds"]:
            for w in t["windows"]:
                for kind in ("cumulative", "onset"):
                    if w[kind] is not None:
                        out[(p["place_id"], t["threshold_kt"], w["window_h"], kind)] = w[kind]
    return out


def test_R3_odds_serves_the_corrected_issuance_only(client, monkeypatch, pg_c, pg):
    b = get(client, monkeypatch, pg_c, f"{P}/odds?storm_id=ep202026")
    live = get(client, monkeypatch, pg, f"{P}/odds?storm_id=ep202026")
    iss = b["issuance"]
    assert (iss["advisory"], iss["issued_ts"], iss["revision"], iss["corrected"]) == (
        "010", "2026-10-09T21:00:00+00:00", 1, True)
    assert "MANZANILLO" in {p["place_id"] for p in live["places"]}
    assert "MANZANILLO" not in {p["place_id"] for p in b["places"]}          # revision 0 only
    r1 = view_rows(pg_c, "SELECT place_id, threshold_kt, window_h, kind, value, below_1pct FROM "
                         "tropical_place_odds WHERE storm_id = 'ep202026' AND advisory = '010' AND revision = 1")
    got, old = cells(b), cells(live)
    assert len(got) == len(r1)
    changed = [k for k in got if got[k] != old[k]]
    assert len(changed) == 1 and got[changed[0]]["value_pct"] == old[changed[0]]["value_pct"] + 1


def test_R3_below_1pct_is_still_carried_as_itself(client, monkeypatch, pg_c):
    b = get(client, monkeypatch, pg_c, f"{P}/odds?storm_id=ep202026")
    r1 = {(r["place_id"], r["threshold_kt"], r["window_h"], r["kind"]): r for r in view_rows(
        pg_c, "SELECT place_id, threshold_kt, window_h, kind, value, below_1pct FROM tropical_place_odds "
              "WHERE storm_id = 'ep202026' AND advisory = '010' AND revision = 1")}
    below = [k for k, r in r1.items() if r["below_1pct"]]
    assert below
    for k, c in cells(b).items():
        assert c["below_1pct"] is r1[k]["below_1pct"]
        if c["below_1pct"]:
            assert r1[k]["value"] == 0 and c["value_pct"] is None


# ═══════════════════════════════════════════════════════════════════════════
# R4 — corrected and revision are true to the ledger
# ═══════════════════════════════════════════════════════════════════════════

def ledger(conn):
    return [json.loads(json.dumps(r["j"])) for r in view_rows(
        conn, "SELECT row_to_json(t) AS j FROM tropical_file_vintage t WHERE status = 'banked'")]


def expected(conn, led, sid, adv, odds=False):
    if odds:
        rev = view_rows(conn, "SELECT coalesce(max(revision), 0) AS r FROM tropical_place_odds "
                              "WHERE storm_id = %s AND advisory = %s", (sid, adv))[0]["r"]
        return lb2.ledger_corrected(led, sid, adv, (lb2.ODDS_FILE,)), rev
    rev = view_rows(conn, "SELECT coalesce(max(revision), 0) AS r FROM tropical_track_points "
                          "WHERE storm_id = %s AND source = 'nhc_official' AND advisory = %s", (sid, adv))[0]["r"]
    return lb2.ledger_corrected(led, sid, adv, lb2.OFFICIAL_FILES), rev


def check_every_object(conn, bodies):
    led, n = ledger(conn), 0
    for name, b in bodies.items():
        sid = b.get("storm_id") or (b.get("storm") or {}).get("storm_id")
        if name == "storms":
            for s in b["storms"]:
                a = s["newest_advisory"]
                assert (a["corrected"], a["revision"]) == expected(conn, led, s["storm_id"], a["advisory"]), (name, s["storm_id"])
                n += 1
        elif name.startswith("odds"):
            a = b["issuance"]
            assert (a["corrected"], a["revision"]) == expected(conn, led, sid, a["advisory"], odds=True), name
            n += 1
        else:
            objs = [b["official"]] if name.startswith("storm ") else [a for c in b["cycles"] for a in c["official"]]
            for a in objs:
                assert (a["corrected"], a["revision"]) == expected(conn, led, sid, a["advisory"]), (name, a["advisory"])
                n += 1
    return n


def test_R4_every_live_advisory_reads_false_at_revision_0_true_to_the_ledger(client, monkeypatch, pg):
    bodies = all_bodies(client, monkeypatch, pg)
    assert check_every_object(pg, bodies) >= 3 + 3 + 3 + 12
    objs = [o for b in bodies.values() for o in lb2.correction_objects(b)]
    assert {(o["corrected"], o["revision"]) for o in objs} == {(False, 0)}
    assert len(ledger(pg)) == 2637 and not [r for r in ledger(pg) if "correction" in r["meta"]]


def test_R4_the_constructed_bank_is_true_to_its_ledger_too(client, monkeypatch, pg_c):
    bodies = all_bodies(client, monkeypatch, pg_c)
    check_every_object(pg_c, bodies)
    flags = {(name, o.get("advisory"), o["corrected"], o["revision"])
             for name, b in bodies.items() for o in lb2.correction_objects(b) if o["corrected"]}
    assert flags == {("storms", "010", True, 1), ("storm ep202026", "010", True, 1),
                     ("tracks ep202026", "010", True, 1), ("odds ep202026", "010", True, 1),
                     ("storms", "013", True, 0), ("storm al092026", "013", True, 0),
                     ("tracks al092026", "013", True, 0), ("odds al092026", "013", True, 0)}


def test_R4_an_anomaly_copy_is_never_a_correction(client, monkeypatch, pg):
    # The d091675 anomaly copies at the cut (ep182026 050 radii, al092026 012A
    # 5-day and radii) are banked beside the originals; they are not corrections.
    led = ledger(pg)
    anomalies = {(r["storm_id"], r["vintage_key"]) for r in led
                 if "anomaly_prior_sha" in r["meta"] and r["product"] in lb2.OFFICIAL_FILES}
    assert anomalies == {("ep182026", "050"), ("al092026", "012A")}
    b = get(client, monkeypatch, pg, f"{P}/tracks?storm_id=al092026&n=8")
    a = {x["advisory"]: x for c in b["cycles"] for x in c["official"]}["012A"]
    assert (a["corrected"], a["revision"]) == (False, 0)


# ═══════════════════════════════════════════════════════════════════════════
# R5 — corrected with identical rows: corrected true, figures unchanged
# ═══════════════════════════════════════════════════════════════════════════

def without_flags(body):
    body = {k: v for k, v in json.loads(json.dumps(body)).items() if k != "cache"}
    lb2.strip_correction(body)
    return body


def test_R5_corrected_with_identical_rows_reads_true_at_revision_0_figures_unchanged(client, monkeypatch, pg, pg_c):
    assert view_rows(pg_c, "SELECT count(*) AS n FROM tropical_track_points WHERE storm_id = 'al092026' "
                           "AND revision > 0")[0]["n"] == 0                  # no row written (C3)
    for route in ("storm", "tracks", "odds"):
        path = f"{P}/{route}?storm_id=al092026" + ("&n=8" if route == "tracks" else "")
        live = get(client, monkeypatch, pg, path)
        corr = get(client, monkeypatch, pg_c, path)
        assert without_flags(corr) == without_flags(live), route
        newest = (corr["official"] if route == "storm" else corr["issuance"] if route == "odds"
                  else corr["cycles"][-1]["official"][-1])
        assert (newest["advisory"], newest["corrected"], newest["revision"]) == ("013", True, 0), route


def test_R5_the_live_case_on_neon_is_recorded():
    m = json.loads((ROOT / "docs/receipts/tropics-api-d091682/neon_measured.json").read_text())
    live = m["live_corrected_with_identical_rows"]
    assert live["official_points"]["revision"] == 0 and "052A" in live["advisory"]
    assert "parses identically" in live["pantry_receipt_ingestion_log_07_10_31Z"]


# ═══════════════════════════════════════════════════════════════════════════
# R6 — d091673's vectors, re-banked with exactly the two new fields
# ═══════════════════════════════════════════════════════════════════════════

OLD = json.loads((lb2.FIX / "vectors_d091673.json").read_text())


@pytest.mark.parametrize("name", sorted(OLD["bodies"]))
def test_R6_the_rebanked_vector_minus_the_two_fields_is_d091673s(name):
    import hashlib
    raw = (lb.FIX / "bodies" / name).read_text()
    body = json.loads(raw)
    objs = lb2.correction_objects(body)
    assert objs and all(set(o) >= {"corrected", "revision"} for o in objs)
    assert raw.count('"corrected":') == raw.count('"revision":') == len(objs)   # nowhere else
    assert lb2.strip_correction(body) == len(objs)
    old = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    assert hashlib.sha256(old).hexdigest() == OLD["bodies"][name]["sha256"]


def test_R6_the_fields_sit_after_the_position_fields():
    b = json.loads((lb.FIX / "bodies" / "storm_ep182026.json").read_text())
    keys = list(b["official"])
    assert keys[keys.index("position_tau_h") + 1:keys.index("position_tau_h") + 4] == ["corrected", "revision", "points"]
    o = json.loads((lb.FIX / "bodies" / "odds_al092026.json").read_text())
    assert list(o["issuance"]) == ["issued_ts", "advisory", "corrected", "revision"]


# ═══════════════════════════════════════════════════════════════════════════
# R7 — no page route names the bare tables
# ═══════════════════════════════════════════════════════════════════════════

BARE = re.compile(r"\btropical_(track_points|place_odds)\b(?!_in_force)")


def test_R7_no_statement_names_a_bare_table():
    stmts = {n: getattr(tr, n) for n in dir(tr) if n.endswith("_SQL")}
    assert len(stmts) == 8
    for name, sql in stmts.items():
        assert not BARE.search(sql), name
    for name in ("STORMS_SQL", "OFFICIAL_NEWEST_SQL", "OBSERVED_SQL", "CYCLES_SQL", "TRACKS_SQL"):
        assert "tropical_track_points_in_force" in stmts[name], name
    assert "tropical_place_odds_in_force" in stmts["ODDS_SQL"]
    for name in ("OFFICIAL_NEWEST_SQL", "TRACKS_SQL", "ODDS_SQL"):
        assert "tropical_file_vintage_in_force" in stmts[name], name


def test_R7_the_routes_in_main_name_no_table():
    src = (ROOT / "main.py").read_text()
    a = src.index("# THE TROPICS PAGE")
    b = src.index("# LOAD OUTLOOK AND CAISO NET DEMAND")
    section = src[a:b]
    assert not BARE.search(section)
    assert "FROM tropical" not in section                               # every statement is tropics.py's


# ═══════════════════════════════════════════════════════════════════════════
# R8 — plan receipts: every read through the views is an index read
# ═══════════════════════════════════════════════════════════════════════════

BIG = ("tropical_track_points", "tropical_place_odds", "tropical_file_vintage")


def statements(sid="ep202026"):
    return [("STORMS_SQL", tr.STORMS_SQL, {"scope": "active", "recent_days": 7}),
            ("OFFICIAL_NEWEST_SQL", tr.OFFICIAL_NEWEST_SQL, {"storm_ids": list(STORMS)}),
            ("OBSERVED_SQL", tr.OBSERVED_SQL, {"storm_id": sid}),
            ("CYCLES_SQL", tr.CYCLES_SQL, {"storm_id": sid}),
            ("TRACKS_SQL", tr.TRACKS_SQL, {"storm_id": sid, "n": 8}),
            ("ODDS_SQL", tr.ODDS_SQL, {"storm_id": sid})]


def nodes(conn, sql, params):
    plan = conn.execute("EXPLAIN (FORMAT JSON) " + sql, params).fetchone()["QUERY PLAN"][0]["Plan"]
    out = []

    def walk(n):
        out.append((n["Node Type"], n.get("Relation Name"), n.get("Index Name")))
        for c in n.get("Plans", []):
            walk(c)
    walk(plan)
    return out


def test_R8_with_a_correction_in_the_bank_every_read_is_an_index_read(pg_c):
    for name, sql, params in statements():
        ns = nodes(pg_c, sql, params)
        assert not [rel for typ, rel, _ in ns if typ == "Seq Scan" and rel in BIG], (name, ns)
        idx = {i for _t, rel, i in ns if rel in BIG}
        assert {"ttp_revised", "tpo_revised"} & idx, (name, idx)        # the anti-join reads 297's index
        if name in ("OFFICIAL_NEWEST_SQL", "TRACKS_SQL", "ODDS_SQL"):
            assert "tfv_identity" in idx, (name, idx)


def test_R8_the_neon_receipt_is_index_reads_within_stop_v():
    r = json.loads((ROOT / "docs/receipts/tropics-api-d091682/neon_plans_297.json").read_text())
    assert set(r["statements"]) == {"STORMS_SQL", "OFFICIAL_NEWEST_SQL", "OBSERVED_SQL", "CYCLES_SQL",
                                    "TRACKS_SQL n=8", "ODDS_SQL"}
    for name, s in r["statements"].items():
        assert s["seq_scan_of_big_table"] is False, name
        assert s["ms"] <= 10 * s["d091673_ms"], name
        assert "Seq Scan" not in s["plan"], name
