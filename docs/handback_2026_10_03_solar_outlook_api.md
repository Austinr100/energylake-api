# Handback — d091568 (energylake-api): the solar outlook API

**Spec:** pantry `docs/cc_spec_2026_10_03_solar_generation_outlook.md` §3.1, §3.3, §3.4 (G1–G5),
read with pantry `docs/handback_2026_10_03_solar_outlook_writer.md` and migration
`264_implied_generation_solar.sql` as built. **Ruling:** D-09-25-114 (with D-09-25-109, -75).
**Branch:** `claude/solar-outlook-api-d091568` (§7.1 on the name). No PR, no merge, no deploy.
**Read-only.** No writes, no DDL, no migration. Every production access was a `SELECT` or an
`EXPLAIN (ANALYZE, BUFFERS)` of one, through the Neon connector.
**Receipts:** `docs/receipts/solar-outlook-api-d091568/`.

## Verdict

- `GET /api/generation/solar/outlook` and `GET /api/generation/solar/sites` are in, with every block
  §3.1 names. The label is §3.3 verbatim on both.
- **G1–G5: 41 tests, red on HEAD, then green.** Ten deliberate breaks, each one fails its test
  (`reds.txt`). The full suite has 2207 passing (2166 before this lane).
- **Every read has a production `EXPLAIN (ANALYZE, BUFFERS)`** (`plans.md`). Each one goes down a
  primary key or `idx_tsv_series_ts`. The worst cold read is 30 ms and warm reads are about 1 ms.
  The two site tables are empty in production, so I also planned their reads at full size on a
  local Postgres 16. `SITE_LATEST_SQL` is a 5,264-buffer seq scan there (§6.3).
- **The issuance the route serves today is unscaled.** Production has 389 calibration lines and 0
  rows carrying one, so every hour shows the registry figure. That figure runs about +1,600 MW high
  at HUBSUM (§5.1). This is the most important finding, and it belongs to the writer.
- **`/sites` returns `absence` today.** `implied_gen_sites` and `implied_gen_site_latest` are empty
  because the backfill writes no site rows (§5.2).

## 1. Files

