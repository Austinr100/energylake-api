"""
Tests for THE ALMANAC v0 (2026-08-07) — the publication surface, lane A.

    GET /api/almanac                     the shelf
    GET /api/almanac/{series}            the series page
    GET /api/almanac/{series}/{issue}    one issue

No live database. Same discipline as tests/test_paper_lab.py and
tests/test_regime.py: a tiny in-memory fake pool dispatches canned
`publications` rows by inspecting the SQL, and the pure layer in almanac.py is
pinned directly with hand-built rows.

WHAT THESE TESTS EXIST TO PROTECT. Each is silent on the server and either
wrong or dishonest on the client, so each gets its own test:

  * THE RESPONSE CONTRACT IS PINNED, KEY FOR KEY, AND CARRIES NO ADDITIVE
    FIELDS. Lane B builds against the three shapes verbatim. Every one is
    asserted as an EXACT key set — this lane is the one place in the repo where
    an additive key is a contract break rather than a courtesy, because the
    spec pinned the shapes and named the consumer.

  * DRAFTS NEVER SERVE, AND NEVER LEAK. `status='published'` is asserted to be
    a predicate in all three SQL statements, and a draft requested by its exact
    key must 404 — the SAME 404 as an issue that was never written. A 403 would
    confirm the draft exists, which is the whole thing "drafts never serve" is
    there to prevent.

  * THE ARCHIVE DOES NOT REPEAT THE LATEST. `latest` is the newest issue in
    full and `archive` is everything OLDER. Get this wrong and every series page
    prints its lead story twice. A one-issue series must serve `archive: []`,
    which is the spec's "Empty archive = []" clause doing real work.

  * NEWEST-FIRST IS PINNED, TIE-BREAK AND ALL. An unpinned sort is a shelf whose
    order changes between deployments. The `issue_key DESC` tie-break is
    load-bearing: a backfill writes a whole series in ONE transaction, where
    `now()` is constant, so without it the order is whatever the heap returns.

  * read_minutes IS THE ONLY DERIVED FIELD, and every branch of its rule is
    pinned: prose counts, FIGURES COUNT ZERO, rounding is UP, the floor is 1,
    and a body with no prose reads 0 rather than 1.

  * BODY IS SERVED EXACTLY AS STORED. Block order, unknown block types, and
    figure params all survive the serving layer untouched. A serving layer that
    "fixes" a block can silently change what was published.

  * THE PRE-MIGRATION STATE IS HONEST, AND ONLY THAT ONE. Migration 154 is
    declared and NOT applied, so `publications` does not exist yet; the missing
    table serves an empty shelf so Lane B can build against live URLs. EVERY
    OTHER database error is a 503 — "no issues" and "could not look" must never
    render the same.

  * THE LEGACY /api/almanac/lmp-shape ROUTE STILL WINS. It is a literal path
    inside the prefix this lane claimed, and it survives only because Starlette
    matches in registration order. Reorder the routes and it starts serving
    "404 no such series: 'lmp-shape'".

  * THE BACKFILL TRANSFORM IS LOSSLESS AND HONEST. Both stripped lines are
    preserved in other fields of the same row; the three nulls (dek,
    verifier_version, data_cutoff_ts) are asserted to BE null, so a later
    "improvement" that guesses one of them fails here.

  * THE BYLINE CONSTANT HAS NOT DRIFTED from main.WEATHER_BYLINE. It is a
    deliberate two-line coupling, and this is the line that keeps it one.

  * NOTHING IN THIS LANE WRITES. Mechanically grepped, the house pattern from
    test_structures.py::test_no_forward_pricing_vocabulary_anywhere_in_the_module.

THE ACCEPTANCE TESTS (`test_acceptance_*`) carry the literal contents of the
`joule_briefs` weather run as read out of the live Neon pantry on 2026-08-07:
14 briefs, brief_key='' on every one, one per date, spanning 2026-07-20 ..
2026-08-06 with four gaps (07-24, 07-25, 07-26, 08-01 were never written).
Every one opens with its own headline and closes with the byline. Word counts
and `issued_ts` are measured, not invented; the 2026-08-06 brief is carried in
FULL so the transform is exercised end to end on real prose.
"""

import datetime
import inspect
import pathlib
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

import almanac as alm
import main


UTC = datetime.timezone.utc
D = datetime.date

SHELF = "/api/almanac"


# ---------------------------------------------------------------------------
# Fake pool — same shape as tests/test_paper_lab.py
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
        if self._state["exc"] is not None:
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
    def __init__(self, router, exc=None):
        self._router = router
        self.sink = {"queries": [], "params": []}
        self.state = {"attempts": 0, "exc": exc}

    def connection(self):
        return _FakeConn(self._router, self.sink, self.state)


@pytest.fixture
def client():
    return TestClient(main.app)


def _newest_first(rows):
    """The SQL's ORDER BY, in Python: issued_ts DESC NULLS LAST, issue_key DESC.

    Reimplemented here on purpose. If the fake sorted by whatever order the
    caller happened to pass rows in, the ordering tests would be asserting the
    fixture rather than the endpoint.
    """
    return sorted(
        rows,
        key=lambda r: (
            r.get("issued_ts") is not None,
            r.get("issued_ts") or datetime.datetime.min.replace(tzinfo=UTC),
            r.get("issue_key") or "",
        ),
        reverse=True,
    )


def use_rows(rows, exc=None):
    """Serve `rows` as `publications`, applying each query's own predicates so
    the routes see exactly what Postgres would return."""
    def router(query, params):
        live = [r for r in rows if r.get("status") == alm.STATUS_PUBLISHED]
        series = params.get("series")
        if series is not None:
            live = [r for r in live if r.get("series") == series]
        if "issue_key = %(issue_key)s" in query:
            key = params.get("issue_key")
            return [r for r in live if r.get("issue_key") == key][:1]
        return _newest_first(live)

    pool = FakePool(router, exc=exc)
    main._pool = pool
    return pool


# ---------------------------------------------------------------------------
# Row builders — a `publications` row as psycopg hands it back
# ---------------------------------------------------------------------------

def prose(md):
    return {"type": "prose", "md": md}


def figure(component="lmp_shape", params=None, as_of="2026-08-06T00:00:00+00:00"):
    return {"type": "figure", "component": component,
            "params": params or {"hub": "TH_SP15"}, "as_of": as_of}


_UNSET = object()   # so `issued_ts=None` means NULL, not "use the default"


