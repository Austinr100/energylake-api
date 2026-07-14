"""
publication_clock — the Market Clock seed (PR-1 of the brief-freshness arc).

Given a brief_type, the brief_date in hand, and the current instant, decide
whether the edition is current / pending / overdue / unscheduled, and expose
the surrounding clock (trade_date, published_at, next_expected_at) as an
*additive* rider on the brief endpoints. No existing response field is touched.

Pure by design: compute_status() takes `now` as an argument and does no I/O, so
every state is unit-testable without a wall clock or a database.

State machine (for a scheduled type):
    * current   — the edition in hand covers today's trade date (or newer).
    * pending   — today's edition is due but we are still inside the grace
                  window (now < expected + grace).
    * overdue   — today's edition is due and the grace window has elapsed.
    * unscheduled — the type carries no publication cadence (see the map).

"Today's trade date" is the trade date the *current* edition should carry, which
flips at `expected` each day: brief_date = run_date - date_offset.

Schedule map (locked 2026-07-13 from 14-day joule_briefs receipts):
    daily                       expected 05:00 UTC, grace 60m, brief_date = run_date - 1
    caiso_fuel_mix_chart        expected 12:00 UTC, grace 60m, brief_date = run_date - 1
    caiso_load_deviation        unscheduled — rolling intraday product, no edition
    caiso_renewables_deviation  unscheduled — rolling intraday product, no edition
    caiso_hub_lmp               expected 10:23 UTC, grace 60m, brief_date = run_date

Any brief_type absent from the map is treated as unscheduled — compute_status
never raises on an unknown type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from typing import Optional

# ---------------------------------------------------------------------------
# Status vocabulary (the four banner states).
# ---------------------------------------------------------------------------
CURRENT = "current"
PENDING = "pending"
OVERDUE = "overdue"
UNSCHEDULED = "unscheduled"


@dataclass(frozen=True)
class ScheduleEntry:
    """One brief type's publication cadence.

    expected_utc  time-of-day (UTC) the edition is expected to publish.
    grace_minutes minutes past `expected` before a missing edition is overdue.
    date_offset   brief_date = run_date - date_offset (0 = same day, 1 = prior).
    """

    expected_utc: _time
    grace_minutes: int
    date_offset: int


# The schedule map — the single source of truth for the publication clock.
#
# A ScheduleEntry value means the type has an edition cadence (banner applies).
# A None value means the type is deliberately unscheduled and documented below;
# a missing key means an unknown type — both resolve to `unscheduled`, so this
# map is additive-safe and never the reason a new type 500s.
SCHEDULE: dict[str, Optional[ScheduleEntry]] = {
    # Daily editorial brief: run 05:00 UTC, recaps the prior trade day.
    "daily": ScheduleEntry(_time(5, 0), 60, 1),
    # #99 fuel-mix chart brief: run 12:00 UTC, covers the prior trade day.
    "caiso_fuel_mix_chart": ScheduleEntry(_time(12, 0), 60, 1),
    # Rolling intraday products — edition/dateline semantics don't apply, so
    # they carry no banner. Permanently unscheduled, not a pending TODO.
    "caiso_load_deviation": None,
    "caiso_renewables_deviation": None,
    # #? hub LMP brief: run 10:23 UTC, brief_date = run_date (receipt-confirmed,
    # so offset 0). Grace 60m absorbs observed GitHub scheduler lag on the
    # shared runner (declared 11:53 UTC vs receipts landing ~12:44 UTC ≈ 51m).
    "caiso_hub_lmp": ScheduleEntry(_time(10, 23), 60, 0),
}


@dataclass(frozen=True)
class PublicationStatus:
    """The publication rider — an additive block on a brief response.

    trade_date        ISO date the edition in hand covers (its brief_date).
    published_at      ISO timestamp the edition was written (created_at).
    next_expected_at  ISO timestamp of the next scheduled run after `now`
                      (None for unscheduled types).
    """

    status: str
    trade_date: Optional[str]
    published_at: Optional[str]
    next_expected_at: Optional[str]

    def as_rider(self) -> dict:
        """The dict merged into a brief response under the `publication` key."""
        return {
            "status": self.status,
            "trade_date": self.trade_date,
            "published_at": self.published_at,
            "next_expected_at": self.next_expected_at,
        }


def _iso_date(d: Optional[_date]) -> Optional[str]:
    return d.isoformat() if d is not None else None


def _iso_dt(dt: Optional[_datetime]) -> Optional[str]:
    return dt.isoformat() if dt is not None else None


def _clock(entry: ScheduleEntry, now: _datetime):
    """Resolve (expected_trade_date, current_run_dt, next_run_dt) for `now`.

    The current cycle flips at `expected` each UTC day: at or after today's
    expected time the current run is today's; before it, yesterday's.
    """
    todays_run = _datetime.combine(now.date(), entry.expected_utc, tzinfo=_timezone.utc)
    if now >= todays_run:
        current_run_dt = todays_run
        next_run_dt = todays_run + _timedelta(days=1)
    else:
        current_run_dt = todays_run - _timedelta(days=1)
        next_run_dt = todays_run
    expected_trade_date = current_run_dt.date() - _timedelta(days=entry.date_offset)
    return expected_trade_date, current_run_dt, next_run_dt


def compute_status(
    brief_type: str,
    brief_date: Optional[_date],
    now: _datetime,
    published_at: Optional[_datetime] = None,
) -> PublicationStatus:
    """Classify the freshness of the brief in hand against the schedule.

    Pure: `now` is injected, no I/O. `now` must be a tz-aware UTC datetime.

    * Unscheduled/unknown type -> UNSCHEDULED (never raises).
    * brief_date >= today's trade date -> CURRENT (covers the "published early"
      case too — an edition at or ahead of the expected date is current).
    * Otherwise behind: PENDING while now < expected + grace, else OVERDUE.
    """
    entry = SCHEDULE.get(brief_type)
    if entry is None:
        return PublicationStatus(
            status=UNSCHEDULED,
            trade_date=_iso_date(brief_date),
            published_at=_iso_dt(published_at),
            next_expected_at=None,
        )

    expected_trade_date, current_run_dt, next_run_dt = _clock(entry, now)
    deadline = current_run_dt + _timedelta(minutes=entry.grace_minutes)

    if brief_date is not None and brief_date >= expected_trade_date:
        status = CURRENT
    elif now < deadline:
        status = PENDING
    else:
        status = OVERDUE

    return PublicationStatus(
        status=status,
        trade_date=_iso_date(brief_date),
        published_at=_iso_dt(published_at),
        next_expected_at=_iso_dt(next_run_dt),
    )
