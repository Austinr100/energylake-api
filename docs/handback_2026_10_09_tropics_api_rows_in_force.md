# Handback d091682: the Tropics API reads the rows in force, and says when an advisory was corrected

**Lane:** d091682 · **Repo:** energylake-api · **Branch:** `claude/tropics-revision-in-force-sxzqej`, from main at `c40c231` (d091673 merged).
**Two firings:**
1. **2026-10-10 10:37–10:50Z — stopped at STOP-V.** The reads through 294's views were whole-table seq scans (§3).
2. **Re-fired on the architect's correction, 11:57–12:30Z,** after pantry migration 297 (§3's fix) was applied on Neon at 11:55:17Z.

**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- Nothing was written to Neon or R2: every Neon statement was a SELECT or an EXPLAIN (ANALYZE, BUFFERS) of one.

**Read:**
- in this repo: `CLAUDE.md`, `tropics.py`, the tropics routes in `main.py`, `tests/test_tropics_d091673.py`, `docs/handback_2026_10_09_tropics_api.md`;
- in `energylake-pantry`, read only: migrations 294 and 297, `docs/handback_2026_10_09_corrected_advisory.md` §1–§5, and `ingesters/tropical_bank.py` (which ledger files feed the official points and the odds).

---

## 0. In ten lines

1. **Every read of track points and place odds is now of pantry's views** (`tropical_track_points_in_force`, `tropical_place_odds_in_force`). No page route names a bare table (R7).
2. **The six reads through the 297 views are index reads on Neon,** none above 3.4× its d091673 receipt (§2), so STOP-V does not fire on the re-fire.
   - The first firing's seq scans (`/tracks` 2.75 → 29–61 ms over all of `tropical_track_points`) are gone: `/tracks` n = 8 is 6.6 ms and `/odds` 0.29 ms.
3. **`corrected` and `revision` ride on every official advisory and every odds issuance:**
   - `/storms`' `newest_advisory`;
   - `/storm`'s `official`;
   - each `/tracks` cycle's `official[]`;
   - `/odds`' `issuance`.

   `corrected` is the ledger's word (`tropical_file_vintage_in_force`) on the files the rows were parsed from. `revision` is the revision of the rows served (§4).
4. **Nothing else in any body changed.** d091673's 11 vectors were re-banked, and each one minus the two fields is d091673's body byte for byte (R6, ruling 2).
5. **Live, at the bank's cut, every advisory reads corrected false at revision 0,** true to the ledger (R4, ruling 1). On Neon today two advisories read otherwise (§1.2):
   - **Rachel 052A:** corrected true, its rows at revision 0. This is R5's case, live.
   - **Simon 012A:** corrected true at revision 1. NHC moved its tau-6 position; it is the first correction whose rows differ.
6. **Production is serving Simon 012A wrong today.** `main` reads the bare table, so Simon's `/storm` official holds both revisions of 012A's tau 6. This branch fixes it (§8).
7. **New tests:** 28 (R1–R8) in `tests/test_tropics_rows_in_force_d091682.py`; together with d091673's 87, all **115 pass**.
   - On the base `tropics.py`, 23 are red: 11 new and 12 of d091673's (§5).
   - Mutation rehearsal: **14 of 14 breaks red, tree restored byte for byte.**
8. **`test_R_no_existing_route_or_memo_changed` is repaired** (ruling 3). Its base is pinned to `149e314`, the commit before d091673's routes. It was red on main since d091673 merged; it is green here.
9. **The banked snapshot gained the ledger,** every `tropical_file_vintage` row at d091673's own cut (2,637 + the 31 heartbeats it had), each file's sha checked against Neon's.
   - **Why the whole ledger:** with only the 36 rows the routes read, the local planner seq-scans a 4-page table and d091673's T10 goes red. At full size it plans as Neon does, through `tfv_identity`.
10. **The whole suite:** 2,761 passed, 1 failed. The failure is a clock-dependent chart-brief test that fails identically without this lane (§5.4).

---

## 1. Measured on Neon (`docs/receipts/tropics-api-d091682/neon_measured.json`, `neon_plans_297.json`)

### 1.1 The views

- **294** was applied 00:39:06Z; **297** 11:55:17Z. Read back at 11:57:53Z, both views' definitions are 297's (with `q.revision > 0`).
- **Indexes:** `ttp_revised` and `tpo_revised` exist, `WHERE revision > 0`.
- **Columns:** each view carries every column of its table + `revision`. The file view carries 15 ledger columns + `revision`, `corrected`, `anomalies_held`.
- **`advisory`** is NOT NULL (default `''`) on both tables, so "per advisory" never meets a NULL. For model rows (advisory `''`) it means per `(storm, source, init)`.

