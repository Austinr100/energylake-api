# Handback d091679: CPC outlooks API — curves, verdicts and banked outlooks, served as the bank states them

**Lane:** d091679 · **Repo:** `energylake-api` · **Branch:** `claude/quirky-cori-1jrmyu` (no PR, no merge, no deploy).
**Neon:** reads only, project `fancy-block-96153928` production, 2026-10-09 ~22Z to 2026-10-10 00:35Z. Nothing written. No migration. **Pantry** (`energylake-pantry`) read only: migrations 286 and 289, the three CPC handbacks, `scripts/cpc_curves_writer.py`, `cpc_archive/products.py`.

## 0. The short version

- **Four routes under `/api/weather/cpc`**: `/curves`, `/curves/vintages`, `/places`, `/outlooks`. No existing route changed.
- **Every curve value comes from `v_cpc_curves_drawable` and nowhere else.** That covers percentiles, equal-odds columns, `member_odds`, the label and the band. A place or issuance the bank does not draw is 200 with `curve: null` and the verdict's reasons.
- **One departure from pantry's contract, stated in every payload's `sources`.** The view's comment says nothing downstream reads `cpc_outlook_curves` or `cpc_curve_verdicts` directly. The brief needs three things the view does not hold:
  - the verdict of a cell that is not drawable;
  - `reading`, `history` and `notes`;
  - which places exist at all.

  So two narrow reads go past the view:
  - **Verdicts:** `cpc_curve_verdicts`, only the version in force, picked by the view's own rule.
  - **Window rows:** `cpc_outlook_curves` window rows, identity and provenance columns only. No value column; a test pins that (§3).

  §8 has the view pantry would add to end both reads.
- **Drawable today, measured.**
  - All **17 places that have curves** (15 stations, plus `pnw` at both weightings) are drawable on the newest issuance of both products.
  - The other **13 places** are under 30 base years. The writer writes no curve there, and every one of their cells says `under_30_base_years`.
  - The only written-but-undrawable cells are **KLAX and KSFO in JJA** (season does not beat equal odds). Summer issuances there are frames with `curve: null`.
- **Ruling 172's label.** The bank has no "overconfident" flag. Its words are `band_claim` (`about_9_in_10` | `measured`), `band_share` and `band_sentence`. They ride verbatim, and the API adds no word of its own.
  - KSAN 814temp, issued 10-08, strong odds: `measured`, 0.725, "… held about 7 in 10 past outcomes when CPC's odds were strong …".
  - Strong odds are not always overconfident: 15 strong cells hold `about_9_in_10`.
- **Weeks 3-4: features, not curves.** It is banked as features (2022-01-07 → 2026-10-02). Monthly and seasonal: nothing is banked, neither bytes nor features, so `/outlooks` states `not_banked` for all eight codes.
- **The CPC curve does not extend the degree-day board past day 15 (§7).** The 8-14 window ends at issued + 14, and weeks 3-4 has no curve.
- **Tests:** 81 new, red then green. Red: 77 failed on the base `main.py`. Green: full suite 2,725 passed, 3 skipped (baseline 2,644 + 81).
- **Mutation rehearsal:** 24 breaks, 24 red, tree restored byte for byte (sha-256).

## 1. Routes and exact body shapes

SQL and shaping are in `cpc_outlooks.py`; routes are in `main.py`, section "/api/weather/cpc".

**The house rules each route follows:**
- Every read goes through `_dd_timed_read`, which wraps it in `SET LOCAL statement_timeout = '2s'` inside a transaction (D-09-25-75).
- Every route serves through a single-flight `_DDCache` memo.
- Every body carries the degree-day `cache` block, plus the `Cache-Control: max-age=900`, `X-Cache` and `Age` headers (`_dd_envelope`).

