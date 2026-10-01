# Handback: 2026-09-30, d091536, the Canadian Columbia joins the Season API as a snow area

**Spec:** pantry `docs/cc_spec_2026_09_30_season_canada_snow_api.md` (read at pantry main `a58912f`). **Branch:** `claude/season-canada-snow-d091536-r5xq6y`, cut from api main `be703ac` (the d091525 merge, PR #88). The dispatch named `claude/season-canada-snow-d091536`. This session's git proxy assigns the `-r5xq6y` suffix, so the branch lives there, as in d091525.

**Lane discipline:**
- No PR, no merge, no migration.
- Nothing under `.github/workflows/`.
- Read-only against Neon: `SELECT` only.

**What's on the branch:**
- `season.py`:
  - `CANADA_SNOW = ("col_canada",)` and `CANADA_SNOW_LABELS`, with the label **"Canadian Columbia (BC)"**, verbatim from pantry `snow/basins.py:71`. Both sit beside `SNOW_BASINS`, which still holds the six US basins.
  - `FLOORS` gains one entry, keyed `(area, var)`: `("snow:col_canada", "swe"): (1.0, ((11, 1), (5, 31)))`.
  - `floor(var, area)` reads that table: the `(area, var)` entry first, then the var's own entry.
  - `count_window`, `window_days`, `_walk`, `_qualifies` and `_season_block` take an optional `area`. `build_areas` and `build_season` pass it.
  - `WINDOW_NOTES` holds the receipt sentence. It is appended to `base.rule` and `source.method` for `snow:col_canada` only.
  - `AREAS_SNOW_SQL` gains `nw`, the count of valued days in Nov–May.
  - `area_vocabulary` and `build_areas` list `snow:col_canada` after the six and before `snow:ca_state`. The 400 vocabulary names it.
  - The module docstring gains a CANADA paragraph.
- `main.py`:
  - `/areas` counts `snow:col_canada` from `nw` (any area with its own `floor`). The six still count from `n`.
  - The route index and the `/areas` docstring are updated.
  - **The board's walk is not touched.**
- `tests/test_season_canada.py` (new) holds K0–K6 (10 tests).
- `tests/fixtures/season_areas_us_snow_main_be703ac.json` (new) holds the six US areas exactly as **main @ `be703ac`** served them from K1's rows. It was generated in a scratch worktree of `be703ac`, not on this branch.
- `tests/test_season.py`, `test_season_snow.py`, `test_season_hydro.py` and `test_season_load.py` change only the area count (85 → 86) and the index offsets after position 44, which the new area moves by one.

---

## Gate 0

### STOP-D: clear

**Part 1: `col_canada.SWE_PCT` is in the bank as §0 says.** I re-measured on Neon `fancy-block-96153928` (default branch) on 2026-10-01, read-only. Table `timeseries_values`, dataset `snow_basin_index_daily`:

| §0 says | lane finds |
|---|---|
| a seventh series, `col_canada.SWE_PCT` | present, beside the six US `*.SWE_PCT` ✓ |
| 10,260 rows | **10,260**, all valued (`count(value) = count(*)`) ✓ |
| 1997-07-27 → 2026-09-29 | first **1997-07-27**, last valued **2026-09-29** ✓. No row on or after 2026-09-30. |
| 22 strict water years: 1999–2011, 2013–2020, 2023 | **22** ✓, exactly those years |
| recent gaps are the summer: Jul–Sep 2024 (84), Jun 17–Sep 2025 (106), Jul–Sep 2026 (80), five scattered single days | 2024-07-01→09-21 + 09-28 = **84** ✓. 2025-06-11, 06-15→20, 06-22, 06-25→09-30 = **106** ✓. 2026-07-04→09-13 + 09-18→25 = **80** ✓. Since 2023-10-01 the scattered single days are 2023-10-16, 10-21, 10-23, 2024-10-09, 2025-01-18 and 2025-10-16 (see below). |
| WY2026 peak 110.0 % on Apr 4 | **109.98 on 2026-04-04**, which rounds to 110.0 ✓ |
| frontier `meta` | `{"n_index": 6, "n_reporting": 6, "normals_version": "v1", "source_dataset": "bc_asws_swe_daily", …}`. `source` carries the first three. |

**Part 2: the window mechanism can express a per-area window without a second mechanism.** It is the same `FLOORS` table, the same `count_window`, the same `_walk_level` "outside the window counts nowhere" branch, the same `_qualifies` comparison, and the same floored (null-aware) statistics path. The only change is that the lookup key may be `(area, var)` as well as `var`, through one resolver, `floor`. There is no second table, no second walk and no second qualify test.

> **For the architect to rule on.** I read "a second mechanism" as a parallel completeness system. Re-keying the one table I read as extending it, not as a second one. If the architect meant that `FLOORS` itself must stay var-keyed, this is the STOP-D case. The only alternative would then be a separate var for Canada (for example `swe_bc`), which §1.1 forbids ("Variable `swe`"). The whole change sits in `floor()` plus an `area=` argument threaded through five functions, so it is easy to reverse.

### Quoted from main @ `be703ac` (before this lane)

`season.py:127`, `SNOW_BASINS` and `SNOW_LABELS`:
```python
SNOW_BASINS = ("columbia_above_the_dalles", "col_above_grand_coulee",
               "col_mid_tributaries", "snake", "snake_upper", "snake_lower")
SNOW_LABELS = {
    "col_above_grand_coulee": "Columbia above Grand Coulee (US)",
    "col_mid_tributaries": "Mid-Columbia tributaries (Grand Coulee → The Dalles)",
    "snake_upper": "Upper Snake (above Hells Canyon)",
    "snake_lower": "Lower Snake (Salmon, Clearwater, Grande Ronde)",
    "snake": "Snake",
    "columbia_above_the_dalles": "Columbia above The Dalles (US)",
}
```

`season.py:226`, the window table (d091522):
```python
FLOORS = {
    "storage": (0.90, None),
    "swe_in": (0.90, ((12, 1), (5, 31))),
    "peak_load": (0.90, None),            # d091525: d091522's rule, every day counts
    "peak_load_7d": (0.90, None),
}
```

`season.py:626`, `_complete` and `_qualifies`:
```python
def _complete(w: _Walk) -> bool:
    return w.first_missing is None and w.days_complete == w.days_in_window

def _qualifies(var: str, w: _Walk) -> bool:
    if var not in FLOORS:
        return _complete(w)
    return w.window_complete >= FLOORS[var][0] * w.window_days
```

**How `swe` qualifies a season today:**
- `swe` has no `FLOORS` entry, so it is **strict**. Its count window is the whole water year (`count_window` falls back to `bounds`), and `_walk_level` counts every calendar day, Feb 29 included.
- A season qualifies iff no day from Oct 1 to Sep 30 is missing (`_complete`).
- Its statistics take the non-floored path: plain `np.percentile` / `mean` / `median` over a NaN-free base.

`main.py:19343`, the board's walk:
```python
    for b in _season.SNOW_BASINS:
        payloads[b] = await _season_payload(f"snow:{b}", "swe", _season.DEFAULT_CLASSIFIER)
```
`season.build_board` walks `SNOW_BASINS` again for its rows, in that order.

### The qualifying-season count under the §1 rule: base n = **25** ✓

Measured on Neon by a calendar join: `generate_series` of every day in each water year, left-joined to the valued rows, counted inside and outside Nov 1 → May 31. The count matches what K0 gets by replaying those exact missing days through `build_season`.

| water year | days valued / in year | Nov 1–May 31 valued / days | verdict under §1 |
|---|---|---|---|
| WY1997 | rows only from 1997-07-27 | 0 / 212 | ✗ (partial) |
| WY1998 | 262 / 365 | 211 / 212 | ✗ **1998-04-19** |
| WY1999–WY2011 (13) | whole | whole | ✓ (strict) |
| **WY2012** | 359 / 366 | **206 / 213** | ✗ **fails: 2012-05-02 → 2012-05-08** (7 days, inside the window) |
| WY2013–WY2020 (8) | whole | whole | ✓ (strict) |
| **WY2021** | 356 / 365 | 212 / 212 | ✓ **passes.** Missing only 2021-08-24, 08-25, 08-28, 09-02→04, 09-13, 09-14, 09-17 (summer) |
| **WY2022** | 363 / 365 | 212 / 212 | ✓ **passes.** Missing only 2021-10-02 and 2022-07-16 (outside the window) |
| WY2023 | whole | whole | ✓ (strict) |
| WY2024 | 279 / 366 | 213 / 213 | ✓. Missing 2023-10-16, 10-21, 10-23, Jul 1→Sep 21 and Sep 28 |
| **WY2025** | 257 / 365 | **211 / 212** | ✗ **fails: 2025-01-18**. That is one of §0's "scattered single days", and it falls inside the window. |
| WY2026 | 283 / 365 | 212 / 212 | ✓ qualifies, **but it is the current season** (frontier 2026-09-29 < Sep 30), so it is not in the base |

**Base n = 22 strict + WY2021 + WY2022 + WY2024 = 25.**

> **⚠ The 25 is right, but its make-up differs from §1's note.** §1 says the rule "admits WY2024, WY2025 and WY2026 on top of the 22". As measured, WY2025 fails on **2025-01-18**, and WY2026 is not yet a past season. The three admitted are **WY2021, WY2022 and WY2024**.
>
> Once a row lands on or after 2026-09-30, WY2026 closes and qualifies, and base n becomes **26**. Note that the BC stations report nothing while the pillows are bare, so the frontier may sit at 2026-09-29 until the first October or November snow.
>
> `/areas` reports `complete_seasons = qualifying_seasons = 26` today, because it counts the current season too (d091522's rule, unchanged).

---

## The cells

`python -m pytest tests/test_season_canada.py -q`: **10 passed**. Full suite: `python -m pytest tests -q` gives **2069 passed** (it was 2059 + 10 new; the 70 season tests were green before the lane).

| cell | test(s) | asserts |
|---|---|---|
| K0 | `test_k0_gate0_record_base_is_25` | the Gate 0 record replayed: every missing day exactly as Neon listed it. Base = the 22 + WY2021, WY2022, WY2024 = **25**. Excluded: WY1997, WY1998 (1998-04-19), WY2012 (2012-05-02, 206/213), WY2025 (2025-01-18). WY2026 is the current season with `complete_to_date` true, and its peak is `{110.0, 2026-04-04}`. |
| K1 | `test_k1_areas_lists_canada_once_after_the_six_and_the_six_are_mains`, `test_k1_the_season_read_and_the_vocabulary` | 86 areas. `snow:col_canada` appears once, kind `snow`, label "Canadian Columbia (BC)", right after the six and right before `snow:ca_state`. Its `vars` are `first_season WY1997, complete 26, qualifying 26`. **The six US rows are byte-identical to the fixture produced by main @ `be703ac`.** The route reads `col_canada.SWE_PCT`. `swe_in` on it is a 400, and the 400 vocabulary names it. |
| K2 | `test_k2_summer_gaps_qualify_…`, `test_k2_oct_and_june_gaps_…` | A season missing only Jul–Sep qualifies. One missing Feb day (2011-02-14) excludes WY2011 at 211/212. **The same rows on `snow:snake` qualify nothing**: strict, 365/366-day windows. Missing days in Oct and Jun don't count; missing Nov 1 or May 31 does. `count_window` for Canada is Nov 1 → May 31, with a leap Feb 29 inside (213 days). |
| K3 | `test_k3_a_missing_summer_day_is_a_gap_on_the_chart` | Every slot from Jul 1 to Sep 30 is `null` in `curves` and in `last_season.values`: not 0, and not Jun 30 carried forward. `range` is null there. `last_season.complete` is true with no `absence`. The walk counts a summer day nowhere (`days_missing 0`, window 212/212). |
| K4 | `test_k4_outlook_equals_a_hand_calculation_…` | `outlook.peak` and `on_day["04-01"]` min/median/max equal a hand calculation from the fixture's formula (no walk), n = 7. WY2004 has no Jul 1, so `on_day["07-01"]` has **n = 6** and is computed over the other six, and its member's `jul1` is null. `peak.base` agrees. `enso.now` works with no special case. |
| K5 | `test_k5_the_board_is_the_six_us_basins_in_mains_order`, `test_k5_no_union_carries_canada` | The board returns exactly the six, in main's order, and never reads `col_canada.SWE_PCT`. `col_canada` is not in `SNOW_BASINS` or `SNOW_LABELS`. |
| K6 | `test_k6_the_receipt_names_the_window_and_the_summer` | `base.window = {11-01, 05-31}` and `min_days_frac 1.0`. Both `base.rule` and `source.method` say "every day from Nov 1 through May 31 carries a value" and "summer days with no value are not reported by the source". The US basins' rule and method are main's, word for word. |

### Reds

Each red is one edit, run and then reverted:

| red | edit | goes red |
|---|---|---|
| apply the US strict rule to Canada | delete the `("snow:col_canada", "swe")` entry from `FLOORS` | **K2** (both), plus K0, K1 ×2, K3, K4 and K6, which all rest on the window |
| fill a missing summer day with 0 | in `_walk_level`, write `0.0` to the slot of an outside-window missing day | **K3**, plus K4 (the zeros reach the outlook) |
| count a missing Jul 1 as zero | in `outlook`, `xs = [v if v is not None else 0.0 …]` | **K4** only |
| add Canada to the board | walk `SNOW_BASINS + CANADA_SNOW` in `main.weather_snow_board` and `build_board` | **K5** only |

---

## What production will show (§3)

| read | expect, as built |
|---|---|
| `GET /api/weather/season/areas` | **86** areas. `snow:col_canada` at index 44, between `snow:snake_lower` and `snow:ca_state`. `first_season WY1997`, `complete_seasons 26`, `qualifying_seasons 26`. |
| `GET /api/weather/season?area=snow:col_canada&var=swe` | `peak.this_season = {110.0, 2026-04-04}`. `base.n = 25` with excluded WY1997, WY1998, WY2012, WY2025. `base.rule` and `source.method` carry the window sentence. `this_season` is WY2026. About 93 KB (measured on the K0 replay). |
| `GET /api/weather/snow/board` | six basins, unchanged |
| the Season page | the area is in the picker, with "Canadian Columbia (BC)" as the label. The summer is null, so it is drawn as a gap. Grouping it into "Snowpack" is the page's decision; the API gives `kind: "snow"`. |

**What the payload will withhold, by the existing gates:**
- `percentiles_absence: short_record` (n 25 < 30).
- `normal_absence: short_window` (21 base years in 1991–2020 < 24).
- `five_year` is WY2020–WY2024.
- `range` and the ENSO outlooks draw. Per-day statistics are null where fewer than 3 seasons carry a value; in the summer that is most days, because only WY1999–WY2023's full years do.

## For the architect

1. **The window is keyed `(area, var)` in the existing `FLOORS` table** (see STOP-D, part 2). If that reads as a second mechanism to you, revert `floor()` and the `area=` arguments and the lane stops there.
2. **The 25 is made of different years than §1 expected.** WY2025 fails on 2025-01-18, a mid-winter day with no row. WY2021 and WY2022 pass, because all their missing days are outside Nov–May. WY2026 joins when its season closes, giving 26.
3. **Canada takes the floored, null-aware statistics path** (as storage and `swe_in` do), because its summer is null by design. Within the window every base season is whole, so the Nov–May statistics are the same numbers the strict path would give. Outside the window, a day's statistics are over the seasons valued that day, and `n_by_day_min` says how few that is.
4. `source.method` keeps the US sentence ("1991–2020 median peaks; 80 % of the index stations"). That holds for Canada too: pantry `snow/normals.py` builds the BC normals over WY1991–2020, and 5 of 6 stations is ≥ 0.80. I appended the window sentence to it.
5. Nothing sums Canada into `columbia_above_the_dalles`: no code path reads two series together for `swe`.

**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/season-canada-snow-d091536-r5xq6y
