# Handback — d091590 (energylake-api): the wind outlook API

**Spec:** pantry `docs/cc_spec_2026_10_03_wind_generation_outlook.md` §3, with the 2026-10-04
addendum (§2, the route). **Rulings:** D-09-25-126, **D-09-25-127** (with -109, -114, -75).
**Branch:** `claude/jolly-planck-4lxjw1` (§7.1 on the name). No PR, no merge, no deploy.
**Read-only.** Every production access was a `SELECT` or an `EXPLAIN (ANALYZE, BUFFERS)` of one,
through the Neon connector. Nothing was written to Neon.
**Receipts:** `docs/receipts/wind-outlook-api-d091590/`.

## Verdict

- `GET /api/generation/wind/outlook` and `GET /api/generation/wind/sites` are in. They have
  solar's shape and **share solar's code**: the parsers, the issuance statements, the score
  statement and its shaping, the actuals statement and the HUBSUM rule, the memo, the timeout
  and the 503 contract. `solar_outlook.py` gained two optional arguments and nothing else. Every
  one of its SQL strings and constants is byte-identical (checked against HEAD), and its 41 tests
  pass unchanged.
- **D-09-25-127 holds on the live cycle.** At HUBSUM, init 2026-10-04 12Z:
  - Leads 1–66 show the calibrated figure.
  - Leads 67–120 show the registry figure only, with `calibrated_absent_reason: "beyond_fitted_leads"`.
  - Leads 121–240 show the registry figure with `"no_line"`.
  - At lead 120 the writer stored a calibrated 1,556 MW. It is not served anywhere in the body.
    The hour shows its registry figure, 5.8 MW.
- **The fitted leads are derived, because `implied_gen_calibration` does not hold them.**
  The derivation reproduces the addendum's measurement (49–66 for every hub-sum, hub and CISO
  `h49_120` line). **Pantry owes two columns** (§5.1).
- **Both seams are named, and both are read off the rows, not constants.** The weather seam is
  48 | 49 (`hrrr_80m` → `gfs_100m`). The calibration seam is 66 | 67. A test fails if the
  module holds 66 or 67, and another moves the fit from 52 to 120 and watches the seam follow.
- **Tests:**
  - 50 new tests, and 12 deliberate breaks, each red (`reds.txt`).
  - The full suite is 2,289 passing. HEAD had 2,239.
- **Every read has a production plan** (`plans.md`). Each goes down a primary key or
  `idx_tsv_series_ts`. The slowest is `HOURS_SQL` at 12 ms; the rest are 0.06–9.9 ms.

## 1. Files

| file | what |
| --- | --- |
| `wind_outlook.py` | New. Wind's SQL and shaping, importing solar's where the payload is the same. It holds `HOURS_SQL` (wind's columns plus the previous row's line and lead), `CALIBRATION_SQL` (the lines with their fitted leads derived), wind's `actual_pairs` / `ACTUALS_SQL`, `FLEET_SQL` and `SITES_SQL` (one lateral per plant). It also holds the shaping: `gate` (D-09-25-127), the seams, `build_days`, the scores (with ZP26's absence), the fleet, the outlook and the sites. The words it carries are `LABEL`, `ATTRIBUTION` and `CAISO_ISSUE_SENTENCE`. |
| `solar_outlook.py` | Two hooks for sharing. `_scores_sql(kind, extra_cols=())` and `build_scores(..., extra=())` add wind's columns. `build_actuals(..., pairs=None)` takes wind's pairs. With the defaults, the output is solar's, byte for byte. |
| `main.py` | A new section after solar's, before the season block. It holds the two routes and `_wind_outlook_cache` / `_wind_sites_cache` (`_DDCache`, 300 s, single-flight, stale-while-revalidate). The routes reuse solar's `_solar_serve`, `_solar_400`, `SOLAR_STATEMENT_TIMEOUT` (5 s) and `SOLAR_BUILD_TIMEOUT` (15 s) rather than copying them. |
| `tests/test_wind_outlook.py` | W1–W9, the shared-code check (S), the D-09-25-75 memo and timeout, the 400/404/503 contract, three PG tests on a real Postgres, and P1 over production's rows. |
| `tests/fixtures/wind_outlook_d091590/` | `production_2026_10_04_12z.json` holds the live cycle's rows for HUBSUM, CISO, ZP26 and SP15, read by the route's own statements. It also holds the 323 plant facts and the site-latest windows. `writer_strings.json` holds the label, the notice, CAISO's sentence and the seam lead, read out of pantry @ `2dbb43a` by AST. |
| `docs/receipts/wind-outlook-api-d091590/` | `plans.md`, `sample.py` with the five banked bodies it writes, and `rehearse.py` with `reds.txt`. |

