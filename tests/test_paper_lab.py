"""
Tests for THE LAB PAPER DESK v0 (2026-08-06) — GET /api/lab/paper-desk.

One endpoint, one read, one payload: blotter + equity + by_node + by_play +
book. No live database. Same discipline as tests/test_paper_desk.py and
tests/test_regime.py: a tiny in-memory fake pool dispatches canned rows by
inspecting the SQL, and the arithmetic is pinned directly against the pure
builders in paper_lab.py with hand-built rows.

WHAT THESE TESTS EXIST TO PROTECT. Each is silent on the server and either
wrong or dishonest on the client, so each gets its own test:

  * THE RESPONSE CONTRACT IS PINNED, KEY FOR KEY. Lane B builds against it
    verbatim. Every stanza's required keys are asserted as an exact set, so a
    rename or a quiet drop fails the build here rather than blanking a column
    in someone's UI. Additive keys are asserted to be exactly the ones this
    lane documents as additive — nothing sneaks onto the wire unannounced.

  * VOIDS ARE THE PRODUCT. A voided position must appear in the blotter, must
    be excluded from every aggregate figure, and must be counted in
    book.n_void. It must NEVER be folded into pending — on the live table 8 of
    14 bids are voids, and counting them as pending would tell the desk it is
    waiting on settlements that are never coming.

  * A VOID-ONLY GROUP STAYS VISIBLE. LUNDY_7_N003 has 7 bids and every one of
    them is voided. If by_node drops it, half the desk's history leaves the
    page. It must appear with zeroes and its n_void.

  * STATUS PRECEDENCE. `settled` is terminal and checked FIRST, so a row
    carrying both settled = true and a voided_* reason reads "settled" — and
    the reason still ships, so the contradiction is visible rather than
    swallowed.

  * THE CURVE IS BY settled_at, NOT trade_date. This is the whole difference
    from /api/desk/equity and it is easy to "fix" back by accident. A bid taken
    on one date and settled on another must land on the SETTLEMENT date.

  * settled_at IS UTC ON THE WIRE. psycopg renders timestamptz in the session
    timezone; the curve buckets on that string's date, so a non-UTC session
    would drift a settlement a day between deployments.

  * A POSITION MARKS ONLY AT SETTLEMENT. A pending or void row carrying a
    stamped P&L must contribute NOTHING. This is the test that catches someone
    wiring a running mark into v0.

  * P&L IS READ, NEVER RECOMPUTED. A row whose stored pnl_dollars contradicts
    (settle_fmm - settle_da) x MWh must serve the STORED figure. This lane puts
    settle_da/settle_fmm on the wire right next to it, which makes the
    temptation worse, not better.

  * THE TOTALS TIE. book.settled_pnl == sum of the blotter's displayed
    pnl_dollars == the curve's last cumulative_pnl. A desk that adds up the
    visible column and gets a different answer stops trusting the page.

  * THE COUNTS TIE. n_settled + n_pending + n_void == len(blotter), always.

  * NOTES ARE NOT BIDS. The journal is 24 prose notes and 14 bids. A note
    reaching the blotter would inflate the position count with prose. Asserted
    in BOTH places the filter lives — the SQL predicate and bid_rows.

  * THE PLAY KEY IS LIVE, AND ITS ABSENCE IS HONEST. The writer took the
    one-field fix filed by the /api/desk/by-play handoff. Both branches are
    pinned: a stamped key groups for real, and a row without one reads
    "unclassified" rather than being guessed at from the rationale prose.

  * ONE READ PER REQUEST, and the curve count rides it.

  * ZERO LLM, MECHANICALLY GREPPED — the house pattern from
    test_structures.py::test_no_forward_pricing_vocabulary_anywhere_in_the_module.

THE ACCEPTANCE TESTS (`test_acceptance_*`) carry the literal contents of
`paper_journal` as read out of the live Neon pantry on 2026-08-06: 38 rows, 24
notes and 14 bids spanning 2026-07-30..2026-08-05. ONE bid has settled
(entry 22, CONTROLX_1_N003, -$918.93, settled 2026-08-06T21:14:11Z by settler
version 2), FIVE are pending (awaiting_settlement) and EIGHT are voided
double-books. Every bid carries exactly 16 rows in `paper_bid_curves`.
"""

import datetime
import decimal
import inspect

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
import paper_desk as pd
import paper_lab as pl


UTC = datetime.timezone.utc
D = datetime.date
DEC = decimal.Decimal

ROUTE = "/api/lab/paper-desk"

NODE_LUNDY = "LUNDY_7_N003"
NODE_CONTROLX = "CONTROLX_1_N003"


# ---------------------------------------------------------------------------
# Fake pool — same shape as tests/test_paper_desk.py
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, router, sink, state):
        self._router, self._sink, self._state = router, sink, state
        self._rows = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._sink["queries"].append(query)
        self._sink["params"].append(params)
        self._state["attempts"] += 1
        if self._state["attempts"] <= self._state["fail_times"]:
            raise self._state["exc"]
        self._rows = self._router(query, params or {})

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, router, sink, state):
        self._router, self._sink, self._state = router, sink, state

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._router, self._sink, self._state)


class FakePool:
    def __init__(self, router, fail_times=0, exc=None):
        self._router = router
        self.sink = {"queries": [], "params": []}
        self.state = {
            "attempts": 0,
            "fail_times": fail_times,
            "exc": exc or psycopg.OperationalError(
                "SSL connection has been closed unexpectedly"),
        }

    def connection(self):
        return _FakeConn(self._router, self.sink, self.state)


@pytest.fixture
def client():
    return TestClient(main.app)


def use_rows(rows, fail_times=0, exc=None):
    """Serve `rows` to any query, applying the SQL's own kind filter so the
    route sees exactly what Postgres would return."""
    def router(query, params):
        kind = params.get("kind")
        return [r for r in rows if kind is None or r.get("kind") == kind]
    pool = FakePool(router, fail_times=fail_times, exc=exc)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builders — a banked `paper_journal` row + its curve count, as the lane's
