"""
Tests for the Interconnection Queue API v0 (2026-08-12):
  GET /api/interconnection/summary
  GET /api/interconnection/projects
  GET /api/interconnection/events
  GET /api/interconnection/rollups   (v0.1, 2026-08-13)

CONTRACT TESTS OVER THE REAL COLUMN SHAPES. Every fixture row below carries the
column set caiso_interconnection_queue / tape_interconnection_events actually
publish, verified against production on 2026-08-12 via information_schema plus
live reads — not a shape invented here.

The live facts these tests pin (read 2026-08-12, snapshot_date 2026-08-07):

  2,278 rows, all present_in_snapshot, 492,196.8 MW
  3 statuses          -> ACTIVE 268 / COMPLETED 250 / WITHDRAWN 1,760
  1,721 withdrawn_date rows; 39 status=WITHDRAWN carry NO withdrawn_date
  41 distinct generation_type strings (tests/fixtures/, checked in)
  queue_date 1999-11 .. 2023; 2021 vintage -> 361 projects, 105,537.135 MW
                              enqueued, 253 / 70,222.325 MW withdrawn
  255 rows have no proposed_completion_date  -> `undated`
  106 distinct (county, state) groups        -> top 25 + `other`
  tape_interconnection_events POPULATED: 8 rows, all event_type='status_change'

The v0.1 rollup facts (read 2026-08-13, same 2026-08-07 generation), pinned by
tests/fixtures/caiso_queue_grains_2026_08_13.json:

  101 county keys (100 named + `unlocated` for 5 NULL rows); 58 have NO ACTIVE
      row and appear in the active view at zero, not missing
  8 state keys (7 named + `unlocated` for 4 NULL rows)
  23 study_process keys (22 named + `unspecified` for 1 NULL row)
  county -> state is NOT a function: LINCOLN spans ID/NM/NV, MARICOPA AZ/CA,
      SAN BENITO CA/NV, and `unlocated` splits 1 CA / 4 stateless
  caiso_interconnection_queue_snapshots (migration 178, applied): ONE
      generation, 2026-08-07, 2,278 rows / 492,196.796 MW -> depth 1

Most of the suite exercises the pure layer in interconnection.py directly; route
tests use the _FakePool idiom from test_weather_delta_board.py.
"""

import datetime
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

import main
import interconnection as ic


D = datetime.date
DT = datetime.datetime

FIXTURE_PATH = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "caiso_generation_types_2026_08_07.json"
)


GRAINS_PATH = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "caiso_queue_grains_2026_08_13.json"
)


def _load_gen_types() -> dict:
    with FIXTURE_PATH.open() as fh:
        return json.load(fh)["types"]


def _load_grains() -> dict:
    with GRAINS_PATH.open() as fh:
        return json.load(fh)


GEN_TYPES = _load_gen_types()
GRAINS = _load_grains()


# ---------------------------------------------------------------------------
# Row fixtures shaped exactly like the table
# ---------------------------------------------------------------------------

def _row(**over):
    r = {
        "queue_position": "1806",
        "project_name": "CAPTIVA ENERGY STORAGE",
        "generation_type": "Storage",
        "capacity_mw": 250.0,
        "status": "ACTIVE",
        "study_process": "Cluster 14",
        "interconnection_location": "SOME SUB 230KV",
        "county": "KERN",
        "state": "CA",
        "transmission_owner": "SCE",
        "deliverability": "Full Capacity",
        "queue_date": D(2021, 4, 1),
        "proposed_completion_date": D(2027, 6, 1),
        "actual_completion_date": None,
        "withdrawn_date": None,
        "withdrawal_comment": None,
        "first_seen_at": DT(2026, 6, 11, 1, 3, 57, tzinfo=datetime.timezone.utc),
        "last_updated": DT(2026, 8, 7, 21, 21, 9, tzinfo=datetime.timezone.utc),
        "snapshot_date": D(2026, 8, 7),
        "present_in_snapshot": True,
    }
    r.update(over)
    return r


# ═══════════════════════════════════════════════════════════════════════════
# The fuel rollup — the parse that is never allowed to be silent
# ═══════════════════════════════════════════════════════════════════════════

def test_fixture_matches_the_modules_known_vocabulary():
    """The checked-in 41 values ARE interconnection.KNOWN_GENERATION_TYPES.

    This is the drift sensor's own sensor: if someone edits one list and not the
    other, `known: false` starts firing on strings we do in fact know about.
    """
    assert set(GEN_TYPES) == set(ic.KNOWN_GENERATION_TYPES)
    assert len(GEN_TYPES) == 41


def test_fixture_totals_reproduce_the_live_bank():
    """2,278 projects, 492,196.8 MW.

    The count is exact. The MW total is approximate BY CONSTRUCTION and the
    tolerance is the honest statement of why: the fixture banks each type's MW
    rounded to 0.1, so 41 roundings accumulate ~0.3 MW of drift against the
    unrounded live sum. Asserting equality here would mean editing one of the 41
    values to absorb the drift, which would make the fixture stop being a
    transcript of the bank.
    """
    assert sum(v["n"] for v in GEN_TYPES.values()) == 2278
    assert sum(v["mw"] for v in GEN_TYPES.values()) == pytest.approx(492196.8, abs=0.5)


@pytest.mark.parametrize("raw", sorted(GEN_TYPES))
def test_every_live_generation_type_maps_to_a_real_fuel(raw):
    """ALL 41 values, one test case each. No value is unclassifiable, and no
    value lands outside the published display vocabulary."""
    fuel, unmapped = ic.classify_fuel(raw)
    assert fuel in ic.FUEL_SET, f"{raw!r} -> {fuel!r}, not a display fuel"
    assert isinstance(unmapped, tuple)


def test_the_mapping_table_as_shipped():
    """THE MAPPING, PINNED VALUE BY VALUE. This is the table in the handback.

    Every one of the 41 live strings is here with the fuel it rolls up to. A
    change to `_COMPONENT_KEYWORDS` that moves any string moves this test, which
    is the point: the rollup is an editorial judgment and it does not get to
    drift quietly.
    """
    expected = {
        # ── single technology ──────────────────────────────────────────────
        "Photovoltaic": "Solar",
        "Solar Thermal": "Solar",
        "Storage": "Battery Storage",
        "Wind Turbine": "Wind",
        "Hydro": "Hydro",
        "Combined Cycle": "Natural Gas",
        "Gas Turbine": "Natural Gas",
        "Combustion Turbine": "Natural Gas",
        "Reciprocating Engine": "Natural Gas",
        "Cogeneration": "Natural Gas",
        # R1: prime mover known, fuel not. Not silently called gas.
        "Steam Turbine": "Other",
        "Other": "Other",
        # R2: duplicate components collapse; these are not hybrids.
        "Storage + Storage": "Battery Storage",
        "Combined Cycle + Combined Cycle": "Natural Gas",
        "Steam Turbine + Steam Turbine": "Other",
        # ── solar + storage (contains BOTH, per the lane's literal rule, even
        #    where a third technology is also present) ──────────────────────
        "Photovoltaic + Storage": "Solar+Storage Hybrid",
        "Storage + Photovoltaic": "Solar+Storage Hybrid",
        "Storage + Solar Thermal": "Solar+Storage Hybrid",
        "Storage + Photovoltaic + Wind Turbine": "Solar+Storage Hybrid",
        "Storage + Wind Turbine + Photovoltaic": "Solar+Storage Hybrid",
        "Wind Turbine + Photovoltaic + Storage": "Solar+Storage Hybrid",
        "Wind Turbine + Storage + Photovoltaic": "Solar+Storage Hybrid",
        "Photovoltaic + Wind Turbine + Storage": "Solar+Storage Hybrid",
        "Photovoltaic + Storage + Combustion Turbine": "Solar+Storage Hybrid",
        # ── every other genuine multi-technology string ────────────────────
        "Wind Turbine + Storage": "Other Hybrid",
        "Storage + Wind Turbine": "Other Hybrid",
        "Wind Turbine + Storage + Storage": "Other Hybrid",
        "Photovoltaic + Wind Turbine": "Other Hybrid",
        "Wind Turbine + Photovoltaic": "Other Hybrid",
        "Combustion Turbine + Storage": "Other Hybrid",
        "Storage + Combustion Turbine": "Other Hybrid",
        "Photovoltaic + Combustion Turbine": "Other Hybrid",
        "Combustion Turbine + Photovoltaic": "Other Hybrid",
        "Gas Turbine + Storage": "Other Hybrid",
        "Storage + Gas Turbine": "Other Hybrid",
        "Combined Cycle + Storage": "Other Hybrid",
        "Steam Turbine + Storage": "Other Hybrid",
        "Photovoltaic + Steam Turbine": "Other Hybrid",
        "Storage + Other": "Other Hybrid",
        "Combustion Turbine + Storage + Steam Turbine": "Other Hybrid",
        "Storage + Steam Turbine + Combustion Turbine": "Other Hybrid",
    }
    assert set(expected) == set(GEN_TYPES), "the pinned table is not the live 41"
    for raw, want in expected.items():
        assert ic.classify_fuel(raw)[0] == want, raw


