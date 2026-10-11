# Handback d091693 (re-fired): the CPC API serves cpc_curves_v2

**Lane:** d091693 · **Repo:** `energylake-api` · **Branch:** `claude/cpc-api-flip-to-v2-8rss1l`, off main `a10b6f2` (#113). No PR, no merge, no deploy.
**Neon:** read only, production `fancy-block-96153928`, 2026-10-11 13:42–14:25Z. Nothing written to Neon or R2. **Pantry:** read only, `9cee080` (migration 298, handback `2026_10_10_cpc_curves_short_base` §4 and §6).
**The first run** (branch `claude/cpc-api-flip-v2-dcaf8g`) stopped at STOP-0 on 2026-10-10 20:55Z: v2 then held 321 of 3,599 issuances and no verdicts. This run passes STOP-0.

## 0. The short version

> **STOP-P is hit on read 04, by the letter. Do not deploy until the architect rules on it (§5).**
> KDEN n=14 takes 24.0 ms warm, against d091691's 1.33 ms (18×). It is not the same read. Under v1, KDEN had no curve and the view was never read. Under v2 it draws 14 frames. Every read that draws something is within 3× of d091691 (1.02×–2.57×), and every v2 read but 04 is faster than or level with production today (0.27×–1.00×). Nothing else in this lane depends on the ruling; everything below is done.

- **STOP-0 holds.** 0 v1 issuances lack v2 (v1 3,599, v2 3,601). There is one v2 `backtest_version`, `cpcv_2026-10-06_243e6acd91`, of 720 cells (§1).
- **The flip** is d091691 §3 items 1–3, in `cpc_outlooks.py`:
  - the constant;
  - clause 5 at the cell's own `min_base_years`, with `base_reason` verbatim;
  - `DRAWABLE_RULE` quoting 298;
  - the count served on every curve (5 fields) and every cell (5 fields).
  
  `main.py` is unchanged (§2).
- **Bodies.** Every route serves v2 only, and the v1 rows beside it change no byte (V1).
  - All 13 short places are written. Of their 26 newest frames, **17 draw a curve** whose label says "…, on N base years, not CPC's 30". The other **9 are not drawable**: their pooled band is now `none`.
  - **Denver is a curve** on both products, on 29 base years, five of them threaded.
  - `/places` drawable cells go **396 → 641**. 2026-10-10, written under v2 only, becomes every route's newest (§3).
- **Vectors.** Nine bodies served from real v2 rows read from Neon, with their sha-256 (§4). d091679's v1 vectors are untouched.
- **Tests.**
  - The new file `tests/test_cpc_flip_v2_d091693.py` holds **63** tests (V0–V5). Run against main's code, **45 fail and 18 pass**; the 18 read Neon's counts, the bank or the receipts, not the code.
  - Full suite: **2,967 passed** (main: 2,952; −48 from d091691's file, retired and restated as V1/V4/V5; +63).
  - Rehearsal: **19 breaks, 19 red**, tree restored byte for byte (§6).

## 1. STOP-0's counts (V0)

Read on Neon production, default branch (`docs/receipts/cpc-api-flip-v2-d091693/stop0.json` holds the statements):

| check | result | needed | holds? |
|---|---|---|---|
| (a) distinct `(product, issued_date)` with v1 rows | **3,599** | — | — |
| (a) distinct `(product, issued_date)` with v2 rows | **3,601** | ≥ v1 | yes |
| (a) v1 issuances with no v2 rows | **0** | 0 | **yes** |
| (a) v2 issuances with no v1 rows | 610temp 2026-10-10 (180 rows), 814temp 2026-10-10 (240 rows) | — | (the writer stamps v2 only since 10-10) |
| (b) `cpc_curve_verdicts`, v2 versions | **one**: `cpcv_2026-10-06_243e6acd91`, **720 cells**, 641 drawable, method `b201e09ff97d`, rules `cpc_curve_verdicts_v2` / `5e3eb4b51c0f`, scored 2026-10-11 07:19:58Z, truth frontier 2026-10-06 | one of 720 | **yes** |
| (b) v1, beside | `cpcv_2026-10-03_d0a93d5811`, 720 cells, 396 drawable | — | — |

(a) was read at 13:42:40Z and (b) at 13:42:53Z. The view's `md5(pg_get_viewdef)` is still `66966f907581836370275d209d564a67`, which equals d091691's DDL fixture.

The v2 verdicts by place (13:43Z) agree with pantry's handback, with the threads applied:
- **22 base years:** Ontario USW00003102, Burbank USW00023152, Palm Springs USW00093138, SP15 ×2, SoCalGas.
- **26:** Yuma USW00003145, desert_sw ×2.
- **28:** San Jose USW00023293 and NP15 ×2, threaded 1991–93 and 1996–98 from USC00047821.
- **29:** Denver USW00003017, threaded 1991–95 from USW00023062.
- **30:** the other 17.

No cell is under its floor of 22, so `base_reason` is NULL on all 720.

## 2. What changed, statement by statement

`cpc_outlooks.py`:

| what | before (main a10b6f2) | after |
|---|---|---|
| `CPC_METHOD_VERSION` | `"cpc_curves_v1"` | `"cpc_curves_v2"`; `_PIN` follows it, so all 10 pinned references in the 3 statements now read `= 'cpc_curves_v2'` (no other SQL text moved) |
| `CURVES_SQL` select list, `vv.` | … `band_sentence`, `drawable` | adds `vv.min_base_years AS v_min_base_years`, `vv.base_reason AS v_base_reason`, `vv.base_by_station AS v_base_by_station`, `vv.base_fewest_stations AS v_base_fewest_stations`, `vv.threaded_base AS v_threaded_base` |
| `CURVES_SQL` select list, `d.` (the view) | … `band_basis`, `backtest_version` | adds `d.base_years`, `d.base_fewest_stations`, `d.base_by_station`, `d.threaded_base`, `d.min_base_years` (298's five appended view columns) |
| `PLACES_SQL` select list | … `band_sentence`, `drawable` | adds `v.min_base_years`, `v.base_reason`, `v.base_by_station`, `v.base_fewest_stations`, `v.threaded_base` |
| `PLACES_NEWEST_SQL`, `OUTLOOK_*_SQL` | — | unchanged |
| `CELL_KEYS` | 19 keys | + `min_base_years`, `base_reason`, `base_by_station`, `base_fewest_stations`, `threaded_base` (so `/places` cells and every `vintage.verdict`) |
| `CURVE_BASE_KEYS` (new) | — | `base_years`, `base_fewest_stations`, `base_by_station`, `threaded_base`, `min_base_years`, served on every `vintage.curve` right after `history_last`, from the view's row |
| `reasons()` clause 5 | `history_years_in_base < 30` → `{code: "under_30_base_years", clause: 5, history_years_in_base}` | `history_years_in_base < cell["min_base_years"]` → `{code: "under_min_base_years", clause: 5, history_years_in_base, min_base_years, base_reason}`; `base_reason` is handed on as stored, and nothing reads it |
| `DRAWABLE_RULE` | "drawable = (verdict = 'beats' AND history_years_in_base >= 30 AND band_claim IS NOT NULL): … migration 286, D-09-25-172 clauses 3, 5 and 4. …" | "drawable = (verdict = 'beats' AND history_years_in_base >= min_base_years AND band_claim IS NOT NULL): cpc_curve_verdicts CHECK ccv_drawable_ck, migration 298, D-09-25-172 clauses 3 and 4 and D-09-25-177 clause 5. verdict is the product x place x season cell's skill test over equal-odds history; min_base_years is the floor the cell was judged at (22 in cpc_curves_v2: the shortest base measured, not a ruled floor); band_claim is judged per strength of CPC's odds." |
| module docstring, the constant's comment, `place_verdict` docstring | v1's facts | v2's facts, read today |

`main.py`: **unchanged**. The routes call the same names with the same parameters, memos, TTL and timeouts.
- I first updated the `/places` and `/curves` docstrings for the new fields. `tests/test_press_d091689.py::test_A8_no_existing_route_changed` holds `main.py` byte for byte against its base, so that went red, and the edit was reverted.
- So those docstrings still list v1's fields and say "under 30 base years". A docs-only follow-up must restate test_A8's base along with them.

The **label and the band sentence** ride verbatim; no code touches them. At a short place the view's label ends ", on N base years, not CPC's 30". The writer's sentence ends "Its normal rests on N base years, not CPC's 30", and a region adds "(the fewest of its stations, at …)".

## 3. Every body difference against today's production bytes

The full list, place by place, is `docs/receipts/cpc-api-flip-v2-d091693/body_differences.md`, generated by `differences.py` from the banked bodies.
- "Production today" is main a10b6f2's code, served by the real routes over Neon's rows as they stood at 13:47Z. The bank's every read hashes equal to Neon's (§6, V1).
- The deployed host answers 401, so its own bytes were not fetched.

The summary:

**`/curves`, place by place (newest frame).**
- Every place's frame moves from 2026-10-09 (v1's newest) to 2026-10-10 (v2's newest, v2 only).
- **The 13 short places**, 26 frames, were all `not_written`; production said why with `place_reason_codes: ["under_30_base_years"]`. Under the flip:

| place | base years | 610temp | 814temp |
|---|---:|---|---|
| Denver USW00003017 | 29 (threaded) | **curve**, measured | **curve**, about 9 in 10 |
| San Jose USW00023293 | 28 (threaded) | not_drawable (no_band_claim) | **curve**, about 9 in 10 |
| caiso_np15_proxy (population) | 28 | **curve** | **curve** |
| caiso_np15_proxy (load_share_365d) | 28 | not_drawable (no_band_claim) | **curve** |
| Yuma USW00003145 | 26 | **curve** | not_drawable (no_band_claim) |
| desert_sw (population), (load_share_365d) | 26 | **curve**, **curve** | **curve**, **curve** |
| Ontario USW00003102, Burbank USW00023152, Palm Springs USW00093138 | 22 | **curve** ×3 | not_drawable ×3 (no_band_claim) |
| caiso_sp15_proxy (population), (load_share_365d) | 22 | **curve** ×2 | not_drawable ×2 (no_band_claim) |
| socalgas_territory (population) | 22 | **curve** | not_drawable (no_band_claim) |

17 curves and 9 not drawable. Each of the 9 is a cell whose v1 band was the pooled stations' and is `none` under v2: 298 lets no short place borrow a 30-year neighbour's band.
- **The 17 places on 30 base years** draw on both products, on both sides. Their `band_claim` changes only where the 10-10 issuance's strength differs from 10-09's.

**`/places`.**
- `newest_written_issued_date` moves from 10-09 / 10-09 to **10-10 / 10-10**.
- `drawable_count` goes **396 → 641**; `cell_count` stays 720 and `place_count` 30.
- `verdict_versions` names v2's version.
- At the 13 short places, 245 cells go from not drawable to drawable, and 67 stay not drawable. The reason code `under_30_base_years` becomes, respectively, none and `no_band_claim`.
- At the 17 places on 30, drawability does not move (396 drawable, 12 not). Their statistics move where v2 was scored to the later truth frontier (10-06 against 10-03):
  - `season_*` in 96 cells;
  - `n`, `skill`, `t` and the shares in 55;
  - `band_*` in 56;
  - one cell's `band_basis`, `band_claim` and `strength_verdict`.
- 96 short-place cells change `history_years_in_base`, because of the threads: San Jose and NP15 ×2 go 22 → 28, Denver 24 → 29.
- `newest_curve` names 10-10 at all 30 places, 51 of 60 drawable. Production names 10-09 at 17 places and `not_written` at 13.

**`/curves/vintages`.**
- Every window gains 2026-10-10 and drops its oldest frame. KDEN 610temp n=14 goes from 0 drawn to **14 drawn**.
- On issuances both versions wrote at 30-year places, every percentile, equal-odds value, `member_odds` and label is **equal**. A frame differs in:
  - the version's identity: `method_version`, `method_hash`, `backtest_version`, `writer_version`, `written_at`;
  - the ten count fields;
  - where the verdict was re-scored, its statistics and the band's share (pnw 610temp: 4 of 6 frames; KSAN 814temp: none).

**Issuances that exist under v2 only** (§1): 610temp 2026-10-10 and 814temp 2026-10-10. Each is every route's newest. From here on each new issuance is v2 only, so production, pinned to v1, stays on 2026-10-09 until this is deployed.

**`/outlooks`** is unchanged: it reads none of the three relations.

## 4. The new vectors (for the dashboard lane)

`docs/receipts/cpc-api-flip-v2-d091693/`, served by this branch over Neon's rows (`sample.py`). Each is the body as served, cache block included. The memo's clock (`built_at`, `build_seconds`) is the only part a re-run changes, and `test_V1_the_vectors_are_what_the_routes_serve` holds the rest byte for byte. Production's bytes for the same requests are in `prod/`.

| vector | request | bytes | sha-256 |
|---|---|---:|---|
| `body_curves_ksan.json` | `/curves?place_kind=station&place=USW00023188` | 15,336 | `f09ced574d2b16525d81e640b6634aef84a17885078b75a6dff9cd07d68a5b41` |
| `body_curves_kden.json` | `/curves?place_kind=station&place=USW00003017` | 16,138 | `85de65cb4dcf13e2771919a11dcecb275d4e30e27e927ba2de3a7fc1b467cb4d` |
| `body_curves_san_jose.json` | `/curves?place_kind=station&place=USW00023293` | 11,417 | `ae193301e488b9b317b91a5835b8db8b2dadd7c84f521da0c026945cf8b5e7cf` |
| `body_curves_pnw_population.json` | `/curves?place_kind=region&place=pnw&weighting=population` | 19,017 | `058f492ed7918feadd3f4e6fb40cc72f935ac090214aec597c6637396bfd6979` |
| `body_curves_sp15_load_share_365d.json` | `/curves?place_kind=region&place=caiso_sp15_proxy&weighting=load_share_365d` | 12,981 | `0b89bc2a49b18cd7fb4cbb78fbdd3b2c4e645c96d62231bd33eb1c6caf7b4cf5` |
| `body_vintages_814temp_ksan_n14.json` | `/curves/vintages?product=814temp&…USW00023188&n=14` | 101,181 | `b1ae9e63637e58f3ccfea736fae4329cf7bc43229990db9fc7fa6dbba8dc79d0` |
| `body_vintages_610temp_pnw_population.json` | `/curves/vintages?product=610temp&…pnw&weighting=population` (n=7) | 55,534 | `cb47057d6f556787bb27cb67c542d2dc53a539353e5e672b0e4120d457387d45` |
| `body_vintages_610temp_kden_n14.json` | `/curves/vintages?product=610temp&…USW00003017&n=14` | 89,945 | `5c0c99db383d0434ab8882a384554a20df8403a56fb2fda5df8beb2c83f2239b` |
| `body_places.json` | `/places` | 769,891 | `a12c803303b1f0fd3b1b8578c0ff01cb9824f4c20e8ebf8947ea6e11e8052bf1` |

What each one shows:
- KDEN: a threaded short station that now draws.
- San Jose: a threaded station that draws on one product and is not drawable on the other.
- SP15 (load share): a region whose fewest are three tied stations, not drawable on 814temp.
- pnw and KSAN: 30-year places, for comparison with d091679's v1 vectors.

d091679's seven vectors are unedited and remain v1's record. The rows they were served from are in `tests/fixtures/cpc_flip_v2_d091693/`: six Neon reads, each checked against the sha-256 Neon computed (README there).

## 5. The plans, and STOP-P

Every run is in `docs/receipts/cpc-api-flip-v2-d091693/plans_raw.txt`, and the table and analysis are in `plans.md`.
- A is the v2-pinned SQL.
- B is main's v1-pinned SQL (production today), run in the same session on the same bank.
- Times are warm Execution Time in ms; the last run of each statement is compared, and every one was run sequentially.

| read | d091679 | d091691 A | B (production now) | **A (v2)** | A / d091691 | A / B |
|---|---:|---:|---:|---:|---:|---:|
| 01 /curves 610 KSAN | 1.253 | 1.412 | 4.212 | **2.254** | 1.60× | 0.54× |
| 02 /curves 814 KSAN | 0.922 | 1.390 | 5.365 | **1.422** | 1.02× | 0.27× |
| 03 vintages 814 pnw n=14 | 8.112 | 12.213 | 77.359 | **31.345** | 2.57× | 0.41× |
| 04 vintages 610 KDEN n=14 | 0.829 | 1.330 | 5.786 | **24.026** | **18.06×** | 4.15× |
| 05 places | 3.555 | 3.693 | 4.228 | **4.208** | 1.14× | 1.00× |
| 06 places newest | 0.325 | 0.181 | 0.447 | **0.242** | 1.34× | 0.54× |

**STOP-P, its two terms:**
- **A whole-table scan that was not one: not hit.**
  - No plan scans `cpc_outlook_curves`.
  - `cpc_curve_verdicts` (1,440 rows, 120 pages) is scanned whole by the `nv` lateral and by PLACES_SQL's `hashes` and `lv`, exactly as in B.
  - d091691's plans scanned it once more per read. At 1,440 rows the view's `newest` CTE and PLACES_SQL's cells use `idx_ccv_version` instead.
- **More than 3× the d091691 timing: hit on read 04 (18×).**
  - The read is not the one d091691 timed. Under v1, KDEN had no window row, so the view lateral was `(never executed)` (1.33 ms). Under v2 it draws 14 frames, 84 view rows.
  - The time is pantry's view: its `newest` CTE (DISTINCT ON over the verdicts) re-runs once per view row, at about 0.25 ms a loop (84 loops, 21 of 24 ms). Read 03, a place drawn on both sides, pays the same per row and is 2.57×.

**What the architect is asked to rule:** accept read 04 as a new read, drawn and correct, at 24 ms under the 2 s statement timeout and once per 15 min per memo key; or hold the flip until pantry takes d091679 §8.2 / §8.1 (a view that computes the version in force once per statement, or the status view). This lane does not touch pantry's view, per "nothing else".

**Two facts for the ruling:**
1. **Production is already past 3× on read 03**, with no flip: 77.4 ms against d091691's 12.2 (6.3×). Since pantry wrote v2's 720 verdicts the table has 1,440 rows, and v1's method_hash sorts second. So each `newest` loop under the v1 pin reads all 1,440 entries (0.58 ms), against 721 under v2.
2. **The flip makes every read but 04 faster than production today:** 0.27×–1.00× of B.

**The `iss` walk.** `method_version` is the last column of `uniq_coc_row`, so the v2-pinned backward walk passes each day's v1 entries in the index: 136 on 814temp, 102 on 610temp, for the days v1 also wrote, with no heap visit. It returns 3,121 v2 entries in 0.7 ms at n=14. The walk is bounded by days, as d091691 said. It shrinks to v2 only if v1 is retired, which is the captain's call.

## 6. Tests

`tests/test_cpc_flip_v2_d091693.py`, 63 tests. The bank is `tests/load_bank_d091693.py`: pantry 286 + 298 DDL, verbatim (d091691's fixture), loaded with **real rows only**. The six Neon reads in `tests/fixtures/cpc_flip_v2_d091693/`, each sha-checked:
- all 720 v1 and all 720 v2 verdict cells;
- `cpc_outlook_curves` v1 and v2 rows at six places on every issuance 2026-09-24 .. 10-10, and at all places on each version's newest issuance.

It builds three databases: `v1`, `v2` and `both`.

| tests | what they hold |
|---|---|
| **V0** ×2 | STOP-0's counts, printed, both conditions (stop0.json); the banked rows obey the same rule |
| **V1** ×40 | the six routes' reads hash **equal on Neon and on the local bank** (N1–N6: v2 KSAN n=14, KDEN n=14, SP15 n=1, PLACES, PLACES_NEWEST; v1 pnw n=14); the view is Neon's; over `both` every body's `method_version`, `method_hash`, `backtest_version` and `writer_version` are v2's and no v1 word appears; **v1 rows beside change no byte** (`both` = `v2`); the vectors are what the routes serve; 2026-10-10 is every route's newest; with the literal set back to v1 the same bank serves v1's rows alone (the pin decides, not the bank) |
| **V2** ×6 | the 13 short places and their counts are the rows' (22×6, 26×3, 28×3, 29); at each, the curve's count, members, fewest and threads are the window row's and its label is the view's own text, or the frame is not drawable for `no_band_claim` (17 drawn); Denver draws on both products with its thread; 30-year labels unchanged; the 66 pooled cells under 30 are `none`; /places counts 30 / 720 / 641 |
| **V3** ×4 | clause 5 at the cell's floor on a real v2 cell (none at 22/22; clause 5 at 21/22 with the bank's sentence verbatim; 29 is under a v1 cell's 30 and not under v2's 22); `reasons()` reads nothing out of `base_reason` and holds no 30; every served cell's five count fields equal its row; `DRAWABLE_RULE` quotes 298's CHECK as it stands in the DDL |
| **V4** ×4 | d091691's P4 rule for v2: one constant, `"cpc_curves_v2"` once, no quoted v1; all 10 references pinned to it (5, 3, 2); main's v1-pinned statements fail the rule for v2 and pass it for v1 |
| **V5** ×3 | the receipts ran this branch's SQL (A) and main's (B) character for character; STOP-P as in §5: no new scan, and over 3× on read 04 alone, recorded, not excused; every v2 read but 04 within 1.25× of production's |

**d091691's P-tests, restated.** P0–P3 → V1 (over real rows; the constructed v2 rows are gone), P4 → V4, P5 → V5.
- `tests/test_cpc_method_pin_d091691.py` (48 tests) and `tests/load_bank_d091691.py` are **retired**. Their premise was the v1 pin over constructed v2 rows, and both are superseded here.
- To restore them: `git checkout a10b6f2 -- tests/test_cpc_method_pin_d091691.py tests/load_bank_d091691.py`.
- Their DDL fixture is reused unchanged, and its README says so.

**d091679's tests** (81) stay as v1's record, with three edits:
- Its harness hands the banked v1 rows over as Postgres serves them since 298: `min_base_years` 30, the other count columns NULL (`post_298`).
- T2's clause-5 facts read `under_min_base_years`, with `min_base_years` 30 and `base_reason` null; the counts (312 cells; [22, 24, 26]; KDEN 24) are unchanged.
- T8 reads this lane's receipts for the three pinned reads.
- Its vector test holds each **unedited** vector equal to the body once this lane's additions are taken back out. The additions are exactly the ten count fields, clause 5's code and its two keys, and `DRAWABLE_RULE`.

**Results** (`docs/receipts/cpc-api-flip-v2-d091693/`):
- **Red** (`red_on_main.txt`): against main's `cpc_outlooks.py` and `main.py`, **45 failed, 18 passed**. The 18 pass by design, and are listed in the file.
- **Green** (`green.txt`): this file 63 passed; both CPC files 144; full suite **2,967 passed**. Main is 2,952: −48 retired, +63.
- **Rehearsal** (`rehearse.py` → `reds.txt`): **19 breaks, 19 red**, clean tree green (144), and the sha-256 of the five files identical before and after. One break per rule:
  - the constant back to v1;
  - a second literal;
  - each of the 10 pin sites;
  - clause 5 at a hard-coded 30, and at a hard-coded 22;
  - a derived `base_reason`;
  - `DRAWABLE_RULE` at 286's `>= 30`;
  - a cell's `base_by_station` dropped;
  - a curve's count dropped;
  - the label's count trimmed.

## 7. What the dashboard lane must change to draw the count

1. **Print the label and the band sentence verbatim.** At a short place the label already ends ", on N base years, not CPC's 30", and the sentence ends "Its normal rests on N base years, not CPC's 30" (a region adds "(the fewest of its stations, at …)"). Add no count words of the page's own, and never write "30-year normal" where a short place can be drawn (pantry §5).
2. **Read the count from the curve.**
   - `curve.base_years` is the count; `curve.min_base_years` is the floor (22).
   - `curve.base_by_station` is `[{station, base_years}]` for every member. For a region, list each member's count and mark every station in `curve.base_fewest_stations`; they can tie (SP15 and SoCalGas name three).
   - `curve.threaded_base` is `[{station, predecessor, years: [...]}]`. Print the years as listed, never as a range: San Jose's are 1991, 1992, 1993, 1996, 1997, 1998.
   - At a 30-year place `base_years` is 30 and `threaded_base` is `[]`.
3. **On a cell or verdict**, the same three lists plus `min_base_years` and `base_reason`.
   - Print `base_reason` verbatim when it is not null. It is null on every cell today.
   - Do not read `notes`' "N complete base windows" as the count (pantry §5).
4. **The reason code is renamed.** `under_30_base_years` is gone. Clause 5 is now `{code: "under_min_base_years", clause: 5, history_years_in_base, min_base_years, base_reason}`, and no v2 cell carries it today. The 9 newest frames that do not draw at short places say `no_band_claim` (`band_basis: "none"`).
5. **Drop any page logic that assumes a short place is unwritten.** Denver and the other 12 are written and mostly drawn.
6. **`drawable_rule` text and `verdict_versions` change** (v2's version, truth frontier 2026-10-06).
7. **Test against the new vectors (§4), and parse numbers rather than compare text.** A whole-valued double prints `N.0`, as d091691 noted.

## 8. What this brief got wrong

1. **STOP-P compares read 04 with a read that drew nothing.** d091691 timed KDEN n=14 when KDEN had no curve, so the view was never executed. Any flip that makes Denver a curve, which is the point of this lane, makes that read 14 frames' work and trips 3×. Its like-for-like comparison is read 03 (2.57×). Production itself is already 6.3× d091691 on read 03 with no flip, because the verdict table doubled. The ruling is the architect's (§5).
2. **"The 13 short places get curves" (§3 item 4 of d091691, and this brief's item 4).**
   - All 13 are written, and every cell and frame states its count. But only **17 of their 26 newest frames draw**: 9 are not drawable because 298 turns a short place's pooled band into `none`, and that is a body difference of its own.
   - "22 to 29 base years" is right, with the threads applied: the counts are 22, 26, 28 and 29.
3. **Clause 5 has nothing to fire on.** No v2 cell is under its floor of 22, and `base_reason` is NULL on all 720. So the clause-5 change and `base_reason` cannot show in any body. V3 proves them on a real cell with its count set under the floor.
   - The brief did not say that the code `under_30_base_years` names the number it must stop assuming. It is renamed `under_min_base_years`, and that is a vocabulary change for the dashboard (§7).
4. **"With v1's real rows as the other version."**
   - d091679's fixtures are CURVES_SQL answers, not base rows. d091691 rebuilt v1 base rows from them, with KSAN's values at 14 other stations.
   - This lane read v1's (and v2's) base rows from Neon instead, so the other version is real. Every route read is hash-checked against Neon.
5. **"Restate d091691's P-tests."** They could not be restated in place: their bank's v2 rows were constructed, and their bodies were main's v1 bytes. They are restated as V1/V4/V5 over real rows, and d091691's file and loader are retired. That deletes 48 tests from the suite; the command to restore them is in §6.
6. **"d091679's vectors stay as v1's record, unedited"** holds. But d091679's tests serve them through today's shaping, which now reads 298's columns. So its harness needed the v1 rows as Postgres hands them over since 298, and an explicit undo of this lane's additions (§6).
7. **"Today's production bytes"** could not be fetched: the deployed host answers 401. They are main a10b6f2's code served over Neon's rows, and the rows are proven equal to Neon's read for read (V1, N1–N6).
8. **The handback's file name** carries 2026_10_10, the first run's date. This run read Neon on 2026-10-11. The name is kept as the brief gives it.
9. **Not in the brief: `main.py` is pinned byte for byte** by the press lane's `test_A8_no_existing_route_changed`. So the route docstrings that describe these bodies cannot change without restating that test's base. They are left describing v1 (§2).

## Files

- `cpc_outlooks.py`: the constant, the count columns, clause 5, `DRAWABLE_RULE`, docstrings.
- `tests/test_cpc_flip_v2_d091693.py`, `tests/load_bank_d091693.py`, `tests/fixtures/cpc_flip_v2_d091693/` (six Neon reads, manifest, README).
- `tests/test_cpc_outlooks_d091679.py`: `post_298`, T2's code, T8's receipts, the vector test.
- `tests/fixtures/cpc_method_pin_d091691/README.md`: one paragraph noting the reuse.
- Removed: `tests/test_cpc_method_pin_d091691.py`, `tests/load_bank_d091691.py`.
- `docs/receipts/cpc-api-flip-v2-d091693/`:
  - STOP-0 and Neon checks: `stop0.json`, `neon_reads.py`, `neon_reads.json`;
  - plans: `make_explains.py`, `explains.sql`, `extract_plans.py`, `plans_raw.txt`, `plans.md`, `pinned_sql.json`;
  - vectors and differences: `sample.py`, `body_*.json` (9), `prod/body_*.json` (9), `bytes.psv`, `curves_by_place.json`, `differences.py`, `body_differences.md`;
  - test runs: `red_on_main.txt`, `green.txt`, `rehearse.py`, `reds.txt`.
