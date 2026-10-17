"""d091766 — the AR index: what the AR bank publishes, every probability gated by its scored cell
(D-09-25-245; show_v1, migration 292; D-09-25-75; CLAUDE.md d091551; D-09-05-T; D-08-02-L).

  B   the bank is Neon's, verbatim and sha-checked; 324 ran whole in the cluster (its own
      checks and bites); the cluster's constraints are Neon's
  A1  /index lists every published init (16) newest first, the registry, and the census of
      the 50 cells (0 scored today); not_published and no_init are bodies with reasons
  A2  /landfall over the empty ar_scores withholds every probability and every count, and
      serves ens_mean, ctrl and max_member on every row
  A3  with one CONSTRUCTED ar_scores row (show = true for or_wa x d1-2 x 250), exactly that
      cell's p250/n250 appear and no other; a scored, not-shown cell carries its show_reasons;
      show_v1 as the schema holds it (S1/S2 in the CHECK, S4 its own CHECK, S3 in neither)
  A4  the edges constant's sha is the pin; a changed constant serves no cell
  A4b the derived region equals the published coast_ivt object's for 71 of 71 points; in the
      route a point whose rows disagree is a 500 naming it
  A5  the statement timeout comes first, a memo hit is served without a read, and each
      LATERAL is one (table, key) probe; no DISTINCT ON
  A6  lead 0 reads no_cell with the edges' rule verbatim; 292's name sets equal the edges'
  X1  the Neon receipts are the routes' statements; no Seq Scan except on tables under 10k rows
  R1  the bodies handed back (docs/receipts/ar-index-d091766/bodies/) are what the routes serve

The mutation rehearsal (R) is tests/rehearse_ar_index_d091766.py.
"""

import contextlib
import copy
import gzip
import json
import pathlib
import re
import sys

import pytest
from fastapi.testclient import TestClient

import main
import ar_index as ar
import load_bank_d091766 as lb

ROOT = pathlib.Path(__file__).resolve().parent.parent
P = "/api/weather/ar"
RECEIPTS = ROOT / "docs" / "receipts" / "ar-index-d091766"
CACHES = ("_ar_index_cache", "_ar_landfall_cache")
DISPLAY = "https://display.energylake.io/"
PIN = "5b0068645d1c3514365f50f15a6900c15bdd6758cde6c1580c2e82ccfb6cc0fe"
PIN_SOURCE = ("energylake-pantry scripts/ar_cells.py --edges at af0aeb902b637218f434110722cdf12170618ee5 "
              "(edge files last changed b786fe79)")
NEWEST = "2026-10-17T00:00:00+00:00"
GATED = ("p250", "n250", "p500", "n500")


def _clear():
    for c in CACHES:
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
    with lb.cluster() as conn:
        yield conn


def get(client, monkeypatch, conn, path, status=200):
    _clear()
    pool = lb.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, (path, r.text[:500])
    return r.json(), pool


class _Rollback(Exception):
    pass


@contextlib.contextmanager
def scratch(conn, *statements):
    """Statements applied for the body of the block only: the cluster is rolled back after."""
    try:
        with conn.transaction():
            for q, p in statements:
                conn.execute(q, p)
            yield
            raise _Rollback
    except _Rollback:
        pass


def fixture_score(**over):
    row = {**lb.SCORES_FIXTURE, **over}
    return ("INSERT INTO ar_scores SELECT * FROM json_populate_record(NULL::ar_scores, %s::json)",
            (json.dumps(row),))


def banked_landfall():
    return {(r["point_code"], r["valid_ts"]): r for r in lb.rows("landfall")}


def coast_object():
    return json.loads(lb.obj("coast_ivt_2026101700"))


# ═══════════════════════════════════════════════════════════════════════════
# B — the bank
# ═══════════════════════════════════════════════════════════════════════════

