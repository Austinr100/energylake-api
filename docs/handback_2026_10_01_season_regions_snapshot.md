# Handback — d091542, the Season API: data types, regions, a snapshot, reads that never wait 30 s

**Date:** 2026-10-01. **Lane:** d091542, `energylake-api`. **Ships:** a branch only: no PR, no merge, no deploy.
**Rulings implemented:** D-09-25-69 … D-09-25-73.
**STOPs:** none triggered. STOP-R lists six rows, as it asks; STOP-M is an estimate, not a measurement (see below).

---

## Gate 0 — who held the pool

**`/api/market-clock`.** The full receipt is `docs/receipts/season-pool-d091542/gate0.md`, with the log extract beside it.

Five market-clock requests started between 10:36:42 and 10:37:54Z. Three of them were abandoned by their browsers (499) but kept running server-side. Together the five held all five shared connections until 10:40:11.85Z, and every waiter was served within 3 s of that.

The cause is one subquery: `max(ingested_ts)` over `caiso_lmp_da_hourly` with no `series` predicate. Neon plans it as a backward walk of `idx_tsv_dataset_ingested_ts` filtered on `ts`. Before CAISO publishes tomorrow's day-ahead market there is no matching row, so the walk covers the whole dataset. Run by hand at 05:30 PT, the statement did not finish in 60 s.

The degree-day board was a victim, not a holder. STOP-H does not apply.

**How Gate 0 was read differs from §2.1.** This container cannot reach Neon over TCP or Neon's HTTPS endpoint (the proxy returns 403), so nothing here could run a real pool against production. Gate 0 was read from production's own Railway HTTP and deploy logs and from Neon's plan. Market-clock itself is not fixed here: it needs its own lane, and soon (gate0.md says why).

## What changed

| file | what |
| --- | --- |
| `season.py` | `VAR_DATA_TYPES`, `DATA_TYPES`. `REGIONS`, `LEVELS`, `STATE_REGIONS`, `BA_HOME_STATES`, `LWT_HOME_STATES`, `_SNOW_PLACES`, `REGION_VIEWS` / `REGION_AGGREGATES`, plus `area_place`, `area_label`, `build_regions`. `in_progress_season`: the one rule `build_season` and `build_areas` now share. `RESERVOIR_REGIONS`, `RESERVOIR_MEMBERS`, and `major8_daily(rows, members)` / `composite_daily`. `AREAS_RESERVOIR_REGIONS_SQL`, and a `last` column on every `AREAS_*_SQL` (`last7` on load). `build_areas(…, lasts)` adds the new keys after the existing ones. `build_season(…, extras=)` fills the unrounded day statistics for the snapshot without changing the body. A level's readout gains `pct_of_median_peak` and `median_peak`. The snapshot: `SNAPSHOT_KEYS`, `DAM_POINTS` / `SNOW_POINTS`, `area_geo`, `snapshot_areas`, `snapshot_row`, `build_snapshot`, `parse_snapshot`. `ROUTE_ADDED_KEYS`, `READOUT_ADDED_KEYS`, `AREA_ADDED_KEYS`, `VAR_ADDED_KEYS`. |
| `main.py` | `_season_pool` (min 1, max 3, checkout timeout 5 s, the same pre-ping), opened and closed in `lifespan`. `_season_connection`: a 5 s wait raises `_SeasonWait`, which becomes a 503 `{"detail": "season read waited 5 s for a connection", "retry_after": 5}` with `Retry-After: 5`. `_season_memo`: fresh, or stale with one background rebuild per key, or a single-flight miss. `/season` and `/areas` add `stale` and `built_at`. The composites read through `RESERVOIRS_SQL` on their members. `GET /api/weather/season/snapshot`. `_season_warm` at boot: 23 keys × 2 classifiers plus `/areas`, sequential, gated by `SEASON_WARM_ON_STARTUP`. The shared pool's `max_size=5` is unchanged. |
| `scripts/season_receipts_d091542.py` | Banks the receipts: the branch's routes answered from the production pulls. |
| `tests/test_season_regions.py` | A1–A4, P1–P4 (16 tests). |
| `tests/test_season_snapshot.py` | S1–S5 (9 tests). |
| `tests/fixtures/season_areas_counts_d091542.json` | Production `/areas` rows for Canada and California's four, for A3. |
| `tests/test_season{,_canada,_hydro,_load,_snow}.py` | The fixtures also patch `_season_pool` and answer the composites statement. The rest of these edits are below. |

### Edits to existing tests, every one named

