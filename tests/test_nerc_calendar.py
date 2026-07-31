"""
Tests for the NERC holiday calendar correction (rider ruled 2026-07-31).

WHAT WAS WRONG. `main._NERC_HOLIDAYS` was a hardcoded 2016-2026 list described
in its own comment as "the locked list is the source of truth". It was not: it
applied FEDERAL observance, which rolls a Saturday holiday BACK to the preceding
Friday. NERC does not. Under the NERC/WECC block definition Saturday is ALREADY
an on-peak day (6x16 is Mon-Sat), so a Saturday July 4th genuinely stays an
on-peak day and genuinely is not a holiday.

WHAT THESE TESTS PROTECT.

  * THE RULE ITSELF — Sunday shifts to Monday, Saturday does NOT move. Pinned
    directly on `nerc_holidays`, with the Saturday case asserted in both
    directions so nobody "fixes" it back to the federal convention.

  * AGREEMENT WITH THE PANTRY. `market_calendar` (region='CAISO', 2020-2030,
    66 holiday rows) is the external power calendar this repo loads, and the
    pantry's `nerc_calendar.py` reproduces it exactly. The full 66 dates are
    pinned here as a fixture cut from the LIVE table on 2026-07-31, and the
    computed calendar must reproduce every one. This is the test that makes the
    two implementations one implementation.

  * THE FOUR DATES THAT CHANGED, named individually, so a regression reports
    which one rather than "a set differs".

  * BEHAVIOUR COMPATIBILITY. Every date the old list got RIGHT must still be
    right — the correction narrowed the calendar, it did not rewrite it. The
    old list's 2016-2019 block and all its Sunday->Monday shifts are asserted
    unchanged.
"""

import datetime

import main


D = datetime.date


# ---------------------------------------------------------------------------
# The live fixture. Cut from `market_calendar` on 2026-07-31 with:
#
#   SELECT string_agg(holiday_date::text, ',' ORDER BY holiday_date)
#   FROM market_calendar WHERE region='CAISO' AND holiday_date IS NOT NULL;
#
# 66 rows, 2020-2030, six per year. This is the external power calendar the
# pantry loads via scripts/load_power_calendar.py, and it is the arbiter.
# ---------------------------------------------------------------------------

MARKET_CALENDAR_66 = [
    "2020-01-01", "2020-05-25", "2020-07-04", "2020-09-07", "2020-11-26", "2020-12-25",
    "2021-01-01", "2021-05-31", "2021-07-05", "2021-09-06", "2021-11-25", "2021-12-25",
    "2022-01-01", "2022-05-30", "2022-07-04", "2022-09-05", "2022-11-24", "2022-12-26",
    "2023-01-02", "2023-05-29", "2023-07-04", "2023-09-04", "2023-11-23", "2023-12-25",
    "2024-01-01", "2024-05-27", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    "2025-01-01", "2025-05-26", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    "2026-01-01", "2026-05-25", "2026-07-04", "2026-09-07", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-05-31", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-25",
    "2028-01-01", "2028-05-29", "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
    "2029-01-01", "2029-05-28", "2029-07-04", "2029-09-03", "2029-11-22", "2029-12-25",
    "2030-01-01", "2030-05-27", "2030-07-04", "2030-09-02", "2030-11-28", "2030-12-25",
]


# ---------------------------------------------------------------------------
# Agreement with the live table — the test that makes this one calendar.
# ---------------------------------------------------------------------------

def test_agrees_with_market_calendar_all_66_rows():
    """Every holiday market_calendar carries, the rule reproduces — and the
    rule invents none that the table does not carry, across the same span."""
    assert len(MARKET_CALENDAR_66) == 66

    computed = [d for d in main._NERC_HOLIDAYS if "2020" <= d[:4] <= "2030"]
    assert computed == MARKET_CALENDAR_66

    # Six per year, every year, with no year quietly dropping or doubling one.
    for year in range(2020, 2031):
        got = [d for d in computed if d.startswith(str(year))]
        assert len(got) == 6, f"{year} has {len(got)} holidays, expected 6"


def test_holiday_names_are_stable_identifiers():
    """The names are what receipts print and tests pin; renaming one is a
    breaking change, so the set is asserted rather than assumed."""
    assert set(main.nerc_holidays(2026).values()) == set(main._NERC_HOLIDAY_NAMES)
    assert set(main._NERC_HOLIDAY_NAMES) == {
        "new_years", "memorial", "independence", "labor", "thanksgiving",
        "christmas",
    }


