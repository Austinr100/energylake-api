"""d091691 — the CPC API reads one method version, so v2 rows beside v1 change nothing.

  P0  the rebuild is d091679's bank: main's statements over it serve d091679's
      vectors, and the pinned statements serve main's bodies byte for byte
  P1  v2 beside v1 (a v2 window row and days for every banked issuance, v2
      verdict cells for every cell, KDEN written by v2 only, a v2-only issuance
      newer than every v1 one): every route returns main's body byte for byte,
      and d091679's vectors; every row each read returns is v1
  P2  the pin removed (main's statements): the same bank duplicates days on
      /curves and cells on /places, and serves v2's newer issuance (the red on main)
  P3  the v2-only newer issuance is not /places' (or /curves') newest issuance
  P4  every SQL statement in cpc_outlooks.py that names cpc_outlook_curves,
      cpc_curve_verdicts or v_cpc_curves_drawable pins each reference to
      CPC_METHOD_VERSION, and names no other version
  P5  the Neon plans: each pinned read was planned on production beside main's,
      within 3x of d091679's timing, with no new whole-table scan
  P6  the flip is the constant: pinned to v2, no route mixes either

The bank is d091679's fixtures rebuilt as base rows in a local Postgres with
pantry 286 + 298 applied (load_bank_d091691). The constructed v2 rows are
fixtures/cpc_method_pin_d091691/v2_*.sql. /outlooks reads none of the three
relations; its two reads are served from d091679's banked rows, as there.
Reds and the rehearsal: docs/receipts/cpc-api-method-pin-d091691/.
"""

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import cpc_outlooks as co
import load_bank_d091691 as lb
import main
import test_cpc_outlooks_d091679 as t
from test_solar_outlook import FakePool

ROOT = pathlib.Path(__file__).resolve().parents[1]
D091679 = ROOT / "docs" / "receipts" / "cpc-outlooks-d091679"
RECEIPTS = ROOT / "docs" / "receipts" / "cpc-api-method-pin-d091691"
# the architect's decision (d091691 §1); cpc_outlooks.CPC_METHOD_VERSION must equal it
VERSION = "cpc_curves_v1"
PIN = f"'{VERSION}'"
RELATIONS = ("cpc_outlook_curves", "cpc_curve_verdicts", "v_cpc_curves_drawable")
CPC_READS = ("CURVES_SQL", "PLACES_SQL", "PLACES_NEWEST_SQL")
# main's statements: d091679's pin, which test_T8 held equal to main 891de2e's
MAIN_SQL = json.loads((D091679 / "pinned_sql.json").read_text())

KDEN = t.KDEN
BODIES = t.BODIES + [
    ("curves_pnw_load", "/api/weather/cpc/curves", t.place_params(t.PNW_LOAD)),
    ("vintages_610_ksan_14", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **t.place_params(t.KSAN), "n": 14}),
    ("vintages_814_pnw_14", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **t.place_params(t.PNW_POP), "n": 14}),
    ("vintages_814_kden_14", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **t.place_params(KDEN), "n": 14}),
]


def _need_postgres():
    if lb.initdb_path() is None:
        pytest.skip("no local Postgres (initdb) on this box: apt install postgresql-16")
    pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def pg_v1():
    """d091679's bank: v1 only, as Neon held it at the d091679 cut."""
    _need_postgres()
    with lb.cluster(load_bank=False) as conn:
        lb.load(conn)
        yield conn


@pytest.fixture(scope="module")
def pg_beside():
    """v2 beside every banked v1 issuance and cell; no newer issuance."""
    _need_postgres()
    with lb.cluster(load_bank=False) as conn:
        lb.load(conn)
        lb.construct(conn, newer=False)
        yield conn


@pytest.fixture(scope="module")
def pg_v2():
    """v2 beside v1, and a v2-only issuance newer than every v1 one."""
    _need_postgres()
    with lb.cluster(load_bank=False) as conn:
        lb.load(conn)
        lb.construct(conn, newer=True)
        yield conn


@pytest.fixture(autouse=True)
def _cold_memos():
    for c in t.CACHES:
        getattr(main, c).clear()
    yield
    for c in t.CACHES:
        getattr(main, c).clear()


@pytest.fixture
def client():
    return TestClient(main.app)


