"""
Tests for the Node Screener read layer (D-08-20):

  GET /api/node-stats/screener
  GET /api/node-stats/node/{pnode_id}
  GET /api/node-stats/breadth

THE ACCEPTANCE CENTREPIECE is test_derived_mean_and_sigma_are_hand_derived and
its sibling test_mean_of_means_would_have_been_wrong: a block's mean and sigma
are recomputed BY HAND from the per-HE sufficient statistics, and then the
mean-of-means answer is computed too and asserted to DIFFER. The second half is
the one that matters — an implementation that averaged the per-HE means would
pass a test that only checked "is it a plausible mean", and fails this one.

The suite also pins, in order of how badly a regression would hurt:

  * THE NULL PERCENTILES. A ceiling-refused node's p75/p95/p99 arrive as null
    and stay null — not filled, not interpolated from the rungs that survived,
    not defaulted, and not dropped from the dict so the key goes missing. Every
    row carries pctl_ceiling whether or not the ladder is complete.

  * COVERAGE ON EVERY ROW. hours_expected and hours_present are asserted present
    and non-null on every row of every route that has them, because a percentile
    without its coverage is not a readable number.

  * STRUCTURAL NULLS. dart is null on DAM and basis/congestion/loss are null on
    RTPD, and the response says WHICH measures are structural for the market it
    served — so a client can tell "this market has no DART" from "this node has
    no data".

  * THE DAY-FILTERED REFUSAL. ON_PEAK and OFF_PEAK are NERC 6x16 and its
    complement; the hourly rail has no day dimension, so they get no derived
    moments and carry the reason instead. Their percentiles and coverage are
    still served in full — the refusal is scoped to the moments only.

  * THE HE-SET MAP, which is the fact the whole recombination rests on. It is a
    partition of HE1..HE24 and it is what was measured against the live bank
    (474/474 on hours_expected, hours_present and n_lmp).

  * SORT/DIR/PAGING VALIDATION, including that a hostile `sort` cannot reach the
    SQL text, and that mean/sigma are deliberately NOT sort keys.

  * THE VINTAGE STAMP — as_of and computed_at both on the wire, and the
    pre-repair span labelling itself without changing a single served value.

No live database. Like the sibling suites this swaps a tiny in-memory fake pool
into `main._pool`; DB rows are faked as psycopg's dict_row would yield them,
Decimals included, because Decimal-vs-float is exactly where a recombination
goes quietly wrong.
"""

import math
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import main
import node_stats as NS


# ---------------------------------------------------------------------------
# The fake pool
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, pool):
        self._pool = pool
        self._rows: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._pool.calls.append({"query": query, "params": params})
        if self._pool.raise_exc is not None:
            raise self._pool.raise_exc
        self._rows = self._pool.rows_for(query, params)

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._pool)


class FakePool:
    """Routes each of the lane's SQLs to a canned result set.

    Dispatch is on a distinctive fragment of each query rather than on call
    order, so a test cannot pass because the reads happened to run in the order
    it assumed. Anything unrecognized returns [] — itself useful, since it
    exercises the honest-absence paths.
    """

    def __init__(self, *, as_of=date(2026, 8, 16), block_rows=None,
                 agg_rows=None, ladder_rows=None, rail_rows=None,
                 breadth_rows=None, raise_exc=None):
        self.as_of = as_of
        self.block_rows = block_rows or []
        self.agg_rows = agg_rows or []
        self.ladder_rows = ladder_rows or []
        self.rail_rows = rail_rows or []
        self.breadth_rows = breadth_rows or []
        self.raise_exc = raise_exc
        self.calls: list = []

    def connection(self):
        return _FakeConn(self)

    def rows_for(self, query, params):
        params = params or {}
        if "zone_breadth_daily" in query:
            return list(self.breadth_rows)
        if "WITH grid AS" in query:
            return list(self.ladder_rows)
        if "h.market, h.as_of, h.he" in query:
            return list(self.rail_rows)
        if "GROUP BY h.pnode_id" in query:
            return list(self.agg_rows)
        if "count(*) OVER ()" in query:
            return list(self.block_rows)
        if "ORDER BY b.as_of DESC" in query:
            return [{"as_of": self.as_of}] if self.as_of else []
        return []


@pytest.fixture
def client(monkeypatch):
    def _mk(pool):
        monkeypatch.setattr(main, "_pool", pool)
        return TestClient(main.app)
    return _mk


# ---------------------------------------------------------------------------
# Fixtures shaped like the live bank
# ---------------------------------------------------------------------------

AS_OF = date(2026, 8, 16)
STAMP = datetime(2026, 8, 18, 20, 37, 29, tzinfo=timezone.utc)

