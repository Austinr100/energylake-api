# Recon d091639: the vintage player (what is banked, and how fast it reads)

**Lane:** d091639 · **Branch:** `claude/vintage-player-recon-58ykng` · **Recon only:** no code, no route, no migration, no test change.
**Read:** energylake-api at `0eb299d`; energylake-pantry main at `c81278e`; energylake-dashboard main at `4316425`; Neon project `fancy-block-96153928` (production branch).
**Measured:** 2026-10-07, ~14:30–15:30Z.
**Written:** nothing to Neon or R2. Every Neon statement was a `SELECT` or an `EXPLAIN (ANALYZE, BUFFERS)` of a `SELECT`. No read came near the 5 s stop; the slowest was 96.6 ms.

**What "cold" means here.** "First" is the first execution in this session. "Warm" is the immediate repeat. A compute-cold read (LFC evicted) cannot be made without restarting the endpoint, and I did not restart it. The "first" reads still show `shared read` > 0, and the File Cache Misses on the JSON plan, so they are the nearest thing to cold available.

**Not reachable from this environment:**
- **R2.** There are no `R2_*` credentials, and egress to `*.r2.cloudflarestorage.com` is refused.
- **The production API.** `web-production-497cb.up.railway.app` returns 403 at the egress proxy.

So the R2 figures in §4 are **not measured**: they come from the pantry code plus a synthetic Parquet. The payload figures in §3 come from the banked route receipts in `docs/receipts/` and from real Neon rows, not from live HTTP.

---

## 0. The answer in six lines

1. **Solar and wind by area can be vintage surfaces today**, with caveats. `implied_gen_area_hourly` keeps every issuance and nothing prunes it. One area's N newest issuances read in **0.7–4.3 ms warm and 19–97 ms first**, down the PK, for N = 4–28. **But only 16 solar and 12 wind issuances are the 6-hourly, 240-hour kind.** The rest are a once-a-day, 66-hour backfill.
2. **The degree-day board can be one too**, but its history is shallow:
   - GFS has 9 runs and IFS and AIFS have 10 each since arming on 10-02. The NWS leg has 5 folds.
   - Its read, through `v_degree_days_region_forecast`, costs about 400 buffers per issuance held, because the view cannot push an `issued_ts` filter. That is fine now and will not stay fine.
3. **The payload is the real cost, not the read.** Bundling N issuances as the existing outlook route shapes them comes to **0.9 MB raw / 65 KB gzip at N = 12** for solar. Integer arrays come to **~11 KB raw / ~3.7 KB gzip** at N = 12, and **~27 KB / ~8 KB** at N = 28. Ship arrays.
4. **The asset page cannot be a vintage surface from what exists.** Recommendation: keep the newest **K = 8** cycles per plant in Neon:
   - Cost: 3.78 M rows, ~0.4–0.47 GB of row data, ~1.0–1.4 GB on disk with index, at today's measured density.
   - Benefit: a 1–2 ms warm read that carries the weather.
   - The alternative is reading Parquet from R2: an estimated ~16 MB fetched for one solar plant × 12 cycles, with no weather in it.
5. **"The previous run" means four different things across the repos** (§5). The outlook and the degree-day views take the previous issuance at any age. The delta board and the meteogram cap it at 24 h. The dashboard's day change needs 24 of 24 hours, while the delta board needs 12.
6. **The dashboard already has the transport.** `AxisScrubber` plus playbackCore §4–8 is reusable as is. Map frames are assumed only in playbackCore §1–3, the tile readiness helpers, `RunGapStrip`'s "on the Globe" meaning, and trendCore's 6 h / f240 ladder.

---

## 1. Inventory: every series that keeps more than one issuance

"Answerable" is the brief's question: **can "this target hour, as every issuance saw it" be answered from Neon today?**

