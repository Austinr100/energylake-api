# Handback: d091576, the degree-day forecast region route

**Date:** 2026-10-03. **Ruling:** D-09-25-120 (a revision is one source against itself). **Spec of record:** energylake-pantry `docs/cc_spec_2026_10_03_degree_day_forecast_board.md`, sections 0–2.
**Repo:** `energylake-api`. **Branch:** `claude/dd-forecast-regions-d091576-n1zcc2` (see W1). Branch only: no PR, no merge, no deploy. **Neon:** read only. Every statement was a `SELECT`, `pg_get_viewdef`, or `EXPLAIN`, and no view was changed.

## Outcome

- `GET /api/weather/dd/forecast/regions?region=&weighting=&from=&days=` ships. It reads pantry's three views as they are, one read per view, run concurrently. Each read runs under a 2 s statement timeout inside its own transaction (D-09-25-75). The board is served from a single-flight `_DDCache` memo with a 300 s TTL, using the sibling routes' envelope and headers (`cache` block, `Cache-Control: max-age=300`, `X-Cache`, `Age`).
- **No STOP-V.** Filtered to one region and weighting, each view takes 9–15 ms. Unfiltered, each takes 101–110 ms. The route's own read, with the date window pushed into the index, takes 12.5 ms. The plans are below and in `docs/receipts/dd-forecast-regions-d091576/plans.txt`.
- Step 1 finds one cause behind all three questions: **the region view nulls every value column, normals included, whenever `basis_complete` is false.** The NWS leg is never complete in the view because its `issued_ts` is per gridpoint. Model rows are incomplete only on the partial day at either end of a run, plus one GFS day where its sample spacing changes (F1–F3).
- Tests: R1–R6 (32 tests in the new file) are red on main (`red_on_main.txt`: 32 failed) and green here. R7: `test_weather_dd_ledger.py` is unchanged and its 39 tests pass. Full suite: **2198 passed, 0 failed**.

## Files

| file | what |
| --- | --- |
| `main.py` | the route, the three view reads plus the vectors read, `_dd_timed_read` (`SET LOCAL statement_timeout` in a transaction), `_dd_400`, two caches added to `_DD_CACHES`, and one line in the route index |
| `degree_days.py` | `SOURCE_LABELS` (the single `gridpoints_raw` → `NWS` mapping), `PERIOD_RULE`, and `region_forecast_payload` with its helpers. Pure: no DB, no clock |
| `tests/test_weather_dd_forecast_regions.py` | new: R1–R7 plus route wiring (32 tests) |
| `scripts/dd_forecast_regions_sample_d091576.py` | rebuilds the pnw sample by passing the Neon export through the real route with a fake pool |
| `docs/receipts/dd-forecast-regions-d091576/` | `plans.txt`, the three `.psv` Neon exports, `sample_pnw.json` (full body), `red_on_main.txt`, `green.txt` |

## Step 1: three findings (hand to pantry; views not changed)

### F1. Why every `gridpoints_raw` row has no normal and no spacing

The cause is in the view text (`pg_get_viewdef`). `spine` takes `DISTINCT (region, weighting, source_product, issued_ts, target_date)` and `memb` LEFT JOINs each member station on the **exact** `issued_ts`. Model runs share one `issued_ts` across all 21 stations. The NWS leg does not, because each gridpoint is fetched at its own time:

| measured | value |
| --- | --- |
| `gridpoints_raw` distinct `issued_ts` | 34, across 42 station-issuances |
| most stations sharing one `issued_ts` | 3 (232 of them carry one station) |
| distinct `issued_ts` on one target date (2026-10-03) | 18 across 21 stations |
| average `members_present` in the region view | 1.00 (load_share) / 1.03 (population) |
| rows with `sample_spacing_hours` NULL in the **base table** | 0 (all are `1`) |

So each NWS region-issuance has about one member present. `bool_and(...)` is false, `basis_complete` is false, and the final `SELECT` nulls `hdd_wtd`, `cdd_wtd`, `*_norm_wtd`, `*_vs_norm` and `tavg_f_wtd` (`CASE WHEN basis_complete THEN … ELSE NULL`). Spacing is NULL because `count(memb.sample_spacing_hours) = count(*)` fails when the LEFT JOIN supplies NULL members. The normals exist in `station_normals_daily`. They are dropped together with the values. **In the region views the NWS leg has no number on any day.** The route serves it as it is: present, `basis_complete: false`, values null, labelled NWS.