| route | reads (each one statement) | memo key, TTL | refusals |
|---|---|---|---|
| `GET /api/weather/cpc/curves?place_kind=&place=[&weighting=]` | `CURVES_SQL` n=1, once per product, concurrently; plus the `/places` memo, for the 404 and `place_verdict` | `cpc/curves` (place), 900 s | 400 place_kind / place / weighting; 404 unknown place; 503 |
| `GET /api/weather/cpc/curves/vintages?product=&place_kind=&place=[&weighting=][&n=]` | `CURVES_SQL` n, once; plus the `/places` memo | `cpc/curves/vintages` (product, place, n), 900 s | 400 product, n (1–14, default 7; above the cap refused, never trimmed), place params; 404; 503 |
| `GET /api/weather/cpc/places` | `PLACES_SQL`, `PLACES_NEWEST_SQL`, concurrently | `cpc/places`, 900 s | 503 |
| `GET /api/weather/cpc/outlooks[?product=610\|814\|wk34\|monthly\|seasonal][&geometry=true]` | `OUTLOOK_FEATURES_SQL`, `OUTLOOK_VINTAGE_SQL` (+ `OUTLOOK_GEOMETRY_SQL`), concurrently | `cpc/outlooks` (families, geometry), 900 s | 400 product, geometry (needs one family); 503 |

**Parameters.**
- `place_kind` is `station` or `region`.
- A station is a GHCN id, and its weighting must be absent: the bank keys a station's weighting as `''`.
- A region is `[a-z0-9_]`, and `weighting` defaults to `population`.
- An unknown place is any (kind, place, weighting) triple with no cell in the verdicts in force. For example, `socalgas_territory` with `load_share_365d` is 404.
- 503 covers both a database that is down and a statement timeout (psycopg `QueryCanceled`). A memo build past its 10 s ceiling is also 503.

### The shared frame: VINTAGE

`/curves` and `/curves/vintages` use the same shaping (`cpc_outlooks.vintage`), so a player's frame and the board's newest curve are the same object:

```
VINTAGE = {
  issued_date, written, valid_start, valid_end, season, strength,
  reading, history, notes, writer_version, source_r2_key,
  source_format_epoch, written_at,                  # the window row, verbatim
  drawable,                                         # the verdict cell's column
  verdict: { season, strength, drawable, n, n_eff, skill, t, strength_verdict,
             share_p5_p95, share_p25_p75, season_n, season_n_eff, season_skill,
             season_t, verdict, history_years_in_base, band_basis, band_basis_n,
             band_basis_n_eff, band_share, band_claim, band_sentence,
             backtest_version, reasons: [ {code, clause, ...} ] } | null,
  curve: { label, band_claim, band_share, band_sentence, band_basis,
           backtest_version, season, strength, n_history_years, history_first,
           history_last, member_odds, method_version, method_hash,
           source_content_sha256,
           window: { valid_start, valid_end, empty_class, tavg_p05 tavg_p25 tavg_p50
                     tavg_p75 tavg_p95 hdd_p05..hdd_p95 cdd_p05..cdd_p95
                     eq_tavg_p05 eq_tavg_p50 eq_tavg_p95 eq_hdd_* eq_cdd_* },
           days: [ { day_index, target_date, empty_class, <the same 24 columns> } ] } | null,
  absence: { reason: not_written | not_drawable | no_verdict, detail,
             reasons? } | null }
```

**Field rules:**
- `curve` is the view's rows or null.
- `days` are the view's day rows; `window` is its window row (window-mean tavg, HDD/CDD window totals).
- Column names are the bank's. The equal-odds columns are `eq_*`, at P5/P50/P95 only, as stored.
- `reasons` names which term of `ccv_drawable_ck` is false, and only where the bank's own `drawable` is false:
  - `season_does_not_beat_equal_odds` or `season_too_thin` (clause 3)
  - `under_30_base_years` (clause 5)
  - `no_band_claim` (clause 4)
- It never sets `drawable`. If a row ever says `drawable = false` with no false term, the code is `unstated`.