| # | Store (table / view) | Issuance key | Issuances held | Cadence (observed) | Gaps (named) | How far back | Pruned? | Target grain | Answerable? |
|---|---|---|---:|---|---|---|---|---|---|
| 1 | `implied_gen_area_hourly`, solar_pv · gfs, area_kind ba (22 areas), hub (3), hub_sum (1) | `init_ts` | **52** per area | 08-27 → 10-01: **once a day**, at 06Z *or* 12Z (backfill, leads 1–66). From 10-03 12Z: **00/06/12/18Z**, leads 1–240 | **10-02 has no run.** 10-03 00Z and 06Z are absent. The once-a-day run alternates 06Z/12Z, so its spacing is 18, 24 or 30 h. Nothing is missing since 10-03 12Z (16 in a row) | 2026-08-27 12Z | No. Upsert on PK; no delete anywhere (pantry `implied_gen/store.py:160-176`) | hourly, `target_ts` interval-begin UTC | **Yes.** At 6 h spacing only from 10-03 12Z (16 runs) |
| 1a | same, area_kind **state** (11 areas) | `init_ts` | **16** | 6-hourly, 240 h | starts 10-03 12Z | 2026-10-03 12Z | No | hourly | Yes, 4 days deep |
| 2 | `implied_gen_area_hourly`, wind · hrrr_gfs, ba (22), hub (3), hub_sum (1), state (11) | `init_ts` | **44** | 09-01 → 10-02: **06Z once a day** (backfill, leads 1–66). From 10-04 12Z: 00/06/12/18Z, leads 1–240 | **10-03 has no run; 10-04 00Z and 06Z absent.** So the previous run for 10-04 12Z is 10-02 06Z, **54 h** earlier. None missing since 10-04 12Z (12 in a row) | 2026-09-01 06Z | No | hourly | **Yes.** At 6 h spacing only from 10-04 12Z (12 runs) |
| 3 | `implied_gen_site_latest` (solar 1,645 plants, wind 323) | `init_ts` (not in PK) | **1** | replaced each cycle (delete-then-insert, 7.19 M rows deleted to date) | n/a | newest cycle only (10-07 06Z) | **Yes**, by design | hourly per plant | **No** |
| 4 | R2 `weather/implied/{tech}/{model}/{yyyymmdd}/{HH}Z/sites.parquet` | object key | **not measured** (no R2 access). Code says solar: one per live cycle since the writer went live on 10-03; the backfill wrote none (pantry `scripts/implied_solar.py:9-11`). Wind: one per backfill day from 09-01 (leads 1–66) plus one per live cycle from 10-04 | as row 1/2 | as row 1/2 | — | none documented (no lifecycle rule on `weather/implied/`) | hourly per **generator unit** (solar ~1,914 units) or per plant (wind 323) | Not from Neon |
| 5 | `station_degree_days_forecast`, GFS (21 stations) | `issued_ts` (= run init) + `source_product` | **11** | 06Z and 12Z only. The cron fires 4×/day, but only those two cycles have ever folded | **One 08-29 dispatch (06Z, 12Z), then nothing for 33 days until 10-02 12Z.** 00Z and 18Z have never folded. 10-07 06Z had not folded at read time | 2026-08-29 06Z (real run: 10-02 12Z) | No (upsert; `ingesters/degree_days_model.py:514-519`) | daily, `target_date`, 16 days | Yes |
| 5b | same, IFS | as above | **12** | 00Z / 12Z | 08-29 00Z orphan, then 10-02 00Z on with none missing | 2026-08-29 | No | daily | Yes |
| 5c | same, AIFS | as above | **11** | 00Z / 12Z | 08-29 00Z orphan, then 10-02 12Z on with none missing | 2026-08-29 | No | daily | Yes |
| 5d | same, `gridpoints_raw` (NWS) | `issued_ts` = **per-WFO updateTime**, a different stamp per station | **"94"** distinct stamps, but each station has **5–6** (one fetch a day) | daily fetch (17:17Z) | days present: 08-29, 10-02, 10-03, 10-04, 10-05, and 10-06 (only 2 stations) | 2026-08-29 | No | daily, 7–9 days | Per station only |
| 5e | `station_degree_days_forecast_folds` (the NWS leg's issuance for the region views) | `fold_ts` | **5** folds | daily | 08-29 20:47Z, 10-02 19:04Z, **10-03 missing**, 10-04 18:13Z, 10-05 17:28Z, 10-06 17:26Z | 2026-08-29 | No | — | Yes, 5 deep |
| 6 | `v_degree_days_region_forecast` (9 region×weighting pairs) | `issued_ts`. For NWS it is `max(member_issued_ts)` over a fold | models: as 5/5b/5c; NWS: 5 | as above | as above | as above | view | daily per region | **Yes** (§2.3 for cost) |
| 6b | `v_degree_days_model_delta` | `issued_ts`, `prior_issued_ts` = `lag()` | as row 6 | — | — | — | view | daily | Yes, run k vs k−1 only |
| 6c | `v_degree_days_model_spread` | newest per model (`DISTINCT ON`) | 1 per model | — | — | — | view | daily | **No.** It has no as-of |
| 7 | `forecasts_gefs`, `gefs_dd_region_daily` (136 series = 17 regions × 8 stats) and `gefs_t2m_region_6h` (85 series) | `issued_ts` | **44** | 00/06/12/18Z (`gefs-sequence.yml`) | **09-30 18Z, 10-01 00Z** | 2026-09-26 00Z | No | daily (15.3 days) / 6-hourly (65 steps) | **Yes.** Index `(dataset, series, target_ts DESC, issued_ts DESC)` fits read (b) exactly. **No API route reads it** |
| 8 | `model_point_forecasts` (migration 177) | `vintage_ts` (fetch clock) | **1** (2026-08-13 12:01Z) for each of 4 models × 18 stations | none; the ingester is unarmed (pantry datasets.yaml:6114-6165) | everything after 08-13 | 2026-08-13 | No | hourly, 384 h | **No** (one vintage) |
| 9 | `forecasts_nws`, station series (21 stations × 10 variables) | `issued_ts` = source updateTime, ragged per station | e.g. `USW00023174_temperature`: **96** stamps in 39 days; **16** of them cover target 10-08 20Z | 4 fetches a day; 43–56 distinct stamps a day across stations | ragged by design | 2026-07-15 (table) | No | hourly (3-hourly source) | Yes, per series. 3.7 GB table |
| 9b | `forecasts_nws`, the 17 BA "LWT" series (`meta.source = 'lwt_temp_fcst_hourly'`, `agg_version = 'asof_v1'`) | `issued_ts` (one per station update event) | e.g. `SCE-TAC`: **160** stamps in 39 days; **30** cover target 10-08 20Z | about every 6 h, plus each station update | ragged | 2026-08-30 | No | hourly | Yes. This is the delta board's and the meteogram's source |
| 10 | `cpc_outlook_vintage` (610/814 × temp/prcp) | `issued_date` | **1,796–1,798** per product | daily | — | **2021-10-26** | No | a 5- or 7-day window (polygons in R2) | Not a line series |
| 10b | `cpc_outlook_features` | `issued_date` | **69** per product | daily | **10-06 not parsed yet** (vintage has it) | 2026-07-29 | No | polygon features | Not a line series |
| 11 | `forecasts_caiso`, load · 7DA / 2DA / DAM (CA ISO-TAC system total) | `generated_ts` (`issued_ts` is NULL on every row; `meta.generated_ts_source = "oasis_convention"`) | 123 / 118 / 117 generations | daily 16:10Z | none since 06-12 (DAM 117 generations over 117 days) | 06-06 / 06-11 / 06-12 | No (never-overwrite) | hourly. **Each generation keeps one target day only:** 7DA → D+7, 2DA → D+2, DAM → D+1 | **3 per target hour, exactly** |
| 11b | `forecasts_caiso`, **solar · DAM and wind · DAM** (CAISO system) | `generated_ts` (14:00Z) | 117 | daily | none | 2026-06-12 | No | hourly, **168 h per generation** | **Yes: 6–8 issuances per target hour** (avg 5.97, max 8) |
| 12 | `caiso_load_fcst_dam` / `_2day` / `_7day` (36 TAC series) | none | **1 value per (dataset, series, ts)** in `timeseries_values` (upsert on key); the issuance is only `meta->>'publish_time'` | daily | — | 7DA from 07-23, DAM/2DA from 07-25 | overwrite by key | hourly | **No** |
| 13 | `caiso_renewables_fcst_dam` (`{NP15,SP15,ZP26,AVA_}:{Solar,Wind}`) | none | **1 per ts** (`timeseries_values`) | daily | — | — | overwrite by key | hourly | **No** |
| 14 | Net demand's parts | — | load: rows 11 (system) / 12 (TAC, no history); solar and wind: rows 1 and 2 | — | — | — | — | hourly | **No.** The CISO load leg it uses (row 12) has no history |
| 15 | `forecasts_site_weather`, `forecasts_climate_outlook`, `forecasts_state_outlook` | `generated_ts` | many | — | — | — | No | text periods, categorical | Not line series |
| 16 | `hrrr_site_wind_cycles`, `d2_render_runs`, `model_runs` | `init_ts` / (run_date, cycle) | 862 / ~6,940 / ~634 | — | — | — | — | catalogs of banked inputs and rendered map frames | Inputs, not series |

**Sizes** (on disk, heap + index):

| table | size | rows |
|---|---:|---:|
| `implied_gen_area_hourly` | 115 MB | 388,560 |
| `implied_gen_site_latest` | 169 MB | 472,320 |
| `station_degree_days_forecast` | 3.5 MB | 12,499 |
| `forecasts_gefs` | 189 MB | 334,492 |
| `forecasts_nws` | 3.7 GB | ~7.1 M |
| `forecasts_caiso` | 10 MB | — |

---

## 2. The reads, timed

These follow the CLAUDE.md read rules:
- Every read names one `(tech, area_kind, area, model)` or one `(region, weighting, source_product)`.
- There is no `max()` over a dataset; the newest issuance comes from `ORDER BY init_ts DESC LIMIT 1` down the PK.
- Ordering is `ORDER BY init_ts, target_ts`, with no casts in a predicate.

Area used: `ba/CISO` (and `ba/AZPS` for one first-run figure). Degree days: `pnw / population`.

### 2.1 Solar and wind outlooks: the read shapes

**(a) One area, N newest issuances, all leads.** A recursive walk down the PK finds the N inits, then one PK range per init:
```sql
WITH RECURSIVE inits AS (
  (SELECT init_ts, 1 AS k FROM implied_gen_area_hourly
    WHERE tech=$tech AND area_kind=$kind AND area=$area AND model=$model
    ORDER BY init_ts DESC LIMIT 1)
  UNION ALL
  SELECT (SELECT h.init_ts FROM implied_gen_area_hourly h
           WHERE h.tech=$tech AND h.area_kind=$kind AND h.area=$area AND h.model=$model
             AND h.init_ts < i.init_ts
           ORDER BY h.init_ts DESC LIMIT 1), i.k+1
  FROM inits i WHERE i.k < $N AND i.init_ts IS NOT NULL)
SELECT h.init_ts, h.target_ts, h.lead_h, h.registry_mw, h.calibrated_mw
FROM inits i JOIN implied_gen_area_hourly h
  ON h.tech=$tech AND h.area_kind=$kind AND h.area=$area AND h.model=$model AND h.init_ts=i.init_ts
ORDER BY h.init_ts, h.target_ts;
```
Plan: `Recursive Union` → `Index Only Scan Backward using implied_gen_area_hourly_pkey` (one probe per init), then `Nested Loop` → `Index Scan using implied_gen_area_hourly_pkey` (Index Cond on the 5-column prefix plus `init_ts = i.init_ts`), then `Sort`. No seq scan anywhere.

**(b) One Pacific target day as every held issuance saw it** (2026-10-08 PT = `[10-08 07Z, 10-09 07Z)`):
```sql
SELECT init_ts, target_ts, lead_h, registry_mw, calibrated_mw
FROM implied_gen_area_hourly
WHERE tech=$tech AND area_kind=$kind AND area=$area AND model=$model
  AND target_ts >= $day_lo AND target_ts < $day_hi
ORDER BY init_ts, target_ts;
```
Plan: one `Index Scan using implied_gen_area_hourly_pkey`, with `target_ts` as a non-leading Index Cond. That walks the index entries of **every** held init for the area and keeps only the day's hours.

**(c) The run-to-run change:** issuance k against k−1 on the hours both cover, summed per Pacific day and per issuance. It uses the same `inits` CTE with N+1 rows, then:
```sql
, cur AS (SELECT i.k, h.init_ts, h.target_ts, h.registry_mw AS mw
          FROM inits i JOIN implied_gen_area_hourly h ON <area key> AND h.init_ts = i.init_ts)
SELECT c.init_ts, p.init_ts AS prev_init_ts,
       (c.target_ts AT TIME ZONE 'America/Los_Angeles')::date AS pday,
       count(*) AS shared_h, sum(c.mw - p.mw) AS delta_mwh, sum(c.mw) AS mwh
FROM cur c JOIN cur p ON p.k = c.k + 1 AND p.target_ts = c.target_ts
WHERE c.k <= $N GROUP BY 1,2,3 ORDER BY 1,3;
```
Plan: the CTE `cur` is materialised (two PK paths as in (a)), then `Hash Join` on `(k+1, target_ts)`, `Sort`, `GroupAggregate`.

`registry_mw` was used for the timing. The route's like-for-like rule (calibrated against calibrated, each gated by its own issuance's line; `solar_outlook.py:610-618`) is Python on the same rows and costs no extra read.

