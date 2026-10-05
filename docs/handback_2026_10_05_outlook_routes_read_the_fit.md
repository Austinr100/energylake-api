# Handback d091608 (energylake-api): the outlook routes read the stored fit, gate solar, and stop serving yesterday

**Lane:** d091608. **Rulings:** D-09-25-136 (route half), D-09-25-138.
**Branch:** `claude/outlook-routes-fitted-leads-90zzlr`. No PR, no merge, no deploy.
**Read-only.** Every production access was a `SELECT`, or an `EXPLAIN (ANALYZE, BUFFERS)` of
one, through the Neon connector. Nothing was written to Neon.
**Receipts:** `docs/receipts/outlook-fit-d091608/`.
**Merge:** after d091609 (the dashboard half) is live, with or after d091607. See §6 for what
the solar page prints if this lands first.

## Verdict

- **Both routes read the fitted leads from the line.** The LATERAL derivation is gone. One
  `CALIBRATION_SQL` serves both techs: a PK lookup of at most ten lines, named by tech and area,
  0.039 ms and 3 buffers. The LATERAL took 3.3 ms and 431 buffers. The basis now reads
  `"stored by the writer (pantry migration 272)"`.
- **One gate, in `solar_outlook.py`, for both techs.**
  - `gate()` and `_fitted()` moved from wind to solar; wind imports them.
  - So do `calibration_seam()` (now over lit hours), the new `calibration_gaps()`,
    `build_calibration()`, `calibration_ids()` and `_previous_mw()`.
  - `wind_outlook.py` lost 162 lines and gained 13.
- **Solar is gated on the newest issuance (2026-10-04 18Z).** The route withholds 313 stored
  calibrated figures across the five calibrated areas: 192 lit and 121 dark (§1.3). It serves
  56–58 calibrated hours per area where it served 120. It reports 5 `calibration_gaps` per area.
- **Wind's served body is main's, hour by hour, on all five calibrated areas.** `hours[]`,
  `seams` and `days` are identical. The differences are the fitted-lead receipts and the fields
  the rulings add. They are more than the brief expected, and §1.4 lists every one.
- **D-09-25-138.**
  - `_DDCache` gains `max_stale_s`. The four outlook and sites caches set it to 900 s.
  - An entry older than ttl + 900 s is rebuilt before answering (state `miss`, single-flight).
  - The degree-day cumulative board keeps its unbounded stale serve, the one deliberate
    exception (A7).
  - No other cache changed. A7 enumerates every `_DDCache` in `main.py`.
- **Tests:**
  - 32 red on main's code, then green (§3).
  - The full suite is 2,321 passing. main has 2,289.
  - A3 cannot go red on main by construction (§3).

## 1. Step 1

### 1.1 The calibration reads as they stood

- **Wind (main).** `CALIBRATION_SQL` derived `fit_lead_min` / `fit_lead_max` / `fit_rows` /
  `fit_issuances` with one LATERAL per line over `implied_gen_area_hourly`. The plan at HUBSUM,
  ids 673 / 674 / 695 / 696, is in `plans.md`:
  - 4 PK ranges, 462 rows read per line and 1,373 removed by the filter;
  - 431 buffers, 3.3 ms.
- **Solar (main).** It read `implied_gen_calibration` by id only, with no lead columns, and
  served `calibrated_mw` as stored.

On the same four HUBSUM lines, derived against stored:

| line | band | derived leads | stored leads | derived `fit_rows` | stored `fit_rows` = `n_hours` |
| --- | --- | --- | --- | --- | --- |
| 673 | h01_06 | 1–6 | 1–6 | 167 | 166 |
| 674 | h07_24 | 7–24 | 7–24 | 504 | 500 |
| 695 | h25_48 | 25–48 | 25–48 | 671 | 666 |
| 696 | h49_120 | 49–66 | 49–66 | 504 | 499 |

Across the other areas:
- **NP15** (665 / 666 / 687 / 688): 167 / 504 / 671 / 491 derived, against 166 / 500 / 666 / 486 stored.
- **SP15**: the same pattern, with h49_120 at 501 → 496.
- **CISO** (677 / 678 / 683 / 684): equal on every line, 167 / 504 / 671 / 504.
- **The leads agree on every line of every area.** The gap is pantry handback §7.1: the derivation
  had no "actual present" join, and the hub actuals miss a few hours that the fuel mix does not.

