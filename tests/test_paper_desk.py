"""
Tests for THE PAPER DESK v0 (2026-07-31) — the blotter/book/equity lane:

  * GET /api/desk/blotter   every kind='bid' row + derived status
  * GET /api/desk/book      open exposure, settled record, cumulative P&L
  * GET /api/desk/equity    cumulative settled P&L by trade_date
  * GET /api/desk/by-node   per-pnode aggregates
  * GET /api/desk/by-play   per-screen aggregates (v0: play is null)

No live database. Same discipline as tests/test_regime.py: a tiny in-memory
fake pool dispatches canned rows by inspecting the SQL, and the arithmetic is
pinned directly against the pure builders in paper_desk.py with hand-built rows.

WHAT THESE TESTS EXIST TO PROTECT. Each of these is silent on the server and
either wrong or dishonest on the client, so each gets its own test:

  * NOTES ARE NOT BIDS. `paper_journal` is 17 notes and 2 bids today. A note
    reaching the blotter would inflate the position count with prose. The
    filter is asserted in BOTH places it lives — the SQL predicate and
    `bid_rows` — because a widened query would otherwise leak silently.
  * STATUS IS DERIVED, AND THE REASON IS VERBATIM. Three inputs, three
    outcomes, plus the contradiction case (settled = true with a stale reason)
    which must read SETTLED and still ship the reason.
  * A POSITION MARKS ONLY AT SETTLEMENT. An unsettled row carrying a stamped
    P&L column must contribute NOTHING — to the book, the curve, or either
    aggregate. This is the test that catches someone "helpfully" wiring a
    running mark into v0.
  * P&L IS READ, NEVER RECOMPUTED. A row whose stored pnl_dollars contradicts
    (settle_fmm - settle_da) x MWh must serve the STORED figure. Two competing
    sign conventions in one building is the defect this guards.
  * ZERO-FILL IS FLAT, NOT A LOSS — whether the writer stamps 0.00 or leaves
    the column null on a filled_hours = 0 settlement.
  * NOTHING IS QUIETLY DROPPED. win + loss + flat + unclassified == settled.n
    is asserted as an invariant, so a settled row with a hole in it cannot be
    swallowed to make a win rate look clean.
  * EMPTY IS [] AND THIN IS null. Per the isOutlooks doctrine: an array guard
    on the consumer fails on a null and blanks the surface, and a 0.0 win rate
    reads "everything lost" where null reads "nothing has settled".
  * THE TOTALS TIE. The book's cumulative dollars equals the sum of the
    blotter's displayed pnl_dollars column, and equals the curve's last
    cumulative point. A desk that adds up the visible column and gets a
    different answer stops trusting the page.
  * GAPS ARE GAPS. The curve emits no point for a date with nothing settled —
    no interpolation, no zero-fill, no carry-forward.
  * ZERO LLM, MECHANICALLY GREPPED — the house pattern from
    test_structures.py::test_no_forward_pricing_vocabulary_anywhere_in_the_module,
    applied to paper_desk.py AND to all five route functions.

THE ACCEPTANCE TESTS (`test_acceptance_*`) carry the literal contents of
`paper_journal` as read out of the live Neon pantry on 2026-07-31: 19 rows, 17
notes and 2 bids, both bids filed 2026-07-30 for LUNDY_7_N003 and
CONTROLX_1_N003, both DEC 10 MW HE7-22, both unsettled with no reason stamped,
both frontier_ok = false. NOTHING HAS SETTLED YET, so those tests pin today's
honest thin state; the settled branches ride hand-built rows and light
themselves when the settlement writer runs.
"""

import datetime
import inspect

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
import paper_desk as pd


UTC = datetime.timezone.utc
D = datetime.date

NODE_A = "LUNDY_7_N003"
NODE_B = "CONTROLX_1_N003"

DESK_ROUTES = ("/api/desk/blotter", "/api/desk/book", "/api/desk/equity",
               "/api/desk/by-node", "/api/desk/by-play")


# ---------------------------------------------------------------------------
# Fake pool — same shape as tests/test_regime.py
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
# Row builders — a banked `paper_journal` row, as psycopg hands it back
# ---------------------------------------------------------------------------

def journal_row(entry_id, kind="bid", day=30, pnode_id=NODE_A, direction="DEC",
                size_mw=10, price_limit=20.68, hour_scope="HE7-22",
                conviction=4, settled=False, settle_da=None, settle_fmm=None,
                pnl_per_mwh=None, pnl_dollars=None, filled_hours=None,
                settled_at=None, unsettled_reason=None, frontier_ok=False,
                inputs_as_of=None, constraint_id="CONSTRAINT_X", month=7):
    return {
        "entry_id": entry_id,
        "entry_ts": datetime.datetime(2026, month, day, 12, 47, 48, tzinfo=UTC),
        "kind": kind,
        "trade_date": D(2026, month, day),
        "pnode_id": pnode_id,
        "constraint_id": constraint_id,
        "direction": direction,
        "size_mw": size_mw,
        "price_limit": price_limit,
        "hour_scope": hour_scope,
        "conviction": conviction,
        "filled_hours": filled_hours,
        "settled": settled,
        "settle_da": settle_da,
        "settle_fmm": settle_fmm,
        "pnl_per_mwh": pnl_per_mwh,
        "pnl_dollars": pnl_dollars,
        "settled_at": settled_at,
        "unsettled_reason": unsettled_reason,
        "frontier_ok": frontier_ok,
        "inputs_as_of": inputs_as_of if inputs_as_of is not None else {
            "map": {"map_id": "desk_reader_prebid", "version": "v1",
                    "scout_maps_id": 9},
            "bid": {"size_mw": 10.0, "pnode_id": pnode_id, "direction": "DEC",
                    "conviction": 4, "hour_scope": "HE7-22",
                    "price_limit": price_limit,
                    "persistence": {"dam_hours_7d": 146.0, "days_present": 7}},
            "bid_screen": {"facts": ["GATE 1 OK: ..."], "bids_filed": 2},
        },
    }