Proposal for pantry (not done here): key the NWS spine on a fetch cycle instead of the exact `issued_ts`. Two options are `date_trunc('hour', issued_ts)`, or "each station's newest NWS issuance as of T" for a grid of T. With either, an NWS region-issuance can have all its members.

### F2. Why a few model rows have no normal

Same mechanism. **Every model row without a normal is a row with `basis_complete` false.** `count(*) FILTER (WHERE hdd_norm_wtd IS NULL AND basis_complete)` is 0 for every source and weighting. The station normals are not missing. The view withholds the normal together with the value.

| source | weighting | rows | complete | no normal (= incomplete) |
| --- | --- | ---: | ---: | ---: |
| AIFS | load_share_365d / population | 192 / 240 | 172 / 215 | 20 / 25 |
| GFS | load_share_365d / population | 256 / 320 | 232 / 290 | 24 / 30 |
| IFS | load_share_365d / population | 256 / 320 | 228 / 285 | 28 / 35 |
| gridpoints_raw | load_share_365d / population | 215 / 272 | 0 / 0 | 215 / 272 |

These counts are per weighting across all regions. Spec §0's "5–7 rows each" is the per-region figure.

### F3. Why `basis_complete` is false on some rows for every source

The base table explains it (`hours_covered` < `hours_required`). For the model runs, an incomplete row is **the partial Pacific day at either end of a run**, plus one other case:

- **First day:** 00Z runs (IFS, AIFS) and the GFS 06Z run start partway through a Pacific day. GFS 2026-10-03 06Z has 1 of 4 samples on 2026-10-02. IFS/AIFS 00Z have 2 of 4 on the day before the issue date. 12Z runs start at 05:00 PDT, so their first day is complete.
- **Last day:** every run except GFS 06Z ends partway through its last Pacific day (1–3 of 4 samples; the GFS 12Z run has 2 of 8). GFS 2026-10-03 06Z's last day, 10-17, is complete. This is why only GFS has a d11_15 sum in the sample below.
- **The GFS spacing seam:** GFS is sampled every **6 h for the near days and 3 h from about day 8**. When a run's change from 6 h to 3 h falls mid-day, that day has 7 of 8 samples and spacing NULL. GFS 2026-10-02 12Z → 2026-10-10 is the measured case; the 2026-08-29 12Z run has the same interior day. For the 06Z run the seam falls on a day boundary (10-10 is 6 h, 10-11 is 3 h), so no day is lost.
- **NWS (`gridpoints_raw`):** in the base table, it is the same partial-day pattern (2026-10-02: 20 of 21 stations incomplete with 19–23 of 24 hours; 2026-10-09: all 21 incomplete). In the region view every NWS row is incomplete because of F1.

All 21 stations share the same incomplete target dates in every run (`n_inc` = 21 on each incomplete date). Station gaps play no part.

## The route

`GET /api/weather/dd/forecast/regions`

| param | default | rule (400 naming the field otherwise) |
| --- | --- | --- |
| `region` | every region the weighting carries | must be a region of that weighting (`socalgas_territory` has `population` only) |
| `weighting` | `population` | `population` or `load_share_365d` (read from `degree_day_region_weights`) |
| `from` | today, Pacific | ISO date, within 60 days of today |
| `days` | 16 | integer 1–30; the 400 states "cap 30" |

How it is built:

