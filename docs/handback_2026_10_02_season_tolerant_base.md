# Handback — d091553 — the tolerant precipitation base ships

**Date:** 2026-10-02. **Ruling:** D-09-25-93 (architect, 2026-10-02): STOP-B is read on published medians (n ≥ 30). **Follows:** d091550 (`docs/handback_2026_10_01_season_missing_days.md`, merged on main at d45f798). **Spec of record:** energylake-pantry `docs/cc_spec_2026_10_01_season_missing_days.md` (D-09-25-86).
**Repo:** `energylake-api`. **Branch:** `claude/season-tolerant-base-d091553-iklbc5` (see W5). Branch only: no PR, no merge, no deploy.

## Outcome

- `season.PRECIP_TOLERANT_BASE = True`. A past station-precipitation season with at most 5 missing days is now summed over the days that reported, and it joins the base, the cone, the normal, the five-year band and the ENSO medians. `/areas` reports `qualifying_seasons` for station precip. `complete_seasons` is unchanged. HDD, CDD and every level are untouched.
- Every `/season` readout now carries `median_basis`, including precipitation, HDD and CDD. It is the snapshot row's string for the same median, from the same function (`day_median`).
- **One change beyond the list (W1):** a past season carried past the tolerance has `years[].to_date: null`. Without this, the flag would have shipped a sum over seasons that mostly did not report, and a **0.0** on LAX's WY1944 (305 days missing). Main had null for both.
- Sacramento and LAX bodies were regenerated. Sacramento's base goes 74 → 77, and its Sep 30 p50 goes 408.7 → **411.7**, the same as d091550's Neon-measured "after". LAX is unchanged (n = 81).
- Tests: the 5 named tests were seen red with only the flag flipped, re-pinned, and are now green. 8 new tests (T1–T5) are red on main, except the two that pin no change, and green here. Full suite: **2164 passed, 0 failed** (main: 2155 passed + 1 clock-window failure, see W4).

## Files

| file | what |
| --- | --- |
| `season.py` | `PRECIP_TOLERANT_BASE = True`, its comment rewritten to cite D-09-25-93; the module header's bullet rewritten; `median_basis` on every readout (was level-only); `READOUT_CUMULATIVE_ADDED_KEYS`; `years[].to_date` is withheld past the tolerance on a carried past season |
| `main.py` | the route index line for d091553 (docstring only) |
| `tests/test_season.py` | S1, S4 (five-year), S11 re-pinned |
| `tests/test_season_missing_days.py` | S4 flag-off, S8 re-pinned; module docstring |
| `tests/test_season_tolerant_base.py` | new: T1–T5 (8 tests) |
| `scripts/season_receipts_d091553.py` | regenerates the bodies, summary and SHA256SUMS: `python scripts/season_receipts_d091553.py <main's season.py>` |
| `docs/receipts/season-tolerant-base-d091553/` | bodies before and after, `bodies_summary.json`, red and green lists, SHA256SUMS |

## Re-pinned tests: red, then green

Red means `PRECIP_TOLERANT_BASE = True` and nothing else changed, with the old pins (`repin_red_flag_only.txt`). Green means this commit.

| test | red, flag only | re-pinned to |
| --- | --- | --- |
| `test_season.py` S1 `test_s1_gap_excludes_base_season_and_stops_this_season` | `KeyError: 'WY2000'`: the 1-missing WY2000 is no longer excluded | WY2000 (1 absent) and WY2003 (1 null) are in the base, `complete`, final = the sum of reported days. A new WY2010 with 6 missing is the only exclusion, with `days_missing: 6`. n = 35, `tolerance` 5. The season-in-progress pins are unchanged. |
| S4 `test_s4_five_year_skips_incomplete_and_lists_its_five` | `['WY2021', …] != ['WY2020', …]`: the 1-missing WY2023 now counts | 6 missing → WY2023 skipped (the old five). 1 missing → WY2023 is in, WY2021–WY2025. |
| S11 `test_s11_route_key_order_and_areas` | `Left contains 1 more item: {'qualifying_seasons': 85}` | `qualifying_seasons: 85`, right after `complete_seasons` |
| `test_season_missing_days.py` S4 `…flag_off_the_base_is_unchanged` | `assert True is False` | sets the flag to False with monkeypatch: the way back still gives main's strict base. Its sibling `…with_the_flag_set…` now asserts the shipped flag instead of setting it. |
| S8 `test_s8_areas_counts` | `'qualifying_seasons' not in off` failed | the shipped call has `qualifying_seasons` 34; with the flag cleared there is none; `complete_seasons` 32 both ways |