def settled_row(entry_id, pnl_dollars, pnl_per_mwh=None, day=30,
                pnode_id=NODE_A, filled_hours=16, **kw):
    return journal_row(
        entry_id, day=day, pnode_id=pnode_id, settled=True,
        filled_hours=filled_hours, pnl_dollars=pnl_dollars,
        pnl_per_mwh=pnl_per_mwh,
        settled_at=datetime.datetime(2026, 7, day, 20, 0, tzinfo=UTC), **kw)


def bids_of(rows):
    """The route's own normalization, so the pure tests and the endpoint tests
    are pinned against the same figures."""
    return pd.bid_rows(rows)


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 1 — notes are not bids
# ═══════════════════════════════════════════════════════════════════════════

def test_the_sql_filters_to_bids_and_never_reads_a_note(client):
    """The predicate is in the query, so notes never leave Postgres."""
    pool = use_rows([journal_row(1, kind="note", pnode_id=None),
                     journal_row(2, kind="bid")])
    r = client.get("/api/desk/blotter")
    assert r.status_code == 200
    assert "kind = %(kind)s" in pool.sink["queries"][0]
    assert pool.sink["params"][0] == {"kind": "bid"}
    assert [row["entry_id"] for row in r.json()["rows"]] == [2]


def test_bid_rows_re_asserts_the_filter_in_python():
    """RAIL 1 twice over: if the query is ever widened, the pure layer still
    refuses to count prose as a position."""
    rows = [journal_row(1, kind="note"), journal_row(2, kind="bid"),
            journal_row(3, kind="note")]
    assert [b["entry_id"] for b in pd.bid_rows(rows)] == [2]


def test_a_journal_of_nothing_but_notes_is_an_empty_blotter_not_a_null(client):
    use_rows([journal_row(i, kind="note") for i in range(1, 18)])
    body = client.get("/api/desk/blotter").json()
    assert body["rows"] == []
    assert body["n"] == 0
    assert body["by_status"] == {"OPEN": 0, "PENDING": 0, "SETTLED": 0}


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 2 — status is derived server-side, and the reason is verbatim
# ═══════════════════════════════════════════════════════════════════════════

def test_unsettled_with_no_reason_is_open():
    assert pd.status_of({"settled": False, "unsettled_reason": None}) == "OPEN"


def test_unsettled_with_a_reason_is_pending():
    assert pd.status_of(
        {"settled": False, "unsettled_reason": "DA prints not banked yet"}
    ) == "PENDING"


def test_settled_is_settled():
    assert pd.status_of({"settled": True, "unsettled_reason": None}) == "SETTLED"


def test_a_whitespace_only_reason_is_absence_not_a_pending_state():
    """'   ' is not a reason a desk can read, so it cannot flip a row to
    PENDING — it would render as an empty amber chip with no explanation."""
    assert pd.status_of({"settled": False, "unsettled_reason": "   "}) == "OPEN"
    assert pd.status_of({"settled": False, "unsettled_reason": ""}) == "OPEN"


def test_settled_is_terminal_and_a_stale_reason_still_ships():
    """A row carrying BOTH must read SETTLED — but the contradiction stays
    visible on the wire rather than being swallowed."""
    row = journal_row(1, settled=True, pnl_dollars=100.0, filled_hours=16,
                      unsettled_reason="left over from a prior sweep")
    assert pd.status_of(row) == "SETTLED"
    b = pd.normalize_bid(row)
    assert b["status"] == "SETTLED"
    assert b["unsettled_reason"] == "left over from a prior sweep"


def test_the_pending_reason_is_served_verbatim(client):
    """Never mapped to a code, never truncated, never title-cased — the desk
    renders the string it was given."""
    reason = ("FMM prints for HE7-22 are not banked for 2026-07-30 "
              "(atlas_pnode_lmp_snapshot max RTPD vintage is 2026-07-29T23:55Z); "
              "settlement deferred, no mark taken.")
    use_rows([journal_row(1, settled=False, unsettled_reason=reason)])
    row = client.get("/api/desk/blotter").json()["rows"][0]
    assert row["status"] == "PENDING"
    assert row["unsettled_reason"] == reason


def test_unsettled_reason_is_always_a_key_even_when_absent(client):
    """Present-as-null, never a missing key: a client reading `row.reason` on a
    key that does not exist cannot tell absence from a shape change."""
    use_rows([journal_row(1)])
    row = client.get("/api/desk/blotter").json()["rows"][0]
    assert "unsettled_reason" in row and row["unsettled_reason"] is None