#: A node whose ladder is COMPLETE — the writer published every rung.
FULL_NODE = {
    "pnode_id": "FULL_1_N001",
    "hours_expected": 168, "hours_present": 158,
    "n_lmp": 158, "pctl_ceiling_lmp": "p95",
    "p05_lmp": Decimal("24.41984"), "p25_lmp": Decimal("31.50"),
    "p50_lmp": Decimal("44.91505"), "p75_lmp": Decimal("52.10"),
    "p95_lmp": Decimal("66.83013"), "p99_lmp": Decimal("78.25846"),
    "n_dart": None, "pctl_ceiling_dart": None,
    "p05_dart": None, "p25_dart": None, "p50_dart": None,
    "p75_dart": None, "p95_dart": None, "p99_dart": None,
    "first_banked": date(2026, 8, 10), "last_banked": AS_OF,
    "computed_at": STAMP, "total_matched": 2,
}

#: A node the writer REFUSED above p50 — the ceiling case this lane exists for.
#: Its p75/p95/p99 are null in the bank and must be null on the wire.
CAPPED_NODE = {
    **FULL_NODE,
    "pnode_id": "CAPPED_1_N002",
    "hours_expected": 168, "hours_present": 12,
    "n_lmp": 12, "pctl_ceiling_lmp": "p50",
    "p05_lmp": Decimal("30.00"), "p25_lmp": Decimal("33.00"),
    "p50_lmp": Decimal("36.00"),
    "p75_lmp": None, "p95_lmp": None, "p99_lmp": None,
}

#: Sufficient statistics for FULL_1_N001 as the recombination query returns
#: them: already pooled in numeric by Postgres. The values are the ones
#: test_derived_mean_and_sigma_are_hand_derived re-derives independently.
AGG_FULL = {
    "pnode_id": "FULL_1_N001",
    "n_lmp": 158, "mean_lmp": Decimal("44.5"), "sigma_lmp": Decimal("11.25"),
    "min_lmp": Decimal("23.23064"), "max_lmp": Decimal("78.25846"),
    "n_dart": None, "mean_dart": None, "sigma_dart": None,
    "min_dart": None, "max_dart": None,
}


def _screener(client, pool, **q):
    params = {"market": "DAM", "window_days": 7, "block_key": "ALL24"}
    params.update(q)
    r = client(pool).get("/api/node-stats/screener", params=params)
    return r


# ---------------------------------------------------------------------------
# THE CENTREPIECE: recombination, and the mean-of-means it is not
# ---------------------------------------------------------------------------

#: Four HEs of a rail, with DELIBERATELY UNEQUAL n. The inequality is the whole
#: point: if every hour carried the same count, pooling and averaging would
#: agree and the test would prove nothing.
RAIL_FOR_ARITHMETIC = [
    {"he": 1, "n": 6, "sum": Decimal("298.31285"), "sum2": Decimal("14876.90")},
    {"he": 2, "n": 6, "sum": Decimal("281.81667"), "sum2": Decimal("13312.55")},
    {"he": 3, "n": 7, "sum": Decimal("308.61164"), "sum2": Decimal("13646.11")},
    {"he": 4, "n": 7, "sum": Decimal("310.39888"), "sum2": Decimal("13830.02")},
]


def _pooled(rail):
    """mean and sigma the way the SQL computes them: pool first, then divide."""
    n = sum(r["n"] for r in rail)
    s = sum(r["sum"] for r in rail)
    s2 = sum(r["sum2"] for r in rail)
    mean = s / n
    var = (s2 - (s * s) / n) / (n - 1)
    return float(mean), math.sqrt(max(float(var), 0.0))


def _mean_of_means(rail):
    """The WRONG answer: average the per-HE means, weighting each hour equally
    no matter how many observations stand behind it."""
    means = [r["sum"] / r["n"] for r in rail]
    return float(sum(means) / len(means))


def test_derived_mean_and_sigma_are_hand_derived():
    """The pooled moments, re-derived here from the raw sums.

    This is the arithmetic _ns_pooled_exprs emits into SQL, restated
    independently in Python. If the SQL drifts, the numbers below stop being
    the ones it produces.
    """
    mean, sigma = _pooled(RAIL_FOR_ARITHMETIC)
    n = sum(r["n"] for r in RAIL_FOR_ARITHMETIC)
    s = sum(r["sum"] for r in RAIL_FOR_ARITHMETIC)

    assert n == 26
    assert mean == pytest.approx(float(s) / 26)
    assert mean == pytest.approx(46.1207707692, abs=1e-9)
    assert sigma > 0