class BankPool(lb.PgPool):
    """The three relations' reads run on the local Postgres; /outlooks' two
    reads (cpc_outlook_features, cpc_outlook_vintage) answer from d091679's
    banked rows, as in its tests."""

    def __init__(self, conn):
        super().__init__(conn)
        self.fake = FakePool(t.pool().routes)

    def connection(self):
        real, fake, calls = super().connection(), self.fake.connection(), self.calls

        class _Cur:
            async def __aenter__(self):
                self.r = await real.cursor().__aenter__()
                self.f = await fake.cursor().__aenter__()
                self.use = self.r
                return self

            async def __aexit__(self, *exc):
                await self.r.__aexit__(*exc)
                return False

            async def execute(self, query, params=None):
                outlook = "cpc_outlook_features" in query or "cpc_outlook_vintage" in query
                self.use = self.f if outlook else self.r
                if outlook:
                    calls.append((query, params))
                await self.use.execute(query, params)

            async def fetchall(self):
                return await self.use.fetchall()

            async def fetchone(self):
                return await self.use.fetchone()

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return _Cur()

            def transaction(self):
                return _Ctx()

        return _Ctx()


def with_sql(monkeypatch, sql):
    """Serve with these statements in place of cpc_outlooks' (None: as is)."""
    for name, text in (sql or {}).items():
        monkeypatch.setattr(co, name, text)


def serve(client, monkeypatch, conn, path, params, sql=None):
    for c in t.CACHES:
        getattr(main, c).clear()
    with monkeypatch.context() as m:
        with_sql(m, sql)
        m.setattr(main, "_pool", BankPool(conn))
        r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r


def body_bytes(r) -> bytes:
    """The served body minus its cache block (timings), as the route serialises it."""
    b = r.json()
    b.pop("cache")
    return json.dumps(b, ensure_ascii=False, separators=(",", ":")).encode()


def unpinned():
    return {n: MAIN_SQL[n] for n in CPC_READS}


def strip(b):
    return {k: v for k, v in b.items() if k != "cache"}


# ═══════════════════════════════════════════════════════════════════════════
# P0 — the rebuild is d091679's bank
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,path,params", t.BODIES)
def test_P0_mains_statements_over_the_rebuild_serve_d091679s_vectors(
        client, monkeypatch, pg_v1, name, path, params):
    r = serve(client, monkeypatch, pg_v1, path, params, sql=unpinned())
    assert strip(r.json()) == strip(json.loads((D091679 / name).read_text()))


@pytest.mark.parametrize("name,path,params", BODIES)
def test_P0_on_todays_bank_the_pin_serves_mains_bytes(client, monkeypatch, pg_v1, name, path,
                                                       params):
    main_b = body_bytes(serve(client, monkeypatch, pg_v1, path, params, sql=unpinned()))
    assert body_bytes(serve(client, monkeypatch, pg_v1, path, params)) == main_b


# ═══════════════════════════════════════════════════════════════════════════
# P1 — v2 beside v1 changes no body
# ═══════════════════════════════════════════════════════════════════════════

def test_P1_the_constructed_bank_is_what_it_says(pg_v2):
    q = lambda s: pg_v2.execute(s).fetchall()
    curves = {(r["method_version"], r["product"]): (r["n"], r["hi"].isoformat()) for r in q(
        "SELECT method_version, product, count(*) n, max(issued_date) hi "
        "FROM cpc_outlook_curves GROUP BY 1, 2")}
    assert curves[("cpc_curves_v1", "610temp")][1] == "2026-10-09"
    assert curves[("cpc_curves_v1", "814temp")][1] == "2026-10-08"
    assert curves[("cpc_curves_v2", "610temp")][1] == curves[("cpc_curves_v2", "814temp")][1] \
        == "2026-10-10"
    # a v2 window row beside every v1 window row
    assert q("SELECT count(*) n FROM cpc_outlook_curves a WHERE a.method_version = "
             "'cpc_curves_v1' AND a.day_index IS NULL AND NOT EXISTS (SELECT 1 FROM "
             "cpc_outlook_curves b WHERE b.method_version = 'cpc_curves_v2' AND "
             "(b.product, b.issued_date, b.place_kind, b.place, b.weighting) = (a.product, "
             "a.issued_date, a.place_kind, a.place, a.weighting) AND b.day_index IS NULL)"
             )[0]["n"] == 0
    # a v2 cell beside every v1 cell; both versions in force, each by its own hash
    cells = q("SELECT method_version, method_hash, count(*) n FROM cpc_curve_verdicts "
              "GROUP BY 1, 2 ORDER BY 1")
    assert [(c["method_version"], c["method_hash"], c["n"]) for c in cells] == [
        ("cpc_curves_v1", "e99c71234013", 720), ("cpc_curves_v2", "b201e09ff97d", 720)]
    view = {r["method_version"]: r["n"] for r in q(
        "SELECT method_version, count(*) n FROM v_cpc_curves_drawable GROUP BY 1")}
    assert view["cpc_curves_v1"] == 616 and view["cpc_curves_v2"] > 0
    # KDEN: written by v2 only, drawable there
    assert {r["method_version"] for r in q(
        "SELECT DISTINCT method_version FROM v_cpc_curves_drawable "
        "WHERE place = 'USW00003017'")} == {"cpc_curves_v2"}