- **86 → 89** areas; the reservoir slice moves 3 places; the H9 vocabulary sentence now names `ca_north, ca_central, ca_south`.
- **Each `vars` entry that is compared whole** gains `data_type` and `measure`.
- **Route key asserts** become `… + season.ROUTE_ADDED_KEYS` (`stale`, `built_at`): this is the test that says exactly which keys were added.
- **K1's byte-for-byte compare** of the US six against main @ be703ac still holds after removing exactly `AREA_ADDED_KEYS` and `VAR_ADDED_KEYS`.
- **D5's counts change, and that is the fix.** CISO's `qualifying_seasons` goes 7 → 6 and PSEI's 5 → 4: the frontier 2026-09-29 is before Sep 30, so WY2026 is in progress, and the payload's base already excluded it. The old test's own comment called this out ("qualifies there, though the payload's base excludes it").

No payload number changed. Of the banked season payloads' keys, only those named above were added.

## Tests, red → green

- **Red:** with `main.py` and `season.py` at main (`git show HEAD:…`), all 25 new tests error. Each fixture or test touches a name main does not have (`_season_snapshot_cache`, `_season_pool`, `RESERVOIR_REGION_IDS`, …).
- **Green:** 25 passed.
- **Rehearsed reds inside the suite:**
  - A3: `in_progress_season` patched back to "nothing is in progress" makes Canada read 26 and California 20 qualifying, so A3 would fail.
  - P4: the season reads put back on the held pool make a cold `/season` wait out the checkout and 503.
- **Full suite:** 2093 passed, 1 failed. The failure is `tests/test_chart_brief.py::test_chart_brief_maps_contract`, which fails the same way on main before this lane (2068 passed, 1 failed). It is not touched here.

| id | test(s) | pins |
| --- | --- | --- |
| A1 | `test_a1_*` (3) | region (or a stated absence) and level on all 89; data_type and measure on every var; swe / swe_in share a data type and differ in measure; new keys after old; the level table; the STOP-R list exactly |
| A2 | `test_a2_regions_block` | CA → `snow:ca_state`, `reservoir:ca_major8`; PNW → The Dalles (with its note); Oregon null with "no Oregon snow index is banked (d091544)" |
| A3 | `test_a3_*` (2) | production rows: Canada 25/25, California 19 qualifying (17 complete); the rehearsed red reads 26 and 20 |
| A4 | `test_a4_*` (3) | members, capacities (north 10,537.227 TAF), labels name members, San Luis in none with `members_note`; a day with Shasta missing is null on ca_north; ca_south equals Millerton; the rule on members by hand |
| S1 | `test_s1_*` (2) | every snapshot field equals the `/season` field (readout, cone p50, range median, peak base) on the frontier; the readout gained exactly two keys |
| S2 | `test_s2_*` | each area's own frontier; 2023-04-01 answers on WY2023's curve against the same base, percentile by hand |
| S3 | `test_s3_*` | the nino median against the all-years median, by hand; nino_strong (n = 1) is null with `small_n` |
| S4 | `test_s4_*` (2) | a hole, outside the reporting window, after the frontier, before the record; a short base |
| S5 | `test_s5_*` (3) | 400s name the allowed set; ETag stable, 304, changes with a value; the snapshot makes no read of its own |
| P1 | `test_p1_*` (2) | source sweep: no `_pool` in the season block, `_season_pool` outside it only in `lifespan`; the pool is (1, 3, 5 s) |
| P2 | `test_p2_*` (3) | five requests on an expired key: all stale, exactly one rebuild; a miss is single-flight; the route says `stale` and keeps `built_at` |
| P3 | `test_p3_*` | an exhausted season pool: 503, `Retry-After: 5`, the stated sentence, in under 6 s (5.0 s wait) |
| P4 | `test_p4_*` | main pool held (each checkout waits 2 s and fails): cold `/season` 200 in under 1.5 s, `/areas` 200, the held pool never asked; then the rehearsed red |

## Banked responses (production rows, read-only, 2026-10-01 ~12:40Z)

The production rows were read through the Neon console's SQL runner, read-only, because this container cannot connect to Neon. They are saved as four gzipped pulls in `docs/receipts/season-regions-snapshot/pulls/`. The receipts are the branch's own routes answered from those pulls by `scripts/season_receipts_d091542.py`, which rebuilds them byte for byte (`sha256sum -c SHA256SUMS` passes). `built_at` reads `(receipt run)`.

