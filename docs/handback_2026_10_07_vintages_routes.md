# Handback d091644: the vintages routes (every held run of one curve, in one read)

**Lane:** d091644 · **Repo:** energylake-api · **Branch:** `claude/vintages-routes-d091644-e3qyku`, from main at `f5da373`. Branch only: no PR, no merge, no deploy, no migration.
**Charter:** `arc_charter_2026_10_07_forecast_desk.md`, step 1. **Stands on:** `docs/recon_2026_10_07_vintage_player.md` (d091639). It is on main, and this lane builds its §3.2 contract.
**Ruling:** D-09-25-167. The server ships the curves and the page does the arithmetic, so no route here computes a change, a sum or a gap.
**Neon:** read-only through the connector, 2026-10-07 ~15:40–16:50Z. Every statement was a `SELECT` or an `EXPLAIN (ANALYZE, BUFFERS)` of one. Nothing was written to Neon or R2.

---

## 0. In six lines

1. **Three routes** are built:
   - `GET /api/generation/solar/vintages`
   - `GET /api/generation/wind/vintages`
   - `GET /api/weather/dd/forecast/regions/vintages`
2. **Curve reads:** ≤ 4.5 ms warm at n = 28 and 36.7 ms on a first read from storage, with no sequential scan. The gate's line lookup adds 0.06–0.9 ms.
3. **Degree-day reads:** ≤ 12.1 ms warm per source. STOP-V (500 ms warm) is not reached. The cost is ~420 buffers per issuance held, so it grows.
4. **Payload at n = 28, as served:** solar CISO is 39.6 KB raw / 8.5 KB gzip, wind CISO 37.9 / 11.5. The degree-day body, everything held for pnw (40 issuances), is 15.3 / 2.5.
5. **Tests:** 51 new tests, red on main, green here. 17 one-line breaks, all red. The whole suite: 2,511 passed, 2 skipped.
6. **Two things in the brief could not be done as written** (§6):
   - The gate's lines live in another table, so a route makes one curve read plus the outlook's own line lookup.
   - The view nulls an incomplete day's value, so "keeps its value" serves the view's null with `false`.

---

## 1. The routes as built

### 1.1 `GET /api/generation/solar/vintages` and `GET /api/generation/wind/vintages`

They sit beside `/api/generation/{solar,wind}/outlook` and take the same parameters, parsed by the same functions (`solar_outlook.parse_area` / `parse_model`, `wind_outlook.parse_model`):

| param | values | default |
|---|---|---|
| `area_kind` | `hub` \| `hub_sum` \| `ba` \| `state` | required |
| `area` | NP15/ZP26/SP15 for hub; HUBSUM (or omit) for hub_sum; a BA or state code | required (except hub_sum) |
| `model` | solar `gfs`; wind `hrrr_gfs` | the one model |
| `n` | 1–120 | **28** |

```
{ "tech": "solar_pv" | "wind", "area_kind", "area", "model", "unit": "MW", "step_h": 1,
  "issuances": [                              // oldest first; the newest is last
    { "init": "2026-10-07T06:00:00+00:00",
      "t0":   "2026-10-07T06:00:00+00:00",    // target_ts of index 0
      "lead0": 1,                              // lead_h of index 0
      "reg": [int | null, ...],                // registry MW, one per hour from t0
      "cal": [int | null, ...] | null,         // what the outlook would SHOW as calibrated
      "method_version": "solar_pv_v1" } ],
  "absence": null | { "reason": "no_issuance", "detail": "..." },
  "cache": { "state", "built_at", "age_seconds", "ttl_seconds", "build_seconds", "refreshing" } }
```