def pub_row(issue_key="2026-08-06", series=alm.SERIES_DAILY, headline="A headline",
            dek=None, body=_UNSET, author=alm.DAILY_BACKFILL_AUTHOR,
            issued_ts=_UNSET, verifier_version=None, verified=True,
            data_cutoff_ts=None, status=alm.STATUS_PUBLISHED):
    if body is _UNSET:
        body = [prose("word " * 100)]
    if issued_ts is _UNSET:
        issued_ts = datetime.datetime.fromisoformat(f"{issue_key}T13:00:00+00:00")
    return {
        "series": series,
        "issue_key": issue_key,
        "headline": headline,
        "dek": dek,
        "author": author,
        "issued_ts": issued_ts,
        "verified": verified,
        "verifier_version": verifier_version,
        "data_cutoff_ts": data_cutoff_ts,
        "body": body,
        "status": status,
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE CONTRACT — pinned key for key, no additive fields anywhere
# ═══════════════════════════════════════════════════════════════════════════

SHELF_ITEM_KEYS = {
    "series", "issue_key", "headline", "dek", "issued_ts", "verified",
    "read_minutes",
}
ISSUE_KEYS = {
    "series", "issue_key", "headline", "dek", "author", "issued_ts",
    "verified", "verifier_version", "data_cutoff_ts", "body",
}
SERIES_PAGE_KEYS = {"series", "intro", "latest", "archive"}
ARCHIVE_ROW_KEYS = {"issue_key", "headline", "issued_ts"}


def test_the_shelf_item_contract_is_exactly_what_was_pinned(client):
    use_rows([pub_row()])
    body = client.get(SHELF).json()
    assert isinstance(body, list), "the shelf is a BARE ARRAY, nothing wraps it"
    assert len(body) == 1
    assert set(body[0]) == SHELF_ITEM_KEYS


def test_the_issue_contract_is_exactly_what_was_pinned(client):
    use_rows([pub_row()])
    body = client.get(f"{SHELF}/daily/2026-08-06").json()
    assert set(body) == ISSUE_KEYS


def test_the_series_page_contract_is_exactly_what_was_pinned(client):
    use_rows([pub_row("2026-08-06"), pub_row("2026-08-05")])
    body = client.get(f"{SHELF}/daily").json()
    assert set(body) == SERIES_PAGE_KEYS
    assert set(body["latest"]) == ISSUE_KEYS
    assert set(body["archive"][0]) == ARCHIVE_ROW_KEYS


def test_the_key_order_matches_the_spec_so_a_diff_reads_cleanly(client):
    """The spec listed the fields in an order. Serving them in that order means
    a reviewer can diff the response against the brief line by line."""
    use_rows([pub_row()])
    assert list(client.get(SHELF).json()[0]) == [
        "series", "issue_key", "headline", "dek", "issued_ts", "verified",
        "read_minutes"]
    assert list(client.get(f"{SHELF}/daily/2026-08-06").json()) == [
        "series", "issue_key", "headline", "dek", "author", "issued_ts",
        "verified", "verifier_version", "data_cutoff_ts", "body"]


def test_no_additive_keys_reach_the_wire_anywhere(client):
    """This lane ships NONE. The spec pinned the shapes and named the consumer,
    so an unannounced key is a contract break, not a courtesy."""
    use_rows([pub_row("2026-08-06"), pub_row("2026-08-05")])
    assert set(client.get(SHELF).json()[0]) == SHELF_ITEM_KEYS
    page = client.get(f"{SHELF}/daily").json()
    assert set(page) == SERIES_PAGE_KEYS
    assert set(page["latest"]) == ISSUE_KEYS
    for stub in page["archive"]:
        assert set(stub) == ARCHIVE_ROW_KEYS


# ═══════════════════════════════════════════════════════════════════════════
# PUBLISHED ONLY — drafts never serve, and never leak
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sql", [
    main._ALMANAC_SHELF_SQL, main._ALMANAC_SERIES_SQL, main._ALMANAC_ISSUE_SQL,
])
def test_published_only_is_a_sql_predicate_not_a_python_filter(sql):
    """A draft must never reach a serializer at all."""
    assert "status = 'published'" in sql
    assert "draft" not in sql


def test_a_draft_never_appears_on_the_shelf(client):
    use_rows([
        pub_row("2026-08-06", status=alm.STATUS_PUBLISHED),
        pub_row("2026-08-05", status=alm.STATUS_DRAFT),
    ])
    keys = [c["issue_key"] for c in client.get(SHELF).json()]
    assert keys == ["2026-08-06"]


def test_a_draft_requested_by_its_exact_key_is_a_404_not_a_403(client):
    """A 403 would confirm the draft exists. That is the leak this prevents."""
    use_rows([pub_row("2026-08-05", status=alm.STATUS_DRAFT)])
    assert client.get(f"{SHELF}/daily/2026-08-05").status_code == 404


def test_a_draft_404_is_indistinguishable_from_an_issue_never_written(client):
    use_rows([pub_row("2026-08-05", status=alm.STATUS_DRAFT)])
    draft = client.get(f"{SHELF}/daily/2026-08-05")
    use_rows([])
    absent = client.get(f"{SHELF}/daily/2026-08-05")
    assert draft.status_code == absent.status_code == 404
    assert draft.json() == absent.json()


def test_a_draft_is_never_the_latest_on_a_series_page(client):
    use_rows([
        pub_row("2026-08-06", status=alm.STATUS_DRAFT),
        pub_row("2026-08-05", status=alm.STATUS_PUBLISHED),
    ])
    page = client.get(f"{SHELF}/daily").json()
    assert page["latest"]["issue_key"] == "2026-08-05"
    assert page["archive"] == []


# ═══════════════════════════════════════════════════════════════════════════
# THE ARCHIVE — older only, never the latest, [] when there is nothing behind
# ═══════════════════════════════════════════════════════════════════════════

def test_the_archive_is_every_older_issue_and_never_repeats_the_latest(client):
    use_rows([pub_row("2026-08-06"), pub_row("2026-08-05"), pub_row("2026-08-04")])
    page = client.get(f"{SHELF}/daily").json()
    assert page["latest"]["issue_key"] == "2026-08-06"
    assert [r["issue_key"] for r in page["archive"]] == ["2026-08-05", "2026-08-04"]
    assert "2026-08-06" not in [r["issue_key"] for r in page["archive"]]


def test_a_one_issue_series_serves_an_empty_archive(client):
    """The spec's 'Empty archive = []' clause, doing real work."""
    use_rows([pub_row("2026-08-06")])
    page = client.get(f"{SHELF}/daily").json()
    assert page["latest"]["issue_key"] == "2026-08-06"
    assert page["archive"] == []