## 2. The routes

### `GET /api/generation/wind/outlook?area_kind=&area=[&init=][&model=]`

**Parameters.**
- `area_kind`, `area` and `init` are as for solar.
- `model` is `hrrr_gfs`. That is the writer's name for the joined issuance, and the only value
  accepted. `gfs` is a 400.
- **Responses:** 400 for bad parameters, 404 for an unbanked `init`, 200 with `absence` for an
  empty area, 503 when the database is down.

What differs from solar's body:

| block | rule |
| --- | --- |
| `hours[]` | Adds `weather_source`, `cap_mw_subtracted`, `scored_registry_mw` and **`calibrated_absent_reason`** (`null` / `"beyond_fitted_leads"` / `"no_line"`). **`calibrated_mw` is served only when the hour's lead is inside its own line's fitted leads.** `previous_calibrated_mw` is gated the same way: by the previous issuance's line, at the previous issuance's lead. So `previous_mw` stays like for like with what each issuance may show. |
| `seams` | `weather`: `{last_lead, first_lead, before, after, target_ts_before, target_ts_after, band_before, band_after}`, at the first change of `weather_source`. `calibration`: `{last_calibrated_lead, first_registry_lead, …, reason}`, at the end of the calibrated run that opens the issuance. It is `null` when no hour is calibrated, or when every hour is. |
| `calibration` | Solar's block (the line the rows carry, per lead band). Each line adds `fit_lead_min`, `fit_lead_max`, `fit_rows`, `fit_issuances`, `fit_leads_source: "derived"`, and `applied_lead_min` / `applied_lead_max` (the leads the writer applied it to). `calibration_fit_leads_basis` states the derivation in words. |
| `days[]` | One entry per Pacific day: `hours_in_day`, `hours_covered`, `complete`, `figure` (`calibrated` / `registry` / `mixed`), `crosses_calibration_seam`, `crosses_weather_seam`, `energy_mwh`, `peak_mw`, `peak_ts`, and `parts[]` by figure. **A day across the calibration seam has two parts and a null total and peak.** A calibrated hour is never added to a registry hour (§6.1). |
| `scores` | Solar's, plus `actual_source`, `mw_yes`, `mw_unknown` and `mw_no` on each score. **ZP26:** no score read is made. `scores` and `score_progress` are null, and `scores_absence` is `{reason: "no_score_row", detail: "… scored inside HUBSUM"}`. |
| `actuals` | Hubs and HUBSUM use `caiso_renewables_hourly {hub}:Wind` (HUBSUM only on hours where all three hubs report). CISO uses `caiso_fuel_mix_hourly wind`. Other areas get `[]`. The two sources are never crossed. |
| `caiso_dam`, `caiso_dam_issued` | `caiso_renewables_fcst_dam {hub}:Wind`, and the sentence beside it. **Hub and hub_sum only; both keys are absent elsewhere.** |
| `label`, `attribution` | The writer's `LABEL_WIND` and `CURVE_DATA_NOTICE` (ODbL-1.0 §4.3), verbatim. |
| `weather_height_m` | `{hrrr_80m: 80, gfs_100m: 100}`, for the site card's "distance from the weather's height". |
| `fleet` | Comes from `implied_gen_wind_sites` for the area: `nameplate_mw_total`, `n_sites`, `scored_mw_total`, `by_counts_in_hub_actual[]`, and `export_caps[]` (group, cap, basis, plants, nameplate). |

### `GET /api/generation/wind/sites?target=|day=[&area_kind=&area=][&model=]`