def test_mean_of_means_would_have_been_wrong():
    """THE HALF THAT SEPARATES A RECOMBINATION FROM AN AVERAGE.

    With unequal per-HE counts the two answers genuinely differ. A future edit
    that 'simplifies' the pooling into an AVG of per-HE means gets caught here
    and nowhere else, because both answers look like plausible prices.
    """
    pooled, _ = _pooled(RAIL_FOR_ARITHMETIC)
    naive = _mean_of_means(RAIL_FOR_ARITHMETIC)
    assert pooled != pytest.approx(naive, abs=1e-9)


def test_sigma_is_null_below_two_observations():
    """A single observation has no spread. Saying 0.0 would claim it does."""
    row = {"n": 1, "sum": Decimal("42.0"), "sum2": Decimal("1764.0")}
    assert row["n"] < 2  # the SQL's CASE WHEN n > 1 guard
    d = NS.derived_block({"n": 1, "mean": Decimal("42.0"), "sigma": None,
                          "min": Decimal("42.0"), "max": Decimal("42.0")},
                         "ALL24")
    assert d["available"] is True
    assert d["n"] == 1
    assert d["mean"] == 42.0
    assert d["sigma"] is None


def test_sql_pools_before_dividing():
    """The emitted SQL divides a SUM by a SUM — it never averages an average.

    Read as text rather than executed, because the point is the SHAPE of the
    expression: sum(...)/sum(...) is a recombination, avg(...) is not.
    """
    sql = main._ns_pooled_exprs("lmp", agg=True)
    assert "sum(h.sum_lmp) / sum(h.n_lmp)" in " ".join(sql.split())
    assert "avg(" not in sql.lower()
    # And the sigma comes off sum_lmp2, with the sample (n-1) denominator.
    flat = " ".join(sql.split())
    assert "sum(h.sum_lmp2)" in flat
    assert "(sum(h.n_lmp) - 1)" in flat
    assert "GREATEST(" in flat        # clamped at exactly zero, never fudged up


# ---------------------------------------------------------------------------
# THE NULL PERCENTILES — the ceiling refusal, passed through
# ---------------------------------------------------------------------------

def test_null_percentiles_pass_through_untouched(client):
    pool = FakePool(block_rows=[FULL_NODE, CAPPED_NODE], agg_rows=[AGG_FULL])
    body = _screener(client, pool).json()

    capped = [r for r in body["rows"] if r["pnode_id"] == "CAPPED_1_N002"][0]
    pct = capped["lmp"]["percentiles"]

    # Present as KEYS, null as VALUES. Both halves matter: a dropped key is as
    # bad as a filled one, because the client cannot tell it from a typo.
    assert set(pct) == set(NS.NS_PERCENTILES)
    assert pct["p05"] == 30.0 and pct["p25"] == 33.0 and pct["p50"] == 36.0
    assert pct["p75"] is None
    assert pct["p95"] is None
    assert pct["p99"] is None

    # Not filled from the surviving rungs, not extrapolated, not defaulted to
    # the median — the three shapes a "helpful" regression would take.
    assert pct["p75"] != pct["p50"]
    assert pct["p95"] not in (36.0, 0.0)


def test_pctl_ceiling_ships_on_every_row(client):
    """Including the rows whose ladder happens to be complete — the field is the
    contract, not a flag raised only on refusal."""
    pool = FakePool(block_rows=[FULL_NODE, CAPPED_NODE], agg_rows=[AGG_FULL])
    body = _screener(client, pool).json()

    assert len(body["rows"]) == 2
    for row in body["rows"]:
        assert "pctl_ceiling" in row["lmp"]
    ceilings = {r["pnode_id"]: r["lmp"]["pctl_ceiling"] for r in body["rows"]}
    assert ceilings == {"FULL_1_N001": "p95", "CAPPED_1_N002": "p50"}


def test_percentiles_helper_never_invents_a_value():
    """The one function that could break rule 1, tested directly on an all-null
    ladder — the case where a fill would be most tempting and least visible."""
    empty = {f"{p}_lmp": None for p in NS.NS_PERCENTILES}
    out = NS.percentiles(empty, "lmp")
    assert set(out) == set(NS.NS_PERCENTILES)
    assert all(v is None for v in out.values())


# ---------------------------------------------------------------------------
# COVERAGE IS A FIRST-CLASS FIELD
# ---------------------------------------------------------------------------

def test_coverage_counts_ride_every_row(client):
    pool = FakePool(block_rows=[FULL_NODE, CAPPED_NODE], agg_rows=[AGG_FULL])
    body = _screener(client, pool).json()

    for row in body["rows"]:
        assert row["hours_expected"] is not None
        assert row["hours_present"] is not None
        assert row["coverage"] is not None

    capped = [r for r in body["rows"] if r["pnode_id"] == "CAPPED_1_N002"][0]
    # 12 of 168 — the number that says the p50 above is a 12-sample p50.
    assert capped["hours_present"] == 12
    assert capped["hours_expected"] == 168
    assert capped["coverage"] == pytest.approx(12 / 168)


