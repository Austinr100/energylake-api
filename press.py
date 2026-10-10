"""The press API — d091689: the Joule Report's editions, served as the bank states them.

    GET /api/press/front                the newest daily edition in force, in full,
                                        and the headers of the newest weekly, the
                                        newest monthly and the five newest articles
    GET /api/press/edition?kind=&date=[&slug=][&revision=]
                                        one edition in full; without revision, the
                                        one in force
    GET /api/press/editions?kind=[&n=]  headers only, newest first, n 1-60 (default
                                        14); above the cap is refused, never trimmed

READ-ONLY. Pantry owns press_editions (lane d091688; the table text is spec
d091689 §7). This module holds the SQL and the pure shaping; main.py holds the
routes, the memo and the statement timeout (D-09-25-75).

THE RULINGS (D-09-25-184, D-09-25-185; spec d091689) and where each is held:

  1. THE BODY IS SERVED AS WRITTEN. `edition.body` is the row's `body` as
     psycopg hands the jsonb over: nothing added inside it, nothing removed,
     nothing read out of it (`edition_obj` never looks inside `body`). Beside
     it ride the row's own columns (EDITION_FIELDS) and `corrected`, which is
     revision > 0 and nothing else. `body_text` is not served.
  2. IN FORCE is the highest revision of an identity (kind, edition_date, slug)
     whose withdrawn_at IS NULL: every in-force read filters on it and orders
     by revision DESC. A revision asked for by number is served whatever its
     state, with `withdrawn` and its reason; an identity whose every revision
     is withdrawn is a 404 that says so (`edition_404`).
  3. ABSENCE IS STATED. No daily in force is a 200 with `edition: null` and a
     sentence; no weekly, monthly or article is null or [] with its own
     sentence. /front carries STALE_AFTER_H and the age of the newest daily.
  4. REPO RULES. Every statement reads press_editions through its identity
     index (press_editions_identity: kind, edition_date, slug, revision), one
     statement per read, under the 2 s statement timeout; the reads of a
     route run concurrently; every route is a single-flight memo (300 s).
     Plans: docs/receipts/press-api-d091689/plans.md.

NEWEST. Editions are ordered by edition_date, then slug, then revision, all
descending: the identity index read backwards. Two articles of one date are
ordered by slug (descending), not by submitted_at; see the handback §5.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# ── parameters ──────────────────────────────────────────────────────────────

KINDS = ("daily", "weekly", "monthly", "article")
SLUGGED = ("article",)                    # the kinds whose identity carries a slug
SCHEMA = "el.edition.v1"
EDITIONS_N_DEFAULT = 14
EDITIONS_N_MAX = 60
FRONT_ARTICLES = 5
REVISION_MAX = 32767                      # smallint
SLUG_MAX = 200
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SLUG_RE = re.compile(r"^[^\s\x00-\x1f\x7f]+$")
PACIFIC = ZoneInfo("America/Los_Angeles")

# The daily is written each morning (the 2026-10-10 vector: scout reports
# 05:12-05:48 ET, edition_date the Pacific date). A day plus two hours past
# the newest daily's first submission, the next one has not arrived. Not a
# datasets row: the bank has no freshness arm for press_editions (handback §6).
STALE_AFTER_H = 26.0


class ParamError(ValueError):
    """A bad parameter. `field` names it; the route's 400 says `field: message`."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field, self.message = field, message


def parse_kind(raw: Optional[str]) -> str:
    if raw is None or raw == "":
        raise ParamError("kind", f"is required: one of {list(KINDS)}")
    if raw not in KINDS:
        raise ParamError("kind", f"must be one of {list(KINDS)}, got {raw!r}")
    return raw


def parse_date(raw: Optional[str]) -> date:
    if raw is None or raw == "":
        raise ParamError("date", "is required: the edition_date, YYYY-MM-DD")
    if not DATE_RE.match(raw):
        raise ParamError("date", f"must be YYYY-MM-DD, got {raw!r}")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ParamError("date", f"is not a calendar date, got {raw!r}")


