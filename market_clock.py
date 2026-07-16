"""
market_clock — the deterministic CAISO market state machine (Ticker v1, Lane 1).

ONE state, computed once, read by every surface. The banner ticker, briefs, the
email arc, and the Paper Desk all read /api/market-clock; none computes its own
opinion of whether the Day-Ahead market has published. This module is that single
opinion, and it is a pure function: `now` is injected and there is no I/O, so
every state and boundary is unit-testable without a wall clock or a database.

Zero LLM. Inputs are (a) the wall clock in Pacific time, (b) the NERC
holiday/weekend calendar — reused from main's locked bundle and passed in as a
single computed `target_is_offpeak_all_day` flag (block-vocabulary context only;
the CAISO DAM clears a full 24h EVERY calendar day, so the calendar never
suppresses a cycle — it only flavours the settlement detail), and (c)
publication detection = "the lake holds the rows," passed in as `target_published`
plus the honest `da_published_at` ingest time. The caller (main) does the reads;
this module decides the state.

State machine (target trade date T = tomorrow PT — the auction currently marching
through its lifecycle during today's session):

    DA_BIDDING          before ~10:00 PT bid close, T's awards not yet in the lake
                        -> "Day-Ahead bidding open · trade date {T}"
    DA_MARKET_RUNNING   post bid close, T's DA LMP rows NOT yet in the lake
                        -> "Day-Ahead market running · awards pending"
    DA_PUBLISHED        T's DA rows detected (fresh window) -> "DA awards
                        published {time}" + the on-cycle SP15 print
    RT_LIVE             ambient: T published and the fresh window has elapsed, so
                        the live real-time (FMM) tape is the headline

Honesty rails:
    * Publication detection is the ONLY authority for DA_PUBLISHED / RT_LIVE — both
      require `target_published` true, so the machine can NEVER claim awards the
      lake does not hold, on any calendar day.
    * If a tier-1 feed is stale the caller passes it in `degraded_feeds`; the
      state then carries `degraded: true` naming the feed — the ops surface
      doubling as product honesty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta as _timedelta
from datetime import timezone as _timezone
from typing import Optional
from zoneinfo import ZoneInfo

# The market is Pacific. Every clock boundary below is a PT wall-clock time.
MARKET_TZ = "America/Los_Angeles"
_PT = ZoneInfo(MARKET_TZ)

# ---------------------------------------------------------------------------
# State vocabulary — the four ticker states.
# ---------------------------------------------------------------------------
DA_BIDDING = "DA_BIDDING"
DA_MARKET_RUNNING = "DA_MARKET_RUNNING"
DA_PUBLISHED = "DA_PUBLISHED"
RT_LIVE = "RT_LIVE"

# ---------------------------------------------------------------------------
# Clock boundaries (PT wall-clock). All are market facts except the last, which
# is an editorial choice documented as such.
# ---------------------------------------------------------------------------
# CAISO DAM bid-submission close. Before this, tomorrow's auction is bidding;
# after it the auction is running/published.
DA_BID_CLOSE_PT = _time(10, 0)
# When CAISO is expected to post DAM results. Used ONLY for next_expected and the
# overdue threshold — never asserted as fact (publication detection is).
DA_EXPECTED_PUBLISH_PT = _time(13, 0)
# Grace past expected publish before a still-absent DAM is treated as overdue.
DA_PUBLISH_GRACE = _timedelta(minutes=60)
# Editorial: how long the "awards just published" headline holds before the state
# relaxes to the ambient RT tape (RT_LIVE). Not a data claim — RT_LIVE still
# requires the rows to exist. Tunable; the Captain may move it.
DA_FRESH_HEADLINE_UNTIL_PT = _time(18, 0)

# Detection: a full DAM is 24 hourly rows per hub; require most of them present so
# a half-written publish never flips the state early (mirrors the pnode endpoint's
# completeness floor).
PUBLICATION_FLOOR_HOURS = 20

_DOW = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _pt(now_utc: _datetime) -> _datetime:
    return now_utc.astimezone(_PT)


def _pt_dt(d: _date, t: _time) -> _datetime:
    """A PT wall-clock (date, time) as a tz-aware PT datetime."""
    return _datetime.combine(d, t, tzinfo=_PT)


def _next_pt_time(now_pt: _datetime, t: _time) -> _datetime:
    """The next occurrence of PT wall-clock time `t` strictly after `now_pt`."""
    today = _pt_dt(now_pt.date(), t)
    return today if today > now_pt else today + _timedelta(days=1)


def _iso(dt: Optional[_datetime]) -> Optional[str]:
    return dt.astimezone(_timezone.utc).isoformat() if dt is not None else None


def _hhmm(dt: _datetime) -> str:
    return _pt(dt).strftime("%H:%M")


def _money(v: Optional[float]) -> Optional[str]:
    return f"${v:,.2f}" if v is not None else None


def _peak_ctx(target_is_offpeak_all_day: bool) -> str:
    """Block-vocabulary context for the settlement detail (the Joule v1.4
    distinction): Sunday/NERC-holiday = off-peak all day (no on-peak block
    exists); every other day carries the HE7–HE22 on-peak block."""
    return "off-peak all hours" if target_is_offpeak_all_day else "on-peak HE7–HE22"


@dataclass(frozen=True)
class MarketClock:
    """The computed market state — the whole /api/market-clock body bar `sources`,
    which the caller attaches (it names the tables IT read)."""

    state: str
    label: str
    detail: str
    trade_date: str
    next_expected: dict
    prints: dict
    as_of: str
    degraded: bool
    degraded_feeds: list

    def as_dict(self, sources: list) -> dict:
        return {
            "state": self.state,
            "label": self.label,
            "detail": self.detail,
            "trade_date": self.trade_date,
            "next_expected": self.next_expected,
            "prints": self.prints,
            "as_of": self.as_of,
            "degraded": self.degraded,
            "degraded_feeds": self.degraded_feeds,
            "sources": sources,
        }


def compute_clock(
    now_utc: _datetime,
    *,
    target_date: _date,
    target_published: bool,
    da_published_at: Optional[_datetime] = None,
    sp15_da_print: Optional[dict] = None,
    latest_fmm: Optional[dict] = None,
    target_is_offpeak_all_day: bool = False,
    degraded_feeds: Optional[list] = None,
) -> MarketClock:
    """Classify the market state for `now_utc` (tz-aware). Pure — no I/O.

    target_date          the trade date the current DA cycle targets (tomorrow PT).
    target_published     do the lake rows for target_date exist? (detection)
    da_published_at      MAX(ingested_ts) of target_date's DA rows — the honest
                         publication time surfaced in the DA_PUBLISHED label.
    sp15_da_print        {hub, ts, price, he} for the on-cycle SP15 DA print, or
                         None when target awards are not yet published.
    latest_fmm           {hub, market, ts, price} freshest FMM (RTPD) interval.
    target_is_offpeak_all_day  calendar block context for the settlement detail.
    degraded_feeds       tier-1 feeds the caller flagged stale (e.g. ["rtpd"]).
    """
    degraded_feeds = list(degraded_feeds or [])
    now_pt = _pt(now_utc)
    T = target_date.isoformat()
    dow = _DOW[target_date.isoweekday() - 1]
    peak = _peak_ctx(target_is_offpeak_all_day)

    # ── State resolution — publication-authoritative. ────────────────────────
    # DA_PUBLISHED / RT_LIVE require target_published, so the machine can never
    # claim awards the lake does not hold. The bidding/running split is a pure
    # wall-clock read of the bid-close boundary.
    if target_published:
        fresh = now_pt.time() < DA_FRESH_HEADLINE_UNTIL_PT
        state = DA_PUBLISHED if fresh else RT_LIVE
    elif now_pt.time() < DA_BID_CLOSE_PT:
        state = DA_BIDDING
    else:
        state = DA_MARKET_RUNNING

    # ── prints — always carry the live FMM layer; the DA print only once out. ─
    prints = {
        "sp15_da": sp15_da_print if target_published else None,
        "latest_fmm": latest_fmm,
    }

    # ── label / detail + next_expected, per state. ───────────────────────────
    if state == DA_BIDDING:
        label = f"Day-Ahead bidding open · trade date {T}"
        detail = f"{T} ({dow} — {peak}) · bids close {DA_BID_CLOSE_PT.strftime('%H:%M')} PT"
        next_expected = {
            "event": "bid_close",
            "at": _iso(_next_pt_time(now_pt, DA_BID_CLOSE_PT)),
        }

    elif state == DA_MARKET_RUNNING:
        label = "Day-Ahead market running · awards pending"
        detail = (
            f"trade date {T} ({dow} — {peak}) · awards expected ~"
            f"{DA_EXPECTED_PUBLISH_PT.strftime('%H:%M')} PT"
        )
        if "da" in degraded_feeds:
            detail += " · overdue"
        expected = _pt_dt(now_pt.date(), DA_EXPECTED_PUBLISH_PT)
        # next_expected is a FUTURE instant; once the expected time has passed the
        # awards are imminent/overdue and `at` is null (the degraded flag carries
        # the rest of the story).
        next_expected = {
            "event": "da_publish",
            "at": _iso(expected) if expected > now_pt else None,
        }

    elif state == DA_PUBLISHED:
        when = f" {_hhmm(da_published_at)} PT" if da_published_at is not None else ""
        label = f"DA awards published{when}"
        detail = f"trade date {T} ({dow} — {peak})"
        if sp15_da_print and sp15_da_print.get("price") is not None:
            he = sp15_da_print.get("he")
            he_tag = f" HE{he}" if he is not None else ""
            detail += f" · SP15 DA {_money(sp15_da_print['price'])}{he_tag}"
        next_expected = {
            "event": "bid_close",
            "at": _iso(_next_pt_time(now_pt, DA_BID_CLOSE_PT)),
        }

    else:  # RT_LIVE
        label = "Real-time market live"
        if latest_fmm and latest_fmm.get("price") is not None:
            fmm_ts = _datetime.fromisoformat(latest_fmm["ts"])
            detail = (
                f"FMM {latest_fmm.get('hub', 'SP15')} {_money(latest_fmm['price'])} "
                f"· as-of {_hhmm(fmm_ts)} PT · DA {T} published"
            )
        else:
            detail = f"DA {T} published · latest FMM unavailable"
        next_expected = {
            "event": "bid_close",
            "at": _iso(_next_pt_time(now_pt, DA_BID_CLOSE_PT)),
        }

    return MarketClock(
        state=state,
        label=label,
        detail=detail,
        trade_date=T,
        next_expected=next_expected,
        prints=prints,
        as_of=now_utc.astimezone(_timezone.utc).isoformat(),
        degraded=bool(degraded_feeds),
        degraded_feeds=degraded_feeds,
    )