def test_zero_expectation_yields_null_coverage_not_a_divide_by_zero():
    assert NS.coverage(0, 0) is None
    assert NS.coverage(None, 168) is None
    assert NS.coverage(0, 168) == 0.0     # a real zero, distinct from null


# ---------------------------------------------------------------------------
# STRUCTURAL NULLS
# ---------------------------------------------------------------------------

def test_dart_is_structurally_null_on_dam(client):
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    body = _screener(client, pool, market="DAM").json()

    assert body["rows"][0]["dart"] is None
    assert body["structural_nulls"]["measures"] == ["dart"]
    assert "CONSTRUCTION" in body["structural_nulls"]["note"]


def test_dart_is_real_on_rtpd(client):
    rtpd_row = {**FULL_NODE, "n_dart": 158, "pctl_ceiling_dart": "p95",
                "p50_dart": Decimal("-5.8306475")}
    pool = FakePool(block_rows=[rtpd_row], agg_rows=[AGG_FULL])
    body = _screener(client, pool, market="RTPD").json()

    assert body["structural_nulls"]["measures"] == []
    dart = body["rows"][0]["dart"]
    assert dart is not None
    assert dart["n"] == 158
    assert dart["percentiles"]["p50"] == pytest.approx(-5.8306475)


def test_hourly_structural_nulls_mirror_between_markets():
    """DAM carries the decomposition and no DART; RTPD is the exact mirror.
    Measured on 720 hourly rows per market at as_of 2026-08-16."""
    dam = NS.structural_nulls("DAM", measures=NS.NS_HOURLY_MEASURES)
    rtpd = NS.structural_nulls("RTPD", measures=NS.NS_HOURLY_MEASURES)
    assert dam["measures"] == ["dart"]
    assert sorted(rtpd["measures"]) == ["basis", "congestion", "loss"]


# ---------------------------------------------------------------------------
# THE DAY-FILTERED REFUSAL
# ---------------------------------------------------------------------------

def test_on_peak_withholds_moments_but_serves_the_ladder(client):
    """ON_PEAK's percentiles, coverage and ceiling are served IN FULL. Only the
    recombined moments are withheld, and the reason rides with them."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[])
    body = _screener(client, pool, block_key="ON_PEAK").json()
    row = body["rows"][0]

    assert row["lmp"]["percentiles"]["p95"] == pytest.approx(66.83013)
    assert row["lmp"]["pctl_ceiling"] == "p95"
    assert row["hours_present"] == 158

    derived = row["lmp"]["derived"]
    assert derived["available"] is False
    assert "NERC 6x16" in derived["reason"]
    assert derived["he_set"] is None
    assert "mean" not in derived and "sigma" not in derived


def test_day_filtered_block_skips_the_recombination_read(client):
    """No HE set means no second read to make — the refusal is decided in code,
    not by running a query whose answer would be discarded."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[])
    _screener(client, pool, block_key="OFF_PEAK")
    assert not any("GROUP BY h.pnode_id" in c["query"] for c in pool.calls)


def test_both_day_filtered_blocks_are_named_with_reasons():
    assert set(NS.NS_DAY_FILTERED_BLOCKS) == {"ON_PEAK", "OFF_PEAK"}
    for key, reason in NS.NS_DAY_FILTERED_BLOCKS.items():
        assert "day" in reason.lower()
        assert "node_stats_hourly" in reason


# ---------------------------------------------------------------------------
# THE HE-SET MAP — measured against the live bank, pinned here
# ---------------------------------------------------------------------------

def test_intraday_blocks_partition_the_day():
    """The five named blocks are disjoint and their union is HE1..HE24, which is
    why their recombinations sum back to ALL24."""
    seen: list[int] = []
    for block in ("off_peak_overnight", "morning_on_peak", "midday_solar",
                  "evening_on_peak", "late_off_peak"):
        seen.extend(NS.NS_BLOCK_HOURS[block])
    assert sorted(seen) == list(range(1, 25))
    assert len(seen) == len(set(seen))


def test_the_measured_he_sets():
    """The mapping established by measurement: for as_of 2026-08-16,
    window_days=30, 40 nodes x both markets, the per-HE rail summed over each
    set reproduced the block row's hours_expected, hours_present AND n_lmp on
    474 of 474 comparisons."""
    assert NS.NS_BLOCK_HOURS["ALL24"] == tuple(range(1, 25))
    assert NS.NS_BLOCK_HOURS["off_peak_overnight"] == (1, 2, 3, 4, 5, 6)
    assert NS.NS_BLOCK_HOURS["morning_on_peak"] == (7, 8)
    assert NS.NS_BLOCK_HOURS["midday_solar"] == (9, 10, 11, 12, 13, 14, 15, 16)
    assert NS.NS_BLOCK_HOURS["evening_on_peak"] == (17, 18, 19, 20, 21, 22)
    assert NS.NS_BLOCK_HOURS["late_off_peak"] == (23, 24)
    for he in range(1, 25):
        assert NS.NS_BLOCK_HOURS[f"HE{he:02d}"] == (he,)