### 1.2 Revisions and corrections

| read at | points with revision > 0 | odds with revision > 0 | file identities `corrected` |
|---|---:|---:|---|
| 10:37Z (first firing) | 0 | 0 | 7, all Rachel 052A |
| 11:57Z (re-fire) | **6** | 0 | 8: Rachel 052A (7 files) and Simon 012A's 5-day zip |

**Rachel 052A (corrected 06:44Z, banked 07:10Z): corrected with identical rows.**
- Pantry's log: "correction revision 2 parses identically to revision 0 (rows in force) -- no new rows".
- Its 4 official points are revision 0. It reads `corrected: true, revision: 0` wherever served: `/tracks` while its 00Z cycle is among the newest n.

**Simon 012A (banked 11:55:13Z, corrected 11:57:11Z): corrected with different rows.**
- Pantry's log: "correction revision 1 wrote 6 rows; they are in force, revision 0's rows kept as history".
- Revision 1 moves tau 6 from 17.2 N, 104.6 W, 125 kt, 942 hPa to 17.3 N, 104.8 W, 130 kt, 938 hPa; taus 12–60 are equal.
- `OFFICIAL_NEWEST_SQL` through the view returns 24 of the 30 candidate rows: revision 0's six are gone.
- It reads `corrected: true, revision: 1` on `/storms`, `/storm` and `/tracks` while 012A is Simon's newest advisory.

### 1.3 Which ledger files an advisory's rows come from (pantry `ingesters/tropical_bank.py`)

- **The official points** are parsed from the advisory's `fcst_5day_zip` and `fcst_radii_zip`. Their revision is the **sum** of the two files' correction counts, which is why pantry calls Rachel 052A "revision 2" while each file reads 1.
- **The odds** are parsed from the advisory's `pws` text.

---

## 2. The plans

| statement | d091673 receipt | through 294's views (first firing) | **through 297's views (re-fire)** | ratio to d091673 |
|---|---:|---:|---:|---:|
| `STORMS_SQL` | 0.135 ms | 0.608 ms | **0.139 ms**, 31 buf | 1.0× |
| `OFFICIAL_NEWEST_SQL` | 0.150 ms | 0.912 ms | **0.516 ms**, 155 buf | 3.4× |
| `OBSERVED_SQL` | 1.02 ms | 3.19 ms (23× same-moment) | **0.242 ms**, 97 buf | 0.2× |
| `CYCLES_SQL` | 10.3 ms | 86.4 ms | **15.9 ms**, 1,204 buf | 1.5× |
| `TRACKS_SQL` n = 8 | 3.26 ms | 29–61 ms, **seq scan of all of `tropical_track_points`** | **6.6 ms**, 2,363 buf | 2.0× |
| `ODDS_SQL` | 0.357 ms | 3.36 ms, **seq scan of all of `tropical_place_odds`** | **0.294 ms**, 19 buf | 0.8× |

**On 297, every anti-join's inner side is `ttp_revised` or `tpo_revised`.**
- Those indexes hold 6 points today, and none of Rachel's.
- Every ledger lookup is `tfv_identity`.
- Not one plan scans a whole table.

**Where the 297 plans stay dearer than d091673's:**
- **`/tracks`** makes one 1-buffer probe of `ttp_revised` per point served: 2,218 of them.
- **`/storm`'s census** probes once per cycle.

Full plans: `plans.md` (both firings) and `neon_plans_297.json` (the re-fire, pinned by R8).

**Locally,** `test_R8_with_a_correction_in_the_bank_every_read_is_an_index_read` plans every statement on the bank with the constructed corrections in place. d091673's `test_T10_*` plan them at the bank's size and at 31×. All are index reads, and every anti-join reads 297's index.

## 3. The first firing's STOP-V, and what pantry did

294's NOT EXISTS was planned as an anti-join against the whole table, restricted only by `storm_id`. That made `/tracks` and `/odds` seq-scan whole tables on Neon, with costs growing with a storm's age.

The first firing stopped there. It handed pantry `proposed_pantry_view_fix.sql`:
- a redundant `q.revision > 0` in each view (no row in force changes);
- partial indexes of the revised rows.