| file | what |
| --- | --- |
| `solar_outlook.py` | New, in the `season.py` shape. It holds the SQL (issuance, hours, scores, calibration, actuals, fleet, site latest, site units), the parameter parsers and the pure shaping (`build_outlook`, `build_sites`, `build_scores`, `build_calibration`, `build_actuals`, `build_fleet`). It also holds `LABEL`. |
| `main.py` | New section before the season block (`tests/test_season_regions.py` P1 treats everything after `import season` as season code). It has the two routes and `_solar_outlook_cache` / `_solar_sites_cache` (`_DDCache`, 300 s, single-flight, stale-while-revalidate, 15 s build backstop). The reads run inside `conn.transaction()` under `SET LOCAL statement_timeout = '5s'`, and the responses carry `_dd_envelope`'s `cache` block and headers. |
| `tests/test_solar_outlook.py` | G1–G5, the memo and timeout, the 400/404/503 contract, `/sites`, P1 over production's rows, and four `PG_` tests that run the SQL on a real Postgres. Those four skip where there is no `initdb`. |
| `tests/fixtures/solar_outlook_d091568/` | `spec_3_3_label.txt` (line 234 of the spec, verbatim) and `production_hubsum_2026_10_03.json` (production rows read by the route's own statements, 2026-10-03 17:20 UTC). |
| `docs/receipts/solar-outlook-api-d091568/` | `plans.md`, `plans_local_scale.py` with its output, `rehearse.py` with `reds.txt`, `red_head.txt`, `sample.py` and `sample_outlook_hubsum.json`. |

## 2. The routes

### `GET /api/generation/solar/outlook?area_kind=&area=[&init=][&model=]`

- **`area_kind`** is one of `hub`, `hub_sum`, `ba` or `state`.
- **`area`** is `NP15`, `ZP26` or `SP15` for a hub, `HUBSUM` (or omitted) for `hub_sum`, and an
  upper-case code for a BA or a state.
- **`init`** is an ISO instant and defaults to the newest issuance. **`model`** defaults to `gfs`,
  which is the only model the writer banks.
- **Errors:** bad parameters return 400 naming the allowed set. An `init` that is not banked returns
  404. A valid area with nothing banked returns 200 with `absence.reason = "no_issuance"`. A database
  failure returns 503.

| block | rule |
| --- | --- |
| `issuance` | `model`, `init_ts`, `method_version`, `previous_init_ts`, and also `previous_method_version`, `source_posted_ts` (the newest), `lead_h_first` and `lead_h_last`. |
| `hours[]` | The §3.1 fields plus `calibration_id`, `previous_registry_mw` and `previous_calibrated_mw`. **`previous_mw` is like for like** with the figure the hour shows: calibrated against calibrated where the hour is calibrated, otherwise registry against registry. It is null when the previous issuance does not reach that hour (§7.6). |
| `unscaled` | `true` when no hour carries `calibrated_mw`. G4's "unscaled" note comes from this flag; the page writes the words. |
| `calibration` | One entry per **lead** band (five; `dam_comparable` has no line, per writer §6.4). An entry is **the line the rows carry, by `calibration_id`**, or null. A line that exists but was not applied to this issuance is not reported as its scaling. |
| `scores` | One entry per band (six), with `registry` and `calibrated`, plus `caiso_dam` beside `dam_comparable` for hub areas only. Each value is the newest row **of that band and that `who`**, or `"not yet scored"` when there is no row, `scored` is false, or the row is for another `method_version`. Nothing falls back to another band or to an older window. |
| `score_progress` | For each unscored (band, who): `{n_days, n_hours, window_end, min_days: 14}`, or null when no row exists. The page can then say "9 of 14 days" without seeing a number from another band. |
| `actuals[]` | `caiso_renewables_hourly` `{hub}:Solar` for a hub. For HUBSUM, the three hubs summed on hours where all three exist (the writer's `store.actual_series` rule). For CISO, `caiso_fuel_mix_hourly` `solar`. Other areas get `[]`. The values are the source's own, negative night values included. |
| `caiso_dam[]` | `caiso_renewables_fcst_dam`, **hub and hub_sum only. The key is absent for any other area** (G2). |
| `label` | §3.3 verbatim. |
| `fleet` | `ac_mw_total` and `n_sites` come from the issuance's rows. `by_mount_basis`, `by_dc_basis`, `registry_units` and `registry_ac_mw` come from `implied_gen_sites` for the area. |

### `GET /api/generation/solar/sites?target=|day=[&area_kind=&area=][&model=]`

Exactly one of `target` (an hour, on the hour) or `day` (a Pacific day of 23, 24 or 25 hours) must
be given. The response has one row per plant: `plant_code`, `plant_name`, coordinates, `ac_mw`,
`hub`, `ba_code`, `state`, and either `implied_mw` / `outage_mw_subtracted` (for `target`) or
`implied_mwh` / `outage_mwh_subtracted` / `hours_covered` (for `day`). Each plant also has
**`units[]`** giving each generator's `dc_mw`, `mount`, tilt and azimuth with **all four** `*_basis`
values (§7.4), so the map can say which values are defaults. Only the newest `init_ts` present is
served. Today it returns `absence.reason = "no_site_rows"`.

## 3. Sample response (production rows, through the route)

`/api/generation/solar/outlook?area_kind=hub_sum`, served by `main.py` over production's rows as
banked 2026-10-03 17:20 UTC. The full body (29 KB) is `sample_outlook_hubsum.json`, and
`sample.py` regenerates it. Abridged:

```json
{
 "label": "Weather-implied generation. What the weather says these plants should produce, from open-source models (pvlib) and public plant data. Not a forecast of metered output and not any plant's schedule.",
 "tech": "solar_pv", "area_kind": "hub_sum", "area": "HUBSUM", "unit": "MW",
 "issuance": {
  "model": "gfs", "init_ts": "2026-10-01T12:00:00+00:00", "method_version": "solar_pv_v1",
  "previous_init_ts": "2026-09-30T06:00:00+00:00", "previous_method_version": "solar_pv_v1",
  "source_posted_ts": "2026-10-01T16:18:58+00:00", "lead_h_first": 1, "lead_h_last": 66
 },
 "hours": [
  {"target_ts": "2026-10-01T18:00:00+00:00", "lead_h": 7, "lead_band": "h07_24", "weather_step_h": 1,
   "registry_mw": 20130.341, "calibrated_mw": null, "calibration_id": null,
   "outage_mw_subtracted": 118.855, "previous_mw": 19866.396,
   "previous_registry_mw": 19866.396, "previous_calibrated_mw": null},
  "… 64 more …",
  {"target_ts": "2026-10-03T00:00:00+00:00", "lead_h": 37, "lead_band": "h25_48", "weather_step_h": 1,
   "registry_mw": 6399.588, "calibrated_mw": null, "calibration_id": null,
   "outage_mw_subtracted": 152.924, "previous_mw": null,
   "previous_registry_mw": null, "previous_calibrated_mw": null}
 ],
 "unscaled": true,
 "calibration": {"h01_06": null, "h07_24": null, "h25_48": null, "h49_120": null, "h121_240": null},
 "scores": {
  "h07_24": {
   "registry":   {"window_start": "2026-09-04", "window_end": "2026-10-01", "n_hours": 501, "n_days": 28,
                  "bias_mw": 1610.826, "mae_mw": 1625.513, "mae_pct_installed": 6.7147, "rmse_mw": 2482.932, "r": 0.99464, "scored_at": "2026-10-03T16:25:30.531978+00:00"},
   "calibrated": {"window_start": "2026-09-11", "window_end": "2026-10-01", "n_hours": 370, "n_days": 21,
                  "bias_mw": -37.747, "mae_mw": 403.086, "mae_pct_installed": 1.6651, "rmse_mw": 717.84, "r": 0.99565, "scored_at": "2026-10-03T16:25:30.531978+00:00"}
  },
  "h121_240": {"registry": "not yet scored", "calibrated": "not yet scored"},
  "dam_comparable": {
   "registry":   {"n_days": 28, "mae_pct_installed": 6.2333, "bias_mw": 1484.651, "…": "…"},
   "calibrated": {"n_days": 21, "mae_pct_installed": 1.9238, "bias_mw": 41.779, "…": "…"},
   "caiso_dam":  {"window_start": "2026-09-04", "window_end": "2026-10-01", "n_hours": 523, "n_days": 22,
                  "bias_mw": 670.066, "mae_mw": 723.113, "mae_pct_installed": 2.987, "rmse_mw": 1195.068, "r": 0.99512, "scored_at": "2026-10-03T16:25:30.531978+00:00"}
  },
  "…": "h01_06, h25_48, h49_120 shaped as h07_24 (all scored)"
 },
 "score_progress": {"h121_240": {"registry":   {"n_days": 0, "n_hours": 0, "window_end": "2026-10-01", "min_days": 14},
                                 "calibrated": {"n_days": 0, "n_hours": 0, "window_end": "2026-10-01", "min_days": 14}}},
 "actuals":   [{"target_ts": "2026-10-01T18:00:00+00:00", "mw": 17146.76}, "… 38 more, through 2026-10-03T02:00Z …"],
 "caiso_dam": [{"target_ts": "2026-10-01T18:00:00+00:00", "mw": 19375.25}, "… 65 more …"],
 "fleet": {"ac_mw_total": 24208.3, "n_sites": 1027, "by_mount_basis": [], "by_dc_basis": [],
           "registry_units": 0, "registry_ac_mw": 0},
 "absence": null,
 "cache": {"state": "miss", "built_at": "…", "age_seconds": 0.0, "ttl_seconds": 300.0, "build_seconds": 0.0, "refreshing": false}
}
```

At 18:00 UTC on Oct 1 the registry figure is 20,130 MW, the hub actual is 17,147 MW and CAISO's
day-ahead is 19,375 MW. That is the registry's +1,600 MW bias on display (§5.1).

## 4. Tests, red then green

`red_head.txt`: on HEAD (`f6a8cd9`), the file cannot import `solar_outlook` and the routes do not
exist. `reds.txt`: each break below is applied to a throwaway copy of the tree, its tests must fail
there, and the clean tree must pass all 41.

| test | pins | break that turns it red |
| --- | --- | --- |
| G1 (3 + PG) | No row means "not yet scored". `scored=false` means "not yet scored", with counts. A number from one band never appears under another. The statement is one `LIMIT 1` lateral per `(lead_band, who)`, naming the band, who, area, kind and method. On a real PG, the newest unscored window wins over an older scored one, and another method's row does not count. | Borrow the same `who` from another band. Serve `scored=false`. Drop `who` from the lateral. |
| G2 (5 + PG) | `caiso_dam` is present for hub and hub_sum, at the top level and beside `dam_comparable` only. It is absent for BAs and states **even when a hostile fake offers it**, and those areas' reads never name the DAM dataset. HUBSUM counts only the hours where all three hubs exist. | Offer DAM to every area. Ask for `caiso_dam` scores on every kind. |
| G3 (4 + PG) | The previous issuance is ranked with `p.model = %(model)s`. On a real PG with an IFS run seeded *between* two GFS runs, GFS's previous issuance is the earlier GFS run and not the IFS run. Hours beyond the previous issuance's horizon get null. `previous_mw` is like for like. | Drop the model from the ranking (the d091557 mistake). Make `previous_mw` always the registry figure. |
| G4 (3) | No line means `calibrated_mw` null, every band null and `unscaled: true`. A line no row carries is not reported, even if read. Calibrated rows name their line. | Report any line for the band. |
| G5 (4) | `LABEL` equals the spec's line 234, and it appears on the outlook (full and empty) and on `/sites`. | Change one word. |
| D-09-25-75 | The first statement is `SET LOCAL statement_timeout`, and a second request is a memo hit with no read. | Drop the timeout. |
| P1 | Production's HUBSUM rows through the route: 66 hours, unscaled, `h121_240` "not yet scored", `previous_mw` null from lead 37, 39 actuals and 66 DAM hours. | — |

**G4 first came up NOT red**, and I am reporting that as a finding rather than loosening the break.
The route reads `implied_gen_calibration` only for ids the rows carry, so a builder that reported
an unused line was never given one, and the test passed with the builder broken. The test now
calls the builder directly as well.

## 5. Found in production (the writer's lane, not fixed here)

1. **No row carries a calibration, although 389 lines exist.** `implied_gen_area_hourly`: 61,776
   rows, `calibration_id` set on 0. `implied_gen_calibration`: 389 lines (fit_end 09-09 … 10-01).
   The backfill wrote the area rows before any line existed, and the score job fits lines but does
   not apply them back to stored rows. Two consequences:
   - **Clause 3 cannot hold today.** The page should show the calibrated figure for hub areas and
     CISO, but there is none to show. The API serves the registry figure with `unscaled: true` and
     does not compute a calibrated figure itself: that would be a number the writer never stored,
     under a `calibration_id` no row carries.
   - **The score card and the chart disagree.** `scores.h07_24.calibrated` reads 1.67% of
     installed, while the line on screen is the registry figure, which scores 6.71%. The page must
     put the registry score beside the registry line. A number is shown with the score of its own
     band and of its own figure (clause 2).

   This resolves itself when a live cycle runs (the writer applies `lines` at write time). If the
   history should be calibrated too, the writer must re-apply the lines to stored rows, which needs
   a ruling.
2. **No live cycle has run.** The 36 issuances are all backfill: one `dam_comparable` cycle a day,
   leads 1–66, `hub` / `hub_sum` / `ba` only. So there are **no `state` rows** (`?area_kind=state`
   answers `no_issuance`), **no lead above 66** (`h121_240` cannot be scored), and **no site rows**
   (`/sites` and the fleet's by-basis lists are empty). The newest issuance is 2026-10-01 12Z, two
   days old. §3's gate ("after … the writer has banked at least three days of cycles") is not met by
   live cycles. The brief said the tables hold the backfill, and this lane served what is there.
3. **Negative actuals.** CAISO's hub actuals go below zero at night (HUBSUM −50.5 MW at 2026-10-03
   02:00Z). They are served as published.

## 6. Choices the spec left open

1. **Scores are the newest, not as of the issuance.** `?init=` for an old issuance still shows the
   newest score row per band.
2. **`hours[]` does not mark which hours are `dam_comparable`.** The rows do not store the writer's
   selection, and recomputing it here would duplicate `scoring.dam_comparable_init`. If the page
   needs the mark, the writer should store it.
3. **`implied_gen_site_latest` has no index on `target_ts`.** Its key is
   `(tech, plant_code, model, target_ts)`, so `/sites` reads the whole table: 5,264 buffers and
   15–27 ms at full size. That is bounded (one cycle at most) and memoised. An index on
   `(tech, model, target_ts)` in a writer migration would fix it.
4. **TTLs:** both memos are 300 s, with the cycles four a day and the scores daily. The statement
   timeout is 5 s against ≤ 31 ms measured. The build backstop is 15 s, as for peak demand.
5. **`model` is a parameter.** It defaults to `gfs` and is checked against `('gfs',)`. Every read is
   then keyed on the full `(tech, area_kind, area, model)`, and IFS needs only a one-word change.

## 7. What the spec got wrong

1. **The branch name.** The spec and brief say `claude/solar-outlook-api-d091568`. The session was
   provisioned on `claude/solar-outlook-api-d091568-qn2xuh` and told to push only there. I pushed the
   same commits to both, as d091551 did.
2. **§3's precondition did not hold** (§5.2). The backfill exists, but no live cycle does, so there
   are no state areas, no leads beyond 66, and no site rows for `/sites` or the map.
3. **§3.1 "calibration: per lead band … or null" and "scores: per lead band".** The two blocks have
   different band sets. `dam_comparable` is a score band but has no line (writer §6.4), so
   `calibration` has five keys and `scores` has six.
4. **"mount and the three `*_basis` values".** There are four: `dc_basis`, `mount_basis`,
   `tilt_basis` and `azimuth_basis` (§1 clause 5 names four defaults). All four are returned.
5. **`fleet.n_sites` is a count of units, not plants.** The writer counts rows of
   `implied_gen_sites`, one per `(plant_code, generator_id)`: 1,027 units at HUBSUM.
6. **"`previous_mw` (the same target hour from the previous issuance)" is ambiguous** in two ways.
   (a) Which figure: it is like for like with the hour's own figure (§2), and both raw values are
   also served. (b) How much of the horizon it covers: in the backfill, "previous" is the previous
   *day's* `dam_comparable` cycle, which is 18–30 h earlier (here 30 h, 12Z after 06Z). So it reaches
   only leads ≤ 36 of a 66-hour issuance. With four live cycles a day, it will be the run 6 h earlier.
7. **Clause 3 ("the page shows the calibrated figure for areas that have an actual")** assumed the
   stored rows would carry one. None does (§5.1). The score card can show calibrated scores for a
   figure the chart cannot draw.
8. **G2 "caiso_dam appears only for hub areas"** has to cover `hub_sum` as well (clause 6 and the
   CHECK in 264 both do). Read that way, it holds.
9. **"Ship EXPLAIN for each read"** cannot say anything about scale for the two site tables while
   they are empty in production. Their full-size plans are local (`plans_local_scale.txt`), and
   §6.3 is the result.

---

**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/solar-outlook-api-d091568