def test_geothermal_is_in_the_vocabulary_and_matches_nothing_live():
    """The bucket exists; the bank has no rows for it. Both facts are kept."""
    assert ic.FUEL_GEOTHERMAL in ic.FUEL_ORDER
    assert all(ic.classify_fuel(raw)[0] != ic.FUEL_GEOTHERMAL for raw in GEN_TYPES)
    assert ic.classify_fuel("Geothermal")[0] == ic.FUEL_GEOTHERMAL


def test_unmapped_components_are_named_not_swallowed():
    assert ic.classify_fuel("Steam Turbine")[1] == ("Steam Turbine",)
    # Lands in Other Hybrid, but the unrecognised half is still reported.
    fuel, unmapped = ic.classify_fuel("Steam Turbine + Storage")
    assert fuel == "Other Hybrid"
    assert unmapped == ("Steam Turbine",)


def test_an_unseen_value_maps_to_other_and_is_flagged_unknown():
    """The runtime drift alarm: a 42nd string nobody has seen."""
    raw = "Fusion Reactor"
    assert raw not in ic.KNOWN_GENERATION_TYPES
    fuel, unmapped = ic.classify_fuel(raw)
    assert fuel == "Other"
    assert unmapped == (raw,)

    rows = [_row(generation_type=raw, capacity_mw=42.0)]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    (entry,) = payload["unmapped_types"]
    assert entry["generation_type"] == raw
    assert entry["known"] is False
    assert entry["count"] == 1 and entry["mw"] == 42.0
    # and its raw label survives into the Other bucket
    other = payload["by_fuel"]["all_statuses"]["Other"]
    assert other["raw_types"] == [{"generation_type": raw, "count": 1, "mw": 42.0}]


def test_a_known_unmapped_value_is_flagged_known():
    rows = [_row(generation_type="Steam Turbine", capacity_mw=100.0)]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    (entry,) = payload["unmapped_types"]
    assert entry["known"] is True
    assert entry["fuel"] == "Other"


def test_null_and_blank_generation_types_are_other_not_crashes():
    assert ic.classify_fuel(None) == ("Other", ())
    assert ic.classify_fuel("   ") == ("Other", ())
    assert ic.classify_fuel(" + ") == ("Other", ())


def test_the_separator_split_is_what_keeps_turbines_apart():
    """A bare substring scan would collide 'Wind Turbine' with 'Combustion
    Turbine'. This is the regression guard for that."""
    assert ic.classify_fuel("Wind Turbine")[0] == "Wind"
    assert ic.classify_fuel("Combustion Turbine")[0] == "Natural Gas"
    assert ic.classify_fuel("Gas Turbine")[0] == "Natural Gas"


def test_matching_is_case_insensitive():
    for variant in ("PHOTOVOLTAIC", "photovoltaic", "PhotoVoltaic"):
        assert ic.classify_fuel(variant)[0] == "Solar"
    assert ic.classify_fuel("storage + PHOTOVOLTAIC")[0] == "Solar+Storage Hybrid"


def test_nothing_is_dropped_every_row_lands_in_exactly_one_bucket():
    """sum(by_fuel.count) == project_count, over all 41 live strings at their
    live multiplicities."""
    rows = []
    for raw, v in GEN_TYPES.items():
        for _ in range(v["n"]):
            rows.append(_row(generation_type=raw, capacity_mw=v["mw"] / v["n"]))
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")

    block = payload["by_fuel"]["all_statuses"]
    assert sum(b["count"] for b in block.values()) == 2278
    assert payload["snapshot"]["project_count"] == 2278
    assert round(sum(b["mw"] for b in block.values()), 0) == round(492196.8, 0)
    # every display bucket is emitted, including the empty ones
    assert list(block) == list(ic.FUEL_ORDER)


# ═══════════════════════════════════════════════════════════════════════════
# Attrition — hand-checked against the live 2021 vintage
# ═══════════════════════════════════════════════════════════════════════════

def _vintage_2021_rows():
    """361 projects entering in 2021, of which 253 later withdrew.

    Live: enqueued 105,537.135 MW, withdrawn 70,222.325 MW. Reproduced here by
    splitting each total evenly across its own row count, so the sums land on
    the live figures rather than on numbers invented for the test.
    """
    wd_each = 70222.325 / 253
    surv_each = (105537.135 - 70222.325) / (361 - 253)
    rows = []
    for i in range(253):
        rows.append(_row(
            queue_position=f"W{i}", queue_date=D(2021, 6, 1),
            capacity_mw=wd_each, status="WITHDRAWN",
            withdrawn_date=D(2024, 3, 1),
        ))
    for i in range(361 - 253):
        rows.append(_row(
            queue_position=f"S{i}", queue_date=D(2021, 6, 1),
            capacity_mw=surv_each, status="ACTIVE", withdrawn_date=None,
        ))
    return rows


def test_attrition_2021_vintage_hand_checked():
    payload = ic.summary_payload(
        _vintage_2021_rows(), snapshot_date=D(2026, 8, 7), data_note="n")
    (y,) = payload["attrition"]["years"]

    assert y["year"] == 2021
    assert y["enqueued_count"] == 361
    assert y["enqueued_mw"] == pytest.approx(105537.135, abs=0.05)
    assert y["withdrawn_count"] == 253
    assert y["withdrawn_mw"] == pytest.approx(70222.325, abs=0.05)

    # the complement, hand-computed: 361 - 253 = 108 projects,
    # 105537.135 - 70222.325 = 35314.810 MW
    assert y["surviving_count"] == 108
    assert y["surviving_mw"] == pytest.approx(35314.81, abs=0.05)

    # 70222.325 / 105537.135 = 0.66538...
    assert y["withdrawn_mw_share"] == pytest.approx(0.6654, abs=0.0002)


