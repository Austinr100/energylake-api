"""d091691 — the CPC API reads one method version, so v2 rows beside v1 change nothing.

  P0  the local bank is d091679's: main's SQL and the pinned SQL both serve
      d091679's banked bodies over it (see WHOLE below)
  P1  cpc_curves_v2 rows beside the banked v1 rows (tests/load_bank_d091691.py:
      v2 window rows and days at every banked issuance, v2 verdict cells for
      every cell, a v2-only issuance newer than every v1 one): every route
      returns exactly main's body, byte for byte (main's SQL over the same
      bank without the v2 rows, through Postgres), and d091679's vectors

WHOLE. d091679's vectors were served over JSON fixtures, where a whole-valued
DOUBLE PRECISION (min_n_eff 30, a percentile of 65) is a JSON int; Postgres
hands psycopg a float, which the route prints 30.0. That is the only byte
difference between a vector and what main serves from Postgres (measured: 210
such numbers in body_curves_ksan.json, none else). So "byte for byte" is held
against main's own bytes, and against d091679's vectors with `N.0` read as `N`.
The memo's clock (built_at, build_seconds) is masked everywhere.
  P2  main's SQL (the pin removed) over the same bank: days duplicated on
      /curves and /curves/vintages, cells duplicated on /places, the v2-only
      issuance served as the newest (the red on main)
  P3  the v2-only newer issuance is not /places' (or /curves') newest
  P4  every SQL statement in cpc_outlooks.py that names cpc_outlook_curves,
      cpc_curve_verdicts or v_cpc_curves_drawable pins every reference to
      CPC_METHOD_VERSION
  P5  the Neon plans: each pinned read re-planned beside main's, index reads,
      within 3x of d091679's timing

The bank tests run the routes' own statements on a throwaway local Postgres
(pantry 286 + 298's DDL verbatim) through load_bank_d091673.PgPool; they skip
where no initdb is installed. The rehearsal is
docs/receipts/cpc-api-method-pin-d091691/rehearse.py.
"""

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

import cpc_outlooks as co
import load_bank_d091673 as lb
import load_bank_d091691 as bank
import main

ROOT = pathlib.Path(__file__).resolve().parents[1]
D091679 = ROOT / "docs" / "receipts" / "cpc-outlooks-d091679"
RECEIPTS = ROOT / "docs" / "receipts" / "cpc-api-method-pin-d091691"

# main 891de2e's statements, as d091679 pinned them (its test_T8 held them equal)
MAIN_SQL = json.loads((D091679 / "pinned_sql.json").read_text())
PINNED_READS = ("CURVES_SQL", "PLACES_SQL", "PLACES_NEWEST_SQL")
RELATIONS = ("cpc_outlook_curves", "cpc_curve_verdicts", "v_cpc_curves_drawable")

CACHES = ("_cpc_places_cache", "_cpc_curves_cache", "_cpc_vintages_cache",
          "_cpc_outlooks_cache")

KSAN = {"place_kind": "station", "place": "USW00023188"}
KDEN = {"place_kind": "station", "place": "USW00003017"}
PNW_POP = {"place_kind": "region", "place": "pnw", "weighting": "population"}

# d091679's banked bodies that read the three relations (/outlooks reads none
# of them: d091679's own test serves its body; test_P4 holds that it names none)
BODIES = [
    ("body_curves_ksan.json", "/api/weather/cpc/curves", KSAN),
    ("body_curves_pnw_population.json", "/api/weather/cpc/curves", PNW_POP),
    ("body_curves_kden.json", "/api/weather/cpc/curves", KDEN),
    ("body_vintages_814temp_ksan_n14.json", "/api/weather/cpc/curves/vintages",
     {"product": "814temp", **KSAN, "n": 14}),
    ("body_vintages_610temp_pnw_population.json", "/api/weather/cpc/curves/vintages",
     {"product": "610temp", **PNW_POP}),
    ("body_places.json", "/api/weather/cpc/places", {}),
]


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


@pytest.fixture
def main_sql(monkeypatch):
    """The pin removed: cpc_outlooks' reads as main 891de2e had them."""
    for name in PINNED_READS:
        monkeypatch.setattr(co, name, MAIN_SQL[name])


def serve(client, monkeypatch, conn, path, params):
    for c in CACHES:
        getattr(main, c).clear()
    pool = lb.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r


_CLOCK = re.compile(rb'"built_at":"[^"]*"|"build_seconds":[0-9.e-]+')


def masked(b: bytes) -> bytes:
    """The body with the memo's clock (built_at, build_seconds) masked; the
    rest of the cache block is fixed on a cold memo (miss, 0.0, 900.0, false)."""
    return _CLOCK.sub(b"<clock>", b)


_WHOLE = re.compile(rb'(?<=[:\[,])(-?\d+)\.0(?=[,}\]])')