def test_blotter_by_status_counts_all_three(client):
    use_rows([
        journal_row(1, pnode_id=NODE_A),
        journal_row(2, pnode_id=NODE_B, unsettled_reason="awaiting FMM"),
        settled_row(3, 250.0, pnode_id=NODE_A),
    ])
    body = client.get("/api/desk/blotter").json()
    assert body["by_status"] == {"OPEN": 1, "PENDING": 1, "SETTLED": 1}
    assert body["n"] == 3


def test_blotter_carries_every_named_field(client):
    use_rows([settled_row(1, 250.0, pnl_per_mwh=1.5625, settle_da=-2.72,
                          settle_fmm=-1.16)])
    row = client.get("/api/desk/blotter").json()["rows"][0]
    for field in ("trade_date", "pnode_id", "direction", "size_mw",
                  "price_limit", "hour_scope", "conviction", "settled",
                  "settle_da", "settle_fmm", "pnl_per_mwh", "pnl_dollars",
                  "filled_hours", "settled_at", "unsettled_reason", "status"):
        assert field in row, f"blotter row is missing {field}"


def test_numeric_columns_arrive_as_decimal_and_survive_the_wire(client):
    """psycopg hands back `numeric` as Decimal, not float. If the coercion is
    ever dropped the response fails to serialize — or worse, Decimal arithmetic
    silently changes a total. Every money/rate column is exercised."""
    from decimal import Decimal
    use_rows([settled_row(
        1, Decimal("250.005"), pnl_per_mwh=Decimal("1.5625"),
        size_mw=Decimal("10"), price_limit=Decimal("20.68"),
        settle_da=Decimal("-2.72486"), settle_fmm=Decimal("-1.16"))])
    row = client.get("/api/desk/blotter").json()["rows"][0]
    assert row["size_mw"] == 10.0
    assert row["price_limit"] == 20.68
    assert row["pnl_dollars"] == 250.0    # banker's rounding to the cent
    assert row["pnl_per_mwh"] == 1.5625
    assert row["settle_da"] == -2.7249
    assert client.get("/api/desk/book").json()["pnl_dollars"] == 250.0


def test_blotter_is_newest_first(client):
    use_rows([journal_row(1, day=28), journal_row(3, day=30),
              journal_row(2, day=29)])
    body = client.get("/api/desk/blotter").json()
    assert [r["trade_date"] for r in body["rows"]] == \
        ["2026-07-30", "2026-07-29", "2026-07-28"]


def test_the_heavy_inputs_jsonb_never_reaches_the_wire(client):
    """`inputs_as_of` is read (the play lookup needs it) and must not be
    served: the blotter is a position list, not an audit log."""
    use_rows([journal_row(1)])
    body = client.get("/api/desk/blotter").json()
    assert "inputs_as_of" not in body["rows"][0]
    assert "rationale" not in body["rows"][0]


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 3 — a position marks ONLY at settlement
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unsettled_row_with_a_stamped_pnl_books_nothing():
    """THE anti-regression for v0. If someone wires a running mark, this fails:
    an OPEN row carrying dollars must contribute zero everywhere."""
    bids = bids_of([journal_row(1, settled=False, pnl_dollars=9999.0,
                                pnl_per_mwh=62.5, filled_hours=16)])
    assert bids[0]["status"] == "OPEN"
    assert pd.booked_dollars(bids[0]) is None
    assert pd.classify(bids[0]) is None

    bk = pd.book(bids)
    assert bk["pnl_dollars"] == 0.0
    assert bk["settled"]["n"] == 0
    assert bk["avg_pnl_per_mwh"] is None
    assert bk["avg_pnl_per_mwh_n"] == 0
    assert pd.equity_curve(bids)["points"] == []
    assert pd.by_node(bids)["nodes"][0]["pnl_dollars"] == 0.0


def test_a_pending_row_with_a_stamped_pnl_books_nothing_either():
    bids = bids_of([journal_row(1, settled=False, unsettled_reason="awaiting FMM",
                                pnl_dollars=-4200.0, pnl_per_mwh=-26.25)])
    assert bids[0]["status"] == "PENDING"
    assert pd.book(bids)["pnl_dollars"] == 0.0
    assert pd.equity_curve(bids)["points"] == []


def test_open_exposure_is_a_count_and_gross_mw_and_nothing_else(client):
    use_rows([journal_row(1, pnode_id=NODE_A, size_mw=10),
              journal_row(2, pnode_id=NODE_B, size_mw=5),
              journal_row(3, pnode_id=NODE_B, size_mw=25,
                          unsettled_reason="awaiting FMM")])
    bk = client.get("/api/desk/book").json()
    assert bk["open"] == {"n": 2, "gross_mw": 15.0, "sized_n": 2}
    assert bk["pending"] == {"n": 1, "gross_mw": 25.0, "sized_n": 1}
    assert bk["settled"]["n"] == 0
    assert bk["pnl_dollars"] == 0.0


def test_gross_mw_is_a_magnitude_and_ignores_direction():
    """INC and DEC both add to gross exposure; they do not net."""
    bids = bids_of([journal_row(1, direction="INC", size_mw=10, pnode_id=NODE_A),
                    journal_row(2, direction="DEC", size_mw=10, pnode_id=NODE_B)])
    assert pd.book(bids)["open"]["gross_mw"] == 20.0


