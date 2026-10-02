# Handback — d091550 — one missing day of rain no longer ends a station's season

**Date:** 2026-10-01. **Spec:** energylake-pantry `docs/cc_spec_2026_10_01_season_missing_days.md` (D-09-25-86).
**Repo:** `energylake-api`. **Branch:** `claude/season-missing-days-d091550-sczehg` (see "What the spec got wrong" §W10). Branch only: no PR, no merge, no deploy.

## Outcome

- **STOP-B fired.** Three stations' Sep 30 median moves more than 5 % when the 1–5-day seasons join the base: **USW00003102 +5.2 %, USW00003145 −11.8 %, USW00023152 −7.0 %** (seasons below). As §2.5 orders, **the base change is not shipped**: §1.2–§1.4 apply to the **season in progress only**. Past seasons walk and qualify exactly as on main. The full rule (past seasons carried, admitted with ≤ 5 missing, `/areas` `qualifying_seasons`, `base.tolerance`) is built and tested behind one constant, `season.PRECIP_TOLERANT_BASE = False`. Flipping it to True is the whole base change. See W1 before ruling: none of the three stations has a median the API serves, on either side.
- **STOP-D did not fire for any day the lane touches.** The only QC-dropped precipitation value in `ghcnd_weather_daily` is **USW00023152, 2001-12-29, QFLAG `S`** (spatial consistency). It sits in WY2002, which has 13 missing days and is out of the base under both rules. All seven WY2026 holes are days with no flag (details in §2.5 below). One is an **ingest-refresh question, not a flag**: USW00023183's 2026-09-16 is NULL in Neon, but GHCN now carries an unflagged **8.6 mm** for it.
- §2.3 shipped: the single-area readout takes the snapshot's median rule through one function, `season.day_median`, which both the readout and `snapshot_row` call.
- Tests: the 18 new tests (S1–S8) fail on main for the expected reasons (15) or pin no change (3), and all pass on the branch. Full suite: **2141 passed** (was 2123).

## Files