def test_a_series_with_nothing_published_is_a_200_with_a_null_latest(client):
    """The page exists; it is empty. That is not a 404."""
    use_rows([])
    r = client.get(f"{SHELF}/weekly")
    assert r.status_code == 200
    assert r.json() == {"series": "weekly", "intro": None,
                        "latest": None, "archive": []}


def test_an_absent_series_intro_is_null(client):
    """SERIES_INTRO is empty at v0, so every series serves null."""
    use_rows([pub_row()])
    for series in alm.SERIES_VOCABULARY:
        assert client.get(f"{SHELF}/{series}").json()["intro"] is None


def test_a_registered_series_intro_reaches_the_wire(monkeypatch, client):
    """The registry is empty, not dead. Filling it must serve, with no migration."""
    monkeypatch.setitem(alm.SERIES_INTRO, "monthly", "The month in power.")
    use_rows([])
    assert client.get(f"{SHELF}/monthly").json()["intro"] == "The month in power."


# ═══════════════════════════════════════════════════════════════════════════
# NEWEST FIRST — pinned, tie-break and all
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("sql", [main._ALMANAC_SHELF_SQL, main._ALMANAC_SERIES_SQL])
def test_newest_first_is_pinned_in_the_sql(sql):
    assert "ORDER BY issued_ts DESC NULLS LAST, issue_key DESC" in " ".join(sql.split())


def test_the_shelf_is_newest_first_across_every_series(client):
    use_rows([
        pub_row("2026-08-04", series="daily"),
        pub_row("2026-W31", series="weekly",
                issued_ts=datetime.datetime(2026, 8, 6, 9, tzinfo=UTC)),
        pub_row("2026-08-05", series="daily"),
    ])
    assert [c["issue_key"] for c in client.get(SHELF).json()] == [
        "2026-W31", "2026-08-05", "2026-08-04"]


def test_a_shared_issued_ts_is_tie_broken_on_issue_key_descending(client):
    """A backfill writes a whole series inside one transaction, where now() is
    constant. Without the tie-break the order is whatever the heap returns."""
    same = datetime.datetime(2026, 8, 7, 3, 30, tzinfo=UTC)
    use_rows([pub_row(k, issued_ts=same)
              for k in ("2026-08-02", "2026-08-06", "2026-08-04")])
    assert [c["issue_key"] for c in client.get(SHELF).json()] == [
        "2026-08-06", "2026-08-04", "2026-08-02"]


def test_an_issue_with_no_issued_ts_sorts_last_never_first(client):
    """A piece with no issue date is not the newest thing on the shelf."""
    use_rows([pub_row("undated", issued_ts=None), pub_row("2026-07-20")])
    assert [c["issue_key"] for c in client.get(SHELF).json()] == [
        "2026-07-20", "undated"]


# ═══════════════════════════════════════════════════════════════════════════
# THE ?series= FILTER
# ═══════════════════════════════════════════════════════════════════════════

def test_the_series_filter_narrows_the_shelf(client):
    use_rows([pub_row("2026-08-06", series="daily"),
              pub_row("2026-W31", series="weekly")])
    keys = [c["issue_key"] for c in client.get(SHELF, params={"series": "weekly"}).json()]
    assert keys == ["2026-W31"]


def test_the_series_filter_is_folded_in_by_parameter_not_string_building(client):
    pool = use_rows([pub_row()])
    client.get(SHELF)
    assert pool.sink["params"][0] == {"series": None}
    assert "%(series)s::text IS NULL" in main._ALMANAC_SHELF_SQL


def test_an_unknown_series_filter_is_a_400_naming_the_vocabulary(client):
    """Never a silently empty shelf: a typo must be told, not absorbed."""
    use_rows([pub_row()])
    r = client.get(SHELF, params={"series": "quarterly"})
    assert r.status_code == 400
    for s in alm.SERIES_VOCABULARY:
        assert s in r.json()["detail"]


def test_an_unknown_series_path_is_a_404_naming_the_vocabulary(client):
    use_rows([pub_row()])
    r = client.get(f"{SHELF}/quarterly")
    assert r.status_code == 404
    for s in alm.SERIES_VOCABULARY:
        assert s in r.json()["detail"]


def test_an_unknown_series_on_an_issue_path_is_a_404(client):
    use_rows([pub_row()])
    assert client.get(f"{SHELF}/quarterly/2026-08-06").status_code == 404


def test_the_series_vocabulary_is_exactly_the_migrations_check_constraint():
    """Four series, in the order the spec wrote them. The CHECK in
    migrations/154_publications.sql must say the same thing."""
    assert alm.SERIES_VOCABULARY == ("daily", "weekly", "monthly", "article")
    sql = (pathlib.Path(__file__).resolve().parent.parent
           / "migrations" / "154_publications.sql").read_text()
    for series in alm.SERIES_VOCABULARY:
        assert f"'{series}'" in sql
    assert "CHECK (series IN ('daily', 'weekly', 'monthly', 'article'))" in sql
    assert "CHECK (status IN ('draft', 'published'))" in sql
    assert "UNIQUE (series, issue_key)" in sql


# ═══════════════════════════════════════════════════════════════════════════
# read_minutes — the only derived field in the contract
# ═══════════════════════════════════════════════════════════════════════════

def test_read_minutes_rounds_up():
    assert alm.read_minutes([prose("w " * 201)]) == 2
    assert alm.read_minutes([prose("w " * 200)]) == 1
    assert alm.read_minutes([prose("w " * 199)]) == 1


def test_read_minutes_floors_at_one_for_any_prose_at_all():
    assert alm.read_minutes([prose("one")]) == 1


def test_read_minutes_is_zero_with_no_prose_never_one():
    """0 is the arithmetic truth. 1 would claim a minute that is not there."""
    assert alm.read_minutes([]) == 0
    assert alm.read_minutes([figure()]) == 0
    assert alm.read_minutes(None) == 0


def test_a_figure_block_counts_zero_words():
    """A figure is looked at, not read, and no honest word count exists for one.
    Its params must not be scraped for text either."""
    long_params = figure(params={"caption": "word " * 500})
    assert alm.read_minutes([long_params]) == 0
    assert alm.read_minutes([prose("w " * 100), long_params]) == 1


def test_read_minutes_sums_across_every_prose_block():
    assert alm.read_minutes([prose("w " * 150), figure(), prose("w " * 150)]) == 2