Pantry applied it, verbatim, as **migration 297** (11:55:17Z). Its post-check proves the rows in force unchanged.

`tests/fixtures/tropics_d091682/ddl_294.sql` and `ddl_297.sql` are the two migrations' schema changes, cut verbatim with each file's sha-256. The local Postgres applies them after the bank's rows, as Neon did.

## 4. The corrected-advisory fields, and where they sit in each body

| route | object | fields, in this order |
|---|---|---|
| `/storms` | `storms[].newest_advisory` | `advisory, init_ts, position_valid_ts, position_tau_h,` **`corrected, revision`** |
| `/storm` | `official` | `advisory, init_ts, position_valid_ts, position_tau_h,` **`corrected, revision`**`, points, label, same_init_advisories` |
| `/tracks` | `cycles[].official[]` | `advisory, init_ts, position_valid_ts, position_tau_h,` **`corrected, revision`**`, points` |
| `/odds` | `issuance` | `issued_ts, advisory,` **`corrected, revision`** |

**`revision`** (integer) is the revision of the rows served, read off the view. The view serves one revision per advisory, so the field cannot mix.

**`corrected`** (true|false) is `bool_or(corrected)` over the copies in force of the files the rows were parsed from:
- for the official, its `fcst_5day_zip` and `fcst_radii_zip`;
- for the odds, its `pws`.

It is read in the same statement as the rows, by a LATERAL on `tropical_file_vintage_in_force` that probes `tfv_identity`. It is never inferred from the numbers. Where the ledger holds no copy, it is false.

**What the combinations mean (ruling 1: true to the ledger for every advisory):**

| corrected | revision | means | live example |
|---|---|---|---|
| false | 0 | the advisory as first published | every advisory at the bank's cut |
| true | 0 | NHC corrected the files; no number we serve changed | Rachel 052A |
| true | n > 0 | the numbers served are the n-th correction's | Simon 012A |

**What did not change:**
- **`main.py`.** The statement count per build, the memo keys and TTLs, the parameters and the status codes are as they were. The lookup rides inside the existing statements.
- **The two fields are not on** `newest_position` or the A-deck `atcf:OFCL` series.

## 5. Tests

### 5.1 The fixtures

**`tests/fixtures/tropics_d091682/` (`manifest.json`):**