# one query hands it back
# ---------------------------------------------------------------------------

def journal_row(entry_id, kind="bid", month=8, day=1, pnode_id=NODE_LUNDY,
                author="agent", direction="DEC", size_mw=5,
                price_limit=19.35, hour_scope="7x16", settled=False,
                unsettled_reason=None, settle_da=None, settle_fmm=None,
                pnl_per_mwh=None, pnl_dollars=None, settled_at=None,
                settled_by_version=None, rationale="DESK BID — ...",
                screen="persistence", inputs_as_of=None, curve_points=16):
    """One row as psycopg hands it back. `screen` is a convenience that stamps
    inputs_as_of.bid.screen — pass `screen=None` for a pre-fix row, or
    `inputs_as_of=` to drive the blob directly."""
    if inputs_as_of is None:
        bid = {"size_mw": size_mw, "pnode_id": pnode_id, "direction": direction,
               "persistence": {"dam_hours_7d": 146.0, "days_present": 7}}
        if screen is not None:
            bid["screen"] = screen
        inputs_as_of = {
            "map": {"map_id": "desk_reader_prebid", "version": "v2",
                    "scout_maps_id": 10},
            "bid": bid,
            "bid_screen": {"facts": ["GATE 1 OK: ..."], "bids_filed": 2},
        }
    return {
        "entry_id": entry_id,
        "kind": kind,
        "author": author,
        "trade_date": D(2026, month, day),
        "pnode_id": pnode_id,
        "direction": direction,
        "size_mw": size_mw,
        "price_limit": price_limit,
        "hour_scope": hour_scope,
        "settled": settled,
        "unsettled_reason": unsettled_reason,
        "settle_da": settle_da,
        "settle_fmm": settle_fmm,
        "pnl_per_mwh": pnl_per_mwh,
        "pnl_dollars": pnl_dollars,
        "settled_at": settled_at,
        "settled_by_version": settled_by_version,
        "rationale": rationale,
        "inputs_as_of": inputs_as_of,
        "curve_points": curve_points,
    }


_UNSET = object()   # so `settled_at=None` means null, not "use the default"


def settled_row(entry_id, pnl_dollars, pnl_per_mwh=None, settled_at=_UNSET, **kw):
    if settled_at is _UNSET:
        settled_at = datetime.datetime(2026, 8, 6, 21, 0, tzinfo=UTC)
    return journal_row(
        entry_id, settled=True, pnl_dollars=pnl_dollars,
        pnl_per_mwh=pnl_per_mwh, settled_by_version=2,
        settled_at=settled_at, **kw)


def void_row(entry_id, reason="voided_writer_alias_double_booked", **kw):
    return journal_row(entry_id, unsettled_reason=reason, **kw)


def pending_row(entry_id, reason="awaiting_settlement", **kw):
    return journal_row(entry_id, unsettled_reason=reason, **kw)


def get(client, rows):
    use_rows(rows)
    resp = client.get(ROUTE)
    assert resp.status_code == 200
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# THE PINNED CONTRACT — key for key. Lane B builds against this verbatim.
# ═══════════════════════════════════════════════════════════════════════════

TOP_LEVEL_CONTRACT = {"as_of", "blotter", "equity", "by_node", "by_play", "book"}
TOP_LEVEL_ADDITIVE = {"source", "paper_only", "derivation"}

BLOTTER_CONTRACT = {
    "entry_id", "trade_date", "pnode_id", "author", "direction", "size_mw",
    "price_limit", "hour_scope", "status", "unsettled_reason", "settle_da",
    "settle_fmm", "pnl_per_mwh", "pnl_dollars", "settled_at",
    "settled_by_version", "rationale", "inputs_as_of", "curve_points",
}
BLOTTER_ADDITIVE = {"play"}

EQUITY_CONTRACT = {"date", "cumulative_pnl"}
BY_NODE_CONTRACT = {"pnode_id", "n_settled", "n_pending", "wins", "total_pnl",
                    "avg_pnl_per_mwh"}
BY_NODE_ADDITIVE = {"n_void"}
BY_PLAY_CONTRACT = {"play", "n_settled", "wins", "total_pnl"}
BY_PLAY_ADDITIVE = {"n_pending", "n_void"}
BOOK_CONTRACT = {"settled_pnl", "n_settled", "n_pending", "n_void"}


def test_the_top_level_contract_is_exactly_what_was_pinned(client):
    body = get(client, [journal_row(1)])
    assert TOP_LEVEL_CONTRACT <= set(body), "a pinned top-level key went missing"
    assert set(body) == TOP_LEVEL_CONTRACT | TOP_LEVEL_ADDITIVE, \
        "an undocumented key reached the wire"


def test_every_blotter_row_carries_the_pinned_keys_and_only_documented_extras(client):
    body = get(client, [settled_row(1, -918.93, -11.486675),
                        void_row(2), pending_row(3)])
    assert len(body["blotter"]) == 3
    for row in body["blotter"]:
        assert BLOTTER_CONTRACT <= set(row), \
            f"a pinned blotter key went missing: {BLOTTER_CONTRACT - set(row)}"
        assert set(row) == BLOTTER_CONTRACT | BLOTTER_ADDITIVE


def test_every_aggregate_row_carries_the_pinned_keys(client):
    body = get(client, [settled_row(1, 10.0), void_row(2, pnode_id=NODE_CONTROLX)])
    for p in body["equity"]:
        assert set(p) == EQUITY_CONTRACT
    for g in body["by_node"]:
        assert BY_NODE_CONTRACT <= set(g)
        assert set(g) == BY_NODE_CONTRACT | BY_NODE_ADDITIVE
    for g in body["by_play"]:
        assert BY_PLAY_CONTRACT <= set(g)
        assert set(g) == BY_PLAY_CONTRACT | BY_PLAY_ADDITIVE
    assert set(body["book"]) == BOOK_CONTRACT