The parameters are solar's. The response has one row per plant:
- **Plant facts, each with its basis:** turbine model, count, rotor, hub height (null on 3 plants,
  `basis: "none"`), the curve's turbine and hub height, `counts_in_hub_actual` and its basis,
  `export_cap_group` / `export_cap_mw` / `export_cap_basis`, and `hrrr_dist_km`.
- **Implied output:** `weather_sources` and the lead range. For `target`: implied MW, outage MW and
  cap MW subtracted, and `capacity_factor` against nameplate. For `day`: the same as MWh, plus
  `hours_covered`.

`figure: "registry"`, because no line is fitted per plant. **`export_cap_mw` is the group's cap.**
SunZia South and North each carry 2,131 MW, shared between them (§6.4).

## 3. D-09-25-127, as built

`implied_gen_calibration` has `fit_start`, `fit_end`, `n_hours` and `n_days`, but no leads.
`CALIBRATION_SQL` derives the leads by the writer's own fit rule, which I read in pantry @ `2dbb43a`:
`implied_gen/scoring.py:fit_line` and `scripts/implied_wind.py:score_day` → `wind_scoring.fit_lines_wind`.

**The rule.** A line is fitted on the rows that meet all of these:
- the area's own rows, in that line's band;
- target on a Pacific day in `[fit_start, fit_end]`;
- `scored_registry_mw > 0`;
- an actual present.

**The derivation** is `min` / `max(lead_h)` over the same rows, with three differences:
1. It also requires `written_at <= fitted_at`. That keeps out rows written later, such as a
   re-backfill.
2. It bounds `init_ts` to `[fit_start − 240 h, fit_end + 1 d)`, so the read stays a PK range.
3. It cannot see "an actual present". So `fit_rows` can be a few above the line's `n_hours`:
   504 against 499 at HUBSUM `h49_120`, and 504 against 504 at CISO. An hour with no actual
   could only narrow the leads at an edge, and no edge lead is missing today.

Derived on production 2026-10-04:

| line | area | band | fitted leads | fit_rows / n_hours | applied to |
| --- | --- | --- | --- | --- | --- |
| 673–675 | HUBSUM | h01_06 · h07_24 · h25_48 | 1–6 · 7–24 · 25–48 | 167/166 · 504/500 · 672/667 | the same |
| **676** | HUBSUM | h49_120 | **49–66** | 504/499 | **49–120** |
| 668 / 672 | NP15 / SP15 | h49_120 | 49–66 | 491/486 · 501/496 | 49–120 |
| 684 | CISO | h49_120 | 49–66 | 504/504 | 49–120 |

**The boundary moves by itself.** When live cycles add leads 67–120 to a fit window, the next
line's `fit_lead_max` rises and the seam moves with it. W1 runs this with a fit to 52, 66, 90 and
120. At 120 the seam's reason becomes `no_line`, because lead 121 is beyond every line.

## 4. Sample response (production rows, through the route)

`/api/generation/wind/outlook?area_kind=hub_sum`, served by `main.py` over the live cycle's rows.
The full bodies are `sample_outlook_{hubsum,ciso,zp26}.json` (HUBSUM is 132 KB) and
`sample_sites_{target_2026_10_04T20Z,day_2026_10_05}.json`. `sample.py` regenerates all five.
Abridged:

```json
{
 "label": "…windpowerlib… (writer's LABEL_WIND, verbatim)",
 "attribution": "Contains information from the Wind Turbine Library … ODbL-1.0 … (writer's CURVE_DATA_NOTICE, verbatim)",
 "tech": "wind",
 "area_kind": "hub_sum",
 "area": "HUBSUM",
 "unit": "MW",
 "issuance": {
  "model": "hrrr_gfs",
  "init_ts": "2026-10-04T12:00:00+00:00",
  "method_version": "wind_v1",
  "previous_init_ts": "2026-10-02T06:00:00+00:00",
  "previous_method_version": "wind_v1",
  "source_posted_ts": "2026-10-04T13:46:32+00:00",
  "lead_h_first": 1,
  "lead_h_last": 240
 },
 "hours": [
  {
   "target_ts": "2026-10-06T11:00:00+00:00",
   "lead_h": 48,
   "lead_band": "h25_48",
   "weather_source": "hrrr_80m",
   "weather_step_h": 1,
   "registry_mw": 3404.952,
   "calibrated_mw": 2255.697,
   "calibrated_absent_reason": null,
   "calibration_id": 675,
   "outage_mw_subtracted": 81.292,
   "cap_mw_subtracted": 0.0,
   "scored_registry_mw": 3404.952,
   "previous_mw": null,
   "previous_registry_mw": null,
   "previous_calibrated_mw": null
  },
  {
   "target_ts": "2026-10-06T12:00:00+00:00",
   "lead_h": 49,
   "lead_band": "h49_120",
   "weather_source": "gfs_100m",
   "weather_step_h": 1,
   "registry_mw": 1701.523,
   "calibrated_mw": 2648.609,
   "calibrated_absent_reason": null,
   "calibration_id": 676,
   "outage_mw_subtracted": 32.053,
   "cap_mw_subtracted": 0.0,
   "scored_registry_mw": 1701.523,
   "previous_mw": null,
   "previous_registry_mw": null,
   "previous_calibrated_mw": null
  },
  "… 49–65 …",
  {
   "target_ts": "2026-10-07T05:00:00+00:00",
   "lead_h": 66,
   "lead_band": "h49_120",
   "weather_source": "gfs_100m",
   "weather_step_h": 1,
   "registry_mw": 397.124,
   "calibrated_mw": 1807.805,
   "calibrated_absent_reason": null,
   "calibration_id": 676,
   "outage_mw_subtracted": 7.688,
   "cap_mw_subtracted": 0.0,
   "scored_registry_mw": 397.124,
   "previous_mw": null,
   "previous_registry_mw": null,
   "previous_calibrated_mw": null
  },
  {
   "target_ts": "2026-10-07T06:00:00+00:00",
   "lead_h": 67,
   "lead_band": "h49_120",
   "weather_source": "gfs_100m",
   "weather_step_h": 1,
   "registry_mw": 283.084,
   "calibrated_mw": null,
   "calibrated_absent_reason": "beyond_fitted_leads",
   "calibration_id": 676,
   "outage_mw_subtracted": 5.579,
   "cap_mw_subtracted": 0.0,
   "scored_registry_mw": 283.084,
   "previous_mw": null,
   "previous_registry_mw": null,
   "previous_calibrated_mw": null
  },
  "… 68–119 …",
  {
   "target_ts": "2026-10-09T11:00:00+00:00",
   "lead_h": 120,
   "lead_band": "h49_120",
   "weather_source": "gfs_100m",
   "weather_step_h": 3,
   "registry_mw": 5.758,
   "calibrated_mw": null,
   "calibrated_absent_reason": "beyond_fitted_leads",
   "calibration_id": 676,
   "outage_mw_subtracted": 0.152,
   "cap_mw_subtracted": 0.0,
   "scored_registry_mw": 5.758,
   "previous_mw": null,
   "previous_registry_mw": null,
   "previous_calibrated_mw": null
  },
  "… 121–240: calibrated_absent_reason \"no_line\" …"
 ],
 "unscaled": false,
 "seams": {
  "weather": {
   "last_lead": 48,
   "first_lead": 49,
   "before": "hrrr_80m",
   "after": "gfs_100m",
   "target_ts_before": "2026-10-06T11:00:00+00:00",
   "target_ts_after": "2026-10-06T12:00:00+00:00",
   "band_before": "h25_48",
   "band_after": "h49_120"
  },
  "calibration": {
   "last_calibrated_lead": 66,
   "first_registry_lead": 67,
   "target_ts_before": "2026-10-07T05:00:00+00:00",
   "target_ts_after": "2026-10-07T06:00:00+00:00",
   "band_before": "h49_120",
   "band_after": "h49_120",
   "reason": "beyond_fitted_leads"
  }
 },
 "calibration": {
  "h49_120": {
   "calibration_id": 676,
   "slope": 0.644592,
   "intercept_mw": 1551.8214,
   "fit_start": "2026-09-05",
   "fit_end": "2026-10-02",
   "n_hours": 499,
   "n_days": 28,
   "fitted_at": "2026-10-04T15:09:24.962656+00:00",
   "lines_on_rows": 1,
   "fit_lead_min": 49,
   "fit_lead_max": 66,
   "fit_rows": 504,
   "fit_issuances": 29,
   "fit_leads_source": "derived",
   "applied_lead_min": 49,
   "applied_lead_max": 120
  },
  "…": "h01_06, h07_24, h25_48: fitted 1–6, 7–24, 25–48",
  "h121_240": null
 },
 "days": [
  {
   "day": "2026-10-06",
   "hours_in_day": 24,
   "hours_covered": 24,
   "complete": true,
   "first_lead": 44,
   "last_lead": 67,
   "figure": "mixed",
   "crosses_calibration_seam": true,
   "crosses_weather_seam": true,
   "weather_sources": [
    "hrrr_80m",
    "gfs_100m"
   ],
   "energy_mwh": null,
   "peak_mw": null,
   "peak_ts": null,
   "parts": [
    {
     "figure": "calibrated",
     "first_lead": 44,
     "last_lead": 66,
     "hours": 23,
     "energy_mwh": 47578.223,
     "peak_mw": 2648.6094608891917,
     "peak_ts": "2026-10-06T12:00:00+00:00"
    },
    {
     "figure": "registry",
     "first_lead": 67,
     "last_lead": 67,
     "hours": 1,
     "energy_mwh": 283.084,
     "peak_mw": 283.084,
     "peak_ts": "2026-10-07T06:00:00+00:00"
    }
   ]
  },
  "… 10 more, each one figure …"
 ],
 "scores": {
  "h49_120": {
   "registry": {
    "mae_pct_installed": 13.5343,
    "n_days": 28,
    "actual_source": "hub_actual",
    "mw_yes": 7183.3,
    "mw_unknown": 2840.7,
    "mw_no": 0,
    "…": "…"
   },
   "calibrated": {
    "mae_pct_installed": 9.2402,
    "n_days": 17,
    "…": "…"
   }
  },
  "h121_240": {
   "registry": "not yet scored",
   "calibrated": "not yet scored"
  },
  "…": "…"
 },
 "scores_absence": null,
 "actuals": [],
 "caiso_dam": "[19 hours …]",
 "caiso_dam_issued": "CAISO's day-ahead is issued between 06:09 PT the day before and 06:09 PT on the day",
 "weather_height_m": {
  "hrrr_80m": 80,
  "gfs_100m": 100
 },
 "fleet": {
  "nameplate_mw_total": 10024.0,
  "n_sites": 113,
  "by_counts_in_hub_actual": "[unknown 2,840.7 MW / 68 plants, yes 7,183.3 MW / 45]",
  "export_caps": "[sunzia: cap 2,131 MW, plants 66923 + 66924, 3,650.2 MW nameplate, with basis]"
 },
 "absence": null,
 "cache": "{…}"
}
```

