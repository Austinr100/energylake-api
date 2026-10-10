# Handback d091691: the CPC API reads one method version, so v2 rows beside v1 change nothing

**Lane:** d091691 · **Repo:** `energylake-api` · **Branch:** `claude/cpc-api-method-pin-d091691`, off main `891de2e`. No PR, no merge, no deploy.
**Neon:** read only, production `fancy-block-96153928`, 2026-10-10 ~13:55–14:30Z. Nothing written. **Pantry:** read only, `f2145f8` (migrations 286 and 298, handback `2026_10_10_cpc_curves_short_base` §4 and §6).

## 0. The short version

- **One constant.** `cpc_outlooks.CPC_METHOD_VERSION = "cpc_curves_v1"`.
  - It goes into the SQL as a literal, so the planner sees the value.
  - Every reference to `cpc_outlook_curves`, `cpc_curve_verdicts` and `v_cpc_curves_drawable` filters on it: 10 references in 3 statements.
  - That includes one the brief did not list: the issuance CTE in `CURVES_SQL` (§4.1).
- **Bodies.** No field is added, renamed or removed.
  - Over the bank with v2 rows beside v1, every route serves **main's bytes**: main's SQL over the same bank without the v2 rows, through Postgres.
  - Each body also equals d091679's vector. The vectors print whole-valued doubles as ints (§4.3), so that comparison reads `N.0` as `N`.
- **Neon at 14:26Z holds no v2 row**, so this lands in time if it is deployed before the first v2 write. Pantry's installed `cpc-curves.yml` runs at **22:47Z** (pantry handback §6 step 3).
- **Plans.** Same shapes; the pin adds index conditions and filters, and no new scan. Warm, every read is within **1.6×** of d091679's timing. STOP-P is not hit (§2).
- **Tests.**
  - New: 48 tests in `tests/test_cpc_method_pin_d091691.py`. They run the routes' own SQL on a throwaway local Postgres built from pantry's DDL verbatim.
  - Red on main: **20 failed** (every P1, P3 and the P4 rule).
  - Green: the full suite is **2,952 passed**, against a baseline of 2,904.
  - Rehearsal: **13 breaks, 13 red**, and the tree restored byte for byte.

## 1. What changed, statement by statement

`cpc_outlooks.py` only. `main.py` is unchanged: it calls the same names with the same params.

| statement | reference | before (main 891de2e) | after |
|---|---|---|---|
| `CURVES_SQL` | `iss`: the n newest issuances | `FROM cpc_outlook_curves WHERE product = …` | `FROM cpc_outlook_curves ci WHERE ci.product = … AND ci.method_version = 'cpc_curves_v1'` |
| | `w`: the place's window row | product, issuance, place, weighting, `day_index IS NULL` | the same `AND w.method_version = 'cpc_curves_v1'` (now the full `uniq_coc_row` key) |
| | `nv`: the verdict version in force | `FROM cpc_curve_verdicts WHERE method_hash = w.method_hash` | `FROM cpc_curve_verdicts cv WHERE cv.method_hash = w.method_hash AND cv.method_version = 'cpc_curves_v1'` |
| | `vv`: the cell | on version, hash, cell | the same `AND vv.method_version = 'cpc_curves_v1'` |
| | `dv`: the view's rows | `dv.method_version = w.method_version` | kept, `AND dv.method_version = 'cpc_curves_v1'` |
| `PLACES_SQL` | `hashes` | `SELECT DISTINCT method_hash FROM cpc_curve_verdicts` | `… FROM cpc_curve_verdicts hv WHERE hv.method_version = 'cpc_curves_v1'` |
| | `inforce` lateral | `… v WHERE v.method_hash = h.method_hash` | `… lv WHERE lv.method_hash = h.method_hash AND lv.method_version = 'cpc_curves_v1'` |
| | the cells | `JOIN inforce …` | the same `WHERE v.method_version = 'cpc_curves_v1'` |
| `PLACES_NEWEST_SQL` | newest issuance lateral | `… c WHERE c.product = p.product` | `… lc WHERE lc.product = p.product AND lc.method_version = 'cpc_curves_v1'` |
| | that issuance's window rows | `AND c.day_index IS NULL` | `AND c.day_index IS NULL AND c.method_version = 'cpc_curves_v1'` |
| `OUTLOOK_*_SQL` | none of the three relations | — | unchanged |