def test_the_contract_key_lists_in_the_module_match_what_is_served(client):
    """The module states the pinned keys as data; that statement must not rot."""
    body = get(client, [settled_row(1, 5.0)])
    assert set(pl.BLOTTER_CONTRACT_KEYS) | set(pl.BLOTTER_ADDITIVE_KEYS) == \
        set(body["blotter"][0])
    assert set(pl.BOOK_CONTRACT_KEYS) == set(body["book"])
    assert {"pnode_id", *pl.BY_NODE_CONTRACT_KEYS, *pl.BY_NODE_ADDITIVE_KEYS} == \
        set(body["by_node"][0])
    assert {"play", *pl.BY_PLAY_CONTRACT_KEYS, *pl.BY_PLAY_ADDITIVE_KEYS} == \
        set(body["by_play"][0])


def test_the_blotter_row_key_order_is_the_contracts_order(client):
    """Not load-bearing for a JSON consumer, but it is what was pinned, and a
    diff of the response against the brief should read cleanly."""
    body = get(client, [journal_row(1)])
    served = [k for k in body["blotter"][0] if k in BLOTTER_CONTRACT]
    assert served == list(pl.BLOTTER_CONTRACT_KEYS)


# ═══════════════════════════════════════════════════════════════════════════
# STATUS — three-valued, derived, and `settled` is terminal
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("reason,expected", [
    (None, "pending"),
    ("", "pending"),
    ("   ", "pending"),
    ("awaiting_settlement", "pending"),
    ("no_dam_price", "pending"),
    ("voided_writer_alias_double_booked", "void"),
    ("voided_settler_v1_double_booked_position", "void"),
    ("VOIDED_SHOUTING", "void"),
    ("  voided_padded  ", "void"),
])
def test_status_is_derived_from_the_reason_for_an_unsettled_row(reason, expected):
    assert pl.status_of({"settled": False, "unsettled_reason": reason}) == expected


def test_settled_is_terminal_and_beats_a_stale_void_reason(client):
    """A row carrying both must read 'settled' — and the reason must still ship,
    so the contradiction is visible on the page instead of resolved in here."""
    row = settled_row(1, 100.0, unsettled_reason="voided_writer_alias_double_booked")
    body = get(client, [row])
    assert body["blotter"][0]["status"] == "settled"
    assert body["blotter"][0]["unsettled_reason"] == \
        "voided_writer_alias_double_booked"
    assert body["book"]["n_settled"] == 1
    assert body["book"]["n_void"] == 0


def test_there_is_no_open_status_in_this_contract(client):
    """/api/desk/* calls an unsettled row with no reason OPEN. This contract
    does not — it is pending. Both lanes are right about their own vocabulary;
    what must never happen is one leaking into the other."""
    body = get(client, [journal_row(1, unsettled_reason=None)])
    assert body["blotter"][0]["status"] == "pending"
    served = {r["status"] for r in body["blotter"]}
    assert served <= {"settled", "pending", "void"}
    assert "OPEN" not in served


def test_the_unsettled_reason_is_served_verbatim_never_mapped(client):
    reason = "voided_settler_v1_double_booked_position"
    body = get(client, [void_row(1, reason=reason)])
    assert body["blotter"][0]["unsettled_reason"] == reason


# ═══════════════════════════════════════════════════════════════════════════
# VOIDS — the product, not noise
# ═══════════════════════════════════════════════════════════════════════════

def test_voids_ride_the_blotter(client):
    body = get(client, [void_row(1), void_row(2), settled_row(3, 5.0)])
    assert len(body["blotter"]) == 3
    assert sum(1 for r in body["blotter"] if r["status"] == "void") == 2


def test_a_void_is_never_counted_as_pending(client):
    """The failure this endpoint exists to prevent: 8 of 14 banked bids are
    voids, and filing them under pending tells the desk it is waiting on
    settlements that are never coming."""
    body = get(client, [void_row(1), void_row(2), pending_row(3)])
    assert body["book"] == {"settled_pnl": 0.0, "n_settled": 0,
                            "n_pending": 1, "n_void": 2}


def test_a_void_contributes_nothing_to_any_aggregate_even_with_a_stamped_pnl(client):
    """A voided position that the writer had already stamped a P&L onto must
    book nothing anywhere. Dropping both the P&L columns must change no figure."""
    with_pnl = [
        void_row(1, pnl_dollars=999.99, pnl_per_mwh=12.5,
                 settle_da=10.0, settle_fmm=22.5),
        settled_row(2, -50.0, -1.0, pnode_id=NODE_CONTROLX),
    ]
    without = [
        void_row(1),
        settled_row(2, -50.0, -1.0, pnode_id=NODE_CONTROLX),
    ]
    a, b = get(client, with_pnl), get(client, without)
    for stanza in ("equity", "by_node", "by_play", "book"):
        assert a[stanza] == b[stanza], \
            f"a voided row's stamped P&L leaked into {stanza}"
    assert a["book"]["settled_pnl"] == -50.0


def test_a_node_whose_every_bid_was_voided_still_appears(client):
    """Today that is LUNDY_7_N003: 7 bids, every one voided. Dropping it would
    take the desk's whole void history off the page."""
    body = get(client, [void_row(1, pnode_id=NODE_LUNDY),
                        void_row(2, pnode_id=NODE_LUNDY),
                        settled_row(3, 10.0, pnode_id=NODE_CONTROLX)])
    nodes = {g["pnode_id"]: g for g in body["by_node"]}
    assert NODE_LUNDY in nodes, "a void-only node must never be dropped"
    assert nodes[NODE_LUNDY] == {
        "pnode_id": NODE_LUNDY, "n_settled": 0, "n_pending": 0, "wins": 0,
        "total_pnl": 0.0, "avg_pnl_per_mwh": None, "n_void": 2}


