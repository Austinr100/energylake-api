# Handback d091682: the Tropics API on the rows in force — STOPPED at STOP-V

**Lane:** d091682 · **Repo:** energylake-api · **Branch:** `claude/tropics-revision-in-force-sxzqej`, from main at `c40c231` (d091673 merged).
**Scope kept:**
- Branch only. No PR, no merge, no deploy.
- Nothing was written to Neon or R2: every Neon statement was a SELECT or an EXPLAIN (ANALYZE, BUFFERS) of one, 2026-10-10 10:37–10:50Z.

**Read:**
- in this repo: `CLAUDE.md`, `tropics.py`, the tropics routes in `main.py`, `tests/test_tropics_d091673.py`, `docs/handback_2026_10_09_tropics_api.md`;
- in `energylake-pantry` at `c7779f2`, read only: `migrations/294_tropical_correction_revisions.sql`, `docs/handback_2026_10_09_corrected_advisory.md` §1–§5, and `ingesters/tropical_bank.py` (which files feed the official points and the odds).

---

## 0. In ten lines

1. **STOP-V fired.** Through pantry's views, `/tracks` and `/odds` each seq-scan a whole table on Neon: all of `tropical_track_points` (919 buffers, against d091673's 110) and all of `tropical_place_odds`.
   - `/tracks` n = 8 goes 2.75 → 29–61 ms.
   - `/storm`'s observed track goes 0.14 → 3.2 ms (23×), and its cycle census 11.5 → 86 ms (§2).
2. **So the reads were not moved.** `tropics.py` and `main.py` are unchanged on this branch, and the API still reads the bare tables.
   - The rewrite is finished and banked as `docs/receipts/tropics-api-d091682/draft_rows_in_force.patch`. It is the exact SQL the Neon plans were taken on.
3. **What pantry's view would need** (§3): one redundant clause, `q.revision > 0`, in each view's NOT EXISTS, and a partial index of the revised rows (`WHERE revision > 0`).
   - It changes no row in force; the rehearsal asserts that at both sizes.
   - Locally it brings every statement back to an index read close to d091673's cost, at the bank's size and at 31×.
4. **Measured on Neon:**
   - 294 was applied at 00:39:06Z, and the three views match the file.
   - **No advisory carries revision > 0**: no track point and no odds row.
   - **7 file identities read `corrected` true**, all of them Rachel's advisory 052A.
5. **NHC already corrected an advisory with identical rows:** Rachel 052A, at 06:44Z on 10-10. Pantry's receipt: "correction revision 2 parses identically to revision 0 — no new rows".
   - That is the brief's R5, live.
   - It makes R4's "false with revision 0 for **every** live advisory" untrue today (§6).
6. **The payload fields are designed and drafted** (§4): `corrected` and `revision` sit on the official advisory objects of `/storms`, `/storm` and `/tracks`, and on `/odds`' `issuance`.
   - **`corrected`** is the ledger's word on the files the rows were parsed from.
   - **`revision`** is the revision of the rows served.
7. **Not built, because STOP-V says stop:**
   - the R1–R7 tests;
   - the ledger fixture slice;
   - the re-banked vectors;
   - the mutation rehearsal.

   §5 is the re-fire checklist. R8, the plan receipts, is done: it is the evidence for the stop.
8. **The whole suite on this branch** is main's: 2,733 passed, 1 failed. The failure is `test_R_no_existing_route_or_memo_changed`, which is **red on main at `c40c231` too**: since d091673 merged, its merge-base contains the routes it asserts are absent (§6).
9. **The brief's R6 cannot hold as written.** The bodies gain two fields, so d091673's byte-for-byte vectors must be re-banked. And one d091673 test was already red before this lane started (§6).
10. **Nothing here needs a decision from the API side before pantry acts.** It needs pantry's fix, a re-take of the Neon plans, and a re-fire of this lane with the patch.

---

## 1. Measured on Neon (`docs/receipts/tropics-api-d091682/neon_measured.json`)

### 1.1 The three views as they stand

- **Applied:** `schema_migrations` 294 at 2026-10-10 00:39:06.036Z. `pg_get_viewdef` of each view equals the migration file's definition.
- **Columns:**
  - `tropical_track_points_in_force`: the table's 15 columns + `revision smallint`;
  - `tropical_place_odds_in_force`: 16 + `revision smallint`;
  - `tropical_file_vintage_in_force`: 15 ledger columns + `revision integer`, `corrected boolean`, `anomalies_held bigint`.
- **The rules:**
  - **Points:** no row of the same `(storm_id, source, init_ts, advisory)` with a higher revision.
  - **Odds:** the same over `(storm_id, source, issued_ts, advisory)`.
  - **Ledger:** `DISTINCT ON (source, product, storm_id, vintage_key)` over banked, non-anomaly rows, newest `fetch_ts`.