| file | sha-256 |
| --- | --- |
| `areas.json` | `4f109965bf0e1c9c485be5dab1632ad6fd7b6088917a4159161a85663ee15d9c` |
| `snapshot_swe_frontier.json` | `03b1f777a26455d1ca3f5af6fa2bb6d2f9660e1ff39a2dafef301ad53db81217` |
| `snapshot_swe_2026-04-01.json` | `60e6f7c786b23ab7a0fc35cc307a589db7307c66d802f48720bc77ea449cceba` |
| `snapshot_swe_2026-04-01_nino.json` | `7648f221fe3931730d8faaa984c6af7433587f7f1716ca1b73fd690709af1e32` |
| `snapshot_storage_frontier.json` | `cc3893c7a7722e2558ed8bf8fe6273d1be81dff12bc19365b94480315ded13ba` |
| `season_reservoir_ca_north.json` | `721c94ce3ad5e8751744857be1ac9148e32cca1aa1491962ee4750aba9bfa541` |
| `season_reservoir_ca_central.json` | `b8549221e513c2c047e78b8e083c229986701d6c8eeb27cc114e97175704ccad` |
| `season_reservoir_ca_south.json` | `ccca825349c8b7eb4f6254b31a71a58dc9bdab431f10312189a8377da80edee5` |
| `pulls/areas_aggregates.json.gz` | `5e00aa3c368001fdc6e199b872dab593e069bd4cfb002f60da252a9d198e4e60` |
| `pulls/snow_basin_index_daily.json.gz` | `a5373a695b8fdceaaf55cc6e7d3478192e35846fb729e9e6aedaa49495dbc5c5` |
| `pulls/cdec_snow_and_reservoirs.json.gz` | `0e83172f322aca77ffca3fb26cbceead5de86e2b7c7bedd6588fd18af447f70e` |
| `pulls/enso.json.gz` | `7cbd829c3df9be7310412671cf90bed11d5418b788f17e6f7d80c935b2d5be40` |

**What they say:**

- **`/areas`:** 89 areas. Canada 25 complete, 25 qualifying. California statewide 19 qualifying, 17 complete. `ca_major8` 25 qualifying; `ca_north` 29, `ca_central` 26, `ca_south` 29.
- **Snowpack on 2026-04-01** (value, % of median, percentile):

  | area | value | % of median | percentile |
  | --- | --- | --- | --- |
  | Columbia above The Dalles | 51.9 % of normal peak | 55.9 % | 2.4 |
  | Canadian Columbia | 109.4 | 128.8 % | none (n = 25) |
  | California statewide | 4.9 in | 20.7 % | none (n = 19) |

  In El Niño years the Columbia's median on that day runs 84 % of all years' median; California's runs 102 %.
- **Storage on the frontier** (2026-09-29): the eight hold 10,968.8 TAF, 109.0 % of the day's median. North is at 97.0 %, Central 102.4 %, South 96.5 %.

## Timings

- **`ca_state` and `ca_major8` with the main pool held.** Locally, the shared pool was a stand-in whose every checkout waits 30 s, and the season pool answered from the production pulls.

  | area | cold | warm |
  | --- | --- | --- |
  | `snow:ca_state` | 65 ms | 9 ms |
  | `reservoir:ca_major8` | 200 ms | 24 ms |

  None of the four asked the shared pool for a connection. These are compute only. On production a cold read adds its database time: `RESERVOIRS_SQL` for the eight is 1,252 ms (§0), and a Columbia basin's `SNOW_SQL` was 2,677 ms cold on Neon today (EXPLAIN ANALYZE, `col_mid_tributaries`).
- **The production numbers are §3 step 2's** (the desk browser, after deploy).

## STOP-M — the warm's two numbers

- **Memory.** The memo after the warm holds 46 payloads, 6.7 MB of JSON. tracemalloc's peak for the whole run, the pulls included, was 60.5 MB. Railway's `web` peaked at 0.40 GB of an 8 GB limit over the last 24 h, so memory is far inside the headroom.
- **Time.** The pure build of all 46 keys is 3.9 s. The database share could not be measured from here. Estimated:
  - first classifier: 23 distinct cold reads, about 33 s (seven Columbia/Canada series at ~2.7 s, four CDEC snow, nine reservoir reads at 0.5–1.3 s);
  - second classifier: the same rows warm in Neon's cache, about 8 s;
  - total: **about 45 s**, under 60 s but not by much.

  `_season_warm` logs both numbers on every boot ("season warm: N keys built, … in S s; memo holds …") and logs it as `STOP-M` if it passes 60 s. **Read the first boot's line after deploy.** If it says STOP-M, cut `_SEASON_WARM_KEYS` to the board's six and California's five, as the spec rules.

## STOP-R — six rows with `region: null`