### Bodies

- **`/curves`:** `{ place_kind, place, weighting, drawable_rule, verdict_versions[], sources, products_drawn[], products: [ { product, cpc_title, newest_written_issued_date, vintage: VINTAGE|null, place_verdict: {drawable_cells, cell_count, reason_codes, history_years_in_base, verdicts}, absence } ], absence, cache }`.
  - Each product is its own newest written issuance. Today that is 610temp 10-09 and 814temp 10-08.
  - `place_verdict` sums the place's 12 cells per product from the `/places` memo, so a place with no curve still says why.
- **`/curves/vintages`:** `{ place_kind, place, weighting, product, cpc_title, n, n_cap: 14, order: "oldest first", drawable_rule, verdict_versions, sources, issued_dates[], drawn_count, vintages: [VINTAGE], absence, cache }`.
- **`/places`:** `{ drawable_rule, verdict_versions[], sources, newest_written_issued_date: {610temp, 814temp}, place_count, cell_count, drawable_count, places: [ { place_kind, place, weighting, products: [ { product, drawable_cells, cell_count, newest_curve: {issued_date, valid_start, valid_end, season, strength, method_version, drawable, reasons}|null, newest_curve_absence, seasons: [ { season, cells: [ {season, strength, drawable, <all CELL_KEYS>, backtest_version, reasons} ] } ] } ] } ], absence, cache }`.
- **`/outlooks`:** `{ families: [ { family, absence, products: [ { product, cpc_title, issued_date, layers, content_sha256[], r2_key[], format_epoch[], parser_version[], parsed_at, bytes_newest_issued_date, feature_count, features: [ {layer, feature_index, category, category_source, prob, valid_start, valid_end, intersects_west, west_area_fraction, centroid: [lon, lat]|null, regions, west_geojson?} ], absence } ] } ], geometry, geometry_note, sources, cache }`.
  - Product-level absence reasons: `not_banked`, `not_parsed` (bytes banked, no feature row) or `no_layer_in_family`.
  - Family-level absence reasons: `nothing_banked` or `no_features`.

**Excerpt: KSAN 814temp, the strong-odds frame.** From `docs/receipts/cpc-outlooks-d091679/body_curves_ksan.json`; `…` marks a cut.

```json
{"product": "814temp", "newest_written_issued_date": "2026-10-08",
 "vintage": {"issued_date": "2026-10-08", "written": true, "valid_start": "2026-10-16", "valid_end": "2026-10-22",
   "season": "SON", "strength": "strong", "reading": "midpoint", "history": "all",
   "notes": ["30 complete base windows 1991-2020"], "drawable": true,
   "verdict": {"verdict": "beats", "season_n_eff": 69.958, "season_t": 2.525, "history_years_in_base": 30,
               "band_basis": "pooled_stations", "band_claim": "measured", "band_share": 0.7249, "drawable": true, "reasons": [], "…": "…"},
   "curve": {"label": "History at USW00023188, 1991-2025, reweighted by NOAA CPC's published 8-14 day odds",
     "band_claim": "measured", "band_share": 0.7248968363136176,
     "band_sentence": "The band runs from the 5th to the 95th percentile of the reweighted years. In the backtest it held about 7 in 10 past outcomes when CPC's odds were strong (60% or more for one side). Measured over the 15 verdict stations together: too few past windows at this place alone.",
     "member_odds": [{"station": "USW00023188", "category": "A", "level": 60.0, "p_a": 65.0, "p_n": 31.67, "p_b": 3.33, "weight": 1.0,
                      "reading": "inside the above 60 contour, read at the band midpoint 65.00; completed by CPC's rule"}],
     "window": {"tavg_p05": 66.571, "tavg_p50": 70.148, "tavg_p95": 72.330, "eq_tavg_p05": 63.821, "eq_tavg_p50": 66.857, "eq_tavg_p95": 72.0, "cdd_p50": 36.04, "…": "…"},
     "days": [{"day_index": 0, "target_date": "2026-10-16", "tavg_p05": 65, "tavg_p25": 68, "tavg_p50": 70, "tavg_p75": 72.54, "tavg_p95": 77, "…": "…"}, "… 6 more"]},
   "absence": null}}
```

