"""d091689: press_editions in a throwaway Postgres, for the press routes' tests.

tests/fixtures/press_d091689 holds byte copies of pantry's press/ vectors
(manifest.json pins the pantry commit and each file's sha-256) and the table
text spec d091689 §7 gives (ddl_press_editions.sql). `vector(name)` checks the
file's bytes before it returns anything, so a hand edit is a red test.

`cluster()` is load_bank_d091673's throwaway Postgres with nothing of the
tropical bank loaded, and press_editions created. `insert()` writes one row
with the body as jsonb parsed by Postgres from the text it is given (a vector
goes in as its file's own bytes). `constructed()` loads the 2,000-row bank
the plans run on (the two vectors among them). PgPool is load_bank_d091673's.

Not a test module (no test_ prefix): the tests and the receipts' scripts both
import it.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import pathlib
from datetime import date, datetime, timedelta, timezone

import load_bank_d091673 as _lb

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "press_d091689"
MANIFEST = json.loads((FIX / "manifest.json").read_text())
DDL = (FIX / "ddl_press_editions.sql").read_text()

DAILY = "vectors/edition_daily_2026_10_10.json"
ARTICLE = "vectors/edition_article_2026_10_10_gas_carried_california.json"
DIGESTS = "vectors/digests.json"

UTC = timezone.utc
# When the vectors are taken to have been submitted, and the instant the
# bodies are banked at: 05:52 ET for the daily (its scouts filed 05:12-05:48
# ET), the article at 08:30 ET, the bodies at 09:00 ET.
DAILY_SUBMITTED = datetime(2026, 10, 10, 9, 52, tzinfo=UTC)
ARTICLE_SUBMITTED = datetime(2026, 10, 10, 12, 30, tzinfo=UTC)
NOW = datetime(2026, 10, 10, 13, 0, tzinfo=UTC)
WRITER = "architect"

initdb_path = _lb.initdb_path
PgPool = _lb.PgPool


def raw(name: str) -> bytes:
    b = (FIX / name).read_bytes()
    got = hashlib.sha256(b).hexdigest()
    want = MANIFEST["files"][name]
    if got != want:
        raise AssertionError(f"{name}: sha256 {got} is not the pinned copy of pantry's ({want})")
    return b


def vector(name: str) -> dict:
    return json.loads(raw(name).decode("utf-8"))


def digests() -> dict:
    return json.loads(raw(DIGESTS).decode("utf-8"))


def canonical_sha(doc) -> str:
    """pantry press/README.md's digest: canonical bytes, ensure_ascii=False, UTF-8."""
    return hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def body_text(doc) -> str:
    """A plain-text rendering for the NOT NULL column. Constructed here: the
    API never reads body_text, and pantry's writer decides what it holds."""
    out = []

    def walk(v):
        if isinstance(v, dict):
            for k in ("headline", "deck", "text"):
                if isinstance(v.get(k), str):
                    out.append(v[k])
            for k, x in v.items():
                if isinstance(x, (dict, list)):
                    walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(doc)
    return "\n".join(out)


def row(doc, *, revision=0, submitted_at, writer=WRITER, text=None, withdrawn_at=None,
        withdrawn_reason=None) -> dict:
    """press_editions' columns for one document. headline is the lead's (the
    table's column; pantry's writer decides, see the handback §4)."""
    return {"kind": doc["kind"], "edition_date": date.fromisoformat(doc["edition_date"]),
            "slug": doc.get("slug", ""), "revision": revision, "schema": doc["schema"],
            "headline": doc["lead"]["headline"],
            "body": text if text is not None else json.dumps(doc, ensure_ascii=False),
            "body_text": body_text(doc), "sha256": canonical_sha(doc), "writer": writer,
            "submitted_at": submitted_at, "withdrawn_at": withdrawn_at,
            "withdrawn_reason": withdrawn_reason}


INSERT = """
    INSERT INTO press_editions (kind, edition_date, slug, revision, schema, headline, body,
                                body_text, sha256, writer, submitted_at, withdrawn_at,
                                withdrawn_reason)
    VALUES (%(kind)s, %(edition_date)s, %(slug)s, %(revision)s, %(schema)s, %(headline)s,
            %(body)s::jsonb, %(body_text)s, %(sha256)s, %(writer)s, %(submitted_at)s,
            %(withdrawn_at)s, %(withdrawn_reason)s)
"""


def insert(conn, r: dict) -> None:
    conn.execute(INSERT, r)