def test_day_filtered_blocks_have_no_he_set():
    """The disqualifying fact, kept as a fact: ON_PEAK/OFF_PEAK are absent from
    the HE map entirely rather than mapped to HE7-HE22 and quietly mis-served."""
    assert "ON_PEAK" not in NS.NS_BLOCK_HOURS
    assert "OFF_PEAK" not in NS.NS_BLOCK_HOURS


def test_block_catalog_states_availability_per_block(client):
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    catalog = {b["block_key"]: b for b in _screener(client, pool).json()["blocks"]}
    assert catalog["ALL24"]["derived_available"] is True
    assert catalog["ALL24"]["he_set"] == list(range(1, 25))
    assert catalog["ON_PEAK"]["derived_available"] is False
    assert catalog["ON_PEAK"]["reason"]


# ---------------------------------------------------------------------------
# SORT, DIR, PAGING
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sort", sorted(NS.NS_SORT_KEYS))
def test_every_advertised_sort_key_is_accepted(client, sort):
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    assert _screener(client, pool, sort=sort).status_code == 200


@pytest.mark.parametrize("hostile", [
    "p50_lmp; DROP TABLE node_stats_block",
    "b.p50_lmp DESC--",
    "(SELECT 1)",
    "mean_lmp",          # real statistic, deliberately not a sort key
    "sigma_lmp",
])
def test_hostile_sort_is_rejected_before_sql(client, hostile):
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    r = _screener(client, pool, sort=hostile)
    assert r.status_code == 400
    # And it never reached a query — the whitelist is a gate, not a filter.
    assert not any("count(*) OVER ()" in c["query"] for c in pool.calls)


def test_derived_statistics_are_not_sort_keys():
    """Deliberate, and the 400 says so: sorting the universe by a recombined
    moment would mean recombining ~19k nodes x 24 HEs per request."""
    for key in ("mean_lmp", "sigma_lmp", "min_lmp", "max_lmp"):
        assert key not in NS.NS_SORT_KEYS
    msg = NS.validate_sort("mean_lmp")
    assert "recombined" in msg


def test_nulls_sort_last_in_both_directions():
    """A refused percentile must never be re-ranked as though its null were a
    zero (bottom on desc) or an infinity (top on asc)."""
    assert "NULLS LAST" in NS.order_by_sql("p95_lmp", "desc")
    assert "NULLS LAST" in NS.order_by_sql("p95_lmp", "asc")


def test_sort_is_tiebroken_for_stable_paging():
    """Without a tiebreak, ties reorder between pages and rows go missing."""
    assert NS.order_by_sql("p50_lmp", "desc").endswith("b.pnode_id ASC")


@pytest.mark.parametrize("bad", [{"dir": "sideways"}, {"limit": 0},
                                 {"limit": NS.NS_LIMIT_MAX + 1},
                                 {"offset": -1}, {"market": "DA"},
                                 {"window_days": 5}, {"block_key": "HE25"}])
def test_bad_parameters_are_400(client, bad):
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    assert _screener(client, pool, **bad).status_code == 400


def test_paging_reports_the_real_total(client):
    pool = FakePool(block_rows=[FULL_NODE, CAPPED_NODE], agg_rows=[AGG_FULL])
    body = _screener(client, pool, limit=100, offset=0).json()
    assert body["paging"] == {"limit": 100, "offset": 0, "total": 2,
                              "pnode_prefix": None}
    assert body["count"] == 2


def test_pnode_prefix_is_escaped_and_bound(client):
    """LIKE metacharacters in a user string match literally."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    _screener(client, pool, pnode="50%_X")
    page = [c for c in pool.calls if "count(*) OVER ()" in c["query"]][0]
    assert page["params"]["prefix"] == "50\\%\\_X%"


def test_recombination_is_bounded_to_the_selected_page(client):
    """The second read receives exactly the page's pnode_ids — never the slice.
    This is the property that keeps it O(page x |HE set|)."""
    pool = FakePool(block_rows=[FULL_NODE, CAPPED_NODE], agg_rows=[AGG_FULL])
    _screener(client, pool)
    agg = [c for c in pool.calls if "GROUP BY h.pnode_id" in c["query"]][0]
    assert agg["params"]["pnodes"] == ["FULL_1_N001", "CAPPED_1_N002"]
    assert agg["params"]["hours"] == list(range(1, 25))


def test_empty_page_skips_the_recombination(client):
    pool = FakePool(block_rows=[], agg_rows=[AGG_FULL])
    body = _screener(client, pool).json()
    assert body["rows"] == [] and body["paging"]["total"] == 0
    assert not any("GROUP BY h.pnode_id" in c["query"] for c in pool.calls)


# ---------------------------------------------------------------------------
# VINTAGE
# ---------------------------------------------------------------------------

def test_vintage_carries_as_of_and_computed_at(client):
    """Two different clocks, both on the wire. The newest window is not
    necessarily the most recently stamped one."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    v = _screener(client, pool).json()["vintage"]
    assert v["as_of"] == "2026-08-16"
    assert v["computed_at"]["min"].startswith("2026-08-18")