### 2.2 Results (implied gen)

| surface | read | N | rows | buffers (first) | first | buffers (warm) | warm |
|---|---|---:|---:|---|---:|---:|---:|
| solar CISO | (a) | 4 | 960 | 26 hit / 39 read | **19.3 ms** | 65 hit | **0.74 ms** |
| solar AZPS | (a) | 12 | 2,880 | 80 hit / 106 read | **58.9 ms** | — | — |
| solar CISO | (a) | 12 | 2,880 | (pages warm from N=28) | — | 217 hit | **2.2 ms** |
| solar CISO | (a) | 28 | 4,632 | 260 hit / 164 read | **96.6 ms** | 424 hit | **4.3 ms** |
| wind CISO | (a) | 4 | 960 | (warm) | — | 73 hit | **0.75 ms** |
| wind CISO | (a) | 12 | 2,880 | 78 hit / 125 read | **60.7 ms** | 203 hit | **2.1 ms** |
| wind CISO | (a) | 28 | 3,936 | 312 hit / 56 read (part warm) | 31.2 ms | 368 hit | **3.1 ms** |
| solar CISO | (b) 10-08 PT | all | 384 (16 inits × 24) | 86 hit / 21 read | **11.9 ms** | 107 hit | **0.56 ms** |
| wind CISO | (b) 10-08 PT | all | 288 (12 × 24) | 69 hit / 13 read | **6.8 ms** | 82 hit | **0.44 ms** |
| solar CISO | (c) | 28 (+1) | 189 (issuance × day) | 432 hit / 3 read | 11.3 ms | — | — |
| wind CISO | (c) | 28 (+1) | 168 | 376 hit / 2 read | 9.3 ms | — | — |