- **`advisory`** is NOT NULL (default `''`) on both tables, so the views' `q.advisory = p.advisory` never meets a NULL. Model rows all share advisory `''`, so for them "per advisory" means per `(storm, source, init)`.

### 1.2 Revisions and corrections today (10:37Z)

| what | count |
|---|---:|
| track points / with revision > 0 | 36,523 / **0** |
| place-odds rows / with revision > 0 | 4,046 / **0** |
| advisories with any revision > 0 | **0** |
| file identities in force / `corrected` true / `revision` > 0 | 3,593 / **7** / 7 |
| file identities holding an anomaly copy (`anomalies_held` > 0) | 13 (the d091675 events: Rachel 050, Isaias 012, 012A, 013); every one reads `corrected` false |

**The seven corrected identities** are Rachel `ep182026` advisory **052A**: `fcst_5day_zip`, `fcst_radii_zip`, `track_kmz`, `cone_kmz`, `ww_kmz`, `initialradii_kmz` and `forecastradii_kmz`.
- Each is at file revision 1, fetched 07:10:33–37Z.
- The signal: fileUpdateTime and Last-Modified (06:44:31–06:45:02Z) were newer than the copies banked at 06:25–06:27Z.
- Pantry's `ingestion_log` at 07:10:31Z: `ep182026/nhc_official/052A: correction revision 2 parses identically to revision 0 (rows in force) -- no new rows`.
- Rachel 052A's 4 official points are all revision 0, inserted at 06:34:39Z.
- No PWS file has been corrected.

### 1.3 Which ledger files an advisory's rows come from (pantry `ingesters/tropical_bank.py`)

- **Official points:** parsed from the advisory's `fcst_5day_zip` and `fcst_radii_zip` (`_official_points`). Their revision is the **sum** of the two files' correction counts: one correction of each makes revision 2.
- **Odds:** parsed from the advisory's `pws` text (`_pws_odds`). The row revision is the file's.
- The other five GIS files (cone, track, ww, initial and forecast radii KMZ) are banked, not parsed into any row.

---

## 2. The plans before and after (`docs/receipts/tropics-api-d091682/plans.md`)

| statement | before, re-taken 10-10 | after, through the views | STOP-V |
|---|---:|---:|---|
| `STORMS_SQL` | 0.140 ms, 25 buf | 0.608 ms, 95 buf | no (4.3×; index) |
| `OFFICIAL_NEWEST_SQL` | 0.249 ms, 34 buf | 0.912 ms, 163 buf | no (3.7×; index) |
| `OBSERVED_SQL` | 0.139 ms, 24 buf | 3.19 ms, 319 buf | **yes by the same-moment ratio (23×)**; index-only; 3.1× d091673's cold receipt |
| `CYCLES_SQL` | 11.5 ms, 1,088 buf | 86.4 ms, 2,223 buf | 7.5×; index, but each cycle read twice |
| `TRACKS_SQL` n = 8 | 2.75 ms, 110 buf | 29.2–61.5 ms, ~1,090 buf | **yes: seq scan of all of `tropical_track_points`** |
| `ODDS_SQL` | 0.944 ms, 17 buf | 3.36 ms, 103 buf | **yes: seq scan of all of `tropical_place_odds`** |

**Why.**
- The NOT EXISTS is an anti-join against the whole table, restricted only by `storm_id` (and `source` where fixed).
- For a statement returning thousands of rows, the planner hashes the storm's whole history instead of probing per row.
- On Neon that history is 62 % of the points table, so it seq-scans.
- At 31× (locally) it reads the storm's whole history by index: 7,906 buffers for /tracks n = 8 against 152.
- Either way the cost now grows with the storm's age, which d091673's loose index scan was built to stop.

Full plans are in `plans.md`. The local three-way rehearsal is in `view_plans_local.txt` / `.json`.

## 3. What pantry's view would need (`proposed_pantry_view_fix.sql`)

```sql
CREATE INDEX ttp_revised ON tropical_track_points (storm_id, source, init_ts, advisory, revision) WHERE revision > 0;
CREATE INDEX tpo_revised ON tropical_place_odds (storm_id, source, issued_ts, advisory, revision) WHERE revision > 0;
CREATE OR REPLACE VIEW tropical_track_points_in_force AS
SELECT p.* FROM tropical_track_points p
 WHERE NOT EXISTS (SELECT 1 FROM tropical_track_points q
                    WHERE q.revision > 0
                      AND q.storm_id = p.storm_id AND q.source = p.source AND q.init_ts = p.init_ts
                      AND q.advisory = p.advisory AND q.revision > p.revision);
-- tropical_place_odds_in_force: the same, over (storm_id, source, issued_ts, advisory)
```

