"""
almanac.py — The Almanac publication surface: the shelf, the series, the issue.

This is the pure layer behind the three Almanac reads:

    GET /api/almanac                       the shelf   -> [shelf item]
    GET /api/almanac/{series}              the series  -> series page
    GET /api/almanac/{series}/{issue}      the issue   -> issue

and the pure transform the backfill adapter runs
(`scripts/backfill_almanac_daily.py`) to bank Kelvin's daily briefs as
`publications` rows. Both live here for one reason: the shape a backfilled row
takes and the shape the API serves are two halves of the same contract, and a
test that pins them together catches a drift that two separate modules would
hide.

Everything here is pure: no I/O, no clock, no database, no network. The route
layer in main.py does the reads; the script does the writes.

═══════════════════════════════════════════════════════════════════════════
THE RESPONSE CONTRACT — PINNED VERBATIM FROM THE SPEC. Lane B builds against
this, so it is transcribed here exactly as filed and asserted key-for-key in
tests/test_almanac.py:

    shelf item: { series, issue_key, headline, dek, issued_ts,
                  verified, read_minutes }
    issue:      { series, issue_key, headline, dek, author,
                  issued_ts, verified, verifier_version,
                  data_cutoff_ts, read_minutes,
                  body: [blocks as stored] }
    series pg:  { series, intro, latest: <issue>, archive:
                  [{issue_key, headline, issued_ts}] }

    Empty archive = [], absent series intro = null. Numerics/dates ISO strings.

NOTHING IS UNANNOUNCED. Every other lane in this repo ships additive fields
(`derivation`, `source`, `paper_only`) and documents them. This one ships no
unannounced keys: the spec pinned three shapes verbatim and named a second lane
building against them, so a key the contract does not carry is a contract
break, not a courtesy. The things those additive blocks would have carried —
the rules applied, the measured backfill state — are in
`docs/handoff_2026_08_07_almanac_data_layer.md` and in the module docstrings,
where they cost a reader nothing on the wire.

ONE ANNOUNCED AMENDMENT (2026-08-07): the ISSUE shape carries `read_minutes`.
The consumer that asked is THE DESK'S READ (dashboard
`src/components/weather/deskRead.ts`), which renders `latest` off the daily
series page — an issue, not a shelf item — found the field lived only on the
shelf item, and REFUSED both plausible fallbacks rather than invent a number
(its finding-test names the wire as the fix). Same derivation, same single
definition (`read_minutes()` below); the amendment is pinned key-for-key and
in order in tests/test_almanac.py, and the dashboard retires its fallback the
day this reaches the deployed wire.

═══════════════════════════════════════════════════════════════════════════
THE JUDGEMENT CALLS, AND WHY EACH WENT THE WAY IT DID

The spec pinned the field names and the empty-state rules. It did not pin the
following, and each one is a place a second implementation could silently
disagree with this one, so each is stated here and pinned by a test.

  1. THE SHELF IS A BARE ARRAY. The spec gives full response objects for the
     issue and the series page, but gives the shelf as "shelf ITEM" — the
     element, not an envelope. So `GET /api/almanac` serves a JSON array of
     shelf items, newest first, and nothing wraps it. There is no `count`, no
     `as_of`: either would be an unannounced key on a pinned contract.

  2. `read_minutes` IS DERIVED, NEVER STORED. It is the only field in the whole
     contract with no column behind it. Rule: count whitespace-separated tokens
     across the `md` of every `prose` block, divide by READ_WORDS_PER_MINUTE
     (200), round UP, floor at 1 when there is any prose at all. FIGURE BLOCKS
     COUNT ZERO — a figure is looked at, not read, and no honest word count
     exists for one. A body with no prose reads 0, not 1: zero is the arithmetic
     truth and 1 would claim a minute of reading that is not there.

  3. ARCHIVE EXCLUDES THE LATEST ISSUE. `latest` carries the newest published
     issue in full; `archive` carries every OLDER published issue as a stub.
     The spec's "Empty archive = []" clause is what settles this — if the
     archive included `latest`, it could never be empty while a series had
     anything published, and the clause would be dead text.

  4. NEWEST-FIRST IS `issued_ts DESC`, TIE-BROKEN ON `issue_key DESC`. An
     unpinned sort is a page whose order changes between deployments. The
     tie-break is load-bearing for a series backfilled in one transaction,
     where many rows can share an `issued_ts` to the microsecond.
     `issued_ts NULL` sorts LAST — a piece with no issue date is not the newest
     thing on the shelf.

  5. A DRAFT IS INDISTINGUISHABLE FROM AN ABSENCE. `status='draft'` rows are
     filtered in SQL, and a request for a draft issue by its exact key gets the
     same 404 as a request for an issue that was never written. Not 403: a 403
     would confirm the draft exists, which is precisely what "drafts never
     serve" is there to prevent.

  6. `intro` COMES FROM A CODE-SIDE REGISTRY, NOT A COLUMN. `publications` is a
     table of ISSUES; a per-series standing introduction is not an issue and
     has no row to live on. SERIES_INTRO below is that registry, and it is
     EMPTY at v0 — so every series serves `intro: null` today, exactly as the
     spec's "absent series intro = null" anticipates, and filling one later is
     a one-line change with no migration.

═══════════════════════════════════════════════════════════════════════════
THE BLOCK VOCABULARY, as the spec declared it and as the table stores it:

    {"type": "prose",  "md": "..."}
    {"type": "figure", "component": "...", "params": {...},
     "as_of": "<timestamptz>"}

`body` is served **as stored** — this module never reshapes, reorders, or
validates a block on the way out. That is deliberate: the Almanac is a
publication surface, the writer owns what a block says, and a serving layer
that "fixes" a block is a serving layer that can silently change what was
published. The only thing read out of a block here is prose word count, for
`read_minutes`, and an unknown block type is skipped rather than rejected —
a body carrying a block type this module has never heard of still serves.

VOCABULARY
    series     one of daily | weekly | monthly | article — the cadence lane
    issue_key  the stable key of one issue within its series. For `daily`,
               the ISO covered date ("2026-08-06"); UNIQUE(series, issue_key)
    issue      one published piece, in full, body blocks and all
    shelf      the cross-series card list, newest first
    archive    a series' older issues, as {issue_key, headline, issued_ts}
"""