# ---------------------------------------------------------------------------
# The rule: Sunday -> Monday, Saturday NOT moved.
# ---------------------------------------------------------------------------

def test_sunday_holiday_shifts_to_monday():
    # Jan 1 2023 was a SUNDAY -> observed Monday Jan 2.
    assert D(2023, 1, 1).weekday() == 6
    assert main.nerc_holidays(2023)[D(2023, 1, 2)] == "new_years"
    assert D(2023, 1, 1) not in main.nerc_holidays(2023)

    # Jul 4 2021 was a SUNDAY -> observed Monday Jul 5.
    assert D(2021, 7, 4).weekday() == 6
    assert main.nerc_holidays(2021)[D(2021, 7, 5)] == "independence"

    # Dec 25 2022 was a SUNDAY -> observed Monday Dec 26.
    assert D(2022, 12, 25).weekday() == 6
    assert main.nerc_holidays(2022)[D(2022, 12, 26)] == "christmas"


def test_saturday_holiday_is_not_moved():
    """THE HALF THAT SEPARATES NERC FROM FEDERAL. Do not 'fix' this."""
    # Jul 4 2026 is a SATURDAY. It stays on the 4th.
    assert D(2026, 7, 4).weekday() == 5
    assert main.nerc_holidays(2026)[D(2026, 7, 4)] == "independence"
    # And the preceding Friday is NOT a holiday — that is the federal answer.
    assert D(2026, 7, 3) not in main.nerc_holidays(2026)
    assert "2026-07-03" not in main._NERC_HOLIDAY_SET

    # Dec 25 2021 was a SATURDAY. It stays on the 25th, not the 24th.
    assert D(2021, 12, 25).weekday() == 5
    assert main.nerc_holidays(2021)[D(2021, 12, 25)] == "christmas"
    assert "2021-12-24" not in main._NERC_HOLIDAY_SET


def test_floating_holidays_never_need_an_observance_shift():
    """Memorial/Labor/Thanksgiving are pinned to a weekday by construction, so
    the shift logic must never touch them."""
    for year in range(2016, 2036):
        cal = main.nerc_holidays(year)
        by_name = {name: d for d, name in cal.items()}
        assert by_name["memorial"].weekday() == 0      # last Monday in May
        assert by_name["memorial"].month == 5
        assert by_name["labor"].weekday() == 0         # 1st Monday in Sep
        assert by_name["labor"].month == 9
        assert by_name["thanksgiving"].weekday() == 3  # 4th Thursday in Nov
        assert by_name["thanksgiving"].month == 11


def test_floating_holiday_arithmetic_on_known_dates():
    # Memorial = LAST Monday in May, which is not the 4th Monday in a 5-Monday
    # May: 2021-05-31 is the fifth.
    assert main.nerc_holidays(2021)[D(2021, 5, 31)] == "memorial"
    # Thanksgiving = 4th Thursday, NOT the last. Nov 2029 has five Thursdays
    # (1, 8, 15, 22, 29), so the two rules disagree and the 4th one wins.
    assert main.nerc_holidays(2029)[D(2029, 11, 22)] == "thanksgiving"
    assert D(2029, 11, 29).weekday() == 3  # a 5th Thursday exists
    assert D(2029, 11, 29) not in main.nerc_holidays(2029)


# ---------------------------------------------------------------------------
# The four dates the correction changed, named one by one.
# ---------------------------------------------------------------------------

def test_the_four_corrected_dates():
    """Each was simply wrong before, and each is a Saturday-holiday case."""
    # 1. Jul 4 2020 was a Saturday; the old list carried the federal 07-03.
    assert "2020-07-04" in main._NERC_HOLIDAY_SET
    assert "2020-07-03" not in main._NERC_HOLIDAY_SET

    # 2. Dec 25 2021 was a Saturday; the old list carried the federal 12-24.
    assert "2021-12-25" in main._NERC_HOLIDAY_SET
    assert "2021-12-24" not in main._NERC_HOLIDAY_SET

    # 3. Jan 1 2022 was a Saturday and the old list LOST IT ENTIRELY — it
    #    carried "2022-12-26" twice and the typo ate New Year's 2022.
    assert "2022-01-01" in main._NERC_HOLIDAY_SET
    assert main.nerc_holidays(2022)[D(2022, 1, 1)] == "new_years"

    # 4. Jul 4 2026 is a Saturday; the old list carried the federal 07-03.
    assert "2026-07-04" in main._NERC_HOLIDAY_SET
    assert "2026-07-03" not in main._NERC_HOLIDAY_SET