def test_gross_mw_reports_how_many_rows_were_actually_sized():
    """A null size_mw contributes 0 MW — so `sized_n` says how many rows the
    figure rests on, rather than letting a null read as a 0 MW position."""
    bids = bids_of([journal_row(1, size_mw=10, pnode_id=NODE_A),
                    journal_row(2, size_mw=None, pnode_id=NODE_B)])
    assert pd.book(bids)["open"] == {"n": 2, "gross_mw": 10.0, "sized_n": 1}


# ═══════════════════════════════════════════════════════════════════════════
# RAIL 4 — P&L is read, never recomputed from prices
# ═══════════════════════════════════════════════════════════════════════════

def test_stored_pnl_wins_over_anything_derivable_from_the_settle_prices():
    """The row says +250.00. Naive (settle_fmm - settle_da) x 10 MW x 16h would
    say -8,000.00 and flip the verdict from a win to a loss. The stored,
    already-signed figure is the one that ships."""
    bids = bids_of([settled_row(1, 250.0, pnl_per_mwh=1.5625,
                                settle_da=52.0, settle_fmm=2.0)])
    assert bids[0]["pnl_dollars"] == 250.0
    assert pd.classify(bids[0]) == "win"
    assert pd.book(bids)["pnl_dollars"] == 250.0
    assert pd.book(bids)["settled"]["win"] == 1


def test_the_settle_prices_are_carried_for_display_and_are_never_operands():
    """They reach the wire; they never move a total. Dropping both to null must
    change no P&L figure anywhere."""
    with_prices = bids_of([settled_row(1, 250.0, pnl_per_mwh=1.5625,
                                       settle_da=-2.72, settle_fmm=-1.16)])
    without = bids_of([settled_row(1, 250.0, pnl_per_mwh=1.5625)])
    assert with_prices[0]["settle_da"] == -2.72
    assert with_prices[0]["settle_fmm"] == -1.16
    assert without[0]["settle_da"] is None and without[0]["settle_fmm"] is None
    # Every derived stanza must be byte-identical: the prices appear on the
    # blotter row and NOWHERE in any total.
    for stanza in (pd.book, pd.equity_curve, pd.by_node):
        assert stanza(with_prices) == stanza(without), \
            f"{stanza.__name__} changed when only settle_da/settle_fmm changed"


def test_a_negative_stored_pnl_is_a_loss_without_re_deriving_a_sign():
    bids = bids_of([settled_row(1, -1337.42, pnl_per_mwh=-8.35875)])
    assert pd.classify(bids[0]) == "loss"
    assert pd.book(bids)["pnl_dollars"] == -1337.42


# ═══════════════════════════════════════════════════════════════════════════
# The zero-fill rule, and the accounting invariant
# ═══════════════════════════════════════════════════════════════════════════

def test_a_zero_fill_stamped_zero_is_flat_not_a_loss():
    bids = bids_of([settled_row(1, 0.0, filled_hours=0)])
    assert pd.classify(bids[0]) == "flat"
    bk = pd.book(bids)
    assert (bk["settled"]["flat"], bk["settled"]["loss"]) == (1, 0)


def test_a_zero_fill_left_null_is_also_flat_not_unclassified():
    """The bid never cleared, so no MWh transacted and there is no P&L to book.
    A null here is 'nothing happened', not 'we lost track'."""
    bids = bids_of([settled_row(1, None, filled_hours=0)])
    assert pd.classify(bids[0]) == "flat"
    assert pd.booked_dollars(bids[0]) == 0.0
    bk = pd.book(bids)
    assert bk["settled"]["flat"] == 1
    assert bk["settled"]["unclassified"] == 0
    assert bk["pnl_dollars"] == 0.0


def test_a_settled_row_with_a_fill_but_no_pnl_is_unclassified_never_flat():
    """16 hours filled and no P&L stamped is a HOLE in the ledger, not a
    scratch. Folding it into flat would launder a data gap into a result."""
    bids = bids_of([settled_row(1, None, filled_hours=16)])
    assert pd.classify(bids[0]) is None
    assert pd.booked_dollars(bids[0]) is None
    bk = pd.book(bids)
    assert bk["settled"]["unclassified"] == 1
    assert bk["settled"]["flat"] == 0
    assert bk["settled"]["win_rate"] is None


def test_nothing_is_dropped_win_loss_flat_unclassified_always_sum_to_settled():
    bids = bids_of([
        settled_row(1, 250.0, pnode_id=NODE_A),
        settled_row(2, -100.0, pnode_id=NODE_B),
        settled_row(3, 0.0, filled_hours=0, pnode_id=NODE_A),
        settled_row(4, None, filled_hours=16, pnode_id=NODE_B),
        journal_row(5, pnode_id=NODE_A),
    ])
    s = pd.book(bids)["settled"]
    assert s["n"] == 4
    assert s["win"] + s["loss"] + s["flat"] + s["unclassified"] == s["n"]
    assert (s["win"], s["loss"], s["flat"], s["unclassified"]) == (1, 1, 1, 1)


def test_win_rate_is_over_classified_rows_and_states_them():
    """3 classified (1 win, 1 loss, 1 flat) + 1 hole -> 1/3, not 1/4."""
    bids = bids_of([
        settled_row(1, 250.0), settled_row(2, -100.0),
        settled_row(3, 0.0, filled_hours=0), settled_row(4, None, filled_hours=16),
    ])
    assert pd.book(bids)["settled"]["win_rate"] == pytest.approx(0.3333, abs=1e-4)