- **Three reads, plus one vocabulary read.** Each view read selects `rn = 1` per `(region, weighting, source_product, target_date)` ordered by `issued_ts DESC`. A day's cell, its change and its spread therefore all describe the same newest run. Every predicate is on a column the views group or partition by, so the filter reaches the base table and the delta view's `lag()` still sees the whole partition (plan 7). The fourth read is `degree_day_region_weights` (39 rows, cached for 15 min). It validates `region`/`weighting` and states `members.expected`, because an empty window is not an unknown region.
- **Change** comes from `v_degree_days_model_delta` only, matched on `(region, target_date, source_product)` **and** the cell's `issued_ts`. The route does no subtraction. If the matched row has no prior, the change is `null` and `change_absence.reason` is `no_prior_issuance`. If the prior exists but either side is incomplete, `reason` is `incomplete_basis` and the absence carries `prior_issued_ts`, `basis_complete` and `prior_basis_complete`. `spacing_comparable: false` is served together with the change (R2).
- **Spread** is `v_degree_days_model_spread` as it is: `hdd`/`cdd` = max minus min over complete sources, `sources_present` (stored names), `sources_complete`, `spacing_comparable`. If the view has no row, it is `null`.
- **Normal:** a cell's `hdd_normal`/`cdd_normal` and departures come from its own row only. The day's `normal` is the view's normal for that region and day from any row that has one, and `null` otherwise. Nothing is filled.
- **Periods:** see `period_rule` in the payload. Day 1 is the Pacific day after the newest issuance served for the region. A source's sum is printed only when all 5 days are present, come from **that source's newest issuance**, and are `basis_complete` with a value. Otherwise `hdd`/`cdd` are null and `days_present`/`days_required` are served. The normal sum covers the same days.
- **Labels:** `degree_days.SOURCE_LABELS = {"gridpoints_raw": "NWS"}` is the only mapping. The keys stay stored names. `sources[].label` and top-level `source_labels` carry the printed name.
- **Members:** `expected` / `expected_stations` come from the vector. `missing` lists stations that are present in **no** served cell, and `missing_by_source` gives the same per source. Each cell's own `missing_stations` and `members_present` are served too.

## Plans (Neon, 2026-10-03 ~17:00Z; full text in `plans.txt`)

| view | filter | rows | execution |
| --- | --- | ---: | ---: |
| `v_degree_days_region_forecast` | pnw / population | 242 | 14.8 ms |
| `v_degree_days_region_forecast` | none | 2,071 | 109.0 ms |
| `v_degree_days_model_delta` | pnw / population | 242 | 9.8 ms |
| `v_degree_days_model_delta` | none | 2,071 | 101.6 ms |
| `v_degree_days_model_spread` | pnw / population | 34 | 8.9 ms |
| `v_degree_days_model_spread` | none | 306 | 105.6 ms |
| route delta read, rn = 1, pnw, 10-03..10-18 | + window | 52 | 12.5 ms |

The filtered forecast-view plan, abridged. The other two views wrap this subtree node for node:

```
Subquery Scan on agg (actual time=5.823..14.699 rows=242)            Buffers: shared hit=6387 read=6
  CTE wt -> Seq Scan on degree_day_region_weights (rows=39)
  -> GroupAggregate  Group Key: f_1.source_product, f_1.issued_ts, f_1.target_date
     -> Incremental Sort (rows=968)
        -> Nested Loop Left Join (rows=968)
           -> Nested Loop Left Join  Join Filter: (f.station_id = w.station_id)  Rows Removed: 1886
              -> Nested Loop (rows=968)
                 -> Unique -> Sort -> Nested Loop (rows=770)
                    -> CTE Scan on wt w_1  Filter: region='pnw' AND weighting='population' (rows=4)
                    -> Bitmap Heap Scan on station_degree_days_forecast f_1 (rows=192 loops=4)
                       -> Bitmap Index Scan on idx_sddf_run_over_run  Index Cond: (station_id = w_1.station_id)
                 -> CTE Scan on wt w (rows=4 loops=242)
              -> Index Scan using idx_sddf_product_target on station_degree_days_forecast f (loops=968)
           -> Index Scan using station_normals_daily_pkey on station_normals_daily n (loops=968)
Execution Time: 14.827 ms
```

Growth note: the unfiltered read walks the whole table (4,045 rows, 109 ms). The route always passes a date window (plan 7: `target_date` reaches `idx_sddf_run_over_run`), so its cost tracks the window, not the history.

## Tests: red, then green

Red is the new test file run against main's `main.py` and `degree_days.py` (`red_on_main.txt`: **32 failed**). Green is this commit (`green.txt`: 71 passed, new file plus the ledger file).

