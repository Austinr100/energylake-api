# Handback d091667: the asset page reads a plant's run history and the filled wind equipment

**Lane:** d091667 · **Repo:** energylake-api · **Branch:** `claude/asset-runs-wind-equipment-d091667`, from main at `cdd1cf1`.
**Scope:** branch only. No PR, no merge, no deploy, no migration.
**Rulings:** D-09-25-167 (the run before), D-09-25-171 (eight runs kept), D-09-25-173 (a source switch is a new series), D-09-25-76 (source text verbatim), D-09-25-75 (one read per question, memo, timeout).
**Neon:** read-only through the connector, 2026-10-08 20:32–21:00Z. Every statement was a `SELECT`, an `EXPLAIN` of one, or a `PREPARE`/`EXECUTE` of one; all are in `docs/receipts/asset-runs-d091667/explains.sql`. Nothing was written.
**Pantry, read only:** `energylake-pantry` at `798dd55` (migration 284, `implied_gen/wind.py`, `implied_gen/wind_equipment.py`, the d091647 handback), for what each basis token means.

---

## 0. In eight lines

1. **New route** `GET /api/generation/asset/runs?plant_code=&tech=&n=`.
   - It serves one plant's newest *n* runs (default and cap 8), oldest first, as arrays.
   - Each run is split into one series per `weather_source`.
   - Every cycle in the window that is not served is listed with its reason.
2. **The read:** one statement, 1.06 ms (wind) / 1.08 ms (solar) on Neon at 3 runs, 33–34 buffers.
   - **Each run prunes to its own partition**, but only because the LATERAL is fenced with `OFFSET 0`. The obvious join walks every partition for every run.
3. **`/api/generation/asset`'s wind `plant` block gains `equipment`:**
   - every basis token verbatim, with one label from one table;
   - the speed band;
   - rotor and hub height, each with a stated absence;
   - `equipment_source` verbatim;
   - the fleet as a list of parts;
   - `animate.can` with `missing` and `why_not`.
4. **311 of 323 plants can animate. 12 cannot**, all for want of a rotor diameter (§4). All 23 of d091647's fills can.
5. **The API never dropped d091647's tokens.** `asset_page.py` already passed all five basis columns through verbatim. It dropped only `equipment_source`, and it carried no labels and no readiness flag.
6. **Production holds no mixed fleet.** The site row holds one machine per plant, and d091647 says so (its per-machine table was proposed, not built). The fleet is served as a one-part list and labelled as such.
7. **Rule 5 found a defect elsewhere:** `/api/generation/wind/vintages` (d091644) served curve-derived wind MW without the ODbL notice. It carries it now, and a sweep over the app's routes holds every wind payload to it.
8. **Tests:**
   - 90 new tests: red on main, green here.
   - 19 one-line breaks in the rehearsal, all red, and the tree restored byte for byte.
   - The whole suite: 2,603 passed.

---

## 1. What the measurements found

### 1.1 The history and its ledger, as they stood at 20:43Z

| tech / model | runs held | init_ts | n_plants per run | rows per run | landed_at − init_ts |
|---|---:|---|---:|---:|---|
| solar_pv / gfs | 3 | 10-08 00Z, 06Z, 12Z | 1,645 | 394,800 | 5.50, 5.45, 5.43 h |
| wind / hrrr_gfs | 3 | 10-08 00Z, 06Z, 12Z | 323 | 77,520 | 3.07, 3.14, 2.97 h |

**After the bank's snapshot:** the wind **18Z run landed at 20:57:37Z** (lag 2.96 h), 14 minutes after it. The bank, the samples and the tests hold the 20:43Z state, where 18Z is `not_yet_landed`.

**Partitioning:** `RANGE (tech, model, init_ts)`, one partition per run (solar 78.8 MB, wind 16.1 MB each). Every partition carries the PK `(tech, plant_code, model, init_ts, target_ts)`.

**The ledger:** never analyzed (`reltuples = −1`), which is what pushes the planner to the bad join in §3.