| area | `region_absence` |
| --- | --- |
| `station:USW00003145`, `USW00023234`, `USW00023293`, `USW00093138` | "no state is banked for this area" (the four `station_metadata.json` does not carry; nothing invented) |
| `lwt:EPE`, `ba:EPE` | "state TX is not in the region table" (El Paso Electric is headquartered in Texas) |

## Checks the spec asked for

- **STOP-G: CDEC agrees.** CDEC's snow-region definitions: North "Trinity through Feather & Truckee"; Central "Yuba & Tahoe through Merced & Walker"; South "San Joaquin & Mono through Kern". Source: CDEC's snow water content chart, `cdec.water.ca.gov/snowapp/sweq.action`. This container's proxy blocks cdec.water.ca.gov, so the text was read through a web search of that page on 2026-10-01. Folsom (American), New Melones (Stanislaus) and Don Pedro (Tuolumne) are Central; Millerton (San Joaquin) is South. That is §2.4's grouping.
- **The Columbia unions.** Banked index station counts on 2026-09-28: Grand Coulee 44 + Mid-Columbia 34 + Snake 85 = The Dalles 163; Upper Snake 61 + Lower Snake 24 = Snake 85. So `members` on The Dalles and on the Snake are the pantry's unions.
- **Neon's ceiling.** `max_connections` = 901; 14 backends open at 12:29Z (7 clients, 2 active). Three more per process is inside it.

## What the spec got wrong (or left open)

1. **The captain's sentence needs a median that `/season` withholds.** The readout's median is the cone's p50 and needs n ≥ 30. California's snow has 19 seasons, the reservoirs 25–29, Canada 25, so "61 % of historical median" would be null for every California area. For a level with 5 ≤ n < 30, the snapshot gives the base's **range median** (the `range` block's own number) and says so in `median_basis`. The percentile stays withheld below n = 30. `/season`'s readout is unchanged, and its `median` stays null for California. **The architect should rule on whether the readout should do the same.**
2. **`season_day` is the axis index, 0-based, as `readout.day` is.** Mar 5 is 155, not the 156 in the illustration.
3. **The default date (each area's frontier) is bare ground for the Columbia in October.** Its frontier is 2026-09-28: the value is 0.1, the day's median 0.0, so `pct_of_median` is null. The map will want a chosen day in winter, not the default.
4. **Gate 0 could not be reproduced as §2.1 describes** (no route to Neon from here). It was read from production's logs instead, which name the holder more directly than a reproduction would.
5. **§0's "`/api/market-clock` took 13.4 s and 2.9 s" understated it.** Five requests ran 14–192 s, and the three the browser abandoned kept their connections. **The market-clock fix (one `series` predicate) should be the next api lane.** Until then `caiso-hub-lmp`, the dd ledger and the rest of the shared pool stay exposed every morning before the day-ahead market publishes. `caiso-hub-lmp` also answers a checkout timeout with 500, not 503.
6. **"California 19" is qualifying seasons; complete seasons are 17.** WY2011 has 177 of 182 days, and the statewide series is a day short in WY2020's leap window.
7. **A snapshot whose cold area fails is a 503,** not a partial map. A partial snapshot memoised for 15 minutes would be wrong for a quarter of an hour.
8. **The map points are hand-placed.** Dams come from GNIS/NID to 0.01°. Basins and snow regions are hand-placed inside the area from WBD and CDEC's map, good to about 0.5°, and the comment says so. The map lane should replace them with computed centroids if it needs better.

## Appendix A — `CLAUDE.md` (proposed)

- **D-09-25-69:** the season day is the frame of reference: value, median that day, % of it, percentile, % of the median peak; a snapshot across areas answers for one day in each area's own season.
- **D-09-25-70:** every area says what it measures (`data_type`, `measure` per var) and where it is (`region`, `level`), pinned in `season.py`, never derived from the id.
- **D-09-25-71:** aggregates are areas, labelled with their membership; nothing is summed in the browser.
- **D-09-25-72:** `/season/snapshot` is built from the `/season` memo and reads nothing of its own.
- **D-09-25-73:** a season read never waits on another route's connections: `_season_pool` (max 3, 5 s), stale-while-rebuild, a warm at boot, a 503 that says it waited.
- **Trap:** one connection pool for every route means the slowest route sets every route's worst case. A route with a memo and a cold build gets its own connections.
- **Trap:** a count over "every season with a row" counts the season in progress (`season.in_progress_season`, one rule for both).
- **Trap:** a 499 in Railway's log is the browser leaving, not the handler. The handler keeps its connection until its query returns.

## Branch

The spec names `claude/season-regions-snapshot-d091542`. This session's designated branch is `claude/epic-knuth-ud1fza`. The same commit is pushed to both.
