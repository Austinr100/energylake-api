# Handback d091757: the API serves `/outlook` and `toa` under `/products`, on banked vectors

**Lane:** d091757 (re-fired) · **Repo:** energylake-api · **Branch:** `claude/tropics-outlook-toa-api-t4gajq`, **cut fresh from main at `de0dee8`** (#117, d091746 merged).
**Ran:** 2026-10-16, about 02:35Z–03:15Z (container clock). **Environment:** EL 6.
**Rulings held:** D-09-25-234 · D-09-25-236 · D-09-05-T · D-09-25-231 · D-09-25-75 / D-09-25-76 (CLAUDE.md) · CLAUDE.md d091551.

**Re-banked after D-09-25-236's withdrawal, 2026-10-16 ~13:05–13:30Z: see §8.** Where §8 and §§0–7 differ (the bank's counts and shas, the label block, the tests' counts, the rehearsal), §8 holds.

**Continued or fresh: fresh.** The earlier branch `claude/tropics-outlook-toa-api-99d0fq` (`682ea04`) held at STOP-D 91 with one file, its held handback, and no code. Its recon (§2 there: the stamps per product, the text meta, the TOA refs) is folded in here and re-read live; nothing of it is carried as code.

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- **No Neon write.** Five connector calls, each one transaction opened by `SET TRANSACTION READ ONLY`, with `transaction_read_only = on` read back. Every statement was a SELECT or an `EXPLAIN (ANALYZE, BUFFERS)` of one.
- **No R2 read or write.**
- **Network:** GitHub (read only): energylake-pantry `main` at `37455cd` and its branch `claude/toa-label-description-parse-cu0m63` at `cc6cbff` (d091756 follow-up 2); energylake-architect `main` at `8e0a2ba`.

---

## 0. In ten lines

1. **`GET /api/weather/tropics/outlook?basin=al|ep|cp[&stamp=YYYYMMDDHHMM]`** serves the 7-day outlook: the graphics (`two_areas`, `two_points`) at their 12-digit stamp, newest by default, and the basin's text paired by its **own** stamp.
2. **The stamp is the issuance (D-09-25-234).** `?stamp=` refuses an hour (`2026101405` → 400, "an hour, not an issuance") and refuses r<n> (`202610140517r1` → 400, "a correction, not an issuance"). The view is read at the exact stamp. No read truncates a stamp to its hour.
3. **The pairing:** each basin's text is the one with the newest stamp **at or before** the graphics' stamp. `basin=cp` is served the East Pacific text, whose own heading states its reach; the bank holds no CPHC text.
4. **`toa_earliest` / `toa_mostlikely` are on `/products`,** in their own `toa` block. They are read exactly as the cone is (314's view, `revision`/`corrected`/`original`, `not_issued` on NHC's refs), plus the row's **label table, verbatim**: `label_rule`, `timezone`, `counts`, `null`, `withdrawal`. No label is parsed or inferred.
5. **What `tropical_display_in_force` held at run time: the first publish.** At the bank read (02:49:59Z), 0 of 112 TOA rows carried D-09-25-236's `label_rule` and 0 a withdrawal note. At 03:09:14Z, 0 of 116 (4 published since on the old parser), with 108 in force. Every banked label table says `parsed: 0`, and that is what is served.
6. **The bank:** 128 TWO rows, 112 TOA rows and 114 NHC `toa_*_kmz` rows, read verbatim off Neon in one read-only transaction and sha-checked on every load. The test cluster loads 314, then 316 verbatim (d091746's loader), then these rows. **No row is constructed.**
7. **Plans: no seq scan.** The stamp read is one `Index Scan Backward using tfv_identity`: 3 buffers, < 0.1 ms. The view reads at a stamp walk the product's rows by `(product, storm_id '')` with the stamp as a Filter, as d091746 measured for WSP (§4).
8. **Tests:** new `tests/test_tropics_outlook_toa_d091757.py`, 257 tests.
   - **Red on main:** 245 of 257; the 12 green check only the bank and the receipts.
   - **Green on the branch:** 257 of 257, and 394 of 394 across the three Tropics index modules.
9. **Rehearsal: 13 of 13 as expected.** The stamp taken for an hour (4 ways) and r<n> taken for an issuance (3 ways) are each red by name. Two diagnostics are green, as no banked vector can see them (§5.2).
10. **Whole suite:** main 3,100 passed / 2 failed / 2 skipped; branch 3,359 passed / 2 failed. The +259 is this module's 257 tests plus main's 2 skips, which run here. The 2 failures are asset H8 on both, red on main before this lane.

---

## 1. What the brief's inputs were, and where they were found

| input | found | used |
|---|---|---|
| d091746's handback | `docs/handback_2026_10_15_tropics_api_revision_read_d091746.md`, main `de0dee8` | read in full; its §7.12 (Python's `re` vs Postgres on 316's base) is held in the tests' `base()` |
| the spec `cc_spec_2026_10_15_tropics_api_outlook_and_toa_d091757.md` | **not found**: no such path on pantry `main` (`37455cd`) or in any pantry ref whose name mentions spec/arch/outlook/toa; not in energylake-architect `main` (`8e0a2ba`, whose newest specs are d091718–d091728) | built from the paste's lane text, which states the routes, the parameters, the vectors, the receipts and the rehearsal |
| D-09-25-234, D-09-25-236 | not in any readable repo as text | held as the paste states them. 234 also as pantry 316's header and `tropical/publish.py` state it; 236 as follow-up 2's commit and `scripts/withdraw_toa_d091756.py` state it |
| 316's DDL in the test cluster after 314 | already loaded by d091746's loader (`ddl_316.sql`, sha-pinned) | unchanged; this lane's rows load after it, so every TWO/TOA row meets 316's CHECK |

---

## 2. The routes

### 2.1 `GET /api/weather/tropics/outlook?basin=&stamp=`

```
{ basin, stamp, stamp_requested, issued_ts, revision, corrected,
  graphics: { stamp, objects: [ { product: two_areas|two_points, url, sha256, bytes, stamp,
              issued_utc, source_layer, n_features, n_records, dbf_fields, geometry_types,
              vertices, source_r2_key, source_sha256, revision, corrected, original } ],
              absent: [ { product, reason, detail } ] },
  text: { product: two_text_at|two_text_ep, text_basin, url, sha256, bytes, basin_heading,
          stamp, wmo_heading, awips_id, bbb, title, forecaster, issued_local, issued_utc,
          transmitted_utc, formation_not_expected, n_paragraphs, n_disturbances,
          disturbances: [ { number, title, prob_2day, prob_7day, risk_2day, risk_7day } ],
          source_r2_key, source_sha256, revision, corrected, original, absence },
  absence, stamp_rule, pairing_rule, basin_rule, revision_rule, index_rule, reasons,
  timestamps, not_served, nhc_terms, cache }
```

**Parameters:**
- **`basin` is required**, `al | ep | cp`: `/ground`'s names and its parser. `al` → `two_text_at`; `ep`, `cp` → `two_text_ep`.
- **`stamp`** is the graphics' 12-digit stamp. Without it, the newest published.

**Each object prints its own stamp.** The text's meta is the publisher's verbatim (`META_TWO_TEXT`).
- **`basin_heading`** is the text's `meta.basin`: its heading line, which is not a basin code.
- **`disturbances`** are passed through as banked (D-09-25-76). No area number, headline or wording is derived; `number` is null where NHC gives none.

**Absence:** `not_published` only.
- A graphics product missing at the stamp goes in `graphics.absent`.
- A text with no stamp at or before the graphics' carries `text.absence`.
- No graphics published at all: top-level `absence`.

**Status codes:**
- A named stamp with no published graphics → **404**. That includes a text's own stamp: `202610152319` is `two_text_at`'s, and the stamp names the gtwo zip.
- A bad `basin` or `stamp` → 400. DB down or timed out → 503.
- Memo: 300 s per `(basin, stamp)`, single-flight, never served stale. `SET LOCAL statement_timeout` comes first (D-09-25-75).

**The outlook reads no NHC row,** so it states no `source_in_force`. The texts' NHC keys are WMO keys (`ABNT20_KNHC_152319`), not stamps.

### 2.2 `toa` on `/products`

`toa: { objects: [ { …the cone's fields…, advisory_in_object, advisory_check, labels: { label_rule, timezone, counts, null, withdrawal } } ], absent: [ … ] }`, plus `toa_rule`.

- **Its own block, not in `objects`.** `objects`/`absent` still name cone, ww and surge_ww only, so the d091741/d091746 bodies are unchanged except for the two added keys. `revision`/`corrected` at the top level count the TOA objects too.
- **Read with the cone:** one `DISPLAY_FILES_SQL` and one `FILES_SQL` per advisory, the product lists extended by `TOA_PRODUCTS = (("toa_earliest", "toa_earliest_kmz"), ("toa_mostlikely", "toa_mostlikely_kmz"))`. That gives `not_issued` on NHC's own `refs` and `source_in_force` from 294.
- **`labels`** is the row's meta under its own names: `meta.label_rule`, `meta.timezone`, `meta.labels` (as `counts`), `meta.labels_null` (as `null`), `meta.withdrawal`. Whatever row is in force when the withdrawal runs is what is served; nothing here needs to change for it.

The 054 body's `toa_mostlikely` (served from banked row 8206, `054r1`):

```json
{"product":"toa_mostlikely","url":"https://display.energylake.io/tropics/ep182026/adv054r1/toa_mostlikely.geojson",
 "sha256":"f2cbe7f330387b46e4de3595d967556251b5d89d72b9ec6bd8b1061f9c14ed96","bytes":30421,
 "source_r2_key":"tropical/nhc/2026/ep182026/adv054/toa_mostlikely_kmz__6812f50d.kmz", "…":"…",
 "revision":1,"corrected":true,
 "original":{"url":"https://display.energylake.io/tropics/ep182026/adv054/toa_mostlikely.geojson",
             "sha256":"f2cbe7f3…","bytes":30421,"absence":null},
 "source_in_force":true,"advisory_in_object":null,
 "advisory_check":"not carried in the KML: checked against the NHC file name in source_url",
 "labels":{"label_rule":null,"timezone":null,"counts":{"n":13,"null":13,"parsed":0},
           "null":[{"why":"no label","label":null}],"withdrawal":null}}
```

---

## 3. The SQL (`tropics_index.py`)

### `OUTLOOK_STAMP_SQL` (new)

```sql
SELECT substring(a.vintage_key FROM '^[0-9]{12}') AS stamp
  FROM tropical_file_vintage AS a
 WHERE a.source = 'display' AND a.product = %(product)s AND a.storm_id = ''
   AND a.status = 'banked' AND a.vintage_key < %(key_below)s
 ORDER BY a.vintage_key DESC
 LIMIT 1
```

- **One product per statement**, and a key, not a row: CLAUDE.md d091551's newest-row shape (`ORDER BY … DESC LIMIT 1`, never the view's `DISTINCT ON`, which sorts every issuance to keep one).
- **`key_below`** is `'<stamp>s'` from `outlook_stamp_params(name, at_or_before)`; the newest of all uses `'999999999999s'`.
  - Under C collation (Neon `datcollate C.UTF-8`, and the test cluster), every key whose base is at or before the stamp sorts below it. That includes `<stamp>r<n>`, since `'r' < 's'`. Every later stamp sorts above. Test W asserts the ordering.
- **The base is cut off the key,** so a correction's key is never taken for a stamp.
- **The row served is then read through the view** at that exact stamp with d091746's `DISPLAY_FILES_SQL`, unchanged, at `storm_id ''`. The original's probe comes with it.

### The `/outlook` reads, in one transaction

1. The statement timeout.
2. `OUTLOOK_STAMP_SQL(two_areas)`, only when no stamp is named.
3. `DISPLAY_FILES_SQL(two_areas, two_points; '', stamp)`.
4. `OUTLOOK_STAMP_SQL(two_text_<b>, ≤ stamp)`.
5. `DISPLAY_FILES_SQL(two_text_<b>; '', text stamp)`.

### Two existing assertions widened (both stated in the tests)

- d091741's `test_C_every_ledger_read_is_of_the_view_in_force` now names two bare reads, `["DISPLAY_FILES_SQL", "OUTLOOK_STAMP_SQL"]`, and pins the latter's base cut.
- d091746's `test_R1_…` sets aside `toa` and `toa_rule` on its two `/products` bodies, and asserts that those are the only new keys. This lane's R1 pins the full bodies.

---

## 4. The bank and the plans

**Script:** `tests/fixtures/tropics_d091741/bank_outlook_toa_from_neon.py`. Same shape as d091746's: the statements verbatim, `--sql` prints them, and nothing is written unless Neon's sha and row count match. Manifest key `"d091757"`.

**Read 2026-10-16T02:49:59.014Z:**

| kind | rows | sha256 (Neon's) | what |
|---|---:|---|---|
| `two` | 128 | `37dbc1fc…f34ec4` | every `display:two_*` row: areas 37, points 37, text_at 27, text_ep 27; all 12-digit keys, no r<n>, storm `''` |
| `toa` | 112 | `0889ebfd…316c23a` | every `display:toa_*` row: 56 per kind = 52 advisories + 4 `r1` (ep182026 054/056/057, ep212026 001) |
| `nhc_toa` | 114 | `b63c7b45…2702ef` | NHC's `toa_*_kmz` rows of al092026, ep182026, ep202026, ep212026 |

**At the read:**
- 8,365 ledger rows, 719 display rows.
- TOA: `label_rule` 0, `withdrawal` 0.
- 8 TWO/TOA `r<n>`, all TOA.
- View md5 `977c97d6…` (316's); max migration 316.

**Plans:** `docs/receipts/tropics-api-outlook-toa-d091757/neon_plans.txt`, verbatim, 2026-10-16T02:59:39.679Z, 8,410 ledger rows / 730 display rows. Statements: `explains.json` (psycopg's mogrify of the routes' own statements: `explains_sql.py`).

| statement | reached by | rows walked → kept | buffers | exec |
|---|---|---:|---:|---:|
| OUTLOOK_STAMP_SQL two_areas newest | **Index Scan Backward using tfv_identity**, all four columns incl. `vintage_key < '999999999999s'` | 1 | 3 hit | 0.081 ms |
| OUTLOOK_STAMP_SQL two_text_at ≤ 202610152347 | the same | 1 | 3 hit | 0.034 ms |
| OUTLOOK_STAMP_SQL two_text_ep ≤ 202610140517 | the same | 1 | 3 hit | 0.031 ms |
| DISPLAY_FILES_SQL graphics 202610152347 | Index Scan tfv_identity `(display, product = ANY, '')`; the stamp a **Filter** | 74 → 2 (72 removed) | 32 hit, 2 read | 1.524 ms |
| DISPLAY_FILES_SQL text_at 202610152319 | the same | 27 → 1 (26 removed) | 11 hit | 0.140 ms |
| DISPLAY_FILES_SQL products+toa ep182026 054 | Index Scan tfv_display_immutable `(product = ANY five, storm)`; the advisory a Filter | 69 → 8 → 4 in force | 29 hit, 4 read | 2.241 ms |
| FILES_SQL products+toa ep182026 054 | Index Scan tfv_identity, all four columns | 8 → 4 | 17 hit | 0.099 ms |

- **No `Seq Scan` anywhere.**
- **The originals' probe** is `tfv_display_immutable` by exact key, and never runs for a revision-0 row (Join Filter).
- **The cost that grows is d091746's §4 again.**
  - Every TWO view read walks its product's display rows to keep one: 37 issuances per product today, about 8 more a day.
  - The expression index d091746 proposed (`tfv_display_base`, with 316's pattern) makes the stamp an Index Cond for TWO as it would for WSP. **It is still owed to pantry. It is not added or tested here.**

---

## 5. Tests

### 5.1 The cells: `tests/test_tropics_outlook_toa_d091757.py` (257)

| cell | asserts | tests |
|---|---|---:|
| B | the read stamp and counts; the rows' shape (12-digit keys, no TWO r<n>, `meta.stamp` = key, the 8 TOA r1, no `label_rule`/withdrawal, 0 labels parsed); the cluster holds them all under 316, its view md5, and reads each stamp whole | 3 |
| S | `test_S_an_hour_is_not_a_stamp` ×2; `test_S_r_n_is_not_an_issuance` ×2 (also pins the base cut); 7 malformed stamps → 400; `test_S_two_outlooks_of_one_hour_are_two_issuances` (10-14 05:17/05:22, 10-15 23:11/23:47); the default = the newest = its named body; **every one of the 37 banked stamps served at itself**; 4 stamps with no graphics → 404 | 54 |
| P | `test_P_the_text_is_paired_by_its_own_stamp`: **37 stamps × 3 basins**, each against the bank's own answer; the pairs where stamp and hour disagree; cp = ep's text, said so | 113 |
| O | the text = the publisher's meta verbatim (the one banked disturbance as is); the graphics' meta; the body's keys and rules; basin required; the absences off the builder with empty reads | 5 |
| T | the pairs; **every one of the 52 banked TOA identities** served as its copy in force with its label table verbatim; the 4 corrections as r1 with the original; 054's corrected NHC file in force; `not_issued` on refs (054A links 054's; Isaias 016 links none); `objects`/`absent` untouched; the label block reads only the row's label keys; no advisory, no `toa` | 63 |
| W | the outlook's statements and parameters as sent; one product per stamp read, a key, LIMIT 1, no DISTINCT; the C-collation bound; memo hit; `/products` asks the TOA pairs; 294's view carries no display product | 5 |
| X1 | the receipts are the module's statements; no seq scan; the stamp read is one backward tfv_identity probe (3 buffers, < 1 ms); the view reads' index range, filter counts and originals' probe | 8 |
| R1 | the five handed-back bodies are what the routes serve, and their rows are the bank's; the bodies' sha-256s | 6 |

**Not reachable on banked vectors, so read off the builder rather than a constructed row:**
- **A text with no stamp at or before the graphics':** the earliest graphics stamp, 10-09 11:43Z, already has both texts (11:03Z, 11:42Z).
- **Points missing beside areas.**
- **No graphics at all.**

`test_O_the_absences_state_their_reason` calls `build_outlook` with the empty reads the routes would return.

### 5.2 Rehearsal (`tests/rehearse_tropics_outlook_toa_d091757.py` → `reds.txt`)

Clean before and after: 394 passed. Tree sha256 `db81dacb…` → `db81dacb…`, restored byte for byte.

| # | break | result |
|---|---|---|
| 1 | **hour:** `?stamp=` takes `YYYYMMDDHH` | RED: `test_S_an_hour_is_not_a_stamp` |
| 2 | **hour:** the text bound cut to the graphics' hour | RED: `test_P_the_text_is_paired_by_its_own_stamp` (19) |
| 3 | **hour:** the view read at `left(…, 10)` | RED: `test_S_two_outlooks_of_one_hour_are_two_issuances` |
| 4 | **hour:** the text paired by the top of the hour (the hour NHC writes) | RED: `test_P_the_text_is_paired_by_its_own_stamp` (92) |
| 5 | **r<n>:** `?stamp=` takes `<stamp>r<n>` | RED: `test_S_r_n_is_not_an_issuance` |
| 6 | **r<n>:** the display read by `vintage_key` (`054r1` taken for its own advisory) | RED: `test_T_each_correction_is_served_as_its_r1` |
| 7 | **r<n>:** the stamp read returns the key, not its base | RED: `test_S_r_n_is_not_an_issuance` (the text pin) |
| 8 | *diagnostic:* 7 with the pin set aside | **GREEN, as expected: no banked TWO row is corrected, so no vector can see it** |
| 9 | a `label_rule` filled in where the row has none | RED: T (52) |
| 10 | a `timezone` filled in | RED: T (53) |
| 11 | `/products` reads no TOA | RED: T (57) |
| 12 | *diagnostic:* TOA left out of the top-level revision | **GREEN, as expected:** every corrected TOA identity's cone is corrected too |
| 13 | cp served the Atlantic text | RED: P (38) |

### 5.3 Red on main, green on the branch

`docs/receipts/tropics-api-outlook-toa-d091757/red_on_main.txt`: the three Tropics index modules, with this branch's tests and receipts, against main's `tropics_index.py` and `main.py` (worktree at `de0dee8`).
- **On main: 248 failed, 146 passed.** This module: 245 red, 12 green (B ×3, P's bank-only pairs, X1's plan facts ×7, R1's hashes).
- The other 3 red are this lane's two widened assertions (§3).
- **Branch:** 394 of 394.

### 5.4 The whole suite

`python3 -B -m pytest -q -p no:cacheprovider`, ~03:05Z.

| | passed | failed | skipped | total |
|---|---:|---:|---:|---:|
| main `de0dee8` (worktree, main's own tests) | 3,100 | 2 | 2 | 3,104 |
| this branch | **3,359** | 2 | 0 | 3,361 |

- **+257:** this module.
- **The 2 failures, identical on both:** asset H8 (`test_asset_runs_d091667.py::test_H8_…[asset_wind_57514.json]` and `[body_wind_ciso_n28.json]`). Red on main before; not touched.
- **The 2 skips on main** are `test_mjo_status.py` with no pantry checkout beside the worktree: environment, as d091746 found.

---

## 6. What to rule, and what this brief got wrong

1. **The spec file is in no repo this lane can read** (§1). The build follows the paste. Anything the spec says beyond it (field names, a staleness threshold) is not built. The earlier brief's 12 h `not_banked` threshold is not built: no ruling in hand names it, and the outlook reads no NHC row to say "not banked" with.
2. **The pairing rule is this lane's reading of "paired by their own stamps":** the basin's newest text stamp at or before the graphics' stamp. It needs the architect's word. The banked vectors where it matters:
   - ep at 10-14 05:17Z → the 10-13 23:16Z text, since ep's 05:22Z came after the zip;
   - al at 10-15 23:11Z → 17:51Z, since al's 23:19Z came after.
3. **`?stamp=` names the graphics only.** A text's own stamp is a 404 that says so. The alternative, accepting a text stamp and serving the graphics around it, was not built.
4. **`toa` is its own block** so the existing `objects`/`absent` keep their meaning. If the page wants one list, it is a two-line change; the d091741/d091746 assertions that pin `objects` would then move.
5. **The labels the page receives today are the first publish's: 0 parsed.** The withdrawal (`scripts/withdraw_toa_d091756.py --write`, the captain's word) had not run at 03:09Z.
   - Its selector is "no `label_rule`", so it would also take the 4 TOA rows published since this bank read.
   - When it runs, the API serves the new rows as they are, with no change here. This bank's TOA rows then go stale, and a re-bank (`bank_outlook_toa_from_neon.py`, one command) re-pins B and T.
6. **The TWO view read is unbounded in time,** as WSP's is (d091746 §4). The same expression index answers both, and it is still owed to pantry.
7. **Railway after deploy:** `/products?storm_id=ep182026&advisory=054` should serve `products_ep182026_054.json` with only `cache` differing, `toa` included.
   - `/outlook?basin=al` will serve whatever stamp is newest then, not the body banked here.
   - `/outlook?basin=ep&stamp=202610140522` is stable and should match its body.

## 7. Files

**Changed:**
- `tropics_index.py`: the docstring, `STAMP`/`parse_stamp`, `TOA_PRODUCTS`, `OUTLOOK_*`, `META_TWO_*`, `OUTLOOK_STAMP_SQL`, `outlook_stamp_params`, `_toa_object`, `build_products`' `toa` block and `TOA_RULE`, `build_outlook` and its rules.
- `main.py`: the tropics index section only, adding `_tropics_outlook_cache`, `_tropics_outlook_build`, the `/outlook` route, `/products` reading the TOA pairs, and the docstrings.
- `tests/load_bank_d091741.py`: loads `two`, `toa`, `nhc_toa` after d091746's rows.
- `tests/test_tropics_index_d091741.py` (test_C) and `tests/test_tropics_revision_read_d091746.py` (R1): §3.
- `tests/fixtures/tropics_d091741/manifest.json`: `"d091757"`.

**New:**
- `tests/test_tropics_outlook_toa_d091757.py`
- `tests/rehearse_tropics_outlook_toa_d091757.py`
- `tests/fixtures/tropics_d091741/bank_outlook_toa_from_neon.py` and `bank/{two,toa,nhc_toa}.jsonl.gz`
- `docs/receipts/tropics-api-outlook-toa-d091757/`: `explains_sql.py`, `explains.json`, `neon_plans.txt`, `write_bodies.py`, `bodies/` (5 bodies + `served_from.json`), `bodies_sha256.json`, `reds.txt`, `red_on_main.txt`
- this handback

---

## 8. Re-bank after the withdrawal (D-09-25-242 §1)

**Branch:** `claude/toa-rebank-withdrawal-o9ntwh`, fast-forwarded to `bbf92f2` (this lane's earlier head on `claude/tropics-outlook-toa-api-t4gajq`) and committed on top. The earlier pane was gone; the session is pinned to this branch name, so the work continues the same history under a new name. `claude/tropics-outlook-toa-api-t4gajq` is untouched.
**Ran:** 2026-10-16, about 13:05Z–13:30Z (container clock).
**Scope kept:** no Neon write (two connector calls, each one transaction opened by `SET TRANSACTION READ ONLY`, with `transaction_read_only = on` read back on every statement); no R2 read or write; no deploy; no PR. GitHub read: energylake-pantry `main` at `96d1806` (13:06:08Z).

### 8.1 The read: 2026-10-16T13:09:21.898Z

One transaction, `bank_outlook_toa_from_neon.py`'s statements. Every body was checked against Neon's sha-256 and row count before it was written.

| kind | rows | sha256 (Neon's = the file's) | vs the 02:49:59Z bank |
|---|---:|---|---|
| `two` | **136** | `4fdc6d85addb47ffd9cdef7336d1f95155230e3a57f52c67d55e47307eac9fec` | all 128 earlier rows present, each row equal in full; 8 new (ids 8470–8747) |
| `toa` | **120** | `c036fbc66b1b7442df8d3f55026b54203b3162a605ba1039f0ef882b2c9e0045` | **0 of 112 earlier rows exist**; 120 new, ids 8603–8722, all `fetch_ts` 10:57:17.013635Z |
| `nhc_toa` | **122** | `9f6ae9b800f587a3537794bcaf26c6d9f9e9b4f4832c7fa47e66f24897b2eea0` | all 114 earlier rows present, each equal in full; 8 new (max id 8569) |

"Equal" here is row-for-row equality of `row_to_json` against the earlier bank, by `vintage_id`. That is stronger than the architect's `md5(string_agg(vintage_id:sha256))` digest, and agrees with it.

**The ledger at the read:**
- 8,596 rows, 767 display rows.
- TOA: `label_rule` **0**; `withdrawal` **120**; first parser's labels (no `labels.by_source`, the withdrawal's own selector) **0**. The count statement gained `n_toa_first_parser` for this.
- 8 TWO/TOA `r<n>`, all TOA, the same 4 corrections × 2 kinds.
- **Max migration 317, not 316.** 317 is `317_cwms_willamette_irregular.sql` (d091761, applied 09:24:19Z), two CWMS dataset rows. The view md5 is still 316's, `977c97d6…`. B now pins 317 and says why.

**TWO, the 8 new rows:**
- `two_areas`/`two_points`: 10-16 05:25Z and 11:36Z.
- `two_text_at`: 05:16Z and 11:36Z.
- `two_text_ep`: 05:15Z and 11:33Z.
- Stamps per product are now 39/39/29/29. The newest outlook is **202610161136**, and al's text pairs to its own 11:36Z.

**TOA, the 120 rows:**
- 60 per kind; **56 identities**. The four new ones are Simon 035/036 and Trudy 011/012.
- **Every row's `withdrawal`** names `D-09-25-236` and the note "withdrawn and republished under D-09-25-236, no reader existed". Its `withdrawn_vintage_id`s are exactly 8139–8250 (the 112 the first bank held), plus 8440–8443 (02:54:12Z) and 8578–8581 (08:58:48Z).
  - Those last eight are rows the scheduled publish wrote on the first parser after the first bank read. The withdrawal took them too. Each replaced id is below its replacement's.
- **Labels:** 2,792 in all, 2,672 parse. The 120 nulls are one per object, each `{why: not_a_time, label: "Wind Speed Probability 5% contour"}`.
- **The four contour-only objects** have `n 1`, `parsed 0`, `by_source {description: 1}`, `icon_bands 0`:
  - ep182026 058 (8620, 8680)
  - ep202026 036 (8649, 8709)
- **Simon 033 `toa_mostlikely` (8706):** 25 labels, 24 parsed, `by_source {icon: 18, description: 7}`, `icon_bands 6`, sha `bda19f72…`, `MST`, `advisory_in_object "33"`. It replaced 8238.
- **Other keys:**
  - `timezone` is on all 120 (CST 48, MST 38, PDT 24, CDT 10).
  - `advisory_in_object` is a string on all 120.
  - `correction` is on the 8 r1 rows.
  - **`advisory_check` is on none.** All 112 first-publish rows carried it.

### 8.2 The publisher's key set (pantry `main` `96d1806`)

- `tropical/publish.py` `_toa_meta` writes `advisory_in_object`, `timezone`, `labels` (`products_geo.toa`'s `{n, parsed, null, not_a_time, by_source, icon_bands, backwards}`) and `labels_null` (the first 12 nulls, `{label, why}`).
- It writes **`advisory_check` only when the KML carries no advisory**: none does now.
- `scripts/withdraw_toa_d091756.py` adds `withdrawal {ruling, note, withdrawn_sha256, withdrawn_fetch_ts, withdrawn_vintage_id}`. Its selector is "no `labels.by_source`" (line 73), not "no `label_rule`", as the paste says. §6.5 above had that wrong.
- **No pantry code writes `label_rule`.** The banked rows agree: 0 of 120.

### 8.3 The label block (`tropics_index._toa_object`)

```python
TOA_LABEL_KEYS = ("labels", "labels_null", "timezone", "withdrawal")
... "advisory_in_object": meta.get("advisory_in_object"),
    **({"advisory_check": meta["advisory_check"]} if "advisory_check" in meta else {}),
    "labels": {k: meta[k] for k in TOA_LABEL_KEYS if k in meta}
```

- **Before:** `labels: {label_rule, timezone, counts, null, withdrawal}`, five names, two of them renamed (`counts` ← `labels`, `null` ← `labels_null`) and one the publisher never writes (`label_rule`). `advisory_check` was always served, null when absent.
- **After:** the row's keys under the row's names, verbatim, each **only where the row carries it**. `labels` is the publisher's object with all seven keys. `advisory_in_object` is served as banked (`"54"`).
- **`advisory_check` is served only where the row carries it (0 of 120 today).** Section (d) does not name it, and the publisher still writes it for a KML without an advisory. Serving it as `null` everywhere would be a key the row does not carry. Dropping it outright would lose the publisher's word if such a row comes back. **For the architect:** keep it conditional, or drop it.
- `correction` (8 rows) is not served. The ruling does not list it, and `original` already states each correction against its revision-0 row.
- `TOA_RULE`, the module docstring and `/products`' route docstring now say the same thing.

**054's `toa_earliest`, as served now** (row 8614, `054r1`):

```json
{"advisory_in_object":"54",
 "labels":{"labels":{"n":13,"null":1,"parsed":12,"backwards":[],"by_source":{"icon":9,"description":4},"icon_bands":3,"not_a_time":1},
           "labels_null":[{"why":"not_a_time","label":"Wind Speed Probability 5% contour"}],
           "timezone":"PDT",
           "withdrawal":{"note":"withdrawn and republished under D-09-25-236, no reader existed","ruling":"D-09-25-236",
                         "withdrawn_sha256":"f06dffbd…","withdrawn_fetch_ts":"2026-10-15T21:17:51.515087+00:00","withdrawn_vintage_id":8150}}}
```

### 8.4 Cells re-pinned, and the new ones (`tests/test_tropics_outlook_toa_d091757.py`: 257 → 394)

**B:**
- The read stamp, counts and shas of §8.1, and max migration 317.
- New `test_B_no_first_parser_row_is_banked`: every `labels` has `by_source`, with exactly the seven keys; no `label_rule`; 2,792 / 2,672 / 120; every null is the 5 % contour; `advisory_in_object` is a string; no `advisory_check`; 8 `correction`.
- New `test_B_every_withdrawal_names_the_ruling_and_the_row_it_replaced`.

**S / P / W:**
- 39 stamps; the newest is 202610161136.
- W's wire pins move to 11:36Z.
- The routes are unchanged.

**T:**
- Every one of the **56** identities is served as its copy in force, with `labels` equal to the row's four keys verbatim, `by_source` present, no `label_rule` anywhere in the object, and `advisory_in_object` as banked.
- The four r1 corrections with their originals: unchanged and green.
- 054's pin is the new row's full label block.
- New `test_T_a_contour_only_object_is_served_with_parsed_0_as_banked` (×2 advisories × both kinds).
- New `test_T_simon_033_mostlikely_as_the_architect_measured`.
- **New `test_T_the_label_block_serves_only_keys_the_row_carries` (×120, every banked row):**
  - the label block's keys ⊆ the row's meta keys, each value verbatim;
  - and the only TOA key beside the cone's is `advisory_in_object`.
  - It runs through `_toa_object` on each row, the 8 superseded originals included. The view's two columns are read off the key.
- The source pin names `TOA_LABEL_KEYS` and forbids `label_rule`/`counts` in the block.

**R1:**
- Bodies re-written by `write_bodies.py`, which now also writes `bodies_sha256.json`.
- **Unchanged byte for byte:** `outlook_ep_202610140517`, `outlook_ep_202610140522`, `outlook_cp_202610152311` (their rows held).
- **Changed:** `outlook_al_newest` (now 202610161136, rows 8743/8744/8746) and `products_ep182026_054` (rows 8614/8613 and 8674/8673, the new label block).

| body | sha256 |
|---|---|
| outlook_al_newest.json | `942efa3f62a65a20a7f4c3b82914dbb25e22569693f51c5fd1b90aacce0ecbb6` |
| outlook_cp_202610152311.json | `f66cdbc05094d9acb359fcc1068c780ed787a7b8119b3c56e01023dd0b7becfd` |
| outlook_ep_202610140517.json | `5c5f9b90354b62492303a354a1bc0c4552ed94949d31ad2fcf03aa13f509ffd5` |
| outlook_ep_202610140522.json | `91eb9ca239503cc20aa90b494c0974f336766e30647991da8da33a66b8f376c0` |
| products_ep182026_054.json | `f3bc0c7d3d26623d720d57cb805975904ed616c073e3ebb917a6878779a7566f` |

**X1:** the plan receipts (02:59Z) were not re-measured. The statements and indexes are unchanged, and every stamp they name is still in the ledger.

### 8.5 Red before, green after

`docs/receipts/tropics-api-outlook-toa-d091757/red_on_bbf92f2.txt` runs this branch's tests, fixtures and receipts against `bbf92f2`'s code: **183 failed, 348 passed.**
- **Every red is the label block:**
  - T every identity ×56;
  - the subset cell ×120;
  - contour-only ×2;
  - Simon 033, 054's pin, the source pin, the `toa_rule` pin, R1's 054 body.
- Everything that reads only rows is green on the old code, so the re-bank moved no route.

**Branch:** 531 of 531 across the three Tropics index modules.

### 8.6 Rehearsal (`reds.txt`): 14 of 14 as expected

- Clean before and after: 531 passed. Tree sha256 `54e09608…` → `54e09608…`, restored byte for byte.
- Breaks 1–8 and 11–13 are kept as they were. Their reds are the same tests; P's counts are now out of 117.
- Break 10 keeps its rule, but every banked row now carries a timezone, so it overwrites the row's (`"UTC"`) rather than filling one in where none was.

| # | break | result |
|---|---|---|
| 9 | **the block serves a key the row does not carry** (`label_rule`, D-09-05-T) | RED: `test_T_the_label_block_serves_only_keys_the_row_carries` (120) |
| 10 | a timezone filled in over the row's own | RED: `test_T_every_banked_identity…` (56), `test_T_the_054…` |
| 14 | **`labels.by_source` dropped from the served object** | RED: `test_T_every_banked_identity_is_served_as_its_copy_in_force` (56) |
| 8, 12 | diagnostics | GREEN, as before |

### 8.7 The whole suite

`python3 -B -m pytest -q -p no:cacheprovider`, ~13:25Z: **3,496 passed, 2 failed.**
- **+137 over §5.4's 3,359:** this module's 394 − 257.
- **The 2 failures** are asset H8 ×2 (`test_asset_runs_d091667.py`). They are the same two, red on main before this lane, and not touched.

### 8.8 Files

**Changed:**
- `tropics_index.py`: the docstring, `TOA_LABEL_KEYS`, `_toa_object`, `TOA_RULE`.
- `main.py`: `/products`' docstring only.
- `tests/fixtures/tropics_d091741/bank_outlook_toa_from_neon.py`: the count gains `n_toa_first_parser`.
- `bank/{two,toa,nhc_toa}.jsonl.gz` and `manifest.json` `"d091757"`.
- `tests/test_tropics_outlook_toa_d091757.py`.
- `tests/rehearse_tropics_outlook_toa_d091757.py`.
- `docs/receipts/tropics-api-outlook-toa-d091757/{write_bodies.py, bodies/*, bodies_sha256.json, reds.txt}`.
- This handback (§8, and one line under the header).

**New:**
- `docs/receipts/tropics-api-outlook-toa-d091757/red_on_bbf92f2.txt`.

**Railway after deploy:**
- `/products?storm_id=ep182026&advisory=054` should serve `products_ep182026_054.json` with only `cache` differing.
- The three stamped outlook bodies are stable.
- `/outlook?basin=al` serves whatever is newest then.