def test_B_the_bank_is_neons_rows_read_only():
    bank = lb.MANIFEST["bank"]
    assert {k: bank[k]["rows"] for k in bank} == {"runs": 16, "display": 33, "landfall": 6603}
    assert all(bank[k]["ro"] == "on" for k in bank)
    for kind in bank:
        assert len(lb.rows(kind)) == bank[kind]["rows"]             # rows() checks Neon's sha first
    assert lb.MANIFEST["neon"]["counts"]["ar_scores"] == 0
    stop_d = lb.MANIFEST["neon"]["stop_d"]
    assert stop_d["ar_display_objects"] == {"coast_ivt": 16, "odds": 16, "registry": 1}


def test_B_the_objects_are_the_buckets_and_their_shas_are_the_ledgers():
    display = {r["display_key"]: r for r in lb.rows("display")}
    for name, o in lb.MANIFEST["objects"].items():
        raw = lb.obj(name)                                          # checks the manifest's sha
        row = display[o["display_key"]]
        assert (o["sha256"], len(raw)) == (row["sha256"], row["bytes"]), name


def test_B_324_ran_whole_in_the_cluster_with_its_own_checks(pg):
    assert [r["version"] for r in pg.execute("SELECT version FROM schema_migrations ORDER BY 1")] == [289, 292, 324]
    n = pg.notices_324
    assert any(m.startswith("PRECONDITION OK") for m in n)
    assert any(m.startswith("POST-CHECK OK") and "10 of 10 bites" in m for m in n)


def test_B_the_clusters_constraints_are_neons(pg):
    """292's sections 1-3 and 324, loaded verbatim, give the four tables Neon's constraints,
    definition for definition (Neon 17.11 deparsed; the cluster 16)."""
    here = {(r["tbl"], r["conname"], r["def"]) for r in pg.execute(
        """SELECT conrelid::regclass::text AS tbl, conname, pg_get_constraintdef(oid) AS def
             FROM pg_constraint WHERE conrelid IN ('ar_runs'::regclass, 'ar_landfall_latest'::regclass,
                                                   'ar_scores'::regclass, 'ar_display_objects'::regclass)""")}
    neon = {(r["tbl"], r["conname"], r["def"]) for r in lb.neon_constraints()}
    assert len(neon) == 44 and here == neon


# ═══════════════════════════════════════════════════════════════════════════
# A1 — /index
# ═══════════════════════════════════════════════════════════════════════════