**Excerpt: KDEN 610temp, a place with no curve:**

```json
{"product": "610temp", "newest_written_issued_date": "2026-10-09",
 "place_verdict": {"drawable_cells": 0, "cell_count": 12, "reason_codes": ["under_30_base_years"],
                   "history_years_in_base": [24], "verdicts": ["beats"]},
 "absence": {"reason": "not_written", "detail": "cpc_outlook_curves holds no 610temp curve for this place on the 2026-10-09 issuance",
             "place_reason_codes": ["under_30_base_years"]}}
```

The full bodies are in `docs/receipts/cpc-outlooks-d091679/body_*.json`. Those are the dashboard lane's vectors. They were served byte for byte by the real routes over the fixtures (`sample.py`), and `test_banked_body_is_what_the_route_serves` holds them.

## 2. What the measurements found

### `cpc_curve_verdicts`

- **Key:** `uniq_ccv_cell` (product, place_kind, place, weighting, season, strength, backtest_version), plus PK `verdict_id`.
- **Columns:** 35 (the table in 286 exactly).
- **One version:** `cpcv_2026-10-03_d0a93d5811`.
  - scored_at 2026-10-08 21:51:37.36165Z, one value for all 720 rows.
  - truth_frontier 2026-10-03, `cpc_curves_v1` / `e99c71234013`, rules `cpc_curve_verdicts_v1` / `5ffe9e61df72`, min_n_eff 30.
- **Cells:** 720 = 2 products × 30 places × 4 seasons × 3 strengths. **396 drawable.**
- **The 324 that are not drawable:**
  - 312 are at 13 places under 30 base years: KDEN 24; the 22-year group USW00003102, USW00023152, USW00023293, USW00093138, caiso_np15/sp15 and socalgas_territory; the 26-year group USW00003145 and desert_sw.
  - 12 are KLAX (USW00023174) and KSFO (USW00023234) in JJA: `does_not_beat`.
- **No cell has `band_basis = 'none'`**, and no cell is `too_thin`. Band claims:
  - `about_9_in_10`: 278 (moderate 171, strong 15, weak 92).
  - `measured`: 442 (cell 316, pooled_stations 126, all of the pooled ones strong).

### `v_cpc_curves_drawable`

- **Columns:** 61 (definition as in 286).
- **Not in the view:** `reading`, `history`, `notes`, `writer_version`, `source_r2_key`, `source_format_epoch`, any undrawable row, and any cell's `drawable` / `verdict` for a place with no curve.

### `cpc_outlook_curves`

- **First read (10-09 ~22Z):** 238,000 rows. 1,000 issuances per product, 2024-01-04 → 2026-10-08. 17 places.
- **By the fixture read (10-10 00:18Z):** 610temp had **2026-10-09**, but 814temp's newest was still **2026-10-08**, though `cpc_outlook_vintage` banks 814temp 10-09. Meanwhile the backfill was writing 814temp 2023-04 issuances (136 rows each, every ~4 s).
- **Rows per place per issuance:** 610temp **6** (5 days + window), 814temp **8** (7 + window).
- **Constant across one (issuance, place):** `member_odds`, `notes`, `season`, `strength`, the history years, `empty_class` and the source. Measured over all 34,000 groups, none varies. `reading` = `midpoint` and `history` = `all` everywhere (pinned by `coc_reading_ck`). `empty_class` is false on every row.
- **Notes:**
  - At 30-year stations: "30 complete base windows 1991-2020".
  - At pnw: "35 common history years across 4 stations".
  - At KSAC (23232) the note varies by window: e.g. "28 complete base windows 1991-2020 | fewer than 30 base years: the classes are not CPC's 30-year terciles". The verdict still counts KSAC at 30 base years. The two measures differ (base windows for that calendar window vs base years with ≥ 330 days), and the note is served verbatim.