@pytest.mark.parametrize("bank", ["pg_beside", "pg_v2"])
@pytest.mark.parametrize("name,path,params", BODIES)
def test_P1_every_route_serves_mains_body_byte_for_byte(client, monkeypatch, request, pg_v1,
                                                         bank, name, path, params):
    main_b = body_bytes(serve(client, monkeypatch, pg_v1, path, params, sql=unpinned()))
    got = serve(client, monkeypatch, request.getfixturevalue(bank), path, params)
    assert body_bytes(got) == main_b
    if name.endswith(".json"):
        assert strip(got.json()) == strip(json.loads((D091679 / name).read_text()))


@pytest.mark.parametrize("name,params", [
    ("CURVES_SQL", {"product": p, "place_kind": pk, "place": pl, "weighting": w, "n": 14})
    for p in co.CURVE_PRODUCTS for pk, pl, w in (t.KSAN, t.PNW_POP, t.PNW_LOAD, KDEN)
] + [("PLACES_SQL", {}), ("PLACES_NEWEST_SQL", {"products": list(co.CURVE_PRODUCTS)})])
def test_P1_every_row_each_read_returns_is_the_pinned_version(pg_v1, pg_v2, name, params):
    """Row for row: the pinned read over v1 + v2 returns exactly what it
    returns over v1 alone, and every version column it carries is the pin."""
    sql = getattr(co, name)
    a, b = pg_v1.execute(sql, params).fetchall(), pg_v2.execute(sql, params).fetchall()
    assert a == b and a
    for r in b:
        for k in ("method_version", "w_method_version", "v_method_version"):
            assert r.get(k) in (None, VERSION), (k, r.get(k))


# ═══════════════════════════════════════════════════════════════════════════
# P2 — the pin removed: the red on main
# ═══════════════════════════════════════════════════════════════════════════

def test_P2_unpinned_curves_duplicate_days(client, monkeypatch, pg_beside):
    r = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/curves",
              t.place_params(t.KSAN), sql=unpinned())
    for p in r.json()["products"]:
        days = [d["day_index"] for d in p["vintage"]["curve"]["days"]]
        n = 5 if p["product"] == "610temp" else 7
        assert len(days) == 2 * n and sorted(set(days)) == list(range(n))   # every day twice
    pinned = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/curves",
                   t.place_params(t.KSAN))
    for p in pinned.json()["products"]:
        days = [d["day_index"] for d in p["vintage"]["curve"]["days"]]
        assert days == sorted(set(days))


def test_P2_unpinned_vintages_duplicate_days_in_every_frame(client, monkeypatch, pg_beside):
    r = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/curves/vintages",
              {"product": "814temp", **t.place_params(t.PNW_POP), "n": 14}, sql=unpinned())
    assert all(len(v["curve"]["days"]) == 14 for v in r.json()["vintages"])


def test_P2_unpinned_places_duplicate_cells(client, monkeypatch, pg_beside):
    b = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/places", {},
              sql=unpinned()).json()
    assert b["cell_count"] == 1440 and len(b["verdict_versions"]) == 2
    for p in b["places"]:
        for pr in p["products"]:
            assert pr["cell_count"] == 12                    # keyed (season, strength) ...
            assert sum(len(s["cells"]) for s in pr["seasons"]) == 24   # ... served twice
    pinned = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/places", {}).json()
    assert pinned["cell_count"] == 720 and len(pinned["verdict_versions"]) == 1


def test_P2_unpinned_kden_reads_v2s_curve(client, monkeypatch, pg_beside):
    b = serve(client, monkeypatch, pg_beside, "/api/weather/cpc/curves",
              t.place_params(KDEN), sql=unpinned()).json()
    assert b["products_drawn"] == ["610temp", "814temp"]
    assert "on 24 base years" in b["products"][0]["vintage"]["curve"]["label"]