On main (`red_on_main.txt`), the re-pinned S1, S4 five-year, S11, S4 flag-set and S8 are red, as expected. S4 flag-off is green there: it pins main's own state.

## New tests (T1–T5)

| test | on main | branch | pins |
| --- | --- | --- | --- |
| T1 `…sacramento_base_takes_its_three_carried_seasons` | red (n 74) | green | n = 77; every exclusion has > 5 missing; `METHOD_PRECIP_TOLERANT`; Sep 30 p50 = 411.7 (d091550's Neon "after"); `median_basis` "the cone's p50 (n = 77 >= 30)" |
| T1 `…lax_base_is_unchanged` | green (pins no change) | green | n = 81 |
| T2 `…precip_readout_and_snapshot_row_agree` | red | green | readout keys = cumulative head + `median_basis` + `days_missing`, `basis`; `median`, `median_basis`, `pct_of_median`, `percentile` equal the snapshot row's |
| T3 `…degree_day_readout_and_snapshot_row_agree` [hdd, cdd] | red | green | a given HDD/CDD readout ends `vs_category`, `median_basis`; equal to the row's |
| T4 `…short_base_has_no_median_basis` [precip, hdd] | red | green | n < 30: `median` and `median_basis` are null on the readout and on the row |
| T5 `…years_to_date_is_withheld_past_the_tolerance` | red (`False is True`: on main the 5-missing season is not complete; LAX WY1944 is already null there) | green | LAX WY1944 `to_date` null, not 0.0. A past season with 5 missing has `to_date` = the sum; with 6, null, counted to the slot. Also red on this branch with the guard disabled: `0.0 is None` (`t5_red_without_guard.txt`) |

## The bodies (`docs/receipts/season-tolerant-base-d091553/`)

Each body is a full `/season` body for station precip, built from d091550's banked rows (frontier 2026-09-26) with the test suite's ENSO bins. **"Before" is main and is byte-identical to d091550's banked "after"** (same sha256). "After" is this branch.

| | Sacramento (USW00023232) before | after | LAX (USW00023174) before | after |
| --- | ---: | ---: | ---: | ---: |
| base n | 74 | **77** | 81 | 81 |
| excluded | 10 | 7 | 1 | 1 |
| p50 Feb 1 | 243.3 | 245.4 | 146.0 | 146.0 |
| p50 Apr 1 | 345.8 | 346.2 | 224.8 | 224.8 |
| p50 Sep 26 (readout day) | 405.5 | 405.7 | 260.0 | 260.0 |
| p50 Sep 30 | 408.7 | **411.7** | 260.0 | 260.0 |
| readout value | 427.7 | 427.7 | 360.5 | 360.5 |
| median | 405.5 | 405.7 | 260.0 | 260.0 |
| pct_of_median | 105.5 | 105.4 | 138.7 | 138.7 |
| percentile | 58.1 | 57.1 | 74.1 | 74.1 |
| vs_five_year | 99.2 | **37.2** | 21.7 | 21.7 |
| vs_category.neutral | 16.0 | 13.4 | 98.6 | 98.6 |
| normal n (1991–2020) | 28 | 29 | 30 | 30 |
| median_basis | — | the cone's p50 (n = 77 >= 30) | — | the cone's p50 (n = 81 >= 30) |

The Feb 1, Apr 1 and Sep 30 numbers in both before columns, and in Sacramento's after column, match d091550's §2.4 Neon table to the 0.1 mm.

These are structural diffs, before → after (`bodies_summary.json`, a key-walk):
- **Sacramento:** the base (`rule`, `seasons`, `n`, `excluded`, + `tolerance`), the cone, the normal, the five-year band, ENSO `neutral` (n 53 → 56), `curves` + WY1982, WY1994, WY2023, `years[]` `complete`/`final`/`to_date` for those three only, `last_season` + `within_tolerance`/`days_missing`/`missing_days`/`first_missing`, the readout numbers above + `median_basis`, `source.method`.
- **LAX:** only `base.rule`, `base.tolerance`, `base.excluded` (each entry gains `days_missing`), `last_season`'s four keys, `readout.median_basis`, `source.method`. No number moved.

The three seasons Sacramento gains: WY1982 (1 missing, 1981-11-26), WY1994 (1, 1994-09-28), WY2023 (3, 2023-01-09, 01-10, 03-29). d091550's Neon count for USW00023232 was also 3 seasons with 1–5 missing.

## Production now (Neon, 2026-10-02 12:49Z, read-only)

All 21 stations' precipitation frontier is **2026-09-28**. No row after Sep 28 has a value yet, so WY2026 is still the season in progress and nothing above depends on the WY2026/WY2027 changeover. Through Sep 28, the six stations d091550 listed still have their one hole, and the other 15 have none.

## What this list got wrong

**W1. Item 1 is not "the whole base change".** d091550's handback said flipping the constant was the whole change, and S4/S8 "already pin that state". They pin qualification and counts. They do not pin what the flag does to `years[]`. With the flag on, *every* past season walks carried, including those over the tolerance, so `years[].to_date` gave a number where main gave null. On the banked rows: Sacramento WY1942 (223 missing) 376.8, WY1943 385.7, …, WY1995 (7 missing) 597.7; **LAX WY1944 (305 missing) 0.0**, a zero before the station's first report. That breaks the spec's §1.4 (over the tolerance the number is withheld) and §1.6 (a blank is never a zero). I added the guard (`to_date` null past 5 missing to that slot, the snapshot row's count-to-the-day rule) and T5. The curve is still in the walk, as §1.4 says. If the architect wants the over-tolerance sums shown, revert that hunk in `build_season`'s years loop and T5.