The N = 28 row counts are below 28 × 240 because the older issuances are the 66-hour backfill: solar 16 × 240 + 12 × 66 = 4,632; wind 12 × 240 + 16 × 66 = 3,936.

**What (c) returns.** Here is what the band would show for solar CISO, issuance 10-06 12Z against 06Z. Each day reads `shared hours: Δ MWh / MWh`:

- 10-10 `24h: −25,684 / 130,234`
- 10-11 `−32,405 / 106,439`
- **10-12 `+56,357 / 116,461`**: one run moved a day by 48 %
- 10-14 `+29,501 / 172,760`

The edges come in two kinds. The leading partial day has `1h/7h/13h/19h` shared. The trailing day has `5h/11h/17h/23h`, where the older run's horizon ends.

The pair **10-03 12Z vs 10-01 12Z** shares 18 hours. **10-01 12Z vs 09-30 06Z** shares 19 + 17. The backfill era cannot support a run-to-run band beyond about lead 36.

**Indexes.** Nothing here is slow today. Two things are worth naming:

1. **(b) walks every held init's index entries for the area.** That is 107 buffers at 52 inits and grows linearly. A year at 4 a day is ~1,460 inits × 240 h ≈ 350 k entries per area, a few thousand buffers. An index on `(tech, area_kind, area, model, target_ts, init_ts)` bounds it to the rows returned. **Not needed yet.**
   - `idx_implied_gen_area_hourly_target (tech, area, target_ts)` exists, but the planner did not choose it. It lacks `area_kind` and `model`.
   - PG 17 has no skip scan.
2. **There is no issuance-list route anywhere.** The recursive walk in (a) is the list (§3).

### 2.3 The degree-day board

Production now holds GFS 11, IFS 12, AIFS 11 issuances and 5 NWS folds, so **N = 12 and N = 28 both mean "everything held"** for every source.

**(a) One region × source, N newest issuances, all target days:**
```sql
WITH runs AS (SELECT DISTINCT issued_ts FROM station_degree_days_forecast
              WHERE source_product = $src ORDER BY issued_ts DESC LIMIT $N)
SELECT v.issued_ts, v.target_date, v.hdd_wtd, v.cdd_wtd, v.basis_complete, v.sample_spacing_hours
FROM v_degree_days_region_forecast v
WHERE v.region=$region AND v.weighting=$w AND v.source_product=$src
  AND v.issued_ts IN (SELECT issued_ts FROM runs)
ORDER BY v.issued_ts, v.target_date;
```
**(b) One target date as every issuance of every source saw it:** `… FROM v_degree_days_region_forecast WHERE region=$r AND weighting=$w AND target_date=$d ORDER BY source_product, issued_ts`.

**(c) Run-to-run:** `… FROM v_degree_days_model_delta WHERE region=$r AND weighting=$w AND source_product=$src AND target_date >= $lo AND target_date < $hi ORDER BY issued_ts, target_date`.

| read | rows | buffers | first | warm |
|---|---:|---|---:|---:|
| (a) pnw/population/GFS, all 11 | 176 | 4,628 hit / 76 read (planning 15.4 ms) | **35.5 ms** | **7.9 ms** (4,663 hit) |
| (b) pnw/population, 10-12, all sources | 32 (30 model + 2 NWS folds) | 895 hit / 41 read | **15.8 ms** | — |
| (c) pnw/population/GFS, 10-02 → 10-22 | 144 | 3,723 hit | — | **5.9 ms** |

**Plan (all three).** `wt` CTE → `Index Only Scan station_degree_days_forecast_pkey (station_id = w.station_id AND source_product = …)` → `Unique` spine → per member: `Index Scan idx_sddf_product_target (source_product, target_date, issued_ts)` + `Index Scan station_normals_daily_pkey` → `GroupAggregate` by `(fold_key, target_date)`. The NWS `fold_spine` branch reads `station_degree_days_forecast_folds` by seq scan (105 rows). The delta view adds a `WindowAgg`.

**The read that will need a change.** The view's `issued_ts` is `max(member_issued_ts)`, an aggregate, so an `issued_ts` predicate **cannot be pushed down**. (a) therefore builds every issuance of the region × source and filters afterwards.

The cost is **~400 buffers per issuance held** (4,663 buffers for 11). At two GFS runs a day for 90 days (~180 issuances), that is ~75 k buffers per read. Against the route's 2 s `SET LOCAL statement_timeout` it will fail first on a cold compute.

Two other notes:
- Listing a source's issuances also has no index of its own: it reads all 3,696 GFS rows down `idx_sddf_product_target`.
- **Named need:** either a spine keyed on `(region, weighting, source_product, fold_key)` that a predicate can reach, or a banked region-forecast table. Not today: everything is under 36 ms.

---

## 3. The payload

### 3.1 Bytes

| surface | shape | N = 4 | N = 12 | N = 28 |
|---|---|---|---|---|
| **solar** CISO | existing route, N separate calls (one route body = 81.5 KB compact / 7.4 KB gz, from `sample_outlook_ciso.json`) | 326 KB / 29 KB gz | 978 KB / 88 KB gz | 2.28 MB / 206 KB gz |
| solar CISO | existing route shape, N `hours[]` in one response | 305 KB / 23 KB gz | 904 KB / 65 KB gz | 2.11 MB / 149 KB gz |
| solar CISO | **proposed arrays** (one integer MW per hour per issuance) | **3.9 KB / 1.5 KB gz** (real rows from Neon) | 11.5 KB / 3.7 KB gz | 26.7 KB / 8.2 KB gz |
| **wind** CISO | existing route, N calls (108.9 KB / 12.7 KB gz each) | 436 KB / 51 KB gz | 1.31 MB / 153 KB gz | 3.05 MB / 356 KB gz |
| wind CISO | existing shape, one response | 393 KB / 38 KB gz | 1.16 MB / 106 KB gz | 2.68 MB / 245 KB gz |
| wind CISO | **proposed arrays** | 4.6 KB / 1.5 KB gz | 11.7 KB / 3.5 KB gz | 28.3 KB / 8.1 KB gz |
| **degree days** (one region) | existing `/dd/forecast/regions` body (newest run only, Δ only) | 31.7 KB / 3.6 KB gz for *one* issuance (no prior values) | — | — |
| degree days pnw/population | **proposed arrays**: every source, every held issuance (34), HDD + CDD to 0.01 (measured in SQL on real rows) | — | — | **8.2 KB raw**, all 34 issuances |