def test_a_vintage_with_no_withdrawals_reports_zero_not_null():
    """1999: one project, 550 MW, none withdrawn. Zero withdrawn out of a known
    enqueued set is a measurement, not an absence."""
    rows = [_row(queue_date=D(1999, 11, 1), capacity_mw=550.0,
                 status="COMPLETED", withdrawn_date=None)]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    (y,) = payload["attrition"]["years"]
    assert y["year"] == 1999
    assert y["enqueued_mw"] == 550.0
    assert y["withdrawn_count"] == 0
    assert y["withdrawn_mw"] == 0.0
    assert y["withdrawn_mw_share"] == 0.0
    assert y["surviving_mw"] == 550.0


def test_the_share_is_null_only_when_the_denominator_is_zero():
    rows = [_row(queue_date=D(2010, 1, 1), capacity_mw=0.0, withdrawn_date=None)]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    (y,) = payload["attrition"]["years"]
    assert y["enqueued_mw"] == 0.0
    assert y["withdrawn_mw_share"] is None


def test_attrition_uses_the_date_and_reports_the_39_row_disagreement():
    """withdrawn = withdrawn_date IS NOT NULL, per the lane's ruling.

    A status='WITHDRAWN' row with no date is NOT counted as withdrawn in the
    series — it cannot be placed on a per-vintage timeline — and its total is
    reported beside the series rather than absorbed into `surviving`.
    """
    rows = [
        _row(queue_position="A", queue_date=D(2011, 1, 1), capacity_mw=100.0,
             status="WITHDRAWN", withdrawn_date=D(2015, 1, 1)),
        _row(queue_position="B", queue_date=D(2011, 1, 1), capacity_mw=250.0,
             status="WITHDRAWN", withdrawn_date=None),   # the ghost
    ]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    att = payload["attrition"]
    (y,) = att["years"]

    assert y["withdrawn_count"] == 1 and y["withdrawn_mw"] == 100.0
    assert y["surviving_count"] == 1 and y["surviving_mw"] == 250.0
    assert att["withdrawn_status_without_date"]["count"] == 1
    assert att["withdrawn_status_without_date"]["mw"] == 250.0
    assert "withdrawn_date IS NOT NULL" in att["definition"]


# ═══════════════════════════════════════════════════════════════════════════
# Absence — undated CODs, county remainder, nulls
# ═══════════════════════════════════════════════════════════════════════════

def test_null_proposed_cod_is_undated_never_dropped_never_zero_dated():
    rows = [
        _row(queue_position="A", proposed_completion_date=D(2027, 6, 1), capacity_mw=10.0),
        _row(queue_position="B", proposed_completion_date=None, capacity_mw=20.0),
        _row(queue_position="C", proposed_completion_date=None, capacity_mw=5.0),
    ]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    block = payload["by_proposed_cod_year"]

    assert block["years"] == [{"year": 2027, "count": 1, "mw": 10.0}]
    assert block["undated"] == {"count": 2, "mw": 25.0}
    # nothing invented a year for the nulls
    assert all(y["year"] is not None for y in block["years"])
    # and nothing was lost
    total = sum(y["count"] for y in block["years"]) + block["undated"]["count"]
    assert total == payload["snapshot"]["project_count"] == 3


def test_by_county_is_a_top_n_with_a_named_remainder_never_a_truncation():
    rows = []
    for i in range(30):
        rows.append(_row(queue_position=f"P{i}", county=f"COUNTY{i:02d}",
                         state="CA", capacity_mw=float(100 - i)))
    payload = ic.summary_payload(
        rows, snapshot_date=D(2026, 8, 7), data_note="n", county_top_n=25)
    block = payload["by_county"]

    assert len(block["top"]) == 25
    assert block["top_n"] == 25
    assert block["group_count"] == 30
    assert block["other"]["group_count"] == 5
    assert block["other"]["count"] == 5
    # top + other accounts for every project and every MW
    assert sum(c["count"] for c in block["top"]) + block["other"]["count"] == 30
    assert round(sum(c["mw"] for c in block["top"]) + block["other"]["mw"], 1) == \
        payload["snapshot"]["total_mw"]
    # ranked by MW descending
    assert block["top"][0]["mw"] == 100.0


def test_a_null_county_or_owner_is_a_bucket_not_a_dropped_row():
    rows = [
        _row(queue_position="A", county=None, state=None, transmission_owner=None,
             capacity_mw=10.0),
        _row(queue_position="B", capacity_mw=20.0),
    ]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    assert sum(c["count"] for c in payload["by_county"]["top"]) == 2
    assert any(c["county"] is None for c in payload["by_county"]["top"])
    assert any(t["to"] is None for t in payload["by_transmission_owner"])


def test_a_row_with_no_queue_date_is_reported_not_silently_missing():
    rows = [
        _row(queue_position="A", queue_date=D(2020, 1, 1), capacity_mw=10.0),
        _row(queue_position="B", queue_date=None, capacity_mw=7.0),
    ]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    assert [y["year"] for y in payload["by_queue_year"]] == [2020]
    assert payload["attrition"]["no_queue_date"] == {"count": 1, "mw": 7.0}


def test_a_null_capacity_stays_null_on_a_row_and_is_zero_in_a_sum():
    rows = [_row(queue_position="A", capacity_mw=None)]
    payload = ic.summary_payload(rows, snapshot_date=D(2026, 8, 7), data_note="n")
    assert payload["snapshot"]["total_mw"] == 0.0
    assert payload["snapshot"]["project_count"] == 1
    assert ic.project_row(rows[0])["capacity_mw"] is None


# ═══════════════════════════════════════════════════════════════════════════
# Projects — shaping, pagination bounds
# ═══════════════════════════════════════════════════════════════════════════

def test_a_project_row_carries_both_raw_and_rollup():
    out = ic.project_row(_row(generation_type="Storage + Photovoltaic"))
    assert out["generation_type"] == "Storage + Photovoltaic"
    assert out["fuel"] == "Solar+Storage Hybrid"
    assert out["first_seen_at"] == "2026-06-11T01:03:57+00:00"
    assert out["withdrawn"] is False and out["withdrawn_date"] is None


def test_a_withdrawn_row_carries_its_date_and_comment():
    out = ic.project_row(_row(status="WITHDRAWN", withdrawn_date=D(2024, 3, 1),
                              withdrawal_comment="Customer request"))
    assert out["withdrawn"] is True
    assert out["withdrawn_date"] == "2024-03-01"
    assert out["withdrawal_comment"] == "Customer request"


@pytest.mark.parametrize("page,size,want_len,want_pages,want_more", [
    (1, 10, 10, 3, True),
    (2, 10, 10, 3, True),
    (3, 10, 5, 3, False),     # the ragged last page
    (4, 10, 0, 3, False),     # past the end -> empty, not a 404 and not a clamp
    (1, 100, 25, 1, False),
    (1, 1, 1, 25, True),
])
def test_pagination_bounds(page, size, want_len, want_pages, want_more):
    rows = [_row(queue_position=str(i)) for i in range(25)]
    window, meta = ic.paginate(rows, page=page, page_size=size)
    assert len(window) == want_len
    assert meta["total"] == 25
    assert meta["page_count"] == want_pages
    assert meta["has_more"] is want_more
    assert meta["page"] == page and meta["page_size"] == size


def test_pagination_over_an_empty_result_set():
    window, meta = ic.paginate([], page=1, page_size=50)
    assert window == []
    assert meta == {"page": 1, "page_size": 50, "total": 0,
                    "page_count": 0, "has_more": False}


def test_the_pages_partition_the_set_exactly():
    rows = [_row(queue_position=str(i)) for i in range(25)]
    seen = []
    for p in range(1, 4):
        window, _ = ic.paginate(rows, page=p, page_size=10)
        seen.extend(r["queue_position"] for r in window)
    assert seen == [str(i) for i in range(25)]


