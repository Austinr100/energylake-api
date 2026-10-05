# Handback d091614: `GET /api/mjo/status` (MJO Explorer lane 5)

**Lane:** d091614, `energylake-api`. **Ruling:** D-09-25-141 clause 3 (the API serves the MJO status and nothing statistical).
**Branch:** `claude/mjo-status-api-wrb0tc`, cut from `main` @ `5de4e89`. This session's harness assigned that name. Branch only: no PR, no merge, no deploy. Nothing was written to Neon. Neon was read through the connector, with SELECT and EXPLAIN only.
**Compare:** https://github.com/Austinr100/energylake-api/compare/main...claude/mjo-status-api-wrb0tc
**Measured:** Neon production, 2026-10-05 ~17:40–17:50Z. The route was rehearsed with NOW pinned at 2026-10-05T18:00Z.
**Read first:** `CLAUDE.md`, `main.py` (`_DDCache` with `max_stale_s`, `/api/enso/indices`, the d091611 load routes), and pantry main @ `c901636`: `scripts/build_mjo_index.py`, `ingesters/psl_romi.py`, `docs/handback_2026_10_04_mjo_composites.md` §1.2.

## The short of it

1. **The route is built and works as briefed.** On production it serves `today` = **2026-09-30**: phase 2, amplitude 1.16974, active, ROMI. The frontier is 5 days old. The walk is 40 days, 08-22 → 09-30, all ROMI, none missing. The OMI → ROMI seam is named from the bank: **OMI through 2026-06-24; ROMI from 2026-06-25**.
2. **The bank agrees with itself everywhere.** I ran one audit over all 17,440 days (a receipt only; not a route read). It found 0 partial days, 0 days where `active` ≠ (amplitude ≥ 1.0), 0 days where `phase` ≠ octant(atan2(−PC1, PC2)), and 0 days where amplitude ≠ ‖(pc1, pc2)‖ to 0.001.
3. **The reads:** three statements, each an index scan of `idx_tsv_series_ts` naming one `(dataset, series)` and a `ts` range. No sequential scan. Each runs in under 0.3 ms with at most 114 buffers. The route does not use `max()` or `DISTINCT ON`. CLAUDE.md's empty-window trap is covered: with the window empty, the newest read touches 30 buffers.
4. **Memo:** 300 s fresh and at most 900 s stale (D-09-25-138), using `_DDCache.max_stale_s` from main. Reads run under a 3 s `SET LOCAL statement_timeout` (D-09-25-75).
5. **Tests:** 30 new tests pass, and the full suite passes (2,359). Each of 14 deliberate breaks turns its tests red.

---

## 1. What was built

| file | what |
| --- | --- |
| `mjo_status.py` | the three statements and the pure shaping (`build_status`, `build_walk`, `build_sources`, `rmm`, `octant`); no DB, no clock |
| `main.py` | `_mjo_status_cache` (300 s / stale ≤ 900 s, single-flight), `_mjo_status_build`, `GET /api/mjo/status`, one line in the route index |
| `tests/test_mjo_status.py` | T1–T4, T2r, P1, the memo and the 503s |
| `tests/fixtures/mjo_status_d091614/production_2026_10_05.json` | what the three statements returned on production, plus four banked days for the rotation test and the whole-bank audit |
| `docs/receipts/mjo-status-api-d091614/` | `plans.txt`, `reds.py` → `reds.txt`, `sample.py` → `sample_status.json` |

### 1.1 The reads

All three run in one `conn.transaction()` under `SET LOCAL statement_timeout = '3s'`.

