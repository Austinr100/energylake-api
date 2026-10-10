"""d091689 — the press API: the front page and its editions, served as the bank states them.

  T0  the fixtures are pantry's: each vector's bytes are the pinned copy, and
      each vector's canonical sha-256 is the one pantry's digests.json states
  A1  the banked daily, loaded as a row, comes back from /front with body equal
      to the vector key for key and sha256 as stored
  A2  the banked article comes back from /edition by slug and is among /front's
      article headers
  A3  revision 1 of the daily supersedes revision 0 on /front; corrected is true
  A4  withdrawing revision 1 puts revision 0 back
  A5  every revision withdrawn is a 404 that says so
  A6  an empty table is 200 with edition null
  A7  a bad kind, a bad date, a slug on a daily, n of 0 and n of 61 are each a
      400 naming the parameter, with no read
  A8  source: no route writes, and none names joule_briefs; no existing route
      changed
  A9  the plans: every statement walks press_editions_identity on the 2,000-row
      constructed bank, and the receipt is the one this code produces
  C   the memo (300 s, single-flight, never stale), the 2 s statement timeout
      on every read, the concurrent reads, the cache block and headers, 503
  V   the banked bodies (the dashboard lane's examples) are served byte for byte

Route tests run main.py's statements unchanged on a throwaway local Postgres
holding the spec's table (load_press_d091689); they skip where no initdb is
installed. Memo and failure cases run on test_solar_outlook's FakePool. Reds:
docs/receipts/press-api-d091689/red_on_main.txt; rehearsal:
tests/rehearse_press_d091689.py -> docs/receipts/press-api-d091689/reds.txt.
"""

import ast
import asyncio
import json
import pathlib
import re
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
import press as pr
import load_press_d091689 as lp
from test_solar_outlook import FakePool

UTC = timezone.utc
ROOT = pathlib.Path(__file__).resolve().parent.parent
RECEIPTS = ROOT / "docs" / "receipts" / "press-api-d091689"
P = "/api/press"
BASE = "ce2522f"                     # main when this lane branched
CACHES = ("_press_front_cache", "_press_edition_cache", "_press_editions_cache")
SLUG = "gas-carried-california-october-2026"
TIMEOUT_SQL = "SET LOCAL statement_timeout = '2s'"


@pytest.fixture(autouse=True)
def _cold_memos(monkeypatch):
    caches = [getattr(main, c) for c in CACHES if hasattr(main, c)]
    for c in caches:
        c.clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lp.NOW)
    yield
    for c in caches:
        c.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(scope="module")
def pg_cluster():
    if lp.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")
    with lp.cluster() as conn:
        yield conn


@pytest.fixture
def pg(pg_cluster):
    """An empty press_editions for each test."""
    lp.reset(pg_cluster)
    return pg_cluster


def get(client, monkeypatch, pg, path, status=200):
    for c in CACHES:
        getattr(main, c).clear()
    monkeypatch.setattr(main, "_pool", lp.PgPool(pg))
    r = client.get(path)
    assert r.status_code == status, r.text
    return r.json()


def strip_cache(body):
    return {k: v for k, v in body.items() if k != "cache"}


# ═══════════════════════════════════════════════════════════════════════════
# T0 — the fixtures are pantry's
# ═══════════════════════════════════════════════════════════════════════════

def test_T0_each_vector_is_the_pinned_copy_and_states_pantrys_digest():
    dig = lp.digests()
    for name in (lp.DAILY, lp.ARTICLE):
        doc = lp.vector(name)
        assert lp.canonical_sha(doc) == dig[name.split("/")[1]]
        assert doc["schema"] == pr.SCHEMA
    assert lp.vector(lp.DAILY)["kind"] == "daily"
    assert lp.vector(lp.ARTICLE)["slug"] == SLUG