| test | pins |
| --- | --- |
| R1 `test_r1_change_is_only_ever_within_one_source_product` | interleaved GFS 10-02 12Z / IFS 10-02 18Z / IFS 10-03 00Z / GFS 10-03 06Z: GFS's change is against GFS 12Z, with the view's delta, never GFS minus IFS |
| R1 `…a_delta_row_never_attaches_to_another_source_or_issuance` | an IFS delta, or a GFS delta for an older issuance, gives the GFS cell no change |
| R1 `…all_three_reads_rank_per_source_product` | both ranked reads partition by `source_product` and keep `rn = 1` |
| R2 `test_r2_spacing_not_comparable_is_served_with_the_change` | GFS 10-11, 3 h against a 6 h prior: change 7.741293 served with `spacing_comparable: false`; the spread's flag is served |
| R3 ×5 | full period sums value and normal over the same days; an incomplete last day gives 4 of 5 and no sum; a missing day gives the same; a day from an older issuance does not count; day 1 follows the Pacific date (06Z → 10-03, 18Z → 10-04) |
| R4 ×2 | `gridpoints_raw` → `label: "NWS"`, stored key kept; a scan of every top-level `.py` finds exactly one `"NWS"` string literal, in `degree_days.py` |
| R5 ×2 | a row with a value and no normal serves the value, `normal: null` and null departures; a view-shaped incomplete row serves nulls, never zeros |
| R6 ×10 | bad `region` (unknown, and `socalgas_territory` under `load_share_365d`), `weighting`, `from` (bad month, word, out of range), `days` (0, 31, word) each give a 400 whose detail starts `<field>:`; caps stated |
| R7 | `test_weather_dd_ledger.py` is unchanged: 39 passed. The per-station SQL still partitions by `source_product`; both paths are routed |
| route | one read per view, each after a `SET LOCAL statement_timeout` inside a transaction; envelope and headers; second call `X-Cache: hit`; the window reaches every read; default regions per weighting; 503 when the DB is down |

## Sample payload: pnw, population, from 2026-10-03

Built by the real route from Neon's rows (`scripts/dd_forecast_regions_sample_d091576.py`; export rounded to 6 dp). The full body is `docs/receipts/dd-forecast-regions-d091576/sample_pnw.json`. Abridged:

| period | AIFS | GFS | IFS | NWS |
| --- | --- | --- | --- | --- |
| d01_05 (10-03..10-07) HDD | 22.306623 | 9.540684 | 18.355052 | — 0 of 5 |
| d06_10 (10-08..10-12) HDD | 42.153972 | 45.858818 | 34.253586 | — 0 of 5 |
| d11_15 (10-13..10-17) HDD | — 4 of 5 | 61.302469 | — 4 of 5 | — 0 of 5 |

AIFS and IFS miss d11_15 because 10-17 is the partial last day of their 00Z runs (F3). GFS 06Z runs six hours later and covers it.