def test_an_unknown_block_type_counts_zero_and_does_not_raise():
    """A body carrying a block type this module has never heard of still serves."""
    assert alm.read_minutes([{"type": "pull_quote", "md": "w " * 400}]) == 0


def test_read_minutes_survives_a_malformed_body():
    """One bad body must not 500 the whole shelf."""
    for body in ({"type": "prose"}, ["not a dict"], [{"type": "prose", "md": 7}]):
        assert alm.read_minutes(body) == 0


def test_the_reading_speed_constant_has_exactly_one_definition():
    assert alm.READ_WORDS_PER_MINUTE == 200
    assert "READ_WORDS_PER_MINUTE" in inspect.getsource(alm.read_minutes)


# ═══════════════════════════════════════════════════════════════════════════
# BODY IS SERVED EXACTLY AS STORED
# ═══════════════════════════════════════════════════════════════════════════

def test_the_body_blocks_are_served_verbatim_in_order(client):
    body = [prose("first"), figure(component="dart_ladder"), prose("last")]
    use_rows([pub_row(body=body)])
    assert client.get(f"{SHELF}/daily/2026-08-06").json()["body"] == body


def test_an_unknown_block_type_survives_the_serving_layer(client):
    """The writer owns what a block says. A serving layer that 'fixes' one can
    silently change what was published."""
    odd = {"type": "pull_quote", "text": "...", "attribution": "Kelvin"}
    use_rows([pub_row(body=[odd])])
    assert client.get(f"{SHELF}/daily/2026-08-06").json()["body"] == [odd]


def test_a_figures_params_are_never_reshaped(client):
    fig = figure(params={"hub": "TH_SP15", "he": [7, 8], "nested": {"a": [1, 2]}})
    use_rows([pub_row(body=[fig])])
    assert client.get(f"{SHELF}/daily/2026-08-06").json()["body"][0] == fig