**Why it is safe.**
- `q.revision > 0` follows from `q.revision > p.revision` and the CHECK `revision >= 0`, so the rows in force do not change.
- `rehearse_view_plans.py` asserts that every statement returns the same rows on the fixed views as on 294's. It does so with a constructed correction in place (Simon 010 and Simon's newest PWS issuance at revision 1), at the bank's size and at 31×.

**Why it is fast.** The anti-join's inner side becomes an index of corrected rows only: empty today, and a handful per corrected advisory ever after.

**Locally, fixed vs before:**

| statement | bank | 31× |
|---|---|---|
| TRACKS n = 8 | 6.4 ms / 130 buf vs 3.0 / 119 | 5.8 / 163 vs 3.3 / 152 |
| ODDS | 0.42 / 23 vs 0.26 / 19 | 1.5 / 781 vs 0.28 / 21 |
| CYCLES | 17 / 1,106 vs 9.9 / 1,048 | 21 / 1,219 vs 14 / 1,161 |
| OBSERVED | 0.11 / 21 vs 0.07 / 20 | — |

- **No seq scan of the points or odds table remains, at either size.** The 31× ODDS figure is 378 probes of an index padded with 6,944 constructed revised rows.
- **Not yet planned on Neon** (this lane writes nothing there). Pantry's lane should apply it and re-take these six plans on Neon.

**Also for pantry, not blocking.** `tropical_file_vintage_in_force` is `DISTINCT ON`, the form CLAUDE.md's d091551 rule bars for newest-row reads.
- Through the API's lookups it is harmless: the identity quals are pushed into it, and Neon reads 1–2 rows by `tfv_identity`.
- A reader that reads it unfiltered would build all 3,593 identities.

## 4. The corrected-advisory fields, and where they sit in each body (as drafted)

| route | where | fields |
|---|---|---|
| `/storms` | `storms[].newest_advisory`, after `position_tau_h` | `corrected`, `revision` |
| `/storm` | `official`, after `position_tau_h`, before `points` | `corrected`, `revision` |
| `/tracks` | each `cycles[].official[]` advisory, after `position_tau_h`, before `points` | `corrected`, `revision` |
| `/odds` | `issuance`, after `advisory` | `corrected`, `revision` |

**`revision`** (integer) is the revision of the rows served, read off the view: 0 for the original, n for the correction whose rows are in force. For the official this is pantry's sum of the 5-day and radii zips' corrections.

**`corrected`** (true|false) is the ledger's word on the files the rows were parsed from. Each is read from `tropical_file_vintage_in_force` as a LATERAL inside the same statement:
- the official forecast: `bool_or(corrected)` over its `fcst_5day_zip` and `fcst_radii_zip`;
- odds: the issuance's `pws`.

It is never inferred from the numbers. When the ledger holds no copy, it reads false.

**Three combinations, each with a meaning:**

| corrected | revision | meaning |
|---|---|---|
| false | 0 | the original advisory |
| true | 0 | NHC corrected the files and the correction changed no number we serve (Rachel 052A today) |
| true | n > 0 | the numbers served are the correction's |

**What else does not change.** `main.py` is untouched, and so are the statement count per build, the memo keys, the TTLs, the parameters and the status codes. The lookups ride inside the existing statements, so FakePool-routed tests see no new statement.

**Not on these:**
- **`newest_position`**, even when its source is the official: the page reads the advisory's state from `newest_advisory`.
- **The A-deck `atcf:OFCL` series:** NHC's GIS correction is not an A-deck correction.

## 5. Not done, and the re-fire checklist

Held by STOP-V, in order:

1. **Pantry** applies §3, or its own equivalent, and re-takes the six plans on Neon.
2. **This lane, re-fired:**
   - Apply `draft_rows_in_force.patch` to `tropics.py`.
   - Re-take `plans.md` on Neon against d091673's receipts. STOP-V is then a seq-scan or > 10× test on the new plans.