This matches the addendum's §0 to the MW:
- Lead 48: registry 3,405 / calibrated 2,256.
- Lead 49: 1,702 / 2,649.
- Lead 120: registry 6, with the stored calibrated 1,556 withheld.
- Scores: 5.18, 6.06, 7.40 and 7.51 day-ahead comparable, against CAISO's 4.84.

## 5. Owed by other lanes

1. **Pantry owes `implied_gen_calibration.fit_lead_min` and `fit_lead_max`** (smallint, set by
   `fit_line` from the rows it fitted on). `fit_rows` is also wanted, as `n_hours` before the
   actual filter, or the leads of the hours with an actual. With them, `CALIBRATION_SQL` becomes a
   4-row PK lookup and the derivation (§3) is deleted. Until then, the derivation is the writer's
   rule copied into SQL, and **a change to `fit_line` has to change it too.** That coupling is
   what the columns remove.
2. **Pantry stores a calibrated figure where its own fit has no data** (leads 67–120 on every
   area). The route withholds it, but it is in the table. The next consumer of
   `implied_gen_area_hourly.calibrated_mw` will believe it, as this lane would have without the
   ruling. The writer should write `calibrated_mw` null beyond `fit_lead_max`, or this route
   remains the only place D-09-25-127 holds.
3. **The `h49_120` score covers leads 49–66 only.** Its rows come from the backfill, which stops at
   66. So the "score on the near side" of the calibration seam is honest: it is the same leads.
   But nothing in a score row says which leads it covers, and the band's name reads 49–120. The
   same pair of columns on `implied_gen_scores` would let the page say "scored on 49–66".

