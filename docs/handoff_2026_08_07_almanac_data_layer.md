# Handoff — The Almanac data layer, lane A (`energylake-api`)

**Date:** 2026-08-07
**Branch:** `claude/almanac-data-layer-6xaxcn`, branched off `main` at `604e3ef`
(after the Lab Paper Desk, PR #60, merged)
**Spec:** `cc_spec_2026_08_06_the_almanac_publication_surface.md`
**House law followed:** `docs/handoff_2026_08_06_lab_paper_desk_v0_api.md` and
the Regime lane before it — pure-layer/route split, fake-pool tests, honest
empty states, EXPLAIN receipt per new query shape, mechanical grep tests.

**Scope shipped:** one migration DECLARED (not applied), one backfill adapter,
three endpoints, + 91 new tests.
Suite: **1209 passed** (91 new, 1118 pre-existing, **none modified**).

**Nothing has been written to the pantry.** Migration 154 is declared and
un-applied; the ledger row 154 is still `reserved`; the backfill script has not
been run. See §6 for exactly what the architect does next.

New files: `almanac.py` (the pure layer), `migrations/154_publications.sql`,
`scripts/backfill_almanac_daily.py`, `tests/test_almanac.py`.
`main.py` gains three routes, four SQL constants, two helpers and four
route-index lines; `README.md` gains an Almanac section.

---

## 1. The response contract, as served

Pinned verbatim from the spec. Lane B builds against this.

```
shelf item: { series, issue_key, headline, dek, issued_ts,
              verified, read_minutes }
issue:      { series, issue_key, headline, dek, author,
              issued_ts, verified, verifier_version,
              data_cutoff_ts, body: [blocks as stored] }
series pg:  { series, intro, latest: <issue>, archive:
              [{issue_key, headline, issued_ts}] }
```

Every shape is asserted as an **exact key set** — and so is its **key order**,
so a diff of the response against the spec reads cleanly.

**This lane ships ZERO additive fields**, which is a deliberate break with every
other lane in the repo. `/api/lab/paper-desk` ships `derivation`, `source` and
`paper_only` and documents them; the Regime lane ships `rules` and `query_plan`.
Here the spec pinned three shapes verbatim *and named a second lane building
against them*, so an unannounced key is a contract break rather than a courtesy.
Everything those blocks would have carried is in this document and in the module
docstrings, where it costs a reader nothing on the wire.
`test_no_additive_keys_reach_the_wire_anywhere` holds the line.

### The five judgement calls the spec left open

Each is a place a second implementation could silently disagree, so each is
stated and pinned by a test.

| # | call | why |
|---|---|---|
| 1 | **`GET /api/almanac` serves a BARE ARRAY** | The spec gives full response objects for the issue and the series page, but gives the shelf as "shelf **item**" — the element, not an envelope. A `count` or `as_of` wrapper would be an unannounced key. |
| 2 | **`read_minutes` = prose words / 200, rounded UP, floored at 1; 0 with no prose** | The only field in the contract with no column behind it. **Figure blocks count zero** — a figure is looked at, not read, and no honest word count exists for one. A body with no prose reads **0, not 1**: zero is the arithmetic truth, 1 claims a minute that is not there. |
| 3 | **`archive` EXCLUDES `latest`** | Settled by the spec's own "Empty archive = []" clause: if the archive repeated `latest`, it could never be empty while a series had anything published, and the clause would be dead text. |
| 4 | **Newest-first is `issued_ts DESC NULLS LAST, issue_key DESC`** | An unpinned sort is a page whose order changes between deployments. The tie-break is **load-bearing**: a backfill writes a whole series inside one transaction where `now()` is constant, so without it the order of a backfilled shelf is whatever the heap returns. NULL `issued_ts` sorts **last** — a piece with no issue date is not the newest thing on the shelf. |
| 5 | **`intro` comes from a code-side registry, not a column** | `publications` is a table of ISSUES; a standing per-series introduction is not an issue and has no row to live on. `almanac.SERIES_INTRO` is that registry and it is **empty at v0**, so every series serves `intro: null` today — exactly as the spec's "absent series intro = null" anticipates — and filling one later is a one-line change with no migration. |

### Drafts, and the shape of their absence

`status = 'published'` is a predicate in **all three** SQL statements, asserted
by `test_published_only_is_a_sql_predicate_not_a_python_filter`. A draft cannot
reach a serializer at all.

A draft requested by its exact key gets **404 — byte-identical to the 404 for an
issue that was never written** (`test_a_draft_404_is_indistinguishable_from_...`).
Not a 403: a 403 confirms the draft exists, which is precisely what "drafts
never serve" is there to prevent.

### The empty states, all of them

| state | served |
|---|---|
| nothing published | `[]` (shelf) |
| series in the vocabulary, nothing published | `200 {series, intro: null, latest: null, archive: []}` — the page exists, it is empty |
| series with exactly one issue | `archive: []` |
| series outside the vocabulary (`/api/almanac/quarterly`) | `404`, detail names the whole vocabulary |
| `?series=quarterly` | `400`, detail names the whole vocabulary — never a silently empty shelf |
| issue absent, or a draft | `404` |
| `publications` table absent (today) | honest empty + a WARNING log — see §5 |
| any other DB error | `503 db unavailable` |

---

## 2. Migration 154 — DECLARED, NOT APPLIED

`migrations/154_publications.sql`. Ledger row 154 was reserved 2026-08-07
(`attestation='ledger-2026-08-07'`, note "almanac lane A: publications table +
daily-brief backfill") and **is still `reserved` in the pantry.** The file
self-records — its final statement flips the reserved row to `committed` with
`attestation='self-recorded'`, in the same transaction as the DDL, per the
convention set by 148 and 151. Apply the whole file or none of it.

Migrations belong to `energylake-pantry`. This one is declared in the API repo
because the lane that reads it is here; mirror or move the file at apply time.

### Four NOT NULLs beyond what the spec wrote — stated, and optional

The spec named `verified boolean NOT NULL` and left the rest unqualified. The
declared DDL adds four more, each because a NULL in that column produces a row
that cannot be served or cannot be addressed:

- **`series`** — a CHECK does not reject NULL and UNIQUE does not collapse
  NULLs, so without this a null-series row is both un-routable *and* insertable
  without limit.
- **`issue_key`** — same reasoning; it is the other half of the identity.
- **`status`** — a NULL status matches neither `'draft'` nor `'published'`, so
  the row is invisible to the API *and* to any draft-review tool: silently lost.
- **`body`** — `NOT NULL DEFAULT '[]'::jsonb`. Empty is a real state (a stub
  issue); NULL is not a different one.

Plus one CHECK the spec did not name: **`jsonb_typeof(body) = 'array'`**. Block
*structure* is deliberately left unconstrained — a CHECK enumerating block types
would need a migration every time the Almanac learns a new figure — but an
object where a list belongs breaks ordering, which is the entire point of the
column.

**These are pantry-side guardrails, not API dependencies.** The serving layer
tolerates every one of those NULLs already: it coerces `verified` to a bool,
serves a non-list body as `[]`, and filters `status` in SQL. If the architect
prefers the spec's literal nullability, drop the four NOT NULLs and nothing in
this repo changes. `test_a_null_body_serves_an_empty_list_...` pins that.

---

## 3. The backfill adapter

`scripts/backfill_almanac_daily.py`. **The only write path in this repository,
and it is not an endpoint** — a hand-run script, not imported by the app, not
routed, not scheduled. `test_the_backfill_script_is_not_reachable_from_the_app`
asserts `main.py` does not so much as name it.

```
joule_briefs (brief_type='weather')  ->  publications (series='daily')
```

**Dry-run is the default and `--apply` is the only way past it.** There is no
`--yes`, no env var, no config file that flips it. A bare run opens a
transaction, does every `INSERT … ON CONFLICT` the real run would do, prints the
identical receipt, and **rolls back**. So the dry-run receipt is not a
simulation of the write — it *is* the write, executed against the real table and
discarded. "A dry run that would have worked, and an apply that fails on a
constraint" is the failure mode this removes.

**Idempotent** on `ON CONFLICT (series, issue_key) DO UPDATE`, riding migration
154's UNIQUE. The receipt separates `inserted` / `updated` / `unchanged` by
**reading each row's pre-image inside the same transaction** and comparing it
column by column — *not* by trusting a row count. `ON CONFLICT DO UPDATE`
reports every conflicting row as affected whether or not it changed a byte, so a
count-based receipt reads a no-op re-run as "14 updated" and reports work it did
not do.

Stated rather than discovered: a `publications` row a human wrote by hand under
a `daily` issue_key **will be overwritten** by a re-run. `--skip-existing` is the
flag that avoids it. The script never DELETEs, never TRUNCATEs, and never touches
a row outside `series='daily'` and the issue keys it read this run.

### KELVIN's daily, not Joule's

`joule_briefs` holds **both** a `brief_type='daily'` (Joule's editorial tape
brief — 53 rows, sourced from `tape_filings`) and `brief_type='weather'`
(Kelvin's Weather Desk daily — 14 rows). The spec says *Kelvin's*. Reading the
wrong discriminator would bank 53 filings-summaries under a weather desk's
byline; `test_the_backfill_reads_kelvins_brief_type_not_the_editorial_daily`
pins it.

**A collision to know about before the second lane lands:** `publications` keys
on `(series, issue_key)`, and both brief types are one-row-per-date. If Joule's
editorial daily is ever backfilled into `series='daily'` too, the two runs
collide on every shared date. They need either separate series or a compound
issue_key — that decision belongs to whoever files lane C, and it is cheaper
made now than after both are banked.

---

## 4. THE FINDINGS — three nulls, and why each stays null

The transform (`almanac.publication_from_daily_brief`) fills eight of the eleven
columns from the brief. Three are null, and each is a **writer-side gap this
lane declines to paper over** — the same posture `/api/desk/by-play` took on
2026-07-31 when it served `play: null` rather than parse the rationale prose.
`test_the_three_findings_are_null_and_are_never_guessed` fails the build if a
later "improvement" guesses one.

### 4.1 `dek` — the writer banks no standfirst

There is no dek column and no field standing in for one. Deriving one from the
first sentence would put an editorial decision in a serving layer and print
prose the desk never wrote. **The fix wanted from the writer:** one field.

### 4.2 `verifier_version` — no verifier version is stamped anywhere

Measured across all 14 briefs: neither `joule_briefs`, nor the row's
`sources_used` jsonb (`{as_of_date, bundle_version, degraded_beats, empty_beats,
gates}`), nor the linked `joule_calls.meta` (`{attempt, provider, cache_hit,
as_of_date, live_beats, voice_version, bundle_version}`) carries one.

`voice_version` (`v4`) is the **voice** the brief was written at.
`bundle_version` (`weather_bundle_v1`) is the **data bundle** it read. Putting
either in a field named `verifier_version` would label a thing as the version of
a check that did not produce it — the field would then read as authoritative
provenance for a verification it knows nothing about. **The fix wanted from the
writer:** stamp the per-sentence verifier's version at UPSERT time.

**A null `verifier_version` does NOT mean unverified.** `verified` is the field
that answers that, it is NOT NULL, and
`test_a_null_verifier_version_does_not_mean_unverified` pins the two apart.

### 4.3 `data_cutoff_ts` — day-grain currency, and it loses nothing

The brief states its data currency at DAY grain (`sources_used.as_of_date`), and
that value **equals `brief_date` on all 14 banked rows** — so it is already on
the wire, as `issue_key`. The column is a `timestamptz`; turning a date into an
instant means choosing an hour the source never stated.

The writer's clock is available (`joule_briefs.created_at`,
`joule_calls.created_at`) and is **deliberately not used**. It records when the
desk *wrote*, not what the desk had *read*. Filing a writer-clock fact under a
data-cutoff field is how a surface starts lying quietly — and it would read as
more precise than the day-grain truth it replaced.

### 4.4 `verified: true` is READ, not assumed

The pantry weather writer UPSERTs a row **only when the per-sentence verifier
PASSES**, so the *existence* of the source row **is** the verifier stamp. This is
not this lane's invention: it is the ratified invariant `main.WeatherDateline`
and `/api/weather/brief/history` already serve, documented at `main.py:2043`.
That is "the brief's own verification state" — there is no second, truer field
being ignored.

### 4.5 Two duplicated lines, stripped losslessly

Measured 2026-08-07: **14 of 14** briefs open `content_md` with their own
headline as the first line and close it with the byline. A weather brief is a
self-contained document; the Almanac renders `headline` and `author` in their
own slots around `body`, so passing `content_md` through verbatim prints both
twice.

Both are stripped, **on an exact match only**, and both are **lossless** — every
character removed survives verbatim in another column of the same row (the
headline line in `headline`, the byline line in `author`). That is the entire
justification, and it is the only editing of writer prose anywhere in this lane:
the serving layer serves `body` exactly as stored, and this transform runs once,
at backfill time, moving a document between two surfaces with different
furniture. `test_the_prose_between_the_two_stripped_lines_is_untouched` asserts
the body is a **verbatim slice** of the brief.

### 4.6 A namespace collision that is already live

`/api/almanac/lmp-shape` (M2, 2026-05-29) is a **literal path inside the prefix
this lane just claimed**, and `lmp-shape` is not a series. It keeps working only
because Starlette matches routes in **registration order** and the literal path
is registered ~13,900 lines earlier than `/api/almanac/{series}`. Move these
routes above it — or move it below them — and it starts serving
`404 no such series: 'lmp-shape'`.

`test_the_legacy_lmp_shape_route_still_wins` asserts the ordering, so the break
lands in the suite instead of in someone's chart. It is deliberately **not**
special-cased in the validator: adding `lmp-shape` to the series vocabulary to
"protect" it would put a chart endpoint in the Almanac's cadence list, which is
worse than the collision.

---

## 5. The pre-migration state, and the one error that is absorbed

`publications` does not exist yet, so all three reads would raise psycopg's
`UndefinedTable`. **That one error class** is caught and served as the honest
empty state — empty shelf, a series page with `latest: null`, a 404 on an issue
— so Lane B can build against live URLs today. It is logged at WARNING naming
migration 154 on every request, because "the table isn't there" is a real
operational fact, not a normal empty page, and the log is where it goes: the
contract is pinned, so there is no field to smuggle it into.

**Every other database error is a 503.** "No issues" and "could not look" must
never render the same. `test_any_other_database_error_is_a_503_never_an_empty_page`
covers all three routes.

Once 154 is applied this branch of `_almanac_read` stops firing on its own. It is
worth deleting after the backfill lands — a permanent "the table might not exist"
handler is a permanent way to miss a dropped table.

---

## 6. EXPLAIN receipts — and what the rehearsal did and did not prove

Rehearsed on an **ephemeral Neon branch** (`almanac-154-rehearsal`,
`br-snowy-glade-ajtgjazc`, forked from the default branch, expiry set) with this
exact DDL applied and all 14 dailies loaded. **Zero bytes of the pantry were
touched.**

Table after backfill: 14 rows, **28,770 bytes** of `body`, 72 kB table / 104 kB
total, three pages.

```
GET /api/almanac                        (shelf, no ?series=)
  Sort  (cost=13.26..13.27 rows=1 width=273) (actual time=0.038..0.039 rows=14)
    Sort Key: issued_ts DESC NULLS LAST, issue_key DESC
    Sort Method: quicksort  Memory: 44kB     Buffers: shared hit=3
    ->  Seq Scan on publications  (actual time=0.016..0.023 rows=14)
          Filter: (status = 'published'::text)
  Planning Time: 0.066 ms   Execution Time: 0.060 ms

GET /api/almanac/daily                  (series page)
  Sort  (cost=8.18..8.18 rows=1 width=273) (actual time=0.045..0.046 rows=14)
    Sort Key: issued_ts DESC NULLS LAST, issue_key DESC     Buffers: shared hit=8
    ->  Index Scan using publications_series_issue_uniq  (actual rows=14)
          Index Cond: (series = 'daily'::text)
          Filter: (status = 'published'::text)
  Planning Time: 0.074 ms   Execution Time: 0.066 ms

GET /api/almanac/daily/2026-08-06       (one issue)
  Limit  (actual time=0.013..0.014 rows=1)     Buffers: shared hit=2
    ->  Index Scan using publications_series_issue_uniq  (actual rows=1)
          Index Cond: ((series = 'daily') AND (issue_key = '2026-08-06'))
  Planning Time: 0.060 ms   Execution Time: 0.026 ms
```

**The Seq Scan on the shelf is the right plan, not a finding** — the whole table
is three pages, so an index would cost a read to save nothing. The series page
and the single issue already ride `publications_series_issue_uniq` on its
leading column; the series page gets its index scan **for free**, with no index
declared for it. The one to add when this reaches the low thousands of rows is
written out in the migration header and deliberately not created today.

### What the rehearsal proved

- The DDL applies clean, and the self-record UPDATE flips ledger 154 from
  `reserved` to `committed` / `self-recorded`.
- **All five guards fire** — an off-vocabulary `series`, an off-vocabulary
  `status`, a jsonb *object* where an array belongs, a duplicate
  `(series, issue_key)`, and a NULL `verified` — and the table's hash is
  **unchanged** after all five are rejected.
- **The backfill is idempotent.** A second full pass left the table hash
  (`51f7fec31d999fb0d0cc49dde1fb8e76`) and every `publication_id` identical.
- **The transform is byte-exact.** `almanac.publication_from_daily_brief`'s
  output and the rehearsal's SQL transcription produce the same 2,103-byte body
  for the 2026-08-06 issue — md5 `9be8b81355865e9d85c8b3d7f3d7bdd1` on both
  sides. The brief carried in full in `tests/test_almanac.py` is likewise
  byte-identical to the live `content_md` (md5 `31a41cec043ccb5b99eaa07972b97444`).
- **It caught a real defect before it shipped.** The acceptance fixtures were
  pinned to millisecond `issued_ts` values read out of a JSON viewer; the pantry
  banks them at **microsecond** grain (`…06.526603`, not `…06.526`). All 14 are
  now pinned to the true values, with a note in the test file saying why.

### What it did NOT prove

`scripts/backfill_almanac_daily.py` **has never been executed against a real
database.** The rehearsal transcribed its transform into SQL because this
container has no outbound Postgres route (HTTPS-proxy only).

What IS covered by a run: the script's `run()` is driven in the suite against a
fake connection that behaves like the table, which pins the part the branch
rehearsal could not check cheaply — that the **receipt tells the truth**. First
pass reports 1 inserted; a second reports `unchanged` and leaves every column
identical; a hand-edited row reports `updated` and is overwritten;
`--skip-existing` leaves it alone; `--since` narrows the window; and every
column written is a column compared (a column written but not compared would
read as "unchanged" forever). Argument parsing, the dry-run default, and the
missing-table message are asserted too.

What is NOT covered by any run: the live `psycopg.connect`, the real
commit/rollback against Postgres, and the adapter reading real `joule_briefs`
rows over the wire. **Whoever applies migration 154 runs the script's own
dry-run first — that receipt is the one that counts.**

---

## 7. Depth report, measured 2026-08-07

`joule_briefs WHERE brief_type='weather'` — **14 rows**, `brief_key=''` on every
one, one row per date, all with a linked `joule_call_id`.

| | |
|---|---|
| span | 2026-07-20 … 2026-08-06 |
| calendar days in span | 18 |
| briefs | **14** |
| gaps | 07-24, 07-25, 07-26, 08-01 — never written |
| `headline` non-null | 14 / 14 |
| opens with its own headline | 14 / 14 |
| closes with the byline | 14 / 14 |
| `sources_used.as_of_date == brief_date` | 14 / 14 |
| `voice_version` | v1 ×2, v2 ×2, v3 ×1, **v4 ×9** |
| body words after both strips | 204 … 433 |
| `read_minutes` | **2** ×13, **3** ×1 (2026-07-20, 433 words) |

**Gaps are gaps.** Nothing pads them, and
`test_acceptance_the_banked_run_is_fourteen_dailies_with_four_gaps` asserts the
four missing dates are absent rather than present-and-empty.

The `daily` series will hold 14 issues. `weekly`, `monthly` and `article` exist
in the vocabulary and publish nothing — each serves
`{series, intro: null, latest: null, archive: []}`, a 200, not a 404.

---

## 8. What the architect does next

1. **Review** `migrations/154_publications.sql` — in particular the four extra
   NOT NULLs and the `body`-is-an-array CHECK (§2). Drop them if the spec's
   literal nullability is preferred; nothing in this repo changes.
2. **Apply** the file to the pantry, whole. It self-records ledger 154.
3. **Dry-run the backfill** — `python scripts/backfill_almanac_daily.py`. It
   writes nothing and prints the receipt. Expect `briefs_read: 14`,
   `inserted: 14`, `updated: 0`, `unchanged: 0`.
4. **Apply the backfill** — `--apply`. Re-run it bare afterwards to confirm
   `unchanged: 14`.
5. **Delete the `UndefinedTable` branch** in `main._almanac_read` (§5) once the
   table is live.
6. **File the two writer-side fixes** at the Weather Desk: a `dek` field, and a
   verifier-version stamp (§4.1, §4.2).
7. **Rule on the `series='daily'` collision** before Joule's editorial daily is
   backfilled (§3).

The ephemeral rehearsal branch `almanac-154-rehearsal`
(`br-snowy-glade-ajtgjazc`) carries an expiry and can be deleted at any time; it
holds nothing this document does not.
