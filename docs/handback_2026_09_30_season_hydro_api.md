# Handback: 2026-09-30, d091522, reservoirs and California's snowpack join the season API

**Spec:** pantry `docs/cc_spec_2026_09_30_season_hydro_api.md` (read at pantry main `be2c729`). **Branch:** `claude/season-hydro-api-d091522-z4zeod`, cut from api main `26e7dcb` (the d091520 merge, PR #86). The dispatch named `claude/season-hydro-api-d091522`; this session's git proxy assigns the `-z4zeod` suffix, so that is where the branch lives. No PR, no merge, no migration, nothing under `.github/workflows/`. Read-only against Neon: `SELECT` only. Nothing was fetched from CDEC.

**Precondition:** d091520's level mode is on main (`5b65af2`, merged as `26e7dcb`). ✓

**What's on the branch:**
- `season.py`:
  - two new level variables:
    - `storage` (TAF) on `reservoir:{trinity…san_luis}` and `reservoir:ca_major8`;
    - `swe_in` (inches) on `snow:ca_{state,north,central,south}`.
  - `FLOORS`: the 0.90 floor. `storage` counts every day of the season; `swe_in` counts Dec 1 → May 31 only.
  - The level walk is window-aware.
  - `_qualifies` replaces `_complete` wherever the base is chosen. A strict var still goes through `_complete`, unchanged.
  - Null-aware per-day statistics (`_masked`, `n_by_day_min`) for the floored vars.
  - New blocks:
    - `range` / `range_absence` on every level;
    - `enso.categories[*].outlook` / `outlook_absence` on every level;
    - `enso.now` on every payload.
  - Reservoir capacities and `source` fields.
  - `LEVEL_RESPONSE_KEYS`.
  - New SQL: `RESERVOIRS_SQL`, `AREAS_RESERVOIR_SQL`, `AREAS_CA_SNOW_SQL`.
  - New pure functions: `storage_daily` and `major8_daily`.
- `main.py`: `/areas` gains two GROUP BYs, and `_season_payload` gains three reads. The docstrings and the route index are updated.
- `tests/test_season_hydro.py` (new) holds cells H1–H11 and a size probe.
- `tests/test_season.py` and `tests/test_season_snow.py` get only the fixture and count edits listed under H11.

---

## Gate 0

### STOP-D: clear

I re-measured on Neon `fancy-block-96153928` / `br-dark-morning-ajosxafs` at about 13:3xZ, read-only.

**Reservoirs, `cdec_reservoir_storage_daily`.** Every §0 figure reproduces.

| series | rows | first → last | WY with every day | ≥ 97 % | **qualifying under §2 (≥ 0.90, WY1996 partial and WY2026 current excluded)** |
|---|---|---|---|---|---|
| `shasta` | 11,215 ✓ | 1996-01-01 → 2026-09-28 ✓ | 27 ✓ | 29 ✓ | **29** (WY1997–WY2025) |
| `oroville` | 11,212 ✓ | ✓ | 26 ✓ | 29 ✓ | **29** |
| `trinity` | 11,200 ✓ | ✓ | 20 ✓ | 29 ✓ | **29** |
| `folsom` | 11,217 ✓ | ✓ | 29 ✓ | 29 ✓ | **29** |
| `new_melones` | 11,218 ✓ | ✓ | 29 ✓ | 29 ✓ | **29** |
| `don_pedro` | 10,819 ✓ | ✓ | 15 ✓ | 24 ✓ | **26** |
| `millerton` | 11,121 ✓ | ✓ | 24 ✓ | 25 ✓ | **29** |
| `san_luis` | 11,145 ✓ | ✓ | 16 ✓ | 26 ✓ | **29** |

**All eight together: days on which all eight report, per water year.**

| WY | 1996 | 1997 | 1998 | 1999 | 2000 | 2001 | 2002 | 2003 | 2004 | 2005 | 2006 | 2007 | 2008 | 2009 | 2010 | 2011 | 2012 | 2013 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| days | 124 | 271 | 288 | 329 | 315 | 328 | 364 | 365 | 364/366 | 365 | 364 | 363 | 356/366 | 359 | 348 | 346 | 349/366 | 350 |

| WY | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (to 09-28) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| days | 360 | 365 | 366/366 | 365 | 365 | 365 | 366/366 | 364 | 362 | 365 | 365/366 | 363 | 351 |

- The sequence 124, 271, 288, 329, 315, 328 reproduces, followed by ≥ 346 from WY2002. ✓
- Nine years have every day (WY2003, 2005, 2015–2020, 2023). ✓

> ⚠ **One discrepancy in §0's arithmetic, not in its counts.** The spec says: *"at a 0.90 floor, WY1999 and every year from WY2001 qualify"*. §5 repeats it: *"WY1999 and WY2001 → WY2025"*. But WY2001 has **328** all-eight days of 365 = **0.8986**, which is below 0.90 (0.90 × 365 = 328.5 needs 329). §0's own count (328) is what I find, so this is not STOP-D. **I implemented the rule as written (≥ 0.90, inclusive, no rounding).** `reservoir:ca_major8` therefore qualifies **25** seasons: **WY1999 and WY2002 → WY2025**. WY2001 lands in `base.excluded` as `{days_complete: 328, days_in_window: 365}`.
>
> If the architect wants WY2001 in the base, there are two ways to do it:
> - lower the floor to 0.898; or
> - round the threshold down (`floor(0.90 × days)`).
>
> Either is a one-constant change in `FLOORS`, and the H1 cell `test_h1_the_floor_is_inclusive_at_exactly_ninety_percent` pins the current reading. It needs a ruling. §5's `reservoir:ca_major8` row will read WY1999 + WY2002 → WY2025 until then.

**California snow, `cdec_snowpack_swe_daily`.**
- Every series runs 2006-05-04 → 2026-06-05, with 4,584–4,586 rows each. ✓
- No NULL values; every row is valued.
- Valued days inside Dec 1 → May 31, per region:

| region (area) | WY2006 | WY2007–WY2010 | WY2011 | WY2012–WY2025 | WY2026 (current) | **qualifying** |
|---|---|---|---|---|---|---|
| state (`snow:ca_state`) | 28/182 | full | 177/182 | full, except WY2020 at 182/183 | 178/182 | **19** (WY2007–WY2025) |
| north (`snow:ca_north`) | 28/182 | full | 177/182 | full | 178/182 | **19** |
| central (`snow:ca_central`) | 28/182 | full | 177/182 | full | 178/182 | **19** |
| south (`snow:ca_south`) | 28/182 | full | 177/182 | full | 178/182 | **19** |

19 ≥ 15 in all four regions, so STOP-D is clear. The spec's "up to 20" is 19 because WY2006 begins May 4 and WY2026 is the current season.

### d091520's level walk and how it builds `curves` (api main `26e7dcb`, `season.py`)

The level walk writes each valued day's own value to its own slot. A missing day leaves its slot `None`, and Feb 29 is on no slot:

```python
def _walk_level(w, start, daily, limit, slots):
    d = start
    while d <= limit:
        v = daily.get(d, _NO_ROW)
        if v is _NO_ROW or v is None:
            w.days_missing += 1
            if w.first_missing is None:
                w.first_missing = d
                w.mode = ABSENT if v is _NO_ROW else INCOMPLETE
        else:
            v = float(v)
            w.days_complete += 1
            w.through = d
            if (d.month, d.day) != (2, 29):
                w.values[slots[f"{d.month:02d}-{d.day:02d}"]] = v
            if w.peak is None or v > w.peak:
                w.peak, w.peak_date = v, d
        d += timedelta(days=1)
```

`curves` holds the complete base seasons whose ENSO year is binned and not open:

```python
curves = {label(var, s): _rv(walks[s].values) for s in base
          if enso_year(var, s) in bins_by_year and enso_year(var, s) not in opened}
```

- The base was `[s for s in past if _complete(walks[s])]`, where `_complete` means no first missing day and every calendar day valued.
- d091522 keeps all three pieces for `swe` and the cumulatives.
- For `storage` and `swe_in`:
  - The walk ignores a missing day outside the count window. It counts no gap and no absence for that day.
  - The base is `_qualifies`.
  - `curves` is every qualifying season (§2.5).

### The Almanac page (dashboard main `1c359f3`, `src/app/almanac/california-water-storage/page.tsx`), for comparison only

`RESERVOIRS`, copied into `season.RESERVOIRS` with this file's citation (`source.capacity_source`):

```ts
const RESERVOIRS: ReservoirMeta[] = [
  { code: "trinity",     displayName: "Trinity Lake",       capacityAF: 2447650, basin: "trinity",          operator: "USBR",      snowpackRegion: "north" },
  { code: "shasta",      displayName: "Shasta Lake",        capacityAF: 4552000, basin: "northern_sierra",  operator: "USBR",      snowpackRegion: "north" },
  { code: "oroville",    displayName: "Lake Oroville",      capacityAF: 3537577, basin: "northern_sierra",  operator: "DWR",       snowpackRegion: "north" },
  { code: "folsom",      displayName: "Folsom Lake",        capacityAF: 977000,  basin: "central_sierra",   operator: "USBR",      snowpackRegion: "central" },
  { code: "new_melones", displayName: "New Melones Lake",   capacityAF: 2400000, basin: "central_sierra",   operator: "USBR",      snowpackRegion: "central" },
  { code: "don_pedro",   displayName: "Don Pedro Reservoir",capacityAF: 2030000, basin: "central_sierra",   operator: "TID/MID",   snowpackRegion: "central" },
  { code: "millerton",   displayName: "Millerton Lake",     capacityAF: 520500,  basin: "southern_sierra",  operator: "USBR",      snowpackRegion: "south" },
  { code: "san_luis",    displayName: "San Luis Reservoir", capacityAF: 2041000, basin: "delta_offstream",  operator: "USBR/DWR",  snowpackRegion: null },
];
```

The total is 18,505,727 AF = **18,505.727 TAF** (`source.capacity_taf` on `reservoir:ca_major8`).

`buildCombinedDailySeries`:

```ts
  const resvByDate = new Map<string, number>();
  for (const obs of reservoirByCode.values()) {
    for (const o of obs) {
      const key = o.date.toISOString().slice(0, 10);
      resvByDate.set(key, (resvByDate.get(key) ?? 0) + o.value / 1000);
    }
  }
  const snowByDate = new Map<string, number>();
  for (const region of ["north", "central", "south"]) {
    const obs = snowpackByRegion.get(region) ?? [];
    const factor = SNOWPACK_TAF_PER_INCH[region] ?? 0;
    ...
      snowByDate.set(key, (snowByDate.get(key) ?? 0) + o.value * factor);
  }
  ...
    const snowTAF = snowByDate.get(key) ?? 0;
    combined.push({ date: ..., value: resvTAF + snowTAF });
```

Two differences from this lane, for the page's authors:
- **(a)** The dashboard sums whichever reservoirs reported that day (`?? 0`), so a seven-of-eight day draws low. `reservoir:ca_major8` makes that day null instead (red R2 / cell H2).
- **(b)** The dashboard converts inches of snow to TAF with `SNOWPACK_TAF_PER_INCH`. This lane does **not** do that: `swe_in` stays in inches, as CDEC reports it.

### `enso_catalog_runs.developing`, verbatim (newest run per classifier, `computed_at` 2026-09-29T19:38:40.604Z, `catalog_version` `d72f2150…6c11`)

```json
cpc_oni: {"kind": "nino", "n_seasons": 4, "first_year": 2026, "latest_oni": 1.8, "latest_year": 2026, "first_season": "MAM", "latest_season": "JJA"}
roni:    {"kind": "nino", "n_seasons": 3, "first_year": 2026, "latest_oni": 1.4, "latest_year": 2026, "first_season": "AMJ", "latest_season": "JJA"}
```

What `enso.now` returns from each:
- **`cpc_oni`** (the default classifier): `{category: "nino_strong", basis: "El Niño developing, latest ONI 1.8 (JJA), 4 seasons; a label for the current state, read from CPC's index as the catalog banks it; not a forecast"}`.
- **`classifier=roni`**: `nino_moderate`, because RONI reads 1.4.

§5's "any area" row assumes the default classifier.

The `cpc_oni` bins at that version, 1995–2025:

| kind | ENSO years | water years |
|---|---|---|
| nino_strong | 1997, 2009, 2015, 2023 | WY1998, WY2010, WY2016, WY2024 |
| nino_moderate | 2002, 2018 | |
| nino_weak | 2004, 2006, 2014, 2019 | |
| nina_strong | 1998, 1999, 2007, 2010 | |
| nina_moderate | 1995, 2011, 2020 | |
| nina_weak | 2000, 2005, 2008, 2017, 2021, 2022 | |
| neutral | 1996, 2001, 2003, 2012, 2013, 2016, 2024, 2025 | |

What that means for §5's `nino_strong` outlooks:

| area | nino_strong members | n |
|---|---|---|
| each reservoir | all four, where WY1998 qualifies for it | ≤ 4 |
| `reservoir:ca_major8` | WY2010, WY2016, WY2024 (WY1998 has 288 all-eight days) | **3** |
| `snow:ca_*` | WY2010, WY2016, WY2024 | **3** |

So `outlook` is present on these areas, at the floor of n = 3.

### Qualifying-season count, per new area (what the lane measures)

| area | var | qualifying seasons (base n) |
|---|---|---|
| `snow:ca_state` / `ca_north` / `ca_central` / `ca_south` | `swe_in` | 19 / 19 / 19 / 19 |
| `reservoir:ca_major8` | `storage` | 25 (WY1999, WY2002–WY2025) |
| `reservoir:trinity` / `shasta` / `oroville` / `folsom` | `storage` | 29 / 29 / 29 / 29 |
| `reservoir:new_melones` / `don_pedro` / `millerton` / `san_luis` | `storage` | 29 / 26 / 29 / 29 |

These are measured with the same SQL `/areas` now runs (`AREAS_RESERVOIR_SQL` was executed on Neon with its parameters inlined). All four timestamp columns sit on a single UTC hour, so `(ts AT TIME ZONE 'UTC')::date` is the CDEC date.

Every base n is below 30, so all 13 new areas carry `percentiles: null` / `short_record`, per §2.3. `range` is present on all of them (n ≥ 5).

---

## The contract, as built

**Areas (§1).** `/areas` lists **57** in this order:
1. 21 stations
2. 17 LWT regions
3. the 6 Columbia basins (d091520)
4. `snow:ca_state`, `ca_north`, `ca_central`, `ca_south`
5. `reservoir:ca_major8`
6. the eight reservoirs, north to south: Trinity, Shasta, Oroville, Folsom, New Melones, Don Pedro, Millerton, San Luis

What each area carries:
- **Reservoir rows** carry `capacity_taf`.
- **Floored vars** (`storage`, `swe_in`) carry `qualifying_seasons` next to `complete_seasons`, in the var row.
- **`swe_in`'s counts** are over its Dec 1 → May 31 window.
- **Labels:**
  - reservoirs use the dashboard's `displayName`;
  - `ca_major8` is "California, eight major reservoirs";
  - the four California snow areas use §1's labels.

**`storage`.** Units `TAF` (acre-feet ÷ 1000).
- A single reservoir's `source` is `{dataset, method, capacity_taf, capacity_source}`.
- `reservoir:ca_major8`'s `source` adds `capacity_af_by_reservoir` and `series_frontiers`: each reservoir's newest valued day, so a stopped feed is visible.
- `ca_major8` builds each day from the eight series: `major8_daily` gives the sum ÷ 1000 when all eight carry a value, and null otherwise. It never renormalises.

**`swe_in`.** Units `in`.
- `source` is `{dataset: cdec_snowpack_swe_daily, method}`.
- Read from `{region}_avg_swc`.

**`base`, for `storage` and `swe_in` only:**
```
{rule, min_days_frac: 0.9, window: {start_md, end_md}, seasons, n, n_by_day_min, excluded}
```
- `window` is `10-01`→`09-30` for `storage` and `12-01`→`05-31` for `swe_in`.
- For `swe_in`, `excluded[].days_complete` / `days_in_window` are the window's counts.
- `swe` keeps `{rule, seasons, n, excluded}`, unchanged.

**Null-aware statistics, for `storage` and `swe_in` only (§2.2):**
- Percentiles use `nanpercentile`, linear. The block gains `n` and `n_by_day_min`; it only appears at n ≥ 30.
- `normal` uses `nanmean`, plus `n_by_day_min`.
- `five_year` uses `nanmean` / `nanmin` / `nanmax`, plus `n` and `n_by_day_min`.
- Category `median` uses `nanmedian`, plus `n_by_day_min`.
- On a day with fewer than 3 contributing seasons, every statistic is null. NumPy never sees those days, so it emits no all-NaN warning and never substitutes 0.
- `n_by_day_min` is the fewest contributing seasons on any **drawn** day, meaning a day with a statistic. A summer day on `swe_in` has 0 contributing, is not drawn, and does not pull the figure to 0.
- The existing floors on `n` hold unchanged.

**`range` (§2.4).** On every level payload (`swe`, `storage`, `swe_in`), right after `five_year_absence`:
```
{n, n_by_day_min, seasons, min, median, max}
```
- Per-day, over the base.
- Null below 5 base seasons, with `range_absence: {reason: "small_n", n}`.
- Cumulative payloads have no `range` key at all (see H11).

**`outlook` (§3).** On every level payload's 13 categories:
```
outlook: {n, seasons, peak: {min, median, max, median_md},
          on_day: {"04-01": {min, median, max, n}, "07-01": {…}},
          members: [{season, peak, peak_md, apr1, jul1}]}
```
- Members are the category's base seasons. `peak` is each member's season maximum over its valued days.
- `median_md` is the lower-median peak day, the same rule as d091520's `peak.base.median_md`.
- `on_day` is over the members valued that day; fewer than 3 gives nulls.
- It is min/median/max whatever n is. It never prints a percentile.
- Null below n = 3, with `outlook_absence: {reason: "small_n", n}`.

**`enso.now` (§3).** The last key of `enso` on every payload, cumulatives included. It is read from `developing` and nothing else:
- `|latest_oni|` ≥ 1.5 → strong, ≥ 1.0 → moderate, ≥ 0.5 → weak.
- No `developing` → `neutral`.
- Not in the spec: a `developing` whose ONI is below 0.5, or null, falls back to its bare kind (`nino` / `nina`).
- `basis` quotes the numbers and says *"not a forecast"*.

**Key order.**
- `season.RESPONSE_KEYS` is unchanged, and cumulative payloads still follow it byte for byte.
- New `season.LEVEL_RESPONSE_KEYS` = `RESPONSE_KEYS` with `range`, `range_absence` inserted after `five_year_absence`.

## Cells and reds

`tests/test_season_hydro.py`, 18 tests:

| cell | test(s) | result |
|---|---|---|
| H1 | `test_h1_storage_floor_keeps_a_holed_season_and_excludes_a_thin_one`, `test_h1_the_floor_is_inclusive_at_exactly_ninety_percent` | ✓ |
| H2 | `test_h2_major8_needs_all_eight_and_sums_without_renormalising`, `test_h2_major8_payload_source_and_a_null_day` | ✓ |
| H3 | `test_h3_swe_in_counts_only_its_window`, `test_h3_a_hole_inside_the_window_is_a_gap_and_a_thin_season_is_excluded` | ✓ |
| H4 | `test_h4_null_aware_statistics_and_n_by_day_min`, `test_h4_cone_on_a_long_floored_record_ignores_nulls` | ✓ |
| H5 | `test_h5_short_record_carries_a_range_not_a_cone` | ✓ |
| H6 | `test_h6_outlook_equals_numpy_on_the_members`, `test_h6_swe_categories_carry_outlook_too` | ✓ |
| H7 | `test_h7_enso_now` | ✓ |
| H8 | `test_h8_swe_stays_strict_and_keeps_its_blocks`, plus the byte comparison below | ✓ |
| H9 | `test_h9_refusals` | ✓ |
| H10 | `test_h10_areas_count_and_order`, `test_h10_major8_and_ca_state_reads` | ✓ |
| H11 | `test_h11_cumulative_payload_gains_only_enso_now`, plus every existing precip / hdd / cdd cell | ✓ |
| — | `test_hydro_payload_sizes_for_the_handback` | ✓ |

What the cells check:
- **H1:** 18/365 missing qualifies, and its nulls appear in `curves`. 55/366 missing is excluded with its counts. `curves` holds all 29 qualifying seasons. The floor is inclusive: 329/365 ✓, 328/365 ✗, 330/366 ✓, 329/366 ✗.
- **H2:** seven of eight (one NULL, or one row absent) gives null, the value is the sum ÷ 1000 exactly, and `series_frontiers` is right. The `ca_major8` payload's null day and `source` (`capacity_taf` 18505.727) are checked too.
- **H3:**
  - A season valued only Dec 1 → May 31, plus a June tail and November rows, qualifies.
  - The current season has `absence: null`, and its walk to Jul 15 has no first missing day.
  - A hole on Jan 10 is a gap, with `days_missing: 1`.
  - 20 of 182 window days missing excludes the season.
- **H4:** 2 contributing seasons gives null in range, the five-year band and the category median. At 4 contributing, the values equal NumPy on the 4 (not padded with 0s). `n_by_day_min` = 4. The n = 32 floored cone at 3 and 2 contributing is checked too.
- **H5:** n = 20 gives `percentiles: null` (`short_record`) and a `range` carrying 20 labels. n = 4 gives `range: null` (`small_n`).
- **H6:**
  - 6 members: `peak` and `on_day` equal NumPy. One member missing Apr 1 gives `on_day["04-01"].n` = 5.
  - `peak` carries only `min`/`median`/`max`/`median_md`.
  - 2 members give `outlook: null` with `small_n`.
- **H7:** 1.8 → `nino_strong`, 1.2 → `nino_moderate`, −0.6 nina → `nina_weak`, no `developing` → `neutral`. The boundaries at 1.5 and 0.5 are inclusive. `basis` carries the numbers, "not a forecast", and RONI for `roni`.
- **H8:** a one-day hole still excludes a `swe` season, while `storage` keeps the same hole. `swe`'s base, percentiles, five-year and category blocks gain no new fields. Key order is `LEVEL_RESPONSE_KEYS`.
- **H9:** `storage` on `snow:ca_state`, `swe_in` on `snow:snake`, `reservoir:castaic`, `swe` on a reservoir, and an unknown var are each a 400 naming the vocabulary.
- **H10:**
  - `/areas` = 44 + 4 + 9 = 57, in §1's order with labels, capacities, and `complete_seasons` / `qualifying_seasons`.
  - The `ca_major8` and `ca_state` routes read the right SQL with the right parameters.
  - The body serialises with `allow_nan=False`.
- **H11:** a precip payload keeps `RESPONSE_KEYS` exactly, has no `range` and no `outlook`, and its `enso` keys are the old five plus `now`.

**H8/H11 byte check.** I ran a scratch script against `origin/main`'s `season.py` (`26e7dcb`) on the same inputs: a `swe` history with a hole, precip, and hdd. After removing `range`, `range_absence`, `enso.now` and each category's `outlook` / `outlook_absence`, the payloads are **byte-identical** (`json.dumps` equal) for all three. The only other key precip / hdd gain is `enso.now`.

**Reds.** I mutated `season.py` in a scratch copy, once per red, and ran the named cell:

| red | mutation | cell |
|---|---|---|
| R1 strict completeness for `storage` | drop `storage` from `FLOORS` | H1: 3 failed |
| R2 sum seven of eight | sum whatever reported | H2: 1 failed |
| R3 count a summer day as a gap | drop the window test in the walk | H3: 2 failed |
| R4 null as 0 in a per-day statistic | `nan_to_num` on the base matrix | H4: 2 failed |
| R5 a percentile in `outlook` | add `p90` to the min/median/max block | H6: 1 failed |
| R6 `enso.now` from the year bin | read `now` from the newest bin | H7: 1 failed |

**Edits to existing tests, all of them** (H11 says those cells pass "unchanged"; these are the fixture and count lines the new areas force):
- `tests/test_season.py`, S11:
  - the stand-in pool answers `AREAS_CA_SNOW_SQL` / `AREAS_RESERVOIR_SQL` with `[]`;
  - `len(areas) == 44` → `57`.
- `tests/test_season_snow.py`, N9:
  - the same two stand-in answers;
  - `44` → `57`;
  - the snow slice is `areas[38:44]`, because California's snow areas are also `kind: "snow"`;
  - the swe body is asserted equal to `LEVEL_RESPONSE_KEYS`, and equal to `RESPONSE_KEYS` once `range` / `range_absence` are removed.
- No other assertion in either file changed.

## Suite

| | result |
|---|---|
| before (api main `26e7dcb`) | **2024 passed**, 1 warning, 31.3 s |
| after (this branch) | **2042 passed**, 1 warning, 29.1 s (+18, all in `test_season_hydro.py`) |

The one warning is the pre-existing one; the lane adds none.

## Payload sizes

From the size probe, on synthetic data shaped like production:

| area | shape | bytes |
|---|---|---|
| `reservoir:ca_major8` | rows 1996-01-01 → 2026-09-28; 30 base seasons in the probe, where production's 25 will be a little smaller | **139,388** |
| `snow:ca_state` | 2006-05-04 → 2026-06-05, snow season only; 19 base seasons | **77,637** |
| `snow:columbia_above_the_dalles` (d091520 probe, for scale) | now carries `range` + `outlook` | 142,559 |

All are well under the probe's 2 MB bound. The largest pieces are `curves` (one 365-slot line per qualifying season) and the 13 `outlook.members` lists.

## For the architect

1. **Ruling needed:** does WY2001 enter `ca_major8`'s base? As built it does not, because 328/365 is below 0.90 (see Gate 0). §5's `ca_major8` row reads "WY1999, WY2002 → WY2025" until then.
2. `enso.now` depends on the classifier: `cpc_oni` gives `nino_strong` (1.8), `roni` gives `nino_moderate` (1.4). §5 holds on the default classifier.
3. With production bins, `nino_strong.outlook` has n = 3 on `ca_major8` and on all four `snow:ca_*` areas, exactly the floor. Each single reservoir that qualifies WY1998 has n = 4.
4. The feeds' frontiers:
   - Reservoirs: 2026-09-28 on all eight.
   - Snow: 2026-06-05. For CDEC's snow feed that is the normal off-season tail, not a stop.

   Nothing in this lane fetches. D-09-25-52 (robots.txt) is untouched.
5. §5's production reads can't run from a branch. They are for the merge.