def test_a_void_only_group_is_distinguishable_from_a_group_that_filed_nothing(client):
    """This is what n_void is FOR. Without it the row above is all zeroes and
    reads identically to a node that never traded."""
    body = get(client, [void_row(1, pnode_id=NODE_LUNDY),
                        pending_row(2, pnode_id=NODE_CONTROLX)])
    nodes = {g["pnode_id"]: g for g in body["by_node"]}
    assert nodes[NODE_LUNDY]["n_void"] == 1
    assert nodes[NODE_CONTROLX]["n_void"] == 0
    assert nodes[NODE_LUNDY] != {**nodes[NODE_CONTROLX], "pnode_id": NODE_LUNDY}


def test_a_void_only_group_sorts_last_but_stays_visible(client):
    body = get(client, [void_row(1, pnode_id=NODE_LUNDY),
                        settled_row(2, -5.0, pnode_id=NODE_CONTROLX)])
    assert [g["pnode_id"] for g in body["by_node"]] == \
        [NODE_CONTROLX, NODE_LUNDY]


# ═══════════════════════════════════════════════════════════════════════════
# THE COUNTS AND THE TOTALS TIE
# ═══════════════════════════════════════════════════════════════════════════

def test_the_three_status_counts_partition_the_blotter(client):
    body = get(client, [settled_row(1, 5.0), pending_row(2), pending_row(3),
                        void_row(4), void_row(5), void_row(6)])
    book = body["book"]
    assert book["n_settled"] + book["n_pending"] + book["n_void"] == \
        len(body["blotter"]) == 6


def test_the_book_the_blotter_column_and_the_curve_all_tie(client):
    rows = [
        settled_row(1, -918.93, -11.486675,
                    settled_at=datetime.datetime(2026, 8, 6, 21, 0, tzinfo=UTC)),
        settled_row(2, 250.55, 3.1,
                    settled_at=datetime.datetime(2026, 8, 7, 21, 0, tzinfo=UTC)),
        void_row(3), pending_row(4),
    ]
    body = get(client, rows)
    visible = sum(r["pnl_dollars"] for r in body["blotter"]
                  if r["status"] == "settled")
    assert round(visible, 2) == body["book"]["settled_pnl"] == -668.38
    assert body["equity"][-1]["cumulative_pnl"] == body["book"]["settled_pnl"]


def test_the_aggregates_sum_back_to_the_book(client):
    rows = [settled_row(1, -918.93, pnode_id=NODE_CONTROLX, screen="persistence"),
            settled_row(2, 100.0, pnode_id=NODE_LUNDY, screen="surprise"),
            void_row(3, pnode_id=NODE_LUNDY, screen="surprise")]
    body = get(client, rows)
    for stanza in ("by_node", "by_play"):
        assert round(sum(g["total_pnl"] for g in body[stanza]), 2) == \
            body["book"]["settled_pnl"]
        assert sum(g["n_settled"] for g in body[stanza]) == \
            body["book"]["n_settled"]
        assert sum(g["n_void"] for g in body[stanza]) == body["book"]["n_void"]


# ═══════════════════════════════════════════════════════════════════════════
# THE EQUITY CURVE — by settled_at, and gaps are gaps
# ═══════════════════════════════════════════════════════════════════════════

def test_the_curve_is_drawn_by_settled_at_not_trade_date(client):
    """THE difference from /api/desk/equity, and the one easy to 'fix' back by
    accident. The live settled bid was taken 2026-07-31 and settled 2026-08-06."""
    body = get(client, [settled_row(
        1, -918.93, month=7, day=31,
        settled_at=datetime.datetime(2026, 8, 6, 21, 14, 11, tzinfo=UTC))])
    assert body["blotter"][0]["trade_date"] == "2026-07-31"
    assert body["equity"] == [{"date": "2026-08-06", "cumulative_pnl": -918.93}]


def test_settled_at_is_normalized_to_utc_on_the_wire(client):
    """psycopg renders timestamptz in the session timezone. The curve buckets on
    this string's date, so a non-UTC session would drift a settlement a day."""
    pacific = datetime.timezone(datetime.timedelta(hours=-7))
    body = get(client, [settled_row(
        1, 10.0,
        settled_at=datetime.datetime(2026, 8, 6, 14, 14, 11, tzinfo=pacific))])
    assert body["blotter"][0]["settled_at"].startswith("2026-08-06T21:14:11")
    assert body["blotter"][0]["settled_at"].endswith("+00:00")
    assert body["equity"][0]["date"] == "2026-08-06"


def test_the_curve_is_cumulative_and_ascending(client):
    body = get(client, [
        settled_row(1, 100.0,
                    settled_at=datetime.datetime(2026, 8, 1, 12, 0, tzinfo=UTC)),
        settled_row(2, -30.0,
                    settled_at=datetime.datetime(2026, 8, 3, 12, 0, tzinfo=UTC)),
        settled_row(3, 5.0,
                    settled_at=datetime.datetime(2026, 8, 4, 12, 0, tzinfo=UTC)),
    ])
    assert body["equity"] == [
        {"date": "2026-08-01", "cumulative_pnl": 100.0},
        {"date": "2026-08-03", "cumulative_pnl": 70.0},
        {"date": "2026-08-04", "cumulative_pnl": 75.0},
    ]


def test_a_date_with_nothing_settled_produces_no_point(client):
    """No interpolation, no zero-fill, no carry-forward. Consecutive points are
    NOT necessarily adjacent dates — draw the gap."""
    body = get(client, [
        settled_row(1, 100.0,
                    settled_at=datetime.datetime(2026, 8, 1, 12, 0, tzinfo=UTC)),
        settled_row(2, 50.0,
                    settled_at=datetime.datetime(2026, 8, 9, 12, 0, tzinfo=UTC)),
        void_row(3), pending_row(4),
    ])
    assert [p["date"] for p in body["equity"]] == ["2026-08-01", "2026-08-09"]