from __future__ import annotations

import math
from datetime import date as _date
from datetime import datetime as _datetime
from typing import Any, Iterable, Optional

# ── The series vocabulary ────────────────────────────────────────────────────
# Mirrors the CHECK constraint declared in migration 154. Kept as a tuple, in
# the order the spec wrote it, so a route can validate a path/query value
# without a round trip and the error message can name the whole vocabulary.
SERIES_DAILY = "daily"
SERIES_WEEKLY = "weekly"
SERIES_MONTHLY = "monthly"
SERIES_ARTICLE = "article"
SERIES_VOCABULARY: tuple[str, ...] = (
    SERIES_DAILY, SERIES_WEEKLY, SERIES_MONTHLY, SERIES_ARTICLE,
)

# ── The status vocabulary ────────────────────────────────────────────────────
# Also mirrors migration 154's CHECK. Only PUBLISHED ever reaches the wire.
STATUS_DRAFT = "draft"
STATUS_PUBLISHED = "published"
STATUS_VOCABULARY: tuple[str, ...] = (STATUS_DRAFT, STATUS_PUBLISHED)

# ── The block vocabulary ─────────────────────────────────────────────────────
BLOCK_PROSE = "prose"
BLOCK_FIGURE = "figure"

# ── read_minutes ─────────────────────────────────────────────────────────────
# 200 wpm is the ordinary reading-speed convention for prose of this kind. It is
# a constant, not a measurement, and it is named here so the number on the card
# has exactly one definition in the building.
READ_WORDS_PER_MINUTE = 200

# ── Per-series standing introductions ────────────────────────────────────────
# EMPTY AT v0, DELIBERATELY. See judgement call 3 above: there is no column for
# a series intro because a series intro is not an issue. Every series serves
# `intro: null` until a line is added here.
SERIES_INTRO: dict[str, str] = {}

