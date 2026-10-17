"""d091770 follow-up (2026-10-17): the five river ROUTES, through TestClient.

The lane's own suite (`test_hydro_rivers_d091770.py`) runs the builders and the SQL. Not
one of its cells called a route, and the handback's §8.4 claimed the five paths were
"asserted registered on `main.app`" with no assertion anywhere behind it. These cells are
that assertion, and the contract CLAUDE.md's D-09-25-75 holds a polled route to:

  T   the statement timeout is the FIRST statement on the connection — all five routes
  M   a memo hit makes NO reads at all, and serves the same body
  B   an unknown river is 400; a window over 14 days is 400, never clipped. Which route
      carries which parameter is asserted too: `/index` takes no `river`, and
      `/stations` and `/gate` take no window.
  A   each of the five paths is registered on `main.app`, GET, with its own endpoint,
      and nothing else is mounted under the prefix
  S   the follow-up's stated absences are in the bodies the routes really serve: the
      gate's item set and the scope of its verdict, the item that can never settle
      (MCN), `/index`'s entries-are-walks, and `?river=` naming ONE walk

Everything runs against `load_bank_d091770`'s throwaway Postgres with `main._pool`
swapped for `load_bank_d091673.PgPool`, which records every `(sql, params)` the routes
execute — so "the timeout is first" and "a memo hit reads nothing" are read off the
statements themselves, not inferred from a body.
"""

from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]

import load_bank_d091673 as lb673          # noqa: E402  (PgPool)
import load_bank_d091770 as lb              # noqa: E402

import hydro_rivers as hr                   # noqa: E402
import main                                 # noqa: E402

P = "/api/hydro/rivers"
COLUMBIA = "nhdplus_hr_columbia_gcl_bon"
WILLAMETTE = "nhdplus_hr_willamette_hcr_columbia"
SNAKE = "nhdplus_hr_snake_lwg_columbia"
SACRAMENTO = "nhdplus_hr_sacramento_shasta_delta"

CACHES = ("_hydro_rivers_index_cache", "_hydro_rivers_stations_cache",
          "_hydro_rivers_flow_cache", "_hydro_rivers_gate_cache",
          "_hydro_rivers_reaches_cache")

# The window the values bank covers, as the routes take it.
WIN = f"from={lb.WIN_FROM.date()}T00:00:00Z&to={lb.WIN_TO.date()}T03:00:00Z"

# (name, path) — the five, each with the parameters it actually takes.
FIVE = [
    ("index", f"{P}/index"),
    ("stations", f"{P}/stations?river=columbia"),
    ("flow", f"{P}/flow?river=columbia&{WIN}"),
    ("gate", f"{P}/gate?river=columbia"),
    ("reaches", f"{P}/reaches?river=columbia&{WIN}"),
]
WITH_RIVER = [p for n, p in FIVE if n != "index"]
WITH_WINDOW = [f"{P}/flow", f"{P}/reaches"]

TIMEOUT_STATEMENT = "SET LOCAL statement_timeout = '5s'"

pytestmark = pytest.mark.skipif(lb.initdb_path() is None,
                                reason="no local initdb to build the throwaway cluster")


@pytest.fixture(scope="module")
def pg():
    pytest.importorskip("psycopg")
    with lb.cluster() as conn:
        yield conn


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _cold_memos(monkeypatch):
    """Every cell starts with cold memos and the bank's own cut as `now`."""
    for c in CACHES:
        getattr(main, c).clear()
    monkeypatch.setattr(main, "_utcnow", lambda: lb.CUT)
    yield
    for c in CACHES:
        getattr(main, c).clear()


def serve(client, monkeypatch, conn, path, status=200):
    """One request on a FRESH recording pool, memos cold. Returns (body, pool)."""
    for c in CACHES:
        getattr(main, c).clear()
    pool = lb673.PgPool(conn)
    monkeypatch.setattr(main, "_pool", pool)
    r = client.get(path)
    assert r.status_code == status, r.text
    return r.json(), pool


def strip_cache(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "cache"}


# ─────────────────────────────── T  the statement timeout ─────────────────────