def daily_row(**kw) -> dict:
    """The banked daily as a row, its body the file's own bytes."""
    return row(vector(DAILY), text=raw(DAILY).decode("utf-8"),
               submitted_at=kw.pop("submitted_at", DAILY_SUBMITTED), **kw)


def article_row(**kw) -> dict:
    return row(vector(ARTICLE), text=raw(ARTICLE).decode("utf-8"),
               submitted_at=kw.pop("submitted_at", ARTICLE_SUBMITTED), **kw)


def correction(doc: dict) -> dict:
    """Revision 1 of a document: one digest paragraph reworded."""
    d = copy.deepcopy(doc)
    d["digest"][0]["text"] = d["digest"][0]["text"] + " (Corrected.)"
    return d


def reset(conn) -> None:
    conn.execute("DROP TABLE IF EXISTS press_editions")
    conn.execute(DDL)


@contextlib.contextmanager
def cluster():
    """A throwaway Postgres with an empty press_editions. Caller skips when
    initdb_path() is None."""
    with _lb.cluster(load_bank=False) as conn:
        reset(conn)
        yield conn


# ── the constructed bank (spec d091689 §6: 2,000 rows) ──────────────────────
#
#   daily     1,461 identities, 2022-10-11 .. 2026-10-10 (the newest is the
#             banked vector); every 30th has a revision 1 (49), every third of
#             those withdrawn (17)                                  1,510 rows
#   weekly    209 identities, Mondays from 2022-10-10                 209 rows
#   monthly   48 identities, the 1st, 2022-11 .. 2026-10               48 rows
#   article   233 identities on 2022-10-14 .. 2026-10-10, two on some
#             dates (the newest is the banked vector)                 233 rows
#                                                                   2,000 rows

N_CONSTRUCTED = 2000
FIRST_DAY = date(2022, 10, 11)
LAST_DAY = date(2026, 10, 10)


def _as(doc: dict, kind: str, d: date, slug: str = "") -> dict:
    x = copy.deepcopy(doc)
    x["kind"], x["edition_date"] = kind, d.isoformat()
    if slug:
        x["slug"] = slug
    else:
        x.pop("slug", None)
    x["lead"]["headline"] = f"{kind} {d.isoformat()}{(' ' + slug) if slug else ''}"
    return x


def constructed_rows() -> list[dict]:
    daily, article = vector(DAILY), vector(ARTICLE)
    out = []
    days = (LAST_DAY - FIRST_DAY).days + 1
    for i in range(days):
        d = FIRST_DAY + timedelta(days=i)
        sub = datetime(d.year, d.month, d.day, 9, 52, tzinfo=UTC)
        if d == LAST_DAY:
            out.append(daily_row())
            continue
        doc = _as(daily, "daily", d)
        out.append(row(doc, submitted_at=sub))
        if i % 30 == 0:
            k = i // 30
            out.append(row(correction(doc), revision=1, submitted_at=sub + timedelta(hours=3),
                           withdrawn_at=(sub + timedelta(hours=5)) if k % 3 == 0 else None,
                           withdrawn_reason="constructed withdrawal" if k % 3 == 0 else None))
    monday = date(2022, 10, 10)
    for i in range(209):
        d = monday + timedelta(weeks=i)
        out.append(row(_as(daily, "weekly", d),
                       submitted_at=datetime(d.year, d.month, d.day, 14, tzinfo=UTC)))
    for i in range(48):
        y, m = 2022 + (10 + i) // 12, (10 + i) % 12 + 1
        d = date(y, m, 1)
        out.append(row(_as(daily, "monthly", d),
                       submitted_at=datetime(y, m, 1, 15, tzinfo=UTC)))
    n_art = N_CONSTRUCTED - len(out)
    for i in range(n_art - 1):
        base = i - 1 if i % 10 == 9 else i              # every tenth shares a date
        d = FIRST_DAY + timedelta(days=3 + 6 * base)
        slug = f"constructed-{i:04d}"
        out.append(row(_as(article, "article", d, slug),
                       submitted_at=datetime(d.year, d.month, d.day, 12, i % 60, tzinfo=UTC)))
    out.append(article_row())
    assert len(out) == N_CONSTRUCTED, len(out)
    return out


def constructed(conn) -> None:
    reset(conn)
    with conn.cursor() as cur:
        cur.executemany(INSERT, constructed_rows())
    conn.execute("ANALYZE press_editions")