def parse_slug(kind: str, raw: Optional[str]) -> str:
    """The identity's slug as the bank keys it: '' for every kind but article."""
    given = raw is not None and raw != ""
    if kind not in SLUGGED:
        if given:
            raise ParamError("slug", f"a {kind} edition has no slug (the bank keys it ''), "
                                     f"got {raw!r}")
        return ""
    if not given:
        raise ParamError("slug", "is required for an article: its slug as the bank keys it")
    if len(raw) > SLUG_MAX or not SLUG_RE.match(raw):
        raise ParamError("slug", f"must be 1-{SLUG_MAX} characters with no whitespace or "
                                 f"control characters, got {raw!r}")
    return raw


def parse_revision(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        r = int(raw)
    except ValueError:
        raise ParamError("revision", f"must be an integer 0-{REVISION_MAX}, got {raw!r}")
    if not 0 <= r <= REVISION_MAX:
        raise ParamError("revision", f"must be 0-{REVISION_MAX}, got {r}")
    return r


def parse_n(raw: Optional[str]) -> int:
    """n or ParamError. Above EDITIONS_N_MAX is refused, never trimmed."""
    if raw is None or raw == "":
        return EDITIONS_N_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        raise ParamError("n", f"must be an integer 1-{EDITIONS_N_MAX}, got {raw!r}")
    if not 1 <= n <= EDITIONS_N_MAX:
        raise ParamError("n", f"must be 1-{EDITIONS_N_MAX} editions, got {n}")
    return n


# ── statements ──────────────────────────────────────────────────────────────
#
# Every statement names its kind and walks press_editions_identity. An
# in-force read is `withdrawn_at IS NULL ORDER BY ... revision DESC LIMIT 1`
# on one identity, or the identity order read backwards for "newest".

_FULL = ("kind, edition_date, slug, revision, schema, headline, body, sha256, writer, "
         "submitted_at, withdrawn_at, withdrawn_reason")
_HEADER = "kind, edition_date, slug, headline, revision, submitted_at"

# /front's edition: the newest daily in force; the first revision the bank
# holds of that identity (the age is measured from it, so a correction does
# not reset the clock); and the newest daily row held in any state (so a
# withdrawn newer daily is said, not hidden). Three index probes, one statement.
FRONT_DAILY_SQL = f"""
    SELECT e.kind, e.edition_date, e.slug, e.revision, e.schema, e.headline, e.body,
           e.sha256, e.writer, e.submitted_at, e.withdrawn_at, e.withdrawn_reason,
           f.revision AS first_revision, f.submitted_at AS first_submitted_at,
           h.edition_date AS held_edition_date, h.revision AS held_revision,
           h.withdrawn_at AS held_withdrawn_at, h.withdrawn_reason AS held_withdrawn_reason
      FROM (SELECT 1) AS one
      LEFT JOIN LATERAL (
            SELECT {_FULL}
              FROM press_editions
             WHERE kind = 'daily' AND withdrawn_at IS NULL
             ORDER BY edition_date DESC, slug DESC, revision DESC
             LIMIT 1) AS e ON true
      LEFT JOIN LATERAL (
            SELECT revision, submitted_at
              FROM press_editions
             WHERE kind = 'daily' AND edition_date = e.edition_date AND slug = e.slug
             ORDER BY revision
             LIMIT 1) AS f ON true
      LEFT JOIN LATERAL (
            SELECT edition_date, revision, withdrawn_at, withdrawn_reason
              FROM press_editions
             WHERE kind = 'daily'
             ORDER BY edition_date DESC, slug DESC, revision DESC
             LIMIT 1) AS h ON true
"""

# The headers of one kind's n newest identities in force. DISTINCT ON here is
# not d091551's trap: the identity index is read backwards in the DISTINCT ON
# order, so Unique passes rows through as they come and LIMIT stops the walk
# after n identities (plans.md shows the rows read). An identity's in-force
# row is its first non-withdrawn row in that order: its highest live revision.
HEADERS_SQL = f"""
    SELECT DISTINCT ON (edition_date, slug) {_HEADER}
      FROM press_editions
     WHERE kind = %(kind)s AND withdrawn_at IS NULL
     ORDER BY edition_date DESC, slug DESC, revision DESC
     LIMIT %(n)s
"""

# /front's headers: the newest weekly, the newest monthly, the five newest
# articles, in force. One statement, three bounded backward walks.
FRONT_HEADERS_SQL = f"""
    (SELECT {_HEADER}
       FROM press_editions
      WHERE kind = 'weekly' AND withdrawn_at IS NULL
      ORDER BY edition_date DESC, slug DESC, revision DESC
      LIMIT 1)
    UNION ALL
    (SELECT {_HEADER}
       FROM press_editions
      WHERE kind = 'monthly' AND withdrawn_at IS NULL
      ORDER BY edition_date DESC, slug DESC, revision DESC
      LIMIT 1)
    UNION ALL
    (SELECT DISTINCT ON (edition_date, slug) {_HEADER}
       FROM press_editions
      WHERE kind = 'article' AND withdrawn_at IS NULL
      ORDER BY edition_date DESC, slug DESC, revision DESC
      LIMIT {FRONT_ARTICLES})
"""

# One identity in force, with what the 404 must say when there is none: how
# many revisions are held and how many withdrawn, and the newest one's state.
EDITION_SQL = f"""
    SELECT e.kind, e.edition_date, e.slug, e.revision, e.schema, e.headline, e.body,
           e.sha256, e.writer, e.submitted_at, e.withdrawn_at, e.withdrawn_reason,
           c.n_held, c.n_withdrawn,
           t.revision AS top_revision, t.withdrawn_at AS top_withdrawn_at,
           t.withdrawn_reason AS top_withdrawn_reason
      FROM (SELECT 1) AS one
      LEFT JOIN LATERAL (
            SELECT {_FULL}
              FROM press_editions
             WHERE kind = %(kind)s AND edition_date = %(date)s AND slug = %(slug)s
               AND withdrawn_at IS NULL
             ORDER BY revision DESC
             LIMIT 1) AS e ON true
      LEFT JOIN LATERAL (
            SELECT count(*) AS n_held, count(withdrawn_at) AS n_withdrawn
              FROM press_editions
             WHERE kind = %(kind)s AND edition_date = %(date)s AND slug = %(slug)s) AS c ON true
      LEFT JOIN LATERAL (
            SELECT revision, withdrawn_at, withdrawn_reason
              FROM press_editions
             WHERE kind = %(kind)s AND edition_date = %(date)s AND slug = %(slug)s
             ORDER BY revision DESC
             LIMIT 1) AS t ON true
"""

# One revision by number, in any state, and the identity's revision in force.
EDITION_AT_SQL = f"""
    SELECT e.kind, e.edition_date, e.slug, e.revision, e.schema, e.headline, e.body,
           e.sha256, e.writer, e.submitted_at, e.withdrawn_at, e.withdrawn_reason,
           f.revision AS in_force_revision
      FROM (SELECT 1) AS one
      LEFT JOIN LATERAL (
            SELECT {_FULL}
              FROM press_editions
             WHERE kind = %(kind)s AND edition_date = %(date)s AND slug = %(slug)s
               AND revision = %(revision)s) AS e ON true
      LEFT JOIN LATERAL (
            SELECT revision
              FROM press_editions
             WHERE kind = %(kind)s AND edition_date = %(date)s AND slug = %(slug)s
               AND withdrawn_at IS NULL
             ORDER BY revision DESC
             LIMIT 1) AS f ON true
"""

STATEMENTS = {"FRONT_DAILY_SQL": FRONT_DAILY_SQL, "FRONT_HEADERS_SQL": FRONT_HEADERS_SQL,
              "HEADERS_SQL": HEADERS_SQL, "EDITION_SQL": EDITION_SQL,
              "EDITION_AT_SQL": EDITION_AT_SQL}


# ── statements the page prints or reads ─────────────────────────────────────

BODY_RULE = ("edition.body is the row's body (el.edition.v1) as written: the API adds "
             "nothing inside it and removes nothing. The fields beside it are the row's own "
             "columns; corrected is revision > 0. body_text is not served.")
IN_FORCE_RULE = ("in force is the highest revision of an identity (kind, edition_date, slug) "
                 "whose withdrawn_at is null. A withdrawn revision is served only when asked "
                 "for by its number, with withdrawn true and its reason.")
ORDER_RULE = ("newest first: edition_date, then slug, then revision, all descending. Two "
              "editions of one kind and date are ordered by slug, not by submitted_at.")
SHA256_RULE = ("sha256 is the row's column as stored: the sha-256 of the body's canonical "
               "bytes (json.dumps(body, ensure_ascii=False, sort_keys=True, "
               "separators=(',', ':')) as UTF-8; pantry press/README.md). The API does not "
               "recompute it.")
TIMESTAMPS = "ISO 8601, UTC, e.g. 2026-10-10T12:00:00+00:00; edition_date is a date, YYYY-MM-DD"
FRESHNESS_RULE = (f"age_h is the time since the bank first received the newest daily in force "
                  f"(its lowest revision's submitted_at), so a correction does not reset it. "
                  f"stale is age_h > stale_after_h ({STALE_AFTER_H:g} h: a day and two hours). "
                  f"today_pacific is the Pacific date at graded_at; edition_date is the "
                  f"Pacific date the edition is for.")


# ── shaping ─────────────────────────────────────────────────────────────────

def _ts(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return v.astimezone(timezone.utc).isoformat()


def _d(v) -> Optional[str]:
    if v is None:
        return None
    return v if isinstance(v, str) else v.isoformat()


def _absence(reason: str, detail: str) -> dict:
    return {"reason": reason, "detail": detail}


def identity_text(kind: str, edition_date, slug: str) -> str:
    s = f" {slug}" if slug else ""
    return f"{kind} {_d(edition_date)}{s}"


def header_obj(r: dict) -> dict:
    return {"kind": r["kind"], "edition_date": _d(r["edition_date"]), "slug": r["slug"],
            "headline": r["headline"], "revision": r["revision"],
            "submitted_at": _ts(r["submitted_at"])}


def edition_obj(r: dict, *, in_force_revision: Optional[int]) -> dict:
    """One edition in full. `body` is the row's, untouched (rule 1)."""
    return {
        "kind": r["kind"], "edition_date": _d(r["edition_date"]), "slug": r["slug"],
        "revision": r["revision"], "schema": r["schema"], "headline": r["headline"],
        "sha256": r["sha256"], "submitted_at": _ts(r["submitted_at"]), "writer": r["writer"],
        "corrected": r["revision"] > 0,
        "withdrawn": r["withdrawn_at"] is not None,
        "withdrawn_at": _ts(r["withdrawn_at"]),
        "withdrawn_reason": r["withdrawn_reason"],
        "in_force": in_force_revision is not None and in_force_revision == r["revision"],
        "in_force_revision": in_force_revision,
        "body": r["body"],
    }


EDITION_FIELDS = ("kind", "edition_date", "slug", "revision", "schema", "headline", "sha256",
                  "submitted_at", "writer", "corrected", "withdrawn", "withdrawn_at",
                  "withdrawn_reason", "in_force", "in_force_revision", "body")


def _common() -> dict:
    return {"body_rule": BODY_RULE, "in_force_rule": IN_FORCE_RULE, "sha256_rule": SHA256_RULE,
            "timestamps": TIMESTAMPS}


# ── /front ──────────────────────────────────────────────────────────────────

def build_front(*, daily: Optional[dict], headers: list, now: datetime) -> dict:
    """GET /front. `daily` is FRONT_DAILY_SQL's one row; `headers` FRONT_HEADERS_SQL's."""
    today = now.astimezone(PACIFIC).date()
    e = daily if daily is not None and daily.get("revision") is not None else None
    held = daily if daily is not None and daily.get("held_revision") is not None else None

    edition = edition_obj(e, in_force_revision=e["revision"]) if e else None
    first = e["first_submitted_at"] if e else None
    age_h = round((now - first).total_seconds() / 3600.0, 2) if first is not None else None

    newest_withdrawn = None
    if held is not None and held["held_withdrawn_at"] is not None and (
            e is None or (held["held_edition_date"], held["held_revision"])
            > (e["edition_date"], e["revision"])):
        newest_withdrawn = {
            "edition_date": _d(held["held_edition_date"]), "revision": held["held_revision"],
            "withdrawn_at": _ts(held["held_withdrawn_at"]),
            "withdrawn_reason": held["held_withdrawn_reason"]}

    if e is not None:
        absence = None
    elif held is None:
        absence = _absence("no_daily", "The bank holds no daily edition yet.")
    else:
        absence = _absence(
            "no_daily_in_force",
            f"The bank holds daily editions, but every revision of each is withdrawn; the "
            f"newest is {_d(held['held_edition_date'])}, revision {held['held_revision']}.")

    by_kind = {"weekly": [], "monthly": [], "article": []}
    for r in headers:
        by_kind[r["kind"]].append(r)
    weekly = header_obj(by_kind["weekly"][0]) if by_kind["weekly"] else None
    monthly = header_obj(by_kind["monthly"][0]) if by_kind["monthly"] else None
    articles = [header_obj(r) for r in by_kind["article"]]

    return {
        "edition": edition,
        "absence": absence,
        "freshness": {
            "edition_date": _d(e["edition_date"]) if e else None,
            "first_submitted_at": _ts(first),
            "age_h": age_h,
            "stale_after_h": STALE_AFTER_H,
            "stale": None if age_h is None else age_h > STALE_AFTER_H,
            "today_pacific": today.isoformat(),
            "is_today": None if e is None else e["edition_date"] == today,
            "graded_at": _ts(now),
            "newest_withdrawn": newest_withdrawn,
            "rule": FRESHNESS_RULE,
        },
        "weekly": weekly,
        "weekly_absence": None if weekly else _absence(
            "no_weekly", "The bank holds no weekly edition in force."),
        "monthly": monthly,
        "monthly_absence": None if monthly else _absence(
            "no_monthly", "The bank holds no monthly edition in force."),
        "articles": articles,
        "articles_absence": None if articles else _absence(
            "no_article", "The bank holds no article in force."),
        "articles_n": FRONT_ARTICLES,
        "order_rule": ORDER_RULE,
        **_common(),
    }


# ── /edition ────────────────────────────────────────────────────────────────

class EditionNotFound(LookupError):
    """The route's 404; the message says which case it is."""


def edition_404(*, kind: str, edition_date, slug: str, revision: Optional[int],
                row: Optional[dict]) -> str:
    who = identity_text(kind, edition_date, slug)
    if revision is not None:
        f = row.get("in_force_revision") if row else None
        tail = (f"; revision {f} is in force" if f is not None
                else "; no revision of it is in force")
        return f"no revision {revision} of {who} in the bank{tail}"
    if not row or not row.get("n_held"):
        return f"no edition {who} in the bank"
    return (f"every revision of {who} is withdrawn ({row['n_withdrawn']} of {row['n_held']}); "
            f"the newest, revision {row['top_revision']}, was withdrawn at "
            f"{_ts(row['top_withdrawn_at'])}: {row['top_withdrawn_reason']}")


def build_edition(*, kind: str, edition_date, slug: str, revision: Optional[int],
                  row: Optional[dict]) -> dict:
    """GET /edition. Raises EditionNotFound with the 404's sentence."""
    if row is None or row.get("revision") is None:
        raise EditionNotFound(edition_404(kind=kind, edition_date=edition_date, slug=slug,
                                          revision=revision, row=row))
    in_force = row["in_force_revision"] if revision is not None else row["revision"]
    return {
        "request": {"kind": kind, "date": _d(edition_date), "slug": slug,
                    "revision": revision},
        "edition": edition_obj(row, in_force_revision=in_force),
        **_common(),
    }


# ── /editions ───────────────────────────────────────────────────────────────

def build_editions(*, kind: str, n: int, rows: list) -> dict:
    """GET /editions. Headers only, newest first, at most n."""
    eds = [header_obj(r) for r in rows]
    return {
        "kind": kind,
        "n": n,
        "count": len(eds),
        "editions": eds,
        "absence": None if eds else _absence(
            f"no_{kind}", f"The bank holds no {kind} edition in force."),
        "order_rule": ORDER_RULE,
        "in_force_rule": IN_FORCE_RULE,
        "timestamps": TIMESTAMPS,
    }