def test_two_settlements_on_one_date_make_one_point(client):
    body = get(client, [
        settled_row(1, 10.0,
                    settled_at=datetime.datetime(2026, 8, 6, 1, 0, tzinfo=UTC)),
        settled_row(2, 15.0,
                    settled_at=datetime.datetime(2026, 8, 6, 23, 0, tzinfo=UTC)),
    ])
    assert body["equity"] == [{"date": "2026-08-06", "cumulative_pnl": 25.0}]


def test_a_settled_row_with_no_settled_at_is_excluded_and_counted(client):
    """It cannot be placed on a curve. It must not be parked on a made-up date
    and it must not vanish in silence."""
    body = get(client, [settled_row(1, 10.0, settled_at=None),
                        settled_row(2, 40.0)])
    assert body["equity"] == [{"date": "2026-08-06", "cumulative_pnl": 40.0}]
    assert body["derivation"]["equity"]["settled_rows_without_settled_at"] == 1
    # It still counts in the book — it settled, it just cannot be drawn.
    assert body["book"]["n_settled"] == 2
    assert body["book"]["settled_pnl"] == 50.0


def test_nothing_settled_is_an_empty_curve_never_a_zero_line(client):
    body = get(client, [void_row(1), pending_row(2)])
    assert body["equity"] == []


# ═══════════════════════════════════════════════════════════════════════════
# MARKS, SIGNS AND WINS
# ═══════════════════════════════════════════════════════════════════════════

def test_a_pending_row_with_a_stamped_pnl_books_nothing(client):
    """The test that catches someone wiring a running mark into v0."""
    body = get(client, [pending_row(1, pnl_dollars=500.0, pnl_per_mwh=6.25)])
    assert body["book"]["settled_pnl"] == 0.0
    assert body["equity"] == []
    assert body["by_node"][0]["total_pnl"] == 0.0
    assert body["by_node"][0]["avg_pnl_per_mwh"] is None


def test_pnl_is_read_never_recomputed_from_the_price_columns(client):
    """The stored figure wins even when it contradicts the displayed prices.
    Two competing sign conventions in one building is the defect this guards —
    and this lane puts settle_da/settle_fmm right next to pnl_dollars."""
    body = get(client, [settled_row(
        1, -918.93, -11.486675, settle_da=-50.359133, settle_fmm=-61.845808)])
    row = body["blotter"][0]
    assert row["pnl_dollars"] == -918.93
    assert row["settle_da"] == -50.3591 and row["settle_fmm"] == -61.8458
    assert body["book"]["settled_pnl"] == -918.93


def test_dropping_the_price_columns_changes_no_total(client):
    a = get(client, [settled_row(1, -918.93, -11.486675,
                                 settle_da=-50.359133, settle_fmm=-61.845808)])
    b = get(client, [settled_row(1, -918.93, -11.486675)])
    for stanza in ("equity", "by_node", "by_play", "book"):
        assert a[stanza] == b[stanza]


@pytest.mark.parametrize("pnl,wins", [(10.0, 1), (0.0, 0), (-10.0, 0),
                                      (0.01, 1), (None, 0)])
def test_a_win_is_a_strictly_positive_settled_pnl(client, pnl, wins):
    body = get(client, [settled_row(1, pnl)])
    assert body["by_node"][0]["wins"] == wins


def test_avg_pnl_per_mwh_is_null_with_no_samples_never_zero(client):
    """0.0 reads 'it averaged to nothing'. null reads 'nothing has settled'."""
    body = get(client, [pending_row(1), void_row(2)])
    assert body["by_node"][0]["avg_pnl_per_mwh"] is None
    assert body["by_node"][0]["total_pnl"] == 0.0, "a SUM of nothing is 0.0"


def test_avg_pnl_per_mwh_is_the_unweighted_mean_over_settled_rows(client):
    body = get(client, [settled_row(1, 100.0, 10.0, size_mw=100),
                        settled_row(2, 1.0, 2.0, size_mw=1),
                        pending_row(3, pnl_per_mwh=999.0)])
    assert body["by_node"][0]["avg_pnl_per_mwh"] == 6.0


# ═══════════════════════════════════════════════════════════════════════════
# THE PLAY KEY — live, and honest where it is absent
# ═══════════════════════════════════════════════════════════════════════════

def test_the_play_key_groups_on_the_writers_stamped_screen(client):
    """The writer took the one-field fix the /api/desk/by-play handoff filed."""
    body = get(client, [settled_row(1, 10.0, screen="persistence"),
                        void_row(2, screen="surprise"),
                        pending_row(3, screen="surprise")])
    plays = {g["play"]: g for g in body["by_play"]}
    assert set(plays) == {"persistence", "surprise"}
    assert plays["persistence"]["n_settled"] == 1
    assert plays["surprise"]["n_pending"] == 1
    assert plays["surprise"]["n_void"] == 1
    assert body["derivation"]["play"]["available"] is True
    assert body["derivation"]["play"]["unclassified_n"] == 0


def test_a_row_with_no_play_key_reads_unclassified_honestly(client):
    body = get(client, [journal_row(1, screen=None)])
    assert body["blotter"][0]["play"] == "unclassified"
    assert [g["play"] for g in body["by_play"]] == ["unclassified"]
    assert body["derivation"]["play"]["available"] is False
    assert body["derivation"]["play"]["unclassified_n"] == 1


def test_the_rationale_prose_is_never_parsed_to_key_a_play(client):
    """The screen name is in the prose under 'WHICH SCREEN FIRED'. Keying an
    aggregate off it would make the aggregate a property of the writer's
    sentence style. A row with the prose and no stamped key is unclassified."""
    prose = ("WHICH SCREEN FIRED\n  Persistence set — bound on 7 of the "
             "window's days.\n  screen persistence — the screen that set the side.")
    body = get(client, [journal_row(1, screen=None, rationale=prose)])
    assert body["blotter"][0]["play"] == "unclassified"
    assert body["blotter"][0]["rationale"] == prose, "the prose still ships"