```json
{"weighting": "population", "from": "2026-10-03", "days": 16, "tz": "America/Los_Angeles",
 "period_rule": "Day 1 is the Pacific calendar day after ...", "source_labels": {"gridpoints_raw": "NWS"}, "region_count": 1,
 "regions": [{"region": "pnw", "weighting": "population",
  "members": {"expected": 4, "expected_stations": ["USW00024131", "USW00024157", "USW00024229", "USW00024233"], "present": 4, "missing": [], "missing_by_source": {"AIFS": [], "GFS": [], "IFS": [], "gridpoints_raw": ["USW00024131", "USW00024157", "USW00024233"]}},
  "sources": [
   {"source_product": "AIFS", "label": "AIFS", "newest_issued_ts": "2026-10-03T00:00:00+00:00", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "sample_spacing_hours": [6]},
   {"source_product": "GFS", "label": "GFS", "newest_issued_ts": "2026-10-03T06:00:00+00:00", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "sample_spacing_hours": [3, 6]},
   {"source_product": "IFS", "label": "IFS", "newest_issued_ts": "2026-10-03T00:00:00+00:00", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "sample_spacing_hours": [6]},
   {"source_product": "gridpoints_raw", "label": "NWS", "newest_issued_ts": "2026-10-02T18:41:09+00:00", "prior_issued_ts": "2026-10-02T18:31:56+00:00", "sample_spacing_hours": []},
  ],
  "period_anchor_issued_ts": "2026-10-03T06:00:00+00:00",
  "days": [
   {"target_date": "2026-10-03", "normal": {"hdd": 6.865104, "cdd": 0.37832},
    "by_source": {
     "AIFS": {"hdd": 3.960956, "cdd": 0.565698, "tavg_f": 61.604742, "hdd_normal": 6.865104, "cdd_normal": 0.37832, "hdd_vs_norm": -2.904148, "cdd_vs_norm": 0.187378, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": 0.54918, "cdd": 0.0, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
     "GFS": {"hdd": 1.497885, "cdd": 1.325015, "tavg_f": 64.82713, "hdd_normal": 6.865104, "cdd_normal": 0.37832, "hdd_vs_norm": -5.367219, "cdd_vs_norm": 0.946694, "issued_ts": "2026-10-03T06:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": 0.0, "cdd": -0.124027, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
     "IFS": {"hdd": 3.411776, "cdd": 0.883343, "tavg_f": 62.471567, "hdd_normal": 6.865104, "cdd_normal": 0.37832, "hdd_vs_norm": -3.453328, "cdd_vs_norm": 0.505023, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": 0.0, "cdd": 0.317645, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
     "gridpoints_raw": {"hdd": null, "cdd": null, "tavg_f": null, "hdd_normal": null, "cdd_normal": null, "hdd_vs_norm": null, "cdd_vs_norm": null, "issued_ts": "2026-10-02T18:41:09+00:00", "basis_complete": false, "sample_spacing_hours": null, "members_present": 1, "missing_stations": ["USW00024131", "USW00024157", "USW00024233"], "change": null, "change_absence": {"reason": "incomplete_basis", "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference", "prior_issued_ts": "2026-10-02T18:31:56+00:00", "basis_complete": false, "prior_basis_complete": false}},
    },
    "spread": {"hdd": 2.463071, "cdd": 0.759316, "sources_present": ["AIFS", "GFS", "IFS", "gridpoints_raw"], "sources_complete": 3, "spacing_comparable": true}},
   {"target_date": "2026-10-10", "normal": {"hdd": 8.963173, "cdd": 0.120099},
    "by_source": {
     "AIFS": {"hdd": 9.974786, "cdd": 0.0, "tavg_f": 55.025214, "hdd_normal": 8.963173, "cdd_normal": 0.120099, "hdd_vs_norm": 1.011614, "cdd_vs_norm": -0.120099, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": -2.388598, "cdd": 0.0, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
     "GFS": {"hdd": 10.239358, "cdd": 0.0, "tavg_f": 54.760642, "hdd_normal": 8.963173, "cdd_normal": 0.120099, "hdd_vs_norm": 1.276185, "cdd_vs_norm": -0.120099, "issued_ts": "2026-10-03T06:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": null, "change_absence": {"reason": "incomplete_basis", "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "basis_complete": true, "prior_basis_complete": false}},
     "IFS": {"hdd": 7.646179, "cdd": 1.116241, "tavg_f": 58.470062, "hdd_normal": 8.963173, "cdd_normal": 0.120099, "hdd_vs_norm": -1.316993, "cdd_vs_norm": 0.996142, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": -6.953243, "cdd": 1.116241, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
    },
    "spread": {"hdd": 2.593179, "cdd": 1.116241, "sources_present": ["AIFS", "GFS", "IFS"], "sources_complete": 3, "spacing_comparable": true}},
   {"target_date": "2026-10-11", "normal": {"hdd": 9.169531, "cdd": 0.107696},
    "by_source": {
     "AIFS": {"hdd": 11.21509, "cdd": 0.0, "tavg_f": 53.78491, "hdd_normal": 9.169531, "cdd_normal": 0.107696, "hdd_vs_norm": 2.045559, "cdd_vs_norm": -0.107696, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": -3.742799, "cdd": 0.0, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
     "GFS": {"hdd": 15.81867, "cdd": 0.0, "tavg_f": 49.18133, "hdd_normal": 9.169531, "cdd_normal": 0.107696, "hdd_vs_norm": 6.649139, "cdd_vs_norm": -0.107696, "issued_ts": "2026-10-03T06:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 3, "members_present": 4, "missing_stations": [], "change": {"hdd": 7.741293, "cdd": 0.0, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 3}, "change_absence": null},
     "IFS": {"hdd": 8.392154, "cdd": 0.0, "tavg_f": 56.607846, "hdd_normal": 9.169531, "cdd_normal": 0.107696, "hdd_vs_norm": -0.777377, "cdd_vs_norm": -0.107696, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 6, "members_present": 4, "missing_stations": [], "change": {"hdd": -7.46447, "cdd": 0.0, "prior_issued_ts": "2026-10-02T12:00:00+00:00", "spacing_comparable": true, "prior_sample_spacing_hours": 6}, "change_absence": null},
    },
    "spread": {"hdd": 7.426516, "cdd": 0.0, "sources_present": ["AIFS", "GFS", "IFS"], "sources_complete": 3, "spacing_comparable": false}},
   {"target_date": "2026-10-17", "normal": {"hdd": 11.111627, "cdd": 0.031764},
    "by_source": {
     "AIFS": {"hdd": null, "cdd": null, "tavg_f": null, "hdd_normal": null, "cdd_normal": null, "hdd_vs_norm": null, "cdd_vs_norm": null, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": false, "sample_spacing_hours": null, "members_present": 0, "missing_stations": ["USW00024131", "USW00024157", "USW00024229", "USW00024233"], "change": null, "change_absence": {"reason": "incomplete_basis", "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "basis_complete": false, "prior_basis_complete": false}},
     "GFS": {"hdd": 11.599422, "cdd": 0.0, "tavg_f": 53.400578, "hdd_normal": 11.111627, "cdd_normal": 0.031764, "hdd_vs_norm": 0.487795, "cdd_vs_norm": -0.031764, "issued_ts": "2026-10-03T06:00:00+00:00", "basis_complete": true, "sample_spacing_hours": 3, "members_present": 4, "missing_stations": [], "change": null, "change_absence": {"reason": "incomplete_basis", "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "basis_complete": true, "prior_basis_complete": false}},
     "IFS": {"hdd": null, "cdd": null, "tavg_f": null, "hdd_normal": null, "cdd_normal": null, "hdd_vs_norm": null, "cdd_vs_norm": null, "issued_ts": "2026-10-03T00:00:00+00:00", "basis_complete": false, "sample_spacing_hours": null, "members_present": 0, "missing_stations": ["USW00024131", "USW00024157", "USW00024229", "USW00024233"], "change": null, "change_absence": {"reason": "incomplete_basis", "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference", "prior_issued_ts": "2026-10-02T12:00:00+00:00", "basis_complete": false, "prior_basis_complete": false}},
    },
    "spread": {"hdd": null, "cdd": null, "sources_present": ["AIFS", "GFS", "IFS"], "sources_complete": 1, "spacing_comparable": true}},
   ... 12 more days ...
  ],
  "periods": [
   {"window": "d01_05", "from": "2026-10-03", "to": "2026-10-07", "by_source": {
     "AIFS": {"hdd": 22.306623, "cdd": 3.046233, "hdd_normal": 38.494708, "cdd_normal": 1.342479, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "GFS": {"hdd": 9.540684, "cdd": 7.254176, "hdd_normal": 38.494708, "cdd_normal": 1.342479, "issued_ts": "2026-10-03T06:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "IFS": {"hdd": 18.355052, "cdd": 5.060973, "hdd_normal": 38.494708, "cdd_normal": 1.342479, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "gridpoints_raw": {"hdd": null, "cdd": null, "hdd_normal": null, "cdd_normal": null, "issued_ts": "2026-10-02T18:41:09+00:00", "days_present": 0, "days_required": 5, "complete": false},
   }},
   {"window": "d06_10", "from": "2026-10-08", "to": "2026-10-12", "by_source": {
     "AIFS": {"hdd": 42.153972, "cdd": 0.620134, "hdd_normal": 44.85838, "cdd_normal": 0.681733, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "GFS": {"hdd": 45.858818, "cdd": 1.984428, "hdd_normal": 44.85838, "cdd_normal": 0.681733, "issued_ts": "2026-10-03T06:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "IFS": {"hdd": 34.253586, "cdd": 2.674154, "hdd_normal": 44.85838, "cdd_normal": 0.681733, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "gridpoints_raw": {"hdd": null, "cdd": null, "hdd_normal": null, "cdd_normal": null, "issued_ts": "2026-10-02T18:41:09+00:00", "days_present": 0, "days_required": 5, "complete": false},
   }},
   {"window": "d11_15", "from": "2026-10-13", "to": "2026-10-17", "by_source": {
     "AIFS": {"hdd": null, "cdd": null, "hdd_normal": null, "cdd_normal": null, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 4, "days_required": 5, "complete": false},
     "GFS": {"hdd": 61.302469, "cdd": 0.0, "hdd_normal": 51.617657, "cdd_normal": 0.316129, "issued_ts": "2026-10-03T06:00:00+00:00", "days_present": 5, "days_required": 5, "complete": true},
     "IFS": {"hdd": null, "cdd": null, "hdd_normal": null, "cdd_normal": null, "issued_ts": "2026-10-03T00:00:00+00:00", "days_present": 4, "days_required": 5, "complete": false},
     "gridpoints_raw": {"hdd": null, "cdd": null, "hdd_normal": null, "cdd_normal": null, "issued_ts": "2026-10-02T18:41:09+00:00", "days_present": 0, "days_required": 5, "complete": false},
   }},
  ]}],
 "cache": {"state": "miss", "built_at": "2026-10-03T17:12:05.370737+00:00", "age_seconds": 0.0, "ttl_seconds": 300.0, "build_seconds": 0.002, "refreshing": false}}```