3. **Fixtures:**
   - Cut the ledger slice from Neon at d091673's cut (21:54:47.543942Z): every `tropical_file_vintage` row of products `fcst_5day_zip`, `fcst_radii_zip` and `pws`, as `row_to_json` text with Neon's sha-256. It includes the d091675 anomaly copies, which must read `corrected` false.
   - In `load_bank_d091673.load()`, apply `tests/fixtures/tropics_d091682/ddl_294.sql` (already cut, verbatim from pantry, with the file's sha) **after** the rows, as on Neon, then pantry's fix.
   - The constructed corrected advisory: Simon 010 revision 1, one tau moved and one dropped, with a `meta.correction` ledger row for its 5-day zip; Simon's newest PWS at revision 1, one value changed, one place dropped, a `below_1pct` cell kept; Isaias 013 corrected with identical rows for R5.
4. **Re-bank the 11 `test_V` vectors.** The only allowed difference: the two added keys on the four objects in §4. Check it by deleting those keys from the new body and comparing with the old bytes.
5. **Tests R1–R7** (red on this base, green with the patch), then the mutation rehearsal, then the whole suite.

The rehearsal needs one break per rule:
- read a bare table;
- drop the ledger lookup;
- take `revision` from the file instead of the rows;
- serve a revision-0 tau beside revision 1;
- serve `below_1pct` as 0;
- `corrected` inferred from changed numbers.

## 6. What this brief got wrong

1. **"Re-take the plan receipts: a view must not turn an index read into a scan."** As pantry wrote them, the views do exactly that for `/tracks` and `/odds` on Neon. STOP-V is the brief's own tripwire, and it fired (§2).
2. **R4: "false with revision 0 for every live advisory."**
   - Since 07:10Z on 10-10, Rachel 052A reads `corrected` true at revision 0: NHC corrected it, and the correction parsed identically.
   - So R5's case is live, not only constructed.
   - Where it shows: `/tracks?storm_id=ep182026` carries it in the 00Z cycle's `official` list while that cycle is among the newest n. `/storm` and `/storms` show 053.
   - At d091673's fixture cut (10-09 21:54Z) every advisory does read false, so R4 holds for the banked snapshot only.
3. **"revision" names two different numbers.**
   - The ledger's `revision` is per file.
   - The rows' revision for the official is the **sum** of two files' revisions. Pantry's receipt calls 052A's "revision 2", while each file reads 1.
   - Rows exist at that revision only if they differ.
   - The draft serves the rows' revision and says so (§4). If the page should show the file count instead, that is a ruling.
4. **"Where a route needs to know an advisory was corrected it reads `tropical_file_vintage_in_force`"** does not say which files.
   - An official advisory has seven files; its rows come from two.
   - The draft reads the two parsed files: a correction of only the cone or warnings KMZ would read false.
   - That is a choice for the architect. 052A corrected all seven, so it cannot tell the two readings apart today.
5. **R6: "every d091673 test still passes untouched except where it names a table."** Three things stop this:
   - **The vectors:** rule 2 adds two fields to four objects, so the eleven byte-for-byte vectors in `test_V_*` must be re-banked. The test code can stay untouched; its fixtures cannot.
   - **The loader:** `load_bank_d091673.load()` must apply 294 for the d091673 Postgres tests to run the new SQL.
   - **An existing red:** `test_R_no_existing_route_or_memo_changed` is **already red on main at `c40c231`**. It diffs `main.py` against its merge-base with main and asserts the base has no `/api/weather/tropics`. Since d091673 merged, the base is main itself. It was left untouched; the one-line fix (pin the base to d091673's own base, `149e314`) is for the architect.
6. **"Extend the banked snapshot with a constructed corrected advisory."** The banked snapshot holds only heartbeat rows of the ledger, so the corrected flag has nothing to read. The snapshot needs the ledger slice first (§5.3).
7. **"Fire after d091673 merges (it edits that lane's module)."** It merged as `c40c231`, and the lane fired on it. But the d091673 test that pinned "the tropics routes are new" became red at that merge (5 above).

## 7. Files

| path | what |
|---|---|
| `docs/handback_2026_10_09_tropics_api_rows_in_force.md` | this |
| `docs/receipts/tropics-api-d091682/plans.md` | Neon plans before and after, STOP-V verdicts, the local three-way table |
| `docs/receipts/tropics-api-d091682/neon_measured.json` | the views, counts, corrected identities, the live 052A witness |
| `docs/receipts/tropics-api-d091682/explains_before.sql`, `explains_after.sql` | the statements as planned on Neon, rendered by `render_explains.py` |
| `docs/receipts/tropics-api-d091682/draft_rows_in_force.patch` | the rewrite of `tropics.py`, held back by STOP-V |
| `docs/receipts/tropics-api-d091682/proposed_pantry_view_fix.sql` | §3 |
| `docs/receipts/tropics-api-d091682/rehearse_view_plans.py`, `view_plans_local.txt`, `view_plans_local.json` | the local rehearsal: before / 294 / fixed, bank and 31×, row equality asserted |
| `tests/fixtures/tropics_d091682/ddl_294.sql` | migration 294's sections 1–2 verbatim, for the local Postgres |

`tropics.py`, `main.py` and every test file are unchanged.