**How the rows were made:**
- **Solar N = 4 arrays:** the 4 newest real CISO issuances. Raw 3,910 B, gz 1,477 B.
- **N = 12 and 28 arrays:** built from 10 distinct real curves in the receipts (the current and previous run for each area), perturbed so gzip cannot reuse a whole array. At N = 4 this agrees with the real rows: 4,009 / 1,382 B.
- **Route bundles:** the receipt bodies re-serialised compactly. FastAPI's `JSONResponse` writes `separators=(",", ":")`; the banked files are indented.

### 3.2 The smallest contract that lets the slider move without a round trip per step (D-09-09-P)

D-09-09-P is in **energylake-dashboard** `CLAUDE.md:430`, not in this repo: nothing on a play step may cost a network round trip. So the page needs every vintage it can step to **before** the first step. The ghost and the band are both arithmetic on two adjacent arrays.

**One read per area per page open:**
```
GET /api/generation/{solar|wind}/vintages?area_kind=ba&area=CISO&n=28

{ "tech":"solar_pv", "area_kind":"ba", "area":"CISO", "model":"gfs", "unit":"MW", "step_h":1,
  "issuances": [                                   // oldest first; the newest is last = the right edge
    { "init":"2026-10-06T12:00Z", "t0":"2026-10-06T13:00Z", "lead0":1,
      "reg":[0,0,3541,15299,…],                    // registry MW, integers, one per hour from t0
      "cal":[null,…] | null,                       // calibrated MW where this issuance's own line covers the lead; null = none
      "method_version":"solar_pv_v1" },
    … ],
  "absence": null, "cache": {…} }
```

What it is:
- **Two integer arrays per issuance and nothing per hour.** `t0` + index gives `target_ts`, and `lead0` + index gives `lead_h`. Rounding to whole MW costs nothing on a chart that spans 0–20 GW.
- The like-for-like rule of `_previous_mw` (calibrated against calibrated, else registry against registry) is then the page's arithmetic on `cal`/`reg` of k and k−1. That keeps D-09-25-120: each is the same source against itself.
- About **2 KB raw / 0.6 KB gz per issuance with both arrays**.

What it leaves out, deliberately:
- **Scores, actuals, fleet, calibration and the per-hour objects.** The existing `/outlook` route keeps serving the selected issuance's detail. The player needs only the curves.
- **Server-computed deltas.** Day sums are the page's arithmetic. They depend on which hours the page counts, which is a page decision; the two existing rules disagree (§5).

How the read sits under the house rules:
- It is §2.1 (a). It names one `(tech, area_kind, area, model)`, as D-09-25-75 asks.
- It sits behind a single-flight memo keyed `(tech, area_kind, area, model, n)` with a 5 s statement timeout.
- **`_DDCache` holds one `asyncio.Lock` per cache, not per key** (`main.py:16001`), so cold builds for different areas queue behind each other. At 19–97 ms a build that does not matter. It would if the read ever grows.

**For degree days:** `GET /api/weather/dd/forecast/regions/vintages?region=pnw&weighting=population` returns, per source, the list of `{issued_ts, d0, hdd[], cdd[], basis_complete[], spacing_h[]}`. It is 8.2 KB for everything held. Today's board payload carries only the Δ, so it cannot draw a ghost.

### 3.3 Where it stops fitting in one response

**It is not bytes for one area:**
- 28 issuances is 8 KB gz.
- At ~0.6 KB gz per issuance (both arrays), a page could carry ~250 issuances (about two months at 4 a day) in ~150 KB gz. That is the size of one existing route body uncompressed.
- The read stays far from the timeout: ~3 ms per init cold, so 250 inits is ~0.75 s.

**It stops at:**
1. **All areas at once.** 37 areas × 28 issuances ≈ 1,036 arrays ≈ 1 MB raw / ~0.6 MB gz with both arrays. Keep it one area per read.
2. **Hub or BA plus the weather.** "Along with the weather" doubles or triples the arrays per issuance (GHI and cloud cover, or hub wind speed). That is still small per area.
3. **The asset page.** One plant × K cycles × 240 h × (MW + 2 drivers) at K = 12 ≈ 8,640 numbers ≈ 40 KB raw. It fits, but only once Neon holds K cycles (§4).

**Recommended default:** `n = 28`, about 7 days of 6-hourly runs, which covers the full 240-hour horizon of the oldest run shown. Accept `n` up to 120.

---

## 4. Per-plant history (the asset page)

### 4.1 What is banked

**Neon `implied_gen_site_latest`:**
- Newest cycle only: solar 1,645 plants × 240 h = 394,800 rows; wind 323 × 240 = 77,520; total 472,320.
- Average row by `pg_column_size`: **105 B solar, 125 B wind.**
- On disk: 111.95 MB heap + 65.0 MB index = **~375 B per row**. The heap carries free space from delete-then-insert churn: 7.66 M inserted and 7.19 M deleted to date.
- **The driver columns of migration 280 (`ghi_wm2`, `clearsky_ghi_wm2`, `clearsky_mw`, `tcc_pct`, `precip_mm`, `hub_ws_ms`, `gust_ms`) are live in the schema now.**
- One plant's newest cycle: `Bitmap Index Scan on implied_gen_site_latest_pkey`, 240 rows, 11 buffers, **6.2 ms first**.

**R2 `sites.parquet` (not measured; there is no R2 access from here):**

| | solar | wind |
|---|---|---|
| Objects (code-stated, not counted) | 4 a day since the live writer, about 16 by now | about 32 backfill (09-01 → 10-02, leads 1–66) + about 12 live |
| Oldest (code-stated) | 2026-10-03 12Z | 2026-09-01 06Z |
| Grain | per generator unit, 1,913–1,914 units × 240 h ≈ 459 k rows | per plant, 77,520 rows |
| Synthetic size (not measured) | **≈1.4 MB**, of which `implied_mw` (4 dp) is ~1.3 MB | **~0.25–0.4 MB**, scaled by row count (my synthetic wind curve compressed unrealistically well) |