@pytest.mark.parametrize("name,path", FIVE, ids=[n for n, _ in FIVE])
def test_t_the_timeout_is_the_first_statement_on_the_connection(
        client, monkeypatch, pg, name, path):
    """D-09-25-75: every read carries a statement timeout. It is worth nothing unless it
    is SET before the first read, so that is what is asserted — the first statement on
    the connection, by position, not merely present somewhere."""
    body, pool = serve(client, monkeypatch, pg, path)
    assert pool.calls, f"{name} took no statement at all"
    assert pool.calls[0][0] == TIMEOUT_STATEMENT, pool.calls[0][0]
    assert pool.calls[0][1] is None
    # Exactly one per connection, and every other statement comes after it.
    assert [c[0] for c in pool.calls].count(TIMEOUT_STATEMENT) == 1
    assert "SELECT" not in pool.calls[0][0].upper()
    assert body["cache"]["state"] == "miss"


# ─────────────────────────────────── M  the memo ──────────────────────────────

@pytest.mark.parametrize("name,path", FIVE, ids=[n for n, _ in FIVE])
def test_m_a_memo_hit_makes_no_reads(client, monkeypatch, pg, name, path):
    """The single-flight 300 s memo (D-09-25-75): the second request inside the ttl
    touches the database not once, and serves the same body."""
    for c in CACHES:
        getattr(main, c).clear()
    pool = lb673.PgPool(conn=pg)
    monkeypatch.setattr(main, "_pool", pool)

    first = client.get(path)
    assert first.status_code == 200, first.text
    after_first = len(pool.calls)
    assert after_first > 0

    second = client.get(path)
    assert second.status_code == 200, second.text
    assert len(pool.calls) == after_first, (
        f"{name}: the memo hit took {len(pool.calls) - after_first} statement(s)")
    assert second.json()["cache"]["state"] == "fresh"
    assert second.headers["X-Cache"] == "hit"
    assert strip_cache(second.json()) == strip_cache(first.json())


# ──────────────────────────────── B  the 400s ─────────────────────────────────

@pytest.mark.parametrize("path", WITH_RIVER)
def test_b_an_unknown_river_is_400(client, monkeypatch, pg, path):
    base = path.split("?")[0]
    rest = "&".join(q for q in path.split("?")[1].split("&") if not q.startswith("river="))
    body, _pool = serve(client, monkeypatch, pg,
                        f"{base}?river=rhine" + (f"&{rest}" if rest else ""), status=400)
    assert "no banked walk is named 'rhine'" in body["detail"]
    assert "/api/hydro/rivers/index" in body["detail"]


def test_b_no_river_is_400_and_an_ambiguous_one_names_the_walks(client, monkeypatch, pg):
    """No `river` at all is 400 with the reason. An ambiguous one is 400 with the walks
    NAMED — which of them compose one river is the registry's, not this route's
    (D-09-25-247 §3). No value in this bank is ambiguous (each of the four walks has its
    own key, tag and first segment), so that branch is exercised on the resolver with a
    two-walk ledger rather than faked into the bank."""
    body, _pool = serve(client, monkeypatch, pg, f"{P}/gate?river=", status=400)
    assert body["detail"] == "river is required"

    code, candidates, why = hr.resolve_river(
        "a", {"walks": {"nhdplus_hr_a_x": {}, "nhdplus_hr_a_y": {}}, "records": {}})
    assert code is None
    assert candidates == ["nhdplus_hr_a_x", "nhdplus_hr_a_y"]
    assert "'a' names 2 banked walks" in why
    assert "the geo.rivers.v1 registry's, not this route's (D-09-25-247 §3)" in why


@pytest.mark.parametrize("base", WITH_WINDOW)
def test_b_a_window_over_fourteen_days_is_400(client, monkeypatch, pg, base):
    body, _pool = serve(client, monkeypatch, pg,
                        f"{base}?river=columbia&from=2026-09-01&to=2026-10-17",
                        status=400)
    assert "at most 14 are served" in body["detail"]
    assert "46 days" in body["detail"]
    # Refused, never clipped: no body is served with a shortened window.
    assert "window_days" not in body