- **Indexing:** index `i` is `target_ts = t0 + i h` and `lead_h = lead0 + i`. The arrays end at the issuance's last held hour.
- **Holes:** an hour missing inside an issuance is `null` in both arrays, never 0 and never closed up. Production has no hole today: every CISO issuance is checked whole in SQL.
- **Rounding:** whole MW, half away from zero (`vintages.whole_mw`, `Decimal` `ROUND_HALF_UP` on the float's exact value).
- **`cal`:** produced by `solar_outlook.gate`, the outlook routes' own gate (D-09-25-136), over the lines the outlook reads with its own statement (`solar_outlook.CALIBRATION_SQL`).
  - At an hour whose line was not fitted on its lead it is `null`.
  - For an issuance with no shown calibrated hour it is `null` as a whole.
  - There is no second gate.
- **Not in the body:** scores, actuals, fleet, per-hour objects and changes.
- **The read:** recon §2.1 (a), a recursive walk down the PK naming one `(tech, area_kind, area, model)`. It has no `max()` and no `DISTINCT ON`. Then the outlook's line lookup by `calibration_id`. Both run in one transaction under `SET LOCAL statement_timeout = '5s'`.
- **The memo:** `_DDCache` as the outlook routes use it: TTL 300 s, stale ≤ 900 s more (D-09-25-138), build timeout 15 s. It is keyed on `(tech, area_kind, area, model, n)`.
  - **The lock is per cache, not per key, and I left `_DDCache` unchanged**, as the brief said to. Cold builds for different areas therefore queue behind each other. At ≤ 37 ms per build, that does not matter today.
- **Errors:**
  - A malformed or out-of-list area, an unknown model, or `n` outside 1–120 → **400**. `n` above 120 is refused, not trimmed.
  - A well-formed area with nothing banked → **200** with `absence` and `issuances: []`.
  - DB down or build over 15 s → **503**.

### 1.2 `GET /api/weather/dd/forecast/regions/vintages`

| param | values | default |
|---|---|---|
| `region` | one region id under the weighting | **required** |
| `weighting` | `population` \| `load_share_365d` | `population` |

```
{ "region", "weighting",
  "sources": [                                 // always GFS, IFS, AIFS, gridpoints_raw, in that order
    { "source_product", "label",               // label: "NWS" for gridpoints_raw (degree_days.SOURCE_LABELS)
      "issuances": [                           // oldest first
        { "issued_ts", "d0",                   // d0: target_date of index 0
          "hdd": [float | null], "cdd": [float | null],   // to 0.01, half away from zero
          "basis_complete": [bool | null],
          "sample_step_h": [int | null] } ] } ],   // the view's sample_spacing_hours — see §5
  "absence": null | { "reason": "no_issuance", "detail" }, "cache": {...} }
```

- **One read per `(region, weighting, source_product)`** of `v_degree_days_region_forecast`. The four run concurrently (`asyncio.gather`), each under the region board's 2 s statement timeout (`_dd_timed_read`).
- **The memo:** `_DDCache`, 300 s, keyed `(region, weighting)`, build timeout 10 s.
- **Validation:** `region` and `weighting` are checked against `degree_day_region_weights`, through the region board's own vectors memo.
  - An unknown or missing one → **400** naming the field.
  - Nothing held → **200** with `absence`.
- **An issuance is the view's `issued_ts`.** For the NWS leg that is a **fold**: the newest member stamp of one `fold_ts`, as the view defines it. pnw has 5 folds and serves 5 issuances; the stamps equal `max(issued_ts)` per fold, read from the folds table and not from the view (D1).
- **An incomplete day** is served as the view serves it: `basis_complete: false`, values `null` (§6 item 2). It is never dropped and never filled.

### 1.3 Files

| file | what |
|---|---|
| `vintages.py` (new) | Both reads' SQL and the shaping, plus the rounding (`half_away`, `whole_mw`, `dd01`) and `DD_SOURCES`. |
| `main.py` | The three routes and their memos. Two sections: after the wind outlook section, and after `/forecast/regions`. No existing route changed. |
| `tests/test_vintages_d091644.py` (new) | V1–V6, D1, D2, the route contract. |
| `tests/test_outlook_fit_d091608.py` | `test_A7`'s exact set of D-09-25-138-bounded memos gains the two vintages memos, as d091635 did for the asset memos. |
| `tests/fixtures/vintages_d091644/` | The production bank (§3.2). |
| `docs/receipts/vintages-d091644/` | Plans, bytes, banked bodies, reds, greens, the rehearsal. |

---

## 2. Receipts (`docs/receipts/vintages-d091644/`)

`plans.md` has the tables and `plans_raw.txt` every plan line. In summary:

| read | n | rows | buffers | first | warm |
|---|---:|---:|---|---:|---:|
| solar CISO curve | 4 / 12 / 28 | 960 / 2,880 / 4,632 | 65 / 217 / 424 | 1.4 / 2.9 / 4.5 ms | **1.0 / 3.0 / 4.5 ms** |
| wind CISO curve | 4 / 12 / 28 | 960 / 2,880 / 4,110 | 75 / 202 / 374 | 1.3 / 3.4 / 4.2 ms | **1.1 / 2.7 / 4.1 ms** |
| solar **AZPS** curve (first read from storage) | 28 | 4,632 | 190 hit / 179 read | **36.7 ms** | 4.5 ms |
| the gate's lines (outlook statement) | 28 | 18 / 10 | 7 / 6 | 0.9 / 0.14 ms | 0.06 / 0.09 ms |
| DD pnw/population GFS / IFS / AIFS / NWS | all held | 192 / 192 / 176 / 43 | 5,064 / 5,223 / 4,854 / 1,231 | 13.3 / 11.5 / 11.2 / 8.4 ms | **12.1 / 11.5 / 11.6 / 2.9 ms** |

**On "first":**
- The CISO and pnw "first" runs are not cold. This lane had already banked those rows, so their pages were in cache.
- AZPS is the honest first read, at 36.7 ms. The recon's 96.6 ms (N = 28, CISO) is the nearest to compute-cold.

**Bytes as served** (compact JSON, gzip -6; `sample.py` serves the real routes over the bank):

| route | n | raw | gzip |
|---|---:|---:|---:|
| solar CISO | 4 / 12 / 28 | 8,878 / 26,183 / 39,596 | 2,106 / 5,459 / 8,544 |
| wind CISO | 4 / 12 / 28 | 9,935 / 28,965 / 37,907 | 3,127 / 8,483 / 11,511 |
| DD pnw/population, every source | all 40 | 15,259 | 2,535 |

**Banked bodies** (the dashboard lane's vectors, D-09-05-T):
- `body_solar_ciso_n28.json`, `body_wind_ciso_n28.json` and `body_dd_pnw_population.json`. Each is real: the route served it over production rows.
- Each is held equal to the route's output by a test.

---

## 3. Tests

### 3.1 Red, then green

**`red_on_main.txt`, run 1:** main at `f5da373` plus the test file and fixtures. Collection fails: `ModuleNotFoundError: No module named 'vintages'`.

**Run 2:** as run 1, plus `vintages.py` alone (no routes in main.py). **49 failed, 2 passed.** The two that pass test the pure module only: `test_V1_whole_mw_is_half_away_from_zero` and `test_the_read_has_no_max_and_no_distinct_on`.

**`green.txt`:** **51 passed** on this branch. The PG tests ran against a local Postgres 16. The whole suite gives **2,511 passed, 2 skipped**.

| test | what holds it |
|---|---|
| **V1** | A 66-hour (10-01 12Z) and a 240-hour (10-03 12Z) issuance side by side. For every row, `t0 + i` is its target_ts and `lead0 + i` its lead_h, and `reg[i]` is its registry MW rounded half away from zero. Registry values are x.5 on an even whole number, so a banker's round is caught. Also over the real SQL (`test_PG_V1_V3`). |
| **V2** | The 66-hour issuance has `cal: null`. The 240-hour one has `cal` at leads 1–21 only: line 11 rides leads 7–24 but was fitted on 7–21, and line 12 has no fitted leads. `cal` equals the outlook route's `calibrated_mw` hour for hour, both over the fake pool and over a real Postgres (`test_PG_V2`, all five gfs issuances through both routes' own SQL). |
| **V3** | Holes at leads 5 and 30 come back `null` in both arrays. The arrays stay 240 long, and the hours after each hole are their own. |
| **V4** | Oldest first. `n` reaches the read (default 28). `n` = 121, 1000, 0, −1, abc or 2.5 → 400, with no read made. Over PG: `n = 3` gives the newest three gfs runs, and `n = 120` gives every gfs run and never the ifs run seeded between them. |
| **V5** | `ba/ZZZZ` (solar and wind) and `state/WY` → 200, `issuances: []`, `absence.reason = "no_issuance"`; over PG too. Malformed areas → 400. |
| **V6** | (a) Over the production bank, the newest issuance (solar 10-07 06Z, wind 10-07 12Z) equals the outlook route served on the same rows, hour for hour, `reg` and `cal`. (b) **The banked outlook receipts** — `solar-outlook-api-d091568/sample_outlook_ciso.json` (10-04 18Z) and `wind-outlook-api-d091590/sample_outlook_ciso.json` (10-05 00Z), served by the outlook route on their own days — each equal their vintage in today's bank, registry and calibrated, all 240 hours. |
| **D1** | Every one of the 603 banked view rows (pnw/population, 4 sources, 40 issuances): its value to 0.01 is at its `(source, issued_ts, target_date)` index, with its `basis_complete` and spacing. The NWS issuance count (5) and stamps equal the folds read from `station_degree_days_forecast_folds`. |
| **D2** | An incomplete first and last day carry the view's `null` and `false`. A missing middle day is `null` in all four arrays, and the alignment holds after it. |
| route | Statement timeout first. One curve read naming one area. The memo keyed on `n` (miss, hit, miss). One DD read per source, each after its own `SET LOCAL`. DD 400s naming the field. 503 on a dead pool. |

### 3.2 The production bank (`tests/fixtures/vintages_d091644/`)

- **The bank:** `VINTAGES_SQL`'s rows for ba/CISO solar and wind at n = 28, `DD_VINTAGES_SQL`'s rows for pnw/population, and the calibration lines they carry.
- **Integrity:** every issuance's text is exactly as Postgres printed it, held against an md5 Neon computed (`manifest_*.psv`, `verify_raw.py`, and again in `load_bank`). The lines are held against a Neon md5 of their key columns.
- **What is left out:** the bank stores `(lead_h, registry_mw, calibrated_mw, calibration_id)` per hour, not `target_ts`. `target_ts = init_ts + (lead_h − 1) h` holds for every CISO row on Neon (checked in SQL: one offset, 6,216 solar and 5,232 wind rows).

### 3.3 The rehearsal (`rehearse.py` → `reds.txt`)

Seventeen one-line breaks, each on a throwaway copy of the tree. Every one is **RED**, and the clean tree is **GREEN** (51 passed).

| tag | break | red |
|---|---|---|
| V1 | `lead0` counted from 0 | 1 failed |
| V1 | banker's rounding | 1 failed |
| V2 | cal ungated (stored calibrated at every lead) | 3 failed |
| V2 | an uncovered issuance ships a list of nulls | 1 failed |
| V3 | a missing hour served as 0 | 2 failed |
| V4 | newest first | 2 failed |
| V4 | n above 120 accepted | 2 failed |
| V4 | the walk steps over another model's run (d091557) | 1 failed (PG) |
| V4 | n never reaches the read | 1 failed |
| V5 | no absence | 4 failed |
| V6 | reg carries the calibrated figure | 4 failed |
| D1 | 0.1 not 0.01 | 1 failed |
| D1 | the 08-29 orphans dropped (an NWS fold lost) | 1 failed |
| D2 | incomplete day dropped | 1 failed |
| D-09-25-75 | statement timeout dropped | 2 failed |
| memo | key drops n | 1 failed |
| one series | DD read stops naming its source | 1 failed |

---

## 4. Held today (what the bodies show, 2026-10-07)

### Solar ba/CISO, n = 28

- Inits run 09-20 12Z → 10-07 06Z: **12 × 66-hour, then 16 × 240-hour**.
- **Spacing between held runs**, in hours, oldest first: 18, 30, 24 ×7, 18, 30, **48** (10-01 12Z → 10-03 12Z), then 6 ×15.
- The 12 backfill issuances have `cal: null`. The newest 240-hour issuance shows calibrated on 66 of its 240 hours.

### Wind ba/CISO, n = 28

- Inits run 09-18 06Z → 10-07 12Z: **15 × 66-hour, then 13 × 240-hour**.
- **Spacing:** 24 ×14, **54** (10-02 06Z → 10-04 12Z), then 6 ×12.

### Degree days, pnw / population

40 issuances:

| source | issuances | spacing between issuances (h) |
|---|---:|---|
| GFS | 12 | 6, **816**, then 18 / 6 alternating |
| IFS | 12 | **816**, then 12 |
| AIFS | 11 | **828**, then 12 |
| NWS | 5 | **818**, 38, 28, 12 |

- The 08-29 orphans are in every source. The 33-day hole after them is real and is served as is. Clause 3: the page states it.
- Each model issuance is 16 days long. The NWS ones are 8–9 days.

---

## 5. What the dashboard lane must know

1. **The two kinds show as array length.** `reg.length === 66` is the once-a-day backfill and `240` is the 6-hourly run; `lead0` is 1 for both today. There is no flag; the length is the fact.
   - Oldest first, so the backfill is on the left and the newest run is the **last** element.
   - D-09-21-01, `AxisScrubber` reads a missing value as index 0 (the oldest): use the `skyRadar.radarPlayheadAfterSlide` "null means newest" pattern.
2. **The run before** (D-09-25-167 clause 1) is `issuances[k−1]`. Its hours apart are `init[k] − init[k−1]`.
   - The body carries no spacing between runs. **Clause 3 gaps are the page's to state:** solar 48 h (10-01 → 10-03), wind 54 h (10-02 → 10-04), and every 18 / 24 / 30 h step of the backfill.
3. **Shared hours** (clause 2): align by `t0` (both arrays are hourly from their own `t0`). A pair shares an hour only where both values are non-null.
   - A backfill pair shares at most 66 − spacing hours: 18 hours for a 48-hour gap.
   - The like-for-like rule of `_previous_mw`: compare `cal` with `cal` where the current run shows `cal`, otherwise `reg` with `reg`. That is page arithmetic on the two arrays.
4. **`cal` is `null` for a whole issuance** when its own line covers none of its hours (every backfill issuance today), and `null` per hour beyond its fitted leads.
   - A non-null `cal` is what the outlook page shows for that issuance and hour.
5. **Whole MW** (half away from zero). Stamps are ISO with `+00:00`, not `Z`; the recon's sketch used `Z`.
6. **`sample_step_h` on the degree-day body is NOT the hours between issuances.**
   - It is the view's `sample_spacing_hours`: the model's step on that day (GFS 6 or 3, NWS 1), or `null` where members differ.
   - The brief named it `spacing_h`. It was renamed to `sample_step_h` before merge so it cannot be read as run spacing; no body with the old name was ever served.
   - Hours between issuances come from `issued_ts`, as in item 2.
7. **An NWS issuance's `issued_ts` is the newest member stamp of a fold, not the fold time.** The 10-06 fold ran at 17:26Z but is stamped 00:41Z. Its `d0` can be the day before (10-05), with that first day incomplete.
8. **Incomplete days arrive as `null` with `false`** (§6 item 2). Every model issuance's first day is partial, and so are many last days. Draw by `basis_complete`, not by null-ness alone.
9. **Fetch once per page open.**
   - `?n=` changes the memo key, so pick one `n` (28 is the default) and keep it.
   - The vintages body is the issuance list. `?init=` on the outlook still serves one issuance's detail.

---

## 6. What this brief got wrong

1. **"One read per call" and "reuse the outlook's gate" pull against each other.** The gate needs each row's line and its fitted leads, which live in `implied_gen_calibration`.
   - I measured the one-statement way first: joining the lines into the walk costs **108 ms first at n = 28**, through a `Seq Scan on implied_gen_calibration` (762 rows) and a hash join.
   - The route instead runs the curve read and then **the outlook's own `CALIBRATION_SQL`**, a PK lookup of ≤ 18 ids (0.06–0.9 ms), in one transaction under one timeout.
   - The curves are one read; the lines are the outlook's statement, reused, not a second gate.
2. **"An incomplete day keeps its value" cannot hold.** `v_degree_days_region_forecast` returns `hdd_wtd` / `cdd_wtd` as NULL whenever `basis_complete` is false (pantry 202; production: 0 of 65 incomplete pnw rows has a value). The route serves the view's null with its `false`.
   - Serving the raw partial sum means exposing `*_wtd_raw` in the view. That is a pantry change.
3. **"spacing_h" named something else.** The brief's body named the view's `sample_spacing_hours` as `spacing_h`, which a reader would take for run spacing. It is served as **`sample_step_h`**, renamed before merge (§5 item 6).
4. **The degree-day route has no `n`**, so "n = 4, 12, 28" does not apply to it. It serves every held issuance, as §1.2 asks, and the receipts give that read once per source.
5. **The counts have moved since the recon:**
   - Wind holds 13 six-hourly runs (10-07 12Z landed).
   - pnw holds 40 issuances, not 34: GFS 12, IFS 12, AIFS 11, NWS 5.
   - The brief's DD "8.2 KB raw for 34 issuances" is **15.3 KB raw / 2.5 KB gzip for 40** as served. The recon's figure was values only, without keys, stamps or the booleans.
6. **"Integer arrays for N = 28 are 26.7 KB raw / 8.2 KB gzip" was `reg` alone.** With `cal`, the keys and the stamps, solar is 39.6 KB raw / 8.5 KB gzip and wind 37.9 / 11.5. Still about 4% of the outlook shape.
7. **"Unknown area" and "an area with no rows" are one answer.** Nothing tells them apart without a second read.
   - A well-formed code the table has never held answers the same `absence` (`no_issuance`) as a real area with nothing banked.
   - A malformed area, or a hub outside NP15/ZP26/SP15, is a 400, as on the outlook routes.
8. **"First" EXPLAINs on CISO and pnw are not cold** once a lane has banked the same rows from the same compute. I added AZPS as the first read from storage (36.7 ms, 179 blocks read). Only an endpoint restart, which I did not do, gives a compute-cold read.
9. **"V6 … banked receipts as the vector":** the banked outlook receipts are not of the newest issuance (10-04 18Z solar, 10-05 00Z wind). V6 therefore holds both:
   - The newest issuance against the outlook route over the same banked rows.
   - Each banked receipt against its own vintage. Both match exactly, which also shows that production has not rewritten those issuances since they were banked.
10. **`_DDCache` cannot key its lock.** As the brief allowed, I said so (§1.1) and used it as the outlook routes do, unchanged.

---

## 7. Not done (out of scope, or named for later)

- **Out of scope:** the asset page, load and net demand, GEFS, and any change to the existing outlook or board routes.
- **The degree-day read's growth** (~1 ms warm per issuance held) reaches the 500 ms STOP-V line at about 500 issuances per source. The fix named in recon §2.3 (a keyed spine) is a pantry lane.
- **The DD route holds four pool connections at once** on a cold build, for ~12 ms. The region board holds three.

*Sources:* `vintages.py`, `main.py` (the two vintages sections), `solar_outlook.py` (`gate`, `CALIBRATION_SQL`, `_AREA_KEY`, the parsers), `degree_days.py` (`SOURCE_LABELS`), `tests/test_vintages_d091644.py`, `docs/receipts/vintages-d091644/*`, and `docs/recon_2026_10_07_vintage_player.md`.