**Aliases.** Every reference now carries its own alias (`ci`, `cv`, `hv`, `lv`, `lc` are new), so the source rule can find each one. `test_P4_every_reference_is_counted` counts them: 5, 3 and 2.

**d091679's tests.** Its two T8 SQL-pin tests read their pin and plans from this lane's receipts (`PIN_RECEIPTS`). Its seven banked bodies and every other line are unchanged, and pass.

## 2. The plans, before and after

Every line is in `docs/receipts/cpc-api-method-pin-d091691/plans_raw.txt`, the table in `plans.md`. A is pinned and B is main's SQL; both were run in the same session on today's bank.

| read | d091679 | B now | A warm | A / d091679 |
|---|---:|---:|---:|---:|
| C01 /curves 610 KSAN | 1.25 ms | 2.89 ms | 1.41 ms | 1.13× |
| C02 /curves 814 KSAN | 0.92 ms | 16.73 ms | 1.39 ms | 1.51× |
| C03 vintages 814 pnw n=14 | 8.11 ms | 15.67 ms | 12.21 ms | 1.51× |
| C04 vintages KDEN n=14 | 0.83 ms | 2.65 ms | 1.33 ms | 1.60× |
| P01 places | 3.56 ms | 3.66 ms | 3.69 ms | 1.04× |
| P02 places newest | 0.33 ms | 0.18 ms | 0.18 ms | 0.56× |

**Cold runs.**
- A03's first run took **876 ms**. It read 81 pages from Neon's storage and set hint bits on 37 freshly written ones, at 29.7 ms per window-row probe.
- The second run was the same plan, every page a hit: 12.2 ms.
- B03 had warmed most of the same pages in between.
- Both runs of every statement are kept.

**What moved.**
- `iss` and the newest-issuance lateral now walk `uniq_coc_row` backward on (product, method_version) instead of `idx_coc_issued`. They read the same number of index entries, and the filter is in the index, with no heap visit.
- `w` uses the full unique key.
- No plan scans `cpc_outlook_curves`.
- The only whole-table scans are of `cpc_curve_verdicts` (720 rows, 51 pages): the `nv` lateral, the view's own `newest` CTE, and PLACES_SQL's three references. Main scans it the same way, read for read. The pin adds a filter to those scans and no new scan.

**Named risk.** Once v2 is written, the v1-pinned `iss` walk passes v2 entries:
- about 2× the entries while both versions are written per issuance;
- about 420 tuples per day once only v2 is written.

Both are bounded by days, not by the table (plans.md).

## 3. What the v2 flip lane must change

1. **`CPC_METHOD_VERSION = "cpc_curves_v2"`**: one line, and every read follows. Flip it only after pantry §6 step 8: the backfill is complete and the first v2 `backtest_version` is written. The memos' 900 s TTL means bodies switch within 15 minutes of the deploy.
2. **Clause 5 in `reasons()`.**
   - It hard-codes `history_years_in_base < 30` → `under_30_base_years`. Under v2 the floor is the cell's own `min_base_years` (22).
   - Read `vv.min_base_years` / `v.min_base_years`.
   - Serve `base_reason` verbatim when present, and derive nothing (pantry §4.2).
   - `DRAWABLE_RULE` quotes 286's `>= 30`. It becomes 298's `history_years_in_base >= min_base_years`, clauses (1)–(5) of D-09-25-177.
3. **Serve the count**, per pantry §4.3: new fields, which this lane was barred from adding.
   - **Curves**, in `CURVES_SQL`'s select list and `vintage()`: `base_years`, `base_fewest_stations`, `base_by_station`, `threaded_base` (from `d.`), and `min_base_years`.
   - **Cells**, in `PLACES_SQL`, the `vv.` columns, `CELL_KEYS` and `_cell()`: `min_base_years`, `base_reason`, `base_by_station`, `base_fewest_stations`, `threaded_base`.
   - The label and the band sentence already carry the words ("on N base years, not CPC's 30"). They ride verbatim.