def test_b_index_takes_no_river_and_stations_and_gate_take_no_window(
        client, monkeypatch, pg):
    """What each route does NOT take. `/index` lists every walk, so a `river=` on it is
    not an unknown river — it is not a parameter at all, and FastAPI ignores it. Nor do
    `/stations` and `/gate` carry a window, so `from`/`to` on them are ignored too; the
    14-day refusal above is the two routes that read a window."""
    body, _pool = serve(client, monkeypatch, pg, f"{P}/index?river=rhine")
    assert body["count"] == 4 and body["walk_count"] == 4
    for base in (f"{P}/stations", f"{P}/gate"):
        body, _pool = serve(client, monkeypatch, pg,
                            f"{base}?river=columbia&from=2026-09-01&to=2026-10-17")
        assert body["walk"] == COLUMBIA
        assert "from" not in body and "window_days" not in body


# ───────────────────────── A  registered on main.app ──────────────────────────

def test_a_each_route_is_registered_on_main_app():
    """The assertion the handback claimed and never made (§8.4)."""
    by_path = {r.path: r for r in main.app.routes if isinstance(r, APIRoute)}
    for name, path in FIVE:
        p = path.split("?")[0]
        assert p in by_path, f"{p} is not registered on main.app"
        route = by_path[p]
        assert route.methods == {"GET"}, (p, route.methods)
        assert route.endpoint.__name__ == f"hydro_rivers_{name}"
        assert route.endpoint.__module__ == "main"
    # Nothing else is mounted under the prefix, and the prefix is the one the module
    # names: five routes, no more.
    under = sorted(p for p in by_path if p.startswith(f"{P}/"))
    assert under == sorted(path.split("?")[0] for _n, path in FIVE)
    assert main.HYDRO_RIVERS_PREFIX == P
    assert main.HYDRO_RIVERS_MEMO_TTL == 300.0
    assert main.HYDRO_RIVERS_STATEMENT_TIMEOUT == "5s"


def test_a_every_river_route_answers_through_its_own_memo():
    """One cache per route, each the module's own: a hit on one is never a hit on
    another (the memo key is the route's key, not the river's alone)."""
    caches = [getattr(main, c) for c in CACHES]
    assert len({id(c) for c in caches}) == 5
    assert [c.ttl for c in caches] == [300.0] * 5
    assert [c.name for c in caches] == [
        "hydro/rivers/index", "hydro/rivers/stations", "hydro/rivers/flow",
        "hydro/rivers/gate", "hydro/rivers/reaches"]


# ───────────────── S  the follow-up's stated absences, as served ──────────────

# The sentence D-09-25-252 §5 asks this route to state, pinned here verbatim: a later
# edit to it has to be deliberate, and when pantry lane d091790 banks the placed set
# this cell is the one that says the sentence must go.
ITEMS_SENTENCE = (
    "items are the banked records on this walk, not the crosswalk's placed set the "
    "pantry gates (scripts/nwm_reach_gate.py); the placed set is not banked in Neon "
    "(D-09-25-252 §5, pantry lane d091790)")


def test_s_gate_states_its_item_set_beside_the_verdict(client, monkeypatch, pg):
    body, _pool = serve(client, monkeypatch, pg, f"{P}/gate?river=columbia")

    # The absence sits beside the verdict, in the house shape, with the ruled sentence.
    assert body["items_absence"] == {"absence": "not_the_crosswalks_placed_set",
                                     "why": ITEMS_SENTENCE}
    assert hr.GATE_ITEMS_WHY == ITEMS_SENTENCE
    assert body["drawable"] is False and body["line"]
    assert body["items_are"].startswith("the records the ledger anchors to this walk")

    # The verdict's own sentence claims nothing about the pantry, and the scope of what
    # it does claim is stated beside it.
    assert "pantry" not in body["line"].lower()
    assert body["verdict_absence"]["absence"] == "not_the_pantrys_verdict"
    why = body["verdict_absence"]["why"]
    assert "it is this API's verdict over this API's item set" in why
    assert "NOT the pantry's gate verdict" in why
    assert "nothing here claims the pantry's" in why
    # The ported FUNCTION is still named as ported — it is; the item set is not.
    assert "THE FUNCTION ported byte for byte" in body["rule"]
    assert "not its item set" in body["rule"]

    # And the two sets really do differ, in both directions, on this bank: the API's
    # items are the records anchored to the walk; the pantry's are the crosswalk's
    # placed dams, as its own d091752 dry run printed them.
    api_items = [i["name"] for i in body["items"]]
    assert api_items == ["GCL", "WAN", "PRD", "MCN", "BON"]
    printed = lb.gate_fixture()["reaches"][
        hr.LAYER_REACH["nwm_crosswalk_columbia"]]["decided_by"]
    pantry_items = [line.split(" ")[1].rstrip(":") for line in printed]
    assert pantry_items == ["GCL", "CHJ", "MCN", "JDA", "TDA", "BON"]
    assert set(pantry_items) - set(api_items) == {"CHJ", "JDA", "TDA"}
    assert set(api_items) - set(pantry_items) == {"WAN", "PRD"}