# ═══════════════════════════════════════════════════════════════════════════
# Honest empty and thin states (the isOutlooks doctrine)
# ═══════════════════════════════════════════════════════════════════════════

def test_an_empty_journal_serves_lists_not_nulls(client):
    use_rows([])
    assert client.get("/api/desk/blotter").json()["rows"] == []
    assert client.get("/api/desk/equity").json()["points"] == []
    assert client.get("/api/desk/by-node").json()["nodes"] == []
    assert client.get("/api/desk/by-play").json()["plays"] == []


def test_a_win_rate_with_nothing_settled_is_null_never_zero(client):
    """0.0 reads 'everything lost'. null reads 'nothing has settled'. Those are
    different claims and the thin state must make the true one."""
    use_rows([journal_row(1), journal_row(2, pnode_id=NODE_B)])
    bk = client.get("/api/desk/book").json()
    assert bk["settled"]["win_rate"] is None
    assert bk["avg_pnl_per_mwh"] is None
    for node in client.get("/api/desk/by-node").json()["nodes"]:
        assert node["win_rate"] is None
        assert node["avg_pnl_per_mwh"] is None


def test_an_empty_curve_has_a_null_span_not_a_fabricated_one(client):
    use_rows([journal_row(1)])
    body = client.get("/api/desk/equity").json()
    assert body["points"] == []
    assert body["n_points"] == 0
    assert body["span"] is None


def test_the_average_pnl_per_mwh_states_the_n_it_rests_on():
    """Two settled rows, one carrying a rate: the mean is over 1, not 2, and
    says so."""
    bids = bids_of([settled_row(1, 250.0, pnl_per_mwh=1.5625),
                    settled_row(2, -100.0, pnl_per_mwh=None)])
    bk = pd.book(bids)
    assert bk["avg_pnl_per_mwh"] == 1.5625
    assert bk["avg_pnl_per_mwh_n"] == 1


def test_the_average_pnl_per_mwh_is_the_unweighted_mean():
    bids = bids_of([settled_row(1, 250.0, pnl_per_mwh=2.0, pnode_id=NODE_A),
                    settled_row(2, 100.0, pnl_per_mwh=8.0, pnode_id=NODE_B)])
    assert pd.book(bids)["avg_pnl_per_mwh"] == 5.0
    assert "unweighted" in pd.book(bids)["avg_rule"]


# ═══════════════════════════════════════════════════════════════════════════
# The equity curve — honest gaps
# ═══════════════════════════════════════════════════════════════════════════

def test_the_curve_is_settled_rows_only():
    bids = bids_of([settled_row(1, 250.0, day=28),
                    journal_row(2, day=29),
                    journal_row(3, day=30, unsettled_reason="awaiting FMM")])
    points = pd.equity_curve(bids)["points"]
    assert [p["trade_date"] for p in points] == ["2026-07-28"]


def test_the_curve_leaves_a_gap_as_a_gap_and_never_interpolates():
    """07-28 and 07-31 settle; 07-29 and 07-30 do not. The curve emits TWO
    points, not four — no zero-fill of the empty dates, no carry-forward of the
    last value across them."""
    bids = bids_of([settled_row(1, 250.0, day=28),
                    settled_row(2, -100.0, day=31)])
    points = pd.equity_curve(bids)["points"]
    assert [p["trade_date"] for p in points] == ["2026-07-28", "2026-07-31"]
    assert [p["cumulative_pnl_dollars"] for p in points] == [250.0, 150.0]
    assert pd.equity_curve(bids)["span"] == {"first": "2026-07-28",
                                             "last": "2026-07-31"}


def test_the_curve_is_ascending_by_trade_date_regardless_of_row_order():
    bids = bids_of([settled_row(3, 10.0, day=30), settled_row(1, 100.0, day=28),
                    settled_row(2, 5.0, day=29)])
    points = pd.equity_curve(bids)["points"]
    assert [p["trade_date"] for p in points] == \
        ["2026-07-28", "2026-07-29", "2026-07-30"]
    assert [p["cumulative_pnl_dollars"] for p in points] == [100.0, 105.0, 115.0]


def test_a_day_with_several_settlements_is_one_point_carrying_its_counts():
    bids = bids_of([settled_row(1, 250.0, day=28, pnode_id=NODE_A),
                    settled_row(2, -50.0, day=28, pnode_id=NODE_B),
                    settled_row(3, None, day=28, filled_hours=16,
                                pnode_id="THIRD_NODE")])
    point = pd.equity_curve(bids)["points"][0]
    assert point["pnl_dollars"] == 200.0
    assert point["settled_n"] == 3     # three rows settled that date
    assert point["booked_n"] == 2      # two of them booked dollars


# ═══════════════════════════════════════════════════════════════════════════
# The totals tie — the blotter column adds up to the book and to the curve
# ═══════════════════════════════════════════════════════════════════════════

def test_the_book_total_equals_the_sum_of_the_displayed_blotter_column():
    """Cent-rounding happens at the ROW, so a desk adding up the visible
    column gets the book's figure exactly. Awkward thirds on purpose."""
    bids = bids_of([settled_row(1, 33.333, day=28, pnode_id=NODE_A),
                    settled_row(2, 33.333, day=29, pnode_id=NODE_B),
                    settled_row(3, 33.333, day=30, pnode_id=NODE_A)])
    shown = [b["pnl_dollars"] for b in bids]
    assert shown == [33.33, 33.33, 33.33]
    assert pd.book(bids)["pnl_dollars"] == 99.99