# ── The backfilled daily's byline ────────────────────────────────────────────
# The ratified Weather Desk byline constant. This is a DELIBERATE TWO-LINE
# COUPLING with `main.WEATHER_BYLINE`, not drift: this module cannot import
# main (main imports this), and the string is a ratified invariant of the
# surface rather than per-row data. `(agent)` is non-negotiable everywhere the
# name renders. `test_the_byline_constant_has_not_drifted_from_main` asserts
# the two are identical, so changing one without the other fails the build.
DAILY_BACKFILL_AUTHOR = "By Kelvin · Weather Desk (agent)"

# The brief_type this lane backfills FROM. Kelvin's Weather Desk daily is
# single-keyed (brief_key = ''), one row per covered date.
BACKFILL_BRIEF_TYPE = "weather"


# ═══════════════════════════════════════════════════════════════════════════
# Rendering helpers
# ═══════════════════════════════════════════════════════════════════════════

def iso(value: Any) -> Optional[str]:
    """Every date and timestamp on this wire, as an ISO 8601 string.

    Per the spec's closing clause ("Numerics/dates ISO strings"): a `date` or
    `datetime` off psycopg becomes its ISO form, a string is passed through
    already-ISO, and null stays null. Timestamps keep whatever offset the
    driver handed over — the reads pin the session to UTC, so these render as
    UTC, and normalising here would hide it if that ever stopped being true.
    """
    if value is None:
        return None
    if isinstance(value, (_datetime, _date)):
        return value.isoformat()
    return str(value)


def _blocks(body: Any) -> list[dict]:
    """`body` as a list of block dicts, tolerating the shapes a table can hold.

    Null, a non-list, or a list with non-dict members all degrade to what is
    actually usable rather than raising: this runs on a publication surface,
    and one malformed body must not 500 the whole shelf.
    """
    if not isinstance(body, list):
        return []
    return [b for b in body if isinstance(b, dict)]


def prose_word_count(body: Any) -> int:
    """Whitespace-separated tokens across every `prose` block's `md`.

    Figure blocks and unknown block types contribute nothing — see the block
    vocabulary note in the module docstring.
    """
    words = 0
    for block in _blocks(body):
        if block.get("type") != BLOCK_PROSE:
            continue
        md = block.get("md")
        if isinstance(md, str):
            words += len(md.split())
    return words


def read_minutes(body: Any) -> int:
    """Minutes to read this body, rounded UP, floored at 1 — 0 with no prose.

    The only derived field in the contract. See judgement call 2.
    """
    words = prose_word_count(body)
    if words <= 0:
        return 0
    return max(1, math.ceil(words / READ_WORDS_PER_MINUTE))


# ═══════════════════════════════════════════════════════════════════════════
# The three pinned shapes
# ═══════════════════════════════════════════════════════════════════════════

def shelf_item(row: dict) -> dict:
    """One card on the shelf.

    { series, issue_key, headline, dek, issued_ts, verified, read_minutes }

    `verified` is coerced to a real bool because the column is NOT NULL and a
    card that renders a verification chip must never be handed a null.
    """
    return {
        "series": row.get("series"),
        "issue_key": row.get("issue_key"),
        "headline": row.get("headline"),
        "dek": row.get("dek"),
        "issued_ts": iso(row.get("issued_ts")),
        "verified": bool(row.get("verified")),
        "read_minutes": read_minutes(row.get("body")),
    }


def issue(row: dict) -> dict:
    """One published piece, in full.

    { series, issue_key, headline, dek, author, issued_ts, verified,
      verifier_version, data_cutoff_ts, read_minutes,
      body: [blocks as stored] }

    `read_minutes` is the contract's one announced amendment (2026-08-07 — see
    the module docstring): the same derivation the shelf card carries, off the
    same single definition, placed before `body` so the payload stays the
    terminal key.

    `body` is passed through EXACTLY as the table holds it — never reshaped,
    never reordered, never block-validated. A null/non-list body serves `[]`
    rather than null so a client can always iterate.
    """
    body = row.get("body")
    return {
        "series": row.get("series"),
        "issue_key": row.get("issue_key"),
        "headline": row.get("headline"),
        "dek": row.get("dek"),
        "author": row.get("author"),
        "issued_ts": iso(row.get("issued_ts")),
        "verified": bool(row.get("verified")),
        "verifier_version": row.get("verifier_version"),
        "data_cutoff_ts": iso(row.get("data_cutoff_ts")),
        "read_minutes": read_minutes(body),
        "body": body if isinstance(body, list) else [],
    }