def test_unmapped_types_describes_the_result_set_not_the_page():
    """A reader paging through Steam Turbine rows should not see the alarm
    blink in and out depending on which page they are on."""
    rows = [_row(queue_position=str(i), generation_type="Steam Turbine",
                 capacity_mw=10.0) for i in range(25)]
    payload = ic.projects_payload(
        rows, page=3, page_size=10, sort="queue_date", order="desc",
        filters={}, snapshot_date=D(2026, 8, 7), data_note="n")
    assert len(payload["rows"]) == 5
    assert payload["page"]["total"] == 25
    (entry,) = payload["unmapped_types"]
    assert entry["count"] == 25          # the whole set, not the 5 on this page
    assert entry["mw"] == 250.0


def test_filter_by_fuel_selects_on_the_rollup():
    rows = [
        _row(queue_position="A", generation_type="Photovoltaic"),
        _row(queue_position="B", generation_type="Storage + Photovoltaic"),
        _row(queue_position="C", generation_type="Solar Thermal"),
    ]
    got = ic.filter_by_fuel(rows, "Solar")
    assert [r["queue_position"] for r in got] == ["A", "C"]
    assert ic.filter_by_fuel(rows, None) is rows


# ═══════════════════════════════════════════════════════════════════════════
# Events — the tape join, and the honest empty
# ═══════════════════════════════════════════════════════════════════════════

def _event(**over):
    e = {
        "id": 8,
        "event_type": "status_change",
        "queue_position": "1806",
        "project_name": "CAPTIVA ENERGY STORAGE",
        "generation_type": "Storage",
        "mw": 250.0,
        "old_values": {"status": "ACTIVE"},
        "new_values": {"status": "WITHDRAWN"},
        "detected_at": DT(2026, 7, 31, 21, 43, 55, tzinfo=datetime.timezone.utc),
        "snapshot_date": D(2026, 7, 31),
    }
    e.update(over)
    return e


def test_an_event_names_what_changed():
    out = ic.event_row(_event())
    assert out["changed_fields"] == ["status"]
    assert out["old_values"] == {"status": "ACTIVE"}
    assert out["new_values"] == {"status": "WITHDRAWN"}
    assert out["fuel"] == "Battery Storage"
    assert out["detected_at"] == "2026-07-31T21:43:55+00:00"


def test_events_payload_over_the_live_shape():
    payload = ic.events_payload([_event(), _event(id=7, new_values={"status": "COMPLETED"})],
                                limit=50)
    assert payload["count"] == 2
    assert payload["event_types"] == ["status_change"]
    assert payload["note"] is None


def test_an_empty_ledger_is_an_honest_empty_not_a_404():
    payload = ic.events_payload([], limit=50)
    assert payload["count"] == 0
    assert payload["rows"] == []
    assert "empty" in payload["note"]
    assert "reconstructed" in payload["note"]


# ═══════════════════════════════════════════════════════════════════════════
# Routes — the injection posture, the caps, the chip
# ═══════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        self._pool.executed.append((sql, params))
        self._rows = self._pool.answer(sql, params)

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, pool):
        self._pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def cursor(self, row_factory=None):
        return _FakeCursor(self._pool)


class _FakePool:
    """The house route-test idiom (see test_weather_delta_board.py): answer by
    inspecting the SQL, and RECORD every statement so a test can assert on the
    text that was actually sent."""

    def __init__(self, queue_rows=None, event_rows=None, generation_rows=None):
        self.queue_rows = queue_rows if queue_rows is not None else []
        self.event_rows = event_rows if event_rows is not None else []
        # The GROUP BY (snapshot_date, status) aggregate over the snapshots
        # table — already reduced, exactly as the database returns it. `None`
        # means "not supplied": the default below mirrors the live depth-1 bank.
        self.generation_rows = generation_rows
        self.executed = []

    def answer(self, sql, params):
        if "caiso_interconnection_queue_snapshots" in sql:
            if self.generation_rows is not None:
                return [dict(r) for r in self.generation_rows]
            return [
                {"snapshot_date": D(2026, 8, 7), "status": "ACTIVE",
                 "row_count": 268, "mw": 75977.333},
                {"snapshot_date": D(2026, 8, 7), "status": "COMPLETED",
                 "row_count": 250, "mw": 34912.787},
                {"snapshot_date": D(2026, 8, 7), "status": "WITHDRAWN",
                 "row_count": 1760, "mw": 381306.675},
            ]
        if "max(snapshot_date)" in sql:
            return [{
                "snapshot_date": D(2026, 8, 7),
                "snapshot_count": 1,
                "first_seen_min": DT(2026, 6, 11, 1, 3, 57,
                                     tzinfo=datetime.timezone.utc),
                "first_seen_max": DT(2026, 6, 11, 1, 3, 57,
                                     tzinfo=datetime.timezone.utc),
                "last_updated": DT(2026, 8, 7, 21, 21, 9,
                                   tzinfo=datetime.timezone.utc),
            }]
        if "tape_interconnection_events" in sql:
            return list(self.event_rows)
        return [dict(r) for r in self.queue_rows]

    def connection(self):
        return _FakeConn(self)


@pytest.fixture
def client(monkeypatch):
    pool = _FakePool(queue_rows=[
        _row(queue_position="A", generation_type="Photovoltaic",
             capacity_mw=100.0, status="ACTIVE"),
        _row(queue_position="B", generation_type="Steam Turbine",
             capacity_mw=200.0, status="WITHDRAWN", withdrawn_date=D(2024, 1, 1)),
        _row(queue_position="C", generation_type="Storage + Photovoltaic",
             capacity_mw=300.0, status="COMPLETED"),
    ], event_rows=[_event()])
    monkeypatch.setattr(main, "_pool", pool)
    for cache in main._IC_CACHES:
        cache.clear()
    # Plain TestClient, NOT the context-manager form: the house idiom (see
    # test_weather_delta_board.py) skips the lifespan so the suite never needs a
    # real NEON_DATABASE_URL. `main._pool` is monkeypatched above instead.
    c = TestClient(main.app)
    c.pool = pool
    yield c
    for cache in main._IC_CACHES:
        cache.clear()


# ── The injection posture ───────────────────────────────────────────────────

@pytest.mark.parametrize("hostile", [
    "capacity_mw; DROP TABLE caiso_interconnection_queue",
    "capacity_mw --",
    "1; SELECT pg_sleep(10)",
    "(SELECT 1)",
    "capacity_mw DESC, (SELECT version())",
    "raw",                     # a real column, but not on the whitelist
    "queue_position",          # ditto
    "CAPACITY_MW",             # the whitelist is exact, not case-folded
    "'",
])
def test_a_hostile_sort_400s_and_never_reaches_the_database(client, hostile):
    """THE INJECTION TEST. A sort value outside the whitelist is a 400 naming
    the whitelist — not a silent fallback to the default, and not a statement."""
    before = len(client.pool.executed)
    r = client.get("/api/interconnection/projects", params={"sort": hostile})
    assert r.status_code == 400
    assert "unknown sort" in r.json()["detail"]
    # and no SQL was sent on this request at all
    assert len(client.pool.executed) == before


def test_the_hostile_string_never_appears_in_any_statement(client):
    client.get("/api/interconnection/projects",
               params={"sort": "capacity_mw; DROP TABLE caiso_interconnection_queue"})
    assert not any("DROP TABLE" in sql for sql, _ in client.pool.executed)


@pytest.mark.parametrize("sort", sorted(ic.SORT_WHITELIST))
def test_every_whitelisted_sort_is_accepted(client, sort):
    r = client.get("/api/interconnection/projects", params={"sort": sort})
    assert r.status_code == 200
    assert r.json()["sort"] == sort