What the code states about the objects:
- **No drivers**, per d091634 §2.4 and the column lists at `scripts/implied_solar.py:188-197` and `scripts/implied_wind.py:223-233`.
- **One row group per object**: no `row_group_size` is set (`store.py:239-244`), and there is no page index.
- No lifecycle rule covers `weather/implied/`.

### 4.2 One plant across the newest 12 cycles

**From R2 (estimated):**
- With one row group per object and no page index, a reader cannot fetch one plant's slice. It needs the whole `plant_code`, `target_ts` and `implied_mw` column chunks.
- That is **~1.32 MB per solar object × 12 ≈ 16 MB fetched**, and ~0.3 MB × 12 ≈ 3–5 MB for wind, as 12 GETs plus footers.
- From Railway that is roughly 0.3–1 s with the GETs parallel. Add the decode, and a solar `groupby plant_code` across the units.
- **It has no weather**, so "as you change the vintage, you can see where those loads shifted, along with the weather" cannot be met from these objects.
- Solar has only about 16 cycles in R2 at all.

**From Neon with K cycles kept:**
- PK `(tech, plant_code, model, init_ts, target_ts)` gives K × 240 rows per plant: about 11 buffers per cycle cold (measured for one cycle), so **~130 buffers / ~10–30 ms first and ~1–2 ms warm** at K = 12. This is extrapolated from the one-cycle read.

### 4.3 Neon cost of keeping the newest K cycles per plant in place of one

| K | rows | row data at 105 B (solar) to 125 B (wind, measured) | on disk at today's measured 375 B/row (heap with churn + index) | packed estimate (~133–153 B heap + ~55 B PK) |
|---:|---:|---:|---:|---:|
| 1 (today) | 472,320 | 50–59 MB | 177 MB | ~95 MB |
| 4 | 1,889,280 | 198–236 MB | ~0.71 GB | ~0.38 GB |
| 8 | 3,778,560 | 397–472 MB | ~1.42 GB | ~0.76 GB |
| 12 | 5,667,840 | 595–708 MB | ~2.13 GB | ~1.13 GB |

**Write load stays the same as today.** Each cycle already deletes and inserts 472 k rows. Keeping K would insert 472 k and delete the oldest cycle's 472 k. The brief's 105–117 B is the solar and pre-driver wind figure; wind now measures 125 B.

### 4.4 Recommendation

**Keep the newest K = 8 cycles per plant in Neon (two days at 4 a day). Do not read R2 for the player.**

| | R2, 12 cycles | Neon, K = 8 |
|---|---|---|
| Fetched per plant | ~16 MB (solar) | ~90 buffers |
| Time | ~0.3–1 s | ~1–2 ms warm |
| Weather | none | yes (the driver columns) |
| History today | about 16 solar cycles | n/a |
| Disk cost | — | ~0.8–1.4 GB |

K = 8 covers the run-to-run band for the full 240-hour horizon of the newest run against the last 7 runs, and it is the asset page's "N = 4 … 12" sweet spot.

- **When to choose K = 12:** if the captain wants a full three days. It costs ~1.1–2.1 GB, a little over half of `forecasts_nws`.
- **Narrower option, if disk matters more than the drivers:** keep history only for `(tech, plant_code, model, init_ts, target_ts, implied_mw, ghi_wm2 | hub_ws_ms)`, with latest-only drivers for the rest.

Not built. The pantry writer and a migration own this.

---

## 5. What "the previous run" means today

D-09-25-120 (a revision is one source against itself) holds on every surface below. All of them key on the model or `source_product`, or on one NWS series. **They disagree about which earlier run of that source counts, and how old it may be.**

| # | Place | Which earlier run | When it is missing | When it covers different hours | Age cap |
|---|---|---|---|---|---|
| 1 | **Outlook `previous_mw`** (solar and wind; api `solar_outlook.py:159-209`, `_previous_mw` :610) | The newest `init_ts` strictly before the current one, for the same `(tech, area_kind, area, model)`. With `?init=X`, the one before X | `previous_init_ts` is null and every `previous_*` is null | LEFT JOIN on exact `target_ts`. Current hours it doesn't reach get `previous_*` = null. Hours only it has are dropped. Calibrated vs registry is gated like for like, otherwise null | **None.** Wind 10-04 12Z's previous run is **10-02 06Z (54 h)**; solar backfill pairs are 18–30 h apart |
| 2 | **Outlook day change** (dashboard `solarOutlook.ts:644-659`, `windOutlook.ts:715`) | as row 1 | "no previous run banked" | **Σ(mw − previous_mw) only when 24 of 24 hours have a previous value.** Otherwise null: "prev. run: k of n h" or "not this day" | inherits row 1 (none) |
| 3 | **`v_degree_days_model_delta`** (pantry 202:329-367) → `/dd/forecast/regions` (`degree_days.py:652`) → board Δ | `lag()` over `(region, weighting, source_product, target_date) ORDER BY issued_ts`: the previous issuance **that has a row for that target date**. For NWS, the previous **fold** | `change = null`, `change_absence.reason = no_prior_issuance` | It is per target date, so the oldest day of a run has no prior (seen on every run: 15 + 1 days). If either composite is incomplete, the delta is null (`incomplete_basis`), and it does **not** fall back to an older complete run. `spacing_comparable = false` when sampling differs (GFS 6 h vs 3 h, ~day 8); the board marks it `≠` and still serves the Δ | **None.** GFS alternates **6 h and 18 h** (06Z vs 12Z of the day before). NWS fold of 10-04 (issued 08Z) vs the 10-02 fold (issued 18Z) is **38 h**. The 08-29 orphans have no overlap, so they never pair with 10-02 |
| 4 | **`/dd/forecast`** per station (main.py:16581) | `row_number()` rank 2 per `(station, target_date, source_product)`: as row 3, but per station | `delta_absence.reason = no_prior_issuance` | per target date | none |
| 5 | **Delta board** (main.py:3204, `_DELTA_MAX_GAP`) | Each `issued_ts` floored to a 6 h synoptic bucket, latest in the bucket wins. Prior = the next older **populated** bucket | the whole cell is null, drawn as a dot (not Δ0) | daily-max °F on hours present in **both**. **≥ 12 shared hours**, except the leading partial day | **24 h**, then null |
| 6 | **Meteogram ghosts** (main.py:3471) | the same buckets as row 5, up to 2 ghosts | no ghost; the legend says so | points clipped to the actuals seam | 24 h per step |
| 7 | **trendCore** (dashboard Viewer) | It does not pick one. It lists **every** run that reaches a valid time, oldest first | excluded with a reason (6 rules) | excluded: off the 6 h ladder, beyond f240, `missing_fhr_list`, … | none, but there is a ladder |
| 8 | Load outlook / CAISO vs actual (main.py:901, 4916) | Not a revision. Freshest **product** (DAM > 2DA > 7DA), or the latest `generated_ts` via `DISTINCT ON` | — | — | — |
| 9 | Net-demand backtest (main.py:982) | Not a revision: the D−1 12Z issuance for scoring | — | — | — |