def archive_row(row: dict) -> dict:
    """One older issue, as a stub: { issue_key, headline, issued_ts }."""
    return {
        "issue_key": row.get("issue_key"),
        "headline": row.get("headline"),
        "issued_ts": iso(row.get("issued_ts")),
    }


def series_page(series: str, rows: Iterable[dict]) -> dict:
    """The series page, off ONE newest-first read of that series' published run.

    { series, intro, latest: <issue>, archive: [{issue_key, headline,
      issued_ts}] }

    `rows` must arrive newest-first (the SQL orders them). The head becomes
    `latest` in full; EVERY OLDER ROW becomes an archive stub — the latest is
    NOT repeated in the archive (judgement call 3). Nothing published at all
    serves `latest: null, archive: []`, never a 404: a series that exists in
    the vocabulary but has not published yet is an empty shelf, not a missing
    page.
    """
    ordered = list(rows)
    return {
        "series": series,
        "intro": SERIES_INTRO.get(series),
        "latest": issue(ordered[0]) if ordered else None,
        "archive": [archive_row(r) for r in ordered[1:]],
    }


# ═══════════════════════════════════════════════════════════════════════════
# THE BACKFILL TRANSFORM — one banked Kelvin daily brief -> one publications row
#
# Pure. `scripts/backfill_almanac_daily.py` reads `joule_briefs`, runs every row
# through here, and UPSERTs the result. Living beside the serving layer is the
# point: `test_a_backfilled_row_serves_as_the_pinned_issue_contract` pushes a
# real brief through this transform and straight out through `issue()`, so the
# two halves cannot drift apart unnoticed.
#
# THE FIELD-BY-FIELD MAPPING, AND THE THREE NULLS THAT ARE FINDINGS:
#
#   series           'daily' — Kelvin's brief is a daily cadence piece
#   issue_key        brief_date, ISO ("2026-08-06"). Measured 2026-08-07: all
#                    14 banked rows are brief_key='' and one row per date, so
#                    the date alone is a unique key within the series
#   headline         headline verbatim (non-null on all 14)
#   dek              NULL — A FINDING. The writer banks no dek and no field
#                    stands in for one. Deriving one from the first sentence
#                    would put an editorial decision in a serving layer and
#                    print prose the desk never wrote
#   body             ONE prose block, `md` = content_md with the DUPLICATED
#                    HEADLINE LINE and the TRAILING BYLINE LINE stripped, both
#                    losslessly (see THE TWO DUPLICATED LINES below)
#   author           DAILY_BACKFILL_AUTHOR, the ratified byline constant
#   issued_ts        created_at — the instant the brief was banked
#   verifier_version NULL — A FINDING. Neither `joule_briefs`, nor the row's
#                    `sources_used`, nor the linked `joule_calls.meta` carries a
#                    verifier version stamp. `voice_version` (v4) and
#                    `bundle_version` (weather_bundle_v1) are the VOICE and the
#                    DATA BUNDLE; putting either in a field named
#                    verifier_version would label a thing as the version of a
#                    check that did not produce it. Filed as the writer-side fix
#                    this lane wants, exactly as /api/desk/by-play filed its
#                    play key on 2026-07-31
#   verified         TRUE, and this is READ, NOT ASSUMED. The pantry writer
#                    UPSERTs a weather row ONLY when the per-sentence verifier
#                    PASSES, so the EXISTENCE of the row is itself the verifier
#                    stamp — the same ratified invariant `main.WeatherDateline`
#                    and `/api/weather/brief/history` already serve. That is
#                    "the brief's own verification state"; there is no second,
#                    truer field being ignored
#   data_cutoff_ts   NULL — A FINDING, and the one that loses nothing. The brief
#                    states its data currency at DAY grain
#                    (`sources_used.as_of_date`), and that value equals
#                    brief_date on all 14 banked rows — so it is already on the
#                    wire as `issue_key`. The column is a timestamptz; turning a
#                    date into an instant means choosing an hour the source
#                    never stated. The writer's clock (`created_at`,
#                    `joule_calls.created_at`) is available and is NOT used: it
#                    records when the desk WROTE, not what the desk had READ,
#                    and filing a writer-clock fact under a data-cutoff field is
#                    how a surface starts lying quietly
#   status           'published' — these 14 briefs are already live on
#                    /api/weather/brief/*. Backfilling them as drafts would take
#                    a published run off a publication surface
#
# THE TWO DUPLICATED LINES. A banked weather brief's content_md is a SELF-
# CONTAINED document: it opens with its own headline as the first line, and it
# closes with the byline. Measured 2026-08-07, 14 of 14 banked briefs do both.
#
# The Almanac renders `headline` and `author` in their own slots around `body`,
# so passing content_md through verbatim prints the headline twice and the
# byline twice. The two strippers below remove those lines ONLY on an EXACT
# match against the value being served in the dedicated field, and pass the
# body through untouched otherwise.
#
# BOTH ARE LOSSLESS. Every character either one removes is preserved verbatim
# in another column of the very same row — the headline line in `headline`, the
# byline line in `author`. Nothing the desk wrote leaves the building; it stops
# being printed twice. That is the whole justification, and it is the reason
# this is the only editing of writer prose anywhere in this lane: the serving
# layer serves `body` exactly as stored, and this transform runs ONCE, at
# backfill time, on a document being moved between two surfaces with different
# furniture.
# ═══════════════════════════════════════════════════════════════════════════