def whole(b: bytes) -> bytes:
    """A whole-valued number printed as an int (WHOLE in the docstring)."""
    return _WHOLE.sub(rb"\1", b)


def banked_body(name):
    return (D091679 / name).read_bytes()


@pytest.fixture(scope="module")
def main_bodies(banks):
    """main 891de2e's bytes for each body: its SQL over the v1 bank (today's
    Neon: no v2 row yet), through Postgres. The memo's clock masked."""
    client = TestClient(main.app)
    saved = {n: getattr(co, n) for n in PINNED_READS}
    pool = main._pool
    out = {}
    try:
        for n in PINNED_READS:
            setattr(co, n, MAIN_SQL[n])
        main._pool = lb.PgPool(banks["v1"])
        for name, path, params in BODIES:
            for c in CACHES:
                getattr(main, c).clear()
            r = client.get(path, params=params)
            assert r.status_code == 200, r.text
            out[name] = masked(r.content)
    finally:
        for n, v in saved.items():
            setattr(co, n, v)
        main._pool = pool
        for c in CACHES:
            getattr(main, c).clear()
    return out


def product(body, prod):
    return next(p for p in body["products"] if p["product"] == prod)


def cells(body):
    return [c for p in body["places"] for pr in p["products"]
            for s in pr["seasons"] for c in s["cells"]]


# ═══════════════════════════════════════════════════════════════════════════
# P0 — the local bank is d091679's
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,path,params", BODIES)
@pytest.mark.parametrize("sql", ["main", "pinned"])
def test_P0_the_local_v1_bank_serves_d091679s_bodies(banks, client, monkeypatch, name, path,
                                                     params, sql):
    if sql == "main":
        for n in PINNED_READS:
            monkeypatch.setattr(co, n, MAIN_SQL[n])
    r = serve(client, monkeypatch, banks["v1"], path, params)
    got, want = masked(r.content), masked(banked_body(name))
    assert whole(got) == whole(want)
    strip = lambda b: {k: v for k, v in json.loads(b).items() if k != "cache"}
    assert strip(r.content) == strip(banked_body(name))


def test_P0_whole_numbers_are_the_only_difference_from_the_vectors(main_bodies):
    """Measured, so the WHOLE reading cannot hide another difference: main's
    bytes and the vector differ, and only by `N.0` against `N`."""
    diff = {n: len(_WHOLE.findall(b)) - len(_WHOLE.findall(masked(banked_body(n))))
            for n, b in main_bodies.items()}
    assert diff == {"body_curves_ksan.json": 210, "body_curves_pnw_population.json": 94,
                    "body_curves_kden.json": 0, "body_vintages_814temp_ksan_n14.json": 1718,
                    "body_vintages_610temp_pnw_population.json": 272,
                    "body_places.json": 86}
    for n, b in main_bodies.items():
        assert whole(b) == whole(masked(banked_body(n))), n


def test_P0_the_v2_rows_pass_pantrys_checks_and_the_view_serves_them(banks):
    """The constructed rows were inserted under 298's CHECKs (an insert that
    broke one would have raised), and the view returns both vintages: the
    problem the pin answers is real on this bank."""
    c = banks["v2"]
    got = {r["method_version"]: r["n"] for r in c.execute(
        "SELECT method_version, count(*) AS n FROM v_cpc_curves_drawable GROUP BY 1")}
    assert set(got) == {bank.V1, bank.V2} and got[bank.V2] > 0
    kden = c.execute("SELECT DISTINCT label FROM v_cpc_curves_drawable "
                     "WHERE place = 'USW00003017'").fetchall()
    assert kden and all(r["label"].endswith(", on 24 base years, not CPC's 30") for r in kden)
    n = c.execute("SELECT count(*) AS n FROM cpc_curve_verdicts").fetchone()["n"]
    assert n == 1440


def test_P0_the_view_is_neons(banks):
    """pantry 298's view text, as created here, deparses to the definition Neon
    holds: md5(pg_get_viewdef) read on Neon production 2026-10-10 ~14:05Z."""
    r = banks["v1"].execute("SELECT md5(pg_get_viewdef('v_cpc_curves_drawable'::regclass)) "
                            "AS m").fetchone()
    assert r["m"] == "66966f907581836370275d209d564a67"


# ═══════════════════════════════════════════════════════════════════════════
# P1 — v2 beside v1: every body is main's, byte for byte
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name,path,params", BODIES)
@pytest.mark.parametrize("state", ["v2", "v2_beside_only"])
def test_P1_v2_rows_beside_v1_change_no_body(banks, main_bodies, client, monkeypatch, name,
                                             path, params, state):
    r = serve(client, monkeypatch, banks[state], path, params)
    assert masked(r.content) == main_bodies[name]                  # main's bytes, exactly
    assert whole(masked(r.content)) == whole(masked(banked_body(name)))   # d091679's vector