### Disagreements, named

1. **Age.** Rows 1–4 compare against the previous run at any age. Rows 5–6 refuse a prior older than 24 h. The same morning, the wind outlook served a "previous run" two and a quarter days old, while the delta board would have shown a dot.
2. **Coverage for a day sum.** Row 2 needs **24 of 24** hours. Row 5 needs **≥ 12** (any for the leading partial day). A vintage band needs one rule. I'd take row 2's, with the partial day labelled as row 5 does, but that is the page's decision under D-09-25-76's spirit.
3. **"Previous" per target versus per issuance.** Rows 3–4 lag per target date, so in principle one issuance's days can be measured against different priors when an intermediate run lacks a date. Rows 1–2 use one previous issuance for the whole curve. For pnw on 10-07, every model issuance used a single prior across its days, so this has not happened yet.
4. **Spacing.** The outlook previous run is now 6 h. DD GFS is 6 h or 18 h alternately; IFS and AIFS are 12 h; NWS is about 12–38 h between folds. A band labelled "run-to-run" means different things per source unless it prints the spacing. The DD payload already carries `prior_issued_ts`; the outlook carries `previous_init_ts`.
5. **Incomplete prior.** The DD views give up (null). The outlook gives up per hour (null `previous_mw`). The delta board walks back to an older populated bucket. Three behaviours.
6. **The wind "issuance" is a hybrid.** HRRR covers leads 1–48 and the newest complete GFS (I − 6 h) covers 49–240. Run k vs k−1 is a single source, `model = 'hrrr_gfs'`, so D-09-25-120 holds. But leads 49–240 of two adjacent wind issuances compare GFS (I − 6) with GFS (I − 12).

---

## 6. What the dashboard already has (energylake-dashboard, read only)

### Reusable as it is

- **`AxisScrubber.tsx`**: one transport over an opaque ordered list of `AxisItem {key, label}`.
  - Its parts: a `wording` override; keyboard scoped to its root (←/→ step, Space play, Home/End); `readyKeys` gating Play; speeds 1/2/4; end-of-list dwell; hold and stop; a coalesced drag; an `onInput(key, "hand"|"play")` hook; a `track` slot for a strip drawn under the slider.
  - `kind="trend"` (450 ms a step) fits a vintage player.
  - It has already been used for issuances beside an SVG line, in `CpcOutlookRoom.tsx:391-433`. That room is **unmounted since d091512**, so the precedent exists but is not live.
- **playbackCore §4–8** (pure functions, no DOM): `nextReadyIndex`, `stepWraps`, `playTick`, `transportStatus`, `statusPlaceCh`, `transportLabel`, `createCoalescer`, `createGestureGate`, `createPlayEpoch`.
- **"null means newest":** `skyRadar.radarPlayheadAfterSlide` (`src/components/sky/skyRadar.ts:335`) is the pattern. **This is needed.** `AxisScrubber` reads a missing value as index 0, the oldest (CLAUDE.md trap D-09-21-01), and the captain's player has the newest at the right.
- **Ghost drawing conventions:**
  - Meteogram / StationMeteogram: the same ink at 0.42, the second ghost at 0.26 dashed, the second ghost behind a toggle at phone width.
  - Outlooks: the `previous` path at opacity 0.35, labelled "Previous run · {run}".
- **Edge wording:** `previousShort` / `previousNote` / `windPreviousNote` ("does not reach these hours", "registry only, no faint line").
- **Chart frame:** `chartAxis.ts`, `GenOutlookParts.tsx` (`chartBox`, `chartDomain`, `scales`, `ChartFrame`, `Key`, `nearestHour`, `invertX`), and `monotoneCurve.ts`. A band is one more `<path>` in `SolarChartSvg` / `WindChartSvg`.
- **Day change bars:** the ±GWh on `DayBarsChart` (GenOutlookParts.tsx:230, 267, 331) is the existing "delta between runs". The captain's "very crude… like we have a table" is this, plus the degree-day board's inline `Δ+1.2`.
- **trendCore's pure time helpers** (`runInitMs`, `validTimeMs`, `fhrForValid`) and its exclude-with-a-reason / `trendShortfallNote` pattern.

### Assumes map frames or the Viewer

- **playbackCore §1–3:** `Layer {src, fhr}`, `PaneState`, the two-`<img>` decode-then-flip, `holdNotice`'s `f048` tokens.
- `readyFhrKeys`, `surfaceReadyKeys`, `tiledHoursFor`: tile manifests and MapLibre idle.
- **`RunGapStrip`:** its "solid = the Globe draws it" meaning and `trendCellRefusal`. `TickStrip` / `TrackCells` have a generic grammar but are **not exported**.
- **trendCore:** the 6 h / f240 ladder, `renders` / `missing_fhr_list`, tile verdicts. `useTrend` is bound to `/api/weather/model-runs`.

### Gaps on the page side

- **`useFeed` (ddFeed.tsx:41-73) has no cache and no single-flight**, and it does not reset on a path change. A slider stepping `?init=` through it would make a round trip per step, which D-09-09-P forbids. The §3.2 one-read contract removes the need. The Viewer's `sharedFetch` (`useViewerFeed.ts:159`) is the in-flight pattern if a cache is still wanted.
- **`?init=` is in the outlook URL state, but no UI sets it**, and no route lists issuances.
- **The DD board's payload has Δ only**, so it can draw no ghost line, and its chart draws the newest values only.

---

## 7. What cannot be a vintage surface yet, and why