def test_T0_a_hand_edit_to_a_vector_is_red(monkeypatch, tmp_path):
    (tmp_path / "vectors").mkdir()
    b = (lp.FIX / lp.DAILY).read_bytes()
    (tmp_path / lp.DAILY).write_bytes(b.replace(b"Nothing here is advice.", b"Nothing here is advice!"))
    monkeypatch.setattr(lp, "FIX", tmp_path)
    with pytest.raises(AssertionError, match="pinned copy"):
        lp.vector(lp.DAILY)


def test_T0_the_table_is_the_spec_text():
    spec = (ROOT / "tests/fixtures/press_d091689/ddl_press_editions.sql").read_text()
    assert "CONSTRAINT press_editions_identity UNIQUE (kind, edition_date, slug, revision)" in spec
    assert spec.count("CREATE") == 1                     # no index beyond the spec's


# ═══════════════════════════════════════════════════════════════════════════
# A1, A2 — the banked editions come back as written
# ═══════════════════════════════════════════════════════════════════════════

def test_A1_the_banked_daily_comes_back_from_front_key_for_key(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    body = get(client, monkeypatch, pg, f"{P}/front")
    ed = body["edition"]
    vec = lp.vector(lp.DAILY)
    assert ed["body"] == vec and set(ed["body"]) == set(vec)
    for k in vec:                                                    # key for key
        assert ed["body"][k] == vec[k], k
    assert ed["sha256"] == lp.digests()["edition_daily_2026_10_10.json"]
    assert ed["sha256"] == pg.execute("SELECT sha256 FROM press_editions").fetchone()["sha256"]
    assert lp.canonical_sha(ed["body"]) == ed["sha256"]              # nothing added, nothing removed
    assert {k: ed[k] for k in ("kind", "edition_date", "slug", "revision", "submitted_at",
                               "writer", "corrected", "withdrawn", "in_force")} == {
        "kind": "daily", "edition_date": "2026-10-10", "slug": "", "revision": 0,
        "submitted_at": "2026-10-10T09:52:00+00:00", "writer": "architect", "corrected": False,
        "withdrawn": False, "in_force": True}
    assert body["absence"] is None


def test_A1_the_served_bytes_carry_the_vectors_non_ascii_unescaped(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    monkeypatch.setattr(main, "_pool", lp.PgPool(pg))
    raw = client.get(f"{P}/front").content.decode("utf-8")
    vec_text = lp.raw(lp.DAILY).decode("utf-8")
    non_ascii = sorted({ch for ch in vec_text if ord(ch) > 127})
    assert non_ascii                                                 # the README's warning
    for ch in non_ascii:
        assert ch in raw or json.dumps(ch)[1:-1] in raw


def test_A1_the_edition_fields_are_the_rows_columns_and_nothing_inside_body(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    ed = get(client, monkeypatch, pg, f"{P}/front")["edition"]
    assert tuple(ed) == pr.EDITION_FIELDS
    assert "body_text" not in json.dumps({k: v for k, v in ed.items() if k != "body"})


def test_A2_the_banked_article_comes_back_from_edition_by_slug(client, monkeypatch, pg):
    lp.insert(pg, lp.article_row())
    body = get(client, monkeypatch, pg, f"{P}/edition?kind=article&date=2026-10-10&slug={SLUG}")
    ed = body["edition"]
    assert ed["body"] == lp.vector(lp.ARTICLE)
    assert ed["sha256"] == lp.digests()["edition_article_2026_10_10_gas_carried_california.json"]
    assert (ed["kind"], ed["slug"], ed["revision"], ed["in_force"]) == ("article", SLUG, 0, True)


def test_A2_the_banked_article_is_among_fronts_article_headers(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    lp.insert(pg, lp.article_row())
    body = get(client, monkeypatch, pg, f"{P}/front")
    assert body["articles"] == [{
        "kind": "article", "edition_date": "2026-10-10", "slug": SLUG,
        "headline": lp.vector(lp.ARTICLE)["lead"]["headline"], "revision": 0,
        "submitted_at": "2026-10-10T12:30:00+00:00"}]
    assert body["articles_absence"] is None


def test_A2_front_headers_are_the_newest_weekly_monthly_and_five_articles(client, monkeypatch, pg):
    lp.constructed(pg)
    body = get(client, monkeypatch, pg, f"{P}/front")
    assert body["weekly"]["edition_date"] == "2026-10-05" and body["weekly"]["kind"] == "weekly"
    assert body["monthly"]["edition_date"] == "2026-10-01"
    arts = body["articles"]
    assert len(arts) == 5 and arts[0]["slug"] == SLUG
    keys = [(a["edition_date"], a["slug"]) for a in arts]
    assert keys == sorted(keys, reverse=True)
    want = pg.execute("""SELECT edition_date, slug FROM press_editions WHERE kind = 'article'
                          ORDER BY edition_date DESC, slug DESC LIMIT 5""").fetchall()
    assert keys == [(r["edition_date"].isoformat(), r["slug"]) for r in want]
    assert set(arts[0]) == {"kind", "edition_date", "slug", "headline", "revision", "submitted_at"}


# ═══════════════════════════════════════════════════════════════════════════
# A3, A4, A5 — revisions, withdrawals
# ═══════════════════════════════════════════════════════════════════════════

def _with_correction(pg):
    lp.insert(pg, lp.daily_row())
    fixed = lp.correction(lp.vector(lp.DAILY))
    lp.insert(pg, lp.row(fixed, revision=1, submitted_at=lp.DAILY_SUBMITTED + timedelta(hours=2)))
    return fixed


def _withdraw(pg, revision, reason):
    pg.execute("UPDATE press_editions SET withdrawn_at = %s, withdrawn_reason = %s "
               "WHERE kind = 'daily' AND revision = %s",
               (lp.NOW - timedelta(minutes=10 * (revision + 1)), reason, revision))


def test_A3_revision_1_supersedes_revision_0_on_front_and_is_corrected(client, monkeypatch, pg):
    fixed = _with_correction(pg)
    ed = get(client, monkeypatch, pg, f"{P}/front")["edition"]
    assert (ed["revision"], ed["corrected"], ed["in_force"]) == (1, True, True)
    assert ed["body"] == fixed and ed["sha256"] == lp.canonical_sha(fixed)
    ed2 = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10")["edition"]
    assert ed2 == ed


def test_A3_a_correction_does_not_reset_the_dailys_age(client, monkeypatch, pg):
    _with_correction(pg)
    fr = get(client, monkeypatch, pg, f"{P}/front")["freshness"]
    assert fr["first_submitted_at"] == "2026-10-10T09:52:00+00:00"
    assert fr["age_h"] == round((lp.NOW - lp.DAILY_SUBMITTED).total_seconds() / 3600, 2) == 3.13


def test_A3_revision_0_by_number_is_served_and_not_in_force(client, monkeypatch, pg):
    _with_correction(pg)
    ed = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10&revision=0")["edition"]
    assert (ed["revision"], ed["corrected"], ed["in_force"], ed["in_force_revision"]) == (0, False, False, 1)
    assert ed["body"] == lp.vector(lp.DAILY)


def test_A4_withdrawing_revision_1_puts_revision_0_back(client, monkeypatch, pg):
    _with_correction(pg)
    _withdraw(pg, 1, "the correction misquoted the source")
    body = get(client, monkeypatch, pg, f"{P}/front")
    ed = body["edition"]
    assert (ed["revision"], ed["corrected"], ed["withdrawn"]) == (0, False, False)
    assert ed["body"] == lp.vector(lp.DAILY)
    assert body["freshness"]["newest_withdrawn"] == {
        "edition_date": "2026-10-10", "revision": 1, "withdrawn_at": "2026-10-10T12:40:00+00:00",
        "withdrawn_reason": "the correction misquoted the source"}
    ed2 = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10")["edition"]
    assert ed2["revision"] == 0


def test_A4_a_withdrawn_revision_asked_for_is_served_with_its_reason(client, monkeypatch, pg):
    _with_correction(pg)
    _withdraw(pg, 1, "the correction misquoted the source")
    ed = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10&revision=1")["edition"]
    assert (ed["withdrawn"], ed["withdrawn_reason"], ed["in_force"], ed["in_force_revision"]) == (
        True, "the correction misquoted the source", False, 0)
    assert ed["withdrawn_at"] == "2026-10-10T12:40:00+00:00"


def test_A5_every_revision_withdrawn_is_a_404_that_says_so(client, monkeypatch, pg):
    _with_correction(pg)
    _withdraw(pg, 1, "the correction misquoted the source")
    _withdraw(pg, 0, "the edition was filed in error")
    d = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10", status=404)["detail"]
    assert d.startswith("every revision of daily 2026-10-10 is withdrawn (2 of 2)")
    assert "revision 1" in d and "the correction misquoted the source" in d


def test_A5_an_identity_never_held_is_a_404_that_says_that_instead(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    d = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-09", status=404)["detail"]
    assert d == "no edition daily 2026-10-09 in the bank"
    d = get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10&revision=4",
            status=404)["detail"]
    assert d == "no revision 4 of daily 2026-10-10 in the bank; revision 0 is in force"


def test_A5_front_with_every_daily_withdrawn_is_200_and_says_so(client, monkeypatch, pg):
    _with_correction(pg)
    _withdraw(pg, 1, "x")
    _withdraw(pg, 0, "y")
    body = get(client, monkeypatch, pg, f"{P}/front")
    assert body["edition"] is None and body["absence"]["reason"] == "no_daily_in_force"
    assert body["freshness"]["newest_withdrawn"]["revision"] == 1


def test_A5_a_withdrawn_identity_is_not_listed(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    older = dict(lp.vector(lp.DAILY), edition_date="2026-10-09")
    lp.insert(pg, lp.row(older, submitted_at=lp.DAILY_SUBMITTED - timedelta(days=1)))
    _withdraw(pg, 0, "both")
    pg.execute("UPDATE press_editions SET withdrawn_at = NULL WHERE edition_date = '2026-10-09'")
    eds = get(client, monkeypatch, pg, f"{P}/editions?kind=daily")["editions"]
    assert [e["edition_date"] for e in eds] == ["2026-10-09"]
    body = get(client, monkeypatch, pg, f"{P}/front")
    assert body["edition"]["edition_date"] == "2026-10-09"           # the newest daily in force
    assert body["freshness"]["newest_withdrawn"]["edition_date"] == "2026-10-10"
    assert body["freshness"]["is_today"] is False


# ═══════════════════════════════════════════════════════════════════════════
# A6 — absence is stated
# ═══════════════════════════════════════════════════════════════════════════

def test_A6_an_empty_table_is_200_with_edition_null(client, monkeypatch, pg):
    body = get(client, monkeypatch, pg, f"{P}/front")
    assert body["edition"] is None
    assert body["absence"] == {"reason": "no_daily", "detail": "The bank holds no daily edition yet."}
    assert (body["weekly"], body["monthly"], body["articles"]) == (None, None, [])
    assert body["weekly_absence"]["reason"] == "no_weekly"
    assert body["articles_absence"]["reason"] == "no_article"
    fr = body["freshness"]
    assert fr["stale_after_h"] == pr.STALE_AFTER_H == 26.0
    assert (fr["age_h"], fr["stale"], fr["is_today"]) == (None, None, None)
    eds = get(client, monkeypatch, pg, f"{P}/editions?kind=weekly")
    assert (eds["count"], eds["editions"], eds["absence"]["reason"]) == (0, [], "no_weekly")


def test_A6_front_carries_stale_after_h_and_the_dailys_age(client, monkeypatch, pg):
    lp.insert(pg, lp.daily_row())
    fr = get(client, monkeypatch, pg, f"{P}/front")["freshness"]
    assert (fr["stale_after_h"], fr["age_h"], fr["stale"], fr["today_pacific"], fr["is_today"]) == (
        26.0, 3.13, False, "2026-10-10", True)
    late = lp.NOW + timedelta(hours=26, minutes=1)
    monkeypatch.setattr(main, "_utcnow", lambda: late)
    fr = get(client, monkeypatch, pg, f"{P}/front")["freshness"]
    assert (fr["stale"], fr["today_pacific"], fr["is_today"]) == (True, "2026-10-11", False)


# ═══════════════════════════════════════════════════════════════════════════
# A7 — parameters
# ═══════════════════════════════════════════════════════════════════════════

BAD = [
    (f"{P}/edition?kind=dailyy&date=2026-10-10", "kind"),
    (f"{P}/edition?date=2026-10-10", "kind"),
    (f"{P}/edition?kind=daily&date=2026-13-01", "date"),
    (f"{P}/edition?kind=daily&date=20261010", "date"),
    (f"{P}/edition?kind=daily", "date"),
    (f"{P}/edition?kind=daily&date=2026-10-10&slug=x", "slug"),
    (f"{P}/edition?kind=weekly&date=2026-10-05&slug=x", "slug"),
    (f"{P}/edition?kind=article&date=2026-10-10", "slug"),
    (f"{P}/edition?kind=article&date=2026-10-10&slug=a%20b", "slug"),
    (f"{P}/edition?kind=daily&date=2026-10-10&revision=-1", "revision"),
    (f"{P}/edition?kind=daily&date=2026-10-10&revision=one", "revision"),
    (f"{P}/editions?kind=daily&n=0", "n"),
    (f"{P}/editions?kind=daily&n=61", "n"),
    (f"{P}/editions?kind=daily&n=x", "n"),
    (f"{P}/editions?kind=bogus", "kind"),
    (f"{P}/editions", "kind"),
]


@pytest.mark.parametrize("path,field", BAD, ids=[f"{f}:{p.rsplit('/', 1)[1]}" for p, f in BAD])
def test_A7_a_bad_parameter_is_a_400_naming_it_with_no_read(client, monkeypatch, path, field):
    pool = FakePool([])
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith(f"{field}: ")
    assert pool.calls == []


def test_A7_n_is_1_to_60_default_14_and_60_is_served_not_trimmed(client, monkeypatch, pg):
    lp.constructed(pg)
    assert get(client, monkeypatch, pg, f"{P}/editions?kind=daily")["count"] == 14
    b = get(client, monkeypatch, pg, f"{P}/editions?kind=daily&n=60")
    assert (b["n"], b["count"]) == (60, 60)
    assert get(client, monkeypatch, pg, f"{P}/editions?kind=monthly&n=60")["count"] == 48
    assert get(client, monkeypatch, pg, f"{P}/editions?kind=daily&n=1")["count"] == 1


def test_A7_editions_are_newest_first_one_per_identity_in_force(client, monkeypatch, pg):
    lp.constructed(pg)
    eds = get(client, monkeypatch, pg, f"{P}/editions?kind=daily&n=60")["editions"]
    dates = [e["edition_date"] for e in eds]
    assert dates == sorted(set(dates), reverse=True) and dates[0] == "2026-10-10"
    want = pg.execute("""
        SELECT DISTINCT ON (edition_date) edition_date, revision FROM press_editions
         WHERE kind = 'daily' AND withdrawn_at IS NULL
         ORDER BY edition_date DESC, revision DESC LIMIT 60""").fetchall()
    assert [(e["edition_date"], e["revision"]) for e in eds] == [
        (r["edition_date"].isoformat(), r["revision"]) for r in want]
    assert {e["revision"] for e in eds} == {0, 1}                     # a live correction is listed


# ═══════════════════════════════════════════════════════════════════════════
# A8 — source: read only, joule_briefs untouched, no existing route changed
# ═══════════════════════════════════════════════════════════════════════════

WRITE = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|DROP|CREATE|GRANT|COPY|"
                   r"nextval|setval|pg_advisory)\b", re.I)


def _press_section() -> str:
    text = (ROOT / "main.py").read_text()
    start = text.index("# THE PRESS — d091689")
    end = text.index("# LOAD OUTLOOK AND CAISO NET DEMAND — d091611")
    return text[start:end]


def test_A8_no_press_statement_writes():
    for name, sql in pr.STATEMENTS.items():
        assert not WRITE.search(sql), name
        assert sql.lstrip().upper().startswith(("SELECT", "(SELECT")), name
    tree = ast.parse((ROOT / "press.py").read_text())
    sql_like = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, str) and "press_editions" in n.value]
    assert sql_like and not any(WRITE.search(s) for s in sql_like)


def test_A8_the_press_routes_are_get_and_read_only_through_dd_timed_read():
    sec = _press_section()
    assert re.findall(r"@app\.(\w+)\(", sec) == ["get", "get", "get"]
    code = "\n".join(l for l in re.sub(r'"""[\s\S]*?"""', "", sec).splitlines()
                     if not l.lstrip().startswith("#"))
    assert not WRITE.search(code)
    assert "_pool.connection" not in sec and "cur.execute" not in sec
    assert sec.count("_dd_timed_read(") == 5


def test_A8_nothing_in_the_press_names_joule_briefs():
    assert "joule_briefs" not in (ROOT / "press.py").read_text()
    assert "joule_briefs" not in _press_section()
    for r in main.app.routes:
        if getattr(r, "path", "").startswith(P):
            src = ast.get_source_segment((ROOT / "main.py").read_text(), _fn_node(r.endpoint.__name__))
            assert "joule" not in src


def _fn_node(name):
    tree = ast.parse((ROOT / "main.py").read_text())
    return next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _base_main():
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:main.py"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"{BASE} is not in this clone")
    return r.stdout


def test_A8_no_existing_route_changed():
    base = _base_main()
    now = (ROOT / "main.py").read_text()
    sec = _press_section()
    # main.py at the base, plus three index lines and the press section, is main.py now
    index = [l for l in now.splitlines(keepends=True) if l.startswith("    GET /api/press/")]
    assert len(index) == 3
    rebuilt = now.replace(sec, "")
    for l in index:
        rebuilt = rebuilt.replace(l, "", 1)
    assert rebuilt == base
    paths = [r.path for r in main.app.routes if getattr(r, "path", "").startswith(P)]
    assert sorted(paths) == [f"{P}/edition", f"{P}/editions", f"{P}/front"]


# ═══════════════════════════════════════════════════════════════════════════
# A9 — the plans
# ═══════════════════════════════════════════════════════════════════════════

def plan_cases():
    d = date(2026, 10, 10)
    return [
        ("FRONT_DAILY_SQL", {}),
        ("FRONT_HEADERS_SQL", {}),
        ("HEADERS_SQL", {"kind": "daily", "n": 14}),
        ("HEADERS_SQL", {"kind": "daily", "n": 60}),
        ("HEADERS_SQL", {"kind": "article", "n": 60}),
        ("EDITION_SQL", {"kind": "daily", "date": d, "slug": ""}),
        ("EDITION_SQL", {"kind": "article", "date": d, "slug": SLUG}),
        ("EDITION_AT_SQL", {"kind": "daily", "date": d, "slug": "", "revision": 0}),
    ]


def plan_nodes(conn, sql, params):
    plan = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql,
                        params).fetchone()["QUERY PLAN"][0]["Plan"]
    out = []

    def walk(n):
        out.append(n)
        for c in n.get("Plans", []):
            walk(c)
    walk(plan)
    return out


@pytest.fixture
def pg_2000(pg):
    lp.constructed(pg)
    assert pg.execute("SELECT count(*) AS n FROM press_editions").fetchone()["n"] == 2000
    return pg


def test_A9_every_statement_walks_the_identity_index(pg_2000):
    for name, params in plan_cases():
        nodes = plan_nodes(pg_2000, pr.STATEMENTS[name], params)
        scans = [n for n in nodes if n.get("Relation Name") == "press_editions"]
        assert scans, name
        for n in scans:
            assert n["Node Type"] in ("Index Scan", "Index Only Scan"), (name, n["Node Type"])
            assert n["Index Name"] == "press_editions_identity", name
        assert not any(n["Node Type"] in ("Sort", "Seq Scan", "Bitmap Heap Scan") for n in nodes), name


def test_A9_the_newest_walks_stop_early_rows_read_bounded_by_what_is_served(pg_2000):
    """DISTINCT ON over the index read backwards stops at LIMIT: rows read is
    n plus the withdrawn and superseded revisions among them, never the kind."""
    for name, params in plan_cases():
        nodes = plan_nodes(pg_2000, pr.STATEMENTS[name], params)
        read = sum(n["Actual Rows"] * n["Actual Loops"] + n.get("Rows Removed by Filter", 0)
                   for n in nodes if n.get("Relation Name") == "press_editions")
        cap = {"HEADERS_SQL": params.get("n", 0) * 2, "FRONT_HEADERS_SQL": 14}.get(name, 6)
        assert read <= cap, (name, params, read)


def test_A9_the_plans_receipt_is_this_codes():
    text = (RECEIPTS / "plans.md").read_text()
    for name, sql in pr.STATEMENTS.items():
        assert name in text
    for line in ("Neon plans are still owed", "2,000", "press_editions_identity"):
        assert line in text
    assert "Seq Scan" not in text.split("## Raw plans")[1]
    pinned = json.loads((RECEIPTS / "pinned_sql.json").read_text())
    assert pinned == pr.STATEMENTS


# ═══════════════════════════════════════════════════════════════════════════
# C — memo, timeout, concurrency, cache block, 503
# ═══════════════════════════════════════════════════════════════════════════

def _daily_front_row():
    r = lp.daily_row()
    r["body"] = lp.vector(lp.DAILY)
    r.update(first_revision=0, first_submitted_at=r["submitted_at"], held_edition_date=r["edition_date"],
             held_revision=0, held_withdrawn_at=None, held_withdrawn_reason=None)
    return r


def routes():
    return [(pr.FRONT_DAILY_SQL, [_daily_front_row()]), (pr.FRONT_HEADERS_SQL, []),
            (pr.HEADERS_SQL, []), (pr.EDITION_SQL, [{**_daily_front_row(), "n_held": 1,
                                                      "n_withdrawn": 0, "top_revision": 0,
                                                      "top_withdrawn_at": None,
                                                      "top_withdrawn_reason": None}]),
            (pr.EDITION_AT_SQL, [{**_daily_front_row(), "in_force_revision": 0}])]


PATHS = (f"{P}/front", f"{P}/edition?kind=daily&date=2026-10-10",
         f"{P}/edition?kind=daily&date=2026-10-10&revision=0", f"{P}/editions?kind=daily")


@pytest.mark.parametrize("path", PATHS)
def test_C_every_read_runs_under_the_2s_statement_timeout(client, monkeypatch, path):
    pool = FakePool(routes())
    monkeypatch.setattr(main, "_pool", pool)
    assert client.get(path).status_code == 200
    sql = pool.sql_run()
    assert sql[0::2] == [TIMEOUT_SQL] * (len(sql) // 2)                 # SET LOCAL first, each read
    assert TIMEOUT_SQL not in sql[1::2] and len(sql) % 2 == 0          # one statement per read
    assert all(q in pr.STATEMENTS.values() for q in sql[1::2])
    assert main._DD_REGION_FC_STATEMENT_TIMEOUT == "2s"


def test_C_front_runs_its_two_reads_concurrently(monkeypatch):
    started, release = [], asyncio.Event()

    class Pool(FakePool):
        def connection(self):
            ctx = super().connection()
            orig = ctx.cursor

            def cursor():
                cur = orig()
                execute = cur.execute

                async def ex(q, p=None):
                    await execute(q, p)
                    if q != TIMEOUT_SQL:
                        started.append(q)
                        if len(started) == 2:
                            release.set()
                        await asyncio.wait_for(release.wait(), 2)    # both must be in flight
                cur.execute = ex
                return cur
            ctx.cursor = cursor
            return ctx
    monkeypatch.setattr(main, "_pool", Pool(routes()))
    import httpx

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
            return await c.get(f"{P}/front")
    r = asyncio.run(run())
    assert r.status_code == 200 and len(started) == 2


def test_C_memoised_per_key_300s_never_stale_with_the_cache_block(client, monkeypatch):
    pool = FakePool(routes())
    monkeypatch.setattr(main, "_pool", pool)
    r1 = client.get(f"{P}/editions?kind=daily")
    r2 = client.get(f"{P}/editions?kind=daily&n=14")
    r3 = client.get(f"{P}/editions?kind=daily&n=13")
    assert [r.headers["x-cache"] for r in (r1, r2, r3)] == ["miss", "hit", "miss"]
    assert r1.headers["cache-control"] == "max-age=300"
    assert set(r1.json()["cache"]) == {"state", "built_at", "age_seconds", "ttl_seconds",
                                       "build_seconds", "refreshing"}
    for c in CACHES:
        cache = getattr(main, c)
        assert cache.ttl == 300.0 and cache.allow_stale is False and cache.max_stale_s is None
    main._press_editions_cache._entries[("daily", 14)].built_mono -= 301
    assert client.get(f"{P}/editions?kind=daily").headers["x-cache"] == "miss"
    e1 = client.get(f"{P}/edition?kind=daily&date=2026-10-10")
    e2 = client.get(f"{P}/edition?kind=daily&date=2026-10-10&revision=0")
    assert [r.headers["x-cache"] for r in (e1, e2)] == ["miss", "miss"]
    assert set(main._press_edition_cache._entries) == {
        ("daily", date(2026, 10, 10), "", None), ("daily", date(2026, 10, 10), "", 0)}


def test_C_twenty_concurrent_cold_requests_make_one_build(monkeypatch):
    pool = FakePool(routes())
    monkeypatch.setattr(main, "_pool", pool)
    import httpx

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://t") as c:
            return await asyncio.gather(*(c.get(f"{P}/front") for _ in range(20)))
    rs = asyncio.run(run())
    assert all(r.status_code == 200 for r in rs)
    assert sum(pr.FRONT_DAILY_SQL in q for q in pool.sql_run()) == 1


class _DeadPool:
    def connection(self):
        raise OSError("connection refused")


@pytest.mark.parametrize("path", PATHS)
def test_C_db_down_is_503(client, monkeypatch, path):
    monkeypatch.setattr(main, "_pool", _DeadPool())
    r = client.get(path)
    assert r.status_code == 503 and r.json()["detail"].startswith("db unavailable")


def test_C_a_statement_timeout_is_503_and_not_memoised(client, monkeypatch):
    import psycopg

    def cancel(_p):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    r_ = [(pr.FRONT_DAILY_SQL, cancel), (pr.FRONT_HEADERS_SQL, [])]
    monkeypatch.setattr(main, "_pool", FakePool(r_))
    r = client.get(f"{P}/front")
    assert r.status_code == 503 and "statement timeout" in r.json()["detail"]
    assert main._press_front_cache._entries == {}


def test_C_a_404_is_not_memoised(client, monkeypatch, pg):
    get(client, monkeypatch, pg, f"{P}/edition?kind=daily&date=2026-10-10", status=404)
    assert main._press_edition_cache._entries == {}


# ═══════════════════════════════════════════════════════════════════════════
# V — the banked bodies, served byte for byte
# ═══════════════════════════════════════════════════════════════════════════

BANKED = [("body_front.json", f"{P}/front"),
          ("body_edition_article.json", f"{P}/edition?kind=article&date=2026-10-10&slug={SLUG}"),
          ("body_editions_daily_n14.json", f"{P}/editions?kind=daily")]


@pytest.fixture(scope="module")
def pg_2000_module(pg_cluster):
    lp.constructed(pg_cluster)
    return pg_cluster


@pytest.mark.parametrize("fname,path", BANKED, ids=[b[0] for b in BANKED])
def test_V_the_banked_body_is_served_byte_for_byte(client, monkeypatch, pg_2000_module, fname, path):
    body = strip_cache(get(client, monkeypatch, pg_2000_module, path))
    banked = (RECEIPTS / fname).read_text()
    assert json.dumps(body, separators=(",", ":"), ensure_ascii=False) == banked