def test_a_hostile_order_400s_too(client):
    r = client.get("/api/interconnection/projects",
                   params={"sort": "capacity_mw", "order": "asc; DROP TABLE x"})
    assert r.status_code == 400
    assert "unknown order" in r.json()["detail"]


def test_order_by_is_assembled_from_constants_only(client):
    client.get("/api/interconnection/projects",
               params={"sort": "capacity_mw", "order": "asc"})
    project_sql = [s for s, _ in client.pool.executed
                   if "FROM caiso_interconnection_queue" in s and "ORDER BY" in s]
    assert project_sql
    assert "ORDER BY capacity_mw ASC NULLS LAST" in project_sql[0]


def test_every_filter_is_a_bound_parameter_not_string_built(client):
    """A hostile value in a FILTER rides as a parameter and is never spliced
    into the statement text."""
    hostile = "KERN'; DROP TABLE caiso_interconnection_queue; --"
    r = client.get("/api/interconnection/projects", params={"county": hostile})
    assert r.status_code == 200
    sent = [(s, p) for s, p in client.pool.executed if "ORDER BY" in s]
    assert sent
    sql, params = sent[0]
    assert hostile not in sql
    assert params["county"] == hostile


def test_like_metacharacters_in_q_are_escaped(client):
    client.get("/api/interconnection/projects", params={"q": "50%_x"})
    sent = [p for s, p in client.pool.executed if "ORDER BY" in s]
    assert sent[0]["q"] == r"%50\%\_x%"


# ── Caps and validation ─────────────────────────────────────────────────────

def test_page_size_is_capped_at_100(client):
    assert client.get("/api/interconnection/projects",
                      params={"page_size": 100}).status_code == 200
    assert client.get("/api/interconnection/projects",
                      params={"page_size": 101}).status_code == 422
    assert client.get("/api/interconnection/projects",
                      params={"page_size": 0}).status_code == 422
    assert client.get("/api/interconnection/projects",
                      params={"page": 0}).status_code == 422


def test_an_unknown_fuel_400s_naming_the_vocabulary(client):
    r = client.get("/api/interconnection/projects", params={"fuel": "Coal"})
    assert r.status_code == 400
    assert "Solar+Storage Hybrid" in r.json()["detail"]


def test_a_known_fuel_filters_on_the_rollup(client):
    r = client.get("/api/interconnection/projects", params={"fuel": "solar"})
    assert r.status_code == 200
    body = r.json()
    assert body["filters"]["fuel"] == "Solar"        # normalised to exact case
    assert [row["queue_position"] for row in body["rows"]] == ["A"]
    assert body["page"]["total"] == 1


def test_an_unknown_status_400s(client):
    r = client.get("/api/interconnection/projects", params={"status": "PENDING"})
    assert r.status_code == 400
    assert "unknown status" in r.json()["detail"]
    assert client.get("/api/interconnection/projects",
                      params={"status": "active"}).status_code == 200


def test_an_inverted_mw_window_400s(client):
    r = client.get("/api/interconnection/projects",
                   params={"min_mw": 500, "max_mw": 100})
    assert r.status_code == 400
    assert "greater than" in r.json()["detail"]


def test_events_limit_is_capped(client):
    assert client.get("/api/interconnection/events",
                      params={"limit": 500}).status_code == 200
    assert client.get("/api/interconnection/events",
                      params={"limit": 501}).status_code == 422


# ── The snapshot chip and the measured data_note ────────────────────────────

def test_every_response_carries_the_snapshot_chip_and_data_note(client):
    for path in ("/api/interconnection/summary", "/api/interconnection/projects"):
        body = client.get(path).json()
        assert body["snapshot_date"] == "2026-08-07"
        assert "Friday" in body["data_note"]
        assert "cache" in body


def test_the_data_note_states_the_first_seen_at_limitation_as_measured(client):
    body = client.get("/api/interconnection/summary").json()
    note = body["data_note"]
    assert "upsert-in-place" in note
    assert "carries no entrant signal yet" in note
    assert "No coordinates exist" in note


def test_the_data_note_drops_the_caveat_when_lineage_appears(client, monkeypatch):
    """The note is DERIVED, not a constant: bank a second generation and the
    'no entrant signal' clause disappears on its own."""
    note = main._ic_data_note({
        "snapshot_count": 4,
        "first_seen_at_is_uniform": False,
        "first_seen_at_min": DT(2026, 6, 11, tzinfo=datetime.timezone.utc),
        "first_seen_at_max": DT(2026, 8, 7, tzinfo=datetime.timezone.utc),
    })
    assert "carries no entrant signal yet" not in note
    assert "now distinguishes entrants" in note


def test_summary_serves_the_full_contract(client):
    body = client.get("/api/interconnection/summary").json()
    for key in ("snapshot", "by_fuel", "by_queue_year", "by_proposed_cod_year",
                "by_county", "by_transmission_owner", "attrition",
                "unmapped_types"):
        assert key in body, key
    assert body["snapshot"]["project_count"] == 3
    assert body["snapshot"]["total_mw"] == 600.0
    assert {s["status"] for s in body["snapshot"]["statuses"]} == \
        {"ACTIVE", "COMPLETED", "WITHDRAWN"}
    assert body["by_fuel"]["active_statuses"] == ["ACTIVE"]
    # active view sees only the ACTIVE row; all_statuses sees all three
    assert body["by_fuel"]["active"]["Solar"]["count"] == 1
    assert body["by_fuel"]["all_statuses"]["Solar+Storage Hybrid"]["count"] == 1
    # Steam Turbine is visible as unmapped, with its raw label
    assert body["unmapped_types"][0]["generation_type"] == "Steam Turbine"
    assert body["by_fuel"]["all_statuses"]["Other"]["raw_types"][0][
        "generation_type"] == "Steam Turbine"


def test_the_summary_read_never_pulls_the_fat_columns(client):
    """`raw` (jsonb) and withdrawal_comment are what make a full-table read
    expensive, and the summary needs neither."""
    client.get("/api/interconnection/summary")
    summary_sql = [s for s, _ in client.pool.executed
                   if "FROM caiso_interconnection_queue" in s
                   and "max(snapshot_date)" not in s]
    assert summary_sql
    # Tokenised, not substring-matched: "withdrawn_date" contains "raw".
    select_list = summary_sql[0].split("FROM")[0]
    selected = {t.strip(" ,\n") for t in select_list.replace("SELECT", "").split()}
    assert "raw" not in selected
    assert "withdrawal_comment" not in selected
    assert "SELECT *" not in summary_sql[0]


def test_events_serves_the_populated_tape(client):
    body = client.get("/api/interconnection/events").json()
    assert body["count"] == 1
    assert body["rows"][0]["event_type"] == "status_change"
    assert body["rows"][0]["changed_fields"] == ["status"]
    assert body["note"] is None


def test_events_is_an_honest_empty_when_the_ledger_is(monkeypatch):
    pool = _FakePool(queue_rows=[], event_rows=[])
    monkeypatch.setattr(main, "_pool", pool)
    for cache in main._IC_CACHES:
        cache.clear()
    r = TestClient(main.app).get("/api/interconnection/events")
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert "empty" in r.json()["note"]
    for cache in main._IC_CACHES:
        cache.clear()


_ALL_IC_ROUTES = (
    "/api/interconnection/summary",
    "/api/interconnection/projects",
    "/api/interconnection/events",
    "/api/interconnection/rollups",
)


