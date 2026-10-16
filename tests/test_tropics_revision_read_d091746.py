"""d091746 — the Tropics index reads tropical_display_in_force by advisory, on banked vectors
(D-09-25-231; D-09-25-226 §2/§5: readers use the copy in force, the highest revision).

  V1  DISPLAY_FILES_SQL names tropical_display_in_force and advisory_or_cycle;
      FILES_SQL and MEMBERS_SQL read no display row; a sweep fails any display
      read through 294's tropical_file_vintage_in_force (statically, and over
      every statement the routes send)
  V2  the banked fixture: every display row is Neon's, verbatim (the read stamp and
      the row count are in manifest.json "d091746"), and is held in the test
      cluster to the CHECKs Neon holds it to (pantry 314, then 316 verbatim)
  I2  every one of the bank's twelve real corrections is served as its r1, with
      revision 1, corrected, and the original stated (the d091741 module holds
      the 054/016 vectors by both routes, named advisory and newest default)
  T2  /text on Simon 033's banked rows: sections are the publisher's section_kinds
      in the bank's order, title, n_forecast_rows 9/0, forecaster Beven, both
      times; no `sections` key is read
  I3  WSP by cycle: Simon 033's cycle is 2026101512 (its synoptic time) and its
      six objects are found at storm_id '' with the banked shas
  X1  the Neon receipts of the new reads: the view's scan named, with its buffers
      and the rows its substring filter drops
  R1  the bodies handed back (docs/receipts/tropics-api-revision-read-d091746/
      bodies/) are what the routes serve over the bank

The rehearsal (R) is tests/rehearse_tropics_revision_read_d091746.py.
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
FIX = lb.FIX
P = "/api/weather/tropics"
RECEIPTS = ROOT / "docs" / "receipts" / "tropics-api-revision-read-d091746"
CACHES = ("_tropics_products_cache", "_tropics_text_cache", "_tropics_members_cache")
DISPLAY = "https://display.energylake.io/"

# The twelve corrections the bank holds (Neon, read 2026-10-15 15:35Z by the brief and
# 22:00Z here): (storm, advisory, product). NHC re-posted ten with the same bytes; Isaias
# 016's TCD and TCP changed (same size, new sha).
CORRECTIONS = [("al092026", "016", "tcd"), ("al092026", "016", "tcp"),
               ("ep182026", "052A", "cone"), ("ep182026", "052A", "ww"),
               ("ep182026", "053A", "cone"), ("ep182026", "053A", "ww"),
               ("ep182026", "054", "cone"), ("ep182026", "054", "ww"),
               ("ep182026", "056", "cone"),
               ("ep182026", "057", "cone"), ("ep182026", "057", "ww"),
               ("ep212026", "001", "cone")]
BYTES_CHANGED = {("al092026", "016", "tcd"), ("al092026", "016", "tcp")}


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
    with lb.cluster(padded=True) as conn:
        yield conn


def get(client, monkeypatch, conn, path, status=200):
    _clear()
    pool = lb.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, (path, r.text[:500])
    return r.json(), pool


def display(product, storm_id, vkey):
    rows = [r for r in lb.display_rows() if (r["product"], r["storm_id"], r["vintage_key"])
            == (product, storm_id, vkey)]
    assert len(rows) == 1, (product, storm_id, vkey)
    return rows[0]


def by_product(items):
    return {o["product"]: o for o in items}


# ═══════════════════════════════════════════════════════════════════════════
# V1 — the display read is 314's reader; 294's view reads no display row
# ═══════════════════════════════════════════════════════════════════════════

def _sql_names():
    return [n for n in dir(ti) if n.endswith("_SQL") and isinstance(getattr(ti, n), str)]


def test_V1_display_files_sql_names_the_view_and_advisory_or_cycle():
    sql = " ".join(ti.DISPLAY_FILES_SQL.split())
    assert "FROM tropical_display_in_force AS v" in sql
    assert "v.advisory_or_cycle = %(advisory_or_cycle)s" in sql
    assert "v.revision > 0 AS corrected" in sql
    assert "a.vintage_key = v.advisory_or_cycle" in sql                 # the original: the revision-0 row
    assert "tropical_file_vintage_in_force" not in sql
    m = " ".join(ti.DISPLAY_MEMBERS_SQL.split())
    assert "FROM tropical_display_in_force AS v" in m and "v.advisory_or_cycle = %(cycle)s" in m
    p = ti.display_files_params("ep182026", "054", ti.ADVISORY_PRODUCTS)
    assert p == {"products": ["display:cone", "display:ww", "display:surge_ww"], "storm_id": "ep182026",
                 "advisory_or_cycle": "054"}
    assert ti.display_files_params("", "2026101512", ti.WSP_PRODUCTS)["storm_id"] == ""


def test_V1_files_sql_and_members_sql_read_no_display_row():
    for name in ("FILES_SQL", "MEMBERS_SQL"):
        sql = getattr(ti, name)
        assert "display" not in sql and "%(sources)s" not in sql, name
    assert "v.source = 'nhc'" in ti.FILES_SQL and "v.source = 'parsed'" in ti.MEMBERS_SQL
    for params in (ti.files_params("ep182026", "054", ti.ADVISORY_PRODUCTS),
                   ti.files_params("", "2026101512", ti.WSP_PRODUCTS),
                   ti.files_params("al092026", "016", ti.TEXT_PRODUCTS),
                   ti.members_params("ep202026", None)):
        assert "display" not in json.dumps(params), params


def test_V1_sweep_no_statement_reads_display_through_294s_view():
    """Statically: every statement on 294's view names a non-display source as a
    literal, and no statement names the source by parameter."""
    on_294 = [n for n in _sql_names() if "tropical_file_vintage_in_force" in getattr(ti, n)]
    assert sorted(on_294) == ["ENTRY_NEWEST_SQL", "ENTRY_SQL", "FILES_SQL", "MEMBERS_SQL"]
    for n in on_294:
        sql = getattr(ti, n)
        assert re.search(r"\bsource = '(nhc|parsed)'", sql), n
        assert ti.DISPLAY_SOURCE not in re.findall(r"'([a-z_]+)'", sql), n
    for n in _sql_names():
        assert "%(sources)s" not in getattr(ti, n) and "%(source)s" not in getattr(ti, n), n


@pytest.mark.parametrize("path", [f"{P}/products?storm_id=ep182026&advisory=054",
                                  f"{P}/text?storm_id=al092026&advisory=016",
                                  f"{P}/products?storm_id=ep202026",
                                  f"{P}/members?storm_id=ep202026"])
def test_V1_sweep_every_statement_the_routes_send(client, monkeypatch, pg, path):
    """Over the wire: a statement on 294's view carries no display product or
    source; every display product is asked of tropical_display_in_force."""
    _, pool = get(client, monkeypatch, pg, path)
    display_reads = 0
    for q, params in pool.calls[1:]:
        flat = json.dumps(params, default=str)
        if "tropical_file_vintage_in_force" in q:
            assert "display" not in flat and "'display'" not in q, (q[:80], flat)
        if "display:" in flat:
            assert "FROM tropical_display_in_force" in q, q[:80]
            display_reads += 1
    assert display_reads >= 1


# ═══════════════════════════════════════════════════════════════════════════
# V2 — the banked fixture
# ═══════════════════════════════════════════════════════════════════════════

def test_V2_the_fixture_carries_the_neon_read_stamp_and_count():
    d = lb.MANIFEST["d091746"]
    disp = d["bank"]["display"]
    assert disp["read_at"] == "2026-10-15T22:00:14.622Z" and disp["rows"] == 61
    assert len(lb.display_rows()) == 61                       # rows() checks Neon's sha first
    assert d["count"]["ro"] == "on" and d["count"]["read_at"] == disp["read_at"]
    assert (d["count"]["n_display"], d["count"]["n_display_revisions"]) == ("710", "20")
    for kind in ("ledger_033", "official_033", "dataset_two", "migrations"):
        assert len(lb.rows(kind)) == d["bank"][kind]["rows"], kind
    # every identity read, and nothing else: the rows are the identities' every revision
    ids = {tuple(i) for i in d["identities"]}
    held = {(r["product"], r["storm_id"], re.sub(r"r[1-9][0-9]*$", "", r["vintage_key"]))
            for r in lb.display_rows()}
    assert held <= ids
    assert ids - held == {("display:surge_ww", "ep202026", "033"), ("display:ww", "ep202026", "033")}


def test_V2_the_banked_corrections_are_the_twelve():
    r1 = sorted((r["storm_id"], r["vintage_key"][:-2], r["product"].split(":")[1])
                for r in lb.display_rows() if r["vintage_key"].endswith("r1"))
    assert r1 == sorted(CORRECTIONS)
    for sid, adv, name in CORRECTIONS:
        r0, r1 = display(f"display:{name}", sid, adv), display(f"display:{name}", sid, adv + "r1")
        assert ((r0["sha256"] == r1["sha256"]) == ((sid, adv, name) not in BYTES_CHANGED)), (sid, adv, name)
        assert r0["byte_size"] == r1["byte_size"]
        assert r1["meta"]["revision"] == 1 and r1["meta"]["correction"]["revision"] == 1
        assert r1["meta"]["correction"]["source_correction"]["prior_sha256"] == [r0["meta"]["source_sha256"]]


def test_V2_316_is_pantrys_verbatim_and_its_checks_ran(pg):
    assert hashlib.sha256((FIX / "ddl_316.sql").read_bytes()).hexdigest() == lb.DDL_316_PANTRY_SHA256
    assert [r["version"] for r in pg.execute("SELECT version FROM schema_migrations ORDER BY 1")] == [314, 315, 316]
    view = pg.execute("SELECT md5(pg_get_viewdef('tropical_display_in_force'::regclass)) AS m").fetchone()["m"]
    assert view == lb.MANIFEST["d091746"]["count"]["view_md5"] == "977c97d66d70ff8a8ed7e6992545f6c7"   # Neon's


def test_V2_every_display_row_passes_314s_checks_in_the_cluster(pg):
    cons = {r["conname"]: (r["def"], r["convalidated"]) for r in pg.execute(
        """SELECT conname, pg_get_constraintdef(oid) AS def, convalidated FROM pg_constraint
            WHERE conrelid = 'tropical_file_vintage'::regclass AND conname LIKE 'tfv_display%%'""")}
    assert sorted(cons) == ["tfv_display_is_banked", "tfv_display_is_display_product", "tfv_display_vintage_key"]
    assert all(v for _, v in cons.values())
    assert "[0-9]{12}" in cons["tfv_display_vintage_key"][0]                 # 316's pattern
    ids = [r["vintage_id"] for r in lb.display_rows()]
    n = pg.execute("""SELECT count(*) AS n FROM tropical_file_vintage WHERE vintage_id = ANY(%s)
                       AND source = 'display' AND status = 'banked' AND product LIKE 'display:%%'
                       AND vintage_key ~ '^([0-9]{3}[A-Z]?|[0-9]{10}|[0-9]{12})(r[1-9][0-9]*)?$'""",
                   (ids,)).fetchone()["n"]
    assert n == len(ids) == 61
    # tfv_display_immutable: a second object under a published key is refused
    import psycopg
    r = display("display:cone", "ep182026", "054r1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pg.transaction():
            pg.execute("""INSERT INTO tropical_file_vintage (dataset, source, product, storm_id, vintage_key,
                              status, r2_key, byte_size, sha256) VALUES (%s, 'display', %s, %s, %s, 'banked',
                              %s, 1, %s)""", (r["dataset"], r["product"], r["storm_id"], r["vintage_key"],
                                              r["r2_key"], "0" * 64))


# ═══════════════════════════════════════════════════════════════════════════
# I2 — every real correction is served as its r1
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sid,adv,name", CORRECTIONS, ids=[f"{s}-{a}-{n}" for s, a, n in CORRECTIONS])
def test_I2_every_banked_correction_is_served_as_its_r1(client, monkeypatch, pg, sid, adv, name):
    route = "text" if name in ("tcd", "tcp") else "products"
    b, _ = get(client, monkeypatch, pg, f"{P}/{route}?storm_id={sid}&advisory={adv}")
    o = b["products"][name] if route == "text" else by_product(b["objects"])[name]
    r0, r1 = display(f"display:{name}", sid, adv), display(f"display:{name}", sid, adv + "r1")
    assert (o["revision"], o["corrected"]) == (1, True)
    assert (o["url"], o["sha256"], o["bytes"]) == (DISPLAY + r1["r2_key"], r1["sha256"], r1["byte_size"])
    # the original is stated even when NHC re-posted the same bytes
    assert o["original"] == {"url": DISPLAY + r0["r2_key"], "sha256": r0["sha256"],
                             "bytes": r0["byte_size"], "absence": None}
    assert (o["original"]["sha256"] == o["sha256"]) == ((sid, adv, name) not in BYTES_CHANGED)
    assert b["revision"] == 1 and b["corrected"] is True


def test_I2_an_uncorrected_object_states_no_original(client, monkeypatch, pg):
    b, _ = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep182026&advisory=058")
    cone = by_product(b["objects"])["cone"]
    assert (cone["revision"], cone["corrected"], cone["original"]) == (0, False, None)
    assert (b["revision"], b["corrected"]) == (0, False)


# ═══════════════════════════════════════════════════════════════════════════
# T2 — /text reads the bank's section_kinds, title and n_forecast_rows
# ═══════════════════════════════════════════════════════════════════════════

TCD_KINDS = ["discussion", "key_messages", "forecast_table"]
TCP_KINDS = ["summary", "watches_and_warnings", "discussion_and_outlook", "hazards_affecting_land",
             "next_advisory"]


@pytest.mark.parametrize("path", [f"{P}/text?storm_id=ep202026&advisory=033", f"{P}/text?storm_id=ep202026"])
def test_T2_simon_033_text_is_the_banked_rows(client, monkeypatch, pg, path):
    b, _ = get(client, monkeypatch, pg, path)
    assert b["advisory"] == "033"                                   # also the newest default
    for product, kinds, rows in (("tcd", TCD_KINDS, 9), ("tcp", TCP_KINDS, 0)):
        o, row = b["products"][product], display(f"display:{product}", "ep202026", "033")
        meta = row["meta"]
        assert meta["section_kinds"] == kinds                       # the bank's order
        assert o["sections"] == [{"kind": k} for k in kinds]
        assert (o["title"], o["n_forecast_rows"], o["forecaster"]) == (meta["title"], rows, "Beven")
        assert (o["issued_utc"], o["transmitted_utc"]) == (meta["issued_utc"], meta["transmitted_utc"])
        assert o["issued_utc"] == "2026-10-15T15:00:00+00:00" and o["issued_utc"] != o["transmitted_utc"]
        assert (o["url"], o["sha256"], o["bytes"]) == (DISPLAY + row["r2_key"], row["sha256"], row["byte_size"])
        assert o["source_in_force"] is True and o["absence"] is None
    assert b["products"]["tcp"]["title"] == "Tropical Storm Simon Advisory Number  33"
    assert b["products"]["tcd"]["title"] == "Tropical Storm Simon Discussion Number  33"
    assert "titles" in b["sections_rule"] and "object at url" in b["sections_rule"]


def test_T2_sweep_no_sections_key_is_read():
    src = (ROOT / "tropics_index.py").read_text()
    assert 'meta.get("sections")' not in src and "meta['sections']" not in src
    assert "sections" not in ti.META_TEXT and "section_kinds" in ti.META_TEXT
    assert not any("sections" in r["meta"] for r in lb.display_rows())          # no writer produces it


# ═══════════════════════════════════════════════════════════════════════════
# I3 — WSP by cycle
# ═══════════════════════════════════════════════════════════════════════════

def test_I3_simon_033s_wsp_is_cycle_2026101512_at_storm_id_blank(client, monkeypatch, pg):
    b, pool = get(client, monkeypatch, pg, f"{P}/products?storm_id=ep202026&advisory=033")
    assert b["init_ts"] == "2026-10-15T12:00:00+00:00"              # the synoptic time
    assert (b["wsp"]["cycle"], b["wsp"]["synoptic_ts"]) == ("2026101512", b["init_ts"])
    objs = by_product(b["wsp"]["objects"])
    assert len(objs) == 6 and b["wsp"]["absent"] == []
    for name, o in objs.items():
        row = display(f"display:{name}", "", "2026101512")
        assert (o["url"], o["sha256"], o["bytes"]) == (DISPLAY + row["r2_key"], row["sha256"], row["byte_size"])
        assert o["url"] == f"{DISPLAY}tropics/wsp/2026101512/{name}.geojson"
        assert o["source_in_force"] is True and o["revision"] == 0
    wsp_reads = [p for q, p in pool.calls if "FROM tropical_display_in_force" in q
                 and p.get("advisory_or_cycle") == "2026101512"]
    assert wsp_reads == [{"products": [f"display:{n}" for n, _ in ti.WSP_PRODUCTS], "storm_id": "",
                          "advisory_or_cycle": "2026101512"}]


# ═══════════════════════════════════════════════════════════════════════════
# X1 — the new reads' plans on Neon
# ═══════════════════════════════════════════════════════════════════════════

def plans():
    text = (RECEIPTS / "neon_plans.txt").read_text()
    return {b.partition("\n")[0].split("   (")[0].strip(): b.partition("\n")[2]
            for b in text.split("\n## ")[1:]}


def test_X1_the_receipts_are_the_routes_statements():
    stated = {s["name"]: s["sql"] for s in json.loads((RECEIPTS / "explains.json").read_text())}
    assert sorted(plans()) == sorted(stated)
    head = " ".join(ti.DISPLAY_FILES_SQL.split()).split(" WHERE v.product")[0]
    for name, sql in stated.items():
        if name.startswith("DISPLAY_FILES_SQL"):
            assert sql.startswith(head), name
    assert "v.storm_id = '' AND v.advisory_or_cycle = '2026101512'" in stated["DISPLAY_FILES_SQL wsp 2026101512"]


# (statement, rows the view's index range walks, rows its substring filter drops)
WALKS = {"DISPLAY_FILES_SQL products ep182026 054": (43, 39),
         "DISPLAY_FILES_SQL text al092026 016": (25, 21),
         "DISPLAY_FILES_SQL text ep202026 033": (74, 72),
         "DISPLAY_FILES_SQL wsp 2026101012": (162, 156),
         "DISPLAY_FILES_SQL wsp 2026101512": (162, 156)}


@pytest.mark.parametrize("name", sorted(WALKS))
def test_X1_the_view_is_reached_by_index_and_the_advisory_is_a_filter(name):
    plan = plans()[name]
    walked, dropped = WALKS[name]
    assert "Seq Scan" not in plan
    # the view's rows come off 314's tfv_display_immutable (product, storm_id, vintage_key) WHERE display
    assert re.search(r"(Index Scan using tfv_display_immutable on tropical_file_vintage v |"
                     r"Bitmap Index Scan on tfv_display_immutable)", plan)
    # the advisory is matched by the view's substring() after the index range: a Filter, not an Index Cond
    assert re.search(r"Filter: .*\"substring\"\(vintage_key, '\^\(\[0-9\]\{3\}", plan)
    assert not re.search(r"Index Cond: .*substring", plan)
    assert f"Rows Removed by Filter: {dropped}" in plan
    kept = 6 if "wsp" in name else (2 if "033" in name else 4)
    assert kept + dropped == walked
    # the original's probe is one exact tfv_display_immutable key per corrected object
    assert re.search(r"Index Scan using tfv_display_immutable on tropical_file_vintage a .*\n"
                     r"\s+Index Cond: \(\(product = v.product\) AND \(storm_id = '[a-z0-9]*'::text\) AND "
                     r"\(vintage_key = '[0-9A-Z]+'::text\)\)", plan)
    hit = int(re.search(r"Buffers: shared hit=(\d+)", plan).group(1))
    assert hit <= 60 and "read=" not in plan.split("Planning")[0]
    assert float(re.search(r"Execution Time: ([0-9.]+) ms", plan).group(1)) < 1.0


# ═══════════════════════════════════════════════════════════════════════════
# R1 — the handed-back bodies
# ═══════════════════════════════════════════════════════════════════════════

SERVED = json.loads((RECEIPTS / "bodies" / "served_from.json").read_text())


@pytest.mark.parametrize("name", sorted(SERVED))
def test_R1_the_handed_back_body_is_what_the_route_serves(client, monkeypatch, pg, name):
    b, _ = get(client, monkeypatch, pg, SERVED[name]["path"])
    b.pop("cache")
    # d091757 adds /products' `toa` block and `toa_rule` and nothing else: they are set
    # aside here, and that lane's own R1 pins its bodies whole.
    if name.startswith("products_"):
        assert set(b) - set(json.loads((RECEIPTS / "bodies" / name).read_text())) == {"toa", "toa_rule"}
        b.pop("toa"), b.pop("toa_rule")
    assert b == json.loads((RECEIPTS / "bodies" / name).read_text())
    by_id = {r["vintage_id"]: r for r in lb.display_rows()}
    for o in SERVED[name]["objects"]:
        row = by_id[o["vintage_id"]]
        assert (row["vintage_key"], row["sha256"]) == (o["vintage_key"], o["sha256"])


def test_R1_the_054_body_is_the_r1_pair_and_its_cycle():
    rows = SERVED["products_ep182026_054.json"]["objects"]
    assert [(o["product"], o["vintage_key"]) for o in rows[:2]] == [("cone", "054r1"), ("ww", "054r1")]
    assert [o["original"]["vintage_key"] for o in rows[:2]] == ["054", "054"]
    assert {o["vintage_key"] for o in rows[2:]} == {"2026101012"} and len(rows) == 8
