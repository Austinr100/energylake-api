# Handback d091666: the degree-day board's scores, blend and band, and the desk

**Lane:** d091666 · **Repo:** `energylake-api` · **Branch:** `claude/dd-scores-blend-band-ioi5rl` (no PR, no merge, no deploy).
**Neon:** reads only, project `fancy-block-96153928` production, 2026-10-08 18:53Z to 20:45Z. Nothing written. No migration.

## 0. The two answers first

**Region blend: no.** `dd_blend_forecast` is keyed by `station_id` and holds 21 stations, all `USW…`. No region blend forecast is banked anywhere: no table or view with "blend" in its name carries a region.

- The `EL_BLEND` rows at `place_kind = region` in `dd_member_scores` are **scores only**: 864 region cells hold 144 EL_BLEND cells, 33 of them scored.
- Pantry forms a region blend in order to score it and does not bank it. So `/blend` serves stations only, and `/forecast/regions` gains no blend source.
- `/desk` refuses `source=EL_BLEND` with a 400 that says why.
- What pantry would have to write is in §7.

**Band: STOP-B tripped.** `forecasts_gefs` holds **no member values**. It holds two datasets of statistics already reduced over the 31 members:
- `gefs_t2m_region_6h`: t2m mean/p10/p50/p90/spread.
- `gefs_dd_region_daily`: cdd and gwdd mean/p10/p50/p90.

Both are per **balancing authority**: 17 BAs (AZPS, BANC, BPAT, … VEA-TAC), not our 21 stations or our 5 region vectors.

- The daily DD uses a 06Z day boundary from four 6-hourly samples, not the 24 local-standard hours the scorer pairs against (D-09-25-165).
- There is `gwdd`, no `hdd`.
- There is no P5, P25, P75 or P95, and a P10/P90 cannot be turned into one.

A P5–P95 band at our places would have to be invented, so `/band` serves a stated absence and reads nothing.

## 1. What the measurements found

| object | measured 2026-10-08 |
|---|---|
| `dd_member_scores` | 2,880 rows, 2,038 scored, **one vintage**: as_of_date **2026-10-06**, ghcn_frontier 2026-10-03, window 2026-09-09..10-06, min_target_days 14, scorer `dd_member_scores_v2` / `7a77ebf160c8`, blend `el_blend_v2`, created_at 18:53:52Z. Leads **0–15**. Station cells carry `weighting = ''`. 25 columns. |
| `v_dd_member_scores_current` | the same 2,880 cells. It is `DISTINCT ON (member, lead_day, place_kind, place, weighting)` over `scorer_version = 'dd_member_scores_v2'`, newest as_of_date, then frontier, then created_at. 24 columns (no score_id). |
| members × places | 6 members × 16 leads at 21 stations (2,016 cells) and at 5 population and 4 load_share_365d regions (864). Scored per member at station: GFS/IFS/AIFS 315, NBM 231, NWS 147, EL_BLEND 115. |
| `dd_blend_forecast` | 13,146 rows: issue dates 2026-08-28..10-08, 21 stations, leads 0–14 (315 rows per issue date), one method `el_blend_v2`. 668 scored, 418 drawable. **Of 12,728 not drawable, 5,369 carry a NULL `no_blend_reason`:** 5,119 have a value but are unscored, and 250 are scored but do not beat their best member. The other 7,359 have no value and a reason (`"N scored member(s) of M with a forecast; a blend needs 2"`). |
| `v_dd_blend_drawable` | `WHERE drawable AND method_version = 'el_blend_v2'`: 418 rows, 20 stations, leads 0–5. **Issue dates 2026-09-28..10-08**; 66 rows are 10-08's. 15 columns: no `members`, `scored`, `beats_best_member`, `drawable` or `no_blend_reason`. |
| pantry's contract | Table comment, dd_blend_forecast (migrations 282/283): *"Read blends ONLY through v_dd_blend_drawable."* View comment: *"Nothing downstream reads dd_blend_forecast directly."* |
| blend MAE, 10-08 issue | Mean `blend_mae` over scored rows, leads 0–5: 1.256, 1.458, 1.662, 1.677, 1.780, 1.930. These are the brief's figures. |
| `forecasts_gefs` | 372,453 rows (two datasets, 49 cycles, 00/06/12/18Z, 2026-09-26 00Z..10-08 12Z). Every row has `n_members = 31` in `meta`. `gefs_dd_region_daily` meta: `method = per_member_per_station_then_weight_then_reduce`, `base_f = 65`, `day_boundary = 06Z`, `fhr = [24, 30, 36, 42]`. The stations and weights behind each BA are not banked. |
| region views | `v_degree_days_region_forecast` and `v_degree_days_model_delta` carry five sources in the window: AIFS, GFS, IFS, **NBM**, gridpoints_raw. |