1. **CAISO load.**
   - `forecasts_caiso` holds exactly **three issuances per target hour** (7DA at D−7, 2DA at D−2, DAM at D−1). Each generation keeps **one** target day, so no issuance has a curve longer than 24 h.
   - The load outlook route reads `caiso_load_fcst_*` in `timeseries_values` instead, which keeps **one value per hour** and sees no vintages at all.
   - The 36 TAC areas have no history anywhere.
2. **Net demand.** Its load leg is item 1. Its solar and wind legs are the newest issuance only by construction. A vintage net demand needs load vintages first.
3. **The asset page.** `implied_gen_site_latest` holds one cycle. Solar R2 history starts 10-03, is per generator unit and has no drivers (§4).
4. **`model_point_forecasts`.** One vintage (2026-08-13); the ingester is unarmed.
5. **CAISO hub DAM renewables** (`caiso_renewables_fcst_dam`). One value per hour. The *system* solar and wind DAM in `forecasts_caiso` **can** be a vintage surface (6–8 issuances per hour since 06-12, 168 h each). It is the one CAISO line that can, and it is CAISO-wide only.
6. **Solar by state.** 16 issuances (since 10-03 12Z). It works, but four days deep.
7. **The NWS leg of the degree-day board.** 5 folds (10-03 missing), one a day. It works, but shallow.
8. **CPC outlooks.** 1,796+ vintages since 2021, but they are polygons and categories: a map player (the Viewer), not a line.
9. **`v_degree_days_model_spread`.** It has no as-of. It would need an `issued_ts <= :asof` inside `latest` to scrub.

Deep enough today but **not read by any route**: `forecasts_gefs` (44 issuances 6-hourly since 09-26, per-region DD p10/p50/p90, index already fits) and `forecasts_nws` station and BA series. They are candidates after the first surfaces, not blockers.

---

## 8. What this brief got wrong

1. **`caiso_load_fcst_dam / _2day / _7day` and `caiso_renewables_fcst_dam` are not tables.**
   - They are `timeseries_values` datasets that upsert on `(ts, dataset, series)`, so they hold **no issuance history**.
   - The vintaged CAISO store is `forecasts_caiso`, and it holds system totals only.
   - "Three issuances per target day" is true there, but it is three *products*, each banking a single target day.
   - The brief missed that `forecasts_caiso` solar and wind DAM hold **6–8 issuances per target hour**.
2. **`lwt_temp_fcst_hourly` is not a table.** Its rows live in `forecasts_nws` as 17 BA series (`meta.source = 'lwt_temp_fcst_hourly'`, `agg_version = 'asof_v1'`). They are the delta board's source.
3. **`gridpoints_raw` (NWS) does not hold 94 issuances.**
   - `issued_ts` there is each station's WFO updateTime, so 94 is a count of distinct stamps.
   - Each station has **5–6** (one fetch a day). The region board sees **5 folds** (08-29, 10-02, 10-04, 10-05, 10-06; **10-03 missing**).
4. **GFS 11 / IFS 12 / AIFS 11 "since 2026-08-29" hides a 33-day hole.**
   - 08-29 was a single dispatch. Real history starts on **10-02**, when the schedule was installed.
   - **GFS has only ever folded 06Z and 12Z**, two a day, though its cron fires four times.
5. **The 52 solar and 44 wind issuances are two regimes, not one series.**
   - 36 solar and 32 wind are a **once-a-day, 66-hour backfill** (solar at mixed 06Z/12Z).
   - Only **16 solar (since 10-03 12Z) and 12 wind (since 10-04 12Z)** are the 6-hourly, 240-hour runs a player wants.
   - Named gaps: solar 10-02 (all), 10-03 00Z and 06Z; wind 10-03 (all), 10-04 00Z and 06Z.
6. **`model_point_forecasts` (177) is not a multi-issuance series.** It has one vintage (08-13), and its ingester is unarmed.
7. **"105 to 117 bytes a row" understates the cost.**
   - Wind now measures **125 B**, because the migration-280 driver columns are live.
   - On disk the table costs **~375 B per row** (237 B heap including churn free space, plus 138 B index).
   - §4.3 gives both figures.
8. **"The outlook routes … accept `init`" is true of solar and wind only.** `/api/load/outlook` and `/api/load/net-demand` take no `init`.
9. **"Per-plant history is one Parquet object per cycle"**:
   - **Solar's starts only on 10-03**, because the backfill wrote no site rows and no R2.
   - The objects are per generator unit, not per plant.
   - Nothing in the pantry records their sizes or counts. They are unmeasured here and need an R2-capable session to count.
10. **D-09-09-P lives in the dashboard's CLAUDE.md (`:430`), not in energylake-api.** A search of this repo and its history finds nothing.
11. **"The degree-day board reads `v_degree_days_model_delta`" is true, but the payload carries the Δ only, not the prior value.** So the board cannot draw a ghost, and the delta is per target date (§5 row 3), not per issuance.
12. **The brief lists `forecasts_gefs` for inventory but not as a first surface.** It is the deepest clean 6-hourly series in the house (44 runs, 2 gaps) with an index that already fits read (b). No API route reads it.

---

*Sources:*
- **energylake-api:** `solar_outlook.py`, `wind_outlook.py`, `load_outlook.py`, `degree_days.py`, `main.py` (lines cited); receipts in `docs/receipts/{solar-outlook-api-d091568,wind-outlook-api-d091590,dd-forecast-regions-d091576}`.
- **energylake-pantry:** `implied_gen/store.py`, `implied_gen/wind_store.py`, `scripts/implied_{solar,wind}.py`, migrations 047, 080, 086, 149, 161, 177, 202, 242, 264, 267, 268, 269, 276, 280, `.github/workflows/{implied-solar,implied-wind,degree-days-model,gefs-sequence}.yml`, `docs/handback_2026_10_06_asset_drivers.md`.
- **energylake-dashboard:** `src/components/viewer/{playbackCore.ts,AxisScrubber.tsx,ViewerChrome.tsx,trendCore.ts,useTrend.ts}`, `src/components/weather/{genOutlook.ts,solarOutlook.ts,windOutlook.ts,GenOutlookParts.tsx,SolarOutlookParts.tsx,WindOutlookParts.tsx,DdForecastBoard.tsx,ddBoard.ts,DdBoardTable.tsx,DeltaBoard.tsx,Meteogram.tsx}`, `CLAUDE.md`.
- **Neon:** every figure in §1–§4 not marked "not measured" was read on 2026-10-07.