| file | what |
| --- | --- |
| `season.py` | `CARRIED`, `PRECIP_MISSING_TOLERANCE = 5` (chosen, not measured, and the code says so), `PRECIP_TOLERANT_BASE = False` (STOP-B), `carried()`, `tolerant_base()`, `carried_basis()`; `_walk(..., carry=)` → `_walk_carried`; `_qualifies` tolerance branch (flag-gated); `_season_block` gains `within_tolerance`, `days_missing`, `missing_days` (first 31), `first_missing`, and the gap absence carries `days_missing`; readout §1.4 (`days_missing`, `basis`; `too_many_missing`); `day_median()` (§2.3); level readouts gain `median_basis`; `snapshot_row` uses `day_median` and, for station precip, appends `days_missing` and `basis` (counted to the row's own date; withheld over 5); `build_areas` `qualifying_seasons` for station precip (flag-gated); `source.method` sentence rewritten (and `METHOD_PRECIP_TOLERANT` for the flag); module header |
| `main.py` | the route index line for d091550 (docstring only) |
| `tests/test_season_missing_days.py` | S1–S8 (18 tests), plus the S5 digest regenerator (`python tests/test_season_missing_days.py <main's season.py>`) |
| `tests/fixtures/season_precip_USW00023232.json`, `…USW00023174.json` | Sacramento and LAX, full record through 2026-09-26, compact per-day encoding (see below for how they were checked) |
| `tests/fixtures/season_s5_main_digests.json` | sha256 of main's (d08d602) S5 payloads, plus main's readouts |
| `tests/test_season.py` | S1's two tests that pinned "the season in progress stops at the gap" now pin the new rule; base exclusions unchanged |
| `tests/test_season_hydro.py` (H5), `tests/test_season_snapshot.py` (S1 ×2) | a short-base level readout now carries the range median, `median_basis` is the third readout key |
| `docs/receipts/season-missing-days-d091550/` | banked bodies before and after, red/green lists, SHA256SUMS |

**The banked station rows.** Direct Postgres egress is blocked from the lane's container, and full histories (~30 k rows each) do not fit through the Neon tool. So the two records were rebuilt from NOAA's own GHCNd files: the `by_station` CSV, plus the 2025 and 2026 PRCP/TMAX/TMIN parquet for the part after the CSV ends. A QFLAGged value is dropped the way `ingesters/ghcnd.py` drops it, and a row exists on any day with a raw element. The result was then **matched to Neon**:
- Sacramento: 29 840 rows, and every water year's valued-day count and total, identical.
- LAX: identical after adding one row, 2024-12-31 (0.0 mm, in Neon but not in NOAA's CSV, which ends 2025-02-06).

## S1–S8, red → green

On main means the new tests run in a worktree of `origin/main` (d08d602); on the branch means this commit.

| test | on main | branch | pins |
| --- | --- | --- | --- |
| S1 `test_s1_sacramento_banked_wy2026` | **red**: `assert '2026-02-18' == '2026-09-26'` | green | through = newest valued day (09-26), `days_missing` 1, `missing_days ["2026-02-19"]`, every slot to 09-26 valued, readout given with its basis |
| S2 `test_s2_the_feb_19_slot_equals_the_feb_18_slot` | red | green | Feb 19 slot == Feb 18 slot (payload and unrounded walk) |
| S3 `…five_missing_is_given_six_is_withheld`, `…snapshot_row_follows_the_tolerance`, `…a_complete_season_says_none_missing` | red | green | 5 given with `basis`; 6 → `too_many_missing`, curve kept; the snapshot row the same, counted to its own date |
| S4 `…with_the_flag_set_precip_qualifies_hdd_does_not`, `…flag_off_the_base_is_unchanged` | red | green | flag set: WY2000 with 3 missing qualifies for precip, the HDD season with 3 missing does not. Flag off (shipped): the base is main's |
| S5 `…degree_days_are_byte_identical_to_main` ×3 (station HDD, station CDD, LWT HDD) | green (pins no change) | green | sha256 of the whole payload == main's |
| S5 `…levels_identical_but_for_the_readouts_median_rule` ×3 (SWE n = 40, storage n = 20, n = 35) | red (no `median_basis`) | green | everything but the readout byte-identical; the readout differs only by §2.3 (W5) |
| S6 `test_s6_missing_feb_29_keeps_the_feb_28_slot` | red | green | WY2024 with Feb 29 missing: the Feb 28 slot is Feb 28's total; Mar 1 adds on |
| S7 `…readout_and_snapshot_row_agree` ×2 (storage n = 19, n = 25), `…day_median_is_the_one_rule` | red | green | the same `median`, `median_basis`, `pct_of_median`; no percentile |
| S8 `test_s8_areas_counts` | red | green | `complete_seasons` unchanged (32); flag set: `qualifying_seasons` 34 (+ the 3- and 5-missing seasons, not the 6), right after `complete_seasons`; HDD untouched |

Lists: `docs/receipts/season-missing-days-d091550/s1_s8_red_on_main.txt`, `s1_s8_green_on_branch.txt`.

## §2.4 — measured (Neon, 2026-10-01 ~20:15Z)

### Past water years by missing-day count, and qualifying seasons

These are past water years only; WY2026 (in progress) is left out, as `/areas` leaves it out. Most "6+" rows are a station's first, partial year.

| station | past WYs | 0 missing | 1–5 | 6+ | qualifying before | after (rule, not shipped) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| USW00003017 | 32 | 28 | 0 | 4 | 28 | 28 |
| USW00003102 | 28 | 21 | 6 | 1 | 21 | 27 |
| USW00003145 | 15 | 5 | 9 | 1 | 5 | 14 |
| USW00023050 | 95 | 92 | 2 | 1 | 92 | 94 |
| USW00023152 | 28 | 21 | 3 | 4 | 21 | 24 |
| USW00023169 | 78 | 76 | 1 | 1 | 76 | 77 |
| USW00023174 | 82 | 81 | 0 | 1 | 81 | 81 |
| USW00023183 | 91 | 81 | 1 | 9 | 81 | 82 |
| USW00023185 | 89 | 87 | 1 | 1 | 87 | 88 |
| USW00023188 | 87 | 85 | 1 | 1 | 85 | 86 |
| USW00023232 | 84 | 74 | 3 | 7 | 74 | 77 |
| USW00023234 | 15 | 14 | 0 | 1 | 14 | 14 |
| USW00023293 | 15 | 13 | 1 | 1 | 13 | 14 |
| USW00024127 | 78 | 76 | 1 | 1 | 76 | 77 |
| USW00024131 | 86 | 85 | 0 | 1 | 85 | 85 |
| USW00024157 | 126 | 123 | 1 | 2 | 123 | 124 |
| USW00024229 | 88 | 85 | 2 | 1 | 85 | 87 |
| USW00024233 | 78 | 73 | 4 | 1 | 73 | 77 |
| USW00024257 | 40 | 39 | 0 | 1 | 39 | 39 |
| USW00093138 | 15 | 11 | 3 | 1 | 11 | 14 |
| USW00093193 | 78 | 77 | 0 | 1 | 77 | 77 |

Across the 21 stations, **39 past station seasons** have 1–5 missing days.

### Day-of-year median (mm, cumulative to the day; percentile_cont 0.5 = NumPy linear)

"Before" is over complete seasons. "After" adds the 1–5-day seasons, each summed over its reported days. "Served?" says whether `/season` publishes a median at all; the cone needs n ≥ 30, and precipitation has no range median.

| station | n before → after | Feb 1 before | after | Apr 1 before | after | Sep 30 before | after | Sep 30 Δ | served? |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| USW00003017 | 28 → 28 | 53.9 | 53.9 | 100.6 | 100.6 | 349.6 | 349.6 | +0.0 % | no, n < 30 both sides |
| USW00003102 | 21 → 27 | 101.8 | 107.0 | 222.3 | 222.3 | 240.6 | 253.2 | +5.2 % **STOP-B** | no, n < 30 both sides |
| USW00003145 | 5 → 14 | 33.9 | 30.2 | 44.1 | 34.2 | 74.6 | 65.8 | −11.8 % **STOP-B** | no, n < 30 both sides |
| USW00023050 | 92 → 94 | 55.8 | 55.4 | 74.5 | 74.2 | 210.6 | 210.6 | +0.0 % | yes |
| USW00023152 | 21 → 24 | 128.5 | 119.1 | 267.1 | 251.1 | 313.8 | 291.8 | −7.0 % **STOP-B** | no, n < 30 both sides |
| USW00023169 | 76 → 77 | 31.2 | 31.9 | 51.0 | 51.1 | 93.6 | 94.3 | +0.7 % | yes |
| USW00023174 | 81 → 81 | 146.0 | 146.0 | 224.8 | 224.8 | 260.0 | 260.0 | +0.0 % | yes |
| USW00023183 | 81 → 82 | 59.3 | 60.3 | 96.9 | 97.7 | 171.2 | 172.0 | +0.5 % | yes |
| USW00023185 | 87 → 88 | 78.0 | 77.9 | 120.9 | 120.8 | 166.5 | 166.1 | −0.2 % | yes |
| USW00023188 | 85 → 86 | 128.6 | 126.3 | 199.5 | 198.9 | 233.4 | 233.1 | −0.1 % | yes |
| USW00023232 | 74 → 77 | 243.3 | 245.4 | 345.8 | 346.2 | 408.7 | 411.7 | +0.7 % | yes |
| USW00023234 | 14 → 14 | 253.8 | 253.8 | 393.6 | 393.6 | 416.7 | 416.7 | +0.0 % | no, n < 30 both sides |
| USW00023293 | 13 → 14 | 163.2 | 166.0 | 233.5 | 233.1 | 256.4 | 252.3 | −1.6 % | no, n < 30 both sides |
| USW00024127 | 76 → 77 | 135.3 | 134.7 | 209.1 | 208.1 | 387.4 | 381.5 | −1.5 % | yes |
| USW00024131 | 85 → 85 | 121.9 | 121.9 | 185.9 | 185.9 | 304.8 | 304.8 | +0.0 % | yes |
| USW00024157 | 123 → 124 | 188.1 | 187.5 | 263.5 | 262.7 | 400.0 | 398.4 | −0.4 % | yes |
| USW00024229 | 85 → 87 | 497.1 | 497.1 | 689.9 | 682.7 | 918.5 | 900.6 | −1.9 % | yes |
| USW00024233 | 73 → 77 | 544.9 | 544.9 | 747.4 | 747.4 | 978.9 | 967.1 | −1.2 % | yes |
| USW00024257 | 39 → 39 | 428.9 | 428.9 | 629.9 | 629.9 | 762.5 | 762.5 | +0.0 % | yes |
| USW00093138 | 11 → 14 | 35.1 | 36.0 | 52.9 | 48.0 | 72.9 | 72.1 | −1.1 % | no, n < 30 both sides |
| USW00093193 | 77 → 77 | 114.4 | 114.4 | 204.3 | 204.3 | 244.3 | 244.3 | +0.0 % | yes |

**STOP-B, the stations and their 1–5-day seasons (WY: days missing):**
- **USW00003102:** WY2000: 3, WY2004: 2, WY2010: 1, WY2012: 1, WY2020: 1, WY2023: 2.
- **USW00003145:** WY2012: 1, WY2014: 2, WY2015: 1, WY2018: 2, WY2019: 1, WY2022: 2, WY2023: 4, WY2024: 2, WY2025: 1.
- **USW00023152:** WY2003: 3, WY2007: 2, WY2012: 1.

Among the 14 stations whose median is served (n ≥ 30), the largest Sep 30 move is USW00024229, −1.9 %.

### WY2026 readout, before and after (Neon frontier 2026-09-28)

By the time the lane read it, the frontier had moved to **2026-09-28** for all 21 stations. The two Sep 26 holes (USW00003145, USW00023183) had been filled by then. "Before" is main's walker; "after" is this branch, base unchanged. "Value" is the sum of reported days through each station's own frontier.

| station | missing to date | before | after: value | n | median | % of median | percentile |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| USW00024233 | 1 (2025-12-15, null) | withheld, gap, cut at Dec 14 | 974.1 | 73 | 952.6 | 102.3 | 50.7 |
| USW00003102 | 1 (2026-01-05, null) | withheld, gap, cut at Jan 4 | 321.4 | 21 | — | — | — |
| USW00023232 | 1 (2026-02-19, no row) | withheld, gap, cut at Feb 18 | 428.0 | 74 | 408.7 | 104.7 | 58.1 |
| USW00024127 | 1 (2026-04-30, no row) | withheld, gap, cut at Apr 29 | 385.6 | 76 | 380.4 | 101.4 | 51.3 |
| USW00093138 | 1 (2026-07-15, null) | withheld, gap, cut at Jul 14 | 93.3 | 11 | — | — | — |
| USW00023183 | 1 (2026-09-16, null) | withheld, gap, cut at Sep 15 | 152.0 | 81 | 171.2 | 88.8 | 38.3 |
| USW00003145 | 0 (09-26 since filled) | given, unchanged | 56.8 | 5 | — | — | — |
| the other 14 | 0 | given | unchanged; the readout gains `days_missing: 0`, `basis: "sum of reported days; 0 missing"` | | | | |

"—" means n < 30: no cone, so no median and no percentile, before or after. All six previously cut stations now open on their frontier, inside the tolerance.

### Banked bodies (`docs/receipts/season-missing-days-d091550/`)

These are full `/season` bodies for station precip, built from the banked rows (frontier 2026-09-26) with the test suite's ENSO bins. ENSO does not touch any number below except `vs_category`.

| body | through | readout |
| --- | --- | --- |
| Sacramento (USW00023232), before | 2026-02-18 | null; `readout_absence` gap, first_missing 2026-02-19 |
| Sacramento, after | **2026-09-26** | 427.7 mm, median 405.5, 105.5 %, p 58.1, `days_missing` 1, `basis "sum of reported days; 1 missing"` |
| LAX (USW00023174, untouched), before | 2026-09-26 | 360.5 mm, median 260.0, 138.7 %, p 74.1 |
| LAX, after | 2026-09-26 | the same numbers; + `days_missing` 0, `basis` |

These are structural diffs, before → after, computed by a key-walk:
- **LAX:** only additions (`this_season.within_tolerance`, `days_missing`, `missing_days`, `first_missing`, `readout.days_missing`, `readout.basis`) plus the `source.method` sentence. Nothing else moved.
- **Sacramento:** the same additions; `this_season.through`; 220 slots (Feb 19 → Sep 26) now valued; the readout and its absence; and every `years[].to_date`, now read like-for-like on Sep 26 instead of Feb 18. The base (n = 74), cone, normal, five-year band and ENSO medians are byte-identical.

`bodies_summary.json` is the readable cut; SHA256SUMS covers every file.

## §2.5 — STOP-D, the evidence

I checked the seven WY2026 holes against NOAA's 2025 and 2026 PRCP parquet, the ingester's own source:

| station | day | Neon | GHCN now |
| --- | --- | --- | --- |
| USW00024233 | 2025-12-15 | NULL (TMAX/TMIN present; no PRCP provenance) | no PRCP record |
| USW00003102 | 2026-01-05 | NULL (same) | no PRCP record |
| USW00023232 | 2026-02-19 | no row | no record (Feb 18 = 0.3 mm and Feb 20 = 0.0 match Neon) |
| USW00024127 | 2026-04-30 | no row | no PRCP record |
| USW00093138 | 2026-07-15 | NULL (same) | no PRCP record |
| USW00023183 | 2026-09-16 | NULL | **86 (8.6 mm), no QFLAG**: GHCN filled it after ingest; the ingester has not re-fetched |
| USW00023183, USW00003145 | 2026-09-26 | no row (then) | 0, no QFLAG; both since banked |

No WY2026 hole is a dropped flag. In the whole table, `meta.provenance.PRCP.qc_dropped` is set on one row only: USW00023152 2001-12-29 `S`, in WY2002, which has 13 missing days.

## What the spec got wrong

**W1. STOP-B is measured on a median the API does not publish.** The three stations that trip it have n < 30 on both sides: USW00003102 21 → 27, USW00003145 5 → 14, USW00023152 21 → 24. Precipitation has a cone (n ≥ 30) but no range median, so `/season` serves no median for them before or after; their readouts carry the value only. The 5 % moves are in a statistic no page draws. Every station whose median is served moves by 1.9 % or less. As written, the STOP holds back the base change for all 21 stations over numbers nobody sees. I honoured it. If the architect re-rules STOP-B on served medians, the base change is one constant: set `PRECIP_TOLERANT_BASE = True`. S4 and S8 already pin that state.

**W2. With STOP-B fired, §1.5's promise breaks in the shipped state.** "Both sides of every comparison are then the same kind of number" no longer holds: the season in progress is a sum of reported days, but the base is complete seasons. For a station with 1 missing day the bias is at most one day's rain. Also, once WY2026 closes it becomes a past season and walks strictly again, so for the six stations its `years[]` and `last_season` will be cut at the hole once more. The fix is the same constant.

**W3. §1.1's premise ("on most days the missing value would have been zero") failed for one of the seven holes.** USW00023183's Sep 16 is a wet day (8.6 mm) that GHCN filled after ingest. That is not STOP-D (no flag), but it is an ingest question: the ingester does not go back over recent NULLs. The carried sum there is 8.6 mm low and says "1 missing", so it is honest, but a re-fetch window would fix the data itself.

**W4. §0's snapshot of the frontier was stale within hours.** Production moved to 2026-09-28 during the lane, and two of the seven holes (both Sep 26) were filled. The §2.4 readout table uses the live frontier; the banked fixtures stop at 2026-09-26.

**W5. S5 contradicts §2.3.** SWE and storage are levels, and §2.3 changes a level's readout by design: it adds `median_basis`, and with 5 ≤ n < 30 the median and `pct_of_median` are no longer null. A level payload therefore cannot be byte-identical to main. S5 is pinned as: HDD and CDD byte-identical in full; SWE and storage byte-identical except the readout, whose only changes are the §2.3 fields.

**W6. §2.3 "exactly as the snapshot row does" is wider than "for a level".** A snapshot row carries `median_basis` for every data type. I added it to **level** readouts only: the spec says "for a level", and HDD and CDD must stay byte-identical. So a cumulative readout (precip, HDD, CDD) still has no `median_basis`, while its snapshot row does. Their `median` values agree, since the rule is the same function. Only the key is missing.

**W7. §1.2 vs §1.6 at the start of a season.** "Its slot carries the running total unchanged" puts **0.0** on a missing day before any day has reported (e.g. a missing Oct 1). That is the literal rule, and the value is the sum of the reported days, but it is a zero on a missing day. I followed §1.2. The alternative, null until the first reported day, would put NaN into the strict cone once past seasons carry.

**W8. Unspecified shapes, chosen here:**
- `readout_absence` for `too_many_missing` is `{reason, days_missing, tolerance, first_missing}`.
- The snapshot row's tolerance counts missing days **up to the row's date**, not to the frontier.
- A station-precip snapshot row appends `days_missing` and `basis` after `absence`.
- The `basis` string appears even with 0 missing (`"sum of reported days; 0 missing"`).
- The gap `absence` on a carried season now carries `days_missing`, as a level's does.

**W9. §2.2's "route's `basis` sentence (`season.py` ~524)"** is `METHODS[("station","precip")]`, which is `source.method`, not a route string. Rewritten there; it states the 5 and says past seasons stay strict.

**W10. Branch name.** The spec and the lane brief say `claude/season-missing-days-d091550`. This session's harness assigned `claude/season-missing-days-d091550-sczehg` and forbids pushing elsewhere without permission, so the work is there. I can push the same commit to the unsuffixed name on request.

**W11. "Sacramento's banked WY2026 rows" (S1) were not banked anywhere.** The test needs the full record, because the base and readout need past seasons. The lane banked both stations' full records, rebuilt from NOAA and matched to Neon (Files, above).