| file | what | rows | sha-256 (Neon's, re-checked on every load) |
|---|---|---:|---|
| `bank/ledger.jsonl.gz` | every `tropical_file_vintage` row at d091673's cut but the heartbeats, as `row_to_json` text | 2,637 | `2ab0c6057ae465dedea7b893a5b3663b64ffea1141ddfc37b458ee9d16000ffa` |
| `bank/dataset.jsonl.gz` | the ledger's four datasets d091673's bank lacks | 4 | `31f70fc4fe3431e66684ada1e356c18aad670d49a836632c7991be7ab1331bfb` |

- **The ledger rows:** all 2,637 are banked with `closed_ts` null; 13 are the d091675 anomaly copies; none carries `meta.correction`; 36 are of the three products the routes read.
- **`ddl_294.sql`, `ddl_297.sql`:** the migrations, verbatim.
- **`corrected.sql`:** the constructed corrections, applied only by the tests that ask for them, on their own cluster. Every ledger row it writes says `constructed`.
  - **C1:** Simon 010's 5-day zip corrected. Revision 1 moves tau 24 (17.5 → 17.8 N, 125 → 130 kt) and drops tau 96, which exists in revision 0 only.
  - **C2:** Simon's PWS 010 corrected. Revision 1 raises one value by 1 and drops MANZANILLO; the below-1 % cells are copied as is.
  - **C3:** Isaias 013's 5-day zip and PWS corrected with identical rows; no row written.
- **`vectors_d091673.json`:** the sha-256 and bytes of each of d091673's 11 vectors as merged (`c40c231`), and its `sizes.json`.

**The loader.** `tests/load_bank_d091682.py`; `load_bank_d091673.load()` now calls its `in_force()` after the bank's rows (294, 297, the ledger).

**d091673 files changed, and why:**

| file | change | why |
|---|---|---|
| `tests/load_bank_d091673.py` | +5 lines: the `in_force()` call | the routes now read the views |
| `tests/test_tropics_d091673.py` | `_base()` pinned to `149e314` | ruling 3 |
| `tests/rehearse_tropics_d091673.py` | break 16's needle names the view | it names a table |
| `tests/fixtures/tropics_d091673/bodies/*.json` | re-banked | ruling 2 |
| `docs/receipts/tropics-api-d091673/sizes.json` | re-banked | each `/tracks` body is 31 bytes per official advisory larger; `test_T10_body_bytes` reads it. Re-banked by d091673's own `bank_bodies.py`, so its `local_build_ms` timings were re-taken too |

Every other d091673 test is untouched.

### 5.2 R1–R8 (`tests/test_tropics_rows_in_force_d091682.py`, 28)

| | test | holds |
|---|---|---|
| R1 | `test_R1_*` (3) | C1 is what it says; `/storm` serves Simon 010 at revision 1 only, taus 3–72 (no 96), tau 24 = 17.8 N 130 kt; `/storms` names it corrected, revision 1 |
| R2 | `test_R2_*` (2) | every `/tracks` official and series has one row per tau; the bare tables hold one mixed advisory and one mixed issuance (C1, C2); the views hold none |
| R3 | `test_R3_*` (2) | `/odds` serves C2's revision 1 only: MANZANILLO gone, exactly one cell changed by +1, every below-1 % cell `value_pct: null, below_1pct: true` |
| R4 | `test_R4_*` (3) | every `corrected`/`revision` in every body equals the ledger's word, computed in Python from the ledger rows (pantry's view rule), and the rows' max revision: all false/0 on the live bank, exactly the 8 objects C1–C3 touch on the constructed one; the d091675 anomaly copies never read as corrections |
| R5 | `test_R5_*` (2) | C3: corrected true at revision 0, and the storm, tracks and odds bodies equal the live bank's minus the two fields; the Neon receipt records Rachel 052A |
| R6 | `test_R6_*` (12) | each re-banked vector carries the fields on exactly the objects of §4, and minus them has d091673's sha; the fields sit after `position_tau_h` and `advisory` |
| R7 | `test_R7_*` (2) | no `*_SQL` and no line of `main.py`'s tropics section names a bare points or odds table; every point/odds statement names its view; the three that serve advisories read `tropical_file_vintage_in_force` |
| R8 | `test_R8_*` (2) | locally, with C1–C3 in the bank, no statement seq-scans the points, odds or ledger table, every anti-join reads `ttp_revised`/`tpo_revised` and the lookups `tfv_identity`; the Neon receipt holds every read an index read within 10× of d091673 |

### 5.3 Red, then green

**Red on the base `tropics.py`** (`red_on_base.txt`): 23 failed, with this lane's fixtures, loader and vectors in place.
- **11 of the new tests:** every R1, R3, R4, R5 test, `test_R2_tracks_*`, `test_R7_no_statement_*` and the local R8.
- **12 of d091673's:** the 11 vectors and the body-size receipt.

**Green on the branch:** 115 of 115 across both tropics modules.

**The rehearsal** (`reds.txt`, `tests/rehearse_tropics_d091682.py`): 14 breaks, one per rule, applied in place and restored:
- **Rule 1 (rows in force):** /tracks, the official and /odds each read a bare table.
- **Rule 2 (`corrected` and `revision`):**
  - the ledger lookup is dropped;
  - corrected is inferred from changed numbers;
  - the official reads only the radii zip's word;
  - revision is served as 0;
  - an anomaly copy is taken as the copy in force.
- **Rule 3 (odds):** below_1pct is served as 0.
- **Rule 4 (no other change to bodies):** a third field is added; the fields move before the position.
- **Rule 5 (plans):** either view loses 297's clause.
- **The repair:** `test_R`'s base is unpinned.

**Every break is red,** the clean tree is green before and after, and the tree hash is unchanged (`269d348a62a39902`).

### 5.4 The whole suite

**2,761 passed, 1 failed:** `tests/test_chart_brief.py::test_chart_brief_maps_contract`.
- **It is not this lane's.** It fails identically with every change of this lane stashed.
- **The cause:** it asserts a publication status of `overdue` from the wall clock, with no clock pinned. It passed in the first firing's run (~11:15Z) and reads `pending` at 12:25Z.
- **Left for its own lane.** At the first firing the suite was 2,733 passed with `test_R_no_existing_route_or_memo_changed` red; that test is now green (ruling 3), and the 28 new tests are the rest of the difference.

## 6. What the dashboard lane must show

**Where to read it:** `corrected` and `revision` from the four objects in §4. Never compare numbers across fetches to guess a correction.

**What to print:**