def test_A1_the_index_lists_every_published_init_and_the_census(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/index")
    coast = {r["init_ts"]: r for r in lb.rows("display") if r["kind"] == "coast_ivt"}
    odds = {r["init_ts"] for r in lb.rows("display") if r["kind"] == "odds"}
    runs = sorted((r["init_ts"] for r in lb.rows("runs")), reverse=True)
    assert [i["init_ts"] for i in b["inits"]] == runs and len(runs) == 16
    assert b["newest_init"] == NEWEST == runs[0] and b["inits_published"] == 16
    for i in b["inits"]:
        row = coast[i["init_ts"]]
        assert i["coast_ivt"] == {"url": DISPLAY + row["display_key"], "sha256": row["sha256"],
                                  "bytes": row["bytes"], "published_at": row["published_at"],
                                  "absence": None}
        assert i["odds_published"] is (i["init_ts"] in odds) is True
        assert (i["status"], i["members_present"], i["steps_with_odds"]) == ("complete", 31, 93)
    reg = next(r for r in lb.rows("display") if r["kind"] == "registry")
    assert b["registry"]["url"] == DISPLAY + reg["display_key"] and b["registry"]["sha256"] == reg["sha256"]
    s = b["scoring"]
    assert (s["rule"], s["cells_scored"], s["cells_shown"], s["cells_total"]) == ("show_v1", 0, 0, 50)
    assert [(c["region"], c["lead_bucket"], c["threshold"]) for c in s["cells"]] == ar.universe()
    assert all(c["withheld"] == {"reason": "not_yet_scored"} and c["show"] is None for c in s["cells"])
    assert b["edges_sha256"] == PIN and b["edges"]["pinned"] is True
    assert b["bias_note"] == coast_object()["bias"] and b["attribution"] == coast_object()["attribution"]
    # the odds object is a bank artifact: no url of it is served
    assert "odds.sub7_v1.json" not in json.dumps(b) and b["absence"] is None


def test_A1_a_banked_cycle_with_no_object_is_not_published(client, monkeypatch, pg):
    with scratch(pg, ("DELETE FROM ar_display_objects WHERE kind IN ('coast_ivt', 'odds') AND init_ts = %s",
                      ("2026-10-16T12:00:00Z",))):
        b, _ = get(client, monkeypatch, pg, f"{P}/index")
    i = next(i for i in b["inits"] if i["init_ts"] == "2026-10-16T12:00:00+00:00")
    assert i["coast_ivt"]["url"] is None and i["coast_ivt"]["absence"]["reason"] == "not_published"
    assert i["odds_published"] is False
    assert len(b["inits"]) == 16 and b["inits_published"] == 15


def test_A1_no_cycle_is_a_body_with_its_reason_on_both_routes(client, monkeypatch, pg):
    with scratch(pg, ("DELETE FROM ar_display_objects", None), ("DELETE FROM ar_landfall_latest", None),
                 ("DELETE FROM ar_runs", None)):
        b, _ = get(client, monkeypatch, pg, f"{P}/index")
        l, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    assert b["inits"] == [] and b["newest_init"] is None and b["absence"]["reason"] == "no_init"
    assert b["registry"]["absence"]["reason"] == "not_published"
    assert l["rows"] == [] and l["absence"]["reason"] == "no_init" and l["init_ts"] is None


# ═══════════════════════════════════════════════════════════════════════════
# A2 — every probability and every count withheld while ar_scores is empty
# ═══════════════════════════════════════════════════════════════════════════

def test_A2_landfall_withholds_every_probability_and_count_and_serves_the_ivt(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    bank = banked_landfall()
    assert b["init_ts"] == NEWEST and len(b["rows"]) == 6603 == len(bank)
    assert sum(1 for r in bank.values() if r["n250"] > 0) == 3357            # the counts are there ...
    assert all(r[k] is not None for r in bank.values() for k in ("n250", "n500", "n_present"))
    for row in b["rows"]:
        assert not set(GATED) & set(row), row                                # ... and none is served
        assert set(row["withheld"]) == {"250", "500"}
        banked = bank[(row["point_code"], row["valid_ts"])]
        for k in ("ens_mean", "ctrl", "max_member", "n_present", "absent_members"):
            assert row[k] == banked[k], k
        want = "no_cell" if row["lead_h"] == 0 else "not_yet_scored"
        assert {w["reason"] for w in row["withheld"].values()} == {want}
    assert b["counts"] == {"rows": 6603, "points": 71, "served": {"250": 0, "500": 0},
                           "withheld_by_reason": {"no_cell": 142, "not_yet_scored": 13064}}
    text = json.dumps(b["rows"])
    assert not re.search(r'"(p|n)(250|500)"', text)


# ═══════════════════════════════════════════════════════════════════════════
# A3 — one shown cell: exactly its p250/n250
# ═══════════════════════════════════════════════════════════════════════════

def test_A3_one_shown_cell_serves_exactly_its_p250_and_n250(client, monkeypatch, pg):
    with scratch(pg, fixture_score()):
        b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
        idx, _ = get(client, monkeypatch, pg, f"{P}/index")
    bank = banked_landfall()
    shown = [r for r in b["rows"] if "p250" in r or "n250" in r]
    assert {(r["region"], r["lead_bucket"]) for r in shown} == {("or_wa", "d1-2")}
    want = [r for r in b["rows"] if r["region"] == "or_wa" and r["lead_bucket"] == "d1-2"]
    assert shown == want and len(shown) == 14 * 16                          # 14 points x leads 3..48
    for r in shown:
        banked = bank[(r["point_code"], r["valid_ts"])]
        assert r["n250"] == banked["n250"] and r["p250"] == round(banked["n250"] / banked["n_present"], 4)
        assert r["withheld"] == {"500": {"reason": "not_yet_scored"}}
    assert not any("p500" in r or "n500" in r for r in b["rows"])
    assert b["counts"]["served"] == {"250": 224, "500": 0}
    assert any(r["n250"] > 0 for r in shown)
    s = idx["scoring"]
    assert (s["cells_scored"], s["cells_shown"]) == (1, 1)
    cell = next(c for c in s["cells"] if (c["region"], c["lead_bucket"], c["threshold"]) == ("or_wa", "d1-2", 250))
    assert (cell["show"], cell["show_reasons"], cell["withheld"]) == (True, [], None)
    assert s["scorer_hash"] == "fixture_d091766"


def test_A3_a_scored_cell_not_shown_carries_the_scorers_show_reasons(client, monkeypatch, pg):
    reasons = ["S3: reliability bin 0.6-0.7 outside tolerance (fixture)"]
    with scratch(pg, fixture_score(show=False, show_reasons=reasons, bss_lo90=-0.01)):
        b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    cell = [r for r in b["rows"] if r["region"] == "or_wa" and r["lead_bucket"] == "d1-2"]
    assert len(cell) == 224
    assert all(r["withheld"]["250"] == {"reason": "not_shown", "show_reasons": reasons} for r in cell)
    assert not any(set(GATED) & set(r) for r in b["rows"])


def test_A3_show_v1_as_the_schema_holds_it(pg):
    """S1 and S2 are terms of ar_scores_show_rule_v1; S4 is its own CHECK; S3 (calibration)
    is in no CHECK, and reaches Neon only through show_reasons."""
    import psycopg
    defs = {r["conname"]: r["def"] for r in lb.neon_constraints() if r["tbl"] == "ar_scores"}
    v1 = defs["ar_scores_show_rule_v1"]
    for term in ("n_eff >= (60)::numeric", "event_days_eff >= (14)::numeric",          # S1
                 "bss_lo90 > (0)::numeric",                                             # S2
                 "cardinality(show_reasons) = 0", "show_rule = 'show_v1'::text"):
        assert term in v1, term
    assert "truth_level_set" not in v1 and defs["ar_scores_like_for_like"] == "CHECK ((truth_level_set = level_set))"
    calibration = ("wilson_lo", "wilson_hi", "obs_freq", "mean_fcst", "r1", "brier")
    assert not any(c in d for d in defs.values() for c in calibration)
    bites = [dict(event_days_eff=13), dict(n_eff=59), dict(bss_lo90=0), dict(show_reasons=["S3: x"]),
             dict(truth_level_set="full17_v1")]
    for over in bites:
        with pytest.raises(psycopg.errors.CheckViolation):
            with scratch(pg, fixture_score(**over)):
                pass
    # an uncalibrated row the scorer did not flag lands: the CHECK does not enforce S3
    with scratch(pg, fixture_score(wilson_lo=0.9, wilson_hi=0.95, obs_freq=0.1, mean_fcst=0.9)):
        assert pg.execute("SELECT count(*) AS n FROM ar_scores").fetchone()["n"] == 1


def test_A3_the_route_recomputes_no_s(client, monkeypatch, pg):
    """show = true is served as written even where the row's own numbers sit at the CHECK's floor."""
    with scratch(pg, fixture_score(n_eff=60, event_days_eff=14, bss_lo90=0.0001)):
        b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    assert b["counts"]["served"]["250"] == 224
    # the S terms are never read: not selected, not looked up
    assert not re.search(r"n_eff|event_days_eff|bss_lo90|wilson|obs_freq", ar.SCORES_SQL)
    src = (ROOT / "ar_index.py").read_text()
    assert not re.search(r"""\[["'](n_eff|event_days_eff|bss_lo90)["']\]|get\(["'](n_eff|event_days_eff|bss_lo90)""", src)


# ═══════════════════════════════════════════════════════════════════════════
# A4 — the edges pin
# ═══════════════════════════════════════════════════════════════════════════

def test_A4_the_edges_constants_sha_is_the_pin():
    assert ar.edges_sha256() == PIN == ar.EDGES_PIN["edges_sha256"]
    assert ar.EDGES_PIN["source"] == PIN_SOURCE
    assert ar.edges_pinned() is True
    assert len(json.dumps(ar.EDGES, sort_keys=True, separators=(",", ":"))) == 1204     # §0.5's canonical bytes


def test_A4_a_changed_constant_serves_no_cell(client, monkeypatch, pg):
    changed = copy.deepcopy(ar.EDGES)
    changed["lead_buckets"][0]["le_h"] = 54
    monkeypatch.setattr(ar, "EDGES", changed)
    assert ar.edges_pinned() is False
    with scratch(pg, fixture_score()):
        b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
        idx, _ = get(client, monkeypatch, pg, f"{P}/index")
    assert not any(set(GATED) & set(r) for r in b["rows"])
    assert {w["reason"] for r in b["rows"] for w in r["withheld"].values()} == {"edges_pin_mismatch"}
    assert idx["edges"]["pinned"] is False and idx["edges_sha256"] != PIN
    assert idx["scoring"]["cells_shown"] == 0
    assert all(c["withheld"] == {"reason": "edges_pin_mismatch"} for c in idx["scoring"]["cells"])


# ═══════════════════════════════════════════════════════════════════════════
# A4b — the derived region is the published object's, 71 of 71
# ═══════════════════════════════════════════════════════════════════════════

def test_A4b_the_derived_region_equals_the_coast_ivt_objects_for_every_point(client, monkeypatch, pg):
    o = coast_object()
    assert o["init"] == "2026-10-17T00Z" and o["level_set"] == ar.LEVEL_SET
    assert len(o["points"]) == 71
    derived = {p["point_code"]: ar.region_of(p["lat"]) for p in o["points"]}
    published = {p["point_code"]: p["region"] for p in o["points"]}
    assert derived == published                                              # 71 of 71
    b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    served = {}
    for r in b["rows"]:
        assert served.setdefault(r["point_code"], (r["lat"], r["lon"], r["region"])) == (r["lat"], r["lon"], r["region"])
    assert {pc: v[2] for pc, v in served.items()} == published
    assert {pc: (v[0], v[1]) for pc, v in served.items()} == {p["point_code"]: (p["lat"], p["lon"]) for p in o["points"]}
    # the band edges, on both sides: the half-degree between bands sits in the band below
    edges = {pc: published[pc] for pc in ("coast_34.5N", "coast_35.0N", "coast_42.5N", "coast_43.0N",
                                          "coast_49.5N", "coast_50.0N", "coast_55.5N", "coast_56.0N")}
    assert edges == {"coast_34.5N": "baja_socal", "coast_35.0N": "central_norcal",
                     "coast_42.5N": "central_norcal", "coast_43.0N": "or_wa", "coast_49.5N": "or_wa",
                     "coast_50.0N": "bc", "coast_55.5N": "bc", "coast_56.0N": "se_alaska"}


def test_A4b_a_point_whose_rows_disagree_is_a_500_naming_it(client, monkeypatch, pg):
    with scratch(pg, ("UPDATE ar_landfall_latest SET lat = 47.5 WHERE point_code = 'coast_47.0N' AND lead_h = 3",
                      None)):
        r = TestClient(main.app)
        _clear()
        monkeypatch.setattr(main, "_pool", lb.PgPool(pg))
        resp = r.get(f"{P}/landfall")
    assert resp.status_code == 500 and "coast_47.0N" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# A5 — the timeout first, the memo, one (table, key) per LATERAL
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("path", [f"{P}/index", f"{P}/landfall"])
def test_A5_the_timeout_comes_first_and_a_memo_hit_reads_nothing(client, monkeypatch, pg, path):
    b, pool = get(client, monkeypatch, pg, path)
    assert pool.calls[0] == (f"SET LOCAL statement_timeout = '{main.AR_STATEMENT_TIMEOUT}'", None)
    assert main.AR_STATEMENT_TIMEOUT == "5s" and len(pool.calls) >= 4
    assert all(p["level_set"] == "sub7_v1" for _, p in pool.calls[1:])           # one level set per read
    assert b["cache"]["state"] == "miss"
    n = len(pool.calls)
    r = client.get(path)                                                         # no _clear(): the memo
    assert r.headers["X-Cache"] == "hit" and r.json()["cache"]["state"] == "fresh"
    assert len(pool.calls) == n


def _sql_names():
    return [n for n in dir(ar) if n.endswith("_SQL") and isinstance(getattr(ar, n), str)]


def test_A5_each_lateral_is_one_exact_primary_key_probe_and_no_distinct_on():
    assert sorted(_sql_names()) == ["INDEX_SQL", "LANDFALL_INIT_SQL", "LANDFALL_SQL", "REGISTRY_SQL", "SCORES_SQL"]
    for n in _sql_names():
        assert "DISTINCT" not in getattr(ar, n).upper(), n
    sql = " ".join(ar.INDEX_SQL.split())
    laterals = sql.split("LEFT JOIN LATERAL (")[1:]
    assert len(laterals) == 2
    for kind, lat in zip(("coast_ivt", "odds"), laterals):
        body = lat.split(") AS ")[0]
        assert f"d.level_set = r.level_set AND d.kind = '{kind}' AND d.display_key = 'ar/gefs/'" in body
        assert body.endswith("LIMIT 1")
    s = " ".join(ar.SCORES_SQL.split())
    assert "ORDER BY a.scored_at DESC LIMIT 1) AS n JOIN LATERAL (" in s


def test_A5_on_the_cluster_each_lateral_index_cond_names_the_whole_key(pg):
    """The key is sargable: with sequential scans priced out (the cluster's 33 rows fit one
    page), each LATERAL probes ar_display_objects_pkey on (level_set, kind, display_key)."""
    with scratch(pg, ("SET LOCAL enable_seqscan = off", None)):
        plan = "\n".join(r["QUERY PLAN"] for r in pg.execute(
            "EXPLAIN (ANALYZE, COSTS OFF) " + ar.INDEX_SQL, ar.params()))
    conds = re.findall(r"Index (?:Only )?Scan using ar_display_objects_pkey on ar_display_objects d(?:_1)?.*\n"
                       r"\s+Index Cond: \(\(level_set = r.level_set\) AND \(kind = '(\w+)'::text\) AND "
                       r"\(display_key = ", plan)
    assert sorted(conds) == ["coast_ivt", "odds"], plan
    assert "Join Filter" not in plan


# ═══════════════════════════════════════════════════════════════════════════
# A6 — no_cell, and the names two ways
# ═══════════════════════════════════════════════════════════════════════════

def test_A6_lead_0_reads_no_cell_with_the_rule_verbatim(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    lead0 = [r for r in b["rows"] if r["lead_h"] == 0]
    assert len(lead0) == 71 and {r["valid_ts"] for r in lead0} == {NEWEST}
    rule = ("lead_h = valid_ts - init_ts in hours; bucket where gt_h < lead_h <= le_h; "
            "lead 0 is in no bucket and never shows")
    assert ar.EDGES["lead_rule"] == rule
    for r in lead0:
        assert r["lead_bucket"] is None
        assert r["withheld"] == {"250": {"reason": "no_cell", "rule": rule}, "500": {"reason": "no_cell", "rule": rule}}
        assert r["ens_mean"] is not None and r["max_member"] is not None and "ctrl" in r
    with scratch(pg, fixture_score(region="baja_socal")):                     # a shown cell does not reach lead 0
        b2, _ = get(client, monkeypatch, pg, f"{P}/landfall")
    assert all("p250" not in r for r in b2["rows"] if r["lead_h"] == 0)
    assert b2["counts"]["served"]["250"] == 20 * 16
    assert ar.bucket_of(0) is None and ar.bucket_of(48) == "d1-2" and ar.bucket_of(51) == "d3-5"
    assert ar.bucket_of(384) == "d13-16" and ar.bucket_of(385) is None


def _check_set(text: str, column: str) -> list[str]:
    m = re.search(column + r"\s+\w+\s+NOT NULL CHECK \(" + column + r" IN \(([^)]*)\)\)", text)
    return [v.strip().strip("'") for v in m.group(1).split(",")]


def test_A6_292s_name_sets_equal_the_edges(pg):
    ddl = lb.ddl("292_ar_ivt_bank_shadow.sql")
    assert _check_set(ddl, "region") == list(ar.REGION_NAMES) == [r["name"] for r in ar.EDGES["regions"]]
    assert _check_set(ddl, "lead_bucket") == list(ar.BUCKET_NAMES) == [b["name"] for b in ar.EDGES["lead_buckets"]]
    assert [int(t) for t in _check_set(ddl, "threshold")] == ar.EDGES["thresholds"]
    assert _check_set(ddl, "k_bin") == [k["name"] for k in ar.EDGES["k_bins"]] + ["all"]
    neon = {r["conname"]: r["def"] for r in lb.neon_constraints()}
    for con, names in (("ar_scores_region_check", ar.REGION_NAMES),
                       ("ar_scores_lead_bucket_check", ar.BUCKET_NAMES)):
        assert re.findall(r"'([^']+)'::text", neon[con]) == list(names), con


# ═══════════════════════════════════════════════════════════════════════════
# X1 — the Neon receipts
# ═══════════════════════════════════════════════════════════════════════════

def plans():
    text = (RECEIPTS / "neon_plans.txt").read_text()
    head, *blocks = text.split("\n## ")
    sizes = {t: int(n) for t, n in re.findall(r"(ar_\w+) (\d+)", head.split("Table sizes")[1].split("\n")[0])}
    out = {}
    for b in blocks:
        title, _, rest = b.partition("\n")
        sql, _, plan = rest.partition("\n\n")
        out[title.split("   (")[0].strip()] = (sql.strip(), plan)
    return sizes, out


def test_X1_the_receipts_are_the_routes_statements():
    sys.path.insert(0, str(RECEIPTS))
    import explains
    stated = {s["name"]: s["sql"] for s in json.loads((RECEIPTS / "explains.json").read_text())}
    assert stated == dict(explains.STATEMENTS)
    _, p = plans()
    assert sorted(p) == sorted(stated) == sorted(_sql_names())
    assert {n: sql for n, (sql, _) in p.items()} == stated


def test_X1_no_seq_scan_except_on_tables_under_10k_rows():
    sizes, p = plans()
    assert sizes == {"ar_runs": 16, "ar_display_objects": 33, "ar_landfall_latest": 6603, "ar_scores": 0}
    for name, (_, plan) in p.items():
        for table in re.findall(r"Seq Scan on (\w+)", plan):
            assert sizes[table] < 10_000, (name, table)
        assert float(re.search(r"Execution Time: ([0-9.]+) ms", plan).group(1)) < 10.0, name
    index = p["INDEX_SQL"][1]
    probes = re.findall(r"Index (?:Only )?Scan using ar_display_objects_pkey on ar_display_objects d(?:_1)? .*"
                        r"rows=1 loops=16\)\n\s+Index Cond: \(\(level_set = r.level_set\) AND "
                        r"\(kind = '(\w+)'::text\) AND \(display_key = ", index)
    assert sorted(probes) == ["coast_ivt", "odds"] and "Join Filter" not in index
    assert "Index Scan using ar_landfall_latest_pkey" in p["LANDFALL_INIT_SQL"][1]
    assert "Index Cond: ((scored_at = a.scored_at) AND (level_set = 'sub7_v1'::text) AND (k_bin = 'all'::text))" \
        in p["SCORES_SQL"][1]


# ═══════════════════════════════════════════════════════════════════════════
# R1 — the bodies handed back (d091767's vectors)
# ═══════════════════════════════════════════════════════════════════════════

BODIES = json.loads((RECEIPTS / "bodies" / "manifest.json").read_text())


def _body(name):
    raw = (RECEIPTS / "bodies" / name).read_bytes()
    return json.loads(gzip.decompress(raw) if name.endswith(".gz") else raw)


@pytest.mark.parametrize("name", sorted(BODIES["bodies"]))
def test_R1_the_handed_back_body_is_what_the_route_serves(client, monkeypatch, pg, name):
    import hashlib
    spec = BODIES["bodies"][name]
    raw = (RECEIPTS / "bodies" / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == spec["sha256"]
    stmts = [fixture_score()] if spec.get("scores_fixture") else []
    with scratch(pg, *stmts):
        b, _ = get(client, monkeypatch, pg, spec["path"])
    b.pop("cache")
    assert b == _body(name)