| statement | per `(dataset, series)` | ts range | returns |
| --- | --- | --- | --- |
| `NEWEST_SQL` | `VALUES` of the 5 series × `LATERAL (… ORDER BY ts DESC LIMIT 1)` | [today − 366 d, tomorrow) | each series' newest day; the frontier is the newest of the five |
| `WALK_SQL` | `VALUES` of the 5 series × `LATERAL (… ORDER BY ts)` | [frontier − 39 d, frontier + 1 d) | 200 rows (40 days × 5) with `meta->>'index_source'` |
| `SEAM_SQL` | `amplitude` only | [today − 3660 d, tomorrow) | the newest `omi` day, then the day after it (named ROMI's first only if its source is `romi`) |

The walk is anchored on the **frontier**, not on the calendar. A bank that is five days behind still serves 40 days, and `frontier.age_days` says how old they are. If no row exists in the 366-day lookback, the route returns 503, and the 503 is not memoised.

### 1.2 The body

| field | contents |
| --- | --- |
| `today` | `{date, phase, amplitude, active, index_source}` for the newest banked day |
| `frontier` | `{date, age_days, as_of}`; the age is in UTC calendar days |
| `walk` | 40 calendar days ending on the frontier, oldest first. Each day carries `date, pc1, pc2, rmm1, rmm2, phase, amplitude, active, source` |
| `walk_window` | `{first, last, days: 40, served, missing, missing_dates, incomplete: [{date, lacking}]}` |
| `active_threshold`, `active_rule` | `1.0`; amplitude ≥ 1.0 is active |
| `phase_convention` | `RMM1 = PC2; RMM2 = -PC1; phase = octant(atan2(RMM2, RMM1))` |
| `sources` | `{omi_last, romi_first, seam, note, names, rule}` |
| `attribution` | NOAA PSL OMI/ROMI, with the URL `https://psl.noaa.gov/mjo/` |
| `cache` | the house envelope |

**How values are served:**
- **`phase` and `active` come from the bank.** The route does not recompute them.
- **`rmm1` and `rmm2` are the rotation of the banked pc1 and pc2.** They are the only values the route computes.
- **Nothing statistical is served.** There are no means, composites, anomalies or forecasts.
- **A missing day is never filled in:**
  - A day with no row is left out of the walk and named in `missing_dates`.
  - A day with only some series is served with the missing fields as null and named in `incomplete`.

## 2. Sample body

Production's rows were run through the route. The full body is `docs/receipts/mjo-status-api-d091614/sample_status.json`; the walk is cut short here.

```json
{
  "label": "MJO status",
  "dataset": "mjo_index_daily",
  "today": {"date": "2026-09-30", "phase": 2, "amplitude": 1.16974, "active": true, "index_source": "romi"},
  "frontier": {"date": "2026-09-30", "age_days": 5, "as_of": "2026-10-05"},
  "walk": [
    {"date": "2026-08-22", "pc1": -0.53379, "pc2": -0.34132, "rmm1": -0.34132, "rmm2": 0.53379,
     "phase": 7, "amplitude": 0.63358, "active": false, "source": "romi"},
    "… 37 more days …",
    {"date": "2026-09-29", "pc1": 1.08477, "pc2": -0.33517, "rmm1": -0.33517, "rmm2": -1.08477,
     "phase": 2, "amplitude": 1.13537, "active": true, "source": "romi"},
    {"date": "2026-09-30", "pc1": 1.06382, "pc2": -0.48639, "rmm1": -0.48639, "rmm2": -1.06382,
     "phase": 2, "amplitude": 1.16974, "active": true, "source": "romi"}
  ],
  "walk_window": {"first": "2026-08-22", "last": "2026-09-30", "days": 40, "served": 40,
                  "missing": 0, "missing_dates": [], "incomplete": []},
  "active_threshold": 1.0,
  "active_rule": "active when amplitude >= 1.0 (exactly 1.0 is active)",
  "phase_convention": "RMM1 = PC2; RMM2 = -PC1; phase = octant(atan2(RMM2, RMM1))",
  "sources": {"omi_last": "2026-06-24", "romi_first": "2026-06-25",
              "seam": "OMI through 2026-06-24; ROMI from 2026-06-25", "note": null,
              "names": {"omi_orig": "OMI (original, frozen file)", "omi": "OMI", "romi": "ROMI (real-time OMI)"},
              "rule": "each day comes from exactly one source; nothing is blended at the seam"},
  "attribution": {"text": "MJO index: NOAA Physical Sciences Laboratory (PSL), OMI and ROMI",
                  "url": "https://psl.noaa.gov/mjo/"},
  "cache": {"state": "miss", "built_at": "…", "age_seconds": 0.0, "ttl_seconds": 300.0,
            "build_seconds": "…", "refreshing": false}
}
```

Over the 40 days the MJO was active from 08-26 to 08-29 (phases 8 → 7 → 8), weak and inactive from 08-30 to 09-27 while it moved through phases 8 → 4 → 2, and active again from 09-28 in phase 2. Seven of the 40 days are active.

## 3. The plans

The full text is in `docs/receipts/mjo-status-api-d091614/plans.txt`, and P1 reads that file. Each statement is `mjo_status.py`'s own text with the parameters the route binds.

| statement | node on `timeseries_values` | Index Cond | rows | buffers | exec |
| --- | --- | --- | ---: | ---: | ---: |
| NEWEST_SQL | Index Only Scan `idx_tsv_series_ts` × 5 loops | `dataset = 'mjo_index_daily' AND series = v.series AND ts >= … AND ts < …` | 5 | 36 | 0.156 ms |
| NEWEST_SQL, empty window (the trap) | Index Only Scan × 5 loops | same | 0 | 30 | 1.109 ms |
| WALK_SQL | Index Scan Backward `idx_tsv_series_ts` × 5 loops | same, 40-day range | 200 | 114 | 0.293 ms |
| SEAM_SQL (outer) | Index Scan `idx_tsv_series_ts` | `dataset … AND series = 'amplitude' AND ts range`; Filter on `index_source = 'omi'`, 98 rows removed | 1 | 22 | 0.221 ms (both nodes) |
| SEAM_SQL (next day) | Index Scan Backward | `dataset … AND series = 'amplitude' AND ts > t.ts AND ts < …` | 1 | 6 | (above) |

There is one `(dataset, series)` per index scan and no sequential scan.

**SEAM_SQL's filter grows by one row per day.** It walks back through the ROMI days from today to the seam: 98 today and about 365 a year from now. That is still tiny. The 3660-day lookback bounds it: once the seam is more than ten years old, `sources.omi_last` goes null with a note.

## 4. Tests, red then green

**Before the build** (main @ `5de4e89`): `tests/test_mjo_status.py` failed collection with `ModuleNotFoundError: mjo_status`, so every test was red.

**Each break against its tests** (`reds.py` applies one break to a clean tree, runs `-k`, and restores). From `reds.txt`:

| test | break | result |
| --- | --- | --- |
| T1 | today is the oldest walk day | 3 failed |
| T1 | the frontier is the oldest series' newest day | 1 failed |
| T2 | the walk is served newest first | 2 failed |
| T2 | each day's source is dropped | 2 failed |
| T2 | the seam names OMI's last day as ROMI's first | 2 failed |
| T2r | rmm is the unrotated (PC1, PC2) | 3 failed |
| T2r | RMM2 = +PC1 (the sign flip dropped) | 3 failed |
| T3 | the threshold drifts from pantry's (0.9) | 4 failed |
| T3 | active recomputed from amplitude > 1.03 instead of served | 1 failed |
| T4 | a missing day is carried forward | 1 failed |
| T4 | a missing day is dropped but not counted | 1 failed |
| P1 | the walk reads the whole dataset (no series predicate) | 1 failed |
| P1 | the statement timeout is not set | 1 failed |
| memo | the D-09-25-138 stale cap is dropped | 1 failed |

**After:** `tests/test_mjo_status.py` 30 passed. The full suite: 2,359 passed.

What each test pins:

- **T1:** `today` is 2026-09-30, the newest of the five series' newest rows (phase 2, amplitude 1.16974, active, romi), and the age is 5 days.
  - A synthetic bank one day newer moves `today` forward.
  - If one series is a day ahead of the others, that day is the frontier and is named as `incomplete`.
- **T2:** the 40 dates are consecutive, run 08-22 → 09-30, and each has a source.
  - The seam is named as 06-24 / 06-25 and agrees with the whole-bank audit.
  - In a synthetic walk that straddles a seam, each day carries its own source (29 OMI, then 11 ROMI).
  - With no ROMI banked yet, the response says so and does not guess.
- **T2r (the rotation, pinned against banked days):**
  - The banked days tested are 1985-01-15 (omi_orig, phase 8), 1998-11-03 (omi, 3), 2026-06-24 (omi, 8), 2026-06-25 (romi, 7) and 2026-09-30 (romi, 2). On each, `(rmm1, rmm2) = (pc2, −pc1)` and the octant equals the banked phase.
  - The same holds on all 40 walk days.
  - `octant` matches pantry's own `rmm_phase` at 720 angles. That test runs when a pantry checkout sits beside this repo and is skipped otherwise.
- **T3:**
  - On all 40 days, `active` is exactly amplitude ≥ `active_threshold`.
  - `ACTIVE_THRESHOLD == 1.0`, and an amplitude of exactly 1.0 counts as active.
  - A second test parses `ACTIVE_THRESHOLD = 1.0` out of pantry's `build_mjo_index.py` when the checkout is present.
- **T4:**
  - With three days deleted, 37 days are served, the three are named, and none is filled in.
  - A missing frontier day moves the frontier back a day.
  - A missing `pc1` gives null `pc1` and `rmm2` and an `incomplete` entry.
- **P1:**
  - Every statement has a plan, and no plan has a Seq Scan.
  - The 5 index conditions each name exactly one dataset and one series.
  - The SQL text has no `DISTINCT ON` and no `max(`.
  - The route runs exactly SET LOCAL, then NEWEST, WALK and SEAM, in that order.

## 5. What this brief got wrong

1. **"The OMI → ROMI seam is named" in the walk (T2) cannot happen with today's bank.** The seam is 2026-06-24/25, 98 days before the frontier, and the 40-day walk starts on 08-22. Every walk day is ROMI. So the seam comes from its own read (`sources`, SEAM_SQL), and the "seam inside the walk" case is pinned on a synthetic bank only. The walk will not cross a seam again unless PSL extends OMI or the pantry rebuilds the join.
2. **"A missing day is absent and counted" (T4) has nothing to bite on in production.** The bank has no missing day (17,440 of 17,440), so T4 is synthetic. The route does count and name missing days; it simply has none to report today.
3. **`today` is not today.** The newest banked day is 2026-09-30, five days before the as-of date, because ROMI lags. The field keeps the brief's name. The page should print `frontier.age_days` beside it so that "today" does not read as 10-05.
4. **The convention is stated for OMI, but every day the walk serves is ROMI.** Pantry records the ROMI rotation separately (`RMM1=ROMI(PC2); RMM2=-ROMI(PC1)`). It is the same rotation, and the 98 ROMI days and the audit confirm it reproduces the banked phase. The body states the convention without naming a product.
5. **"A test pinning it to pantry's value" cannot import pantry from this repo's CI.** The constant is pinned to the literal 1.0, with pantry main @ `c901636` cited. A second test reads pantry's source when a checkout is beside this repo, and it is skipped otherwise.
6. **A strict-inequality break would pass on production's rows.** No banked day in the walk has amplitude exactly 1.0 (the closest is 08-26 at 1.00267). The `>=` vs `>` boundary is pinned on `is_active`, and the route serves pantry's banked `active` rather than recomputing it, so the boundary is pantry's.
7. **The seam read is a filter, not a pure index read.** `index_source` lives in `meta`, which has no index. Naming ROMI's first day takes a backward index scan on `(mjo_index_daily, amplitude)` filtered on `meta->>'index_source'`. That is 98 rows today and grows by one a day (§3). The other option, a hard-coded 2026-06-25, would go stale silently if PSL extended OMI.

## 6. Not done

- **No PR, merge or deploy.** The room (d091615) waits on this lane merging.
- **The omi_orig → omi seam (1990-12-31 / 1991-01-01) is not served.** The brief asks only for OMI → ROMI.
- **No ETag.** The house pattern for index reads, `/api/enso/indices`, has one, but the brief's memo is the `_DDCache` pattern, which carries the age in the body and in `Cache-Control` and `Age`. I followed the brief.