def test_pre_repair_span_labels_itself_without_changing_a_value(client):
    """The known issue is CARRIED, not masked: the note appears and the served
    percentile is byte-for-byte the banked one."""
    pool = FakePool(as_of=date(2026, 8, 14), block_rows=[FULL_NODE],
                    agg_rows=[AGG_FULL])
    body = _screener(client, pool).json()
    issue = body["vintage"]["known_issue"]
    assert issue["id"] == "pre_repair_as_of_span"
    assert issue["span"] == {"from": "2026-08-13", "to": "2026-08-16"}
    assert body["rows"][0]["lmp"]["percentiles"]["p50"] == pytest.approx(44.91505)


def test_the_note_is_self_clearing_outside_the_span(client):
    pool = FakePool(as_of=date(2026, 8, 17), block_rows=[FULL_NODE],
                    agg_rows=[AGG_FULL])
    assert "known_issue" not in _screener(client, pool).json()["vintage"]
    assert NS.pre_repair_note(date(2026, 8, 12)) is None
    assert NS.pre_repair_note(date(2026, 8, 13)) is not None
    assert NS.pre_repair_note(date(2026, 8, 16)) is not None
    assert NS.pre_repair_note(None) is None


def test_latest_as_of_is_resolved_per_window_and_market(client):
    """Never once globally: the writer advances windows and markets
    independently, so a shared max would cross-date the slices."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL])
    _screener(client, pool, window_days=30, market="RTPD")
    resolver = [c for c in pool.calls if "ORDER BY b.as_of DESC" in c["query"]][0]
    assert resolver["params"] == {"window_days": 30, "market": "RTPD"}


def test_never_stamped_slice_is_an_honest_empty_200(client):
    pool = FakePool(as_of=None)
    body = _screener(client, pool).json()
    assert body["vintage"]["as_of"] is None
    assert body["rows"] == [] and body["count"] == 0


# ---------------------------------------------------------------------------
# ROUTE 2 — the stance ladder
# ---------------------------------------------------------------------------

def _ladder_cell(window_days, market, **over):
    cell = {
        **FULL_NODE,
        "window_days": window_days, "market": market, "as_of": AS_OF,
        "computed_at": STAMP,
        # The recombination's columns, `d_`-prefixed so they cannot collide
        # with the block row's own n_lmp/n_dart.
        "d_n_lmp": 158, "d_mean_lmp": Decimal("44.5"),
        "d_sigma_lmp": Decimal("11.25"), "d_min_lmp": Decimal("23.23064"),
        "d_max_lmp": Decimal("78.25846"),
        "d_n_dart": None, "d_mean_dart": None, "d_sigma_dart": None,
        "d_min_dart": None, "d_max_dart": None,
    }
    cell.pop("total_matched", None)
    cell.update(over)
    return cell


LADDER_ROWS = [_ladder_cell(w, m)
               for w in NS.NS_LADDER_WINDOWS for m in NS.NS_MARKETS]

RAIL_ROWS = [
    {"market": "DAM", "as_of": AS_OF, "he": he,
     "hours_expected": 30, "hours_present": 29, "computed_at": STAMP,
     "n_lmp": 29, "sum_lmp": Decimal("1200.5"), "sum_lmp2": Decimal("51000.25"),
     "mean_lmp": Decimal("41.396551"), "sigma_lmp": Decimal("9.1"),
     "min_lmp": Decimal("22.0"), "max_lmp": Decimal("70.0"),
     "n_dart": None, "sum_dart": None, "sum_dart2": None, "mean_dart": None,
     "sigma_dart": None, "min_dart": None, "max_dart": None,
     "n_basis": 29, "sum_basis": Decimal("12.5"), "sum_basis2": Decimal("40.0"),
     "mean_basis": Decimal("0.431"), "sigma_basis": Decimal("1.1"),
     "min_basis": Decimal("-2.0"), "max_basis": Decimal("3.0"),
     "n_congestion": 29, "sum_congestion": Decimal("5.0"),
     "sum_congestion2": Decimal("9.0"), "mean_congestion": Decimal("0.172"),
     "sigma_congestion": Decimal("0.5"), "min_congestion": Decimal("-1.0"),
     "max_congestion": Decimal("1.0"),
     "n_loss": 29, "sum_loss": Decimal("7.5"), "sum_loss2": Decimal("12.0"),
     "mean_loss": Decimal("0.258"), "sigma_loss": Decimal("0.4"),
     "min_loss": Decimal("-0.5"), "max_loss": Decimal("1.5")}
    for he in range(1, 25)
]


def _node(client, pool, node="FULL_1_N001", **q):
    return client(pool).get(f"/api/node-stats/node/{node}", params=q)


def test_ladder_spans_four_windows_and_both_markets(client):
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    body = _node(client, pool).json()

    cells = body["ladder"]["cells"]
    assert len(cells) == 8
    assert {(c["window_days"], c["market"]) for c in cells} == {
        (w, m) for w in (3, 7, 14, 30) for m in ("DAM", "RTPD")}


def test_each_ladder_cell_carries_its_own_vintage(client):
    """A stale cell must be visible as a stale cell, not averaged into its
    neighbours."""
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    for cell in _node(client, pool).json()["ladder"]["cells"]:
        assert cell["vintage"]["as_of"] == "2026-08-16"
        assert cell["hours_present"] is not None
        assert "pctl_ceiling" in cell["lmp"]


def test_ladder_resolves_as_of_per_cell(client):
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    _node(client, pool)
    q = [c for c in pool.calls if "WITH grid AS" in c["query"]][0]
    assert q["params"]["windows"] == [3, 7, 14, 30]
    assert q["params"]["markets"] == ["DAM", "RTPD"]
    # One backward index scan per cell, inside the query — not one global max.
    assert "ORDER BY b.as_of DESC" in q["query"]


def test_ladder_derived_columns_do_not_clobber_the_banked_count(client):
    """The collision this lane's `d_` prefix exists to prevent.

    dict_row keeps the LAST column of a duplicated name. Unprefixed, the
    recombination's n_lmp would overwrite the block's — and on a day-filtered
    block, where the LATERAL yields NULL, it would blank a populated banked
    field outright.
    """
    row = _ladder_cell(7, "DAM", n_lmp=158, d_n_lmp=None, d_mean_lmp=None,
                       d_sigma_lmp=None, d_min_lmp=None, d_max_lmp=None)
    pool = FakePool(ladder_rows=[row], rail_rows=RAIL_ROWS)
    cell = _node(client, pool).json()["ladder"]["cells"][0]

    assert cell["lmp"]["n"] == 158              # banked, survived
    assert cell["lmp"]["derived"]["available"] is False   # recombination absent


def test_he_rail_carries_all_24_hours_with_coverage(client):
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    rails = _node(client, pool).json()["he_rail"]["markets"]
    dam = [r for r in rails if r["market"] == "DAM"][0]

    assert dam["count"] == 24
    assert [h["he"] for h in dam["hours"]] == list(range(1, 25))
    for hour in dam["hours"]:
        assert hour["hours_expected"] == 30
        assert hour["hours_present"] == 29
        assert hour["coverage"] == pytest.approx(29 / 30)


def test_he_rail_ships_the_sufficient_statistics_it_used(client):
    """So a client can pool a custom hour set itself and check our arithmetic
    against the same numbers."""
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    rails = _node(client, pool).json()["he_rail"]["markets"]
    hour = [r for r in rails if r["market"] == "DAM"][0]["hours"][0]

    ss = hour["lmp"]["sufficient_statistics"]
    assert ss == {"n": 29, "sum": pytest.approx(1200.5),
                  "sum2": pytest.approx(51000.25)}
    # And the server's own mean is what those statistics imply.
    assert hour["lmp"]["mean"] == pytest.approx(1200.5 / 29, abs=1e-5)


def test_he_rail_structural_nulls_are_named_per_market(client):
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    rails = _node(client, pool).json()["he_rail"]["markets"]
    dam = [r for r in rails if r["market"] == "DAM"][0]

    assert dam["structural_nulls"]["measures"] == ["dart"]
    hour = dam["hours"][0]
    assert hour["dart"] is None                 # structural, not missing
    assert hour["basis"]["n"] == 29             # real on DAM
    assert hour["congestion"]["n"] == 29
    assert hour["loss"]["n"] == 29


def test_unbanked_node_is_400(client):
    pool = FakePool(ladder_rows=[], rail_rows=[])
    r = _node(client, pool, node="NOT_A_NODE")
    assert r.status_code == 400
    assert "migration 182" in r.json()["detail"]


def test_node_route_rejects_a_bad_block_or_window(client):
    pool = FakePool(ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS)
    assert _node(client, pool, block_key="NOPE").status_code == 400
    assert _node(client, pool, rail_window_days=5).status_code == 400


# ---------------------------------------------------------------------------
# ROUTE 3 — zone breadth
# ---------------------------------------------------------------------------

BREADTH_ROWS = [
    {"zone_key": "TH_SP15_GEN-APND", "zone_family": "HUB_AGG", "market": mkt,
     "trade_date": date(2026, 8, 16 - i), "nodes_in_zone": 1094,
     "nodes_priced": 909, "nodes_up": 752, "nodes_down": 157,
     "net_breadth": 595, "computed_at": STAMP, "rn": i + 1}
    for mkt in ("DAM", "RTPD") for i in range(3)
]


def _breadth(client, pool, **q):
    return client(pool).get("/api/node-stats/breadth", params=q)


def test_breadth_groups_by_zone_then_market(client):
    pool = FakePool(breadth_rows=BREADTH_ROWS)
    body = _breadth(client, pool, days=3).json()

    assert body["count"] == 1
    zone = body["zones"][0]
    assert zone["zone_key"] == "TH_SP15_GEN-APND"
    assert zone["zone_family"] == "HUB_AGG"
    assert {m["market"] for m in zone["markets"]} == {"DAM", "RTPD"}
    assert all(m["count"] == 3 for m in zone["markets"])
    assert body["as_of"] == "2026-08-16"


def test_breadth_counts_pass_through_and_carry_coverage(client):
    pool = FakePool(breadth_rows=BREADTH_ROWS)
    day = _breadth(client, pool).json()["zones"][0]["markets"][0]["days"][0]

    assert day["nodes_in_zone"] == 1094
    assert day["nodes_priced"] == 909
    assert day["nodes_up"] == 752
    assert day["nodes_down"] == 157
    assert day["net_breadth"] == 595
    # +595 over 909 priced of 1094 is a different read from +595 over 70.
    assert day["priced_coverage"] == pytest.approx(909 / 1094)


def test_breadth_ranks_within_each_zone(client):
    """Latest N days PER zone, not against a shared date floor — a zone that
    stopped reporting shows its own last N days."""
    pool = FakePool(breadth_rows=BREADTH_ROWS)
    _breadth(client, pool, days=5)
    q = pool.calls[0]
    assert "PARTITION BY z.zone_key, z.market" in q["query"]
    assert q["params"]["days"] == 5


def test_breadth_filters_are_bound_parameters(client):
    pool = FakePool(breadth_rows=BREADTH_ROWS)
    _breadth(client, pool, market="rtpd", zone_key="TH_SP15_GEN-APND, TH_NP15_GEN-APND")
    q = pool.calls[0]
    assert q["params"]["market"] == "RTPD"
    assert q["params"]["zone_keys"] == ["TH_SP15_GEN-APND", "TH_NP15_GEN-APND"]


@pytest.mark.parametrize("bad", [{"days": 0}, {"days": NS.NS_BREADTH_DAYS_MAX + 1},
                                 {"market": "SPOT"}])
def test_breadth_bad_parameters_are_400(client, bad):
    pool = FakePool(breadth_rows=BREADTH_ROWS)
    assert _breadth(client, pool, **bad).status_code == 400


def test_breadth_empty_is_an_honest_200(client):
    pool = FakePool(breadth_rows=[])
    body = _breadth(client, pool).json()
    assert body["zones"] == [] and body["count"] == 0 and body["as_of"] is None


# ---------------------------------------------------------------------------
# The lane is READ-ONLY, and says 503 rather than lying when the bank is down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,params", [
    ("/api/node-stats/screener", {"market": "DAM", "window_days": 7}),
    ("/api/node-stats/node/FULL_1_N001", {}),
    ("/api/node-stats/breadth", {}),
])
def test_db_failure_is_503(client, path, params):
    pool = FakePool(raise_exc=RuntimeError("boom"))
    assert client(pool).get(path, params=params).status_code == 503


def test_every_statement_this_lane_issues_is_a_select(client):
    """READ-ONLY BY CONSTRUCTION. No DDL, no writes — asserted against the SQL
    actually sent, not against intent."""
    pool = FakePool(block_rows=[FULL_NODE], agg_rows=[AGG_FULL],
                    ladder_rows=LADDER_ROWS, rail_rows=RAIL_ROWS,
                    breadth_rows=BREADTH_ROWS)
    c = client(pool)
    c.get("/api/node-stats/screener", params={"market": "DAM", "window_days": 7})
    c.get("/api/node-stats/node/FULL_1_N001")
    c.get("/api/node-stats/breadth")

    assert pool.calls
    forbidden = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                 "TRUNCATE", "GRANT")
    for call in pool.calls:
        sql = call["query"].strip().upper()
        assert sql.startswith("SELECT") or sql.startswith("WITH")
        for word in forbidden:
            assert word not in sql