### Drawable today

All 17 written places, both products, newest issuance, season SON. Strength of the newest cells:
- **610temp:** weak at 9 stations, moderate at 6 stations and pnw.
- **814temp:** moderate at 13 stations and pnw; weak at KABQ; **strong at KSAN** (pooled band, measured 0.725).

### `cpc_outlook_features` (parsed) and `cpc_outlook_vintage` (bytes)

| product | features: issuances, span | newest parsed | bytes newest |
|---|---|---|---|
| 610temp / 610prcp / 814temp / 814prcp | 72 each, 2026-07-29 → 10-08 | 10-08 | 10-09 (the parse runs for D−1) |
| wk34temp | 247, 2022-01-07 → 2026-10-02 (epochs wk34_v1, wk34_v2) | 10-02 | 10-02 |
| wk34prcp | 248, same span | 10-02 | 10-02 |
| seastemp, seasprcp, monthupd_{temp,prcp}_{e1,e2} | **none** | — | **none** |

Both longrange datasets are `planned` with `refresh_cron 'manual'`.

**Feature shape:**
- `west_geom` is a MULTIPOLYGON in SRID 4269, clipped to the West window.
- It is NULL, with a NULL centroid, on 6,489 features that do not reach the West.
- Newest issuance: 12–24 features per product, about 0.75 MB of GeoJSON each.
- Weeks 3-4 carries EC as `EC` at 33.

### Routes that already touch CPC

- **`/api/weather/regime`** reads `cpc_outlook_vintage` (issued/valid metadata, `lean: null`).
- **`/api/weather/outlooks`** reads `forecasts_climate_outlook` (discussions and the GIF registry).
- **The Brief routes** (`/api/weather/brief/*`, `/api/briefs/daily/*`) serve prewritten `content_md`.
- **None** reads a curve, a verdict or a feature.

## 3. Reads, and the departure from pantry's contract

`CURVES_SQL` answers one question: the product's n newest issuances at one place.

1. **The issuances come from the whole bank,** not the place (`DISTINCT issued_date … LIMIT n` on `idx_coc_issued`). A place with no curve therefore never walks the table. This is the CLAUDE.md trap, and C04 shows it: 0.83 ms for KDEN at n = 14.
2. **Per issuance, three lookups:**
   - the place's window row (`uniq_coc_row`);
   - the verdict in force for its cell: one LATERAL … LIMIT 1 per method_hash, then `uniq_ccv_cell`;
   - the view's rows, through a LATERAL fenced with `OFFSET 0`.
3. **Why the fence.** Unfenced, the planner flattens the LATERAL into a hash join and walks all 12,104 rows of the place: 112 ms warm (C00b).
4. **One statement,** so the verdict and the view come from one snapshot.

**The contract tests:**
- `test_S_no_value_is_read_from_a_base_table` pins that every value column, `member_odds`, the label and the band are referenced only through `d.` (the view) or `vv.` (the verdict cell).
- `test_S_a_curve_is_the_views_rows_or_nothing` drops the view's rows under a verdict that says drawable, and no curve is made.

`PLACES_SQL` takes the version in force with the view's own rule (newest `scored_at`, then `backtest_version`, per method_hash), as one LATERAL … LIMIT 1 per method_hash, not DISTINCT ON (d091551).

## 4. Body sizes and timings

**Plans:** `docs/receipts/cpc-outlooks-d091679/plans.md`, with every line in `plans_raw.txt`.