def test_the_curves_last_point_equals_the_books_cumulative_total():
    bids = bids_of([settled_row(1, 250.55, day=28, pnode_id=NODE_A),
                    settled_row(2, -100.27, day=29, pnode_id=NODE_B),
                    settled_row(3, 0.0, day=30, filled_hours=0, pnode_id=NODE_A)])
    curve = pd.equity_curve(bids)
    assert curve["points"][-1]["cumulative_pnl_dollars"] == \
        pd.book(bids)["pnl_dollars"] == 150.28


def test_the_node_aggregate_pnl_sums_to_the_book_total():
    bids = bids_of([settled_row(1, 250.0, pnode_id=NODE_A),
                    settled_row(2, -100.0, pnode_id=NODE_B),
                    settled_row(3, 25.5, pnode_id=NODE_A)])
    nodes = pd.by_node(bids)["nodes"]
    assert round(sum(n["pnl_dollars"] for n in nodes), 2) == \
        pd.book(bids)["pnl_dollars"] == 175.5


# ═══════════════════════════════════════════════════════════════════════════
# By node
# ═══════════════════════════════════════════════════════════════════════════

def test_by_node_reports_n_win_rate_total_pnl_and_avg_rate():
    bids = bids_of([
        settled_row(1, 250.0, pnl_per_mwh=1.5625, pnode_id=NODE_A),
        settled_row(2, -50.0, pnl_per_mwh=-0.3125, pnode_id=NODE_A),
        journal_row(3, pnode_id=NODE_A, size_mw=10),
    ])
    node = pd.by_node(bids)["nodes"][0]
    assert node["pnode_id"] == NODE_A
    assert node["n"] == 3
    assert node["settled_n"] == 2
    assert node["open_n"] == 1
    assert node["open_gross_mw"] == 10.0
    assert node["win_rate"] == 0.5
    assert node["pnl_dollars"] == 200.0
    assert node["avg_pnl_per_mwh"] == 0.625


def test_by_node_ranks_by_pnl_descending():
    bids = bids_of([settled_row(1, 10.0, pnode_id="LOW"),
                    settled_row(2, 900.0, pnode_id="HIGH"),
                    settled_row(3, -40.0, pnode_id="NEG")])
    assert [n["pnode_id"] for n in pd.by_node(bids)["nodes"]] == \
        ["HIGH", "LOW", "NEG"]


def test_a_node_with_nothing_settled_sorts_last_but_stays_visible():
    """Dropping it would turn a mixed list into a performance ranking. It has
    live exposure and no record — both facts must be legible."""
    bids = bids_of([settled_row(1, -500.0, pnode_id="LOSER"),
                    journal_row(2, pnode_id="UNSETTLED", size_mw=25)])
    nodes = pd.by_node(bids)["nodes"]
    assert [n["pnode_id"] for n in nodes] == ["LOSER", "UNSETTLED"]
    assert nodes[1]["settled_n"] == 0
    assert nodes[1]["win_rate"] is None
    assert nodes[1]["open_gross_mw"] == 25.0
    assert "never dropped" in pd.by_node(bids)["order"]


def test_by_node_applies_the_zero_fill_rule_too():
    bids = bids_of([settled_row(1, None, filled_hours=0, pnode_id=NODE_A)])
    node = pd.by_node(bids)["nodes"][0]
    assert (node["flat"], node["loss"], node["win_rate"]) == (1, 0, 0.0)


# ═══════════════════════════════════════════════════════════════════════════
# By play — THE FINDING
# ═══════════════════════════════════════════════════════════════════════════

def test_the_banked_writer_carries_no_machine_readable_play_key():
    """Read against the real `inputs_as_of` shape banked on 2026-07-31. The
    screen name lives only in the rationale PROSE, so this returns null."""
    play, path, note = pd.play_of(journal_row(1)["inputs_as_of"])
    assert play is None
    assert path is None
    assert "no machine-readable play key" in note


def test_map_id_is_not_a_play_key():
    """`map.map_id` is identical on every row in the table, notes included —
    the agent program, not the screen. Grouping on it would produce one bucket
    mislabelled as a play."""
    play, _path, _note = pd.play_of({"map": {"map_id": "desk_reader_prebid"}})
    assert play is None


def test_the_presence_of_bid_persistence_is_not_treated_as_a_play():
    """THE NEAR MISS. `bid.persistence` is a metrics block, and inferring
    play='persistence' from its presence is schema-shape divination, not a
    contract — there is no phantom/surprise counterpart to discriminate
    against."""
    play, _path, _note = pd.play_of(
        {"bid": {"persistence": {"dam_hours_7d": 146.0, "days_present": 7}}})
    assert play is None


