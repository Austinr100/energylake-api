# Handback: 2026-09-30, d091525, daily peak load by balancing area joins the season API

**Spec:** pantry `docs/cc_spec_2026_09_30_season_load_api.md` (read at pantry main `a79464c`). **Branch:** `claude/season-load-api-d091525-1xzl9l`, cut from api main `e7ae75f` (the d091522 merge, PR #87). The dispatch named `claude/season-load-api-d091525`; this session's git proxy assigns the `-1xzl9l` suffix, so the branch lives there.

**Lane discipline:**
- No PR, no merge, no migration.
- Nothing under `.github/workflows/`.
- Read-only against Neon: `SELECT` and `EXPLAIN ANALYZE SELECT` only.

**Precondition:** d091522 is on main (`e8c6e00`, merged as `e7ae75f`). ✓

**What's on the branch:**
- `season.py`:
  - two new level variables on `ba:{code}`: `peak_load` and `peak_load_7d`;
  - the 28 codes pinned in `BAS`;
  - `LOAD_MIN_HOURS = 20`;
  - `season.day_rule` on the load vars only;
  - both vars in `FLOORS` at `(0.90, None)`;
  - `source` for a ba area;
  - new SQL: `LOAD_PEAK_SQL`, `AREAS_LOAD_SQL`;
  - new pure functions: `load_day`, `load_days_from_hours` (the SQL's reference in Python), `peak_load_daily`, `peak_load_7d`.
- `main.py`:
  - `/areas` gains one grouped read and a `frontier` per ba row;
  - `_season_payload` gains the ba branch;
  - docstrings and the route index are updated.
- `tests/test_season_load.py` (new) holds cells D1–D5, one D6 guard and a size probe.
- `tests/test_season.py`, `tests/test_season_snow.py` and `tests/test_season_hydro.py` get only the fixture and count edits listed under D6.

---

## Gate 0

### STOP-D: clear

I re-measured on Neon `fancy-block-96153928` / `br-dark-morning-ajosxafs` on 2026-09-30, read-only. `timeseries_values`, dataset `wecc_load_hourly`:

| §0 says | lane finds |
|---|---|
| 28 balancing areas | **28** ✓: AVA AZPS BANC BPAT CHPD CISO DOPD EPE GCPD IID IPCO LDWP NEVP NWMT PACE PACW PGE PNM PSCO PSEI SCL SRP TEPC TIDC TPWR WACM WALC WAUW |
| hourly, 2018-12-31 → 2026-09-30 | first `2018-12-31T23:00Z` on all 28 ✓. Last `2026-09-30T05:00Z` on 26 ✓ |
| about 67,900 rows each | 67,589 (PGE) → 67,903 (PACE, PACW, PNM) ✓ |
| `PSEI` 58,664 rows | **58,664** ✓ |
| `WACM` and `WAUW` end 2026-04-02 | both end **2026-04-02T17:00Z** ✓ (63,484 / 63,564 rows) |
| `DOPD` and `TEPC` about 1,000–1,400 rows short | DOPD **66,485** (1,418 short of 67,903), TEPC **66,915** (988 short) ✓ |

Other checks:
- No NULL values: `count(value) = count(*)` on all 28.
- **No repeated hours.** The primary key is `(ts, dataset, series)`, and a measured `count(DISTINCT ts)` equals the row count on every UTC−8 day. So `count(value)` is the hour count, and both reads use it; see "SQL" below.

### Qualifying UTC−8 days per water year (≥ 20 of 24 hours), and the 0.90 floor

This is measured with `AREAS_LOAD_SQL` itself, params inlined. Each cell is `peak_load` days / `peak_load_7d` days.

| area | WY2019 | WY2020 | WY2021 | WY2022 | WY2023 | WY2024 | WY2025 | WY2026 (to 09-29) | base today (peak_load / 7d) |
|---|---|---|---|---|---|---|---|---|---|
| 21 of the 28 | 269–273 | 364–366 | 363–365 | 363–365 | 358–365 | 361–366 | 365 | 364 | **6 / 6** |
| `DOPD` | 270 | **320/214** | 355/329 | 365 | 365 | 366 | 365 | 364 | **5 / 5** (WY2020 0.874 ✗) |
| `PSEI` | 253 | **92/91** | **274/267** | 365 | 365 | 366 | 365 | 364 | **4 / 4** |
| `TEPC` | 272 | 363/357 | 333/**327** | 365 | 365 | 361/346 | 365 | 364 | **6 / 5** (7d WY2021 = 327/365 = 0.896 ✗) |
| `WACM` | 273 | 366 | 365 | 365 | 365 | 366 | 365 | **179/170** | 6 / 6, frontier **2026-04-01** |
| `WAUW` | 273 | 366 | 365 | 365 | 365 | 366 | 365 | **183/183** | 6 / 6, frontier **2026-04-01** |

(The other 21 rows are in the Neon read. Their 7d counts are ≥ 344 in every full season.)

**§0's "seven water years (WY2020–WY2026)" and what the base holds today:**
- WY2019 begins 2018-12-31 (273 days of 365) and is excluded on every area.
- WY2026 is the **current** season until its Sep 30 closes. The frontier is 2026-09-29: the Sep 29 UTC−8 day has 22 hours to `05:00Z` and qualifies. The Sep 30 UTC−8 day has no rows yet.
- So today's base is **six** seasons (WY2020–WY2025) on most areas. On the first `/season` read after Sep 30 lands, WY2026 closes and qualifies, and the base becomes the spec's seven.
- Either way, `percentiles: null` / `short_record`, `range` present, `five_year` present. The exception is PSEI, whose 4 give `range: null` and `five_year: null` (`small_n` / `fewer_than_five`).

The WACM / WAUW frontier is **2026-04-01**, not 04-02: 04-02's UTC−8 day holds 10 hours (08Z → 17Z), so it is null.

### The UTC−8 day on Postgres (Neon, PostgreSQL 17.11, session `TimeZone` = GMT)

```
((timestamptz '2026-07-15 07:59+00' AT TIME ZONE 'UTC') - interval '8 hours')::date  → 2026-07-14
((timestamptz '2026-07-15 08:00+00' AT TIME ZONE 'UTC') - interval '8 hours')::date  → 2026-07-15
```

- **One deviation from §1's literal SQL, on purpose.** The spec writes `date_trunc('day', ts - interval '8 hours')`. On a `timestamptz`, that truncates in the **session** zone. It gives the same answer only while the session is UTC/GMT, which Neon's is today. The branch writes `((ts AT TIME ZONE 'UTC') - interval '8 hours')::date`, which is UTC−8 whatever the session zone.
- `load_day` in Python is the same rule.

### ENSO at this catalog (cpc_oni, from d091522's handback; `developing` ONI 1.8)

The base's water years map to ENSO years 2019–2024:

| ENSO years | category |
|---|---|
| 2019 | `nino_weak` |
| 2020 | `nina_moderate` |
| 2021, 2022 | `nina_weak` |
| 2023 | `nino_strong` |
| 2024 | `neutral` |

- `nina` has n = 3 (WY2021–WY2023), so it carries an `outlook`, at the floor.
- Every other category is `small_n`, including `nino_strong` (n = 1: WY2024).
- `enso.now` = `nino_strong`.

This is §0's "`small_n` for nearly every category", as expected.

---

## The contract, as built

**Areas (§1).** `/areas` lists **85**: d091522's 57, then **`ba:{code}`** for the 28, A→Z.
- Each ba row is `{area, kind: "ba", label: code, frontier, vars}`.
- **`frontier`** is the area's newest qualifying UTC−8 day, from `max(d) FILTER (WHERE ok)`. It is `2026-09-29` on 26 areas and **`2026-04-01` on WACM and WAUW** (D5).
- `vars` = `peak_load`, `peak_load_7d`. Each is `{var, season: water_year, units: MW, first_season, complete_seasons, qualifying_seasons}`.

> `qualifying_seasons` in `/areas` follows d091522's existing rule: every season with rows, **the current one included**. So CISO reads 7 there (WY2020–WY2026), while the payload's `base.n` is 6. I left the rule as it is; the payload's `base` is the authority.

**`peak_load`.** A level on the water year, in MW. Each day's value is the maximum hourly load on that UTC−8 day when ≥ 20 hours carry a value. Otherwise the day is present but null: `INCOMPLETE` in a gap, a hole in the line on that day only.
- `season.day_rule`, after `mode`: *"a fixed UTC−8 day for every area, no daylight-saving shift: day D runs D 08:00Z → D+1 07:59Z; it carries the maximum hourly load when >= 20 of its 24 hours report, and is null otherwise"*.
- The key is only on the load vars. No other var's `season` block changes.

**`peak_load_7d`.** On every date the daily series holds, the mean of D−6 … D when all seven carry a peak, else null.
- The window is by calendar date, so a thin day and a day with no rows both break it.
- It crosses the Oct 1 season boundary: WY N's Oct 1 uses Sep 25–30 of WY N−1.
- The payload's `day_rule` adds that sentence.

**Everything else is d091522's level arithmetic**, through `FLOORS` at `(0.90, None)`:
- a 0.90 floor over every day of the season;
- null-aware per-day statistics with `n` / `n_by_day_min`;
- `curves` = every qualifying season;
- `range`, the categories' `outlook`, `enso.now`;
- body order `LEVEL_RESPONSE_KEYS`.

**`source`** = `{dataset: "wecc_load_hourly", method, series: code, min_hours: 20}`.

**SQL.** One grouped read per area, memoised 15 min in the existing `/season` memo, keyed `(area, var, classifier)`:
```sql
SELECT ((ts AT TIME ZONE 'UTC') - interval '8 hours')::date AS obs_date,
       max(value)::float8 AS peak, count(value)::int AS hours
FROM timeseries_values WHERE dataset = %(d)s AND series = %(s)s
GROUP BY 1 ORDER BY 1
```
- On CISO it takes **194 ms** (`EXPLAIN ANALYZE`) and returns 2,830 rows.
- `peak_load_7d` issues the same read. It is derived in Python and not cached separately.

`/areas` adds `AREAS_LOAD_SQL`: one GROUP BY over the 28 series, plus a 7-day `RANGE` window for the 7d count. Its cost:
- **1.44 s** as a hash aggregate over 1.88 M rows (`EXPLAIN ANALYZE` of the grouping step, warm).
- An earlier `count(DISTINCT ts)` form took 7.9 s through a disk sort. That is why the branch counts `count(value)`; the primary key makes the two equal.
- `/areas` stays memoised 1 h.

**Refusals.** Each of these is a 400 naming the vocabulary:
- `ba:CISO&var=swe`;
- `ba:ERCO`;
- `lwt:BPAT&var=peak_load`;
- an unknown var.

The vocabulary strings gain `; ba:{code} for AVA, …` and `; ba: areas carry peak_load, peak_load_7d`, appended. Existing refusals are unchanged, and their cells assert with `in`.

## Cells and reds

`tests/test_season_load.py`, 17 tests:

| cell | test(s) | result |
|---|---|---|
| D1 | `test_d1_the_utc_minus_8_day_boundary`, `test_d1_the_grouped_read_says_utc_minus_8_in_sql`, `test_d1_a_day_is_the_max_of_its_own_24_hours` | ✓ |
| D2 | `test_d2_nineteen_hours_is_null_and_twenty_carries_the_max`, `test_d2_a_null_hour_is_not_an_hour`, `test_d2_a_thin_day_is_a_gap_in_the_line_on_its_day_only` | ✓ |
| D3 | `test_d3_trailing_seven_day_mean`, `test_d3_a_null_or_absent_day_inside_the_window_makes_it_null`, `test_d3_seven_day_payload` | ✓ |
| D4 | `test_d4_seven_seasons_short_record`, `test_d4_the_peak_is_the_max_of_the_daily_peaks_on_its_own_day`, `test_d4_six_seasons_and_four` | ✓ |
| D5 | `test_d5_areas_lists_the_28_after_the_reservoirs`, `test_d5_the_early_frontier_carries_into_the_payload`, `test_d5_season_reads_and_refusals` | ✓ |
| D6 | the whole prior suite, plus `test_d6_day_rule_rides_on_the_load_vars_only` | ✓ |
| — | `test_load_payload_sizes_for_the_handback` | ✓ |

What the cells check:
- **D1:**
  - 07:59Z → previous day; 08:00Z → the next, in January and July alike (07:00Z in July is the previous day, where PDT would say the same day).
  - The two DST-switch days are ordinary 24-hour days.
  - An aware non-UTC timestamp is read by its instant.
  - A 07:00Z spike is the previous day's peak.
  - Both SQL strings carry the UTC−8 expression and no zone name.
  - `season.day_rule` is set.
- **D2:**
  - 19 hours → null; 20 → the max of those 20; 24 → the max of 24.
  - A NULL hour is a row, not an hour.
  - A thin day is a hole on that day only, and its season still qualifies (364/365).
- **D3:**
  - The first six days are null. Day 7 is the mean of 1–7 (NumPy-checked).
  - A null day nulls D … D+6, and an absent day does the same and stays absent.
  - In the payload, `day_rule` is the 7-day text, and Oct 1's mean uses the previous season's Sep 25–30.
- **D4:**
  - Seven seasons → `percentiles: null` (`short_record`, n 7), `normal: null`, `range.n` 7, and `five_year` = WY2021–WY2025.
  - `nino_strong`: n 1, `outlook: null`, `outlook_absence: small_n`. `nina`: n 3 with an outlook. `enso.now` = `nino_strong`.
  - Each season's `peak` / `peak_md` is the max daily peak on its own day: summer for a summer-peaking area, Dec–Feb for a winter one.
  - Six seasons → `range` present. Four (PSEI's shape) → `range: null` and `five_year: null`.
- **D5:**
  - `/areas`: 85 rows, the 28 at `[57:85]` A→Z, labels = codes, both vars, `AREAS_LOAD_SQL` called with `{d, s: BAS, h: 20}`.
  - WACM's row carries `frontier: "2026-04-01"`. Its payload's `frontier` / `this_season.through` are 04-01, and the readout is on 04-01.
  - The CISO payload's key order, `source`, `day_rule`, the 19-hour hole and the 22-hour frontier day are checked, with no NaN on the wire.
  - BPAT's `peak_load_7d` curve is NumPy-checked.
  - The refusals are checked.

**Reds.** I mutated `season.py` in a scratch copy, once per red, and ran the cell the spec names:

| red | mutation | cell |
|---|---|---|
| local-time day with DST | `load_day` → `ts.astimezone(ZoneInfo("America/Los_Angeles")).date()` | D1: **2 failed** |
| max over any number of hours | drop `hours >= LOAD_MIN_HOURS` in `peak_load_daily` | D2: **2 failed** |

**D6: edits to existing tests, all of them.** These are the fixture and count lines the 28 new areas force, the same kind d091522 made:
- `tests/test_season.py`, S11:
  - the stand-in pool answers `AREAS_LOAD_SQL` with `[]`;
  - `len(areas) == 57` → `85`.
- `tests/test_season_snow.py`, N9: the same stand-in answer, and `57` → `85`.
- `tests/test_season_hydro.py`, H10:
  - the same stand-in answer;
  - `44 + 4 + 9 == 57` → `44 + 4 + 9 + 28 == 85`;
  - the reservoir slice `areas[48:]` → `areas[48:57]`, since the ba rows now follow it.
- No other assertion changed.

**Byte check.** I ran a scratch script against `origin/main`'s `season.py` (`e7ae75f`) on the same inputs:
- the inputs are Sacramento precip, BPAT hdd, `reservoir:shasta` storage and `snow:ca_state` swe_in;
- the payloads are **byte-identical** (`json.dumps` equal) for all four.

## Suite

| | result |
|---|---|
| before (api main `e7ae75f`) | **2042 passed**, 1 warning |
| after (this branch) | **2059 passed**, 1 warning, 20.4 s (+17, all in `test_season_load.py`) |

The one warning is the pre-existing one.

## Payload sizes

From the size probe: `ba:CISO` shaped like production (2018-12-31 → 2026-09-29, 8 seasons).

| payload | bytes |
|---|---|
| `peak_load` | **60,068** |
| `peak_load_7d` | **60,182** |

## Acceptance

§2 says: *`ba:CISO`'s `peak_load` peaks on a summer day in every season and equals `max(value)` for that UTC−8 day read straight from Neon; `ba:BPAT` peaks in winter; both state `short_record`.*

**How I measured it.** A branch can't serve production, so the payload itself is for the merge. On Neon, read-only, I took each season's peak from `LOAD_PEAK_SQL`'s own grouping: the highest qualifying day, first on a tie, which is what `_walk_level`'s `peak` is. I then read `max(value)` separately, straight from the hourly rows in `[D 08:00Z, D+1 08:00Z)`:

| WY | CISO day | CISO peak (MW) | straight max | hour (UTC) | BPAT day | BPAT peak (MW) | straight max | hour (UTC) |
|---|---|---|---|---|---|---|---|---|
| 2019 † | 2019-08-15 | 43,849 | 43,849 ✓ | 00Z | 2019-02-07 | 10,275 | 10,275 ✓ | 15Z |
| 2020 | 2020-08-18 | 46,643 | 46,643 ✓ | 22Z | 2020-01-14 | 9,418 | 9,418 ✓ | 15Z |
| 2021 | 2021-09-08 | 43,615 | 43,615 ✓ | 00Z | **2020-11-25** ⚠ | **11,742** | 11,742 ✓ | 23Z |
| 2022 | 2022-09-06 | 51,104 | 51,104 ✓ | 00Z | 2022-02-23 | 10,458 | 10,458 ✓ | 15Z |
| 2023 | 2023-08-16 | 44,007 | 44,007 ✓ | 01Z | 2022-12-22 | 11,068 | 11,068 ✓ | 16Z |
| 2024 | 2024-09-05 | 47,571 | 47,571 ✓ | 01Z | 2024-01-13 | 11,496 | 11,496 ✓ | 18Z |
| 2025 | 2025-08-21 | 43,860 | 43,860 ✓ | 02Z | 2025-02-12 | 11,537 | 11,537 ✓ | 15Z |
| 2026 (current) | 2026-09-09 | 49,959 | 49,959 ✓ | 00Z | 2026-01-25 | 10,365 | 10,365 ✓ | 16Z |

† WY2019 is partial and not in the base. It still carries no `peak`, because `years[].peak` is only for a qualifying or current season.

**Results:**
- **CISO:**
  - Every season peaks in Aug–Sep, between 22Z and 02Z, which is 14:00–18:00 UTC−8 on the stated day, far from the 08Z boundary. ✓
  - Each peak equals the straight `max(value)`. ✓
  - The payload will say `short_record`: n = 6 today, 7 after WY2026 closes, and both are below 30. ✓
- **BPAT:**
  - Every straight max matches. ✓
  - Seven of eight seasons peak Dec–Feb, on a morning or evening hour. ✓
  - The payload will say `short_record`. ✓
  - **⚠ WY2021 is the exception, and it is the data, not the day rule.** On 2020-11-25 BPAT's hourly load reads about 6,800 MW through 22Z. It then steps to **11,742** at 23Z, **11,352** at 00Z, 10,076, 9,911, and back to 7,662 at 03Z. That is a four-hour +5,000 MW step on the eve of Thanksgiving, in the source. It is 2,200 MW above that season's next-highest day, 2021-02-12 at 9,539, which is a winter day.
  - The lane passes EIA-930 through as banked, as the spec asks (`max(value)` for the day), so WY2021's `peak_md` reads **11-25**.
  - As built, "BPAT peaks in winter" holds in 7 of 8 seasons, and the eighth is this spike.

## For the architect

1. **BPAT 2020-11-25 23Z–02Z** looks like a reporting artifact in EIA-930 as banked. It sets BPAT's WY2021 peak (and its `range.max` around late November, and `peak_load_7d` for that week). This lane does not despike, and nothing in the spec asks it to. It needs a ruling: leave it as the record says, or have the ingest side look at it. No other area was screened for spikes.
2. **Base n today is 6, not 7.** WY2019 is partial, and WY2026 is current until Sep 30's UTC−8 day lands. It becomes 7 on its own. PSEI is 4 (WY2020 92 days, WY2021 274), DOPD 5, and TEPC's `peak_load_7d` 5 (its WY2021 7-day count is 327/365 = 0.896).
3. **WACM and WAUW** stopped at 2026-04-02T17:00Z. Their `/areas` `frontier` and payload `frontier` read 2026-04-01, and `this_season` is frozen there. WY2026 will never qualify for them unless the feed resumes and back-fills.
4. **Deviation from §1's SQL text:** `(ts AT TIME ZONE 'UTC') - interval '8 hours'` instead of `date_trunc('day', ts - interval '8 hours')`. The result is the same on Neon's GMT session. Mine does not depend on the session zone.
5. `/areas` `qualifying_seasons` counts the current season (d091522's rule). For the load areas that reads one more than the payload's `base.n` (7 vs 6 on CISO). This is unchanged by this lane and noted only because the gap is now visible on 28 areas.
6. Nothing here fetches from EIA. §0's degree-day point stands: this record can't carry an ENSO read for load. The payload says so category by category (`small_n`).