| state | label |
|---|---|
| `corrected: true` | "Corrected advisory" beside the advisory number, on the storm card (`/storm` official), the list (`/storms` newest_advisory), each cycle's official track in the player, and the odds table header (`/odds` issuance) |
| `corrected: true`, `revision: 0` | the same label, with "NHC re-issued this advisory; no position, intensity or radius changed" (Rachel 052A today) |
| `corrected: true`, `revision` > 0 | the same label: the numbers shown are the correction's. Do not draw the superseded revision anywhere: the API never serves it, and a "revisions of this advisory" read does not exist yet |
| `corrected: false` | nothing |

**Odds:** a corrected issuance can drop a place or a cell. Absence keeps its d091673 meaning (below NHC's print threshold), and `below_1pct` still prints "<1 %".

**Vectors:** `tests/fixtures/tropics_d091673/bodies/*.json` are the re-banked bodies (all false/0 at the cut). The constructed cases live in `corrected.sql` and R1–R5.

## 7. What this brief got wrong

1. **"A view must not turn an index read into a scan."** As pantry first wrote them, the views did exactly that for `/tracks` and `/odds`. STOP-V fired, and pantry's 297 fixed the views (§3).
2. **R4 as first written,** "false with revision 0 for every live advisory", was not true by the first firing (Rachel 052A).
   - Ruling 1 restated it as "true to the ledger for every advisory", which R4 now holds.
   - Since 11:57Z, Simon 012A is a live corrected-with-different-rows case too.
3. **"revision" names two numbers.**
   - The ledger's is per file.
   - The official rows' is the sum over two files (pantry's "revision 2" for 052A).
   - The payload serves the rows' (§4).
4. **"Reads `tropical_file_vintage_in_force`" did not say which files.** The API reads the files the rows are parsed from:
   - **The official:** the 5-day and radii zips.
   - **The odds:** the PWS.
   - **A correction of only the cone or warnings KMZ** reads false. Pantry wrote no rows for one, and no number we serve came from it. Simon 012A shows the line: only its 5-day zip is corrected in the ledger, and its rows changed.
5. **R6 as written** ("every d091673 test passes untouched except where it names a table") could not hold. The vectors had to be re-banked, which ruling 2 restated, and one d091673 test was already red on main, which ruling 3 repaired. Two more d091673 files had to move:
   - the loader, to apply 294 and 297;
   - `sizes.json`, the bytes T10 holds the bodies to.
6. **"Extend the banked snapshot"** needed the ledger first: d091673's bank held only its heartbeat rows. It also needed the **whole** ledger, not the 36 rows the routes read, or the local planner seq-scans a 4-page table and T10 cannot hold.
7. **"Fire after d091673 merges"** was right, but that merge is also what turned `test_R_no_existing_route_or_memo_changed` red.

## 8. Production, today

`main` (d091673) reads the bare tables. Since pantry wrote Simon 012A's revision 1 at 11:57:26Z:
- **`/storm?storm_id=ep202026`'s official** holds both revisions' points under 012A: two tau-6 points, 125 kt and 130 kt.
- **`/tracks`** holds the same under the 06Z cycle's official.

This branch serves revision 1 only. Until it merges, Simon's official on the page shows the doubled tau 6.

## 9. Files

| path | what |
|---|---|
| `tropics.py` | every statement on the views; the ledger LATERAL; `correction()`; the two fields |
| `tests/test_tropics_rows_in_force_d091682.py` | R1–R8 (28) |
| `tests/load_bank_d091682.py` | `in_force`, `construct`, the ledger rule in Python, the R6 strip |
| `tests/rehearse_tropics_d091682.py` | the mutation rehearsal (14 breaks) |
| `tests/fixtures/tropics_d091682/` | ledger and datasets (Neon's, sha-checked), `ddl_294.sql`, `ddl_297.sql`, `corrected.sql`, `vectors_d091673.json`, `manifest.json` |
| `tests/load_bank_d091673.py`, `tests/test_tropics_d091673.py`, `tests/rehearse_tropics_d091673.py`, `tests/fixtures/tropics_d091673/bodies/`, `docs/receipts/tropics-api-d091673/sizes.json` | §5.1 |
| `docs/receipts/tropics-api-d091682/` | `plans.md` (both firings), `neon_plans_297.json`, `neon_measured.json`, `explains_before.sql` / `explains_after.sql`, `red_on_base.txt`, `reds.txt`, the first firing's `proposed_pantry_view_fix.sql`, `draft_rows_in_force.patch` (now applied), `rehearse_view_plans.py` and its outputs |
