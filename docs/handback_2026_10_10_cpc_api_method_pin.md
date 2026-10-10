# Handback d091691: the CPC API reads one method version

**Lane:** d091691 · **Repo:** `energylake-api` · **Branch:** `claude/cpc-api-method-pin-g74pox` (no PR, no merge, no deploy).
**Neon:** reads only (production `fancy-block-96153928`, 2026-10-10 ~17:30–17:50Z). Nothing written to Neon or R2.
**Pantry** read only: `migrations/286`, `migrations/298`, `scripts/cpc_curves.py`, `scripts/cpc_curves_writer.py`, `docs/handback_2026_10_10_cpc_curves_short_base.md` §4 and §6 (pantry main `e71fa23`).

## 0. The short version

- **Still in time.** At 17:32Z Neon held no v2 row: 720 verdicts and 428,264 curve rows, all `cpc_curves_v1`.
  - Pantry's writer now writes **v2 only** (`cpc_curves.py: METHOD_VERSION = "cpc_curves_v2"`), and 298 is applied.
  - So the installed 22:47Z schedule writes v2 tonight. This branch must be merged and deployed before then.
- **One constant**, `CPC_METHOD_VERSION = "cpc_curves_v1"`, in `cpc_outlooks.py`.
  - It reaches the SQL as a literal, so the planner sees it. `main.py` is unchanged.
  - Every reference to the three relations is pinned: 10 references in 3 statements.
  - That includes `CURVES_SQL`'s issuance list, which the brief did not list (§5.1).
- **Today's bodies are byte-equal to main's.** Every route, on d091679's bank, with or without v2 beside it. d091679's seven vectors pass untouched. No field was added, renamed or removed.
- **Plans stay index reads**, within 3× of d091679's timings (0.56×–1.14×). STOP-P not met.
- **Tests:** 81 new.
  - Red on main: 38 failed, 43 passed (`red_on_main.txt`).
  - Green: full suite 2,985 passed, which is main's 2,904 plus the 81.
  - Rehearsal: 15 breaks, 15 red, tree sha-256 identical before and after.

## 1. What changed, statement by statement

`cpc_outlooks.py` only. Each reference to the three relations now has an alias and `<alias>.method_version = 'cpc_curves_v1'`.

| statement | reference | pin | on main, with v2 written |
|---|---|---|---|
| `CURVES_SQL` | `iss` (`cpc_outlook_curves c`) | `c.method_version` | a v2-only newer issuance becomes `/curves`' newest; the frame reads `not_written` or v2 |
| | window row `w` | `w.method_version` | two window rows per issuance |
| | verdict lateral `nv` (`cpc_curve_verdicts cv`) | `cv.method_version` | none once `w` is pinned (§5.2) |
| | verdict cell `vv` | `vv.method_version` | none once `w` is pinned |
| | view lateral `dv` | `dv.method_version`, beside main's `dv.method_version = w.method_version` (kept, so a frame with no window row draws nothing, as on main) | each version's days |
| `PLACES_SQL` | `hashes` (`hv`), `inforce` lateral (`v`), the cells (`v`) | all three | two cells per (place, season, strength); two `verdict_versions` |
| `PLACES_NEWEST_SQL` | newest lateral (`c`), window join (`c`) | both | the newest issuance is v2's, or the newest rows are doubled |

The FakePool needles d091679's tests route on (`FROM v_cpc_curves_drawable dv`, `WITH hashes AS`, `AND c.day_index IS NULL`) are unchanged. `SOURCES`, `DRAWABLE_RULE` and every payload key are unchanged.

## 2. The plans, before and after

Full table: `docs/receipts/cpc-api-method-pin-d091691/plans.md`. Every plan line is in `plans_raw.txt`. The same sitting re-took main's statement (B) beside the pinned one (A), because the bank has grown from 238k to 428k rows since d091679.

| read | d091679 | B main | A pinned |
|---|---:|---:|---:|
| /curves 610 KSAN n=1 | 1.253 | 0.837 | 0.839 ms |
| /curves 814 KSAN n=1 | 0.922 | 0.843 | 0.857 ms |
| vintages 814 pnw n=14 | 8.112 | 7.692 | 9.259 ms |
| vintages KDEN n=14 | 0.829 | 0.610 | 0.850 ms |
| /places | 3.555 | 3.724 | 3.674 ms |
| /places newest | 0.325 | 0.182 | 0.181 ms |

- **The issuance scans** move from `idx_coc_issued` to `uniq_coc_row` read backward.
  - `method_version` is the last column of that key, so it is an Index Cond and the scan stays index-only (0–1 heap fetches).
  - The other `cpc_outlook_curves` probes gain `method_version` as a key equality.
- **The verdict reads** were seq scans of the 720-row table on main, and still are. No statement scans a table it did not scan before.

## 3. Tests

`tests/test_cpc_method_pin_d091691.py` (81 tests) and `tests/load_bank_d091691.py`.

**The bank.**
- d091679 banked read outputs, not tables. The loader rebuilds the base rows from those outputs, each file checked against Neon's sha-256, in a local Postgres with pantry 286 and 298 applied verbatim (`tests/fixtures/cpc_method_pin_d091691/ddl_*.sql`).
- **P0** proves the rebuild: main's statements over it serve d091679's vectors.
- v2 is constructed in committed SQL, every row passing 298's CHECKs:
  - `v2_beside.sql`: a v2 window row and days for every banked issuance, with tavg moved +1 °F; v2 verdict cells for all 720 cells; KDEN written by v2 only, at 24 base years.
  - `v2_newer.sql`: a v2-only issuance on 2026-10-10.