def test_a_multi_screen_array_is_unclassified_not_silently_flattened(client):
    """The v1 shape the /api/desk handoff anticipated. v0 groups on scalars
    only rather than inventing a flattening rule the writer never agreed to."""
    body = get(client, [journal_row(1, inputs_as_of={
        "bid": {"screen": ["persistence", "surprise"]}})])
    assert body["blotter"][0]["play"] == "unclassified"


def test_the_play_lookup_is_not_re_implemented_in_this_lane(client):
    """There is ONE implementation of the play key in the building. If someone
    adds a path to paper_desk.PLAY_KEY_PATHS, this lane must pick it up."""
    searched = get(client, [journal_row(1)])["derivation"]["play"]["paths_searched"]
    assert searched == [".".join(p) for p in pd.PLAY_KEY_PATHS]


def test_a_play_group_that_is_all_voids_still_appears(client):
    body = get(client, [void_row(1, screen="surprise"),
                        settled_row(2, 5.0, screen="persistence")])
    plays = {g["play"]: g for g in body["by_play"]}
    assert plays["surprise"] == {"play": "surprise", "n_settled": 0, "wins": 0,
                                 "total_pnl": 0.0, "n_pending": 0, "n_void": 1}


# ═══════════════════════════════════════════════════════════════════════════
# NOTES ARE NOT BIDS
# ═══════════════════════════════════════════════════════════════════════════

def test_notes_never_reach_the_blotter_the_sql_filters_them(client):
    pool = use_rows([journal_row(1, kind="note"), journal_row(2, kind="bid")])
    body = client.get(ROUTE).json()
    assert pool.sink["params"][0] == {"kind": "bid"}
    assert len(body["blotter"]) == 1
    assert body["blotter"][0]["entry_id"] == 2


def test_notes_are_filtered_again_in_python_so_a_widened_query_cannot_leak(client):
    """The filter lives in two places on purpose. This asserts the second one
    by handing the pure layer a note the SQL would have excluded."""
    rows = pl.bid_rows([journal_row(1, kind="note"), journal_row(2, kind="bid")])
    assert [r["entry_id"] for r in rows] == [2]


# ═══════════════════════════════════════════════════════════════════════════
# SHAPE, ORDER, EMPTY STATES, TYPES
# ═══════════════════════════════════════════════════════════════════════════

def test_the_blotter_is_newest_first(client):
    body = get(client, [journal_row(1, day=1), journal_row(2, day=5),
                        journal_row(3, day=5), journal_row(4, day=3)])
    assert [r["entry_id"] for r in body["blotter"]] == [3, 2, 4, 1]


def test_an_empty_journal_is_empty_lists_and_a_zeroed_book_never_null(client):
    """The isOutlooks doctrine: a consumer's array guard fails on a null and
    blanks the whole surface."""
    body = get(client, [])
    assert body["blotter"] == [] and body["equity"] == []
    assert body["by_node"] == [] and body["by_play"] == []
    assert body["book"] == {"settled_pnl": 0.0, "n_settled": 0,
                            "n_pending": 0, "n_void": 0}
    assert body["derivation"]["play"]["available"] is False


def test_numeric_columns_arrive_as_decimal_and_survive_the_wire(client):
    """psycopg hands numeric back as Decimal, not float. Every money and rate
    column is driven as Decimal through the full route."""
    body = get(client, [settled_row(
        1, DEC("-918.93"), DEC("-11.486675"), size_mw=DEC("5"),
        price_limit=DEC("-0.55"), settle_da=DEC("-50.359133"),
        settle_fmm=DEC("-61.845808"))])
    row = body["blotter"][0]
    assert row["pnl_dollars"] == -918.93 and row["pnl_per_mwh"] == -11.4867
    assert row["size_mw"] == 5.0 and row["price_limit"] == -0.55
    assert body["book"]["settled_pnl"] == -918.93


def test_curve_points_is_a_count_and_a_missing_join_row_is_zero_not_null(client):
    body = get(client, [journal_row(1, curve_points=16),
                        journal_row(2, curve_points=None)])
    counts = {r["entry_id"]: r["curve_points"] for r in body["blotter"]}
    assert counts == {1: 16, 2: 0}


def test_the_rationale_and_the_inputs_blob_reach_the_wire_unreshaped(client):
    """This page is an audit surface — the whole point is that a reader can see
    what the position was taken on. The blob is served, not pruned."""
    blob = {"map": {"map_id": "desk_reader_prebid"},
            "bid": {"screen": "persistence", "rules": {"a": 1}},
            "bid_screen": {"facts": ["GATE 1 OK: ..."]}}
    body = get(client, [journal_row(1, inputs_as_of=blob,
                                    rationale="DESK BID — the whole prose")])
    assert body["blotter"][0]["inputs_as_of"] == blob
    assert body["blotter"][0]["rationale"] == "DESK BID — the whole prose"


def test_the_response_carries_an_as_of_and_names_its_source(client):
    body = get(client, [journal_row(1)])
    datetime.datetime.fromisoformat(body["as_of"])  # parses, or this raises
    assert "paper_journal" in body["source"]
    assert "paper_bid_curves" in body["source"]
    assert "no such code path exists" in body["paper_only"]


# ═══════════════════════════════════════════════════════════════════════════
# THE READ
# ═══════════════════════════════════════════════════════════════════════════

def test_one_read_per_request_and_the_curve_count_rides_it(client):
    pool = use_rows([journal_row(1), journal_row(2, pnode_id=NODE_CONTROLX)])
    assert client.get(ROUTE).status_code == 200
    assert len(pool.sink["queries"]) == 1, "the lane must issue exactly one read"
    q = pool.sink["queries"][0]
    assert "FROM paper_journal" in q
    assert "paper_bid_curves" in q, "the curve count must ride the one read"