| read | execution |
|---|---:|
| C01 /curves 610 KSAN | 1.25 ms |
| C02 /curves 814 KSAN | 0.92 ms |
| C03 vintages 814 pnw n=14 | 8.11 ms |
| C04 vintages KDEN n=14 | 0.83 ms |
| P01 places | 3.56 ms |
| P02 places newest | 0.33 ms |
| O01 features | 10.9 ms (first read) |
| O02 vintage index | 0.19 ms |
| O03 geometry wk34 | 6.13 ms |

**Bodies, served bytes** (cache block included) and gzip −6:

| body | route | raw | gzip |
|---|---|---:|---:|
| body_curves_ksan.json | /curves KSAN | 14,280 | 3,147 |
| body_curves_pnw_population.json | /curves pnw | 17,423 | 4,999 |
| body_curves_kden.json | /curves KDEN | 3,190 | 1,116 |
| body_vintages_814temp_ksan_n14.json | /curves/vintages 814 KSAN n=14 | 92,953 | 9,442 |
| body_vintages_610temp_pnw_population.json | /curves/vintages 610 pnw n=7 | 50,262 | 10,060 |
| body_places.json | /places | 628,931 | 68,001 |
| body_outlooks.json | /outlooks (all families, no geometry) | 30,633 | 2,768 |
| (not banked) | /outlooks?product=wk34&geometry=true | 1,492,221 | 536,072 |

**Vintages bodies by n:**
- A station: 7.3 KB (n=1), 39–46 KB (n=7), 77–93 KB (n=14).
- pnw: 8.7–10 KB (n=1), 50–60 KB (n=7), **100–121 KB (n=14), the largest body: 22 KB gzip**.
- KDEN at n=14 is 7.5 KB of absences.

**`/places` is the heavy one:** 629 KB raw, 68 KB gzip. It is 720 cells, and each repeats its band_sentence. It is one 15-min memo and the picker needs it once.

## 5. The memo TTL

**900 s on all four memos.**

Nothing these routes read moves more than once a day:
- CPC posts the dailies about 19:05–19:30Z;
- the capture banks them about 20:23–21:26Z;
- the features parse runs at 21:41Z (for D−1);
- the curves writer runs at 22:47Z;
- the verdicts writer runs Sunday 06:11Z.

So 15 minutes is the longest a new issuance waits to be seen, for one read of 1–11 ms per key per quarter hour. Shorter buys nothing; longer leaves the evening's curve unseen for longer. This is the same reasoning and value as `dd/scores` (d091666).

## 6. Tests: red, then green, then the rehearsal

`tests/test_cpc_outlooks_d091679.py`, 81 tests:

| tests | what they check |
|---|---|
| T1 | /curves at KSAN equals the hand-read view rows and window row, both products, value for value |
| T2 | KDEN with its 12 cells' reason; a restated not-drawable cell with `does_not_beat`; every one of the 324 undrawable cells names its term (312 / 12) |
| T3 | the strong frame's claim, share and sentence verbatim; no API word anywhere |
| T4 | oldest first at n 1/7/14; one read for every frame; n 15/0/−1/100/seven/7.5 → 400 |
| T5 | /places against the count query; every cell is the stored row; the version |
| T6 | monthly and seasonal `not_banked`; stored features; `not_parsed`; parsed trails banked; geometry on request |
| T7 | 404 ×6, 400 ×12 (refused before any read), 503 down / statement timeout / build ceiling |
| T8 | pinned SQL; plan receipts rebuilt from the pin; body bytes |
| S | the rules |
| banked bodies | 7 |

- **Red** (`red_on_main.txt`): this file, `cpc_outlooks.py` and the fixtures against `main.py` at `149e314`: **77 failed, 4 passed**. The four are the pure-module pins and receipts.
- **Green** (`green.txt`): this file 81 passed. Full suite **2,725 passed, 3 skipped**. The existing 2,644 are unchanged.
- **Rehearsal** (`rehearse.py` → `reds.txt`): **24 breaks over the six rules, all red**. The clean tree is green, and the working tree's sha-256 is identical before and after.
  - The first run found one break that stayed green: the 30-year floor read as 25. KDEN's 24 is under both.
  - `test_T2_every_undrawable_cell_names_its_term` now counts all 312 cells at 22, 24 and 26 years, and that break is red.