**W2. STOP-B was read on medians. The five-year band moves more, and it is published.** At Sacramento, WY2023 (1–5 missing) joins the five-year band and WY2020 leaves it. The band's mean on Sep 26 goes 328.5 → 390.5, and `readout.vs_five_year` goes **99.2 → 37.2 mm**. The band has no n gate, so this is on the page. That is the rule working (WY2023 is a real, nearly complete season), but D-09-25-93's evidence does not cover it. The normal (n 28 → 29) and ENSO category medians (`neutral` n 53 → 56, `vs_category.neutral` 16.0 → 13.4) move too. The other 20 stations' bands were not measured here; it needs full histories, which the banked fixtures cover for these two stations only.

**W3. Item 3 contradicts d091550's S5 "HDD and CDD byte-identical to main".** A given HDD/CDD readout now ends with `median_basis`, so those bodies differ from main by that one key. S5 still passes only because each of its HDD/CDD fixtures has a gap in the season in progress, so the readout is withheld (null) and never shows the key. I left S5 as is, since it still pins everything it ever pinned, and T3 pins the new key on a given readout. The key's position: after `vs_category` and before a CARRIED pair's `days_missing`, `basis` (`READOUT_CUMULATIVE_ADDED_KEYS`). A level's position is unchanged (after `median_peak`).

**W4. "Full suite green" was not true of main.** `tests/test_chart_brief.py::test_chart_brief_maps_contract` reads the wall clock: it expects `overdue` for a fuel-mix brief, and from 12:00 to 13:00 UTC (the 60-minute grace) it reads `pending`. It failed on main and here at 12:39Z and passed on both after 13:00Z. It is unrelated to this lane and I did not change it, but it will go red again for one hour every day.

**W5. Branch name.** The brief says `claude/season-tolerant-base-d091553`. This session's harness assigned `claude/season-tolerant-base-d091553-iklbc5` and forbids pushing elsewhere without permission (as d091550's W10). I can push the same commit to the unsuffixed name on request.

**W6. "The handback's tables".** I read this as the before/after tables for the banked bodies, regenerated above. I did not edit d091550's merged handback. Its §2.4 "after (rule, not shipped)" columns are now the shipped state. Its WY2026 readout table was computed against the strict base, so its n, median and percentile columns are pre-ruling. Re-measuring those for all 21 stations needs their full histories, which the Neon tool cannot carry (d091550 §Files).