def test_db_down_is_503_never_a_fabricated_empty_desk(client):
    """'No positions' and 'could not look' must never render the same."""
    use_rows([journal_row(1)], fail_times=99)
    assert client.get(ROUTE).status_code == 503


def test_a_dropped_connection_is_retried_once(client):
    pool = use_rows([journal_row(1)], fail_times=1)
    assert client.get(ROUTE).status_code == 200
    assert pool.state["attempts"] == 2


def test_this_lane_writes_nothing(client):
    """Read-only, mechanically. No new tables, no writes — the brief's rail."""
    src = inspect.getsource(main._lab_read_desk) + \
        inspect.getsource(main.lab_paper_desk) + main._LAB_PAPER_DESK_SQL
    for banned in ("INSERT", "UPDATE ", "DELETE", "UPSERT", "CREATE ",
                   "ALTER ", "DROP ", "TRUNCATE"):
        assert banned not in src.upper().replace("UPDATED", ""), \
            f"a write verb appeared in a read-only lane: {banned}"


# ═══════════════════════════════════════════════════════════════════════════
# ZERO LLM — grep-enforced, the house pattern
# ═══════════════════════════════════════════════════════════════════════════

_BANNED_LLM_VOCABULARY = (
    "anthropic", "openai", "claude", "gpt-", "llm", "language model",
    "chat.completions", "completions.create", "prompt", "system_prompt",
    "max_tokens", "temperature=", "embedding", "generative", "inference",
)


def test_no_language_service_vocabulary_in_the_pure_layer():
    src = inspect.getsource(pl).lower()
    for banned in _BANNED_LLM_VOCABULARY:
        assert banned not in src, f"language-service vocabulary leaked in: {banned}"


def test_no_language_service_vocabulary_in_the_route_or_its_reader():
    for fn in (main.lab_paper_desk, main._lab_read_desk):
        src = inspect.getsource(fn).lower()
        for banned in _BANNED_LLM_VOCABULARY:
            assert banned not in src, \
                f"language-service vocabulary leaked into {fn.__name__}: {banned}"


def test_the_pure_layer_stays_pure():
    """No I/O, no clock, no database, no network. It may import the sibling
    ledger module (that is the point — one implementation of the play key)."""
    src = inspect.getsource(pl)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert ("datetime" in stripped or "__future__" in stripped
                    or "paper_desk" in stripped), \
                f"unexpected import in the pure ledger layer: {stripped}"


# ═══════════════════════════════════════════════════════════════════════════
# ACCEPTANCE — the live pantry, read 2026-08-06
# ═══════════════════════════════════════════════════════════════════════════
#
# `paper_journal` held 38 rows: 24 kind='note' and 14 kind='bid'. Every scalar
# column below is the value Neon returned, verbatim. The two heavy columns are
# represented by what this lane actually reads out of them — `inputs_as_of` by
# its real `bid.screen` stamp (present on 12 of 14, absent on entries 18/19,
# which predate the writer's fix) and `rationale` by its real first line. The
# blob's byte-for-byte passthrough is pinned separately, by
# test_the_rationale_and_the_inputs_blob_reach_the_wire_unreshaped.
#
# Depth, measured 2026-08-06:
#
#   status    n   which
#   settled   1   entry 22 — CONTROLX_1_N003, -$918.93, settled 2026-08-06
#   pending   5   entries 25/28/31/34/37 — all CONTROLX, awaiting_settlement
#   void      8   entries 18/19 (settler v1 double-book) + 21/24/27/30/33/36
#                 (writer alias double-book) — the latter six are EVERY bid
#                 ever filed at LUNDY_7_N003
#
# Every bid carries exactly 16 rows in `paper_bid_curves` (HE7-22, one segment).

_VOID_SETTLER = "voided_settler_v1_double_booked_position"
_VOID_WRITER = "voided_writer_alias_double_booked"
_AWAITING = "awaiting_settlement"