def test_unkeyed_bids_fall_into_one_null_play_group_that_ties_to_the_blotter():
    """Serving `plays: []` would lose the positions. One null-keyed group keeps
    the counts tied to the blotter while refusing to name a play."""
    rows = [journal_row(1, pnode_id=NODE_A), journal_row(2, pnode_id=NODE_B)]
    bids = bids_of(rows)
    plays = [pd.play_of(r["inputs_as_of"]) for r in rows]
    out = pd.by_play(bids, plays)
    assert len(out["plays"]) == 1
    assert out["plays"][0]["play"] is None
    assert out["plays"][0]["n"] == len(bids) == 2
    assert out["key"]["available"] is False
    assert out["key"]["keyed_n"] == 0
    assert out["key"]["unkeyed_n"] == 2
    assert "inputs_as_of.bid.screen" in " ".join(out["key"]["reasons"])


def test_the_endpoint_reports_which_paths_it_searched(client):
    """The finding is actionable only if it names the slot the writer should
    fill."""
    use_rows([journal_row(1)])
    key = client.get("/api/desk/by-play").json()["key"]
    assert "bid.screen" in key["paths_searched"]
    assert key["paths_found"] == []
    assert key["available"] is False


def test_the_play_lookup_is_live_and_groups_the_day_the_writer_stamps_it():
    """No code change here when `inputs_as_of.bid.screen` appears — this is the
    forward half of the finding, and it must not rot."""
    rows = [
        journal_row(1, pnode_id=NODE_A,
                    inputs_as_of={"bid": {"screen": "persistence"}}),
        journal_row(2, pnode_id=NODE_B,
                    inputs_as_of={"bid": {"screen": "phantom"}}),
        journal_row(3, pnode_id="THIRD",
                    inputs_as_of={"bid": {"screen": "persistence"}}),
    ]
    bids = bids_of(rows)
    by_id = {r["entry_id"]: r["inputs_as_of"] for r in rows}
    plays = [pd.play_of(by_id[b["entry_id"]]) for b in bids]
    out = pd.by_play(bids, plays)
    assert {g["play"]: g["n"] for g in out["plays"]} == \
        {"persistence": 2, "phantom": 1}
    assert out["key"]["available"] is True
    assert out["key"]["unkeyed_n"] == 0
    assert out["key"]["paths_found"] == ["bid.screen"]


def test_a_multi_screen_array_is_reported_not_silently_flattened():
    """`bid.screens: [...]` is a v1 shape. v0 reads scalars, and says so,
    rather than inventing a flattening rule the writer never agreed to."""
    play, path, note = pd.play_of(
        {"bid": {"screen": ["persistence", "phantom"]}})
    assert play is None
    assert path == "bid.screen"
    assert "not a scalar" in note


def test_by_play_applies_the_same_settlement_rules_as_by_node():
    rows = [settled_row(1, 250.0, pnl_per_mwh=1.5625, pnode_id=NODE_A),
            settled_row(2, None, filled_hours=0, pnode_id=NODE_B)]
    bids = bids_of(rows)
    plays = [pd.play_of(r["inputs_as_of"]) for r in rows]
    group = pd.by_play(bids, plays)["plays"][0]
    assert group["settled_n"] == 2
    assert (group["win"], group["flat"], group["loss"]) == (1, 1, 0)
    assert group["pnl_dollars"] == 250.0
    assert group["win_rate"] == 0.5


# ═══════════════════════════════════════════════════════════════════════════
# ZERO LLM — grep-enforced, the house pattern
# ═══════════════════════════════════════════════════════════════════════════

_BANNED_LLM_VOCABULARY = (
    "anthropic", "openai", "claude", "gpt-", "llm", "language model",
    "chat.completions", "completions.create", "prompt", "system_prompt",
    "max_tokens", "temperature=", "embedding", "generative", "inference",
)


def test_no_language_service_vocabulary_anywhere_in_the_pure_layer():
    """RAIL: this lane is arithmetic over 19 banked rows. Any of these
    appearing here means someone wired a language service into a ledger."""
    src = inspect.getsource(pd).lower()
    for banned in _BANNED_LLM_VOCABULARY:
        assert banned not in src, f"language-service vocabulary leaked in: {banned}"


def test_no_language_service_vocabulary_in_any_desk_route():
    """The pure layer is not the only place it could hide — grep the routes
    and the lane's reader too."""
    for fn in (main.desk_blotter, main.desk_book, main.desk_equity,
               main.desk_by_node, main.desk_by_play, main._pd_read_bids,
               main._pd_base):
        src = inspect.getsource(fn).lower()
        for banned in _BANNED_LLM_VOCABULARY:
            assert banned not in src, \
                f"language-service vocabulary leaked into {fn.__name__}: {banned}"


def test_the_lane_imports_no_language_service_client():
    """A dependency is as damning as a call site."""
    src = inspect.getsource(pd)
    assert "import" in src  # sanity: we are reading real source
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "datetime" in stripped or "__future__" in stripped, \
                f"unexpected import in the pure ledger layer: {stripped}"


# ═══════════════════════════════════════════════════════════════════════════
# Every stanza carries as_of
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("route", DESK_ROUTES)
def test_every_stanza_carries_an_as_of(client, route):
    use_rows([journal_row(1)])
    body = client.get(route).json()
    assert "as_of" in body
    datetime.datetime.fromisoformat(body["as_of"])  # parses, or this raises


@pytest.mark.parametrize("route", DESK_ROUTES)
def test_every_stanza_names_its_source_and_says_it_is_paper(client, route):
    use_rows([journal_row(1)])
    body = client.get(route).json()
    assert body["source"] == "paper_journal (kind='bid')"
    assert "no such code path exists" in body["paper_only"]