- **Fixtures:** `tests/fixtures/cpc_outlooks_d091679/`, 18 files, gzip of the exact text Neon returned.
  - Each is checked against the sha-256 and length Neon computed over that text (`manifest.psv`, `verify.py`, and on every load).
  - Total 756 KB; 4.4 MB raw.
  - Sha-256 of the four place snapshots:
    - KSAN `36314a78…98c39` (610), `49fb50f1…eacaa` (814)
    - pnw/population `b469f954…0ed5c9` / `6ad5be59…cfa0fb`
    - pnw/load_share_365d `47e1d265…6e5913` / `43d74414…98ccbcd`
    - KDEN `b7753214…66765d` / `b07c31a2…190462`

    The full hashes are in the manifest.

## 7. What the dashboard lane must know

- **Picker:** `/places`.
  - List every place. Disable a place whose `products[].drawable_cells == 0`, and print why from its cells' `reasons` codes (`under_30_base_years` with `history_years_in_base`, or `season_does_not_beat_equal_odds`). The words are the page's (D-09-25-76 spirit).
  - `newest_curve.drawable` says whether today's frame draws.
  - Key places on (place_kind, place, weighting). Map the GHCN id to a display name as the board does.
- **What to draw:** `/curves` `products[].vintage.curve`.
  - Band P5–P95 and the inner P25–P75 band.
  - Median `*_p50`.
  - The equal-odds baseline `eq_*_p05 / p50 / p95` beside it (equal-odds has no P25/P75).
  - Per `days[].target_date`, for tavg, hdd and cdd. `window` is the window-mean tavg and HDD/CDD window totals.
- **What to print with it, always:**
  - `curve.label`.
  - `band_sentence` (ruling 172; where odds were strong it says how much the band held).
  - `notes`.
  - The verdict's `band_basis` when it is `pooled_stations`: the sentence already says it.
- **Do not print** a "9 in 10" of your own, or call the band confident or not.
- **What to state as absent:**
  - `curve: null` with `absence.reason` `not_written` (with `place_reason_codes`), `not_drawable` (with `reasons`) or `no_verdict`.
  - In `/outlooks`, each product's `absence` (`not_banked` for monthly and seasonal; `not_parsed` when bytes lead the parse).
- **The player:** `/curves/vintages?product=&n=14` holds every frame, oldest first.
  - Step locally. No frame costs a request.
  - A frame with `curve: null` is a frame to show as absent, not to skip.
  - Each product steps alone. The two products' newest issuances can differ by a day, as they do now.
- **How a curve joins the degree-day board:** by `target_date` and place.
  - At a station, place = `station_id`.
  - At pnw, the board region `pnw` with the same `weighting`.
  - **It does not extend the board past day 15.** The 6–10 window is issued + 6 … + 10 and the 8–14 is issued + 8 … + 14.
  - With CPC's issuance landing the evening before, the curve covers about board days 5 to 13. That is **beside** the models (pantry 286 clause 6: never in their place) and inside the 0–15 the models already reach.
  - The only CPC product beyond day 15 is weeks 3-4 (issued + 15 … + 28). It is banked as polygons, with no station odds and no curve. Draw it as the `/outlooks` `wk34` map, or a stated absence on the board.
- **Overlap between issuances:** two consecutive 6–10 issuances share four target dates. The page picks the newest, which is selection, not arithmetic.
- **Units:** tavg °F, HDD/CDD °F-days (base 65), as stored.
- **Maps:** `/outlooks?product=wk34&geometry=true` returns the West-clipped polygons (`geometry_note`). It is ~1.5 MB raw / 0.5 MB gzip, so fetch it on demand, never on a poll.