## 2. The routes

Every route is in `main.py` (§ "/scores, /blend, /band, /desk"). SQL and shaping are in `dd_board.py`.

- Every read goes through `_dd_timed_read`: `SET LOCAL statement_timeout = '2s'` inside a transaction (D-09-25-75).
- Every route serves through a single-flight `_DDCache` memo and carries `cache` plus `X-Cache` and `Age`.
- `/band` is the exception: it reads nothing.

| route | reads | memo / TTL | 400s |
|---|---|---|---|
| `GET /api/weather/dd/scores[?place_kind=station\|region]` | `SCORES_SQL` once per place_kind, concurrently | `dd/scores`, 900 s | place_kind |
| `GET /api/weather/dd/blend[?station=&from=&days=]` | `BLEND_SQL` once | `dd/blend`, 900 s | station (GHCN id format), from (±60 d), days (1–30) |
| `GET /api/weather/dd/band` | none | none | none |
| `GET /api/weather/dd/desk?source=…[&region=&weighting=&from=&days=]` | the region board's three reads (its own memo) and the scores reads (their memo) | `dd/desk`, 300 s | source (required, five sources; EL_BLEND refused), region, weighting, from, days |

**Contracts.**

- **/scores**: `v_dd_member_scores_current` as written.
  - A cell has member, lead_day, n_target_days, n_pairs, n_provisional_days, scored, the five metrics and excluded, plus `vintage`. That index points into `vintages`, which lists the fields the cells share (as_of_date, ghcn_frontier, window, min_target_days, scorer and blend versions, created_at) once each.
  - An unscored cell rides with `scored: false` and null metrics. Pantry's CHECK `dms_floor_ck` guarantees the nulls, and nothing here fills them.
  - `members` carries each member's label through `degree_days.source_label`.
- **/blend**: `v_dd_blend_drawable` rows for target dates in `[from, from + days)`, every issue date that reaches them. The page picks the issue it wants.
  - Rows carry `best_member_label` (via SOURCE_LABELS).
  - `not_served` names the five fields and the rows this route cannot serve, quotes pantry's rule, and gives the view proposal.
  - `region_blend: {banked: false, why}`. An empty window is `absence.reason = no_drawable_blend`.