@pytest.mark.parametrize("route", DESK_ROUTES)
def test_db_down_is_503_never_a_fabricated_empty_desk(client, route):
    """'No positions' and 'could not look' must never render the same."""
    use_rows([journal_row(1)], fail_times=99)
    assert client.get(route).status_code == 503


@pytest.mark.parametrize("route", DESK_ROUTES)
def test_one_read_per_request_and_only_the_lanes_one_query_shape(client, route):
    pool = use_rows([journal_row(1), journal_row(2, pnode_id=NODE_B)])
    assert client.get(route).status_code == 200
    assert len(pool.sink["queries"]) == 1
    assert "FROM paper_journal" in pool.sink["queries"][0]


# ═══════════════════════════════════════════════════════════════════════════
# ACCEPTANCE — the live pantry, read 2026-07-31
# ═══════════════════════════════════════════════════════════════════════════
#
# `paper_journal` held exactly 19 rows: entry_id 1..17 are kind='note' (the
# daily Morning Pre-Bid Read, 2026-07-12 .. 2026-07-30) and 18..19 are
# kind='bid', both filed 2026-07-30T12:47:48Z. Both bids: DEC, 10 MW, HE7-22,
# conviction 4, frontier_ok = false, settled = false, unsettled_reason null,
# every settlement column null.

def _banked_2026_07_31():
    notes = [journal_row(i, kind="note", pnode_id=None, direction=None,
                         size_mw=None, price_limit=None, hour_scope=None,
                         conviction=None, constraint_id=None,
                         day=12 + (i - 1) if i <= 17 else 30)
             for i in range(1, 18)]
    bids = [
        journal_row(18, kind="bid", day=30, pnode_id="LUNDY_7_N003",
                    direction="DEC", size_mw=10, price_limit=20.68,
                    hour_scope="HE7-22", conviction=4, frontier_ok=False,
                    constraint_id="22208_EL CAJON_69.0_22408_LOSCOCHS_69.0_BR_1 _1"),
        journal_row(19, kind="bid", day=30, pnode_id="CONTROLX_1_N003",
                    direction="DEC", size_mw=10, price_limit=0.78,
                    hour_scope="HE7-22", conviction=4, frontier_ok=False,
                    constraint_id="35202_USWP-WKR_60.0_33777_SBTAP   _60.0_BR_1 _1"),
    ]
    return notes + bids


def test_acceptance_the_blotter_is_two_open_bids_and_no_prose(client):
    use_rows(_banked_2026_07_31())
    body = client.get("/api/desk/blotter").json()
    assert body["n"] == 2, "17 notes must not reach the blotter"
    assert body["by_status"] == {"OPEN": 2, "PENDING": 0, "SETTLED": 0}
    assert [r["pnode_id"] for r in body["rows"]] == \
        ["CONTROLX_1_N003", "LUNDY_7_N003"]
    for row in body["rows"]:
        assert row["status"] == "OPEN"
        assert row["direction"] == "DEC"
        assert row["size_mw"] == 10.0
        assert row["hour_scope"] == "HE7-22"
        assert row["conviction"] == 4
        assert row["unsettled_reason"] is None
        assert row["settled_at"] is None
        assert row["pnl_dollars"] is None
        # Every banked bid to date was taken on flagged inputs. Surfaced,
        # never hidden.
        assert row["frontier_ok"] is False


def test_acceptance_the_book_is_20_mw_open_and_an_empty_record(client):
    use_rows(_banked_2026_07_31())
    bk = client.get("/api/desk/book").json()
    assert bk["open"] == {"n": 2, "gross_mw": 20.0, "sized_n": 2}
    assert bk["pending"]["n"] == 0
    assert bk["settled"] == {"n": 0, "win": 0, "loss": 0, "flat": 0,
                             "unclassified": 0, "win_rate": None}
    assert bk["pnl_dollars"] == 0.0
    assert bk["avg_pnl_per_mwh"] is None
    assert bk["bids_n"] == 2


def test_acceptance_the_equity_curve_is_honestly_empty(client):
    """Nothing has settled, so there is no curve — not a zero line."""
    use_rows(_banked_2026_07_31())
    body = client.get("/api/desk/equity").json()
    assert body["points"] == []
    assert body["span"] is None


def test_acceptance_by_node_is_two_nodes_with_exposure_and_no_record(client):
    use_rows(_banked_2026_07_31())
    nodes = client.get("/api/desk/by-node").json()["nodes"]
    assert {n["pnode_id"] for n in nodes} == {"LUNDY_7_N003", "CONTROLX_1_N003"}
    for node in nodes:
        assert node["n"] == 1
        assert node["open_n"] == 1
        assert node["open_gross_mw"] == 10.0
        assert node["settled_n"] == 0
        assert node["win_rate"] is None
        assert node["pnl_dollars"] == 0.0


def test_acceptance_by_play_serves_one_null_group_and_the_finding(client):
    use_rows(_banked_2026_07_31())
    body = client.get("/api/desk/by-play").json()
    assert len(body["plays"]) == 1
    assert body["plays"][0]["play"] is None
    assert body["plays"][0]["n"] == 2
    assert body["key"]["available"] is False
    assert body["key"]["keyed_n"] == 0
    assert body["key"]["unkeyed_n"] == 2