### 1.2 The replacement reads

They are in `plans.md`, with all three reads measured on production:
- **`CALIBRATION_SQL`:** a PK Index Scan, 0.039 ms, 3 buffers.
- **`SCORES_SQL`:** 13 backward PK probes, with `lead_min`, `lead_max` and
  `to_jsonb(s) -> 'hours_rule'`. 0.30 ms, 39 buffers.
- **Solar `HOURS_SQL`:** gains `prev_calibration_id` and `prev_lead_h`. It runs as a merge join
  on the PK, 0.32 ms, 24 buffers.

### 1.3 Every area, newest issuance: what the gate withholds, and the gaps

The newest issuances were solar `gfs` 2026-10-04 18Z (previous 12Z) and wind `hrrr_gfs`
2026-10-05 00Z (previous 10-04 18Z). The full listing is in `step1.txt`, from `step1.py`, over
the re-banked rows.

**Solar, the five calibrated areas.** The writer stored a calibrated figure on leads 1–120 of
each, ungated.

| area | lines (h01 / h07 / h25 / h49) fitted on | withheld, lit | withheld, dark | served calibrated | `seams.calibration` | `calibration_gaps` |
| --- | --- | --- | --- | --- | --- | --- |
| NP15 | 1–6 / 7–21 / 26–45 / 50–66 | 36 | 26 | 58 | 21 \| 22 | 22–24 h07 · 25 h25 · 46–48 h25 · 49 h49 · 69–120 h49 |
| ZP26 | 1–6 / 7–20 / 26–44 / 50–66 | 38 | 26 | 56 | 8 \| 21 | 21–24 · 25 · 45–48 · 49 · 69–120 |
| SP15 | 1–6 / 7–20 / 26–45 / 50–66 | 40 | 23 | 57 | 20 \| 21 | 21–24 · 25 · 46–48 · 49 · 68–120 |
| HUBSUM | 1–6 / 7–21 / 26–45 / 50–66 | 39 | 23 | 58 | 21 \| 22 | 22–24 · 25 · 46–48 · 49 · 68–120 |
| CISO | 1–6 / 7–21 / 26–45 / 50–66 | 39 | 23 | 58 | 21 \| 22 | 22–24 · 25 · 46–48 · 49 · 68–120 |

Notes on the table:
- **Each gap carries its line's fitted leads.** For example, HUBSUM's first is
  `{first_lead: 22, last_lead: 24, band: "h07_24", reason: "beyond_fitted_leads", fit_lead_min: 7, fit_lead_max: 21}`.
- **ZP26's seam is 8 | 21.** Leads 9–20 are night, and dark hours move no seam (clause 4). Its
  last lit calibrated hour is lead 8 (18 PDT) and its first lit withheld hour is lead 21 (07 PDT).
- **Why the h49_120 gap starts at 68 or 69, not 67.** Leads 67 (and 68 in three areas) are
  before dawn: they are withheld, but they open no gap.
- **Across all six issuances that carry calibrated figures** (10-03 12Z through 10-04 18Z),
  1,888 stored solar figures lie beyond their line's fitted leads, 894 of them nonzero. That
  matches the brief.

**Solar, the other 32 areas** (21 BAs and 11 states). No row carries a calibrated figure. The
gate withholds nothing: every hour is `no_line`. The gaps are `[]` and the seam is `null`.

**Wind, the four calibrated areas** (NP15, SP15, HUBSUM, CISO):
- **The gate withholds nothing.** The wind writer is already gated (pantry d091599): leads
  67–120 are stored with `calibrated_mw` **and `calibration_id`** null. The 10-04 12Z issuance
  now carries 18 calibrated h49_120 hours, not 72, so the one-off has run.
- **So the route serves:**
  - leads 1–66 calibrated;
  - leads 67–240 `no_line`;
  - `seams.calibration` 66 | 67 with `reason: "no_line"` (main said the same);
  - `calibration_gaps: []`.

**Wind, ZP26 and the other 33 areas.** None is calibrated. The gate withholds nothing and the
gaps are `[]`.

### 1.4 Wind's served body, hour by hour, against main's

This uses the same banked rows. Main's body was built by main's modules over main's derived
lines and banked as `tests/fixtures/wind_outlook_d091590/main_body_2026_10_05_00z.json`. A3
compares the route against it.