def test_P1_no_v2_word_reaches_a_body(banks, client, monkeypatch):
    for name, path, params in BODIES:
        text = serve(client, monkeypatch, banks["v2"], path, params).text
        words = (bank.V2, bank.V2_HASH, bank.V2_BACKTEST, "base years, not CPC")
        assert [w for w in words if w in text] == [], name     # a short diff on a 629 KB body


# ═══════════════════════════════════════════════════════════════════════════
# P2 — the pin removed: what main serves over the same bank (the red on main)
# ═══════════════════════════════════════════════════════════════════════════

def test_P2_main_duplicates_days_on_curves(banks, client, monkeypatch, main_sql):
    """v2 beside every banked issuance: two window rows per issuance, each
    bringing its own version's view rows, so every frame has twice the days."""
    body = serve(client, monkeypatch, banks["v2_beside_only"], "/api/weather/cpc/curves",
                 KSAN).json()
    for prod, n in (("610temp", 5), ("814temp", 7)):
        days = product(body, prod)["vintage"]["curve"]["days"]
        assert len(days) == 2 * n, prod
        assert len({d["day_index"] for d in days}) == n
    want = json.loads(banked_body("body_curves_ksan.json"))
    assert len(product(want, "814temp")["vintage"]["curve"]["days"]) == 7


def test_P2_main_duplicates_days_on_vintages(banks, client, monkeypatch, main_sql):
    body = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/curves/vintages",
                 {"product": "814temp", **KSAN, "n": 14}).json()
    doubled = [v for v in body["vintages"] if v["curve"] and len(v["curve"]["days"]) == 14]
    assert len(doubled) == 13          # every banked issuance in the window but the oldest
    assert body["issued_dates"][-1] == bank.NEWER["814temp"].isoformat()


def test_P2_main_duplicates_cells_on_places(banks, client, monkeypatch, main_sql):
    body = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/places", {}).json()
    assert body["cell_count"] == 1440 and len(cells(body)) == 1440
    assert len(body["verdict_versions"]) == 2
    ksan = next(p for p in body["places"] if p["place"] == KSAN["place"])
    son = next(s for s in product(ksan, "814temp")["seasons"] if s["season"] == "SON")
    assert [c["strength"] for c in son["cells"]] == ["weak", "weak", "moderate", "moderate",
                                                     "strong", "strong"]


def test_P2_main_serves_the_v2_only_issuance_as_newest(banks, client, monkeypatch, main_sql):
    body = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/places", {}).json()
    assert body["newest_written_issued_date"] == {
        p: d.isoformat() for p, d in bank.NEWER.items()}
    cur = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/curves", KDEN).json()
    # KDEN: main draws the v2 curve where d091679's body says not_written
    c = product(cur, "610temp")["vintage"]["curve"]
    assert c["method_version"] == bank.V2 and "on 24 base years" in c["label"]


@pytest.mark.parametrize("name,path,params", BODIES)
def test_P2_main_changes_every_body(banks, main_bodies, client, monkeypatch, main_sql, name,
                                    path, params):
    r = serve(client, monkeypatch, banks["v2"], path, params)
    assert masked(r.content) != main_bodies[name]


# ═══════════════════════════════════════════════════════════════════════════
# P3 — the v2-only newer issuance is not the newest
# ═══════════════════════════════════════════════════════════════════════════

def test_P3_the_v2_only_issuance_is_not_places_newest(banks, client, monkeypatch):
    c = banks["v2"]
    newest = {r["product"]: r["d"].isoformat() for r in c.execute(
        "SELECT product, max(issued_date) AS d FROM cpc_outlook_curves GROUP BY 1")}
    assert newest == {p: d.isoformat() for p, d in bank.NEWER.items()}   # it is on the bank
    body = serve(client, monkeypatch, c, "/api/weather/cpc/places", {}).json()
    assert body["newest_written_issued_date"] == {"610temp": "2026-10-09",
                                                  "814temp": "2026-10-08"}
    dates = {pr["newest_curve"]["issued_date"] for p in body["places"]
             for pr in p["products"] if pr["newest_curve"]}
    assert dates == {"2026-10-09", "2026-10-08"}
    assert {pr["newest_curve"]["method_version"] for p in body["places"]
            for pr in p["products"] if pr["newest_curve"]} == {bank.V1}


@pytest.mark.parametrize("params", [KSAN, KDEN, PNW_POP])
def test_P3_nor_curves_newest(banks, client, monkeypatch, params):
    body = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/curves", params).json()
    assert [p["newest_written_issued_date"] for p in body["products"]] == [
        "2026-10-09", "2026-10-08"]
    vint = serve(client, monkeypatch, banks["v2"], "/api/weather/cpc/curves/vintages",
                 {"product": "610temp", **params, "n": 14}).json()
    assert bank.NEWER["610temp"].isoformat() not in vint["issued_dates"]