**Eviction (from the tables' own comments):**
- The ledger keeps the newest eight per `(tech, model)`, and a trigger refuses a ninth.
- An evicted run's partition and ledger row leave together. So **an evicted run leaves no ledger row**: "evicted" is read off a full ledger, not off a row.

**Rows:**
- Every row has `target_ts = init_ts + (lead_h − 1) h` (checked in SQL for both plants: 0 off).
- **Solar rows carry `weather_source` NULL**, all 394,800 per run.
- **Wind rows are `hrrr_80m` at leads 1–48 and `gfs_100m` at 49–240.**
  - `gust_ms` is null on every HRRR hour and present on every GFS hour.
  - `cap_mw_subtracted` is null on every solar row and 0 or more on wind.

### 1.2 The plan for one plant across the runs

All plans are in `docs/receipts/asset-runs-d091667/plans.md`.

- **Static pruning on `(tech, model)` happens** with literals.
  - Under a forced generic plan, initial pruning removes the other tech's partitions (`Subplans Removed: 3`).
- **Pruning on `init_ts` does not happen with the obvious join.** `init_ts` stays a join filter, and each ledger row scans every held partition: 3 × 3 today, 8 × 8 when full; 93 buffers, 4.0 ms.
  - A plain `LATERAL` is flattened into the same plan.
  - `= ANY(ARRAY(subquery))` reaches every index condition but still probes each partition.
- **The route's statement fences the LATERAL with `OFFSET 0`.** `r.init_ts` stays a per-loop parameter, so each run executes its own partition only (each partition `loops=1` under an Append with `loops=3`).

| read | buffers | execution |
|---|---:|---:|
| wind 66923, n = 8 | 33 | 1.06 ms |
| solar 58388, n = 8 | 34 | 1.08 ms |
| wind, n = 1 (older runs `never executed`) | 13 | 0.41 ms |
| wind, generic plan | 33 | 0.79 ms |

### 1.3 The basis tokens, every distinct value, and what `asset_page.py` did with them on main

| column | values (count) | main's `asset_page.py` |
|---|---|---|
| `turbine_model_basis` | eia860_sch3 318, public_record 2, uswtdb 2, none 1 | passed verbatim (`plant.turbine_model_basis`) |
| `n_turbines_basis` | eia860_sch3 318, public_record 2, uswtdb 2, none 1 | passed verbatim |
| `rotor_basis` | uswtdb 288, same_model_uswtdb 16, none 12, model_designation 5, public_record 2 | passed verbatim |
| `hub_height_basis` | eia860_sch3 318, none 3, uswtdb 2 | passed verbatim |
| `curve_basis` | vintage_band 323 | passed verbatim (`plant.curve.basis`) |
| `equipment_source` | NULL 288; a text on each of the 35 plants d091647 handled (29 distinct strings) | **dropped** (never selected) |
| `cut_in_ms` / `rated_ms` / `cut_out_ms` | 4 distinct bands, one per converting curve, on all 323 plants: E48/800 3/14/25, GE120/2750 3/12/25, V100/1800 3/12/20, V90/2000 3.5/12.5/16.5 | passed through, via the catalog |

**Nothing was relabelled, and nothing labelled.** No token was mapped. The flat fields are unchanged on this branch, beside the new block.

### 1.4 What each plant has

| | plants |
|---|---:|
| a rotor diameter | 311 |
| a hub height | 320 |
| a turbine count | 322 |
| a turbine model | 322 |
| a converting curve and its speed band | 323 |
| a rotor **and** a speed band (can animate) | **311** |

**Still empty:**
- **Rotor (12):** the plants in §4.
- **Hub height (3):** SunZia Wind South, SunZia Wind North, West Camp.
- **Count and model (1):** West Camp.

**West Camp Wind Farm (69451):** every equipment column is NULL with basis `none`. Its `equipment_source` states why, verbatim:

> "Vestas's 401 MW order names 89 V150-4.5 MW turbines (2023-12-22); AES's project pages say up to 104 and about 112 turbines; EIA-860M carries 500 MW. Count and the make-up of the other ~99 MW disagree -- nothing filled (STOP-M)."

It converts on GE120/2750 at 95 m like any 2026 plant, so it has a speed band and no rotor.

### 1.5 What a "mixed fleet" row looks like

**There is none.**
- No `turbine_model` contains a separator (`; | + , &`, " and ", or two slashes): 0 of 323.
- No other table holds per-machine rows for these plants.
- d091647's handback §4 says the site row "holds one model, one count, one rotor, one hub height", and proposes a child table `implied_gen_wind_site_equipment`. It is not built.

**What does exist:**
- **Seven `equipment_source` texts** name a model under two USWTDB maker labels (e.g. "GE Vernova GE1.7-100 + GE Wind GE1.7-100"). That is one machine under two names.
- **A blend inside a `uswtdb` rotor:** the rotor is USWTDB's capacity-weighted mean at the EIA id, and **64 of the 288 `uswtdb` rotors are blends** (more than one `t_rd` at that id, read from `atlas_wind_turbines`). The bank holds them as one number, and only a pantry change can split them. The API says so in the label and in `fleet.detail`.

---

## 2. The contract

### 2.1 `GET /api/generation/asset/runs?plant_code=&tech=&n=`

| param | values | default |
|---|---|---|
| `plant_code` | EIA plant code (1–7 digits) | required |
| `tech` | `solar` \| `wind` | required |
| `n` | 1–8 (above 8 refused, never trimmed) | 8 |

```
{ label, tech, plant_code, plant_name, model, figure: "registry", unit: "MW",
  step_h: 1, run_step_h: 6, n, drivers: [..],
  attribution (wind), weather_height_m (wind: {hrrr_80m: 80, gfs_100m: 100}),
  ledger: { table, held, keep: 8, full, oldest_init_ts, newest_init_ts },
  issuances: [                                   // OLDEST FIRST; the run before issuances[k] is issuances[k-1]
    { init_ts, model, method_version, landed_at, n_plants,
      weather_sources: ["hrrr_80m", "gfs_100m"] | [null],
      series: [ { weather_source, t0, lead0, n_hours,
                  implied_mw: [..], outage_mw_subtracted: [..], cap_mw_subtracted: [..],
                  + drivers: solar ghi_wm2, clearsky_ghi_wm2, clearsky_mw, tcc_pct, precip_mm;
                             wind hub_ws_ms, gust_ms } ] } ],
  absent: [ { init_ts, reason, detail } ],       // every unserved cycle of the window, and the next one
  before_oldest: { reason, init_ts, detail } | null,
  absence: null | { reason: "no_run_held" | "plant_in_no_held_run", detail },
  cache }
```

**Arrays:**
- Index `i` of each array is `target_ts = t0 + i h` and `lead_h = lead0 + i`.
- An hour missing inside a series is `null`, never 0, and never closed up.
- Values are as stored: the bank's float, unrounded (rule 3).

**The window** is the newest *n* cycles of the 6-hour ladder, ending at the newest run the ledger holds. Each cycle in it is either an issuance or in `absent`, with one of:

| reason | when, read off the ledger |
|---|---|
| `not_yet_landed` | the cycle after the newest held run, always listed |
| `not_in_ledger` | a cycle between held runs with no ledger row (skipped) |
| `plant_not_in_run` | the ledger holds the run; it has no row for this plant |
| `before_history` | older than the oldest held run, the ledger not full: the history began there |
| `evicted` | older than the oldest held run, the ledger full (8): D-09-25-171 |

**`before_oldest`** says why `issuances[0]` has no run before it:
- `before_history` today;
- `evicted` once the history is full;
- `outside_window` when `n` < held;
- `plant_not_in_run` when no older held run carries the plant.

**Status codes:**

| case | answer |
|---|---|
| plant not in the registry | **404** (no history read is made) |
| nothing held | **200** with `absence` |
| bad params | **400** |
| DB down | **503** |

**Rules held:** a 5 s statement timeout before every read, and the memo described in §3.

### 2.2 `plant.equipment` on `GET /api/generation/asset` (wind)

The existing flat keys are unchanged, and the block is added beside them.

```
equipment: { source_table: "implied_gen_wind_sites",
  turbine_model | n_turbines | rotor_m | hub_height_m:
      { value, unit, basis: { token, status: "known"|"unknown"|"absent", label },
        absence: null | { reason: "not_stated"|"value_null", detail } },
  curve: { turbine_type, hub_height_m, basis: {..}, cut_in_ms, rated_ms, cut_out_ms,
           speed_band_of, cut_out_note, absence: null | { missing, reason, detail } },
  equipment_source: <verbatim text> | null, equipment_source_absence: null | { reason: "column_absent", detail },
  fleet: { parts: [ { turbine_model, n_turbines, rotor_m, hub_height_m } ], stated_as: "one_machine", detail },
  animate: { can, needs: ["rotor_m","cut_in_ms","rated_ms","cut_out_ms"], missing, why_not, speed_band_of } }
```

**Labels** live in one table, `asset_page.BASIS_LABELS`, worded from the writer's own definitions.
- A token not in it is served `status: "unknown"` with `UNKNOWN_BASIS_LABEL`, never mapped to a known one.
- `vintage_band_default_2010`, which the writer can write and production does not hold today, is labelled too.

**`equipment_source`** is detected in the catalog like the drivers (absent before migration 284) and served verbatim. Nothing is derived from it.

### 2.3 Real sample responses (`docs/receipts/asset-runs-d091667/`)

Each was served by this branch's route over the production bank.

**`sample_runs_wind_66923.json`: SunZia Wind South, n = 8**
- 3 issuances (10-08 00/06/12Z), each with 2 series:
  - `hrrr_80m`: t0 = init, lead0 1, 48 h;
  - `gfs_100m`: lead0 49, 192 h.
- `absent` holds five `before_history` cycles (10-06 18Z → 10-07 18Z) and `not_yet_landed` 10-08 18Z.
- `before_oldest` is `before_history`, and the attribution is present.

**`sample_runs_solar_58388.json`: Solar Star 1, n = 8**
- 3 issuances, each one series with `weather_source: null`, 240 h, five drivers.
- The 12Z run's peak `implied_mw` is 280.358. There is no attribution.

**`sample_plant_wind_66923.json`: SunZia Wind South's `plant` block and attribution**

| field | value | basis |
|---|---|---|
| `turbine_model` | "GE Vernova 3.6-154" | `public_record`, labelled |
| `n_turbines` | 674 | `public_record` |
| `rotor_m` | 154.0 | `public_record` |
| `hub_height_m` | `null` | `none`, absence `not_stated` |
| `curve` | GE120/2750 at 95 m, 3 / 12 / 25 m/s | `vintage_band`; `speed_band_of`: the converting curve, not the stated turbine |

- `animate`: `{can: true, missing: [], why_not: null}`.
- `fleet.parts`: one part.
- `equipment_source`: the GE Vernova / EIA *Today in Energy* citations, verbatim.

**Bytes as served** (compact JSON; gzip -6):

| body | raw | gzip |
|---|---:|---:|
| runs, wind SunZia, today's 3 runs | 44,090 | 19,103 |
| runs, wind SunZia, 8 runs (5 synthesised from the live ones) | 112,515 | 27,676 |
| runs, solar Solar Star 1, today's 3 runs | 35,480 | 7,094 |
| runs, solar Solar Star 1, 8 runs (synthesised as above) | 89,689 | 11,102 |
| the asset `plant` block, SunZia | 4,267 | 1,615 |

Most of the wind bytes are `hub_ws_ms` at the bank's full float precision (e.g. 5.247218608856201).

### 2.4 Why a route of its own, not a `runs` block on `/asset`

The brief preferred one round trip. I chose a separate route:

1. **`/asset`'s body is the dashboard's pinned vector** (d091635's `test_V`). Its keys stay as they were, gaining only `plant.equipment`, which (2) requires.
2. **The runs body is 2–2.3× the whole asset body raw** (90–113 KB at 8 runs against 46–49 KB; 2.5–5× gzipped). Holding it in `/asset`'s 256-entry memo would cost ~30 MB.
3. **A failed or slow runs read must not blank the plant card.**

The page fetches both at open, in parallel. Stepping between runs then costs no round trip (D-09-09-P), because all *n* runs arrive in one body.

---

## 3. Plan receipts and TTLs

**Plans:** `plans.md` (summarised in §1.2). The SQL is pinned by `test_H9_the_runs_sql_is_pinned`.

**On a real Postgres** (`test_PG_H9_*`):
- the pinned statement returns exactly the rows the shaper is tested on;
- `n` keeps older runs off their partitions (`never executed`);
- each run's partition runs once;
- removing the fence takes `init_ts` out of every partition's condition.

**Memos:**

| memo | TTL | stale | key | max entries |
|---|---|---|---|---|
| `generation/asset/runs` (new) | 300 s | ≤ 900 s more (D-09-25-138) | `(tech, plant_code, n)` | 64 (≤ ~7 MB) |
| `generation/asset` (unchanged) | 300 s | ≤ 900 s more | `(tech, plant_code)` | 256 |

**Why these TTLs:**
- **Runs memo:** the writers land one run per tech every 6 h (lag 3.0 h wind, 5.5 h solar). So 300 s shows a landed run within 5 minutes. It is also the asset route's TTL, so the card and the panel disagree for at most one TTL.
- **Asset memo:** its new block reads `implied_gen_wind_sites`, which the wind writer refreshes once per issuance (4 a day).

**Statement timeout:** 5 s, `SET LOCAL`, first in each transaction.

**d091608's exact set of D-09-25-138-bounded memos** gains `generation/asset/runs`, as d091635 and d091644 each added theirs.

---

## 4. Plants that cannot animate (12), and what each lacks

Every one lacks a **rotor diameter** (`rotor_basis` `none`). Each has a speed band.

| plant | code | lacks | why, from `equipment_source` (abridged) |
|---|---:|---|---|
| Ridgetop Energy LLC | 10597 | rotor | Nordtank NTK 75/15: no USWTDB rotor, and the name states none |
| Victory Garden (Tehachapi) | 50532 | rotor | Vestas V15: a bare "V<n>" is refused (EIA writes V150 as "V15") |
| East Winds Project | 50820 | rotor | Micon M1500-600/150: no USWTDB rotor |
| Mojave 16/17/18 | 50821 | rotor | Mitsubishi MWT-250: no USWTDB rotor |
| Mojave 3/4/5 | 52142 | rotor | Mitsubishi MWT-250: no USWTDB rotor |
| TPC Windfarms LLC | 54647 | rotor | Danwin 23/160: no USWTDB rotor |
| Difwind Farms Ltd VI | 54686 | rotor | Micon 108: USWTDB holds the model with no rotor |
| Phoenix Wind Power LLC | 55339 | rotor | Gamesa G114-2.0: USWTDB's 114 m refused (0.7 MW per turbine vs a 2 MW rating) |
| ZCO | 56276 | rotor | Vestas V15 (as Victory Garden) |
| Foundation IE | 57792 | rotor | Mitsubishi MWT-1000A: no USWTDB rotor |
| Springfield Wind | 67160 | rotor | GE 1.5 S: no USWTDB rotor |
| West Camp Wind Farm | 69451 | rotor, hub height, model, count | STOP-M: the public sources disagree (§1.4) |

**Can animate but have no hub height:** SunZia Wind South and SunZia Wind North (basis `none`).

---

## 5. Tests

### 5.1 Red, then green (`red_on_main.txt`, `green.txt`)

- **Run 1:** main at `cdd1cf1` plus the test file, its fixture and the bank scripts. Collection fails: `asset_page` has no `EQUIPMENT_SOURCE`.
- **Run 2:** as run 1, plus this branch's `asset_page.py` alone (no route, no vintages fix). **44 failed, 46 passed.**
  - The 46 test the pure module: the equipment sweep, the labels, the pinned SQL, and the PG statements.
  - Every route test, H7 and the H8 vectors are red.
- **Green:** 90 passed here. d091635 + d091644 + d091608: 129 passed. The whole suite: **2,603 passed** (PG tests on a local Postgres 16).
- **One unexplained failure.** One full-suite run of the final tree printed "1 failed, 2602 passed". That run did not capture the failing test's name.
  - Seven reruns of the same tree, with `-rf`, each gave 2,603 passed. This lane's 90 tests passed in every run, including the rehearsal's clean run.
  - I could not reproduce it, so I am not calling it a flake. It is somewhere in the suite, unidentified.

| test | what holds it |
|---|---|
| **H1** | SunZia and Solar Star are served oldest first, every value equal to the banked row (720 each). The page's run-before move on the served arrays equals a hand difference on the raw bank, pinned to 4 dp (table below). No change, sum, gap or "previous" key is in the body. Eight runs (live + 5 synthesised) are default and cap, each 6 h after the one before. `n` reaches the read and the window. `n` = 9, 100, 0, −1, abc or 2.5 → 400 with no read. |
| **H2** | Today's state: 5 × `before_history` + 18Z `not_yet_landed`; every window cycle accounted for once. A full ledger names `evicted`. A skipped cycle is `not_in_ledger`, and `issuances[k−1]` steps over it. A run without the plant is `plant_not_in_run` (with its 323), not a hole. A missing hour is `null` in every array. Nothing held, or no run with the plant, gives an `absence`. |
| **H3** | A wind run is two series split at lead 48 \| 49. The six seam hours (10-10 00–05Z, GFS in 00Z vs HRRR in 06Z) never pair. Solar is one series with the bank's `null`. Every read names one model and one plant. A second source mid-run becomes its own series. |
| **H4** | The bank holds exactly the measured tokens and counts. Every production token on all 323 plants is served verbatim with its table label. Each label carries the writer's meaning (and not another token's). No two tokens of a column share a label. No other module words a label. A made-up token is `unknown`; a null one is `absent`. `equipment_source` is verbatim (35 plants), or `column_absent` before 284. The site read selects it only where the catalog has it. |
| **H5** | Every plant is the one machine its row states, never averaged by the API. The 7 two-label USWTDB texts stay one part. SunZia South and North are two plants of one part each. The `uswtdb` label says "blend". |
| **H6** | Exactly the 12 plants in §4 cannot animate, each `missing: ["rotor_m"]` with the `none` label in `why_not`. All 23 d091647 fills can; 311 can in all. West Camp states all four absences. No speed band says `column_absent` / `all_null`. The band says it is the converting curve's, with V90/2000's table-end note. |
| **H7** | Every generation, asset, wind and net-demand route is classed either "wind figure" or "not, and why" (the sweep fails on an unclassed route). Each wind payload carries `wind_outlook.ATTRIBUTION`: outlook, sites, **vintages (fixed)**, asset, asset/runs (full and empty) and net-demand. |
| **H8** | d091635's and d091644's test files are byte-identical to main and pass. The two re-banked vectors equal main's copies plus exactly `plant.equipment` and `attribution`. The solar vectors are untouched. |
| **H9** | The SQL is pinned. On PG: the rows are the shaper's rows, there is one partition per run, `n` prunes older runs, the fence is load-bearing, and the registry reads work. |
| **C** | Timeout first. Memoised per `(tech, plant_code, n)`; `n=8` is the default's key. TTL and stale cap match `/asset`. 404 before any history read; 400 for bad params; 503 for a dead pool; no `max()` and no `DISTINCT ON`. |

**H1's hand differences** (MWh, the run against the run before, over the hours both hold):

| plant, series | runs | day | hours | Δ |
|---|---|---|---:|---:|
| SunZia South, `hrrr_80m` | 12Z vs 06Z | 10-09 | 24 | **+1,129.2529** |
| SunZia South, `hrrr_80m` | 06Z vs 00Z | 10-08 | 18 | −854.9087 |
| SunZia South, `gfs_100m` | 12Z vs 06Z | 10-13 | 24 | −8,137.3389 |
| Solar Star 1 | 12Z vs 06Z | 10-11 | 24 | **+672.4618** |
| Solar Star 1 | 06Z vs 00Z | 10-10 | 24 | −331.953 |

### 5.2 The bank (`tests/fixtures/asset_runs_d091667/bank_2026_10_08.json`, committed)

**What is in it:**
- the whole ledger at 20:43:13Z;
- SunZia South's and Solar Star 1's rows of all three runs, as per-run arrays;
- all 323 rows of `implied_gen_wind_sites` in the asset route's columns plus `equipment_source`.

**How it is held:**
- Every run carries the md5 Neon computed over its canonical text. The site table is held to a second Neon md5.
- `bank_from_neon.pgtext` reproduces Postgres's float text, and the suite re-checks every md5 on load.
- A hand edit to the bank is a red test.

### 5.3 The rehearsal (`rehearse.py` → `reds.txt`)

**Method:** each break is applied **in place**, the guarding tests run, and the file is written back from its original bytes. The whole tree is hashed before and after.
**Result:** the clean tree is green (90), **19 of 19 breaks are red**, and the tree is restored byte for byte.

| rule | break |
|---|---|
| 1 D-09-25-167 | newest first; a server-side `gap_h`; each series an hour early (the hand difference) |
| 2 D-09-25-173 | one series across the seam; the ledger read loses its model filter |
| 3 verbatim | an unknown token mapped to a known label; `public_record` relabelled as the USWTDB rotor; `equipment_source` trimmed; the source text split into parts on " + "; the rotor animates without a diameter |
| 4 absence | a run without the plant becomes a silent hole; a full ledger's older run is called `before_history`; the next cycle is not listed |
| 5 attribution | dropped from the runs payload; dropped from wind vintages |
| 6 repo rules | no statement timeout; the memo key drops `n`; the LATERAL loses `OFFSET 0`; `n` above 8 is trimmed instead of refused |

**One break was green on the first rehearsal:** relabelling `public_record`. H4 compared each label with the very table the break edited. I added `test_H4_each_label_says_what_the_writer_does` and `test_H4_no_two_tokens_of_a_column_share_a_label`, and the break is red now.

---

## 6. What the dashboard lane must know

### 6.1 To draw the run-by-run panel

1. **Fetch** `/api/generation/asset/runs?plant_code=&tech=` once per page open, beside `/asset`.
   - Default `n` = 8. Keep one `n`: it is the memo key.
2. **Oldest first:** the newest run is the **last** issuance. The run before `issuances[k]` is `issuances[k−1]`, as on the area vintages route.
   - The body carries no move, no gap and no sum (D-09-25-167 clause 4). The page computes them.
   - The gap is `init[k] − init[k−1]`: 6 h today, more where a cycle was skipped (it is in `absent` as `not_in_ledger`).
3. **Compare series with the same `weather_source` only** (D-09-25-173).
   - Wind: `hrrr_80m` with `hrrr_80m`, `gfs_100m` with `gfs_100m`.
   - Align by `t0`, and count an hour only where both arrays hold a value.
   - Each adjacent wind pair has **6 hours where one run is GFS and the other HRRR**. Those hours have no like-for-like move; say so rather than draw one.
   - Solar's series has `weather_source: null`. Compare `null` with `null`.
4. **Draw every cycle in `absent` as an absence with its `detail`, never as a gap in the scrubber.**
   - Today that is five `before_history` cycles: the history began 10-08 00Z and fills over two days.
   - The pending `not_yet_landed` cycle is also listed. Wind's 18Z landed at 20:57Z; the bank predates it.
5. **The per-day move:** the hand differences in §5.1 are the vector. The page's sum over shared hours must reproduce them to 4 dp.
   - Day boundaries are the page's call. The vector uses UTC days; the outlook pages use Pacific days.
6. **Values are unrounded**, as stored. Round for display only.
7. **Drivers per hour sit in the same series:**
   - solar: GHI, clear-sky GHI, clear-sky MW, cloud %, precipitation;
   - wind: hub-height speed, and gust on GFS hours only (`null` on HRRR by design).

### 6.2 To turn the rotor

1. **Read `plant.equipment.animate`.** Turn the rotor when `can` is true; otherwise print `why_not`.
   - Decide nothing from tokens: the server has already decided from the bank, and 311 of 323 plants can turn.
2. **Diameter:** `equipment.rotor_m.value`.
   **Speed band:** `equipment.curve.cut_in_ms` / `rated_ms` / `cut_out_ms`.
   - The band is the **converting curve's**, not the stated turbine's. SunZia's GE 3.6-154 turns on GE120/2750's band.
   - Where `cut_out_note` is set (V90/2000, V100/1800), the cut-out is the curve table's end, not the maker's.
3. **Speed per hour:** `hub_ws_ms`, from `/asset`'s `hours` or from the runs' series.
   - Its height is `weather_height_m[weather_source]`: 80 m HRRR, 100 m GFS.
4. **Print the basis `label`**, not the token. An `unknown` status must print as unknown.
5. **`hub_height_m` can be `null`** with `absence.reason: "not_stated"` (SunZia ×2, West Camp). The tower drawing must not invent one.
6. **`fleet.parts` is a list** with one part today. Draw parts, not an average. If pantry builds the per-machine table, more parts arrive in the same key.
7. **Print the `attribution`** on any panel with a wind figure. Every wind payload now carries it.

---

## 7. What this brief got wrong

1. **"the per-day move … state the gap in hours" contradicts the ruling it cites.**
   - D-09-25-167 clause 4, as the area route implements it: "no change, sum or spacing is computed here."
   - Computing them server-side would be the second definition that rule 1 forbids. The move and the gap are page arithmetic, held by H1's hand differences instead.
2. **"newest first" (H1) contradicts the area contract.** The area vintages route is oldest first, with the run before at `k−1`. Serving newest first would move the run before to `k+1`, a second definition. The runs are served oldest first.
3. **"per run, … weather_source"** does not hold for wind.
   - A wind run carries two sources (HRRR 1–48, GFS 49–240) under one model, and the ledger has no `weather_source` column.
   - D-09-25-173 is met by splitting each run into series, not by a per-run field.
   - Solar rows have no source at all (`NULL`), which is served as is.
4. **"mixed fleets" are not in the table.** d091647 filled one machine per plant and proposed, but did not build, a per-machine table. "A mixed fleet as a list of its parts" is served as a one-part list, with the reason stated.
   - The real averaged turbines are 64 `uswtdb` rotors that blend more than one rotor diameter. Splitting them is a pantry change.
5. **"the API and the page do not read the new tokens"** is half right.
   - `asset_page.py` passed every basis token through verbatim on main.
   - It dropped `equipment_source`, labelled nothing, and gave no readiness flag. The rotor not turning is the page's gate.
6. **"Hub height is still empty on some plants" is 3 plants** (SunZia ×2, West Camp). d091647 filled no hub height, by its own scope.
7. **"Evicted past eight" never appears inside a window of n ≤ 8.** The ledger deletes an evicted run's row, and with n capped at 8, the window cannot reach past the eighth.
   - "Evicted" is therefore stated in `before_oldest`, once the ledger is full.
   - The reason is read off the ledger being full, not off a row, because the row is gone.
8. **"GET /api/weather/asset/runs":** the asset route lives at `/api/generation/asset`, so the runs route is `/api/generation/asset/runs`.
9. **"H8 the existing asset tests pass unchanged":** the test files are unchanged and pass. Two vectors had to change:
   - `test_V` pins the whole wind body, so (2)'s new block required re-banking `asset_wind_57514.json`;
   - rule 5 required re-banking d091644's `body_wind_ciso_n28.json`.
   - Both were re-banked additively from main's copies (`rebank_vectors.py`), as d091608 re-banked earlier lanes' receipts. A test holds each to main's copy plus exactly its new key.
   - d091608's exact set of bounded memos gained one name, as it did for d091635 and d091644.
10. **"eight runs" for H1 cannot be live yet:** production holds three per tech. H1 runs on the three live runs, and the eight-run cases synthesise five older runs from the live rows.
11. **Rule 5 caught an existing defect:** `/api/generation/wind/vintages` (d091644) served wind MW without the ODbL notice. It is fixed here and held by the H7 sweep.

---

## 8. Not done, or for later

- **Pantry:**
  - **The per-machine equipment table** (d091647 §4), if mixed plants are to be drawn as parts. The API's `fleet.parts` is ready for it.
  - **`ANALYZE implied_gen_site_history_runs`**, or a writer-side analyze. The fence makes the route independent of it, but other readers of the ledger would plan better.
  - **Hub height for SunZia** (d091647's STOP-N: the sources are denied to its lane).
- **Not served:** the GFS init behind a wind run's hours 49–240. The writer records it nowhere (the ledger comment says so), so the API cannot state it.
- **This lane's container** reached Neon only through the connector. The bank went from the connector's saved results to the fixture via `bank_from_neon.py`, without a database URL.

*Sources:*
- this repo: `asset_page.py` (equipment, runs), `main.py` (the `/asset/runs` route, `_asset_build`'s catalog read), `vintages.py` (attribution);
- tests: `tests/test_asset_runs_d091667.py`, `tests/fixtures/asset_runs_d091667/`, `docs/receipts/asset-runs-d091667/*`;
- pantry: `migrations/284_wind_sites_equipment_bases.sql`, `implied_gen/wind.py`, `implied_gen/wind_equipment.py`, `docs/handback_2026_10_07_wind_equipment_gaps.md`.