@pytest.mark.parametrize("bank", ["pg_beside", "pg_v2"])
@pytest.mark.parametrize("name,path,params", t.BODIES[:6])
def test_P2_unpinned_no_cpc_vector_survives(client, monkeypatch, request, bank, name, path,
                                            params):
    """Main's statements over the same bank: every /curves, /vintages and
    /places vector d091679 banked is broken (/outlooks reads none of the three)."""
    got = serve(client, monkeypatch, request.getfixturevalue(bank), path, params,
                sql=unpinned())
    assert strip(got.json()) != strip(json.loads((D091679 / name).read_text()))


# ═══════════════════════════════════════════════════════════════════════════
# P3 — a newer v2-only issuance is not the newest
# ═══════════════════════════════════════════════════════════════════════════

def test_P3_the_newer_v2_issuance_is_not_places_newest(client, monkeypatch, pg_v2):
    want = {"610temp": "2026-10-09", "814temp": "2026-10-08"}
    b = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/places", {}).json()
    assert b["newest_written_issued_date"] == want
    newest = [pr["newest_curve"] for p in b["places"] for pr in p["products"]
              if pr["newest_curve"]]
    assert len(newest) == 34
    assert {(n["issued_date"], n["method_version"]) for n in newest} == {
        ("2026-10-09", "cpc_curves_v1"), ("2026-10-08", "cpc_curves_v1")}
    unp = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/places", {},
                sql=unpinned()).json()
    assert unp["newest_written_issued_date"] == {"610temp": "2026-10-10",
                                                 "814temp": "2026-10-10"}


def test_P3_nor_curves_newest_nor_a_frame(client, monkeypatch, pg_v2):
    b = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/curves",
              t.place_params(t.KSAN)).json()
    assert {p["product"]: p["newest_written_issued_date"] for p in b["products"]} == {
        "610temp": "2026-10-09", "814temp": "2026-10-08"}
    v = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/curves/vintages",
              {"product": "610temp", **t.place_params(t.KSAN), "n": 14}).json()
    assert "2026-10-10" not in v["issued_dates"] and v["issued_dates"][-1] == "2026-10-09"
    unp = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/curves",
                t.place_params(t.KSAN), sql=unpinned()).json()
    assert {p["newest_written_issued_date"] for p in unp["products"]} == {"2026-10-10"}


# ═══════════════════════════════════════════════════════════════════════════
# P4 — the source: every reference to the three relations carries the pin
# ═══════════════════════════════════════════════════════════════════════════

_REF = re.compile(r"\b(?:FROM|JOIN)\s+(" + "|".join(RELATIONS) + r")\b(?:\s+(\w+))?")
_KEYWORDS = {"ON", "WHERE", "JOIN", "LEFT", "CROSS", "INNER", "ORDER", "GROUP", "LIMIT", "OFFSET"}


def pin_report(sql: str) -> dict:
    """Per alias: how many references to the three relations, how many
    `<alias>.method_version = <PIN>` predicates."""
    refs = {}
    for rel, alias in _REF.findall(sql):
        assert alias and alias.upper() not in _KEYWORDS, f"{rel} has no alias"
        refs[alias] = refs.get(alias, 0) + 1
    return {a: (n, len(re.findall(rf"\b{a}\.method_version = {re.escape(PIN)}", sql)))
            for a, n in refs.items()}


def test_P4_every_statement_naming_the_relations_pins_each_reference():
    seen = {}
    for name in sorted(n for n in dir(co) if n.endswith("_SQL")):
        sql = getattr(co, name)
        if not any(re.search(rf"\b{rel}\b", sql) for rel in RELATIONS):
            continue
        rep = pin_report(sql)
        seen[name] = sum(n for n, _p in rep.values())
        for alias, (n, pins) in rep.items():
            assert pins >= n, (name, alias, n, pins)
    # not vacuous: the three reads, every reference found
    assert seen == {"CURVES_SQL": 5, "PLACES_SQL": 3, "PLACES_NEWEST_SQL": 2}


def test_P4_one_version_named_and_it_is_the_constant():
    assert getattr(co, "CPC_METHOD_VERSION", None) == VERSION
    for name in (n for n in dir(co) if n.endswith("_SQL")):
        sql = getattr(co, name)
        assert set(re.findall(r"'(cpc_curves_v\d+)'", sql)) <= {VERSION}, name
        assert "%(method_version)s" not in sql, name


def test_P4_the_view_rows_stay_tied_to_the_window_row():
    """main's tie (dv.method_version = w.method_version) is kept beside the pin,
    so an issuance with no window row draws nothing, as on main."""
    assert "AND dv.method_version = w.method_version" in co.CURVES_SQL