4. **What changes in bodies** under v2:
   - The 13 short places get curves; KDEN's `not_written` becomes a curve.
   - Pooled bands under 30 become `none` / not drawable.
   - `verdict_versions` carries the v2 version.
   - All seven of d091679's banked vectors and its v1 facts move: T2's 312 cells / [22, 24, 26] / `under_30_base_years`, T5's version literal, and T1's KDEN absence. Bank new vectors for the dashboard lane from real v2 rows.
5. **This lane's tests.** P1 asserts that no v2 word reaches a body, and P3 / P4 assert `cpc_curves_v1`. Restate them for v2: v1 rows beside v2 change nothing.
   - Replace the constructed v2 rows in `load_bank_d091691.py` with real ones read from Neon after the backfill.
   - Keep the v1 rows as the "other version beside".
6. **Re-take the plans** with the v2 pin:
   - `iss` walking past v1-only days;
   - `cpc_curve_verdicts` at 1,440+ rows, so its scans double: the `nv` lateral, PLACES_SQL, and the view's DISTINCT ON `newest` CTE, once per issuance at n = 14. That is d091679 §8.2, still open in pantry.
7. **Still open, not this lane's:** d091679 §8.1, the status view that would end the base-table reads. With it, the pin would sit in one view predicate.
8. **The 404 set** (`known_places`) comes from the verdicts in force. v2 scores the same 30 places, so there is nothing to do unless pantry adds or drops a place.

## 4. What this brief got wrong

1. **The list of pin sites missed one.** `CURVES_SQL`'s `iss` CTE reads `cpc_outlook_curves` for the product's n newest issuances, with no method filter. Without a pin there, a v2-only issuance newer than every v1 one becomes `/curves`' newest frame, and that frame is `not_written` under a pinned `w`. This is rehearsal break 1, red in P1 and P3. It is pinned.
2. **"P2: with the pin removed, P1's fixture duplicates days on /curves."** Not with the v2-only newer issuance in the fixture. Main's `/curves` then serves that issuance alone as the newest, as a v2 curve: KDEN draws "…, on 24 base years, not CPC's 30" where main's body says `not_written`.
   - Days duplicate on `/curves` when v2 sits beside the newest v1 issuance and nothing newer: the `v2_beside_only` bank, 10 and 14 days instead of 5 and 7.
   - They also duplicate on `/curves/vintages` in both banks.
   - Cells duplicate on `/places` (1,440).
   - P2 tests each case.
3. **"Byte for byte against d091679's vectors."** d091679 banked its vectors from JSON fixtures, where a whole-valued `DOUBLE PRECISION` is an int (`"min_n_eff":30`, `"tavg_p05":65`). From Postgres, psycopg returns a float and the route prints `30.0`.
   - Main's real bytes over the same rows differ from the vectors in exactly those numbers and nowhere else. Measured: 210 in `body_curves_ksan.json`, 1,718 in the n=14 vintages body, 0 in KDEN's. `test_P0_whole_numbers_are_the_only_difference_from_the_vectors` holds those counts.
   - So P1 holds bytes against main's own bytes, and the vectors with `N.0` read as `N`. JSON is equal everywhere.
   - This is main's behaviour today, not a change here. The dashboard lane should parse numbers, not compare text.