def strip_duplicated_headline(content_md: str, headline: Optional[str]) -> str:
    """content_md without its leading headline line, when it has one.

    Exact match only. A brief whose first line merely resembles the headline
    keeps every character it was written with.
    """
    if not isinstance(content_md, str):
        return ""
    if not headline:
        return content_md
    first, sep, rest = content_md.partition("\n")
    if first.strip() != headline.strip():
        return content_md
    if not sep:
        # The whole body was the headline and nothing else. Removing it would
        # serve an empty piece; keep it and let the duplication be visible.
        return content_md
    return rest.lstrip("\n")


def strip_trailing_byline(content_md: str, byline: Optional[str]) -> str:
    """content_md without its closing byline line, when it has one.

    Exact match on the LAST non-empty line only. A brief that names Kelvin
    mid-sentence, or signs off differently, keeps every character.
    """
    if not isinstance(content_md, str):
        return ""
    if not byline:
        return content_md
    head, sep, last = content_md.rstrip().rpartition("\n")
    if not sep:
        # One line, and it is the byline: there is no piece left to serve.
        # Keep it rather than banking an empty body.
        return content_md
    if last.strip() != byline.strip():
        return content_md
    return head.rstrip("\n")


def publication_from_daily_brief(brief: dict) -> dict:
    """One `joule_briefs` weather row -> one `publications` row, as a dict.

    Column-keyed, ready for the adapter's UPSERT. Pure: no clock, no DB. See
    the field-by-field mapping above for why each of the three nulls is null.
    """
    headline = brief.get("headline")
    md = strip_duplicated_headline(brief.get("content_md"), headline)
    md = strip_trailing_byline(md, DAILY_BACKFILL_AUTHOR)
    return {
        "series": SERIES_DAILY,
        "issue_key": iso(brief.get("brief_date")),
        "headline": headline,
        "dek": None,
        "body": [{"type": BLOCK_PROSE, "md": md}],
        "author": DAILY_BACKFILL_AUTHOR,
        "issued_ts": brief.get("created_at"),
        "verifier_version": None,
        # The table's ratified invariant: a stored weather row is a PASSED row.
        "verified": True,
        "data_cutoff_ts": None,
        "status": STATUS_PUBLISHED,
    }