## 8. What pantry would have to add

1. **`v_cpc_curve_status`:** one row per `cpc_outlook_curves` window row with its cell's verdict in the version in force.
   - Identity, valid window, season, strength, reading, history, notes, writer_version, source_r2_key, source_format_epoch.
   - The verdict cell's drawable, verdict, history_years_in_base, band_basis and band_claim.
   - **No value column.**

   Plus **`v_cpc_curve_verdicts_current`:** the cells of the version in force. With these two, this API reads no base table and the departure in §3 ends: a two-line change in `CURVES_SQL` and `PLACES_SQL`.
2. **The view's `newest` CTE** as one LATERAL … LIMIT 1 per method_hash on `idx_ccv_version`, not DISTINCT ON over the whole table. It runs once per issuance in a vintages read, and the table grows 720 rows a week (plans.md, the named risk).
3. **The 814temp 2026-10-09 issuance.** It is banked, but not written by 10-10 00:18Z, while 610temp 10-09 is. Whether the 22:47Z fire wrote 610 only, or the backfill is still in front of it, is the writer's arm to say; the arm is not installed yet (both datasets are `planned`).
4. **Weeks 3-4 as a curve** if the board is to reach past day 15: station odds from the wk34 polygons and the two-category reader (d091653 §1b), through the same verdict machinery. Today there is none.

## 9. What this brief got wrong

1. **"`cpc_outlook_curves` … reading, history, notes … serve verbatim" and "`cpc_curve_verdicts`, `v_cpc_curves_drawable`"** read as if the API may read both tables. Pantry's view comment says nothing downstream reads them directly, and the view carries none of reading, history, notes or an undrawable cell's verdict. I read past it narrowly and said so in every payload (§3), rather than drop /places and the reasons. If the architect prefers pantry's contract kept strictly, as d091666 did, /places and the reasons become absences until §8.1 lands.
2. **"Where CPC's odds are strong the band is overconfident: the bank's label for that."** No such label exists.
   - The bank's words are `band_claim` / `band_share` / `band_sentence`.
   - Strong is not always overconfident: 15 strong cells hold about 9 in 10.
   - Some weak and moderate bands are under-confident, holding about 19 in 20, also `measured`.
   - The payload carries the claim and sentence; T3 tests those, not a flag.
3. **"Which places are drawable today (15 stations and pnw at last read)."** True of places with curves: 15 stations + pnw at **two** weightings, 17 written places. But "drawable" is per season and strength, not per place.
   - KLAX and KSFO are drawable in SON and not in JJA.
   - 13 places are drawable in no cell at all and get no curve.
4. **"Returns the newest issuance of both products"** assumes they share a date. Measured: 610temp 10-09, 814temp 10-08. Each product is served at its own newest.
5. **"The degree-day board stops at day 15 … how a curve joins the board after day 15."** The curves end at issued + 14; they overlap the models' days 5–13 and do not reach past 15 (§7). Only weeks 3-4 does, and it is not a curve.
6. **"Weeks 3-4 … served as outlook features where banked."** Banked, from 2022-01-07, with a two-category era to 2025-09-26 (EC never drawn then) and three-category after. The page must not read a 2024 wk34 "A 50" as a tercile tilt (d091653).
7. **"`*_p05 … *_p95` … the equal-chances columns."** The equal-odds twin has only P5/P50/P95; there is no `eq_*_p25/p75`.
8. **"The size of a vintages body."** The largest is pnw 814temp at n = 14: 121 KB raw, 22 KB gzip. `/places` (629 KB / 68 KB) is the body to watch, not the player's.
9. **"2,000 issuances written 2026-10-09, the rest tonight."** The backfill was still writing on 10-10 00:18Z: the first 2,000 spanned 2024-01-04 → 2026-10-08, and at 00:18Z it was writing 814temp 2023-04 issuances. The routes read the newest n only, so nothing here waits on it.