def test_a_null_body_serves_an_empty_list_so_a_client_can_always_iterate(client):
    """Migration 154 declares `body` NOT NULL, but the serving layer does not
    depend on that — the API tolerates the spec's literal nullability."""
    use_rows([pub_row(body=None)])
    assert client.get(f"{SHELF}/daily/2026-08-06").json()["body"] == []
    assert client.get(SHELF).json()[0]["read_minutes"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# DATES AND BOOLEANS ON THE WIRE
# ═══════════════════════════════════════════════════════════════════════════

def test_every_timestamp_is_an_iso_string(client):
    # Deliberately synthetic microseconds — the measured values live in
    # BANKED_DAILIES. This test is about the RENDERING, not the data.
    ts = datetime.datetime(2026, 8, 6, 13, 55, 6, 123456, tzinfo=UTC)
    use_rows([pub_row(issued_ts=ts, data_cutoff_ts=ts)])
    issue = client.get(f"{SHELF}/daily/2026-08-06").json()
    assert issue["issued_ts"] == "2026-08-06T13:55:06.123456+00:00"
    assert issue["data_cutoff_ts"] == "2026-08-06T13:55:06.123456+00:00"
    assert client.get(SHELF).json()[0]["issued_ts"] == issue["issued_ts"]


def test_a_null_timestamp_stays_null_never_becomes_a_string(client):
    use_rows([pub_row(issued_ts=None, data_cutoff_ts=None)])
    issue = client.get(f"{SHELF}/daily/2026-08-06").json()
    assert issue["issued_ts"] is None
    assert issue["data_cutoff_ts"] is None


def test_verified_is_always_a_real_boolean(client):
    """A card that renders a verification chip must never be handed a null."""
    use_rows([pub_row(verified=True)])
    assert client.get(SHELF).json()[0]["verified"] is True
    use_rows([pub_row(verified=False)])
    assert client.get(SHELF).json()[0]["verified"] is False
    assert client.get(f"{SHELF}/daily/2026-08-06").json()["verified"] is False


def test_a_null_verifier_version_does_not_mean_unverified(client):
    """The two fields answer different questions and must not be conflated."""
    use_rows([pub_row(verified=True, verifier_version=None)])
    issue = client.get(f"{SHELF}/daily/2026-08-06").json()
    assert issue["verified"] is True
    assert issue["verifier_version"] is None


# ═══════════════════════════════════════════════════════════════════════════
# THE PRE-MIGRATION STATE, AND EVERY OTHER FAILURE
# ═══════════════════════════════════════════════════════════════════════════

def test_a_missing_publications_table_serves_an_empty_shelf(client, caplog):
    """Migration 154 is declared and NOT applied. Lane B builds against live
    URLs today; the fact is logged, never smuggled into the payload."""
    use_rows([], exc=psycopg.errors.UndefinedTable("no such table"))
    with caplog.at_level("WARNING"):
        r = client.get(SHELF)
    assert r.status_code == 200
    assert r.json() == []
    assert "migration 154" in caplog.text


def test_a_missing_table_serves_a_series_page_with_a_null_latest(client):
    use_rows([], exc=psycopg.errors.UndefinedTable("no such table"))
    r = client.get(f"{SHELF}/daily")
    assert r.status_code == 200
    assert r.json() == {"series": "daily", "intro": None,
                        "latest": None, "archive": []}


def test_a_missing_table_serves_an_issue_404(client):
    use_rows([], exc=psycopg.errors.UndefinedTable("no such table"))
    assert client.get(f"{SHELF}/daily/2026-08-06").status_code == 404


@pytest.mark.parametrize("route", [
    SHELF, f"{SHELF}/daily", f"{SHELF}/daily/2026-08-06",
])
def test_any_other_database_error_is_a_503_never_an_empty_page(client, route):
    """'No issues' and 'could not look' must never render the same."""
    use_rows([], exc=psycopg.ProgrammingError("column does not exist"))
    r = client.get(route)
    assert r.status_code == 503
    assert "db unavailable" in r.json()["detail"]


def test_only_undefined_table_is_absorbed_and_it_is_named_in_the_handler():
    src = inspect.getsource(main._almanac_read)
    assert "psycopg.errors.UndefinedTable" in src
    assert "503" in src


# ═══════════════════════════════════════════════════════════════════════════
# THE LEGACY ROUTE INSIDE THIS PREFIX
# ═══════════════════════════════════════════════════════════════════════════

def test_the_legacy_lmp_shape_route_still_wins(client):
    """`/api/almanac/lmp-shape` (M2, 2026-05-29) is a literal path inside the
    prefix this lane claimed. It survives ONLY because Starlette matches in
    REGISTRATION ORDER. Move these routes above it and it starts serving
    '404 no such series: lmp-shape'."""
    paths = [r.path for r in main.app.routes if getattr(r, "path", "").startswith(SHELF)]
    assert "/api/almanac/lmp-shape" in paths
    assert paths.index("/api/almanac/lmp-shape") < paths.index("/api/almanac/{series}")


def test_lmp_shape_is_not_smuggled_into_the_series_vocabulary():
    """Protecting the legacy route by calling it a series would put a chart
    endpoint in the Almanac's cadence list. Worse than the collision."""
    assert "lmp-shape" not in alm.SERIES_VOCABULARY


# ═══════════════════════════════════════════════════════════════════════════
# THE BACKFILL TRANSFORM
# ═══════════════════════════════════════════════════════════════════════════

BRIEF_20260806_HEADLINE = (
    "Strong Southwest ridge crests Friday and holds through the middle of "
    "next week")

# Carried in FULL, verbatim from `joule_briefs` on 2026-08-07, so the transform
# is exercised end to end on real prose rather than on a fixture that agrees
# with it by construction.
BRIEF_20260806_MD = (
    "Strong Southwest ridge crests Friday and holds through the middle of next week"
    "\n\n"
    "A strong upper-level ridge is the week's story, centered over the desert "
    "Southwest with strong companion ridging across the Great Basin, interior "
    "California, and out to the coast, plus a more modest bump over the Pacific "
    "Northwest. It crests Friday and holds unbroken into the middle of next week; "
    "the single strongest anomaly on the map — exceptional on the desk's ladder — "
    "peaks Saturday and sits outside any named region. The desert is already "
    "living in it: Phoenix averaged 105 over the weekend, about eight degrees "
    "above normal, with Las Vegas close behind, and Sacramento, Fresno, and "
    "Redding all running well warm. The coast stays the mild edge from San "
    "Francisco to San Diego, and the Northwest is the true counterweight — "
    "Spokane, Seattle, and Portland have been running well below normal, and the "
    "ridge over them is the weakest on the map and the first to fade, gone after "
    "Saturday. Denver is the one blind spot this morning, with no fresh "
    "observations on the desk."
    "\n\n"
    "Do not look for moisture to change any of this: nothing organized reaches "
    "the load centers, and the desk has no moisture signal to read this cycle. "
    "What the runs do agree on is the ridge itself — every named region's signal "
    "held steady from one run to the next — and the small movement on the board "
    "leans warm, with the overnight run nudging next Tuesday warmer around "
    "Sacramento and Northern California and lifting Friday's peak in the Arizona "
    "desert. Yesterday's forecasts verified across the full network, tightest "
    "along the coast and a touch warm inland and at Denver, so the guidance has "
    "earned some trust this week."
    "\n\n"
    "The hazard feed is not on the desk and most of the longer-range climate "
    "drivers are lagging, so the wider frame is the season's own: peak Southwest "
    "monsoon and fire-weather season, with interior heat keeping cooling demand "
    "at the center of the market story. Expect Friday hot and Saturday no cooler. "
    "The question worth watching is the back half of next week — the ridge is "
    "drawn holding through Wednesday, and whether it breaks then or lingers is "
    "the difference between one hot stretch and a longer one."
    "\n\n"
    "By Kelvin · Weather Desk (agent)"
)

BRIEF_20260806 = {
    "brief_date": D(2026, 8, 6),
    "headline": BRIEF_20260806_HEADLINE,
    "content_md": BRIEF_20260806_MD,
    "created_at": datetime.datetime(2026, 8, 6, 13, 55, 6, 526603, tzinfo=UTC),
}


def test_the_transform_maps_a_brief_onto_the_publications_columns():
    row = alm.publication_from_daily_brief(BRIEF_20260806)
    assert row["series"] == "daily"
    assert row["issue_key"] == "2026-08-06"
    assert row["headline"] == BRIEF_20260806_HEADLINE
    assert row["author"] == alm.DAILY_BACKFILL_AUTHOR
    assert row["issued_ts"] == BRIEF_20260806["created_at"]
    assert row["status"] == "published"
    assert set(row) == {
        "series", "issue_key", "headline", "dek", "body", "author",
        "issued_ts", "verifier_version", "verified", "data_cutoff_ts", "status"}


def test_the_body_is_exactly_one_prose_block():
    body = alm.publication_from_daily_brief(BRIEF_20260806)["body"]
    assert len(body) == 1
    assert body[0]["type"] == "prose"
    assert set(body[0]) == {"type", "md"}


def test_verified_is_true_and_read_from_the_tables_own_invariant():
    """The pantry weather writer UPSERTs only on verifier PASS, so the EXISTENCE
    of the source row IS the stamp. Not an assumption — the same invariant
    /api/weather/brief/history already serves."""
    assert alm.publication_from_daily_brief(BRIEF_20260806)["verified"] is True


@pytest.mark.parametrize("field", ["dek", "verifier_version", "data_cutoff_ts"])
def test_the_three_findings_are_null_and_are_never_guessed(field):
    """Each is a thing the writer does not bank. Deriving a dek from the first
    sentence, or filing `voice_version` / `bundle_version` / a writer clock
    under a verifier or data-cutoff field, is a guess dressed as a record."""
    assert alm.publication_from_daily_brief(BRIEF_20260806)[field] is None


def test_the_duplicated_headline_line_is_stripped_from_the_body():
    md = alm.publication_from_daily_brief(BRIEF_20260806)["body"][0]["md"]
    assert not md.startswith(BRIEF_20260806_HEADLINE)
    assert md.startswith("A strong upper-level ridge is the week's story")


def test_the_trailing_byline_line_is_stripped_from_the_body():
    md = alm.publication_from_daily_brief(BRIEF_20260806)["body"][0]["md"]
    assert not md.rstrip().endswith(alm.DAILY_BACKFILL_AUTHOR)
    assert md.rstrip().endswith("between one hot stretch and a longer one.")


def test_both_stripped_lines_survive_verbatim_in_other_fields():
    """The strip is LOSSLESS. That is the whole justification for editing the
    writer's document at all."""
    row = alm.publication_from_daily_brief(BRIEF_20260806)
    assert row["headline"] in BRIEF_20260806_MD
    assert row["author"] in BRIEF_20260806_MD
    assert row["headline"] == BRIEF_20260806_HEADLINE
    assert row["author"] == alm.DAILY_BACKFILL_AUTHOR


def test_the_prose_between_the_two_stripped_lines_is_untouched():
    md = alm.publication_from_daily_brief(BRIEF_20260806)["body"][0]["md"]
    assert md in BRIEF_20260806_MD, "the body must be a verbatim SLICE of the brief"
    for para in ("Do not look for moisture", "The hazard feed is not on the desk",
                 "exceptional on the desk's ladder"):
        assert para in md


def test_a_headline_that_only_resembles_the_first_line_is_never_stripped():
    """Exact match only. A brief keeps every character it was written with."""
    md = "A strong Southwest ridge crests\n\nBody."
    assert alm.strip_duplicated_headline(md, "A strong Southwest ridge") == md


def test_a_body_that_is_only_a_headline_keeps_it():
    """Removing it would bank an empty piece; keep it and let the duplication
    be visible."""
    assert alm.strip_duplicated_headline("Just a headline", "Just a headline") == (
        "Just a headline")


def test_a_body_that_is_only_a_byline_keeps_it():
    assert alm.strip_trailing_byline(
        alm.DAILY_BACKFILL_AUTHOR, alm.DAILY_BACKFILL_AUTHOR
    ) == alm.DAILY_BACKFILL_AUTHOR


def test_a_different_signoff_is_never_stripped():
    md = "Body.\n\nBy someone else"
    assert alm.strip_trailing_byline(md, alm.DAILY_BACKFILL_AUTHOR) == md


def test_the_byline_constant_has_not_drifted_from_main():
    """A deliberate two-line coupling — almanac.py cannot import main. This is
    the line that keeps the two identical. '(agent)' is non-negotiable."""
    assert alm.DAILY_BACKFILL_AUTHOR == main.WEATHER_BYLINE
    assert "(agent)" in alm.DAILY_BACKFILL_AUTHOR


def test_the_backfill_reads_kelvins_brief_type_not_the_editorial_daily():
    """`joule_briefs` holds BOTH a brief_type='daily' (Joule's editorial tape
    brief, 53 rows) and brief_type='weather' (Kelvin's Weather Desk daily, 14
    rows). This lane backfills KELVIN's. Reading the wrong one would bank 53
    filings-summaries under a weather desk's byline."""
    assert alm.BACKFILL_BRIEF_TYPE == "weather"


def test_a_backfilled_row_serves_as_the_pinned_issue_contract(client):
    """The two halves of the lane, joined: a real brief through the real
    transform and straight out through the real serving shape."""
    row = alm.publication_from_daily_brief(BRIEF_20260806)
    use_rows([row])
    served = client.get(f"{SHELF}/daily/2026-08-06").json()
    assert set(served) == ISSUE_KEYS
    assert served["headline"] == BRIEF_20260806_HEADLINE
    assert served["author"] == alm.DAILY_BACKFILL_AUTHOR
    assert served["verified"] is True
    assert served["verifier_version"] is None
    assert served["data_cutoff_ts"] is None
    assert served["dek"] is None
    assert served["body"] == row["body"]
    assert served["issued_ts"] == "2026-08-06T13:55:06.526603+00:00"


# ═══════════════════════════════════════════════════════════════════════════
# ACCEPTANCE — the live `joule_briefs` weather run, read 2026-08-07
# ═══════════════════════════════════════════════════════════════════════════

# (issue_key, issued_ts, measured body words after both strips, headline)
#
# `issued_ts` IS PINNED TO THE MICROSECOND, and that is not fussiness. The
# pantry banks `joule_briefs.created_at` at microsecond grain (…06.526603), and
# a JSON viewer that renders it to milliseconds (…06.526) will make these look
# like round numbers they are not. Pinning the real value is what caught a
# millisecond-truncated fixture in this very file before it shipped.
BANKED_DAILIES = [
    ("2026-08-06", "2026-08-06T13:55:06.526603+00:00", 364,
     "Strong Southwest ridge crests Friday and holds through the middle of next week"),
    ("2026-08-05", "2026-08-05T13:57:29.909488+00:00", 379,
     "Strong Southwest ridge crests Friday with an exceptional core, and desert heat owns the week"),
    ("2026-08-04", "2026-08-04T14:00:27.177688+00:00", 365,
     "A strong Southwest ridge crests Friday and holds deep into next week"),
    ("2026-08-03", "2026-08-03T14:29:42.330331+00:00", 345,
     "Strong Southwest ridge builds toward a Friday crest — interior heat runs the demand story"),
    ("2026-08-02", "2026-08-02T13:12:43.799498+00:00", 365,
     "A strong Southwest ridge crests Friday and holds into next weekend"),
    ("2026-07-31", "2026-07-31T13:50:50.855616+00:00", 340,
     "An exceptional Southwest ridge crests Friday and holds the West hot through Monday"),
    ("2026-07-30", "2026-07-30T13:45:21.455243+00:00", 358,
     "Exceptional Southwest ridge crests Friday and holds through the weekend — desert heat leads"),
    ("2026-07-29", "2026-07-29T14:01:09.584652+00:00", 204,
     "Exceptional ridge over the desert Southwest crests Friday and holds interior heat"),
    ("2026-07-28", "2026-07-28T11:29:39.265362+00:00", 353,
     "Exceptional Southwest ridge crests Friday and holds — desert heat anchors the week"),
    ("2026-07-27", "2026-07-27T11:56:40.389879+00:00", 282,
     "Guidance cools Idaho midweek: the 06Z run cuts 5°F from IPCO's Wednesday peak"),
    ("2026-07-23", "2026-07-23T17:55:14.951079+00:00", 347,
     "Valley heat is the demand story: Sacramento 9.7 degrees above normal through the settled read"),
    ("2026-07-22", "2026-07-22T11:38:20.320801+00:00", 265,
     "Interior heat holds well above normal; Sacramento leads as verification slips at Phoenix"),
    ("2026-07-21", "2026-07-21T15:38:10.424843+00:00", 387,
     "Forecasts ran cold at Phoenix and Sacramento; the settled walk shows Sacramento +9.7F above normal"),
    ("2026-07-20", "2026-07-21T01:10:28.870949+00:00", 433,
     "Interior heat holds through the settled walk; Sacramento runs +9.7°F as runs nudge warmer"),
]


def banked_rows():
    """The 14 banked briefs as the publications rows the backfill would write.

    Word counts are the MEASURED post-strip counts; the prose is stood in with
    a token of the identical count, because carrying 29 kB of the desk's prose
    into a test file buys nothing the 2026-08-06 brief (carried in full above)
    does not already prove.
    """
    rows = []
    for issue_key, issued, words, headline in BANKED_DAILIES:
        rows.append(pub_row(
            issue_key=issue_key,
            headline=headline,
            body=[prose(" ".join(["word"] * words))],
            issued_ts=datetime.datetime.fromisoformat(issued),
        ))
    return rows


def test_acceptance_the_banked_run_is_fourteen_dailies_with_four_gaps():
    """2026-07-20 .. 2026-08-06 is 18 calendar days; 07-24, 07-25, 07-26 and
    08-01 were never written. GAPS ARE GAPS — nothing pads them."""
    keys = [k for k, _, _, _ in BANKED_DAILIES]
    assert len(keys) == 14
    assert keys[0] == "2026-08-06" and keys[-1] == "2026-07-20"
    assert set(keys).isdisjoint(
        {"2026-07-24", "2026-07-25", "2026-07-26", "2026-08-01"})


def test_acceptance_the_shelf_is_the_fourteen_dailies_newest_first(client):
    use_rows(banked_rows())
    shelf = client.get(SHELF).json()
    assert len(shelf) == 14
    assert [c["issue_key"] for c in shelf] == [k for k, _, _, _ in BANKED_DAILIES]
    assert [c["headline"] for c in shelf] == [h for _, _, _, h in BANKED_DAILIES]
    assert all(c["series"] == "daily" for c in shelf)
    assert all(c["verified"] is True for c in shelf)
    assert all(c["dek"] is None for c in shelf)


def test_acceptance_the_issued_ts_column_is_what_the_pantry_banked(client):
    """2026-07-20's brief was banked at 01:10 UTC on the 21st — the run is
    ordered by ISSUE KEY here only because that ordering happens to agree.
    Pinning the real instants keeps that agreement honest."""
    use_rows(banked_rows())
    shelf = client.get(SHELF).json()
    assert [c["issued_ts"] for c in shelf] == [t for _, t, _, _ in BANKED_DAILIES]
    assert shelf[-1]["issued_ts"] == "2026-07-21T01:10:28.870949+00:00"


def test_acceptance_read_minutes_over_the_real_word_counts(client):
    """Thirteen briefs read in 2 minutes; 2026-07-20, at 433 words, reads in 3."""
    use_rows(banked_rows())
    shelf = client.get(SHELF).json()
    got = {c["issue_key"]: c["read_minutes"] for c in shelf}
    assert got["2026-07-20"] == 3
    assert got["2026-07-29"] == 2, "204 words is still two minutes, rounded up"
    assert sorted(got.values()) == [2] * 13 + [3]


def test_acceptance_the_daily_series_page_leads_with_the_newest(client):
    use_rows(banked_rows())
    page = client.get(f"{SHELF}/daily").json()
    assert page["series"] == "daily"
    assert page["intro"] is None
    assert page["latest"]["issue_key"] == "2026-08-06"
    assert len(page["archive"]) == 13
    assert page["archive"][0]["issue_key"] == "2026-08-05"
    assert page["archive"][-1]["issue_key"] == "2026-07-20"


def test_acceptance_the_other_three_series_are_empty_and_say_so(client):
    """Only `daily` is backfilled. weekly/monthly/article exist in the
    vocabulary and publish nothing yet — an empty page, not a 404."""
    use_rows(banked_rows())
    for series in ("weekly", "monthly", "article"):
        page = client.get(f"{SHELF}/{series}").json()
        assert page == {"series": series, "intro": None,
                        "latest": None, "archive": []}
    assert client.get(SHELF, params={"series": "weekly"}).json() == []


def test_acceptance_one_read_per_request(client):
    pool = use_rows(banked_rows())
    client.get(SHELF)
    assert len(pool.sink["queries"]) == 1
    pool = use_rows(banked_rows())
    client.get(f"{SHELF}/daily")
    assert len(pool.sink["queries"]) == 1, "the series page is ONE read, not two"
    pool = use_rows(banked_rows())
    client.get(f"{SHELF}/daily/2026-08-06")
    assert len(pool.sink["queries"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# NOTHING IN THE SERVING LANE WRITES — mechanically grepped
# ═══════════════════════════════════════════════════════════════════════════

_WRITE_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b", re.IGNORECASE)


@pytest.mark.parametrize("sql", [
    main._ALMANAC_SHELF_SQL, main._ALMANAC_SERIES_SQL, main._ALMANAC_ISSUE_SQL,
])
def test_no_almanac_query_can_write(sql):
    """This service is read-only. The backfill adapter is a hand-run script and
    is not importable from the app."""
    assert not _WRITE_SQL.search(sql)


def test_the_pure_layer_has_no_io_at_all():
    """almanac.py is pure: no database, no network, no clock. The route layer
    reads; the script writes."""
    src = (pathlib.Path(alm.__file__)).read_text()
    for forbidden in ("import psycopg", "import requests", "import httpx",
                      "datetime.now", "utcnow", "open("):
        assert forbidden not in src, f"almanac.py must not contain {forbidden!r}"


def test_the_backfill_script_is_not_reachable_from_the_app():
    """It is a script, not a module the API imports. If this ever fails, the
    only write path in the repository has become reachable from a request."""
    import sys
    assert "backfill_almanac_daily" not in sys.modules
    assert "backfill_almanac_daily" not in pathlib.Path(main.__file__).read_text()


# ═══════════════════════════════════════════════════════════════════════════
# THE BACKFILL ADAPTER — receipt arithmetic, driven through a fake connection
#
# The script cannot reach a database from CI (no outbound Postgres route), so
# `run()` is driven here against a fake connection that behaves like the table:
# it holds rows keyed on (series, issue_key) and applies the UPSERT. What this
# pins is the part a live rehearsal CANNOT check cheaply — that the receipt
# tells the truth about which of inserted/updated/unchanged actually happened.
#
# THE FAILURE THIS EXISTS TO CATCH: `ON CONFLICT DO UPDATE` reports every
# conflicting row as affected whether or not it changed a byte. A receipt that
# counted rows instead of comparing pre-images would report "14 updated" on a
# no-op re-run — work it did not do — and nobody would notice, because the
# table would be correct either way.
# ═══════════════════════════════════════════════════════════════════════════

import importlib.util as _ilu

_BACKFILL_PATH = (pathlib.Path(__file__).resolve().parent.parent
                  / "scripts" / "backfill_almanac_daily.py")


def load_backfill():
    """Import the script by path. It is NOT importable as a module from the
    app, which is the point — see test_the_backfill_script_is_not_reachable."""
    spec = _ilu.spec_from_file_location("_backfill_under_test", _BACKFILL_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeTable:
    """Just enough `publications` to drive the adapter: rows keyed on
    (series, issue_key), with the UPSERT's overwrite semantics."""

    def __init__(self, briefs, existing=None):
        self.briefs = briefs
        self.rows = dict(existing or {})
        self.upserts = 0

    def execute(self, sql, params):
        bf = load_backfill  # noqa: F841 - readability only
        if "FROM joule_briefs" in sql:
            since = params["since"]
            self._result = [
                b for b in self.briefs
                if since is None or b["brief_date"] >= since]
        elif sql.strip().startswith("SELECT") and "FROM publications" in sql:
            key = (params["series"], params["issue_key"])
            self._result = [self.rows[key]] if key in self.rows else []
        else:  # the UPSERT
            self.upserts += 1
            key = (params["series"], params["issue_key"])
            stored = {k: v for k, v in params.items()
                      if k not in ("series", "issue_key")}
            # `body` arrives wrapped as Jsonb; unwrap the way psycopg would
            # hand it back on the next read.
            stored["body"] = getattr(stored["body"], "obj", stored["body"])
            self.rows[key] = stored
            self._result = [{"publication_id": len(self.rows)}]

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeBackfillConn:
    def __init__(self, table):
        self.table = table

    def cursor(self, row_factory=None):
        return self.table


def _args(apply=False, since=None, skip_existing=False):
    import argparse
    return argparse.Namespace(
        apply=apply, since=since, skip_existing=skip_existing)


def test_the_backfill_receipt_counts_a_first_run_as_all_inserts():
    bf = load_backfill()
    table = _FakeTable([BRIEF_20260806])
    receipt = bf.run(_FakeBackfillConn(table), _args())
    assert receipt["briefs_read"] == 1
    assert receipt["inserted"] == 1
    assert receipt["updated"] == 0
    assert receipt["unchanged"] == 0
    assert receipt["issue_keys"] == ["2026-08-06"]
    assert receipt["changed_issue_keys"] == ["2026-08-06"]


def test_the_backfill_is_idempotent_and_a_rerun_reports_unchanged():
    """The receipt must not claim work it did not do."""
    bf = load_backfill()
    table = _FakeTable([BRIEF_20260806])
    bf.run(_FakeBackfillConn(table), _args())
    before = dict(table.rows)

    receipt = bf.run(_FakeBackfillConn(table), _args())
    assert receipt["unchanged"] == 1
    assert receipt["inserted"] == 0
    assert receipt["updated"] == 0
    assert receipt["changed_issue_keys"] == []
    assert table.rows == before, "a re-run must not change a single column"


def test_a_rerun_over_an_edited_row_reports_an_update_not_unchanged():
    bf = load_backfill()
    table = _FakeTable([BRIEF_20260806])
    bf.run(_FakeBackfillConn(table), _args())
    table.rows[("daily", "2026-08-06")]["headline"] = "Someone edited this"

    receipt = bf.run(_FakeBackfillConn(table), _args())
    assert receipt["updated"] == 1
    assert receipt["unchanged"] == 0
    assert receipt["changed_issue_keys"] == ["2026-08-06"]
    assert table.rows[("daily", "2026-08-06")]["headline"] == (
        BRIEF_20260806_HEADLINE), "the upsert restores the brief's headline"


def test_skip_existing_leaves_a_hand_edited_row_alone():
    """Stated in the script's docstring rather than discovered: a re-run
    overwrites hand edits. This is the flag that avoids it."""
    bf = load_backfill()
    table = _FakeTable([BRIEF_20260806])
    bf.run(_FakeBackfillConn(table), _args())
    table.rows[("daily", "2026-08-06")]["headline"] = "Hand edited"

    receipt = bf.run(_FakeBackfillConn(table), _args(skip_existing=True))
    assert receipt["skipped_existing"] == 1
    assert receipt["inserted"] == receipt["updated"] == receipt["unchanged"] == 0
    assert table.rows[("daily", "2026-08-06")]["headline"] == "Hand edited"


def test_since_narrows_the_window():
    bf = load_backfill()
    older = dict(BRIEF_20260806, brief_date=D(2026, 7, 20))
    table = _FakeTable([older, BRIEF_20260806])
    receipt = bf.run(_FakeBackfillConn(table), _args(since=D(2026, 8, 1)))
    assert receipt["briefs_read"] == 1
    assert receipt["issue_keys"] == ["2026-08-06"]


def test_the_backfill_writes_every_column_the_migration_declares():
    bf = load_backfill()
    table = _FakeTable([BRIEF_20260806])
    bf.run(_FakeBackfillConn(table), _args())
    stored = table.rows[("daily", "2026-08-06")]
    assert set(stored) == {
        "headline", "dek", "body", "author", "issued_ts", "verifier_version",
        "verified", "data_cutoff_ts", "status"}
    assert set(bf.COMPARED_COLUMNS) == set(stored), (
        "every written column must be compared, or a change to it reads as "
        "'unchanged' forever")


def test_the_dry_run_is_the_default_and_apply_is_the_only_way_past_it():
    bf = load_backfill()
    assert bf.parse_args([]).apply is False
    assert bf.parse_args(["--apply"]).apply is True
    src = _BACKFILL_PATH.read_text()
    assert "conn.rollback()" in src
    assert "if args.apply:\n            conn.commit()" in src


def test_the_backfill_never_deletes_or_truncates():
    """Grepped over the script's SQL CONSTANTS, not its prose — the docstring
    is allowed to say the word 'DELETE' while promising never to run one."""
    bf = load_backfill()
    for name in ("READ_BRIEFS_SQL", "READ_EXISTING_SQL", "UPSERT_SQL"):
        sql = getattr(bf, name).upper()
        for forbidden in ("DELETE", "TRUNCATE", "DROP", "ALTER"):
            assert forbidden not in sql, f"{name} contains {forbidden}"
    # And the script runs NOTHING but those three: every execute() call site
    # names one of the constants, so there is no inline SQL to slip past the
    # check above.
    executed = re.findall(r"\.execute\(\s*([A-Za-z_][A-Za-z_0-9]*)",
                          inspect.getsource(bf.run))
    assert set(executed) == {"READ_BRIEFS_SQL", "READ_EXISTING_SQL", "UPSERT_SQL"}


def test_the_backfill_names_the_migration_when_the_table_is_missing():
    """A psycopg traceback tells the operator nothing. The message names 154."""
    src = _BACKFILL_PATH.read_text()
    assert "psycopg.errors.UndefinedTable" in src
    assert "Migration 154" in src
    assert "Nothing was written" in src