def test_the_lane_issues_no_writes_and_no_ddl(client):
    """READ-ONLY, proved over every statement all FOUR routes send."""
    for path in _ALL_IC_ROUTES:
        client.get(path)
    assert client.pool.executed
    for sql, _ in client.pool.executed:
        head = sql.strip().split()[0].upper()
        assert head in ("SELECT", "WITH"), sql
        upper = sql.upper()
        for forbidden in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER",
                          "CREATE", "TRUNCATE", "GRANT"):
            assert forbidden not in upper, (forbidden, sql)


def test_only_the_three_banked_tables_are_touched(client):
    """v0 touched two tables; v0.1 adds the snapshots table and NOTHING else."""
    for path in _ALL_IC_ROUTES:
        client.get(path)
    for sql, _ in client.pool.executed:
        tables = {w for w in sql.replace("\n", " ").split() if w.startswith(("caiso_", "tape_"))}
        assert tables <= {"caiso_interconnection_queue",
                          "caiso_interconnection_queue_snapshots",
                          "tape_interconnection_events"}


# ═══════════════════════════════════════════════════════════════════════════
# v0.1 — the full-cardinality rollups
# ═══════════════════════════════════════════════════════════════════════════
#
# The shaping tests below run on a hand-built bank that reproduces the live
# bank's awkward corners rather than its easy case: a county under two states, a
# county with no ACTIVE row, a NULL county WITH a state, a NULL county WITHOUT
# one, a NULL study_process, a BLANK study_process, and two county strings the
# bank really holds and we really do not repair ("USA", "KINGS/KERN").

def _rollup_rows():
    return [
        # KERN/CA — the ordinary case: some active, some not.
        _row(county="KERN", state="CA", study_process="C14",
             status="ACTIVE", capacity_mw=100.0),
        _row(county="KERN", state="CA", study_process="C14",
             status="WITHDRAWN", capacity_mw=200.0),
        # MARICOPA under TWO states — the live ambiguity, reproduced.
        _row(county="MARICOPA", state="AZ", study_process="C13",
             status="ACTIVE", capacity_mw=50.0),
        _row(county="MARICOPA", state="CA", study_process="C13",
             status="COMPLETED", capacity_mw=25.0),
        # A county with ZERO active rows — must be DIMMED, not missing.
        _row(county="MONO", state="CA", study_process="SGIP",
             status="WITHDRAWN", capacity_mw=19.993),
        # NULL county AND NULL state -> `unlocated` at both grains.
        _row(county=None, state=None, study_process="FT",
             status="WITHDRAWN", capacity_mw=10.0),
        # NULL county WITH a state -> `unlocated` county, CA state.
        _row(county=None, state="CA", study_process="FT",
             status="WITHDRAWN", capacity_mw=5.0),
        # NULL study_process -> `unspecified`.
        _row(county="USA", state="CA", study_process=None,
             status="WITHDRAWN", capacity_mw=18.0),
        # BLANK study_process -> also `unspecified`; county rides out as banked.
        _row(county="KINGS/KERN", state="CA", study_process="   ",
             status="WITHDRAWN", capacity_mw=20.0),
    ]


def _payload():
    return ic.rollups_payload(
        _rollup_rows(),
        generation_rows=[
            {"snapshot_date": D(2026, 8, 7), "status": "ACTIVE",
             "row_count": 268, "mw": 75977.333},
            {"snapshot_date": D(2026, 8, 7), "status": "COMPLETED",
             "row_count": 250, "mw": 34912.787},
            {"snapshot_date": D(2026, 8, 7), "status": "WITHDRAWN",
             "row_count": 1760, "mw": 381306.675},
        ],
        generations_source="caiso_interconnection_queue_snapshots",
        snapshot_date=D(2026, 8, 7),
        data_note="note",
    )


# ── Nothing is folded, nothing is truncated ─────────────────────────────────

@pytest.mark.parametrize("block,want_keys", [
    ("by_county", {"KERN", "MARICOPA", "MONO", "USA", "KINGS/KERN", "unlocated"}),
    ("by_state", {"CA", "AZ", "unlocated"}),
    ("by_process", {"C14", "C13", "SGIP", "FT", "unspecified"}),
])
def test_every_key_at_every_grain_no_other_bucket(block, want_keys):
    """THE `other: 343` ERA ENDS HERE. Every key, at every grain, named."""
    b = _payload()[block]
    assert set(b["all_statuses"]) == want_keys
    assert set(b["keys"]) == want_keys
    assert b["key_count"] == len(want_keys)
    # and there is no folding bucket anywhere in the block
    assert "other" not in b
    assert "top" not in b and "top_n" not in b


@pytest.mark.parametrize("block", ["by_county", "by_state", "by_process"])
def test_nothing_is_dropped_the_grain_partitions_the_bank(block):
    """sum over the keys == the whole bank, at every grain. A dropped NULL row
    or a folded key would break this arithmetic, which is the point."""
    body = _payload()
    b = body[block]
    assert sum(v["count"] for v in b["all_statuses"].values()) == 9
    assert round(sum(v["mw"] for v in b["all_statuses"].values()), 1) == \
        body["snapshot"]["total_mw"] == 448.0


# ── Missing is a named key ──────────────────────────────────────────────────

def test_a_null_county_is_the_named_key_unlocated_never_dropped():
    b = _payload()["by_county"]
    assert b["missing_key"] == "unlocated"
    assert b["all_statuses"]["unlocated"] == {"count": 2, "mw": 15.0}


def test_a_null_state_is_unlocated_but_a_stateless_county_row_still_has_its_state():
    """The two NULL-county rows split: one carries state CA, one carries none.
    They are ONE `unlocated` county key and TWO different state keys — the
    grains are independent, and neither invents the other's value."""
    b = _payload()["by_state"]
    assert b["all_statuses"]["unlocated"] == {"count": 1, "mw": 10.0}
    assert b["all_statuses"]["CA"]["count"] == 7


@pytest.mark.parametrize("process", [None, "   "])
def test_a_null_or_blank_study_process_is_unspecified(process):
    rows = [_row(study_process=process, capacity_mw=7.0, status="WITHDRAWN")]
    b = ic.grain_block(rows, "study_process", grain="study_process",
                       missing_key=ic.UNSPECIFIED_KEY)
    assert b["all_statuses"] == {"unspecified": {"count": 1, "mw": 7.0}}


# ── Dimmed at zero, not missing at zero ─────────────────────────────────────

@pytest.mark.parametrize("block,zero_keys", [
    ("by_county", {"MONO", "USA", "KINGS/KERN", "unlocated"}),
    ("by_state", {"unlocated"}),
    ("by_process", {"SGIP", "FT", "unspecified"}),
])
def test_active_carries_every_key_including_the_zeros(block, zero_keys):
    """A key that VANISHES from the active view reads as 'no such county'. A key
    at zero reads as 'nothing live here'. Only the second is true."""
    b = _payload()[block]
    assert set(b["active"]) == set(b["all_statuses"])
    for k in zero_keys:
        assert b["active"][k] == {"count": 0, "mw": 0.0}, k
    assert b["keys_with_zero_active"] == len(zero_keys)


@pytest.mark.parametrize("block", ["by_county", "by_state", "by_process"])
def test_active_and_all_statuses_share_one_key_order(block):
    """Zippable by construction — a client can render the two maps side by side
    without re-sorting either of them."""
    b = _payload()[block]
    assert list(b["active"]) == list(b["all_statuses"]) == b["keys"]