def _banked_2026_08_06():
    notes = [journal_row(i, kind="note", month=7, day=12 + (i % 19),
                         pnode_id=None, direction=None, size_mw=None,
                         price_limit=None, hour_scope=None, curve_points=0,
                         rationale="MORNING PRE-BID READ — ...")
             for i in range(1, 25)]
    b = lambda eid, m, d, node, limit, **kw: journal_row(  # noqa: E731
        eid, month=m, day=d, pnode_id=node, price_limit=DEC(str(limit)),
        size_mw=DEC("5"), direction="DEC", hour_scope="7x16", author="agent",
        rationale="DESK BID — %04d-%02d-%02d — DEC 5.0 MW" % (2026, m, d), **kw)
    bids = [
        # The two pre-fix rows: 10 MW, no bid.screen stamp, voided by settler v1.
        journal_row(18, month=7, day=30, pnode_id=NODE_LUNDY, size_mw=DEC("10"),
                    price_limit=DEC("20.68"), unsettled_reason=_VOID_SETTLER,
                    screen=None, author="agent", hour_scope="7x16"),
        journal_row(19, month=7, day=30, pnode_id=NODE_CONTROLX, size_mw=DEC("10"),
                    price_limit=DEC("0.78"), unsettled_reason=_VOID_SETTLER,
                    screen=None, author="agent", hour_scope="7x16"),
        b(21, 7, 31, NODE_LUNDY, "19.35", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        # THE ONE SETTLED BID.
        b(22, 7, 31, NODE_CONTROLX, "-0.55", settled=True, screen="persistence",
          settle_da=DEC("-50.359133"), settle_fmm=DEC("-61.845808"),
          pnl_per_mwh=DEC("-11.486675"), pnl_dollars=DEC("-918.93"),
          settled_by_version=2,
          settled_at=datetime.datetime(2026, 8, 6, 21, 14, 11, 443000, tzinfo=UTC)),
        b(24, 8, 1, NODE_LUNDY, "19.35", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        b(25, 8, 1, NODE_CONTROLX, "-0.55", unsettled_reason=_AWAITING,
          screen="persistence", settled_by_version=2),
        b(27, 8, 2, NODE_LUNDY, "10.58", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        b(28, 8, 2, NODE_CONTROLX, "-9.31", unsettled_reason=_AWAITING,
          screen="persistence", settled_by_version=2),
        b(30, 8, 3, NODE_LUNDY, "5.83", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        b(31, 8, 3, NODE_CONTROLX, "-14.07", unsettled_reason=_AWAITING,
          screen="persistence", settled_by_version=2),
        b(33, 8, 4, NODE_LUNDY, "3.8", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        b(34, 8, 4, NODE_CONTROLX, "-16.1", unsettled_reason=_AWAITING,
          screen="persistence", settled_by_version=2),
        b(36, 8, 5, NODE_LUNDY, "-10.47", unsettled_reason=_VOID_WRITER,
          screen="surprise"),
        b(37, 8, 5, NODE_CONTROLX, "-30.37", unsettled_reason=_AWAITING,
          screen="persistence", settled_by_version=2),
    ]
    return notes + bids


def test_acceptance_the_blotter_is_fourteen_bids_and_no_prose(client):
    body = get(client, _banked_2026_08_06())
    assert len(body["blotter"]) == 14, "24 notes must not reach the blotter"
    assert [r["entry_id"] for r in body["blotter"]] == \
        [37, 36, 34, 33, 31, 30, 28, 27, 25, 24, 22, 21, 19, 18]
    assert {r["author"] for r in body["blotter"]} == {"agent"}
    assert {r["direction"] for r in body["blotter"]} == {"DEC"}
    assert {r["hour_scope"] for r in body["blotter"]} == {"7x16"}
    assert {r["curve_points"] for r in body["blotter"]} == {16}


def test_acceptance_the_status_split_is_one_settled_five_pending_eight_void(client):
    body = get(client, _banked_2026_08_06())
    by_status = {}
    for r in body["blotter"]:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    assert by_status == {"settled": 1, "pending": 5, "void": 8}


def test_acceptance_the_book_carries_the_one_settlement_and_the_eight_voids(client):
    body = get(client, _banked_2026_08_06())
    assert body["book"] == {"settled_pnl": -918.93, "n_settled": 1,
                            "n_pending": 5, "n_void": 8}


def test_acceptance_the_one_settled_row_reads_exactly_as_banked(client):
    body = get(client, _banked_2026_08_06())
    row = next(r for r in body["blotter"] if r["entry_id"] == 22)
    assert row["status"] == "settled"
    assert row["pnode_id"] == NODE_CONTROLX
    assert row["trade_date"] == "2026-07-31"
    assert row["settled_at"] == "2026-08-06T21:14:11.443000+00:00"
    assert row["settled_by_version"] == 2
    assert row["unsettled_reason"] is None, "null when settled, per the contract"
    assert row["settle_da"] == -50.3591
    assert row["settle_fmm"] == -61.8458
    assert row["pnl_per_mwh"] == -11.4867
    assert row["pnl_dollars"] == -918.93
    assert row["play"] == "persistence"


def test_acceptance_the_curve_is_one_point_on_the_settlement_date(client):
    """Taken 2026-07-31, settled 2026-08-06. The curve is drawn on the LATTER."""
    body = get(client, _banked_2026_08_06())
    assert body["equity"] == [{"date": "2026-08-06", "cumulative_pnl": -918.93}]


def test_acceptance_by_node_is_two_nodes_and_lundy_is_entirely_void(client):
    """The finding, on the page: every bid ever filed at LUNDY_7_N003 — all
    seven — was voided as a double-book. It stays visible with its n_void."""
    body = get(client, _banked_2026_08_06())
    assert body["by_node"] == [
        {"pnode_id": NODE_CONTROLX, "n_settled": 1, "n_pending": 5, "wins": 0,
         "total_pnl": -918.93, "avg_pnl_per_mwh": -11.4867, "n_void": 1},
        {"pnode_id": NODE_LUNDY, "n_settled": 0, "n_pending": 0, "wins": 0,
         "total_pnl": 0.0, "avg_pnl_per_mwh": None, "n_void": 7},
    ]


def test_acceptance_by_play_groups_on_the_writers_real_key(client):
    """NOT 'unclassified' across the board — the writer took the fix. Only the
    two pre-fix rows fall through, and both of them are voids."""
    body = get(client, _banked_2026_08_06())
    assert body["by_play"] == [
        {"play": "persistence", "n_settled": 1, "wins": 0,
         "total_pnl": -918.93, "n_pending": 5, "n_void": 0},
        {"play": "surprise", "n_settled": 0, "wins": 0, "total_pnl": 0.0,
         "n_pending": 0, "n_void": 6},
        {"play": "unclassified", "n_settled": 0, "wins": 0, "total_pnl": 0.0,
         "n_pending": 0, "n_void": 2},
    ]


def test_acceptance_the_play_key_covers_twelve_of_fourteen(client):
    body = get(client, _banked_2026_08_06())
    play = body["derivation"]["play"]
    assert play["keyed_n"] == 12
    assert play["unclassified_n"] == 2
    assert play["available"] is False, "12 of 14 is not full coverage"
    assert "inputs_as_of.bid.screen" in play["finding"]
    unkeyed = [r["entry_id"] for r in body["blotter"]
               if r["play"] == "unclassified"]
    assert unkeyed == [19, 18], "only the two pre-fix rows"


def test_acceptance_the_totals_tie_on_the_real_book(client):
    body = get(client, _banked_2026_08_06())
    visible = sum(r["pnl_dollars"] for r in body["blotter"]
                  if r["status"] == "settled")
    assert round(visible, 2) == body["book"]["settled_pnl"] == \
        body["equity"][-1]["cumulative_pnl"] == -918.93
    assert body["book"]["n_settled"] + body["book"]["n_pending"] + \
        body["book"]["n_void"] == len(body["blotter"]) == 14