# ═══════════════════════════════════════════════════════════════════════════
# P4 — the source: every reference to the three relations carries the pin
# ═══════════════════════════════════════════════════════════════════════════

_REF = re.compile(r"\b(FROM|JOIN)\s+(" + "|".join(RELATIONS) + r")\b(?:\s+(\w+))?", re.I)
_NOT_ALIAS = {"where", "on", "join", "left", "cross", "order", "group", "limit", "offset"}


def unpinned_refs(sql: str, version: str) -> list[str]:
    """Each FROM/JOIN of the three relations whose alias does not carry
    `<alias>.method_version = '<version>'` in the statement (an unaliased
    reference cannot carry it, so it counts)."""
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


def test_P4_one_constant():
    assert co.CPC_METHOD_VERSION == "cpc_curves_v1"
    src = (ROOT / "cpc_outlooks.py").read_text()
    assert src.count("'cpc_curves_v1'") == 0 and src.count('"cpc_curves_v1"') == 1
    assert "CPC_METHOD_VERSION" in src.split("_PIN =")[1].splitlines()[0]


def test_P4_every_statement_naming_the_relations_carries_the_pin():
    named = {n: s for n, s in _statements(co).items()
             if any(re.search(rf"\b{r}\b", s) for r in RELATIONS)}
    assert set(named) == set(PINNED_READS)
    for n, s in named.items():
        assert _REF.search(s), n
        assert unpinned_refs(s, co.CPC_METHOD_VERSION) == [], n
    # the reads that name none of them: /outlooks
    for n in set(_statements(co)) - set(named):
        assert not any(r in getattr(co, n) for r in RELATIONS), n


def test_P4_every_reference_is_counted():
    """Five references in CURVES_SQL (ci, w, cv, vv, dv), three in PLACES_SQL
    (hv, lv, v), two in PLACES_NEWEST_SQL (lc, c): none slips past the rule."""
    counts = {n: len(_REF.findall(flat(getattr(co, n)))) for n in PINNED_READS}
    assert counts == {"CURVES_SQL": 5, "PLACES_SQL": 3, "PLACES_NEWEST_SQL": 2}


def test_P4_main_fails_the_source_rule():
    """The same rule over main 891de2e's statements: every one is unpinned."""
    for n in PINNED_READS:
        assert unpinned_refs(MAIN_SQL[n], "cpc_curves_v1"), n


# ═══════════════════════════════════════════════════════════════════════════
# P5 — the plans on Neon
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


def test_P5_the_receipts_ran_the_pinned_and_main_sql():
    blocks = EXPLAIN.findall((RECEIPTS / "explains.sql").read_text())
    assert {t for t, *_ in blocks} == {f"{s}0{i}" for s in "AB" for i in range(1, 7)}
    plans = _plans(RECEIPTS / "plans_raw.txt")
    for tag, name, params, stmt in blocks:
        p = json.loads(params)
        sql = flat(getattr(co, name)) if tag[0] == "A" else MAIN_SQL[name]
        want = re.sub(r"%\((\w+)\)s", lambda m: _lit(p[m.group(1)]), sql)
        assert flat(stmt) == "EXPLAIN (ANALYZE, BUFFERS) " + want, tag
        assert plans[tag] and all("Execution Time" in x for x in plans[tag]), tag
    assert json.loads((RECEIPTS / "pinned_sql.json").read_text()) == _statements(co)


D091679_TAG = {"01": "C01", "02": "C02", "03": "C03", "04": "C04", "05": "P01", "06": "P02"}


def test_P5_index_reads_within_three_times_d091679():
    """STOP-P: no read becomes a scan of a whole table, or more than 3x its
    d091679 timing. Warm (the last run of each) against d091679's receipt;
    cpc_curve_verdicts (720 rows, 51 pages) was seq-scanned by main too."""
    ours = _plans(RECEIPTS / "plans_raw.txt")
    theirs = _plans(D091679 / "plans_raw.txt")
    for n, t in D091679_TAG.items():
        a, b = ours[f"A{n}"][-1], ours[f"B{n}"][-1]
        assert _ms(a) <= 3 * _ms(theirs[t][-1]), (n, _ms(a), _ms(theirs[t][-1]))
        assert "Seq Scan on cpc_outlook_curves" not in a, n
        assert (len(re.findall(r"Seq Scan on cpc_curve_verdicts", a))
                == len(re.findall(r"Seq Scan on cpc_curve_verdicts", b))), n
        assert ("cpc_outlook_curves" in a) == ("cpc_outlook_curves" in b), n
        if "cpc_outlook_curves" in a:
            assert "uniq_coc_row" in a, n