**`hours[]` (240 per area), `seams` and `days` are identical in all five areas.** Every
difference, by path:

| path | main → branch | areas | why |
| --- | --- | --- | --- |
| `calibration.<band>.fit_rows` | 167→166, 504→500, 671→666, 491/501/504→486/496/499 | NP15, SP15, HUBSUM (all 4 bands); CISO unchanged | the writer's count (= `n_hours`) replaces the derivation's (§1.1) |
| `calibration.<band>.fit_issuances` | 28 / 29 → absent | the 4 calibrated | a derivation-only count; nothing stored corresponds |
| `calibration.<band>.fit_leads_source` | `"derived"` → `"stored"` | the 4 calibrated | says where the leads came from |
| `calibration_fit_leads_basis` | the derivation's sentence → `"stored by the writer (pantry migration 272)"` | all 5 | ruling clause 1 |
| `calibration_gaps` | absent → `[]` | all 5 | ruling clause 4 (empty: the writer already withholds, §1.3) |
| `scores.<band>.<who>.lead_min` / `lead_max` | absent → e.g. 49 / 66 on `h49_120`; null on every `dam_comparable` row | the 4 scored | ruling clause 5 |

The brief expected "identical except `fit_rows` 167 → 166 and the basis string". The table
above is the full list. Of it, only `fit_issuances` and `fit_leads_source` were not anticipated
by a ruling. `fit_leads_source` was there to say "derived", and `fit_issuances` cannot be served
without the derivation, so I removed one key and changed the other.

## 2. Files