# ---------------------------------------------------------------------------
# Behaviour compatibility: everything the old list got right is still right.
# ---------------------------------------------------------------------------

# The old hardcoded list's 2016-2019 block, which the true rule reproduces
# EXACTLY — those four years contain no Saturday-holiday case at all.
OLD_LIST_2016_2019 = [
    "2016-01-01", "2016-05-30", "2016-07-04", "2016-09-05", "2016-11-24", "2016-12-26",
    "2017-01-02", "2017-05-29", "2017-07-04", "2017-09-04", "2017-11-23", "2017-12-25",
    "2018-01-01", "2018-05-28", "2018-07-04", "2018-09-03", "2018-11-22", "2018-12-25",
    "2019-01-01", "2019-05-27", "2019-07-04", "2019-09-02", "2019-11-28", "2019-12-25",
]


def test_pre_2020_output_is_byte_for_byte_unchanged():
    """The correction narrowed the calendar; it did not rewrite it. 2016-2019
    held no Saturday holiday, so every date the old list carried survives."""
    computed = [d for d in main._NERC_HOLIDAYS if "2016" <= d[:4] <= "2019"]
    assert computed == OLD_LIST_2016_2019


def test_the_span_is_wider_than_the_old_list_and_sorted():
    """A rule does not expire. The materialized list is only a cache of it, so
    it now runs past 2026 — and it stays a sorted list[str] because it is
    passed straight into SQL as %(holidays)s::date[]."""
    assert isinstance(main._NERC_HOLIDAYS, list)
    assert all(isinstance(d, str) for d in main._NERC_HOLIDAYS)
    assert main._NERC_HOLIDAYS == sorted(main._NERC_HOLIDAYS)
    assert len(main._NERC_HOLIDAYS) == len(set(main._NERC_HOLIDAYS))  # no dupes
    span = main._NERC_CALENDAR_MAX_YEAR - main._NERC_CALENDAR_MIN_YEAR + 1
    assert len(main._NERC_HOLIDAYS) == span * 6
    assert main._NERC_HOLIDAYS[0].startswith(str(main._NERC_CALENDAR_MIN_YEAR))
    assert main._NERC_HOLIDAYS[-1].startswith(str(main._NERC_CALENDAR_MAX_YEAR))


def test_holiday_set_mirrors_the_list():
    assert main._NERC_HOLIDAY_SET == frozenset(main._NERC_HOLIDAYS)


# ---------------------------------------------------------------------------
# is_nerc_holiday — the year-boundary case the helper exists for.
# ---------------------------------------------------------------------------

def test_is_nerc_holiday_spans_the_year_boundary():
    # 2023-01-02 is the OBSERVED 2023 New Year's and belongs to
    # nerc_holidays(2023) — the prior-year check must not lose it, and must not
    # invent a holiday on the un-observed Jan 1.
    assert main.is_nerc_holiday(D(2023, 1, 2)) is True
    assert main.is_nerc_holiday(D(2023, 1, 1)) is False
    # An ordinary day either side of a shifted Christmas.
    assert main.is_nerc_holiday(D(2022, 12, 26)) is True   # Sun 25 -> Mon 26
    assert main.is_nerc_holiday(D(2022, 12, 25)) is False
    assert main.is_nerc_holiday(D(2022, 12, 27)) is False


def test_is_nerc_holiday_agrees_with_the_materialized_set():
    """The function and the list are the same calendar, checked day by day
    across a full year that contains a Saturday holiday (2026)."""
    d = D(2026, 1, 1)
    while d.year == 2026:
        assert main.is_nerc_holiday(d) == (d.isoformat() in main._NERC_HOLIDAY_SET), d
        d += datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Downstream consumers keep behaviour-compatible outputs.
# ---------------------------------------------------------------------------

def test_market_clock_offpeak_all_day_follows_the_corrected_calendar():
    """The market-clock lane reuses the same bundle, so it moves with it."""
    # Saturday July 4 2026 is the holiday -> off-peak all day.
    assert main._market_clock_offpeak_all_day(D(2026, 7, 4)) is True
    # Friday July 3 2026 is an ordinary weekday again.
    assert main._market_clock_offpeak_all_day(D(2026, 7, 3)) is False
    # Sundays are off-peak all day regardless of the holiday calendar.
    assert main._market_clock_offpeak_all_day(D(2026, 7, 5)) is True
    # A plain Saturday has an on-peak block (Mon-SAT), so it is not off-peak
    # all day — the 52-days-a-year trap.
    assert main._market_clock_offpeak_all_day(D(2026, 7, 11)) is False