## 6. Choices the brief left open

1. **The days block is built in the route.** The solar route has no daily energy or peak (§7.3),
   so there was no route to follow. It follows the solar PAGE's rule (dashboard
   `solarOutlook.dayCells`, test D1): parts by figure, never one total. A day that crosses only
   the weather seam keeps its total, because both sides are the same figure. It is flagged
   `crosses_weather_seam`.
2. **The calibration seam is the end of the opening calibrated run.** If a band in the middle had
   no line while a later one did, the later calibrated hours would still be served. The seam would
   name the first gap, with its reason. That does not happen today.
3. **A line whose fitted leads cannot be derived (no rows) covers nothing.** Its band's hours fall
   back to the registry figure, with `beyond_fitted_leads`. A missing receipt reads as no data, not
   as all data.
4. **`export_cap_mw` is the group's cap on each plant**, as the writer stores it. `fleet.export_caps`
   states each group once. The page must not add a group's cap twice.
5. **The `/sites` read is one lateral per plant.** Solar's reads `implied_gen_site_latest` by a
   target window, which walks the whole table (solar handback §6.3). Wind's would have walked
   77,520 rows. CLAUDE.md's d091551 shape gives 323 PK ranges in 3–10 ms.
6. **TTLs, the timeout and the build backstop are solar's** (300 s, 5 s, 15 s), and are shared, not
   copied.

## 7. What this brief got wrong

1. **The branch name.** The brief says `claude/wind-outlook-api-d091590`. This session was
   provisioned on `claude/jolly-planck-4lxjw1` and told to push nowhere else without permission.
   The commits are there only. Solar's lane pushed to both; I did not.
2. **The model is not named, and it is not `gfs`.** The writer's rows carry
   `model = 'hrrr_gfs'` (`wind_store.MODEL_WIND`). A route copied from solar's `MODELS = ("gfs",)`
   would answer `no_issuance` for every area.
3. **"Read from the issuances the fit used."** The calibration row holds a date window, not
   issuances or leads. Leads were derivable (§3), but only by copying the writer's fit rule into
   SQL. That is the coupling §5.1 asks pantry to remove.
4. **"The way the solar route labels its lead-120 day."** The solar route has no days block. The
   lead-120 day is labelled by the solar page (`dayCells`: two parts, never one total). This route
   now does the labelling for wind (§6.1), so the wind page can draw it without re-deriving it.
5. **CAISO's sentence differs from the writer's.** The brief reads *"CAISO's day-ahead is issued
   between 06:09 PT the day before and 06:09 PT on the day"*. Pantry's `CAISO_ISSUE_SENTENCE` reads
   *"CAISO's day-ahead forecast: issued between … (as far as we know)."* I served the brief's words,
   and both are banked in `writer_strings.json`. One should be ruled the sentence; the hedge
   "as far as we know" is the writer's and is arguably the more honest of the two.
6. **"Not measured: whether a score row exists for `h49_120`."** It does, at every scored area:
   HUBSUM registry 13.53 % over 28 days and calibrated 9.24 % over 17 days. **It covers leads
   49–66 only** (§5.3).
7. **The GFS side turns 3-hourly at lead 115, not 121.** `weather_step_h = 3` from lead 115 on the
   live cycle. The GFS run joined to the 12Z HRRR is 6 h older, so GFS f120 is lead 114. The hours
   are still hourly rows (interpolated). The page may want to mark the step.
8. **"Hub areas pair with hub actuals"** holds, but **no hub actual exists yet for the live
   window.** At banking, `caiso_renewables_hourly` had nothing from 2026-10-04 12Z on, so
   `actuals` is `[]` for hubs and HUBSUM. CISO's fuel mix had 5 hours. That is not a defect, but
   the page will open with no actual line.
9. **The previous issuance is two days back.** The only earlier issuance is the 2026-10-02 06Z
   backfill, which reaches 66 leads. So `previous_mw` covers leads ≤ 12 of the live cycle, and is
   null even there wherever the hour is calibrated: the backfill carried no calibration, so there
   is nothing like for like. With four live cycles a day, the previous issuance will be 6 h old.

---

**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/jolly-planck-4lxjw1