def test_s_gate_says_which_item_can_never_settle(client, monkeypatch, pg):
    """MCN serves no `cwms_outflow_hourly` series, so no receipt can ever be written
    under its subject. The item says so, and says what it blocks: the verdict over THIS
    route's set. (It is an item of the pantry's placed set too — see the handback's
    correction — so this says nothing about the pantry's gate either way.)"""
    body, _pool = serve(client, monkeypatch, pg, f"{P}/gate?river=columbia")
    by_name = {i["name"]: i for i in body["items"]}

    mcn = by_name["MCN"]
    assert mcn["has_receipt"] is False
    assert mcn["why"] == hr.GATE_ARBITER["Q4"][1]
    assert mcn["line"] == ("Q4 MCN: no banked release (cwms_outflow_hourly holds no "
                           "series for it)")
    ab = mcn["item_absence"]
    assert ab["absence"] == "no_banked_arbiter_series"
    assert ab["why"].startswith("MCN serves no cwms_outflow_hourly row in the bank, so "
                                "no receipt can be written under its subject and this "
                                "item can never read settled.")
    assert "blocks the verdict stated here, which is over THIS ROUTE's item set only" \
        in ab["why"]
    assert "no claim about the pantry's gate" in ab["why"]

    # The item that HAS a release and only wants a receipt carries no such absence: the
    # two absences are different things and are not collapsed.
    assert by_name["GCL"]["item_absence"] is None
    assert by_name["GCL"]["why"] == hr.NO_GATE_RECEIPT_WHY
    # In this banked slice WAN and BON hold no release either (the slice banks three
    # series); live, both publish one — so the absence is per item, off the bank, never
    # a list of names in the code.
    assert {n for n, i in by_name.items() if i["item_absence"]} == {"WAN", "MCN", "BON"}


def test_s_gate_serves_no_forecast_and_no_dead_cycle_read(client, monkeypatch, pg):
    """`FORECAST_CYCLE_SQL` is gone: nothing executed it. The body says why a drawable
    line would still carry no forecast, and no statement the routes take names the
    forecast dataset at all."""
    body, pool = serve(client, monkeypatch, pg, f"{P}/gate?river=columbia")
    assert body["forecast"] is None
    assert body["forecast_absence"]["absence"] == "gate_not_drawable"
    assert not hasattr(hr, "FORECAST_CYCLE_SQL")
    for sql, _params in pool.calls:
        assert "nwm_reach_forecast_hourly" not in sql
        assert "nwm_reach_analysis_hourly" not in sql


def test_s_a_drawable_gate_states_the_forecast_absence(pg):
    """The drawable branch, which no bank reaches today: with every item settled the line
    reads drawable, and the forecast is still an absence — the series it would name are
    `<feature_id>/<product>/<cycle>` and the feature_ids are in the crosswalk object.
    Built over the bank's own ledger with synthetic receipts, the builder being pure."""
    walks = [r for r in pg.execute(hr.WALKS_SQL).fetchall()]
    records = [r for r in pg.execute(hr.RECORDS_SQL).fetchall()]
    names = ["GCL", "WAN", "PRD", "MCN", "BON"]
    present = {("cwms_outflow_hourly", n) for n in names}
    receipts = [{"subject": f"nwm_reach:nwm_crosswalk_columbia:{n}{sfx}",
                 "receipt_id": i, "verdict": "settled", "statistic": 1.0,
                 "n_obs": 40, "unit_tag": "cfs", "unit_settled": "cfs",
                 "measured_at": lb.CUT}
                for i, (n, sfx) in enumerate(
                    [(n, s) for n in names for s in ("", ":no_da")])]
    gate = hr.build_gate("columbia", COLUMBIA, walks, records, receipts, present)

    assert gate["drawable"] is True, gate["line"]
    assert gate["line"].endswith("NWM drawable — decided by Q4 every dam: "
                                 "settled (1.0–1.0, n >= 40)")
    assert gate["forecast"] is None
    assert gate["forecast_absence"]["absence"] == "in_crosswalk_object"
    assert "no statement here can name one" in gate["forecast_absence"]["why"]
    # Drawable or not, the item set is still not the pantry's, and it still says so.
    assert gate["items_absence"]["why"] == ITEMS_SENTENCE
    assert gate["verdict_absence"]["absence"] == "not_the_pantrys_verdict"