4. **"d091679's banked vectors pass untouched."** The bodies do. But two of d091679's tests pin SQL text and plan receipts, so they must move with any SQL change. They now read this lane's receipts for the three pinned reads; no other line of d091679's file changed.
5. **"The verdict laterals (nv, vv) … every (place, season, strength) has two cells."** That holds for `/places`. In `/curves`, `nv` and `vv` key on `w.method_hash`, so once `w` is pinned they return v1 only, and the view lateral was already tied to `w.method_version`. The duplication there comes from two `w` rows per issuance. All three are pinned anyway, per decision 1. Rehearsal breaks 3–5 show that only P4 can see them; no body can.
6. **"The plans stay index reads."** Two reads never were: every CURVES_SQL and PLACES_SQL scans `cpc_curve_verdicts` whole (720 rows) on main too. I read STOP-P as "becomes", and nothing becomes one.
7. **"On the branch this session is assigned."** No branch was named; this is `claude/cpc-api-method-pin-d091691`.
8. **Pantry §4.1 suggests a bound `%(method_version)s`.** The architect ruled a constant, and I used it as a literal: the planner sees the value, and P4 reads it from the source.

## 5. Tests

`tests/test_cpc_method_pin_d091691.py`, 48 tests. The bank is `tests/load_bank_d091691.py` with `tests/fixtures/cpc_method_pin_d091691/ddl_286_298.sql`: pantry 286 §1–3 and 298 §1–4, verbatim. Its view's `md5(pg_get_viewdef)` equals Neon's: `66966f90…`.

**Three databases in one cluster:**
- **`v1`:** d091679's bank, rebuilt as base rows from its sha-checked fixtures.
- **`v2`:** the v1 rows, plus the constructed v2 rows: every one of the 720 cells as a v2 version; window rows and days at every banked issuance for KSAN, pnw × 2 and KDEN; and a v2-only issuance (610temp 10-10, 814temp 10-09). Every row passes 298's CHECKs.
- **`v2_beside_only`:** the same without the newer issuance.

| tests | what they hold |
|---|---|
| P0 ×15 | main's and the pinned SQL over `v1` serve d091679's bodies; the only byte difference is `N.0`; the v2 rows pass pantry's CHECKs and the view serves both vintages; the view is Neon's |
| P1 ×13 | over `v2` and `v2_beside_only`, every route's bytes are main's; no v2 word reaches a body |
| P2 ×10 | main's SQL over the same banks: days ×2 on /curves and /curves/vintages, 1,440 cells and two versions on /places, the v2-only issuance as newest, KDEN drawn from v2; every body differs from main's |
| P3 ×4 | the v2-only issuance is not /places' newest, nor /curves', nor in /curves/vintages |
| P4 ×4 | one constant, one literal; every reference to the three relations carries `<alias>.method_version = '<the constant>'`; main's statements fail the same rule |
| P5 ×2 | the receipts ran the pinned and main's SQL; warm ≤ 3× d091679; no scan of `cpc_outlook_curves`; verdict scans equal main's |

**Results:**
- **Red** (`red_on_main.txt`): against main's `cpc_outlooks.py`, **20 failed, 28 passed**. P0, P2 and the static P5 check pass on main by design.
- **Green:** this file 48 passed. Both CPC files 129 passed. Full suite **2,952 passed**: baseline 2,904, plus 48 (`green.txt`).
- **Rehearsal** (`rehearse.py` → `reds.txt`): **13 breaks, 13 red**, clean tree green, and the sha-256 of the five files identical before and after. There are ten breaks for the pin sites, two for the constant and one for a hash substituted for the pin.
  - The first run found two faults in the tests, both fixed:
    - P4 matched `v.method_version` inside `lv.method_version`, so "PLACES_SQL cells unpinned" stayed green. P4 now anchors the alias.
    - A failing `x not in <629 KB body>` assertion took 5½ minutes to render its diff.

## Files

- `cpc_outlooks.py`: the constant and the pinned statements.
- `tests/test_cpc_method_pin_d091691.py`, `tests/load_bank_d091691.py`, `tests/fixtures/cpc_method_pin_d091691/` (DDL, README).
- `tests/test_cpc_outlooks_d091679.py`: the two T8 tests read `PIN_RECEIPTS`.
- `docs/receipts/cpc-api-method-pin-d091691/`: `explains.sql`, `make_explains.py`, `extract_plans.py`, `plans_raw.txt`, `plans.md`, `pinned_sql.json`, `red_on_main.txt`, `green.txt`, `rehearse.py`, `reds.txt`.