- **/band**: `band: null`, `percentile_rule: null`, `absence.reason = not_banked`, plus `banked` (what forecasts_gefs does hold) and `requires` (what pantry must bank). The page reads `absence` and greys the toggle.
- **/desk**: a projection of the region board and the region scores for one `source_product`.
  - Per day: target_date, issued_ts, `lead_day` (the scorer's key: target minus the UTC date of the issuance), hdd, cdd, basis_complete, normal.
  - `change` / `change_absence` are the delta view's row, exactly as `/forecast/regions` serves it.
  - `score` has a `state` of `scored`, `not_yet_scored` or `no_score_cell` (lead > 15), plus the cell's counts and metrics.
  - `absence` is set when the source holds no issuance for that day.
  - `periods` are the board's period sums for the source, under `degree_days.PERIOD_RULE`. No other arithmetic is done.
  - `inputs` gives when each underlying memo was built.

`/forecast/regions` is unchanged in what it serves. Its parameter handling and memo moved into `_dd_region_board` / `_dd_window_params` / `_dd_today_pt`, so the desk shares its build: a page polling both pays for one. Its tests and the vintages and ledger tests run unchanged (S7).

## 3. Sample responses (production rows, 2026-10-08)

The full bodies are `docs/receipts/dd-board-d091666/body_*.json`. They were served by the real routes over `tests/fixtures/dd_board_d091666`, with today pinned to 2026-10-08 Pacific (`sample.py`); test `test_banked_body_is_what_the_route_serves` holds them. Excerpts follow, with `…` marking a cut.

### /scores

```json
{
 "place_kinds": [
  "station",
  "region"
 ],
 "sources": {
  "tables": [
   "v_dd_member_scores_current"
  ],
  "scorer_versions": [
   "dd_member_scores_v2"
  ],
  "scorer_hashes": [
   "7a77ebf160c8"
  ],
  "blend_method_versions": [
   "el_blend_v2"
  ]
 },
 "vintages": [
  {
   "vintage": 0,
   "as_of_date": "2026-10-06",
   "ghcn_frontier": "2026-10-03",
   "window_start": "2026-09-09",
   "window_end": "2026-10-06",
   "min_target_days": 14,
   "scorer_version": "dd_member_scores_v2",
   "scorer_hash": "7a77ebf160c8",
   "blend_method_version": "el_blend_v2",
   "created_at": "2026-10-08T18:53:52.237446+00:00"
  }
 ],
 "members": [
  {
   "member": "AIFS",
   "label": "AIFS"
  },
  {
   "member": "EL_BLEND",
   "label": "EL_BLEND"
  },
  {
   "member": "GFS",
   "label": "GFS"
  },
  {
   "member": "IFS",
   "label": "IFS"
  },
  {
   "member": "NBM",
   "label": "NBM"
  },
  {
   "member": "gridpoints_raw",
   "label": "NWS"
  }
 ],
 "cell_count": 2880,
 "scored_count": 2038,
 "absence": null,
 "places": [
  {
   "place_kind": "region",
   "place": "pnw",
   "weighting": "population",
   "cells": [
    {
     "member": "EL_BLEND",
     "lead_day": 0,
     "vintage": 0,
     "n_target_days": 24,
     "n_pairs": 24,
     "n_provisional_days": 3,
     "scored": true,
     "bias_tavg_f": 0.29429555824992687,
     "mae_tavg_f": 0.6490056651819712,
     "rmse_tavg_f": 0.8995277787716669,
     "mae_hdd": 0.5194038636449347,
     "mae_cdd": 0.19826468238973738,
     "excluded": {}
    },
    {
     "member": "EL_BLEND",
     "lead_day": 6,
     "vintage": 0,
     "n_target_days": 11,
     "n_pairs": 11,
     "n_provisional_days": 3,
     "scored": false,
     "bias_tavg_f": null,
     "mae_tavg_f": null,
     "rmse_tavg_f": null,
     "mae_hdd": null,
     "mae_cdd": null,
     "excluded": {}
    },
    "\u2026 94 more"
   ]
  },
  "\u2026 125 more places"
 ],
 "cache": {
  "state": "miss",
  "ttl_seconds": 900.0,
  "\u2026": "\u2026"
 }
}
```

### /blend?from=2026-10-08

```json
{
 "from": "2026-10-08",
 "days": 16,
 "station": null,
 "served_from": "v_dd_blend_drawable",
 "sources": {
  "tables": [
   "v_dd_blend_drawable"
  ],
  "method_versions": [
   "el_blend_v2"
  ],
  "method_hashes": [
   "04ae30886c80"
  ]
 },
 "stations_present": [
  "USW00003017",
  "USW00003102",
  "USW00023050",
  "USW00023152",
  "USW00023169",
  "USW00023174",
  "USW00023183",
  "USW00023185",
  "USW00023188",
  "USW00023232",
  "USW00024127",
  "USW00024131",
  "USW00024157",
  "USW00024229",
  "USW00024233",
  "USW00024257",
  "USW00093138",
  "USW00093193"
 ],
 "newest_issue_date": "2026-10-08",
 "computed_ts": [
  "2026-10-08T18:53:52.237446+00:00"
 ],
 "row_count": 154,
 "rows": [
  {
   "station_id": "USW00024233",
   "target_date": "2026-10-08",
   "issue_date": "2026-10-08",
   "lead_day": 0,
   "tavg_f": 59.914549916622654,
   "hdd": 5.085450083377346,
   "cdd": 0,
   "n_members_used": 4,
   "blend_mae": 0.9250198658920382,
   "best_member": "NBM",
   "best_member_label": "NBM",
   "best_member_mae": 1,
   "blend_n_target_days": 24,
   "method_version": "el_blend_v2",
   "method_hash": "04ae30886c80",
   "computed_ts": "2026-10-08T18:53:52.237446+00:00"
  },
  {
   "station_id": "USW00024233",
   "target_date": "2026-10-08",
   "issue_date": "2026-10-07",
   "lead_day": 1,
   "tavg_f": 60.68011844753274,
   "hdd": 4.319881552467258,
   "cdd": 0,
   "n_members_used": 5,
   "blend_mae": 0.8813768525001163,
   "best_member": "gridpoints_raw",
   "best_member_label": "NWS",
   "best_member_mae": 1.1785714285714286,
   "blend_n_target_days": 21,
   "method_version": "el_blend_v2",
   "method_hash": "04ae30886c80",
   "computed_ts": "2026-10-08T18:53:52.237446+00:00"
  },
  "\u2026 152 more"
 ],
 "not_served": {
  "rows": "every dd_blend_forecast row that is not drawable",
  "fields": [
   "no_blend_reason",
   "members",
   "scored",
   "beats_best_member",
   "drawable"
  ],
  "why": "pantry's contract (comment on dd_blend_forecast, migrations 282/283): \"Read blends ONLY through v_dd_blend_drawable. Nothing downstream reads dd_blend_forecast directly.\" The view carries drawable rows of the served method only, without these fields.",
  "pantry_proposal": "a view beside v_dd_blend_drawable, one row per dd_blend_forecast row of the served method: station_id, target_date, issue_date, lead_day, method_version, drawable, scored, beats_best_member, no_blend_reason, n_members_used, members, blend_mae, best_member, best_member_mae, and tavg_f/hdd/cdd only where drawable. Measured 2026-10-08: of 13,146 rows, 5,369 not drawable carry a NULL no_blend_reason (5,119 unscored with a value, 250 scored that do not beat the best member), so the view must also say which gate failed."
 },
 "region_blend": {
  "banked": false,
  "why": "dd_blend_forecast is keyed by station_id (21 stations); no region blend forecast is banked anywhere. EL_BLEND rows at place_kind region in dd_member_scores are scores of a blend pantry forms to score, not a banked forecast, so this API serves none and computes none."
 },
 "absence": null
}
```

### /band

```json
{
 "band": null,
 "percentile_rule": null,
 "absence": {
  "reason": "not_banked",
  "detail": "no GEFS member value is banked at our stations or region vectors, so a P5-P95 degree-day band cannot be formed from members (D-09-25-163: a band is members or history, never a guess). forecasts_gefs holds statistics already reduced over 31 members, per balancing authority, not the members."
 },
 "banked": {
  "table": "forecasts_gefs",
  "datasets": [
   "gefs_dd_region_daily",
   "gefs_t2m_region_6h"
  ],
  "places": "17 balancing authorities (AZPS, BANC, BPAT, ...), not the 21 stations or the 5 region vectors",
  "statistics": [
   "mean",
   "p10",
   "p50",
   "p90"
  ],
  "degree_days": "cdd and gwdd per BA day; no hdd; day boundary 06Z from four 6-hourly samples, not 24 local-standard hours",
  "cycles": "00, 06, 12, 18Z; 49 cycles 2026-09-26 00Z to 2026-10-08 12Z"
 },
 "requires": [
  "per-member daily tavg_f, hdd and cdd for each of the 31 GEFS members, per (cycle, member, station_id, target_date), on the 24 local-standard-hour day the scorer pairs against (D-09-25-165), with hours_covered and basis_complete",
  "the same per region vector (degree_day_region_weights), or a view that weights the station rows",
  "a score for the band (coverage of P5-P95 against truth by lead) before it is shown (D-09-25-163: nothing is shown before it is scored)"
 ]
}
```

### /desk?region=pnw&source=GFS&from=2026-10-08

```json
{
 "source_product": "GFS",
 "label": "GFS",
 "weighting": "population",
 "from": "2026-10-08",
 "days": 16,
 "tz": "America/Los_Angeles",
 "period_rule": "\u2026 degree_days.PERIOD_RULE \u2026",
 "lead_rule": "\u2026 dd_board.LEAD_RULE \u2026",
 "sources": {
  "tables": [
   "v_degree_days_region_forecast",
   "v_degree_days_model_delta",
   "v_degree_days_model_spread",
   "v_dd_member_scores_current"
  ],
  "scorer_versions": [
   "dd_member_scores_v2"
  ],
  "scorer_hashes": [
   "7a77ebf160c8"
  ],
  "blend_method_versions": [
   "el_blend_v2"
  ]
 },
 "regions": [
  {
   "region": "pnw",
   "weighting": "population",
   "newest_issued_ts": "2026-10-08T06:00:00+00:00",
   "prior_issued_ts": "2026-10-08T00:00:00+00:00",
   "period_anchor_issued_ts": "2026-10-08T13:00:00+00:00",
   "days": [
    {
     "target_date": "2026-10-08",
     "issued_ts": "2026-10-08T06:00:00+00:00",
     "lead_day": 0,
     "hdd": 2.047065535900533,
     "cdd": 1.116240852702006,
     "basis_complete": true,
     "normal": {
      "hdd": 8.611732502177423,
      "cdd": 0.1518632808288624
     },
     "change": {
      "hdd": 0.0,
      "cdd": 0.0,
      "prior_issued_ts": "2026-10-08T00:00:00+00:00",
      "spacing_comparable": true,
      "prior_sample_spacing_hours": 6
     },
     "change_absence": null,
     "score": {
      "state": "scored",
      "n_target_days": 28,
      "n_pairs": 28,
      "n_provisional_days": 3,
      "bias_tavg_f": 0.5806896682408047,
      "mae_tavg_f": 1.2297492618911203,
      "rmse_tavg_f": 1.5208383654119575,
      "mae_hdd": 1.0041001578404807,
      "mae_cdd": 0.23839640578484264,
      "vintage": 0
     },
     "absence": null
    },
    {
     "target_date": "2026-10-15",
     "issued_ts": "2026-10-08T06:00:00+00:00",
     "lead_day": 7,
     "hdd": 13.336213064055617,
     "cdd": 0.0,
     "basis_complete": true,
     "normal": {
      "hdd": 10.313876104307967,
      "cdd": 0.0565698344189157
     },
     "change": null,
     "change_absence": {
      "reason": "incomplete_basis",
      "message": "this issuance or the prior one is not basis_complete, so the view carries no value to difference",
      "prior_issued_ts": "2026-10-08T00:00:00+00:00",
      "basis_complete": true,
      "prior_basis_complete": false
     },
     "score": {
      "state": "scored",
      "n_target_days": 28,
      "n_pairs": 28,
      "n_provisional_days": 3,
      "bias_tavg_f": 0.6047163863892059,
      "mae_tavg_f": 2.75939007226467,
      "rmse_tavg_f": 3.7130486412816497,
      "mae_hdd": 2.148319806168221,
      "mae_cdd": 0.7369379690891549,
      "vintage": 0
     },
     "absence": null
    },
    {
     "target_date": "2026-10-23",
     "issued_ts": null,
     "lead_day": null,
     "hdd": null,
     "cdd": null,
     "basis_complete": null,
     "normal": null,
     "change": null,
     "change_absence": null,
     "score": null,
     "absence": {
      "reason": "no_issuance",
      "detail": "no GFS issuance holds this target date in the window"
     }
    },
    "\u2026 13 more"
   ],
   "periods": [
    {
     "window": "d01_05",
     "from": "2026-10-09",
     "to": "2026-10-13",
     "hdd": 59.686709,
     "cdd": 0.744161,
     "hdd_normal": 45.791373,
     "cdd_normal": 0.637566,
     "issued_ts": "2026-10-08T06:00:00+00:00",
     "days_present": 5,
     "days_required": 5,
     "complete": true
    },
    {
     "window": "d06_10",
     "from": "2026-10-14",
     "to": "2026-10-18",
     "hdd": 57.23424,
     "cdd": 0.0,
     "hdd_normal": 53.468678,
     "cdd_normal": 0.240198,
     "issued_ts": "2026-10-08T06:00:00+00:00",
     "days_present": 5,
     "days_required": 5,
     "complete": true
    },
    {
     "window": "d11_15",
     "from": "2026-10-19",
     "to": "2026-10-23",
     "hdd": null,
     "cdd": null,
     "hdd_normal": null,
     "cdd_normal": null,
     "issued_ts": "2026-10-08T06:00:00+00:00",
     "days_present": 4,
     "days_required": 5,
     "complete": false
    }
   ]
  }
 ]
}
```


| body | route | raw bytes | gzip -6 bytes |
|---|---|---:|---:|
| body_scores.json | /api/weather/dd/scores | 767,314 | 80,494 |
| body_blend_from_2026-10-08.json | /api/weather/dd/blend?from=2026-10-08 | 66,970 | 7,972 |
| body_band.json | /api/weather/dd/band | 1,281 | 703 |
| body_desk_pnw_gfs.json | /api/weather/dd/desk?region=pnw&source=GFS&from=2026-10-08 | 12,628 | 3,150 |

## 4. Plan receipts

`docs/receipts/dd-board-d091666/plans.md` has the table. `plans_raw.txt` has every plan line verbatim, and `explains.sql` the statements, which S6 rebuilds from the pinned SQL.

- Scores: 14.1 ms first, 5.8 ms warm (station); 3.1 ms (region). Plan: Seq Scan → Sort → Unique → Sort over 79 pages.
- Blend, default window: 5.8 ms first, 1.0 ms warm, on `idx_dbf_target`. One station: 0.1–1.9 ms on the primary key. A 30-day back window: 123 ms first, 4.1 ms warm, as a seq scan of 2,159 pages.

**Named risk.** The scores view's `DISTINCT ON` sorts every v2 row of a place_kind, and the scorer adds 2,880 rows a day. That is linear growth toward the 2 s line within a year (projection, `plans.md`). The fix is pantry's (§7).

## 5. TTLs and why

| memo | TTL | why |
|---|---|---|
| `dd/scores` | 900 s | The scorer writes once a day (the brief's 18:41Z schedule; first commit 18:53:52Z). A new day's scores are seen within 15 min of landing, at the cost of one ~5 ms read per quarter hour. Shorter buys nothing, because the data does not move in between. |
| `dd/blend` | 900 s | Written by the same script in the same transaction (`computed_ts` = scores `created_at`). |
| `dd/desk` | 300 s | The region board's TTL. The folds it shows land hourly (NWS, NBM) and every 6 h (the models). The desk composes the board memo (300 s) and the scores memo (900 s), and `inputs` states each one's build time. |
| `dd/band` | none | No read. |

## 6. Tests: red, then green, then the rehearsal

`tests/test_dd_board_d091666.py`, 44 tests.

- **Red** (`red_on_main.txt`): the tests and `dd_board.py` against main.py before the routes. 42 failed, 1 passed (the pin of the pure module's SQL). `test_S5_the_state_is_the_views_flag_not_a_count` was added after the rehearsal found a gap (below).
- **Green** (`green.txt`): this file plus `test_weather_dd_forecast_regions.py`, `test_vintages_d091644.py` and `test_weather_dd_ledger.py`: 166 passed. The full suite: 2,555 passed, 2 skipped.
- S1–S7 are the brief's. S2 and S4 test what the bank allows (§8).
- **Rehearsal** (`rehearse.py` → `reds.txt`): 16 breaks, one per rule, each in a throwaway copy of the tree. All 16 are red, the clean tree is green, and the working tree's md5s are identical before and after.
- The first run found one break that stayed green: "not yet scored" read off `n_target_days >= 11`. The bank's GFS/NBM/NWS cells cannot tell that from the view's flag. A test now flips one 28-day cell's flag, and the break is red.
- **Fixtures**: `tests/fixtures/dd_board_d091666/`, 8 files cut from live rows. Each was written from the text Neon returned and checked against the md5 and length Neon computed over that same text (`manifest.psv`, `verify.py`). The fixtures are every current score cell, every drawable blend row, the region board's three reads for pnw/population over 2026-10-08 + 16 days, and the 39 vector rows.

## 7. What the dashboard lane must know

- **Source dropdown.**
  - At a **station**, the members are `/scores` `members`, each with its `label` (print `label`; key on `member` / `source_product`). The blend is selectable at a (station, target date) only where `/blend` returns a row. Several issue dates may reach one target: take the newest `issue_date`; that is selection, not arithmetic.
  - At a **region**, the sources are `/forecast/regions` `sources` (five today, NBM included). There is no blend at a region; do not offer it.
- **Scoreboard.**
  - Use `/scores` `places[].cells[]`, keyed (member, lead_day). The window, frontier and versions come from `vintages[cell.vintage]`.
  - `scored: false` is "not yet scored"; the page chooses its words (D-09-25-76 spirit). `n_target_days` against `min_target_days` says how close a cell is.
  - Print `n_provisional_days` beside a cell scored on provisional truth (D-09-25-165).
  - For the region scoreboard, `?place_kind=region` is 864 cells rather than 767 KB.
- **Band toggle.** `/band` is an absence. Render the toggle disabled with `absence.detail`. When pantry banks members, the route changes and the toggle does not.
- **Desk.**
  - Call `/desk?source=<src>` (all regions) or with `&region=`.
  - Per day: print `hdd`/`cdd`; print `change` or, when it is null, the reason in `change_absence`; print the score from `score.state`.
  - `periods` are d01_05/d06_10/d11_15 under `period_rule`. Day 1 is anchored on the region's newest issuance of **any** source, as on the board.
  - `lead_day` is the scorer's key (UTC issuance date), not a Pacific day count.

## 8. What pantry would have to add

1. **A region blend, if the board is to draw one:** `dd_blend_region_forecast`, keyed (region, weighting, target_date, issue_date, method_version), with lead_day, tavg_f/hdd/cdd, members_present/missing_stations, scored, blend_mae, best_member(_mae), beats_best_member, drawable and no_blend_reason, plus its drawable view. Pantry already forms the region blend to score EL_BLEND at regions; it would bank what it scored.
2. **A blend status view** beside `v_dd_blend_drawable`: one row per `dd_blend_forecast` row of the served method, with drawable, scored, beats_best_member, no_blend_reason, n_members_used, members, the errors, and values only where drawable.
   - It must also say **which gate failed**: today 5,369 non-drawable rows have a NULL reason.
   - With it, `/blend` can serve S2 as the brief wrote it.
3. **GEFS members:** per (cycle, member, station_id, target_date) daily tavg_f/hdd/cdd on the 24-LST-hour day, with hours_covered and basis_complete. Then the same per region vector (or a weighting view), and a **score for the band** (P5–P95 coverage by lead) before it is shown (D-09-25-163).
4. **Scores at scale:** a current-cell table the scorer replaces, or an index on the DISTINCT ON key and order with the view rewritten as one `LATERAL … LIMIT 1` per cell (d091551), before the table reaches ~1 M rows.

## 9. What this brief got wrong

1. *"v_dd_blend_drawable holds 20 stations, leads 0–5, issue date 2026-10-08."* It holds 418 rows over **issue dates 2026-09-28..10-08**; 10-08 alone is 66 rows. In the window today + 16 days, 18 stations appear.
2. *"dd_member_scores was first written today"* is true, but its `as_of_date` is **2026-10-06** (frontier 10-03), not today. The scores lead to **15**, not 5 (leads 6–15 are mostly unscored).
3. **(2) cannot be built as written.** The fields it lists (`no_blend_reason`, `members`, `scored`, `beats_best_member`, `drawable`) and the non-drawable rows exist only in `dd_blend_forecast`, which pantry says nothing downstream reads. I kept pantry's contract and stopped at the proposal (§8.2).
4. *"A row that is not drawable is served with its reason."* Most non-drawable rows have a reason, but 5,369 do not: they failed the scored or beats-best gate, and the reason column stays NULL.
5. *"No degree-day band is derived from it anywhere yet."* `gefs_dd_region_daily` **is** a derived degree-day distribution (cdd/gwdd p10/p50/p90), but per BA, at P10/P90, on a 06Z day. It is not usable here, but it exists.
6. *"forecasts_gefs holds GEFS members."* It holds 31-member statistics, not members.
7. The region views carry **NBM** (five sources). `vintages.DD_SOURCES` still lists four, so `/forecast/regions/vintages` does not serve NBM. That is a one-line fix in the d091644 code, but it changes that lane's banked body, so it is left for its own change.
8. S2 and S4 as written assume a buildable blend status and band. They test the absence instead: no `dd_blend_forecast` read, and nothing served the view does not hold. For S4: no read, no number, no relabelled p10/p90.