def test_s_index_says_its_entries_are_walks(client, monkeypatch, pg):
    """The index lists WALKS. `?river=willamette` reaching one of the basin's nine walks
    was the review's finding (c); this is the half of the cure that `/index` owes."""
    body, _pool = serve(client, monkeypatch, pg, f"{P}/index")

    assert body["entries_are"] == ("one per banked and published WALK, "
                                   "not one per river")
    assert body["entries_absence"]["absence"] == "river_structure_not_banked"
    why = body["entries_absence"]["why"]
    assert why == hr.STRUCTURE_NOT_BANKED_WHY
    assert "D-09-25-252 §5, pantry lane d091790" in why
    assert "are NOT banked in Neon" in why
    assert "nine walks are nine entries" in why
    assert body["not_served"]["which walks compose one river"] == why

    # count IS the walk count, and every entry is one walk.
    assert body["count"] == body["walk_count"] == 4
    assert [e["walks"][0]["layer_code"] for e in body["rivers"]] == sorted(
        [COLUMBIA, SNAKE, SACRAMENTO, WILLAMETTE])
    assert all(len(e["walks"]) == 1 for e in body["rivers"])


@pytest.mark.parametrize("requested,matched,matched_by", [
    ("columbia", COLUMBIA, "the river tag this walk's own records carry"),
    ("columbia_gcl_bon", COLUMBIA, "the walk's key"),
    (COLUMBIA, COLUMBIA, "the walk's layer_code"),
    ("willamette", WILLAMETTE, "an unambiguous first segment of the walk's key"),
    ("sacramento", SACRAMENTO, "an unambiguous first segment of the walk's key"),
])
def test_s_river_states_that_it_matched_one_walk(client, monkeypatch, pg, requested,
                                                 matched, matched_by):
    body, _pool = serve(client, monkeypatch, pg, f"{P}/stations?river={requested}")
    m = body["river_match"]
    assert (m["requested"], m["matched"]) == (requested, matched)
    assert m["matched_by"].startswith(matched_by)
    assert m["is"] == "one walk, not a river"
    assert m["absence"] == "river_structure_not_banked"
    assert m["why"] == hr.RIVER_MATCH_WHY
    assert "?river=willamette answers for willamette_hcr_columbia by itself, never for " \
           "the basin's nine walks" in m["why"]
    # The walks it cannot compose are NAMED, not composed.
    others = sorted({COLUMBIA, SNAKE, SACRAMENTO, WILLAMETTE} - {matched})
    assert m["other_banked_walks"] == others
    assert m["other_banked_walks_count"] == 3
    assert m["composed_by"]["schema"] == "geo.rivers.v1"


@pytest.mark.parametrize("name,path", [(n, p) for n, p in FIVE if n != "index"],
                         ids=[n for n, _ in FIVE if n != "index"])
def test_s_every_river_route_states_the_match(client, monkeypatch, pg, name, path):
    """Every route that takes `?river=` carries it — the gate's `no_crosswalk` body
    included, which returns before the gate is computed at all."""
    body, _pool = serve(client, monkeypatch, pg, path)
    assert body["river_match"]["matched"] == COLUMBIA
    assert body["river_match"]["why"] == hr.RIVER_MATCH_WHY

    willamette = path.replace("river=columbia", "river=willamette")
    body, _pool = serve(client, monkeypatch, pg, willamette)
    assert body["river_match"]["matched"] == WILLAMETTE
    assert body["walk"] == WILLAMETTE