def test_the_key_order_is_descending_all_statuses_mw_on_the_UNROUNDED_value():
    """KINGS/KERN (20.0) outranks MONO (19.993) even though both round to 20.0
    on the way out. Ordering is decided before rounding, ties by key string."""
    keys = _payload()["by_county"]["keys"]
    assert keys == ["KERN", "MARICOPA", "KINGS/KERN", "MONO", "USA", "unlocated"]


def test_active_statuses_is_published_so_nobody_has_to_guess():
    for block in ("by_county", "by_state", "by_process"):
        assert _payload()[block]["active_statuses"] == ["ACTIVE"]


# ── Keys are the strings AS BANKED ──────────────────────────────────────────

@pytest.mark.parametrize("banked", ["USA", "KINGS/KERN"])
def test_county_strings_ride_out_exactly_as_banked(banked):
    """Real values in this table. No title-casing, no splitting on '/', no
    lookup, no repair — this lane does not invent geography."""
    assert banked in _payload()["by_county"]["all_statuses"]


def test_the_county_grain_keys_on_county_alone_not_on_county_state():
    """MARICOPA is ONE county with two states behind it, not two counties. A
    compound (county, state) key would silently split it on a page that asked
    for counties."""
    b = _payload()["by_county"]
    assert b["all_statuses"]["MARICOPA"] == {"count": 2, "mw": 75.0}


# ── State pairing is measured, and it is not clean ──────────────────────────

def test_an_ambiguous_county_gets_no_invented_state():
    b = _payload()["by_county"]
    assert b["key_states"]["MARICOPA"] == ["AZ", "CA"]
    assert b["key_state"]["MARICOPA"] is None
    assert "MARICOPA" in b["state_pairing"]["ambiguous_keys"]
    assert b["state_pairing"]["clean"] is False
    assert "NOT a function" in b["state_pairing"]["note"]
    assert "MARICOPA" in b["state_pairing"]["note"]


def test_an_unambiguous_county_does_get_its_state():
    b = _payload()["by_county"]
    assert b["key_states"]["KERN"] == ["CA"]
    assert b["key_state"]["KERN"] == "CA"
    assert "KERN" not in b["state_pairing"]["ambiguous_keys"]


def test_the_pairing_note_flips_to_clean_when_the_bank_is():
    """MEASURED, not hard-coded: hand it a bank where every county has one
    state and the payload says so on its own."""
    rows = [_row(county="KERN", state="CA"), _row(county="INYO", state="CA")]
    b = ic.county_block(rows)
    assert b["state_pairing"]["clean"] is True
    assert b["state_pairing"]["ambiguous_keys"] == []
    assert b["key_state"] == {"KERN": "CA", "INYO": "CA"}


def test_a_stateless_row_makes_the_unlocated_key_itself_ambiguous():
    """The live `unlocated` bucket holds 1 CA row and 4 stateless ones, so even
    the missing-county key spans two state values. It is reported, not tidied."""
    b = _payload()["by_county"]
    assert b["key_states"]["unlocated"] == ["CA", "unlocated"]
    assert b["key_state"]["unlocated"] is None


# ── generations — the honest trend read ─────────────────────────────────────

def test_generations_reports_depth_1_as_a_point_not_a_trend():
    g = _payload()["generations"]
    assert g["depth"] == 1
    assert g["snapshot_dates"] == ["2026-08-07"]
    assert g["source_table"] == "caiso_interconnection_queue_snapshots"
    assert "POINT, not a trend" in g["note"]
    e = g["entries"][0]
    assert e["snapshot_date"] == "2026-08-07"
    assert e["rows"] == 2278
    assert e["mw_total"] == 492196.8
    assert e["by_status"]["ACTIVE"] == {"count": 268, "mw": 75977.3}
    assert e["by_status"]["WITHDRAWN"] == {"count": 1760, "mw": 381306.7}
    assert e["by_status"]["COMPLETED"] == {"count": 250, "mw": 34912.8}


def test_a_generation_entry_reconciles_to_its_own_statuses():
    e = _payload()["generations"]["entries"][0]
    assert sum(v["count"] for v in e["by_status"].values()) == e["rows"]
    assert round(sum(v["mw"] for v in e["by_status"].values()), 1) == e["mw_total"]


def test_generations_are_ordered_ascending_and_gaps_are_not_interpolated():
    """Fed out of order, they come back oldest-first — and the two-week hole
    between 07-24 and 08-07 stays a hole."""
    g = ic.generations_block([
        {"snapshot_date": D(2026, 8, 7), "status": "ACTIVE", "row_count": 3, "mw": 30.0},
        {"snapshot_date": D(2026, 7, 17), "status": "ACTIVE", "row_count": 1, "mw": 10.0},
        {"snapshot_date": D(2026, 7, 24), "status": "ACTIVE", "row_count": 2, "mw": 20.0},
    ], source_table="t")
    assert g["depth"] == 3
    assert g["snapshot_dates"] == ["2026-07-17", "2026-07-24", "2026-08-07"]
    assert [e["rows"] for e in g["entries"]] == [1, 2, 3]
    assert "not interpolated" in g["note"]


def test_an_empty_snapshots_table_is_an_honest_depth_zero():
    g = ic.generations_block([], source_table="t")
    assert g["depth"] == 0
    assert g["entries"] == []
    assert g["snapshot_dates"] == []
    assert "absence, not an error" in g["note"]
    assert "nothing is reconstructed" in g["note"].lower()


def test_generations_statuses_are_ordered_by_mw_within_a_generation():
    e = _payload()["generations"]["entries"][0]
    assert list(e["by_status"]) == ["WITHDRAWN", "ACTIVE", "COMPLETED"]


# ── The live bank, rebuilt from the checked-in recon receipt ────────────────

def _rows_from_grain(grain: str, column: str):
    """Rebuild a row set that reproduces the live aggregate at ONE grain.

    Per key: `active_count` ACTIVE rows carrying `active_mw` between them and
    the rest non-ACTIVE carrying the complement, with the MW concentrated on one
    row of each group. Counts and MW sums are therefore exactly the live ones —
    which is all a grain block reads.
    """
    rows = []
    for key, n, mw, an, amw in GRAINS["grains"][grain]["rows"]:
        missing = GRAINS["grains"][grain]["missing_key"]
        value = None if key == missing else key
        for i in range(an):
            rows.append(_row(**{column: value, "status": "ACTIVE",
                                "capacity_mw": amw if i == 0 else 0.0}))
        for i in range(n - an):
            rows.append(_row(**{column: value, "status": "WITHDRAWN",
                                "capacity_mw": (mw - amw) if i == 0 else 0.0}))
    return rows


@pytest.mark.parametrize("grain,column", [
    ("county", "county"),
    ("state", "state"),
    ("study_process", "study_process"),
])
def test_the_live_grain_reproduces_key_for_key(grain, column):
    """THE RECON RECEIPT, EXECUTED. Every key the 2026-08-13 read found, with
    its live count and MW, all-statuses and active-only. A folded key, a dropped
    NULL or a truncated tail moves this test."""
    want = GRAINS["grains"][grain]
    missing = want["missing_key"]
    block = ic.grain_block(_rows_from_grain(grain, column),
                           column, grain=grain, missing_key=missing)

    assert block["key_count"] == want["key_count"]
    assert block["keys_with_zero_active"] == want["keys_with_zero_active"]
    assert missing in block["all_statuses"], "the NULL bucket was dropped"
    assert len([k for k in block["keys"] if k != missing]) == want["named_keys"]

    for key, n, mw, an, amw in want["rows"]:
        assert block["all_statuses"][key] == {"count": n, "mw": round(mw, 1)}, key
        assert block["active"][key] == {"count": an, "mw": round(amw, 1)}, key