## What this spec got wrong, and what I decided

- **W1. Branch name.** The spec names `claude/dd-forecast-regions-d091576`. This session's harness assigns `claude/dd-forecast-regions-d091576-n1zcc2` and permits pushes only there, so the work is on that branch, as in d091553.
- **W2. R5's premise does not occur in the views today.** §0 and R5 imagine a row with a value and no normal. In the views, no normal ⇔ no value ⇔ `basis_complete` false (F2). R5 is pinned on a synthetic row so the route stays correct if pantry ever serves values without normals. In today's data, every dash for a departure is also a dash for the value.
- **W3. The NWS leg is empty on the region board.** This is not the route's doing (F1). Spec §0 calls it "not explained". It is the exact-`issued_ts` spine. Until pantry re-keys it, the page will show NWS with dashes on every day, and the spread view counts NWS in `sources_present` without it ever being in `sources_complete`.
- **W4. "Day 1 is the day after the newest issuance's date" does not say whose issuance.** I used the newest issuance across **all** sources served for the region, and the payload states it (`period_rule`, `period_anchor_issued_ts`). When the NWS leg is fresh (hourly), an NWS fetch after 07:00Z moves day 1 forward a day while the models' newest run is still from the previous Pacific date. Options are anchoring on model sources only, or per source. That is the architect's call.
- **W5. A period sum mixes issuances unless it is told not to.** The spec says "present and complete for that source". I also require each day to come from that source's newest issuance, so a sum is one run, not a blend of the newest run and an older one past its horizon. Each period cell carries `issued_ts`.
- **W6. `members {expected, present, missing[]}` needed a definition.** Taking the union of each cell's missing stations reports `present: 0` for pnw, because a partial last day lists every member missing. `missing` is therefore "present in no served cell". I added `expected_stations` and `missing_by_source`. For pnw, the only absences are NWS's three stations.
- **W7. "One read per view" is three reads plus one.** The route also reads `degree_day_region_weights` (39 rows, cached for 15 min) to validate `region`/`weighting` and to state `members.expected` independently of the window.
- **W8. "Caps stated in the 400s, as the sibling routes do."** The per-station sibling bounds `days` with FastAPI `le=`, which returns a 422. This route parses its own params so every bad one is a 400 naming the field (R6). The sibling is unchanged (R7).
- **W9. The delta view calls two missing spacings comparable.** `NOT NULL IS DISTINCT FROM NULL` is true, so NWS and incomplete→incomplete pairs carry `spacing_comparable: true` with no spacing on either side. The route does not leak this: those pairs have no delta, so `change` is null with `incomplete_basis`. It is pantry's to tidy.
- **W10. TTL and timeout.** The TTL is 300 s, the same as the per-station forecast route. Model runs come every 6 h, so five minutes never hides a run for long. The statement timeout is 2 s, the STOP-V line, so a view that degrades past it returns a 503 and is never served slowly.

## Compare

https://github.com/Austinr100/energylake-api/compare/main...claude/dd-forecast-regions-d091576-n1zcc2