| test | what it holds |
|---|---|
| P0 | main's SQL on the rebuild = d091679's vectors; on today's bank the pinned bodies = main's, byte for byte |
| P1 | 11 bodies × 2 banks (v2 beside; v2 beside and newer): byte-equal to main's on v1, and equal to the vectors. Every row of every read is identical with and without v2, and carries only v1 |
| P2 | main's SQL: `/curves` serves each day twice; every vintages frame has 14 days; `/places` has 1,440 cells and two versions; KDEN draws v2's curve; no curve, vintages or places vector survives |
| P3 | `/places` and `/curves` newest stay 610temp 10-09 and 814temp 10-08, and no frame is 10-10; main's SQL serves 10-10 |
| P4 | every reference in every `*_SQL` that names the three relations carries the pin; no other version is named; the view's tie to `w` is kept |
| P5 | the receipts ran exactly the pinned and main statements; each A is ≤ 3× d091679 and seq-scans nothing d091679 did not |
| P6 | the flip is the constant: pinned to v2, `/places` has one version and 720 cells, and every frame is v2 with 7 days |

- **d091679's tests:** the seven vectors pass untouched. `test_T8_every_read_is_pinned` and `test_T8_the_plan_receipts_ran_the_pinned_sql` now read the re-banked pin and the A plans for the three CPC reads (§5.6). d091679's receipt files are not edited.
- **Rehearsal** (`rehearse.py` → `reds.txt`): one break per pinned predicate, plus the constant set to v2, main's whole file, and a statement edited without its plan re-taken. All 15 are red.

## 4. What the v2 flip lane must change

1. **`CPC_METHOD_VERSION = "cpc_curves_v2"`**, after the v2 backfill is complete (d091687 §6 step 8).
   - Before that, vintages at n=14 would show fewer v2 frames than exist.
   - Re-take the six plans, and re-bank `pinned_sql.json` and `explains.sql`; P5 and d091679's T8 require it.
2. **Flip before v1 is ever deleted.**
   - Pinned to a version with no rows, the issuance scan reads every entry of the product: the CLAUDE.md trap.
   - Until the flip, the v1 scan walks past v2's newer entries (180–240 a product a day, index-only).
3. **`reasons()`** hard-codes `history_years_in_base < 30` → `under_30_base_years`.
   - Under v2 the floor is the cell's `min_base_years` (22).
   - Serve the bank's `base_reason` verbatim and derive nothing (D-09-25-76).
   - `DRAWABLE_RULE` quotes 286's CHECK (`>= 30`); 298 made it `>= min_base_years`.
4. **Select and serve the count.**
   - Cells: `min_base_years`, `base_reason`.
   - Curves, from the view (`d.`): `base_years`, `base_fewest_stations`, `base_by_station`, `threaded_base`.
   - `CURVES_SQL` and `PLACES_SQL` list their columns, so each must be added. The v2 label already says "on N base years, not CPC's 30" and rides verbatim.
5. **d091679's v1 truths in tests:** T2's 312 cells at [22, 24, 26] with `under_30_base_years`, T5's 396 drawable, T5's `verdict_versions`, T1's newest dates and the seven vectors. Re-bank them, and give the dashboard lane the new vectors.
6. **`SOURCES.verdicts`** says "newest scored_at per method_hash"; it should name the version.
7. **§8.1 of d091679** (the status view) is still open. With it the pin would sit in one view, not ten predicates.

## 5. What this brief got wrong

1. **The pin list missed `CURVES_SQL`'s `iss`.**
   - With only `w`, both laterals and the view pinned, a v2-only newer issuance is still `/curves`' and `/curves/vintages`' newest: every place gets a `not_written` frame.
   - P1 and P3 fail without it (rehearsal rule 1), so it is pinned.
2. **"Both verdict laterals … and the view lateral" add no behaviour on their own.**
   - Once `w` is pinned, `nv`, `vv` and `dv` are implied: a `method_hash` belongs to one version.
   - `PLACES_SQL`'s three pins each imply the others.
   - Removing any one of them alone changes no body and no row; only P4 sees it (rehearsal rules 3–5). Removing all three of `PLACES_SQL`'s is a red P1/P2.
   - They are kept, as decided. The load-bearing pins are `iss`, `w`, `PLACES_NEWEST_SQL`'s two, and `PLACES_SQL` as a whole.
3. **"Byte-equal to main's" holds, but the pinned API freezes at the first v2 write.**
   - Pantry writes v2 only, so no v1 issuance arrives after tonight.
   - `/curves`, `/curves/vintages` and `/places` stay at v1's last issuance (on Neon now: 610temp and 814temp 2026-10-09) until the flip, and `newest_written_issued_date` stops advancing.
   - Main would have advanced, but into mixed versions.
   - The flip lane's timing is therefore a staleness clock, not only a backfill step.
4. **"P2 duplicates days on /curves"** holds while v2 sits beside v1 on the newest issuance. Once a v2-only newer issuance exists, unpinned `/curves` instead serves v2's newer frame (one copy, the wrong version). P2 tests both.
5. **"The view serves both vintages"** understates it: it also serves v2 curves where v1 wrote none. Unpinned, KDEN (24 base years, no v1 curve) would draw v2's curve.
6. **"d091679's banked vectors pass untouched"** holds for the bodies. d091679's two T8 receipt tests pin the SQL text and its plans, which any pin must change, so they now read this lane's re-banked pin for the three CPC reads.
7. **"Beside d091679's receipts":** the bank has nearly doubled since then (238k → 428k rows). Main's statements were re-taken in the same sitting (B) so the comparison is like for like.