def test_the_live_grains_each_account_for_all_2278_rows():
    for grain, column in (("county", "county"), ("state", "state"),
                          ("study_process", "study_process")):
        block = ic.grain_block(_rows_from_grain(grain, column),
                               column, grain=grain,
                               missing_key=GRAINS["grains"][grain]["missing_key"])
        counts = sum(v["count"] for v in block["all_statuses"].values())
        assert counts == GRAINS["totals"]["rows"] == 2278, grain
        # Each key's MW is rounded to 0.1 on the way out, so summing 101 of them
        # back up drifts from the live 492,196.796 by less than a megawatt
        # (0.8 MW at the county grain). The COUNT above is exact and is what
        # proves nothing was dropped; this is the magnitude check beside it.
        mw = sum(v["mw"] for v in block["all_statuses"].values())
        assert abs(mw - GRAINS["totals"]["mw"]) < 1.0, (grain, mw)


def test_the_ambiguous_county_keys_are_exactly_the_four_the_bank_holds():
    """100 named counties, 106 (county, state) pairs — and this is where the six
    extra pairs live. Rebuilt from the receipt, not asserted from memory."""
    rows = []
    for county, states in GRAINS["county_states_ambiguous"].items():
        for st in states:
            rows.append(_row(
                county=None if county == "unlocated" else county,
                state=None if st == "unlocated" else st))
    rows.append(_row(county="KERN", state="CA"))
    block = ic.county_block(rows)
    assert block["state_pairing"]["ambiguous_keys"] == \
        sorted(GRAINS["county_states_ambiguous"])
    for county, states in GRAINS["county_states_ambiguous"].items():
        assert block["key_states"][county] == sorted(states)
        assert block["key_state"][county] is None


def test_the_live_generation_depth_is_one():
    gens = GRAINS["generations"]
    g = ic.generations_block(
        [{"snapshot_date": D(*[int(p) for p in e["snapshot_date"].split("-")]),
          "status": s, "row_count": v[0], "mw": v[1]}
         for e in gens for s, v in e["by_status"].items()],
        source_table="caiso_interconnection_queue_snapshots")
    assert g["depth"] == len(gens) == 1
    assert g["entries"][0]["rows"] == GRAINS["totals"]["rows"]
    assert g["entries"][0]["mw_total"] == round(GRAINS["totals"]["mw"], 1)


# ── The route ───────────────────────────────────────────────────────────────

def test_rollups_serves_the_pinned_contract(client):
    body = client.get("/api/interconnection/rollups").json()
    for key in ("snapshot_date", "data_note", "rollup_note",
                "generated_from_rows", "snapshot", "by_county", "by_state",
                "by_process", "generations", "cache"):
        assert key in body, key
    for block in ("by_county", "by_state", "by_process"):
        for key in ("grain", "key_count", "missing_key", "active_statuses",
                    "keys_with_zero_active", "keys", "all_statuses", "active"):
            assert key in body[block], (block, key)
    for key in ("key_states", "key_state", "state_pairing"):
        assert key in body["by_county"], key
    assert body["by_county"]["grain"] == "county"
    assert body["by_state"]["grain"] == "state"
    assert body["by_process"]["grain"] == "study_process"
    assert body["generations"]["depth"] == 1


def test_the_rollup_snapshot_chip_is_the_same_block_summary_serves(client):
    """No module can drift from its neighbour: the two surfaces share one
    helper, one table and one read shape, so their chips must agree."""
    roll = client.get("/api/interconnection/rollups").json()
    summ = client.get("/api/interconnection/summary").json()
    assert roll["snapshot"] == summ["snapshot"]
    assert roll["snapshot_date"] == summ["snapshot_date"] == "2026-08-07"
    assert roll["data_note"] == summ["data_note"]


def test_the_rollups_read_never_pulls_the_fat_columns(client):
    client.get("/api/interconnection/rollups")
    reads = [s for s, _ in client.pool.executed
             if "FROM caiso_interconnection_queue\n" in s
             and "max(snapshot_date)" not in s]
    assert reads
    select_list = reads[0].split("FROM")[0]
    selected = {t.strip(" ,\n") for t in select_list.replace("SELECT", "").split()}
    assert "raw" not in selected
    assert "withdrawal_comment" not in selected
    assert "generation_type" not in selected      # the rollups parse no fuel
    assert "SELECT *" not in reads[0]


def test_the_generations_read_is_reduced_in_the_database(client):
    """depth x statuses over the wire, not depth x 2,278 rows. The aggregate is
    what keeps this cheap as the bank deepens."""
    client.get("/api/interconnection/rollups")
    gen_sql = [s for s, _ in client.pool.executed
               if "caiso_interconnection_queue_snapshots" in s]
    assert len(gen_sql) == 1
    assert "GROUP BY snapshot_date, status" in gen_sql[0]
    assert "count(*)" in gen_sql[0] and "sum(capacity_mw)" in gen_sql[0]


def test_the_snapshots_table_is_read_by_nothing_but_generations(client):
    """The live table stays the source of truth for the current generation; the
    snapshots table is the trend read and nothing else."""
    for path in ("/api/interconnection/summary", "/api/interconnection/projects",
                 "/api/interconnection/events"):
        client.get(path)
    assert not any("caiso_interconnection_queue_snapshots" in s
                   for s, _ in client.pool.executed)


# ── The v0 contract is byte-stable ──────────────────────────────────────────

def test_v0_top_level_keys_are_unchanged_by_this_commit(client):
    """EVERY PRE-EXISTING KEY IS LOAD-BEARING. These are the exact top-level key
    sets v0 shipped; nothing here was renamed, reshaped or removed."""
    summary = client.get("/api/interconnection/summary").json()
    assert set(summary) == {
        "snapshot_date", "data_note", "generated_from_rows", "snapshot",
        "by_fuel", "by_queue_year", "by_proposed_cod_year", "by_county",
        "by_transmission_owner", "attrition", "unmapped_types", "cache"}

    projects = client.get("/api/interconnection/projects").json()
    assert set(projects) == {
        "snapshot_date", "data_note", "sort", "order", "filters", "page",
        "unmapped_types", "rows", "cache"}

    events = client.get("/api/interconnection/events").json()
    assert set(events) == {"count", "limit", "event_types", "rows", "note", "cache"}


def test_summary_by_county_keeps_its_v0_top_n_shape(client):
    """The full-cardinality rollup lives on /rollups PRECISELY so this one does
    not move. /summary's by_county is still the top-25 + named remainder the
    dashboard captured."""
    by_county = client.get("/api/interconnection/summary").json()["by_county"]
    assert set(by_county) == {"top", "other", "group_count", "top_n"}
    assert by_county["top_n"] == ic.COUNTY_TOP_N == 25
    assert set(by_county["other"]) == {"count", "mw", "group_count"}
    assert set(by_county["top"][0]) == {"county", "state", "count", "mw"}


def test_the_extracted_snapshot_helper_did_not_move_summary(client):
    """`_snapshot_block` was lifted out of summary_payload for reuse. Same keys,
    same values, same order — an extraction, not a change."""
    snapshot = client.get("/api/interconnection/summary").json()["snapshot"]
    assert list(snapshot) == ["snapshot_date", "project_count", "total_mw",
                              "statuses"]
    assert snapshot == {
        "snapshot_date": "2026-08-07",
        "project_count": 3,
        "total_mw": 600.0,
        "statuses": [
            {"status": "COMPLETED", "count": 1, "mw": 300.0},
            {"status": "WITHDRAWN", "count": 1, "mw": 200.0},
            {"status": "ACTIVE", "count": 1, "mw": 100.0},
        ],
    }