| file | change |
| --- | --- |
| `solar_outlook.py` | `HOURS_SQL` gains `prev_calibration_id` and `prev_lead_h`. `_scores_sql` adds `lead_min`, `lead_max` and `to_jsonb(s) -> 'hours_rule'`. `CALIBRATION_SQL` selects `fit_lead_min`, `fit_lead_max` and `fit_rows`, named by tech and area. **The gate moves here**: `_fitted`, `gate`, `_lit`, `calibration_seam` (lit hours only), `calibration_gaps`, `build_calibration` (now with fitted and applied leads), `calibration_ids` (this and the previous issuance's lines), `BEYOND_FIT`, `NO_LINE`, `FIT_LEADS_BASIS`, `HOURS_RULE`. `build_outlook` serves the gated `calibrated_mw`, `calibrated_absent_reason`, `seams.calibration`, `calibration_gaps` and `calibration_fit_leads_basis`. `unscaled` reads the gated hours, and `previous_mw` is like for like through the gate. `_score_obj` adds `lead_min` / `lead_max`, and `hours_rule` only when the row has one. |
| `wind_outlook.py` | The LATERAL, `_MAX_LEAD_H`, its `FIT_LEADS_BASIS`, `calibration_ids`, `_fitted`, `gate`, `build_calibration`, `calibration_seam` and `_previous_mw` are deleted; all are imported from solar. `CALIBRATION_SQL is so.CALIBRATION_SQL`. `build_outlook` adds `calibration_gaps`. |
| `main.py` | `_DDCache(max_stale_s=None)`, with the bound in `serve()`. `OUTLOOK_MAX_STALE_S = 900.0` is set on `_solar_outlook_cache`, `_solar_sites_cache`, `_wind_outlook_cache` and `_wind_sites_cache`. The solar route passes tech and area to the calibration read. The two outlook docstrings now describe the new fields and the 900 s bound. |
| `tests/test_outlook_fit_d091608.py` | New: A1–A7 and two PG tests (30). |
| `tests/test_solar_outlook.py`, `tests/test_wind_outlook.py` | Changed assertions: §3.2. |
| `tests/fixtures/…` | Re-banked: §5. |
| `docs/receipts/outlook-fit-d091608/` | `bank_sql.py` (the banking statement, generated from the modules), `step1.py` / `step1.txt`, `page_before_d091609.py` / `.txt`, `plans.md`, `reds.txt`. |
| `docs/receipts/{solar-outlook-api-d091568,wind-outlook-api-d091590}/sample.py` | Read the new banks. Their sample bodies are regenerated: solar now writes all five calibrated areas, and wind's site samples are for 10-05 20Z and 10-06. |

## 3. Tests: red, then green

### 3.1 Red

`reds.txt` holds the new and changed tests run against **main's** `main.py`, `solar_outlook.py`
and `wind_outlook.py`, with this branch's tests and fixtures. **32 failed, 91 passed**:
- every A1, A2, A4, A5, A6 and A7 test, and both PG tests;
- solar P1 ×2;
- wind W1 (source), W5 (reads the line), PG W5, and P1 ×2.

**A3 (5 cases) passes on main.** It pins that nothing changes beyond the named fields, and main
changes nothing, so it cannot go red there. Its job is to stay green after the change: it is the
guard on "wind's body is unchanged".

### 3.2 Green, and every changed assertion

The three files: 123 passed. The full suite: **2,321 passed** (main: 2,289).

| test | was | now | why |
| --- | --- | --- | --- |
| solar `test_G3_calibrated_hours_compare_calibrated_to_calibrated` | a pool with no lines | a pool with `fitted_lines()` | the gate reads the line the row names; with no line the hour is `no_line` and serves no calibrated figure (D-09-25-136 clause 2) |
| solar `test_G4_calibrated_rows_name_their_line` | lines without lead columns | `fitted_lines()`, carrying 272's columns | a line with null fitted leads covers nothing (clause 3) |
| solar `_DDL` (PG) | `implied_gen_scores` without leads | `+ lead_min, lead_max` | the score read selects them (clause 5) |
| solar `test_P1_production_hubsum_bank` | 10-01 12Z, unscaled, 66 hours | 10-04 18Z, 240 hours, gated (leads 22–24, 25, 46–49, 67–120 withheld), fitted leads 1–6 / 7–21 / 26–45 / 50–66, 5 gaps, scores' leads | re-banked through the merged code (§5); the old bank's issuance predates calibration |
| solar `test_P1_production_every_calibrated_area` | — | new: on all five areas, each hour serves exactly the stored figure iff its own line's stored leads hold its lead | A1 on production rows |
| wind `test_W1_each_line_states_its_fitted_and_applied_leads` | `fit_leads_source == "derived"` | `== "stored"` | clause 1 |
| wind `test_W1_no_constant_for_todays_boundary_in_the_module` | wind only | wind **and** solar | the gate now lives in solar |
| wind `test_W5_derivation_follows_the_writers_fit_rule…` | pinned the LATERAL's clauses | renamed `…read_from_the_line_not_derived`: the statement is solar's, selects the three columns, names tech and area, and holds no `implied_gen_area_hourly` / LATERAL / `written_at` | the derivation is deleted (clause 1) |
| wind `test_W5_previous_issuances_lines_are_read_too` | `params["area_kind"]`, `params["model"]` | `params["tech"]`, `params["area"]` | the statement's predicates changed |
| wind `_DDL` / seed (PG) | calibration without leads | `+ fit_lead_min, fit_lead_max, fit_rows`; seed lines carry 49–66 / 25–48 | migration 272 |
| wind `test_PG_W5_derived_fit_leads_are_the_leads_the_fit_used` | 49–66 derived from seeded rows, `fit_rows` 504 | renamed `…fit_leads_are_the_lines_stored_columns`: 49–66 and 25–48 read, `fit_rows == n_hours == 499` | the same |
| wind `test_P1_production_hubsum` | 10-04 12Z: lead 120 withheld with `beyond_fitted_leads`, scores 5.18 / 6.06 / 7.40, DAM 4.84, crossing day 10-06 | 10-05 00Z: lead 48 947 / 712, lead 49 681 / 1,959; lead 67 `no_line` (the writer stores none); line 696 (1,483.7487, 0.699056, 499), stored 49–66, `fit_rows` 499; seam 66 \| 67 `no_line`; scores 5.18 / 6.06 / 7.17, DAM 4.73; h49_120 calibrated score covers 49–66; crossing day 10-07; `calibration_gaps == []` | re-banked; the writer gate and its one-off have changed what is stored |
| wind `test_P1_production_ciso_and_zp26` | — | `+ zp["calibration_gaps"] == []` | clause 4 |
| wind `test_P1_production_sites` | 10-04 20Z, init 10-04 12Z | 10-05 20Z, init 10-05 00Z | re-banked; the sites route is unchanged |

Unchanged and green: every other solar test (G1–G5, the contract, the sites) and every other
wind test (W1–W4, W6–W9, S, the contract, the PG hours and sites tests).

### 3.3 What A1–A7 pin

| test | pins |
| --- | --- |
| A1 ×2 | solar: lit leads 22–24, 25, 46–49 and 67+ beyond the lines (7–21, 26–45, 50–66) are null with `beyond_fitted_leads`; inside, the stored figure; no withheld number anywhere in the body; `previous_*` gated by the previous run's own line at its own lead |
| A2 ×7 | dark hours beyond the fit open no gap and move no seam; a lit run with a dark hour inside it is one gap with its band and fitted leads; a change of line closes a gap; a `no_line` hour is not a gap and closes a run; an hour stored with no figure but its line's id, beyond that line's fit, reads `beyond_fitted_leads` and is a gap (§7.2); wind serves gaps from the same function |
| A3 ×5 | wind's body against main's banked body on NP15, ZP26, SP15, HUBSUM and CISO: only the §1.4 paths differ; `hours`, `seams` and `days` are identical; the leads agree and `fit_rows == n_hours` |
| A4 ×4 | null, or half-null, fitted leads cover nothing; the band's hours are one gap carrying the null leads |
| A5 ×3 | `lead_min` / `lead_max` on each score (null on `dam_comparable` / `caiso_dam`); `hours_rule` passed through untouched when present, and absent when not; wind's columns kept |
| A6 ×5 | fresh inside ttl; inside ttl + 900 s, 5 concurrent callers get the old payload as `stale` and **one** refresh runs; beyond it, 8 concurrent callers all get `miss` with the new payload from **one** build; the desk case (7 h 7 min old) is rebuilt; the four caches are set to 900 |
| A7 ×2 | `dd/cumulative` serves a month-old entry stale; the set of bounded caches is exactly the four |
| PG ×2 | the calibration read returns the stored columns and refuses another tech's or area's id; the score read is valid **before** an `hours_rule` column exists (null) and passes it through **after** (`ALTER TABLE … ADD COLUMN`) |

## 4. Plans

See `docs/receipts/outlook-fit-d091608/plans.md` (§1.1 and §1.2 above): production, warm, every
read on a PK.

## 5. The re-banked fixtures

Read-only, through the Neon connector, by the merged code's own statements. The statement is
generated from the modules by `bank_sql.py`, with every area in one `SELECT`, as d091590 did.

| file | md5 | what |
| --- | --- | --- |
| `tests/fixtures/solar_outlook_d091568/production_2026_10_04_18z.json` | `b84124c161597d139a5d99ebc0160df9` | Solar 10-04 18Z beside 12Z, for NP15, ZP26, SP15, HUBSUM and CISO. Each area has the issuance, 240 hours (with the previous line and lead), 13 or 12 score rows (with leads), the lines of both issuances (with stored leads), actuals and fleet. **Replaces** `production_hubsum_2026_10_03.json` (md5 `39a72ec5…`). |
| `tests/fixtures/wind_outlook_d091590/production_2026_10_05_00z.json` | `fb42ba3ae1ef0e5bae4a27a03b8025fa` | Wind 10-05 00Z beside 10-04 18Z, for the same five areas, plus `lines_derived` (main's LATERAL, the same ids). It also holds the 323 plant facts and the site windows 10-05 20Z and Pacific 10-06. **Replaces** `production_2026_10_04_12z.json` (md5 `de6eb485…`). |
| `tests/fixtures/wind_outlook_d091590/main_body_2026_10_05_00z.json` | `1bb44cd719c4b27da8e3ba4204aceb61` | New: main's wind bodies over the bank above (A3's reference). |
| `tests/fixtures/solar_outlook_d091568/spec_3_3_label.txt` | `6e8ea6b43dbca205f6e3868781d77878` | Unchanged. |
| `tests/fixtures/wind_outlook_d091590/writer_strings.json` | `447e6be8268f26daed7dc0e02e49f1ba` | Unchanged. |

**The old banks are replaced, not kept beside the new ones.** The 10-04 12Z wind issuance they
held no longer reads the same in production: the one-off nulled 54 hours per area. So a re-bank
of the same init would not have been the d091590 bank either.

## 6. If this merges before d091609: the solar page at HUBSUM

The page is dashboard @ `a0609c4`, `solarOutlook.ts`. `seams()` prints a seam at every change
of drawn figure between consecutive hours, dark hours included. `bandBasis()` draws a band as
calibrated only when every hour of it is. Run over this branch's HUBSUM body
(`page_before_d091609.py`), it gives:

- **5 seams, where main's body prints 1** (120 | 121):
  - 21 | 22, calibrated → registry;
  - 25 | 26, registry → calibrated;
  - 45 | 46, calibrated → registry;
  - 49 | 50, registry → calibrated;
  - 66 | 67, calibrated → registry.
- **Each calibrated → registry seam prints "no calibration line was applied to these hours."**
  That is false: a line was applied, and it is withheld beyond its fit. The page does not read
  `calibrated_absent_reason` or `calibration_gaps`.
- **The score card loses its calibrated cells in h07_24, h25_48 and h49_120** (and in
  `dam_comparable`, which reads h07_24 and h25_48). Each of those bands now has a withheld hour,
  so `bandBasis` falls back to the registry score. Only h01_06 keeps its calibrated score.
- The days that straddle a seam print in parts. That is correct, but there are more of them.

The bars stay honest (no withheld number is drawn). The wording does not.
**Hold the merge for d091609**, as the brief says.

## 7. What this brief got wrong

1. **"Identical except `fit_rows` 167 → 166 and the basis string."**
   - `fit_rows` differs on **every** band of NP15, SP15 and HUBSUM (4–5 rows each), not only
     h01_06. It is equal everywhere at CISO.
   - The other differences are named in §1.4: `fit_issuances` gone, `fit_leads_source`, the new
     `calibration_gaps`, and clause 5's score leads.
   - None is in `hours[]`.
2. **"1,888 stored solar figures"** is right: 1,888, with 894 nonzero, over six issuances.
   **Wind has none:** the wind writer gate and its one-off are already live. That has a
   consequence the rulings do not settle:
   - **A writer-gated hour stores `calibration_id` null too** (pantry handback §7.4, "say if the
     id should stay"). So the route cannot tell "beyond the fit" from "no line".
   - On wind today, 67–120 read `no_line`, the seam's reason is `no_line`, and
     **`calibration_gaps` is `[]`**.
   - When d091607 gates the solar writer the same way, solar's gaps empty too, and the page loses
     the "beyond fitted leads" wording that clause 4 exists to give it.
   - **The fix is in pantry:** keep `calibration_id` on a gated hour (null `calibrated_mw`, the
     line still named). The gate reads the line before the figure: an hour whose named line does
     not hold its lead is `beyond_fitted_leads` whether or not a figure was stored. So
     `calibration_gaps` fills with no API change (pinned by A2,
     `…writer_gated_hour_that_keeps_its_line_id…`). I did not infer the line from the band's other
     hours. That would be deriving again, and it would change wind's served body against main's.
3. **"The hours rule when present (d091607, D-09-25-137)."**
   - Neither production nor pantry main has the column, and no d091607 branch or PR is visible.
     I could not learn its name or type.
   - The score read takes `to_jsonb(s) -> 'hours_rule'`. The statement is valid now (null) and
     passes the column through untouched once it exists (PG test).
   - **The name `hours_rule` is my guess.** If d091607 names it otherwise, one constant
     (`solar_outlook.HOURS_RULE`) changes.
4. **"Re-bank as d091590 did."** d091590's bank cannot be reproduced. The one-off rewrote 10-04
   12Z after it was banked. So both banks are new issuances, and every P1 number moved (§3.2).
5. **"`seams.calibration` keeps wind's shape (the end of the opening calibrated run)."** Over lit
   hours only, the seam can sit across a night:
   - ZP26's is 8 | 21, thirteen hours apart.
   - `target_ts_before` / `target_ts_after` say so, but a page that draws the seam at one x will
     have to choose.
   - The gaps carry the exact runs, so the page can draw from those instead.
6. **The gaps split at a change of line.** The ruling's shape carries one band and one line's
   fitted leads. So a run of lit withheld hours across a band edge (24 | 25, 48 | 49) is two gaps.
   That is why every calibrated solar area shows 5 gaps, not 3.
7. **"Build times 2.57 s / 3.13 s."** These are now what the first reader after a 20-minute quiet
   spell waits. That is within the 15 s build backstop and the ruling, but it is not free. A
   periodic warm (a cron hitting the four routes every 10 minutes) would hide it, and that is
   not this lane's to add.

---

**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/outlook-routes-fitted-leads-90zzlr