def test_P4_mains_statements_fail_the_source_test():
    """The source test sees main's SQL as unpinned (so a removed pin is red)."""
    for name in CPC_READS:
        rep = pin_report(MAIN_SQL[name].replace("FROM cpc_outlook_curves WHERE",
                                                "FROM cpc_outlook_curves c WHERE")
                         .replace("FROM cpc_curve_verdicts WHERE",
                                  "FROM cpc_curve_verdicts cv WHERE")
                         .replace("FROM cpc_curve_verdicts )", "FROM cpc_curve_verdicts hv )"))
        assert all(p == 0 for _n, p in rep.values()), name


# ═══════════════════════════════════════════════════════════════════════════
# P5 — the Neon plans
# ═══════════════════════════════════════════════════════════════════════════

def _blocks(path):
    return re.findall(r"^-- (\w+) (\w+_SQL) (\{.*?\})\n(.*?);\s*$", path.read_text(),
                      re.M | re.S)


def _plans(path):
    out = {}
    for seg in path.read_text().split("== ")[1:]:
        tag = seg.split(" ", 1)[0]
        out[tag] = seg
    return out


def _ms(seg):
    return float(re.search(r"Execution Time: ([\d.]+) ms", seg).group(1))


def _seq(seg):
    return set(re.findall(r"Seq Scan on (\w+)", seg))


def test_P5_each_plan_ran_the_pinned_or_mains_statement():
    """A<nn> is this pin's statement, B<nn> main's (d091679's pin), the params
    as literals; each has its plan and timing in plans_raw.txt."""
    blocks = _blocks(RECEIPTS / "explains.sql")
    plans = _plans(RECEIPTS / "plans_raw.txt")
    assert sorted(tag for tag, *_ in blocks) == sorted(
        [f"{k}0{i}" for k in "AB" for i in range(1, 7)])
    for tag, name, params, stmt in blocks:
        src = getattr(co, name) if tag[0] == "A" else MAIN_SQL[name]
        p = json.loads(params)
        want = re.sub(r"%\((\w+)\)s", lambda m: t_lit(p[m.group(1)]), t.flat(src))
        assert t.flat(stmt) == "EXPLAIN (ANALYZE, BUFFERS) " + want, tag
        assert "Execution Time" in plans[tag], tag


def t_lit(v):
    if isinstance(v, list):
        return "ARRAY[" + ",".join(f"'{x}'" for x in v) + "]"
    return str(v) if isinstance(v, int) else f"'{v}'"


D091679_TAG = {"01": "C01", "02": "C02", "03": "C03", "04": "C04", "05": "P01", "06": "P02"}


@pytest.mark.parametrize("nn", sorted(D091679_TAG))
def test_P5_stop_p_no_new_whole_table_scan_and_within_3x(nn):
    """STOP-P: a pinned read that scans a whole table it did not scan in
    d091679's plan, or runs more than 3x d091679's time, stops the lane."""
    new = _plans(RECEIPTS / "plans_raw.txt")
    old = _plans(D091679 / "plans_raw.txt")[D091679_TAG[nn]]
    a = new[f"A{nn}"]
    assert _ms(a) <= 3 * _ms(old), (nn, _ms(a), _ms(old))
    assert _seq(a) <= _seq(old), (nn, _seq(a) - _seq(old))
    assert "cpc_outlook_curves" not in _seq(a)
    assert "Index Only Scan Backward using uniq_coc_row" in a or nn == "05"


# ═══════════════════════════════════════════════════════════════════════════
# P6 — the flip is the constant
# ═══════════════════════════════════════════════════════════════════════════

def test_P6_pinned_to_v2_no_route_mixes_either(client, monkeypatch, pg_v2):
    v2 = {n: getattr(co, n).replace(PIN, "'cpc_curves_v2'") for n in CPC_READS}
    b = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/places", {}, sql=v2).json()
    assert [v["method_version"] for v in b["verdict_versions"]] == ["cpc_curves_v2"]
    assert b["cell_count"] == 720
    assert b["newest_written_issued_date"] == {"610temp": "2026-10-10", "814temp": "2026-10-10"}
    c = serve(client, monkeypatch, pg_v2, "/api/weather/cpc/curves/vintages",
              {"product": "814temp", **t.place_params(t.KSAN), "n": 14}, sql=v2).json()
    assert {v["curve"]["method_version"] for v in c["vintages"]} == {"cpc_curves_v2"}
    assert all(len(v["curve"]["days"]) == 7 for v in c["vintages"])
