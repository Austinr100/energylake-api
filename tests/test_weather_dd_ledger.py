"""
Tests for the Degree Day Ledger, Phase B (2026-08-10):
  GET /api/weather/dd/regions
  GET /api/weather/dd/daily
  GET /api/weather/dd/cumulative
  GET /api/weather/dd/socalgas-grade
  GET /api/weather/dd/forecast

CONTRACT TESTS OVER THE REAL VIEW SHAPES. Every fixture row below is the column
set migration 159/160/161/164 actually publish, verified against production on
2026-08-10 via information_schema plus live reads — not a shape invented here.
The live numbers the fixtures reproduce:

  socalgas_territory 2026-07-31 MTD -> 421.25 CDD vs 372.755 normal, +48.495,
                                       complete (31 of 31 days)
  caiso_np15_proxy   2026-07-31 MTD -> NULL cdd, complete=false, 30 of 31 days
  desert_sw          2026-07-31 STD -> 2178.77 CDD, complete (92 of 92)
  socalgas_territory 2026-07-31 STD -> NULL, 90 of 92 days
  2026-04-15 / 2025-10-20 STD       -> a ROW per region with season=NULL and
                                       every total NULL (shoulder months)

Most of the suite exercises the pure layer in degree_days.py directly; route
tests use the _FakePool idiom from test_weather_delta_board.py.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

import main
import degree_days as dd


D = datetime.date


# ---------------------------------------------------------------------------
# Fixtures shaped exactly like the views
# ---------------------------------------------------------------------------

def _mtd(region, *, days_complete=31, days_in_window=31, complete=True,
         normals_complete=True, hdd=0.0, cdd=421.25, hdd_norm=0.14,
         cdd_norm=372.755, hdd_dep=-0.14, cdd_dep=48.495, obs_date=D(2026, 7, 31)):
    return {
        "region": region, "version": "v1", "obs_date": obs_date,
        "window_start": D(obs_date.year, obs_date.month, 1),
        "window_label": "2011-2025",
        "days_in_window": days_in_window, "days_complete": days_complete,
        "normals_days_complete": 31,
        "complete": complete, "normals_complete": normals_complete,
        "hdd": hdd, "cdd": cdd, "hdd_norm": hdd_norm, "cdd_norm": cdd_norm,
        "hdd_departure": hdd_dep, "cdd_departure": cdd_dep,
        "ly_obs_date": D(2025, 7, 31), "ly_days_in_window": 31,
        "ly_days_complete": 31, "ly_complete": True,
        "ly_hdd": 0.0, "ly_cdd": 277.15,
        "hdd_vs_ly": -0.14, "cdd_vs_ly": 144.10,
    }


def _std(region, *, season="cdd", days_complete=92, days_in_window=92,
         complete=True, normals_complete=True, hdd=8.84, cdd=2178.77,
         obs_date=D(2026, 7, 31)):
    shoulder = season is None
    return {
        "region": region, "version": "v1", "obs_date": obs_date,
        "season": season,
        "season_start": None if shoulder else D(2026, 5, 1),
        "window_label": None if shoulder else "2011-2025",
        "days_in_window": None if shoulder else days_in_window,
        "days_complete": None if shoulder else days_complete,
        "normals_days_complete": None if shoulder else 92,
        "complete": None if shoulder else complete,
        "normals_complete": None if shoulder else normals_complete,
        "hdd": None if shoulder else hdd, "cdd": None if shoulder else cdd,
        "hdd_norm": None, "cdd_norm": None,
        "hdd_departure": None, "cdd_departure": None,
        "ly_obs_date": None, "ly_season_start": None,
        "ly_days_in_window": None, "ly_days_complete": None, "ly_complete": None,
        "ly_hdd": None, "ly_cdd": None, "hdd_vs_ly": None, "cdd_vs_ly": None,
    }


def _daily(region, obs_date, *, basis_complete=True, members_present=5,
           member_stations=5, missing=None, cdd=13.6, cdd_dep=1.55):
    return {
        "region": region, "version": "v1", "obs_date": obs_date,
        "basis_complete": basis_complete, "normals_complete": True,
        "tavg_f": 78.6, "hdd": 0.0, "cdd": cdd,
        "tavg_norm_f": 77.05, "hdd_norm": 0.0, "cdd_norm": 12.05,
        "window_label": "2011-2025",
        "tavg_departure_f": 1.55, "hdd_departure": 0.0, "cdd_departure": cdd_dep,
        "member_stations": member_stations, "members_present": members_present,
        "missing_stations": missing, "normals_missing_stations": None,
        "min_sample_count": 15,
    }


def _weight(region, station_id, weight, note=None):
    return {"region": region, "station_id": station_id, "weight": weight,
            "version": "v1",
            "vintage": datetime.datetime(2026, 8, 9, 17, 40, 26,
                                         tzinfo=datetime.timezone.utc),
            "note": note}


def _block(payload, region, which):
    return next(r for r in payload["regions"] if r["region"] == region)[which]


# ═══════════════════════════════════════════════════════════════════════════
# THE RULE: a NULL total is null, with counts — never zero, never summed
# ═══════════════════════════════════════════════════════════════════════════

def test_complete_region_month_returns_numbers():
    """socalgas_territory July 2026: 421.25 CDD vs 372.755, +48.495, no absence."""
    payload = dd.cumulative_payload([_mtd("socalgas_territory")], [],
                                    as_of=D(2026, 7, 31), as_of_source="requested")
    mtd = _block(payload, "socalgas_territory", "mtd")
    assert mtd["cdd"] == 421.25
    assert mtd["cdd_norm"] == 372.755
    assert mtd["cdd_departure"] == 48.495
    assert mtd["complete"] is True
    assert mtd["days_complete"] == 31 and mtd["days_in_window"] == 31
    assert mtd["absence"] is None


def test_incomplete_region_month_returns_nulls_with_counts():
    """MUTATION RECEIPT. Coalesce a NULL total to 0 in the handler and this dies.

    caiso_np15_proxy on 2026-07-31 is 30 of 31 days, so the view publishes NULL
    totals. `is None` is asserted, not falsiness — `assert not mtd["cdd"]` would
    pass for 0.0 and is exactly the check that would let the defect through.
    """
    row = _mtd("caiso_np15_proxy", days_complete=30, complete=False,
               hdd=None, cdd=None, hdd_dep=None, cdd_dep=None)
    payload = dd.cumulative_payload([row], [], as_of=D(2026, 7, 31),
                                    as_of_source="requested")
    mtd = _block(payload, "caiso_np15_proxy", "mtd")

    assert mtd["cdd"] is None
    assert mtd["hdd"] is None
    assert mtd["cdd_departure"] is None
    assert mtd["cdd"] != 0 and mtd["cdd"] != 0.0

    # The normals ARE complete for that window, so they still serve — absence is
    # about the observation basis, not a blanket blackout of the row.
    assert mtd["cdd_norm"] == 372.755

    # ...and the reason travels with it, in the view's own counts.
    assert mtd["absence"] == {
        "reason": "incomplete_obs",
        "message": "30 of 31 days",
        "days_complete": 30,
        "days_in_window": 31,
    }


def test_incomplete_season_to_date_returns_nulls_with_counts():
    """desert_sw completes (2178.77); socalgas is 90 of 92 and stays null."""
    rows = [
        _std("desert_sw"),
        _std("socalgas_territory", days_complete=90, complete=False,
             hdd=None, cdd=None),
    ]
    payload = dd.cumulative_payload([], rows, as_of=D(2026, 7, 31),
                                    as_of_source="requested")

    done = _block(payload, "desert_sw", "std")
    assert done["cdd"] == 2178.77 and done["complete"] is True
    assert done["absence"] is None
    assert done["season"] == "cdd" and done["season_start"] == "2026-05-01"

    short = _block(payload, "socalgas_territory", "std")
    assert short["cdd"] is None and short["hdd"] is None
    assert short["absence"]["reason"] == "incomplete_obs"
    assert short["absence"]["message"] == "90 of 92 days"
    assert short["absence"]["days_complete"] == 90
    assert short["absence"]["days_in_window"] == 92


@pytest.mark.parametrize("shoulder", [D(2026, 4, 15), D(2025, 10, 20)])
def test_shoulder_months_return_season_null_and_no_std(shoulder):
    """Apr and Oct: the view returns a ROW with season NULL, not zero rows.

    `no_season` is a different answer from `incomplete_obs`: there is no ruled
    anchor covering this date, so there is no season to be short of.
    """
    payload = dd.cumulative_payload(
        [], [_std("pnw", season=None, obs_date=shoulder)],
        as_of=shoulder, as_of_source="requested")
    std = _block(payload, "pnw", "std")

    assert std["present"] is True          # the row exists...
    assert std["season"] is None           # ...and says there is no season
    assert std["season_start"] is None
    assert std["hdd"] is None and std["cdd"] is None
    assert std["absence"]["reason"] == "no_season"
    assert std["absence"]["days_complete"] is None


def test_normals_incomplete_is_its_own_reason():
    row = _mtd("pnw", complete=True, normals_complete=False,
               hdd_dep=None, cdd_dep=None)
    row["normals_days_complete"] = 28
    mtd = dd.cumulative_block(row, kind="mtd")
    assert mtd["absence"]["reason"] == "incomplete_norm"
    assert mtd["absence"]["normals_days_complete"] == 28
    # Observed totals survive; only the departure is missing its baseline.
    assert mtd["cdd"] == 421.25 and mtd["cdd_departure"] is None


def test_std_totals_serve_while_the_seasonal_departure_does_not():
    """The live 2026-08-06 STD shape, verified on production 2026-08-10.

    Every region's STD row carries normals_complete=false with
    normals_days_complete=219 against days_in_window=98 — the two counts are not
    commensurate, so the message stays plain rather than claiming "219 of 98
    days". desert_sw's OBSERVED season total still serves (2381.59 CDD, 98 of
    98); only the departure is missing its baseline.
    """
    row = _std("desert_sw", days_complete=98, days_in_window=98, complete=True,
               normals_complete=False, hdd=8.84, cdd=2381.59,
               obs_date=D(2026, 8, 6))
    row["normals_days_complete"] = 219
    std = dd.cumulative_block(row, kind="std")

    assert std["cdd"] == 2381.59            # observed total is whole and serves
    assert std["cdd_departure"] is None     # ...the departure has no baseline
    assert std["absence"]["reason"] == "incomplete_norm"
    assert std["absence"]["message"] == "normals basis incomplete for this window"
    assert std["absence"]["normals_days_complete"] == 219
    assert std["absence"]["days_in_window"] == 98


def test_missing_row_is_absence_not_an_error():
    mtd = dd.cumulative_block(None, kind="mtd")
    assert mtd["present"] is False
    assert mtd["hdd"] is None and mtd["cdd"] is None
    assert mtd["absence"]["reason"] == "no_row"
    # Same key set as a present block, so the client has one branch, not two.
    present = dd.cumulative_block(_mtd("pnw"), kind="mtd")
    assert set(mtd) == set(present)


def test_last_year_deltas_are_the_views_arithmetic_not_ours():
    """The view left cdd_vs_ly NULL; holding both totals is not a licence to subtract."""
    row = _mtd("pnw", days_complete=30, complete=False, hdd=None, cdd=None)
    row["cdd_vs_ly"] = None
    row["hdd_vs_ly"] = None
    mtd = dd.cumulative_block(row, kind="mtd")
    assert mtd["last_year"]["cdd"] == 277.15       # last year is complete...
    assert mtd["last_year"]["cdd_vs_ly"] is None   # ...the comparison still is not


def test_board_is_built_for_every_region_and_filtered_in_memory():
    board = dd.cumulative_payload(
        [_mtd(r) for r in ("caiso_np15_proxy", "caiso_sp15_proxy", "desert_sw",
                           "pnw", "socalgas_territory")],
        [_std(r) for r in ("caiso_np15_proxy", "desert_sw")],
        as_of=D(2026, 7, 31), as_of_source="latest_obs")
    assert board["region_count"] == 5
    assert board["vector_version"] == "v1"
    # Regions present in only one of the two views still get both blocks.
    assert _block(board, "pnw", "std")["absence"]["reason"] == "no_row"

    one = dd.filter_board(board, "desert_sw")
    assert one["region_count"] == 1
    assert one["region_requested"] == "desert_sw"
    assert board["region_count"] == 5          # the cached board is not mutated


# ═══════════════════════════════════════════════════════════════════════════
# /daily — completeness rides every row
# ═══════════════════════════════════════════════════════════════════════════

def test_daily_row_carries_completeness_and_missing_stations():
    row = dd.daily_row(_daily("socalgas_territory", D(2026, 8, 6),
                              basis_complete=False, members_present=4,
                              missing=["USW00023188"]))
    assert row["basis_complete"] is False
    assert row["members_present"] == 4 and row["member_stations"] == 5
    assert row["missing_stations"] == ["USW00023188"]
    assert row["normals_missing_stations"] == []      # SQL NULL -> "none missing"
    assert row["min_sample_count"] == 15


def test_daily_payload_reports_the_right_edge_not_today():
    rows = [_daily("pnw", D(2026, 8, d)) for d in (4, 5, 6)]
    payload = dd.daily_payload(rows, start=D(2026, 8, 1), end=D(2026, 8, 10),
                               regions_requested=["pnw"])
    assert payload["obs_through"] == "2026-08-06"     # GHCND lags 2-4 days
    assert payload["end"] == "2026-08-10"
    assert payload["row_count"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# /socalgas-grade — the arbiter
# ═══════════════════════════════════════════════════════════════════════════

def test_grade_row_signs_and_absolute_delta():
    row = dd.grade_row({
        "obs_date": D(2026, 8, 3), "version": "v1",
        "el_composite_temp_f": 80.40, "scg_composite_temp_f": 80.0,
        "delta_f": 0.40, "basis_complete": True, "arbiter_present": True,
        "member_stations": 5, "members_present": 5, "missing_stations": None,
    })
    assert row["delta_f"] == 0.40            # ours minus theirs; + = we read warmer
    assert row["abs_delta_f"] == 0.40
    assert row["missing_stations"] == []


def test_ungraded_day_is_not_a_zero_delta_day():
    row = dd.grade_row({
        "obs_date": D(2026, 8, 8), "version": "v1",
        "el_composite_temp_f": 79.5, "scg_composite_temp_f": None,
        "delta_f": None, "basis_complete": True, "arbiter_present": False,
        "member_stations": 5, "members_present": 5, "missing_stations": None,
    })
    assert row["arbiter_present"] is False
    assert row["delta_f"] is None and row["abs_delta_f"] is None


def test_grade_summary_keeps_n_days_and_n_graded_apart():
    """The live all-time shape: 29,956 rows in scope, 6,069 of them graded."""
    s = dd.grade_summary({
        "n_days": 29956, "n_graded": 6069, "mean_delta_f": 0.7546,
        "sd_delta_f": 2.4551, "mean_abs_delta_f": 1.7787,
        "min_delta_f": -12.4, "max_delta_f": 11.9,
        "first_graded": D(2009, 12, 15), "last_graded": D(2026, 8, 3),
    })
    assert s["n_days"] == 29956 and s["n_graded"] == 6069
    assert s["mean_delta_f"] == 0.7546
    assert s["first_graded"] == "2009-12-15"


def test_grade_summary_sd_is_null_below_two_graded_days():
    s = dd.grade_summary({"n_days": 1, "n_graded": 1, "mean_delta_f": 0.4,
                          "sd_delta_f": None, "mean_abs_delta_f": 0.4})
    assert s["sd_delta_f"] is None       # not 0.0 — that would claim agreement


def test_grade_summary_of_nothing_is_zeros_and_nulls():
    s = dd.grade_summary(None)
    assert s["n_graded"] == 0 and s["mean_delta_f"] is None


# ═══════════════════════════════════════════════════════════════════════════
# /forecast — the run-over-run delta is the trader number
# ═══════════════════════════════════════════════════════════════════════════

def _fc(station, target, issued, rn, *, hdd=0, cdd=14, tavg=79):
    return {"station_id": station, "target_date": target, "issued_ts": issued,
            "rn": rn, "tmax_f": 92, "tmin_f": 66, "tavg_f": tavg,
            "hdd": hdd, "cdd": cdd, "basis_complete": True,
            "hours_covered": 24, "hours_required": 24,
            "source_product": "nws_gridpoint", "gridpoint_id": "LOX/155,44",
            "icao": "KLAX"}


def test_run_over_run_delta_is_computed_against_the_prior_issuance():
    t = datetime.datetime(2026, 8, 12, tzinfo=datetime.timezone.utc)
    prior = datetime.datetime(2026, 8, 10, 6, tzinfo=datetime.timezone.utc)
    now = datetime.datetime(2026, 8, 10, 18, tzinfo=datetime.timezone.utc)
    rows = [_fc("USW00023174", t.date(), now, 1, cdd=17, tavg=82),
            _fc("USW00023174", t.date(), prior, 2, cdd=14, tavg=79)]

    payload = dd.forecast_payload(rows, station="USW00023174",
                                  from_date=D(2026, 8, 10), days=15)
    cell = payload["rows"][0]
    assert cell["cdd"] == 17 and cell["prior_cdd"] == 14
    assert cell["delta_cdd"] == 3.0
    assert cell["delta_tavg_f"] == 3.0
    assert cell["delta_basis"] == "run_over_run"
    assert cell["delta_absence"] is None
    assert payload["rows_with_run_over_run_delta"] == 1
    assert payload["board_state"] == "populated"


def test_first_sighting_has_no_delta_and_says_so():
    now = datetime.datetime(2026, 8, 10, 18, tzinfo=datetime.timezone.utc)
    payload = dd.forecast_payload([_fc("USW00023174", D(2026, 8, 24), now, 1)],
                                  station="USW00023174",
                                  from_date=D(2026, 8, 10), days=15)
    cell = payload["rows"][0]
    assert cell["delta_cdd"] is None and cell["delta_hdd"] is None
    assert cell["delta_cdd"] != 0                 # a first run is not "unchanged"
    assert cell["delta_basis"] is None
    assert cell["delta_absence"]["reason"] == "no_prior_issuance"
    assert payload["rows_with_run_over_run_delta"] == 0


def test_empty_forecast_board_is_an_absence_not_an_empty_list():
    """station_degree_days_forecast holds 0 rows today (measured 2026-08-10)."""
    payload = dd.forecast_payload([], station=None,
                                  from_date=D(2026, 8, 10), days=15)
    assert payload["board_state"] == "empty"
    assert payload["rows"] == []
    assert payload["absence"]["reason"] == "no_rows"
    assert "absence, not a zero forecast" in payload["absence"]["message"]
    assert payload["newest_issued_ts"] is None


# ═══════════════════════════════════════════════════════════════════════════
# /regions — the ratified vectors
# ═══════════════════════════════════════════════════════════════════════════

def test_regions_payload_groups_members_and_reports_weight_sum():
    rows = [_weight("pnw", "USW00024233", 0.4, note="ratified 2026-08-09"),
            _weight("pnw", "USW00024229", 0.3),
            _weight("pnw", "USW00024157", 0.2),
            _weight("pnw", "USW00024131", 0.1),
            _weight("desert_sw", "USW00023183", 1.0)]
    payload = dd.regions_payload(rows)
    assert payload["vector_version"] == "v1"
    assert payload["region_count"] == 2
    pnw = next(r for r in payload["regions"] if r["region"] == "pnw")
    assert pnw["member_stations"] == 4
    assert pnw["weight_sum"] == 1.0
    assert [m["station_id"] for m in pnw["members"]][0] == "USW00024233"  # heaviest first


def test_weight_sum_is_reported_not_normalized():
    payload = dd.regions_payload([_weight("x", "A", 0.4), _weight("x", "B", 0.4)])
    assert payload["regions"][0]["weight_sum"] == 0.8     # says so, does not fix it


# ═══════════════════════════════════════════════════════════════════════════
# Route wiring: cache envelope, params, 503 — via a fake pool
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, script, fail):
        self._script, self._fail, self._rows = script, fail, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        if self._fail:
            raise RuntimeError("boom")
        # Route by a distinctive fragment of each statement, so one fake pool
        # serves a handler that makes several different reads.
        for needle, rows in self._script:
            if needle in query:
                self._rows = rows
                return
        self._rows = []

    async def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, script, fail):
        self._script, self._fail = script, fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self):
        return _FakeCursor(self._script, self._fail)


class _FakePool:
    def __init__(self, script, fail=False):
        self._script, self._fail = script, fail

    def connection(self):
        return _FakeConn(self._script, self._fail)


_EDGE = [{"obs_through": D(2026, 8, 6)}]
_VECTORS = [_weight("socalgas_territory", "USW00023174", 0.4),
            _weight("socalgas_territory", "USW00023152", 0.6),
            _weight("caiso_np15_proxy", "USW00023234", 1.0)]


@pytest.fixture(autouse=True)
def _clear_dd_caches():
    for c in main._DD_CACHES:
        c.clear()
    yield
    for c in main._DD_CACHES:
        c.clear()


def test_regions_route_serves_vectors_and_states_its_age(monkeypatch):
    monkeypatch.setattr(main, "_pool",
                        _FakePool([("dd_region_weights_active", _VECTORS)]))
    client = TestClient(main.app)
    resp = client.get("/api/weather/dd/regions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["region_count"] == 2
    assert body["vector_version"] == "v1"

    # Every cached response states its own age — body AND headers.
    assert body["cache"]["state"] == "miss"
    assert body["cache"]["age_seconds"] >= 0
    assert body["cache"]["ttl_seconds"] == main._DD_REGIONS_TTL
    assert resp.headers["X-Cache"] == "miss"
    assert "Age" in resp.headers
    assert resp.headers["Cache-Control"] == "max-age=900"

    # Second call is served from cache even though the pool would now fail.
    monkeypatch.setattr(main, "_pool", _FakePool([], fail=True))
    again = client.get("/api/weather/dd/regions")
    assert again.status_code == 200
    assert again.json()["cache"]["state"] == "fresh"
    assert again.headers["X-Cache"] == "hit"


def test_cumulative_route_serves_the_board_and_names_its_absences(monkeypatch):
    script = [
        ("dd_region_weights_active", _VECTORS),
        ("AS obs_through", _EDGE),
        ("FROM dd_region_mtd", [
            _mtd("socalgas_territory"),
            _mtd("caiso_np15_proxy", days_complete=30, complete=False,
                 hdd=None, cdd=None, hdd_dep=None, cdd_dep=None)]),
        ("FROM dd_region_std", [
            _std("socalgas_territory", days_complete=90, complete=False,
                 hdd=None, cdd=None),
            _std("caiso_np15_proxy", days_complete=89, complete=False,
                 hdd=None, cdd=None)]),
    ]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    client = TestClient(main.app)
    resp = client.get("/api/weather/dd/cumulative?as_of=2026-07-31")
    assert resp.status_code == 200
    body = resp.json()

    assert body["as_of"] == "2026-07-31"
    assert body["as_of_source"] == "requested"
    assert body["obs_through"] == "2026-08-06"
    assert body["region_count"] == 2

    complete = _block(body, "socalgas_territory", "mtd")
    assert complete["cdd"] == 421.25 and complete["absence"] is None

    incomplete = _block(body, "caiso_np15_proxy", "mtd")
    assert incomplete["cdd"] is None
    assert incomplete["absence"]["message"] == "30 of 31 days"

    assert _block(body, "socalgas_territory", "std")["absence"]["message"] \
        == "90 of 92 days"
    assert body["cache"]["ttl_seconds"] == main._DD_CUMULATIVE_TTL


def test_cumulative_defaults_as_of_to_the_composite_right_edge(monkeypatch):
    script = [("dd_region_weights_active", _VECTORS),
              ("AS obs_through", _EDGE),
              ("FROM dd_region_mtd", [_mtd("pnw", obs_date=D(2026, 8, 6))]),
              ("FROM dd_region_std", [])]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    body = TestClient(main.app).get("/api/weather/dd/cumulative").json()
    assert body["as_of"] == "2026-08-06"          # the data's edge, not today
    assert body["as_of_source"] == "latest_obs"


def test_cumulative_region_filter_narrows_the_cached_board(monkeypatch):
    script = [("dd_region_weights_active", _VECTORS),
              ("AS obs_through", _EDGE),
              ("FROM dd_region_mtd", [_mtd("socalgas_territory"),
                                      _mtd("caiso_np15_proxy")]),
              ("FROM dd_region_std", [])]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    client = TestClient(main.app)
    resp = client.get("/api/weather/dd/cumulative"
                      "?as_of=2026-07-31&region=socalgas_territory")
    body = resp.json()
    assert body["region_count"] == 1
    assert body["regions"][0]["region"] == "socalgas_territory"

    # The board was cached whole: the unfiltered call is a cache HIT, proving the
    # five-region build is shared rather than re-run per region.
    whole = client.get("/api/weather/dd/cumulative?as_of=2026-07-31").json()
    assert whole["region_count"] == 2
    assert whole["cache"]["state"] == "fresh"


def test_unknown_region_is_a_400_naming_the_vocabulary(monkeypatch):
    monkeypatch.setattr(main, "_pool",
                        _FakePool([("dd_region_weights_active", _VECTORS)]))
    resp = TestClient(main.app).get("/api/weather/dd/daily?region=ercot_north")
    assert resp.status_code == 400
    assert "caiso_np15_proxy" in resp.json()["detail"]


def test_daily_route_and_its_window_cap(monkeypatch):
    script = [("dd_region_weights_active", _VECTORS),
              ("AS obs_through", _EDGE),
              ("dd_region_daily_vs_normal", [_daily("pnw", D(2026, 8, 6))])]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    client = TestClient(main.app)

    ok = client.get("/api/weather/dd/daily?start=2026-08-01&end=2026-08-06")
    assert ok.status_code == 200
    assert ok.json()["row_count"] == 1
    assert ok.json()["rows"][0]["missing_stations"] == []

    too_wide = client.get("/api/weather/dd/daily?start=2000-01-01&end=2026-08-06")
    assert too_wide.status_code == 400
    assert "cap" in too_wide.json()["detail"]

    backwards = client.get("/api/weather/dd/daily?start=2026-08-06&end=2026-08-01")
    assert backwards.status_code == 400

    assert client.get("/api/weather/dd/daily?start=august").status_code == 400


def test_grade_route_carries_window_and_all_time_summaries(monkeypatch):
    summary = [{"n_days": 29956, "n_graded": 6069, "mean_delta_f": 0.7546,
                "sd_delta_f": 2.4551, "mean_abs_delta_f": 1.7787,
                "min_delta_f": -12.4, "max_delta_f": 11.9,
                "first_graded": D(2009, 12, 15), "last_graded": D(2026, 8, 3)}]
    script = [("AS obs_through", _EDGE),
              ("FROM dd_socalgas_grade\n    WHERE obs_date BETWEEN", [{
                  "obs_date": D(2026, 8, 3), "version": "v1",
                  "el_composite_temp_f": 80.4, "scg_composite_temp_f": 80.0,
                  "delta_f": 0.4, "basis_complete": True, "arbiter_present": True,
                  "member_stations": 5, "members_present": 5,
                  "missing_stations": None}]),
              ("stddev_samp", summary)]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    body = TestClient(main.app).get("/api/weather/dd/socalgas-grade").json()
    assert body["row_count"] == 1
    assert body["summary"]["all_time"]["n_graded"] == 6069
    assert body["summary"]["all_time"]["mean_delta_f"] == 0.7546
    assert "finding about OUR station weights" in body["note"]


def test_forecast_route_is_honestly_empty_today(monkeypatch):
    monkeypatch.setattr(main, "_pool",
                        _FakePool([("station_degree_days_forecast", [])]))
    resp = TestClient(main.app).get("/api/weather/dd/forecast?station=USW00023174")
    assert resp.status_code == 200
    body = resp.json()
    assert body["board_state"] == "empty"
    assert body["absence"]["reason"] == "no_rows"
    assert body["station"] == "USW00023174"


def test_forecast_horizon_cap_is_enforced_by_the_signature(monkeypatch):
    monkeypatch.setattr(main, "_pool",
                        _FakePool([("station_degree_days_forecast", [])]))
    assert TestClient(main.app).get(
        "/api/weather/dd/forecast?days=90").status_code == 422


@pytest.mark.parametrize("path", [
    "/api/weather/dd/regions",
    "/api/weather/dd/daily",
    "/api/weather/dd/cumulative",
    "/api/weather/dd/socalgas-grade",
    "/api/weather/dd/forecast",
])
def test_every_dd_route_503s_when_the_db_is_down(monkeypatch, path):
    monkeypatch.setattr(main, "_pool", _FakePool([], fail=True))
    assert TestClient(main.app).get(path).status_code == 503


def test_stale_board_is_served_labelled_rather_than_rebuilt_on_the_request(monkeypatch):
    """Stale-while-revalidate: past the TTL the caller still gets an answer, and
    that answer says it is stale. A fresh-looking stale number is the failure
    this desk refuses; a labelled one is honest."""
    script = [("dd_region_weights_active", _VECTORS),
              ("AS obs_through", _EDGE),
              ("FROM dd_region_mtd", [_mtd("pnw")]),
              ("FROM dd_region_std", [])]
    monkeypatch.setattr(main, "_pool", _FakePool(script))
    client = TestClient(main.app)
    first = client.get("/api/weather/dd/cumulative?as_of=2026-07-31")
    assert first.json()["cache"]["state"] == "miss"

    # Age the entry past its TTL without touching the clock.
    entry = main._dd_cumulative_cache._entries[D(2026, 7, 31)]
    entry.built_mono -= (main._DD_CUMULATIVE_TTL + 5)

    stale = client.get("/api/weather/dd/cumulative?as_of=2026-07-31")
    assert stale.status_code == 200
    assert stale.json()["cache"]["state"] == "stale"
    assert stale.headers["X-Cache"] == "stale"
    assert stale.json()["cache"]["age_seconds"] > main._DD_CUMULATIVE_TTL
    assert int(stale.headers["Age"]) > main._DD_CUMULATIVE_TTL
    # The numbers are still there — stale is labelled, not withheld.
    assert _block(stale.json(), "pnw", "mtd")["cdd"] == 421.25
