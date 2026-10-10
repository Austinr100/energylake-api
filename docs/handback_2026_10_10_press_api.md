# Handback d091689: the press API serves the front page and its editions as the bank states them

**Lane:** d091689 · **Repo:** energylake-api · **Branch:** `claude/press-api-routes-o96yt5`, from main at `ce2522f`.
**Scope:** branch only. No PR, no merge, no deploy, no migration. No pantry change; no R2 read.
**Neon:** not touched. `press_editions` does not exist on Neon yet, so there was nothing to read. No statement of any kind was sent to Neon, and nothing was written there or to R2.
**Pantry, read only:** `energylake-pantry` main at `169e2c1` (PR #1103 merged `arch/press-edition-vectors` at `a2fcdc6`): `press/README.md`, `press/vectors/*.json` and `press/vectors/digests.json`. The lane first stopped because `press/` was not yet on pantry main (as the brief asks). It resumed once #1103 merged.
**Rulings:** D-09-25-184, D-09-25-185. Spec d091689.

---

## 0. In ten lines

1. **Three new routes**: `GET /api/press/front`, `/api/press/edition` and `/api/press/editions` (§1). No existing route changed; test A8 rebuilds main.py at `ce2522f` from the branch's main.py to prove it. Neither `joule_briefs` nor its routes is named or touched.
2. **The body is served as written.** `edition.body` is the row's jsonb as psycopg hands it over, and `press.edition_obj` never looks inside it. The banked daily comes back equal to pantry's vector key for key. Its canonical sha-256 equals the stored `sha256` and pantry's `digests.json` (A1).
3. **In force** is the highest revision with `withdrawn_at IS NULL`. Revision 1 supersedes revision 0 (A3). Withdrawing revision 1 brings revision 0 back (A4). Every revision withdrawn is a 404 that says how many were withdrawn, which was withdrawn last, when and why (A5).
4. **Absence is stated.** An empty bank is a 200 with `edition: null` and a sentence (A6). `/front` carries `freshness.stale_after_h` (26) and `age_h`, measured from the newest daily's *first* revision, so a correction does not reset the clock.
5. **House rules (the degree-day and CPC plumbing):**
   - every read is one statement through `_dd_timed_read` (2 s `SET LOCAL statement_timeout`, any failure a 503);
   - `/front`'s two reads run concurrently;
   - each route has a single-flight `_DDCache` (300 s, never stale);
   - each response carries the cache block and headers.
6. **Every read is an index read** on `press_editions_identity`, on a throwaway Postgres 16 holding 2,000 constructed rows (42 MB with TOAST). It reads at most 62 rows over at most 9 buffers, and execution is under 0.1 ms (§3). **The Neon plans are still owed**, because the table is not on Neon.
7. **Pantry's migration is not visible anywhere.** Pantry main, every remote branch name and the open PRs hold no `press_editions` migration and nothing named for d091688. So there is nothing to diff. §4 lists what the routes assume, for that lane to check against.
8. **jsonb does not keep the writer's object key order.** It keeps arrays, values and the canonical digest, and the README promises no key order. §5 still tells the dashboard not to rely on it.
9. **Tests:** 61 new; 54 are red on main with `press.py` added, and the rest are fixture and SQL tests (§6). The rehearsal broke 21 rules one at a time and every break turned the suite red; the tree was restored byte for byte.
10. **The whole suite:** 2,874 passed (main's 2,813 + 61), 2 failed. Both failures are red on main at `ce2522f` too, and neither is this lane's (§6).

---

## 1. The three routes, with a banked example of each

The banked bodies are produced from the 2,000-row constructed bank, which holds both pantry vectors as its newest daily and newest article. They are compact JSON with the cache block removed, at 2026-10-10 13:00Z: `docs/receipts/press-api-d091689/body_*.json`. Test V serves each one byte for byte. `bytes.psv` has the sizes: `/front` is 24,845 B raw and 9,351 B gzip; the article `/edition` is 11,858 B and 4,702 B; `/editions?kind=daily` is 2,730 B and 671 B.

The shared shapes:

```
EDITION = { kind, edition_date, slug, revision, schema, headline, sha256, submitted_at, writer,
            corrected, withdrawn, withdrawn_at, withdrawn_reason, in_force, in_force_revision,
            body }                                  // body: the row's el.edition.v1, untouched
HEADER  = { kind, edition_date, slug, headline, revision, submitted_at }
cache   = { state, built_at, age_seconds, ttl_seconds, build_seconds, refreshing }
          + headers Cache-Control: max-age=300, X-Cache: hit|miss, Age
```

- `corrected` is `revision > 0`.
- `withdrawn` is `withdrawn_at IS NOT NULL`.
- `in_force_revision` is the identity's revision in force, or null. `in_force` is whether this one is it.
- `body_text` is not served.

### 1.1 `GET /api/press/front`

```
{ edition: EDITION | null,
  absence: { reason: no_daily | no_daily_in_force, detail } | null,
  freshness: { edition_date, first_submitted_at, age_h, stale_after_h, stale, today_pacific,
               is_today, graded_at,
               newest_withdrawn: { edition_date, revision, withdrawn_at, withdrawn_reason } | null,
               rule },
  weekly: HEADER | null, weekly_absence, monthly: HEADER | null, monthly_absence,
  articles: [HEADER] (up to 5), articles_absence, articles_n,
  order_rule, body_rule, in_force_rule, sha256_rule, timestamps, cache }
```

Banked (`body_front.json`, body elided):

```json
{"edition":{"kind":"daily","edition_date":"2026-10-10","slug":"","revision":0,"schema":"el.edition.v1",
  "headline":"California's October heat wave ends, and the price of power falls by half in four days",
  "sha256":"a3d6c4dad724f4f3e5e325bf08f6ae45401dfffb3115b99c56ccd6569220effb",
  "submitted_at":"2026-10-10T09:52:00+00:00","writer":"architect","corrected":false,"withdrawn":false,
  "withdrawn_at":null,"withdrawn_reason":null,"in_force":true,"in_force_revision":0,
  "body":{"kind":"daily","lead":{…},"side":{…},"clock":[…],"feeds":[…],"digest":[…],"schema":"el.edition.v1",
          "window":{…},"changed":{…},"sources":"…","stories":[…],"masthead":{…},
          "disclaimer":"Nothing here is advice.","edition_no":283,"edition_date":"2026-10-10"}},
 "absence":null,
 "freshness":{"edition_date":"2026-10-10","first_submitted_at":"2026-10-10T09:52:00+00:00","age_h":3.13,
  "stale_after_h":26.0,"stale":false,"today_pacific":"2026-10-10","is_today":true,
  "graded_at":"2026-10-10T13:00:00+00:00","newest_withdrawn":null,"rule":"…"},
 "weekly":{"kind":"weekly","edition_date":"2026-10-05","slug":"","headline":"weekly 2026-10-05","revision":0,
  "submitted_at":"2026-10-05T14:00:00+00:00"},"weekly_absence":null,
 "monthly":{"kind":"monthly","edition_date":"2026-10-01",…},"monthly_absence":null,
 "articles":[{"kind":"article","edition_date":"2026-10-10","slug":"gas-carried-california-october-2026",
   "headline":"Gas carried California through its biggest October demand on record","revision":0,
   "submitted_at":"2026-10-10T12:30:00+00:00"}, …four constructed…],
 "articles_absence":null,"articles_n":5,"order_rule":"…","body_rule":"…","in_force_rule":"…",
 "sha256_rule":"…","timestamps":"…"}
```

- The weekly and monthly headers, and four of the five article headers, are constructed rows: pantry has no weekly or monthly vector yet.
- The `submitted_at` values and the `writer` of the two vector rows are the fixture's choices (`load_press_d091689`), not pantry's.

How `/front` reads:
- **The newest daily in force:** across dates, the newest daily that has an in-force revision. If today's daily is withdrawn entirely, `/front` serves yesterday's and names today's in `freshness.newest_withdrawn`. That field also names a withdrawn correction that sits above the revision in force.
- **The age:** `age_h` is `graded_at` minus the identity's first `submitted_at`. `stale` is `age_h > 26`. `is_today` compares `edition_date` with the Pacific date at `graded_at`.

### 1.2 `GET /api/press/edition?kind=&date=[&slug=][&revision=]`

```
{ request: { kind, date, slug, revision }, edition: EDITION,
  body_rule, in_force_rule, sha256_rule, timestamps, cache }
```

Banked (`body_edition_article.json`, kind=article, date=2026-10-10, slug=gas-carried-california-october-2026):

```json
{"request":{"kind":"article","date":"2026-10-10","slug":"gas-carried-california-october-2026","revision":null},
 "edition":{"kind":"article","edition_date":"2026-10-10","slug":"gas-carried-california-october-2026","revision":0,
  "schema":"el.edition.v1","headline":"Gas carried California through its biggest October demand on record",
  "sha256":"4c2ecada3c246babc8206d7a4bb782d67b5f07caf64fa691a8065e2e038349ce",
  "submitted_at":"2026-10-10T12:30:00+00:00","writer":"architect","corrected":false,"withdrawn":false,
  "withdrawn_at":null,"withdrawn_reason":null,"in_force":true,"in_force_revision":0,"body":{…the vector…}},
 "body_rule":"…","in_force_rule":"…","sha256_rule":"…","timestamps":"…"}
```

| case | answer |
|---|---|
| no `revision` | the revision in force |
| `revision=r`, held | that revision in any state. A withdrawn one carries `withdrawn: true`, `withdrawn_at` and `withdrawn_reason`. `in_force_revision` says which revision is in force. |
| every revision withdrawn | 404: `every revision of daily 2026-10-10 is withdrawn (2 of 2); the newest, revision 1, was withdrawn at …: <reason>` |
| no such identity | 404: `no edition daily 2026-10-09 in the bank` |
| no such revision | 404: `no revision 4 of daily 2026-10-10 in the bank; revision 0 is in force` |
| bad parameter | 400, `detail` starts with the parameter's name: `kind: …`, `date: …`, `slug: …`, `revision: …` |

Parameter rules:
- `kind` must be one of daily, weekly, monthly, article.
- `date` must be `YYYY-MM-DD` and a real calendar date.
- `slug` is required for an article: 1–200 characters, no whitespace or control characters. A slug on any other kind is refused.
- `revision` must be an integer from 0 to 32767.

### 1.3 `GET /api/press/editions?kind=[&n=]`

```
{ kind, n, count, editions: [HEADER], absence: { reason: no_<kind>, detail } | null,
  order_rule, in_force_rule, timestamps, cache }
```

Banked (`body_editions_daily_n14.json`): `{"kind":"daily","n":14,"count":14,"editions":[{"kind":"daily","edition_date":"2026-10-10","slug":"","headline":"California's October heat wave ends, …","revision":0,"submitted_at":"2026-10-10T09:52:00+00:00"},{"kind":"daily","edition_date":"2026-10-09",…},…],"absence":null,…}`

- One header per identity in force, newest first.
- An identity whose every revision is withdrawn is not listed.
- A live correction is listed under its revision number.
- `n` runs from 1 to 60 (default 14). 0, 61 or anything that isn't an integer is a 400 naming `n`, never trimmed.

---

## 2. The rules, and where each is held

| rule | held in | guarded by |
|---|---|---|
| body as written | `press.edition_obj` passes `r["body"]` through; nothing reads inside it | A1 (key for key, canonical sha), rehearsal 1–4 |
| beside it: kind … corrected | `EDITION_FIELDS`; `corrected = revision > 0` | A1, A3 |
| in force = highest revision with `withdrawn_at IS NULL` | every in-force statement filters on it and orders by `revision DESC` | A3, A4, A5, rehearsal 5–9 |
| withdrawn by number is served with its reason | `EDITION_AT_SQL` reads the revision in any state | A4 |
| all withdrawn is a 404 that says so | `press.edition_404` | A5 |
| absence stated | `build_front` and `build_editions` | A6, rehearsal 10–12 |
| `stale_after_h` and the age | `press.STALE_AFTER_H`, `freshness` | A3, A6 |
| parameters, refused and never trimmed | `press.parse_*` | A7, rehearsal 13–14 |
| one statement per read, 2 s timeout | `_dd_timed_read` (5 call sites) | C, A8, rehearsal 15 |
| concurrent reads | `asyncio.gather` in `_press_front_build` | C, rehearsal 16 |
| memo: 300 s, single-flight, never stale, keyed per request | `_press_*_cache` | C, rehearsal 17–18 |
| 503 on a down database or a timeout, never memoised | `_dd_timed_read` and `_press_serve` | C |
| index reads | `press_editions_identity` | A9, rehearsal 19 |
| read only, no `joule_briefs` | — | A8, rehearsal 20 |

---

## 3. Plans (`docs/receipts/press-api-d091689/plans.md`; regenerate with `receipts.py`)

Ephemeral PostgreSQL 16.15. The table is the spec's text verbatim, and its only indexes are the PK and `press_editions_identity`. It holds 2,000 rows:
- 1,461 daily identities, including 49 corrections, of which 17 are withdrawn;
- 209 weekly;
- 48 monthly;
- 233 articles.

Every body is a full vector re-dated, so the TOAST is real size: 432 kB of heap, 42 MB in all.

| statement | case | scans | rows read | buffers | exec ms | fetch ms |
|---|---|---|---:|---:|---:|---:|
| `FRONT_DAILY_SQL` | /front | Index Scan ×3 on `press_editions_identity` | 3 | 9 | 0.066 | 0.69 |
| `FRONT_HEADERS_SQL` | /front | Index Scan ×3 | 7 | 9 | 0.055 | 0.27 |
| `HEADERS_SQL` | daily, n 14 | Index Scan Backward | 14 | 3 | 0.043 | 0.31 |
| `HEADERS_SQL` | daily, n 60 | Index Scan Backward | 62 | 4 | 0.093 | 0.52 |
| `HEADERS_SQL` | article, n 60 | Index Scan Backward | 60 | 7 | 0.092 | 0.55 |
| `EDITION_SQL` | daily, today | Index Scan ×3 | 3 | 9 | 0.058 | 0.79 |
| `EDITION_SQL` | article by slug | Index Scan ×3 | 3 | 9 | 0.035 | 0.54 |
| `EDITION_SQL` | identity with a withdrawn rev 1 | Index Scan ×3 | 5 | 9 | 0.039 | 0.71 |
| `EDITION_AT_SQL` | rev 0 | Index Scan ×2 | 2 | 6 | 0.026 | 0.77 |

- No plan has a Seq Scan or a Sort.
- `exec` is EXPLAIN's execution time, which does not detoast the body. `fetch` is the client's execute plus fetch of the real statement, which does detoast it.

**`DISTINCT ON` and CLAUDE.md's d091551 rule.** `HEADERS_SQL` and the articles arm of `FRONT_HEADERS_SQL` use `DISTINCT ON (edition_date, slug) … ORDER BY edition_date DESC, slug DESC, revision DESC LIMIT n`.
- The identity index is read backwards in exactly that order, so `Unique` passes rows through as they arrive, and `Limit` stops the walk after n identities.
- Rows read are n plus any withdrawn or superseded revisions met on the way. That is 62 for daily at n = 60, and the A9 test caps it at 2n. The trap d091551 names is a `DISTINCT ON` with no usable order, which reads every row; this is not that.
- The rule's LATERAL-per-`(dataset, series)` form doesn't apply here, because the identities aren't known before the read.

**Owed: the Neon plans.** When pantry's migration lands on Neon:
1. Run `pinned_sql.json`'s statements under `EXPLAIN (ANALYZE, BUFFERS)` with the cases above.
2. Fill the table for Neon.
3. Confirm the index name: A9 asserts `press_editions_identity` by name.

---

## 4. What differs from pantry's migration

**No migration exists to diff against.** At the finish, pantry main (`169e2c1`) holds no `press_editions` migration. No remote branch name contains `press` other than the merged `arch/press-edition-vectors`, none contains `edition` or `d091688`, and the newest pantry PRs (#1096–#1103) hold none. The routes are built and tested on the spec's §7 text, held verbatim in `tests/fixtures/press_d091689/ddl_press_editions.sql`.

What the routes depend on, for lane d091688 to check its migration against. If any of these differ, the migration wins and these routes must follow:

1. **`press_editions_identity` is `UNIQUE (kind, edition_date, slug, revision)` in that column order.** Every read walks it, including backwards. Another order, or another name, changes the plans; A9 names it.
2. **`slug` is `''`, not NULL, for daily, weekly and monthly.** The routes key those kinds with `slug = ''`. A NULL slug would make every read of them miss, and would also make the unique constraint admit duplicates.
3. **`withdrawn_at IS NULL` means live.** A withdrawal is an UPDATE of `withdrawn_at` and `withdrawn_reason` on the row, never a delete.
4. **`headline`** is the column the headers serve. The fixtures put the lead story's headline there; the writer decides what it holds.
5. **`sha256`** is served as stored. The fixtures store pantry's canonical digest.
6. **`body` is jsonb.**
   - jsonb keeps the values and the order of arrays, but not the writer's key order inside objects, its whitespace, or duplicate keys.
   - Numbers pass through `numeric` to a Python float.
   - The canonical digest is unaffected (it sorts keys), and A1 recomputes it from the served body. Byte-for-byte "as written" would need `json` or `text`.

What pantry could add. None of these is required by the routes:

| addition | what it buys |
|---|---|
| `CHECK ((kind = 'article') = (slug <> ''))` | the slug rule the API enforces on its parameters would also hold on the rows |
| `CHECK ((withdrawn_at IS NULL) = (withdrawn_reason IS NULL))` | a withdrawal would always carry the reason the API serves beside it |
| a CHECK that `body->>'schema'`, `body->>'kind'`, `body->>'edition_date'` and `body->>'slug'` equal their columns | the API trusts the columns and never reads the body |
| a `datasets` row with a `stale_after_override` for the daily | `STALE_AFTER_H` could then be read from the bank rather than set in `press.py` |

---

## 5. What the dashboard lane must read

1. **Draw `edition.body` with the el.edition.v1 rules in pantry `press/README.md`.** Check `edition.schema` (or `body.schema`) is `el.edition.v1`; on another value, draw nothing and say so. The API adds nothing to the body.
2. **Object key order inside `body` is not the writer's.** `body_front.json` shows `kind, lead, side, clock, …`. Draw from keys, never from object order. Arrays (`digest`, `stories`, `blocks`, `series`, `values`) keep the writer's order.
3. **No daily.** `/front` with `edition: null` is a 200: print `absence.detail`. `absence.reason` is `no_daily` (none banked) or `no_daily_in_force` (every one withdrawn).
4. **Today's daily hasn't arrived** when `freshness.is_today` is false, or when `freshness.stale` is true (26 h since the newest daily's first submission). `freshness.newest_withdrawn` names a newer daily, or a correction, that was withdrawn.
5. **Corrections.** `corrected: true` means revision > 0. HEADERs carry `revision` but not `corrected`, as the brief lists them; `revision > 0` is the same test. A withdrawn revision is reachable only by `revision=`, and it comes with `withdrawn: true` and its reason.
6. **Order.** Lists are newest first by `edition_date`. Two articles of one date are ordered by **slug, descending**, not by `submitted_at`.
7. **404 and 400.** A 404 `detail` from `/edition` is a sentence the page can print. A 400 `detail` starts with the parameter's name.
8. **Caching.** Every body carries `cache`, and the headers carry `Cache-Control: max-age=300`, `X-Cache` and `Age`. A correction or withdrawal shows within 5 minutes.
9. **Encoding.** Non-ASCII characters in the vectors are served unescaped as UTF-8.
10. **The weekly and monthly `edition_date`.** Its meaning (week start? the 1st?) is the writer's, and nothing pins it yet. The fixtures used Mondays and the 1st.

---

## 6. Tests (`tests/test_press_d091689.py`, 61)

- **Fixtures** (`tests/fixtures/press_d091689`):
  - byte copies of pantry's vectors, `digests.json` and README, each sha-256 pinned in `manifest.json` against pantry `a2fcdc6`;
  - the spec's DDL.
- **Loader:** `tests/load_press_d091689.py` builds the table on `load_bank_d091673`'s throwaway Postgres. The vectors go in as their own file bytes, cast `::jsonb` by Postgres.

| group | what it holds |
|---|---|
| T0 ×3 | the vectors are pantry's (raw sha and canonical digest); a hand edit is red; the table is the spec's |
| A1 ×3 | the banked daily from `/front`: body equal key for key, sha256 as stored and equal to pantry's digest, canonical sha recomputed equal, non-ASCII unescaped, fields exactly `EDITION_FIELDS` |
| A2 ×3 | the banked article from `/edition` by slug, among `/front`'s articles; `/front`'s weekly, monthly and five articles equal a hand query on the 2,000 rows |
| A3 ×3 | revision 1 supersedes revision 0 and is `corrected`; the age keeps revision 0's submission; revision 0 by number is served, not in force |
| A4 ×2 | withdrawing revision 1 brings revision 0 back and names revision 1 in `newest_withdrawn`; revision 1 by number is served withdrawn with its reason |
| A5 ×4 | every revision withdrawn is a 404 that says so; an identity or revision never held is a different 404; `/front` with every daily withdrawn is a 200; a withdrawn identity is not listed, and `/front` falls back to the previous daily |
| A6 ×2 | an empty table is a 200 with `edition: null` and every absence; `stale_after_h`, `age_h`, `stale` and `is_today`, including past 26 h |
| A7 ×18 | 16 bad parameters, each a 400 naming its parameter with no read; n default 14, n = 60 served whole; newest first, one per identity, equal to a hand query |
| A8 ×4 | no press statement writes; three GET routes, all reads through `_dd_timed_read`; no `joule_briefs`; main.py at `ce2522f` is the branch's main.py minus the press section and its 3 index lines |
| A9 ×3 | every statement is an Index Scan on `press_editions_identity` (no Seq Scan or Sort) at 2,000 rows; rows read are bounded; `plans.md` and `pinned_sql.json` are this code's |
| C ×13 | `SET LOCAL statement_timeout = '2s'` before every read, one statement per read; `/front`'s two reads in flight together; memo 300 s, never stale, keyed per request; 20 concurrent cold requests make one build; DB down is a 503; a timeout is a 503 and not memoised; a 404 is not memoised |
| V ×3 | the three banked bodies are served byte for byte |

Receipts in `docs/receipts/press-api-d091689/`:

| file | what it shows |
|---|---|
| `red_on_main.txt` | **Run 1** (main + tests, no `press.py`): a collection error. **Run 2** (+ `press.py`, no routes): 54 failed, 7 passed. The 7 are T0 ×3, the A8 statement test and A9 ×3, which test `press.py` and the fixtures, not a route. |
| `reds.txt` | `tests/rehearse_press_d091689.py`: **21 of 21 breaks red, tree restored byte for byte** (`9ff5dd5a…` → `9ff5dd5a…`). The clean tree is green before and after. |
| `green.txt` | the whole suite on the branch (below) |

**The whole suite** (`green.txt`): **2,874 passed, 2 failed** (main's 2,813 passing tests and this lane's 61). On main at `ce2522f` the suite is **2,813 passed, 2 failed**. The 2 are the same on both, are not this lane's, and are left as they are:
- `test_chart_brief.py::test_chart_brief_maps_contract` depends on the wall clock: the publication status reads `pending` before the day's 12:00Z deadline and `overdue` after it.
- `test_tropics_d091673.py::test_R_no_existing_route_or_memo_changed` asserts that main has no `/api/weather/tropics`, which stopped holding when d091673 merged. A8 here pins its base commit (`ce2522f`) rather than the merge-base, so it survives its own merge.

---

## 7. What this brief got wrong

1. **"House rules as the degree-day and tropics routes follow them: … concurrent reads, the 2 s statement timeout."** The two don't agree:
   - the tropics routes run their reads **one after another in one transaction at 5 s** (`TROPICS_STATEMENT_TIMEOUT`);
   - the degree-day and CPC routes run one statement per read through `_dd_timed_read`, at 2 s, concurrently.

   The press routes follow the latter, which is what the brief's words describe.
2. **"Needs on pantry main first."** It wasn't there when the lane fired. The lane stopped, as told, and resumed after #1103 merged.
3. **"If its branch differs when you finish, the migration wins."** There is no d091688 branch or migration visible on the pantry remote, so nothing could be diffed. §4 lists what the routes assume instead.
4. **"`edition.body` is the row's `body` verbatim."** For a `jsonb` column that holds as a JSON value, not as bytes: key order, whitespace and duplicate keys are jsonb's. If verbatim bytes are meant, the column must be `json` or `text` (§4.6).
5. **"The body carries `stale_after_h` and the age of the newest daily."** The brief gives no value and no source, and the bank has no freshness row for press. `STALE_AFTER_H = 26` is a constant in `press.py`. The age is measured from the identity's first submission, because measured from the revision in force a correction would reset it (A3 pins this).
6. **"A slug on a daily" is a 400.** The brief says nothing about weekly and monthly. The README says only an article carries a slug, so a slug on any non-article is a 400, and an article without one is too.
7. **"Every read is an index read"** sits beside CLAUDE.md's d091551 rule against `DISTINCT ON`. The list reads use `DISTINCT ON` in the index's own order under a `LIMIT`, which stops early. The rule's per-key LATERAL form needs keys known in advance, which identities are not (§3).
8. **"The whole suite green."** Two tests are red on main already (§6), and neither belongs to this lane.

---

## 8. Not done, or for later

- **The Neon plans**, once the table exists there (§3).
- **No ETag.** `sha256` would make a natural strong ETag for `/edition`; the brief didn't ask for one.
- **`body_text` is not served.** The brief lists no route that needs it; search would.
- **Pantry has no weekly or monthly vector yet.** The fixtures construct them from the daily vector re-dated. A banked weekly and monthly would pin their shape the way the daily and article are pinned.
